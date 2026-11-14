"""Role × Visibility matrix across document reads, recall, and Workflow recall."""

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from app.core.security import SecurityContext, service_context
from app.db.database import session_scope
from app.db.models import Document, DocumentVersion
from app.fixtures.mock_data import KNOWLEDGE
from app.main import app
from app.rag.hybrid import cache_document_embeddings, retrieve
from app.services.agent_loop import execute_read_only_tool
from app.services.tasking import create_task, list_events
from app.worker import run_once

CUSTOMER = {
    'X-User-Id': 'U1001',
    'X-Tenant-Id': 'TENANT-DEMO',
    'X-User-Role': 'customer',
}
AGENT = {**CUSTOMER, 'X-User-Role': 'agent'}
MANAGER = {**CUSTOMER, 'X-User-Role': 'manager'}
OTHER_TENANT = {**CUSTOMER, 'X-Tenant-Id': 'TENANT-OTHER'}
OTHER_MANAGER = {**MANAGER, 'X-Tenant-Id': 'TENANT-OTHER'}

CUSTOMER_CTX = SecurityContext('U1001', 'TENANT-DEMO', 'customer')
AGENT_CTX = SecurityContext('A1001', 'TENANT-DEMO', 'agent')
MANAGER_CTX = SecurityContext('M1001', 'TENANT-DEMO', 'manager')
SERVICE_CTX = service_context('TENANT-DEMO')

SOP_BODY = next(row['content'] for row in KNOWLEDGE if row['id'] == 'KB-LOGISTICS-V1')
RESTRICTED_BODY = '内部受限政策：仅 customer 角色可以读取。'
RESTRICTED_QUERY = '受限政策 指定角色'


def _ids(ctx: SecurityContext, query: str = '物流异常处理SOP') -> set[str]:
    return {
        item['id']
        for item in retrieve(query, top_n=10, ctx=ctx)
    }


def _add_restricted_document() -> None:
    with session_scope() as session:
        session.add(
            Document(
                id='DOC-RESTRICTED',
                tenant_id='TENANT-DEMO',
                title='受限内部政策',
                category='general',
                product_type='all',
                visibility='restricted',
                allowed_roles=['customer'],
                status='active',
                owner_id='seed',
            )
        )
        session.flush()
        session.add(
            DocumentVersion(
                id='KB-RESTRICTED-V1',
                document_id='DOC-RESTRICTED',
                version_number=1,
                label='2026-v1',
                status='published',
                effective_from='2020-01-01',
                content_text=RESTRICTED_BODY,
                source_filename='restricted.txt',
                published_at=None,
            )
        )
    cache_document_embeddings()


def _document_ids(client: TestClient, headers) -> set[str]:
    response = client.get('/api/v1/documents', headers=headers)
    assert response.status_code == 200
    return {row['id'] for row in response.json()}


def test_service_role_cannot_be_claimed_through_http_headers(seeded_db):
    response = TestClient(app).get(
        '/api/v1/documents', headers={**CUSTOMER, 'X-User-Role': 'service'}
    )
    assert response.status_code == 403
    assert 'service' in response.json()['detail']


def test_document_list_applies_role_visibility_matrix(seeded_db):
    client = TestClient(app)

    assert _document_ids(client, CUSTOMER) == {
        'DOC-REFUND-ELECTRONICS',
        'DOC-INVOICE',
    }
    assert _document_ids(client, AGENT) == {
        'DOC-REFUND-ELECTRONICS',
        'DOC-LOGISTICS-SOP',
        'DOC-INVOICE',
    }
    assert _document_ids(client, MANAGER) == {
        'DOC-REFUND-ELECTRONICS',
        'DOC-LOGISTICS-SOP',
        'DOC-INVOICE',
    }


def test_staff_only_detail_and_download_are_403_for_customer_and_404_cross_tenant(
    seeded_db,
):
    client = TestClient(app)
    for path in (
        '/api/v1/documents/DOC-LOGISTICS-SOP',
        '/api/v1/documents/DOC-LOGISTICS-SOP/versions/KB-LOGISTICS-V1',
        '/api/v1/documents/DOC-LOGISTICS-SOP/versions/KB-LOGISTICS-V1/download',
    ):
        assert client.get(path, headers=CUSTOMER).status_code == 403, path
        assert client.get(path, headers=AGENT).status_code == 200, path
        # Cross-tenant reads never reveal whether the document exists.
        assert client.get(path, headers=OTHER_TENANT).status_code == 404, path


def test_knowledge_read_model_applies_caller_visibility(seeded_db):
    client = TestClient(app)

    customer_ids = {
        row['id']
        for row in client.get('/api/v1/knowledge', headers=CUSTOMER).json()
    }
    assert customer_ids == {'KB-REFUND-V3', 'KB-INVOICE-V1'}

    agent_ids = {
        row['id']
        for row in client.get('/api/v1/knowledge', headers=AGENT).json()
    }
    assert agent_ids == {'KB-REFUND-V3', 'KB-LOGISTICS-V1', 'KB-INVOICE-V1'}


def test_retrieve_applies_visibility_for_every_role(seeded_db):
    assert 'KB-LOGISTICS-V1' not in _ids(CUSTOMER_CTX)
    assert 'KB-LOGISTICS-V1' in _ids(AGENT_CTX)
    assert 'KB-LOGISTICS-V1' in _ids(MANAGER_CTX)
    assert 'KB-LOGISTICS-V1' in _ids(SERVICE_CTX)


def test_restricted_documents_follow_allowed_roles_and_service_override(seeded_db):
    _add_restricted_document()
    client = TestClient(app)

    # allowed_roles grants customer; agent is not listed and is denied.
    assert 'KB-RESTRICTED-V1' in _ids(CUSTOMER_CTX, RESTRICTED_QUERY)
    assert 'KB-RESTRICTED-V1' not in _ids(AGENT_CTX, RESTRICTED_QUERY)
    # The internal service principal always sees restricted tenant documents.
    assert 'KB-RESTRICTED-V1' in _ids(SERVICE_CTX, RESTRICTED_QUERY)

    assert (
        client.get('/api/v1/documents/DOC-RESTRICTED', headers=CUSTOMER).status_code
        == 200
    )
    assert (
        client.get('/api/v1/documents/DOC-RESTRICTED', headers=AGENT).status_code
        == 403
    )
    # manager is not implicitly granted restricted documents either.
    assert (
        client.get('/api/v1/documents/DOC-RESTRICTED', headers=MANAGER).status_code
        == 403
    )

    customer_knowledge = {
        row['id']
        for row in client.get('/api/v1/knowledge', headers=CUSTOMER).json()
    }
    agent_knowledge = {
        row['id']
        for row in client.get('/api/v1/knowledge', headers=AGENT).json()
    }
    assert 'KB-RESTRICTED-V1' in customer_knowledge
    assert 'KB-RESTRICTED-V1' not in agent_knowledge


def test_agent_loop_retrieval_uses_the_callers_security_context(seeded_db):
    customer_ids = {
        item['id']
        for item in execute_read_only_tool(
            'retrieve_knowledge',
            {'query': '物流异常处理SOP', 'top_n': 10},
            CUSTOMER_CTX,
        )
    }
    agent_ids = {
        item['id']
        for item in execute_read_only_tool(
            'retrieve_knowledge',
            {'query': '物流异常处理SOP', 'top_n': 10},
            AGENT_CTX,
        )
    }

    assert 'KB-LOGISTICS-V1' not in customer_ids
    assert 'KB-LOGISTICS-V1' in agent_ids


def test_retrieve_has_no_security_context_default():
    ctx = inspect.signature(retrieve).parameters['ctx']
    assert ctx.kind is inspect.Parameter.KEYWORD_ONLY
    assert ctx.default is inspect.Parameter.empty


def test_workflow_reads_staff_only_sop_without_leaking_its_body(seeded_db):
    client = TestClient(app)
    task = create_task(
        CUSTOMER_CTX,
        'complaint',
        {
            'message': '订单 O10001 物流异常，我要投诉',
            'order_id': 'O10001',
            'role': 'customer',
        },
    )
    assert run_once() is True

    body = client.get(f"/api/v1/tasks/{task['task_id']}").json()
    assert body['status'] == 'completed'
    assert body['result']['ticket']['category'] == 'complaint'

    steps = [
        step
        for step in client.get('/api/v1/traces').json()
        if step['trace_id'] == task['trace_id']
    ]
    policy = next(step for step in steps if step['node'] == 'retrieve_policy')
    knowledge = policy['output']['knowledge']
    assert '物流异常处理SOP' in {doc['title'] for doc in knowledge}
    for doc in knowledge:
        assert set(doc) == {'title', 'version', 'rerank_score'}

    node_events = [
        event
        for event in list_events(task['task_id'])
        if event['event_type'] == 'node_completed'
        and event['payload']['node'] == 'retrieve_policy'
    ]
    assert node_events
    for event in node_events:
        for doc in event['payload']['output']['knowledge']:
            assert set(doc) == {'title', 'version', 'rerank_score'}

    visible_payloads = json.dumps(
        {'task': body, 'trace': steps, 'events': list_events(task['task_id'])},
        ensure_ascii=False,
    )
    assert SOP_BODY not in visible_payloads


@pytest.mark.parametrize('headers', [CUSTOMER, AGENT])
def test_non_managers_cannot_use_any_document_write(seeded_db, headers):
    client = TestClient(app)
    upload = {'files': {'file': ('policy.txt', b'policy body', 'text/plain')}}

    requests = [
        ('POST', '/api/v1/documents', {**upload, 'data': {'title': 'nope'}}),
        ('POST', '/api/v1/documents/DOC-INVOICE/versions', upload),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/publish',
            {},
        ),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/archive',
            {},
        ),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/restore',
            {},
        ),
        ('POST', '/api/v1/documents/DOC-INVOICE/archive', {}),
        ('POST', '/api/v1/documents/DOC-INVOICE/restore', {}),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/retry',
            {},
        ),
    ]

    for method, path, kwargs in requests:
        response = client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == 403, path


def test_cross_tenant_document_writes_are_404(seeded_db):
    client = TestClient(app)
    upload = {'files': {'file': ('policy.txt', b'policy body', 'text/plain')}}

    requests = [
        ('POST', '/api/v1/documents/DOC-INVOICE/versions', upload),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/publish',
            {},
        ),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/archive',
            {},
        ),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/restore',
            {},
        ),
        ('POST', '/api/v1/documents/DOC-INVOICE/archive', {}),
        ('POST', '/api/v1/documents/DOC-INVOICE/restore', {}),
        (
            'POST',
            '/api/v1/documents/DOC-INVOICE/versions/KB-INVOICE-V1/retry',
            {},
        ),
    ]

    for method, path, kwargs in requests:
        response = client.request(method, path, headers=OTHER_MANAGER, **kwargs)
        assert response.status_code == 404, path
