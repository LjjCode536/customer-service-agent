from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import SecurityContext
from app.db.database import loads, session_scope
from app.db.models import AuditLog
from app.main import app
from app.services.tasking import create_task, list_events
from app.worker import run_once
from app.workflow import service_graph

COMPLAINT_NODES = {
    'load_customer',
    'load_order',
    'retrieve_policy',
    'load_logistics',
    'assess_complaint',
    'create_complaint_ticket',
    'answer',
}
REFUND_ONLY_NODES = {
    'load_refunds',
    'check_eligibility',
    'risk_check',
    'human_review',
    'submit_refund',
}


def _run_case(client, case_key):
    response = client.post('/api/v1/mock-cases/run', json={'case_key': case_key})
    assert response.status_code == 200
    return response.json()


def _task(client, task_id):
    return client.get(f'/api/v1/tasks/{task_id}').json()


def _trace_steps(client, trace_id):
    return [
        step
        for step in client.get('/api/v1/traces').json()
        if step['trace_id'] == trace_id
    ]


def _trace_nodes(client, trace_id):
    return {step['node'] for step in _trace_steps(client, trace_id)}


def _refunds(client, order_id):
    return [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == order_id]


def test_vip_complaint_uses_dedicated_path_and_never_refunds(seeded_db):
    client = TestClient(app)

    assert service_graph.GRAPH is not None
    body = _run_case(client, 'vip_complaint')
    assert body['mode'] == 'workflow'
    assert run_once() is True

    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    assert task['current_step'] == 'done'

    ticket = task['result']['ticket']
    assert ticket['category'] == 'complaint'
    assert ticket['priority'] == 'P1'
    assert ticket['assigned_group'] == 'complaint_specialist'

    assert _refunds(client, 'O10005') == []
    assert task['result'].get('refund') is None

    steps = _trace_steps(client, task['trace_id'])
    nodes = {step['node'] for step in steps}
    assert COMPLAINT_NODES <= nodes
    assert nodes & REFUND_ONLY_NODES == set()

    policy = next(step for step in steps if step['node'] == 'retrieve_policy')
    knowledge = policy['output']['knowledge']
    assert '物流异常处理SOP' in {doc['title'] for doc in knowledge}
    # The service principal read the SOP body, but the customer-visible trace
    # only keeps citation metadata.
    assert all(set(doc) == {'title', 'version', 'rerank_score'} for doc in knowledge)


def test_stale_logistics_participates_in_complaint_priority(seeded_db):
    client = TestClient(app)

    task = create_task(
        SecurityContext('U1001', 'TENANT-DEMO', 'customer'),
        'complaint',
        {
            'message': '订单 O10001 物流两天没更新，我要投诉',
            'order_id': 'O10001',
            'role': 'customer',
        },
    )
    assert run_once() is True

    body = _task(client, task['task_id'])
    assert body['status'] == 'completed'
    ticket = body['result']['ticket']
    assert ticket['priority'] == 'P2'
    assert ticket['assigned_group'] == 'complaint_specialist'
    assert body['result']['complaint']['priority'] == ticket['priority']
    assert 'LOGISTICS_STALE' in body['result']['complaint']['reasons']
    assert _refunds(client, 'O10001') == []
    assert body['result'].get('refund') is None


def test_fresh_logistics_complaint_stays_p3(seeded_db):
    client = TestClient(app)

    task = create_task(
        SecurityContext('U1002', 'TENANT-DEMO', 'customer'),
        'complaint',
        {
            'message': '订单 O10002 我要投诉',
            'order_id': 'O10002',
            'role': 'customer',
        },
    )
    assert run_once() is True

    body = _task(client, task['task_id'])
    assert body['status'] == 'completed'
    ticket = body['result']['ticket']
    assert ticket['priority'] == 'P3'
    assert ticket['assigned_group'] == 'complaint_specialist'
    assert body['result']['complaint']['reasons'] == ['COMPLAINT']
    assert _refunds(client, 'O10002') == []
    assert body['result'].get('refund') is None


def test_complaint_ticket_writes_event_store_and_audit(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'vip_complaint')
    assert run_once() is True
    task = _task(client, body['task_id'])
    ticket = task['result']['ticket']

    created_events = [
        event
        for event in list_events(body['task_id'])
        if event['event_type'] == 'ticket_created'
    ]
    assert len(created_events) == 1
    assert created_events[0]['payload']['ticket_id'] == ticket['id']

    with session_scope() as session:
        audits = session.scalars(
            select(AuditLog).where(
                AuditLog.trace_id == task['trace_id'],
                AuditLog.action == 'ticket_created',
            )
        ).all()
    assert len(audits) == 1
    detail = loads(audits[0].detail_json, {})
    assert detail['ticket_id'] == ticket['id']
    assert detail['priority'] == 'P1'
    assert detail['assigned_group'] == 'complaint_specialist'
    assert 'VIP' in detail['reasons']
