from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import SecurityContext
from app.db.database import loads, session_scope
from app.db.models import AuditLog, Document, DocumentVersion
from app.llm.provider import ProviderError, set_provider
from app.main import app
from app.rag.hybrid import retrieve
from app.worker import run_once
from tests.fakes import DeterministicProvider

MANAGER = {
    'X-User-Id': 'M1001',
    'X-Tenant-Id': 'TENANT-DEMO',
    'X-User-Role': 'manager',
}
CUSTOMER = {**MANAGER, 'X-User-Role': 'customer'}
AGENT = {**MANAGER, 'X-User-Role': 'agent'}
OTHER_MANAGER = {**MANAGER, 'X-Tenant-Id': 'TENANT-OTHER'}
CTX = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

NEW_VERSION_BODY = '二〇二六版发票说明。'.encode()


def _upload_version(
    client: TestClient,
    document_id: str = 'DOC-INVOICE',
    *,
    content: bytes = NEW_VERSION_BODY,
    headers: dict = MANAGER,
    **fields,
):
    data = {}
    data.update(fields)
    return client.post(
        f'/api/v1/documents/{document_id}/versions',
        files={'file': ('policy-v2.txt', content, 'text/plain')},
        data=data,
        headers=headers,
    )


def _ready_version(
    client: TestClient,
    document_id: str = 'DOC-INVOICE',
    *,
    content: bytes = NEW_VERSION_BODY,
    **fields,
) -> dict:
    body = _upload_version(
        client, document_id, content=content, **fields
    ).json()
    assert run_once() is True
    version = client.get(
        f'/api/v1/documents/{document_id}/versions/{body["version_id"]}',
        headers=MANAGER,
    ).json()
    assert version['status'] == 'ready'
    return version


def _publish(client: TestClient, document_id: str, version_id: str, headers=MANAGER):
    return client.post(
        f'/api/v1/documents/{document_id}/versions/{version_id}/publish',
        headers=headers,
    )


def _archive_version(
    client: TestClient, document_id: str, version_id: str, headers=MANAGER
):
    return client.post(
        f'/api/v1/documents/{document_id}/versions/{version_id}/archive',
        headers=headers,
    )


def _restore_version(
    client: TestClient, document_id: str, version_id: str, headers=MANAGER
):
    return client.post(
        f'/api/v1/documents/{document_id}/versions/{version_id}/restore',
        headers=headers,
    )


def _archive_document(client: TestClient, document_id: str, headers=MANAGER):
    return client.post(f'/api/v1/documents/{document_id}/archive', headers=headers)


def _restore_document(client: TestClient, document_id: str, headers=MANAGER):
    return client.post(f'/api/v1/documents/{document_id}/restore', headers=headers)


def _knowledge_ids(client: TestClient) -> set[str]:
    response = client.get('/api/v1/knowledge', headers=MANAGER)
    assert response.status_code == 200
    return {row['id'] for row in response.json()}


def _version_statuses(client: TestClient, document_id: str = 'DOC-INVOICE'):
    detail = client.get(f'/api/v1/documents/{document_id}', headers=MANAGER).json()
    return detail, {version['id']: version['status'] for version in detail['versions']}


def _audit_actions(*actions: str):
    with session_scope() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(actions))
            .order_by(AuditLog.id)
        ).all()
    return [(row.action, loads(row.detail_json, {}), row.actor_id) for row in rows]


def test_publish_ready_version_supersedes_current_and_updates_read_models(seeded_db):
    client = TestClient(app)
    version = _ready_version(client)

    response = _publish(client, 'DOC-INVOICE', version['id'])

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'published'
    assert body['document_status'] == 'active'
    assert body['superseded_version_id'] == 'KB-INVOICE-V1'
    assert body['published_at']

    detail, statuses = _version_statuses(client)
    assert detail['published_version']['id'] == version['id']
    assert statuses['KB-INVOICE-V1'] == 'superseded'
    assert statuses[version['id']] == 'published'
    assert _knowledge_ids(client) == {version['id'], 'KB-REFUND-V3', 'KB-LOGISTICS-V1'}

    audits = _audit_actions('version.published', 'version.superseded')
    assert [action for action, _detail, _actor in audits] == [
        'version.superseded',
        'version.published',
    ]
    superseded = audits[0][1]
    assert superseded['version_id'] == 'KB-INVOICE-V1'
    assert superseded['replacement_version_id'] == version['id']
    published = audits[1][1]
    assert published['version_id'] == version['id']
    assert published['superseded_version_ids'] == ['KB-INVOICE-V1']
    assert audits[0][2] == audits[1][2] == 'M1001'


def test_republishing_superseded_version_rolls_back(seeded_db):
    client = TestClient(app)
    version = _ready_version(client)
    assert _publish(client, 'DOC-INVOICE', version['id']).status_code == 200

    response = _publish(client, 'DOC-INVOICE', 'KB-INVOICE-V1')

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'published'
    assert body['superseded_version_id'] == version['id']

    detail, statuses = _version_statuses(client)
    assert detail['published_version']['id'] == 'KB-INVOICE-V1'
    assert statuses[version['id']] == 'superseded'
    assert statuses['KB-INVOICE-V1'] == 'published'
    assert 'KB-INVOICE-V1' in _knowledge_ids(client)
    assert version['id'] not in _knowledge_ids(client)

    published_audits = _audit_actions('version.published')
    assert [detail['version_id'] for _action, detail, _actor in published_audits] == [
        version['id'],
        'KB-INVOICE-V1',
    ]
    superseded_audits = _audit_actions('version.superseded')
    assert [detail['version_id'] for _action, detail, _actor in superseded_audits] == [
        'KB-INVOICE-V1',
        version['id'],
    ]


def test_publishing_an_already_published_version_is_idempotent(seeded_db):
    client = TestClient(app)

    response = _publish(client, 'DOC-INVOICE', 'KB-INVOICE-V1')

    assert response.status_code == 200
    assert response.json()['status'] == 'published'
    assert response.json()['superseded_version_id'] is None
    assert _audit_actions('version.published') == []
    assert _audit_actions('version.superseded') == []


def test_illegal_lifecycle_transitions_return_400(seeded_db):
    client = TestClient(app)
    draft = _upload_version(client).json()
    assert _publish(client, 'DOC-INVOICE', draft['version_id']).status_code == 400
    assert _archive_version(client, 'DOC-INVOICE', draft['version_id']).status_code == 400

    failed = _upload_version(client, content=b'  \r\n ').json()
    # Drain both queued versions: one becomes ready, the other failed.
    assert run_once() is True
    assert run_once() is True
    assert _publish(client, 'DOC-INVOICE', failed['version_id']).status_code == 400

    ready = _ready_version(client, content='另一个可发布版本。'.encode())
    assert _archive_version(client, 'DOC-INVOICE', ready['id']).status_code == 400
    assert _restore_version(client, 'DOC-INVOICE', 'KB-INVOICE-V1').status_code == 400

    assert _archive_document(client, 'DOC-INVOICE').status_code == 200
    assert _publish(client, 'DOC-INVOICE', ready['id']).status_code == 400
    assert _archive_document(client, 'DOC-INVOICE').status_code == 400
    assert _restore_document(client, 'DOC-INVOICE').status_code == 200
    assert _restore_document(client, 'DOC-INVOICE').status_code == 400


def test_publish_recomputes_missing_embedding(seeded_db, fake_provider):
    client = TestClient(app)
    version = _ready_version(client)
    with session_scope() as session:
        row = session.get(DocumentVersion, version['id'])
        row.embedding = None
        row.embedding_model = None
        row.embedding_dimensions = None
        row.embedding_generated_at = None

    assert _publish(client, 'DOC-INVOICE', version['id']).status_code == 200

    with session_scope() as session:
        row = session.get(DocumentVersion, version['id'])
        assert row.embedding
        assert row.embedding_model == fake_provider.embedding_model
        assert row.embedding_dimensions == fake_provider.dimensions
        assert row.embedding_generated_at is not None


def test_publish_recomputes_embedding_when_provider_model_changed(
    seeded_db, fake_provider
):
    client = TestClient(app)
    version = _ready_version(client)
    fake_provider.embedding_model = 'fake-embedding-v2'

    assert _publish(client, 'DOC-INVOICE', version['id']).status_code == 200

    with session_scope() as session:
        row = session.get(DocumentVersion, version['id'])
        assert row.embedding_model == 'fake-embedding-v2'
        assert row.embedding_dimensions == fake_provider.dimensions


def test_publish_recomputes_embedding_when_dimensions_changed(
    seeded_db, fake_provider
):
    client = TestClient(app)
    version = _ready_version(client)
    resized = DeterministicProvider(dimensions=6)
    set_provider(resized)
    try:
        response = _publish(client, 'DOC-INVOICE', version['id'])
    finally:
        set_provider(fake_provider)

    assert response.status_code == 200
    with session_scope() as session:
        row = session.get(DocumentVersion, version['id'])
        assert len(row.embedding) == 6
        assert row.embedding_dimensions == 6


def test_republishing_current_version_repairs_missing_embedding(
    seeded_db, fake_provider
):
    client = TestClient(app)
    with session_scope() as session:
        row = session.get(DocumentVersion, 'KB-INVOICE-V1')
        row.embedding = None
        row.embedding_model = None
        row.embedding_dimensions = None
        row.embedding_generated_at = None

    response = _publish(client, 'DOC-INVOICE', 'KB-INVOICE-V1')

    assert response.status_code == 200
    assert response.json()['status'] == 'published'
    with session_scope() as session:
        row = session.get(DocumentVersion, 'KB-INVOICE-V1')
        assert row.embedding
        assert row.embedding_model == fake_provider.embedding_model
        assert row.embedding_dimensions == fake_provider.dimensions
    assert _audit_actions('version.published') == []


class FailingEmbedProvider(DeterministicProvider):
    embedding_model = 'fake-embedding-v2'

    def embed(self, texts):
        raise ProviderError('embedding provider unavailable')


def test_publish_provider_failure_returns_503_and_keeps_ready(seeded_db, fake_provider):
    client = TestClient(app)
    version = _ready_version(client)
    failing = FailingEmbedProvider()
    set_provider(failing)
    try:
        response = _publish(client, 'DOC-INVOICE', version['id'])
    finally:
        set_provider(fake_provider)

    assert response.status_code == 503
    assert 'embedding provider unavailable' in response.json()['detail']
    _detail, statuses = _version_statuses(client)
    assert statuses[version['id']] == 'ready'
    assert statuses['KB-INVOICE-V1'] == 'published'
    assert _audit_actions('version.published') == []


def test_failed_rollback_keeps_current_published_version(seeded_db, fake_provider):
    client = TestClient(app)
    version = _ready_version(client)
    assert _publish(client, 'DOC-INVOICE', version['id']).status_code == 200

    failing = FailingEmbedProvider()
    set_provider(failing)
    try:
        response = _publish(client, 'DOC-INVOICE', 'KB-INVOICE-V1')
    finally:
        set_provider(fake_provider)

    assert response.status_code == 503
    detail, statuses = _version_statuses(client)
    assert detail['published_version']['id'] == version['id']
    assert statuses[version['id']] == 'published'
    assert statuses['KB-INVOICE-V1'] == 'superseded'
    published_audits = _audit_actions('version.published')
    assert [detail['version_id'] for _action, detail, _actor in published_audits] == [
        version['id']
    ]


def test_publish_validates_content_and_effective_from(seeded_db):
    client = TestClient(app)
    version = _ready_version(client)
    with session_scope() as session:
        row = session.get(DocumentVersion, version['id'])
        row.effective_from = 'not-a-date'

    invalid_date = _publish(client, 'DOC-INVOICE', version['id'])
    assert invalid_date.status_code == 400
    assert 'effective_from' in invalid_date.json()['detail']

    with session_scope() as session:
        row = session.get(DocumentVersion, version['id'])
        row.effective_from = '2026-01-01'
        row.content_text = '   '

    empty_content = _publish(client, 'DOC-INVOICE', version['id'])
    assert empty_content.status_code == 400
    assert 'content' in empty_content.json()['detail']


def test_version_archive_and_restore_controls_recall(seeded_db, fake_provider):
    client = TestClient(app)

    archived = _archive_version(client, 'DOC-INVOICE', 'KB-INVOICE-V1')

    assert archived.status_code == 200
    assert archived.json()['status'] == 'archived'
    detail, statuses = _version_statuses(client)
    assert detail['published_version'] is None
    assert statuses['KB-INVOICE-V1'] == 'archived'
    assert 'KB-INVOICE-V1' not in _knowledge_ids(client)
    assert retrieve('电子发票怎么开', category='invoice', ctx=CTX) == []

    assert _archive_version(client, 'DOC-INVOICE', 'KB-INVOICE-V1').status_code == 400

    restored = _restore_version(client, 'DOC-INVOICE', 'KB-INVOICE-V1')
    assert restored.status_code == 200
    assert restored.json()['status'] == 'ready'
    assert 'KB-INVOICE-V1' not in _knowledge_ids(client)

    assert _publish(client, 'DOC-INVOICE', 'KB-INVOICE-V1').status_code == 200
    assert 'KB-INVOICE-V1' in _knowledge_ids(client)

    assert [action for action, _detail, _actor in _audit_actions(
        'version.archived', 'version.restored'
    )] == ['version.archived', 'version.restored']


def test_document_archive_and_restore_controls_recall(seeded_db):
    client = TestClient(app)

    archived = _archive_document(client, 'DOC-INVOICE')

    assert archived.status_code == 200
    assert archived.json() == {
        'document_id': 'DOC-INVOICE',
        'status': 'archived',
        'published_version_id': 'KB-INVOICE-V1',
    }
    detail, statuses = _version_statuses(client)
    assert detail['status'] == 'archived'
    # Document-level archive keeps version statuses untouched.
    assert statuses['KB-INVOICE-V1'] == 'published'
    assert 'KB-INVOICE-V1' not in _knowledge_ids(client)
    assert retrieve('电子发票怎么开', category='invoice', ctx=CTX) == []

    restored = _restore_document(client, 'DOC-INVOICE')

    assert restored.status_code == 200
    assert restored.json()['status'] == 'active'
    assert 'KB-INVOICE-V1' in _knowledge_ids(client)

    audits = _audit_actions('document.archived', 'document.restored')
    assert [action for action, _detail, _actor in audits] == [
        'document.archived',
        'document.restored',
    ]
    assert audits[0][1]['published_version_id'] == 'KB-INVOICE-V1'
    assert audits[0][1]['tenant_id'] == 'TENANT-DEMO'


def test_future_effective_from_publishes_but_does_not_join_recall(seeded_db):
    client = TestClient(app)
    version = _ready_version(client, effective_from='2999-01-01')

    response = _publish(client, 'DOC-INVOICE', version['id'])

    assert response.status_code == 200
    assert response.json()['status'] == 'published'
    detail, _statuses = _version_statuses(client)
    assert detail['published_version']['id'] == version['id']
    assert retrieve('电子发票怎么开', category='invoice', ctx=CTX) == []


def test_concurrent_publish_conflict_returns_409(seeded_db, monkeypatch):
    client = TestClient(app)
    version = _ready_version(client)
    # Simulate another transaction publishing between our read and write.
    monkeypatch.setattr(
        'app.documents.lifecycle._supersede_current', lambda *args, **kwargs: []
    )

    response = _publish(client, 'DOC-INVOICE', version['id'])

    assert response.status_code == 409
    _detail, statuses = _version_statuses(client)
    assert statuses[version['id']] == 'ready'
    assert statuses['KB-INVOICE-V1'] == 'published'
    assert _audit_actions('version.published') == []


def test_lifecycle_writes_are_manager_only_and_tenant_scoped(seeded_db):
    client = TestClient(app)
    version = _ready_version(client)

    for headers in (CUSTOMER, AGENT):
        assert _publish(client, 'DOC-INVOICE', version['id'], headers).status_code == 403
        assert (
            _archive_version(client, 'DOC-INVOICE', 'KB-INVOICE-V1', headers).status_code
            == 403
        )
        assert (
            _restore_version(client, 'DOC-INVOICE', 'KB-INVOICE-V1', headers).status_code
            == 403
        )
        assert _archive_document(client, 'DOC-INVOICE', headers).status_code == 403
        assert _restore_document(client, 'DOC-INVOICE', headers).status_code == 403

    assert _publish(client, 'DOC-INVOICE', version['id'], OTHER_MANAGER).status_code == 404
    assert (
        _archive_version(
            client, 'DOC-INVOICE', 'KB-INVOICE-V1', OTHER_MANAGER
        ).status_code
        == 404
    )
    assert _archive_document(client, 'DOC-INVOICE', OTHER_MANAGER).status_code == 404
    assert _restore_document(client, 'DOC-INVOICE', OTHER_MANAGER).status_code == 404


def test_publish_does_not_leak_other_documents_versions(seeded_db):
    client = TestClient(app)

    response = _publish(client, 'DOC-INVOICE', 'KB-REFUND-V3')

    assert response.status_code == 404
    with session_scope() as session:
        document = session.get(Document, 'DOC-INVOICE')
        refund = session.get(DocumentVersion, 'KB-REFUND-V3')
    assert document.status == 'active'
    assert refund.status == 'published'
