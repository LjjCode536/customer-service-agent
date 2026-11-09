import hashlib
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import loads, session_scope
from app.db.models import AuditLog, DocumentVersion
from app.documents.parsing import parse_document
from app.llm.provider import ProviderError, set_provider
from app.main import app
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


def _upload(
    client: TestClient,
    *,
    filename: str = 'policy.txt',
    content: bytes = '退款政策正文。'.encode(),
    headers: dict = MANAGER,
    **fields,
):
    data = {
        'title': '测试退款政策',
        'category': 'refund',
        'product_type': 'electronics',
        'visibility': 'tenant',
    }
    data.update(fields)
    return client.post(
        '/api/v1/documents',
        files={'file': (filename, content, 'text/plain')},
        data=data,
        headers=headers,
    )


def _upload_version(
    client: TestClient,
    document_id: str,
    *,
    filename: str = 'policy-v2.txt',
    content: bytes = '新版本退款政策。'.encode(),
    headers: dict = MANAGER,
    **fields,
):
    data = {}
    data.update(fields)
    return client.post(
        f'/api/v1/documents/{document_id}/versions',
        files={'file': (filename, content, 'text/plain')},
        data=data,
        headers=headers,
    )


def _document(client: TestClient, document_id: str, headers: dict = MANAGER):
    return client.get(f'/api/v1/documents/{document_id}', headers=headers)


def _version(
    client: TestClient,
    document_id: str,
    version_id: str,
    headers: dict = MANAGER,
):
    return client.get(
        f'/api/v1/documents/{document_id}/versions/{version_id}', headers=headers
    )


def _audit_actions(*actions: str):
    with session_scope() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(actions))
            .order_by(AuditLog.id)
        ).all()
    return [(row.action, loads(row.detail_json, {}), row.actor_id) for row in rows]


def test_manager_upload_returns_202_and_creates_draft_document(seeded_db):
    client = TestClient(app)
    content = '第一行\r\n第二行'.encode()

    response = _upload(client, content=content, title='新退款政策')

    assert response.status_code == 202
    body = response.json()
    assert body['status'] == 'draft'
    assert body['version_number'] == 1
    assert body['document_status'] == 'active'
    assert body['source_filename'] == 'policy.txt'
    assert body['checksum'] == hashlib.sha256(content).hexdigest()

    detail = _document(client, body['document_id']).json()
    assert detail['title'] == '新退款政策'
    assert detail['owner_id'] == 'M1001'
    assert detail['category'] == 'refund'
    assert detail['version_count'] == 1
    assert detail['versions'][0]['id'] == body['version_id']
    assert detail['versions'][0]['status'] == 'draft'

    version = _version(client, body['document_id'], body['version_id']).json()
    assert version['status'] == 'draft'
    assert version['content_text'] is None

    audits = _audit_actions('document.created', 'version.created')
    assert [action for action, _detail, _actor in audits] == [
        'document.created',
        'version.created',
    ]
    assert audits[0][2] == 'M1001'
    assert audits[1][1]['document_id'] == body['document_id']
    assert audits[1][1]['version_id'] == body['version_id']


@pytest.mark.parametrize('headers', [CUSTOMER, AGENT])
def test_non_manager_writes_are_forbidden(seeded_db, headers):
    client = TestClient(app)

    assert _upload(client, headers=headers).status_code == 403
    assert _upload_version(client, 'DOC-INVOICE', headers=headers).status_code == 403
    assert (
        client.post(
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/retry',
            headers=headers,
        ).status_code
        == 403
    )


def test_cross_tenant_version_writes_are_404(seeded_db):
    client = TestClient(app)

    assert (
        _upload_version(client, 'DOC-INVOICE', headers=OTHER_MANAGER).status_code
        == 404
    )
    assert (
        client.post(
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/retry',
            headers=OTHER_MANAGER,
        ).status_code
        == 404
    )


def test_upload_rejects_unsupported_oversized_and_binary_files(seeded_db, monkeypatch):
    client = TestClient(app)

    unsupported = _upload(client, filename='policy.pdf', content=b'%PDF-1.7')
    assert unsupported.status_code == 415

    binary = _upload(client, content=b'\x00\x01\x02not-text')
    assert binary.status_code == 415

    monkeypatch.setattr('app.api.router.MAX_DOCUMENT_BYTES', 8)
    oversized = _upload(client, content=b'x' * 9)
    assert oversized.status_code == 413


def test_worker_ingests_uploaded_text_to_ready(seeded_db, fake_provider):
    client = TestClient(app)
    content = '第一行\r\n第二行\r第三行\n'.encode()
    body = _upload(client, content=content).json()

    assert run_once() is True

    version = _version(client, body['document_id'], body['version_id']).json()
    assert version['status'] == 'ready'
    assert version['content_text'] == '第一行\n第二行\n第三行\n'
    assert version['error_code'] is None
    assert version['error_message'] is None

    with session_scope() as session:
        row = session.get(DocumentVersion, body['version_id'])
        assert row.embedding
        assert row.embedding_model == fake_provider.embedding_model
        assert row.embedding_dimensions == fake_provider.dimensions
        assert row.embedding_generated_at is not None

    download = client.get(
        f"/api/v1/documents/{body['document_id']}/versions/{body['version_id']}/download",
        headers=MANAGER,
    )
    assert download.status_code == 200
    assert download.content == content

    assert _audit_actions('version.failed') == []


def test_duplicate_checksum_is_409_and_force_allows_new_document_or_version(
    seeded_db,
):
    client = TestClient(app)
    content = '同租户重复内容。'.encode()
    first = _upload(client, content=content).json()

    duplicate = _upload(client, content=content, title='重复标题')
    assert duplicate.status_code == 409
    body = duplicate.json()
    assert body['existing_document']['document_id'] == first['document_id']
    assert body['existing_document']['version_id'] == first['version_id']
    assert body['existing_document']['checksum'] == first['checksum']

    forced = _upload(client, content=content, title='强制副本', force='true')
    assert forced.status_code == 202
    assert forced.json()['document_id'] != first['document_id']
    assert forced.json()['status'] == 'draft'

    version_duplicate = _upload_version(
        client, 'DOC-INVOICE', content=content
    )
    assert version_duplicate.status_code == 409
    version_forced = _upload_version(
        client, 'DOC-INVOICE', content=content, force='true'
    )
    assert version_forced.status_code == 202
    assert version_forced.json()['version_number'] == 2

    other_tenant = _upload(client, content=content, headers=OTHER_MANAGER)
    assert other_tenant.status_code == 202


def test_new_version_upload_increments_version_number(seeded_db):
    client = TestClient(app)

    response = _upload_version(
        client,
        'DOC-INVOICE',
        content='二〇二六版发票说明。'.encode(),
        label='2026-v2',
        effective_from='2026-06-01',
    )

    assert response.status_code == 202
    body = response.json()
    assert body['document_id'] == 'DOC-INVOICE'
    assert body['version_number'] == 2
    assert body['status'] == 'draft'

    detail = _document(client, 'DOC-INVOICE').json()
    assert detail['versions'][0]['id'] == body['version_id']
    assert detail['versions'][0]['label'] == '2026-v2'
    assert detail['versions'][0]['effective_from'] == '2026-06-01'

    assert run_once() is True
    version = _version(client, 'DOC-INVOICE', body['version_id']).json()
    assert version['status'] == 'ready'
    assert version['content_text'] == '二〇二六版发票说明。'

    audits = _audit_actions('version.created')
    assert any(detail['version_id'] == body['version_id'] for _action, detail, _actor in audits)


def test_unknown_document_version_upload_is_404(seeded_db):
    client = TestClient(app)

    assert _upload_version(client, 'DOC-MISSING').status_code == 404


def test_invalid_metadata_is_422(seeded_db):
    client = TestClient(app)

    assert _upload(client, visibility='public').status_code == 422
    assert _upload(client, visibility='restricted').status_code == 422
    assert _upload(client, allowed_roles='owner').status_code == 422
    assert _upload(client, effective_from='not-a-date').status_code == 422
    assert _upload(client, label='x' * 65).status_code == 422
    missing_title = client.post(
        '/api/v1/documents',
        files={'file': ('policy.txt', b'content', 'text/plain')},
        data={},
        headers=MANAGER,
    )
    assert missing_title.status_code == 422


def test_restricted_visibility_keeps_allowed_roles(seeded_db):
    client = TestClient(app)

    response = _upload(
        client,
        visibility='restricted',
        allowed_roles=['agent', 'manager'],
    )

    assert response.status_code == 202
    detail = _document(client, response.json()['document_id']).json()
    assert detail['visibility'] == 'restricted'
    assert detail['allowed_roles'] == ['agent', 'manager']


def test_filename_is_display_metadata_only(seeded_db):
    client = TestClient(app)
    content = '内容寻址存储验证。'.encode()

    body = _upload(client, filename='../../etc/passwd.txt', content=content).json()
    version = _version(client, body['document_id'], body['version_id']).json()

    assert version['source_filename'] == 'passwd.txt'
    with session_scope() as session:
        row = session.get(DocumentVersion, body['version_id'])
        assert row.storage_key == f'{body["checksum"][:2]}/{body["checksum"]}'

    forced = _upload(client, content=content, force='true').json()
    with session_scope() as session:
        first = session.get(DocumentVersion, body['version_id'])
        second = session.get(DocumentVersion, forced['version_id'])
        assert first.storage_key == second.storage_key


def test_empty_upload_fails_asynchronously_and_retry_requires_failed(seeded_db):
    client = TestClient(app)
    body = _upload(client, content=b'  \r\n\t ').json()

    assert run_once() is True
    version = _version(client, body['document_id'], body['version_id']).json()
    assert version['status'] == 'failed'
    assert version['error_code'] == 'empty_content'
    assert version['error_message']
    assert version['content_text'] is None

    failed_audits = _audit_actions('version.failed')
    assert failed_audits[-1][1]['version_id'] == body['version_id']
    assert failed_audits[-1][1]['error_code'] == 'empty_content'

    not_failed = client.post(
        '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/retry',
        headers=MANAGER,
    )
    assert not_failed.status_code == 400


def test_slow_parse_marks_version_failed_with_parse_timeout(seeded_db, monkeypatch):
    client = TestClient(app)
    body = _upload(client, content='超时正文。'.encode()).json()

    def slow_parse(filename, data):
        time.sleep(0.2)
        return parse_document(filename, data)

    monkeypatch.setattr('app.documents.ingestion.parse_document', slow_parse)
    monkeypatch.setattr('app.documents.ingestion.PARSE_TIMEOUT_SECONDS', 0.01)

    assert run_once() is True
    version = _version(client, body['document_id'], body['version_id']).json()
    assert version['status'] == 'failed'
    assert version['error_code'] == 'parse_timeout'
    assert 'limit' in version['error_message']


class FlakyEmbeddingProvider(DeterministicProvider):
    def __init__(self):
        super().__init__()
        self.fail_next_embed = True

    def embed(self, texts):
        if self.fail_next_embed:
            self.fail_next_embed = False
            raise ProviderError('fake embedding unavailable')
        return super().embed(texts)


def test_failed_ingestion_records_reason_and_retry_does_not_reupload(
    seeded_db, fake_provider
):
    client = TestClient(app)
    content = '可以重试的退款正文。'.encode()
    body = _upload(client, content=content).json()

    flaky = FlakyEmbeddingProvider()
    set_provider(flaky)
    try:
        assert run_once() is True
    finally:
        set_provider(fake_provider)

    failed = _version(client, body['document_id'], body['version_id']).json()
    assert failed['status'] == 'failed'
    assert failed['error_code'] == 'embedding_failed'
    assert 'fake embedding unavailable' in failed['error_message']

    retry = client.post(
        f"/api/v1/documents/{body['document_id']}/versions/{body['version_id']}/retry",
        headers=MANAGER,
    )
    assert retry.status_code == 202
    assert retry.json()['status'] == 'draft'
    assert retry.json()['version_number'] == 1

    assert run_once() is True
    ready = _version(client, body['document_id'], body['version_id']).json()
    assert ready['status'] == 'ready'
    assert ready['content_text'] == '可以重试的退款正文。'
    assert ready['error_code'] is None
    assert ready['error_message'] is None

    download = client.get(
        f"/api/v1/documents/{body['document_id']}/versions/{body['version_id']}/download",
        headers=MANAGER,
    )
    assert download.content == content

    detail = _document(client, body['document_id']).json()
    assert detail['version_count'] == 1

    assert [action for action, _detail, _actor in _audit_actions('version.retried')] == [
        'version.retried'
    ]
    failed_audit = _audit_actions('version.failed')[-1][1]
    assert failed_audit['error_code'] == 'embedding_failed'
