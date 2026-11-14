from fastapi.testclient import TestClient

from app.fixtures.mock_data import KNOWLEDGE
from app.main import app

TENANT_HEADERS = {
    'X-User-Id': 'U1001',
    'X-Tenant-Id': 'TENANT-DEMO',
    'X-User-Role': 'customer',
}
AGENT_HEADERS = {**TENANT_HEADERS, 'X-User-Role': 'agent'}
OTHER_TENANT_HEADERS = {**TENANT_HEADERS, 'X-Tenant-Id': 'TENANT-OTHER'}
KNOWLEDGE_BY_ID = {row['id']: row for row in KNOWLEDGE}

LEGACY_KNOWLEDGE_KEYS = {
    'id',
    'category',
    'product_type',
    'title',
    'version',
    'status',
    'effective_from',
    'content',
    'keywords',
}


def _get(path: str, **headers):
    return TestClient(app).get(path, headers={**TENANT_HEADERS, **headers})


def test_document_list_returns_customer_visible_seed_documents(seeded_db):
    response = _get('/api/v1/documents')

    assert response.status_code == 200
    rows = {row['id']: row for row in response.json()}
    # A customer never sees the staff_only logistics SOP.
    assert set(rows) == {'DOC-REFUND-ELECTRONICS', 'DOC-INVOICE'}

    refund = rows['DOC-REFUND-ELECTRONICS']
    assert refund['title'] == '电子商品售后退款政策'
    assert refund['category'] == 'refund'
    assert refund['product_type'] == 'electronics'
    assert refund['visibility'] == 'tenant'
    assert refund['status'] == 'active'
    assert refund['version_count'] == 2
    assert refund['published_version']['id'] == 'KB-REFUND-V3'
    assert refund['published_version']['label'] == '2026-v3'

    assert rows['DOC-INVOICE']['published_version']['id'] == 'KB-INVOICE-V1'


def test_document_list_includes_staff_only_for_agent(seeded_db):
    response = _get('/api/v1/documents', **AGENT_HEADERS)

    assert response.status_code == 200
    rows = {row['id']: row for row in response.json()}
    assert set(rows) == {
        'DOC-REFUND-ELECTRONICS',
        'DOC-LOGISTICS-SOP',
        'DOC-INVOICE',
    }
    assert rows['DOC-LOGISTICS-SOP']['published_version']['id'] == 'KB-LOGISTICS-V1'


def test_document_detail_exposes_full_version_history(seeded_db):
    response = _get('/api/v1/documents/DOC-REFUND-ELECTRONICS')

    assert response.status_code == 200
    body = response.json()
    assert body['id'] == 'DOC-REFUND-ELECTRONICS'
    assert [version['id'] for version in body['versions']] == [
        'KB-REFUND-V3',
        'KB-REFUND-V2',
    ]
    superseded = body['versions'][1]
    assert superseded['status'] == 'superseded'
    assert superseded['label'] == '2025-v2'
    assert superseded['effective_from'] == '2025-01-01'
    assert 'content_text' not in superseded


def test_document_version_detail_returns_parsed_text(seeded_db):
    response = _get(
        '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1'
    )

    assert response.status_code == 200
    body = response.json()
    assert body['id'] == 'KB-INVOICE-V1'
    assert body['document_title'] == '电子发票开具说明'
    assert body['label'] == '2026-v1'
    assert body['content_text'] == KNOWLEDGE_BY_ID['KB-INVOICE-V1']['content']


def test_seed_source_file_can_be_downloaded_by_staff(seeded_db):
    response = _get(
        '/api/v1/documents/DOC-LOGISTICS-SOP/versions/KB-LOGISTICS-V1/download',
        **AGENT_HEADERS,
    )

    assert response.status_code == 200
    assert response.content.decode('utf-8') == KNOWLEDGE_BY_ID['KB-LOGISTICS-V1']['content']
    assert 'KB-LOGISTICS-V1.txt' in response.headers['content-disposition']
    assert response.headers['content-type'].startswith('text/plain')


def test_document_list_filters_apply_metadata_and_keyword(seeded_db):
    def ids(query: str, **headers) -> set[str]:
        response = _get(f'/api/v1/documents?{query}', **headers)
        assert response.status_code == 200
        return {row['id'] for row in response.json()}

    assert ids('category=invoice') == {'DOC-INVOICE'}
    assert ids('product_type=electronics') == {'DOC-REFUND-ELECTRONICS'}
    assert ids('visibility=staff_only') == set()
    assert ids('visibility=staff_only', **AGENT_HEADERS) == {'DOC-LOGISTICS-SOP'}
    assert ids('status=archived') == set()
    assert ids('keyword=发票') == {'DOC-INVOICE'}
    assert ids('keyword=二次销售') == {'DOC-REFUND-ELECTRONICS'}
    assert ids('keyword=2025-v2') == {'DOC-REFUND-ELECTRONICS'}
    assert ids('category=refund&visibility=tenant') == {'DOC-REFUND-ELECTRONICS'}


def test_cross_tenant_document_reads_are_404(seeded_db):
    client = TestClient(app)
    headers = OTHER_TENANT_HEADERS

    listing = client.get('/api/v1/documents', headers=headers)
    assert listing.status_code == 200
    assert listing.json() == []

    for path in (
        '/api/v1/documents/DOC-INVOICE',
        '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1',
        '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/download',
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 404, path


def test_unknown_document_and_version_are_404(seeded_db):
    client = TestClient(app)

    assert (
        client.get(
            '/api/v1/documents/DOC-MISSING', headers=TENANT_HEADERS
        ).status_code
        == 404
    )
    assert (
        client.get(
            '/api/v1/documents/DOC-INVOICE/versions/KB-MISSING',
            headers=TENANT_HEADERS,
        ).status_code
        == 404
    )


def test_knowledge_read_model_includes_staff_only_for_agent(seeded_db):
    response = _get('/api/v1/knowledge', **AGENT_HEADERS)

    assert response.status_code == 200
    rows = {row['id']: row for row in response.json()}
    # KB-REFUND-V2 is superseded, so only the three published versions remain.
    assert set(rows) == {'KB-REFUND-V3', 'KB-LOGISTICS-V1', 'KB-INVOICE-V1'}

    for row in rows.values():
        assert set(row) == LEGACY_KNOWLEDGE_KEYS
        assert isinstance(row['keywords'], list)

    refund = rows['KB-REFUND-V3']
    assert refund['category'] == 'refund'
    assert refund['product_type'] == 'electronics'
    assert refund['title'] == '电子商品售后退款政策'
    assert refund['version'] == '2026-v3'
    assert refund['status'] == 'published'
    assert refund['content'] == KNOWLEDGE_BY_ID['KB-REFUND-V3']['content']

    invoice = rows['KB-INVOICE-V1']
    assert invoice['version'] == '2026-v1'
    assert invoice['title'] == '电子发票开具说明'


def test_knowledge_read_model_is_tenant_scoped(seeded_db):
    response = TestClient(app).get('/api/v1/knowledge', headers=OTHER_TENANT_HEADERS)

    assert response.status_code == 200
    assert response.json() == []
