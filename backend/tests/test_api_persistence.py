from fastapi.testclient import TestClient

from app.main import app
from app.worker import run_once


def _run_case(client, case_key):
    response = client.post('/api/v1/mock-cases/run', json={'case_key': case_key})
    assert response.status_code == 200
    return response.json()


def _chat(client, message, **headers):
    return client.post('/api/v1/chat', json={'message': message}, headers=headers)


def test_order_query_uses_direct_tool(seeded_db):
    client = TestClient(app)

    response = _chat(client, '帮我查一下订单 O10002 状态', **{'X-User-Id': 'U1002'})

    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'direct_tool'
    assert body['intent'] == 'order_query'
    assert body['order']['id'] == 'O10002'


def test_cross_user_order_query_returns_403(seeded_db):
    client = TestClient(app)

    response = _chat(client, '订单 O10003 状态', **{'X-User-Id': 'U1002'})

    assert response.status_code == 403


def test_missing_order_returns_404(seeded_db):
    client = TestClient(app)

    response = _chat(client, '订单 O99999 状态', **{'X-User-Id': 'U1002'})

    assert response.status_code == 404


def test_cross_tenant_order_query_is_not_visible(seeded_db):
    client = TestClient(app)

    response = _chat(
        client,
        '订单 O10001 状态',
        **{'X-User-Id': 'U1001', 'X-Tenant-Id': 'TENANT-OTHER'},
    )

    assert response.status_code == 404


def test_cross_tenant_logistics_query_is_not_visible(seeded_db):
    client = TestClient(app)

    response = _chat(
        client,
        '订单 O10001 物流到哪了',
        **{'X-User-Id': 'U1001', 'X-Tenant-Id': 'TENANT-OTHER'},
    )

    assert response.status_code == 404


def test_logistics_query_creates_p2_ticket(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'logistics_stale')

    assert body['mode'] == 'direct_tool'
    assert body['ticket']['category'] == 'logistics_abnormal'
    assert body['ticket']['priority'] == 'P2'


def test_vip_complaint_creates_p1_ticket_for_complaint_specialist(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'vip_complaint')
    assert body['mode'] == 'workflow'
    assert run_once() is True

    tickets = [
        t
        for t in client.get('/api/v1/tickets').json()
        if t['order_id'] == 'O10005' and t['category'] == 'complaint'
    ]
    assert len(tickets) == 1
    assert tickets[0]['priority'] == 'P1'
    assert tickets[0]['assigned_group'] == 'complaint_specialist'


def test_low_risk_refund_runs_through_worker(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'low_refund')
    assert body['mode'] == 'workflow'
    assert run_once() is True

    task = client.get(f"/api/v1/tasks/{body['task_id']}").json()
    refunds = [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == 'O10002']
    assert task['status'] == 'completed'
    assert len(refunds) == 1


def test_high_risk_refund_enforces_approval_order_then_resumes(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    assert client.get(f"/api/v1/tasks/{body['task_id']}").json()['status'] == 'waiting_approval'

    approvals = sorted(
        [a for a in client.get('/api/v1/approvals').json() if a['task_id'] == body['task_id']],
        key=lambda a: a['sequence_no'],
    )
    assert [(a['approver_role'], a['sequence_no']) for a in approvals] == [
        ('refund_specialist', 1),
        ('service_manager', 2),
    ]

    skip = client.post(f"/api/v1/approvals/{approvals[1]['id']}/decision", json={'status': 'approved'})
    assert skip.status_code == 409

    for approval in approvals:
        decision = client.post(
            f"/api/v1/approvals/{approval['id']}/decision", json={'status': 'approved'}
        )
        assert decision.status_code == 200

    assert client.get(f"/api/v1/tasks/{body['task_id']}").json()['status'] == 'resume_pending'
    assert run_once() is True
    assert client.get(f"/api/v1/tasks/{body['task_id']}").json()['status'] == 'completed'
    refunds = [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == 'O10003']
    assert len(refunds) == 1


def test_duplicate_refund_is_blocked(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'duplicate_refund')
    assert run_once() is True

    refunds = [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == 'O10004']
    tickets = [
        t
        for t in client.get('/api/v1/tickets').json()
        if t['order_id'] == 'O10004' and t['category'] == 'refund_duplicate'
    ]
    assert len(refunds) == 1
    assert len(tickets) == 1
    assert client.get(f"/api/v1/tasks/{body['task_id']}").json()['status'] == 'completed'
