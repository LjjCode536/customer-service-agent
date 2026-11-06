from fastapi.testclient import TestClient
import pytest
from langgraph.types import Command

import app.workflow.service_graph as service_graph
from app.core.config import settings
from app.core.security import SecurityContext
from app.db.database import session_scope
from app.db.models import Order
from app.main import app
from app.services.tasking import create_task, get_task
from app.worker import run_once
from app.workflow.checkpointer import create_checkpointer


def _run_case(client, case_key):
    response = client.post('/api/v1/mock-cases/run', json={'case_key': case_key})
    assert response.status_code == 200
    return response.json()


def _task(client, task_id):
    return client.get(f'/api/v1/tasks/{task_id}').json()


def _approvals(client, task_id):
    return sorted(
        [a for a in client.get('/api/v1/approvals').json() if a['task_id'] == task_id],
        key=lambda a: a['sequence_no'],
    )


def _refunds(client, order_id):
    return [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == order_id]


def _approve_chain(client, approvals):
    for approval in approvals:
        response = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={'status': 'approved'},
        )
        assert response.status_code == 200


def test_low_risk_refund_completes_on_langgraph_main_path(seeded_db):
    client = TestClient(app)

    assert service_graph.GRAPH is not None
    body = _run_case(client, 'low_refund')
    assert run_once() is True

    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    assert task['current_step'] == 'done'
    assert task['result']['refund']['order_id'] == 'O10002'
    assert len(_refunds(client, 'O10002')) == 1


def test_duplicate_refund_returns_duplicate_reason_without_new_refund(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'duplicate_refund')
    assert run_once() is True

    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    assert task['result']['eligibility']['reason'] == 'DUPLICATE_REFUND_REQUEST'
    assert len(_refunds(client, 'O10004')) == 1
    assert any(t['category'] == 'refund_duplicate' for t in client.get('/api/v1/tickets').json())


def test_high_risk_refund_resumes_from_checkpoint_after_graph_restart(seeded_db, monkeypatch):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    assert _task(client, body['task_id'])['status'] == 'waiting_approval'

    # The pause is a real LangGraph interrupt checkpoint, not an application-side flag.
    paused = service_graph.GRAPH.get_state({'configurable': {'thread_id': body['task_id']}})
    assert paused.next == ('human_review',)

    approvals = _approvals(client, body['task_id'])
    assert [a['approver_role'] for a in approvals] == ['refund_specialist', 'service_manager']

    _approve_chain(client, approvals)
    assert _task(client, body['task_id'])['status'] == 'resume_pending'

    # A new graph + new SQLite connection stands in for a worker process restart.
    monkeypatch.setattr(service_graph, 'GRAPH', service_graph.compile_graph(create_checkpointer()))
    assert run_once() is True

    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    assert task['current_step'] == 'refund_submitted'
    assert len(_refunds(client, 'O10003')) == 1


def test_repeated_resume_keeps_single_refund(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    approvals = _approvals(client, body['task_id'])
    _approve_chain(client, approvals)

    assert run_once() is True
    first = _task(client, body['task_id'])
    assert len(_refunds(client, 'O10003')) == 1

    # A duplicate Command(resume=...) must stay idempotent at the refund boundary.
    service_graph.GRAPH.invoke(
        Command(resume={'approved': True}), {'configurable': {'thread_id': body['task_id']}}
    )
    repeated = service_graph.resume_after_approval(first)
    assert repeated['status'] == 'completed'
    assert repeated['refund']['id'] == first['result']['refund']['id']
    assert len(_refunds(client, 'O10003')) == 1


def test_rejection_cancels_later_approvals_and_never_refunds(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    approvals = _approvals(client, body['task_id'])

    rejected = client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decision",
        json={'status': 'rejected', 'comment': '金额异常'},
    )
    assert rejected.status_code == 200
    assert _task(client, body['task_id'])['status'] == 'resume_pending'

    remaining = _approvals(client, body['task_id'])
    assert [a['status'] for a in remaining] == ['rejected', 'cancelled']

    assert run_once() is True
    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    assert task['current_step'] == 'rejected'
    assert task['result']['status'] == 'rejected'
    assert _refunds(client, 'O10003') == []


def test_high_risk_but_ineligible_refund_creates_ticket_without_approval(seeded_db):
    client = TestClient(app)
    with session_scope() as session:
        session.add(
            Order(
                id='O-INELIGIBLE-HIGH',
                tenant_id='TENANT-DEMO',
                user_id='U1002',
                amount=12999,
                status='delivered',
                payment_status='paid',
                shipment_status='delivered',
                tracking_no='SF-INELIGIBLE',
                product_type='electronics',
                created_at='2026-09-14T09:00:00',
                delivered_at='2026-09-16T09:00:00',
            )
        )
    task = create_task(
        SecurityContext('U1002', 'TENANT-DEMO', 'customer'),
        'refund',
        {
            'message': '订单 O-INELIGIBLE-HIGH 帮我退款',
            'order_id': 'O-INELIGIBLE-HIGH',
            'role': 'customer',
        },
    )
    assert run_once() is True

    body = _task(client, task['task_id'])
    assert body['status'] == 'completed'
    assert body['result']['eligibility']['reason'] == 'RETURN_FLOW_REQUIRED'
    assert body['result']['ticket']['category'] == 'refund_review'
    assert _refunds(client, 'O-INELIGIBLE-HIGH') == []
    assert [a for a in client.get('/api/v1/approvals').json() if a['task_id'] == task['task_id']] == []


def test_langgraph_is_required_unless_fallback_is_explicit(seeded_db, monkeypatch):
    client = TestClient(app)
    body = _run_case(client, 'low_refund')
    task = get_task(body['task_id'])

    monkeypatch.setattr(service_graph, 'GRAPH', None)
    monkeypatch.setattr(settings, 'workflow_engine', 'langgraph')
    with pytest.raises(RuntimeError, match='LANGGRAPH_UNAVAILABLE'):
        service_graph.process_task(task)

    monkeypatch.setattr(settings, 'workflow_engine', 'fallback')
    payload = service_graph.process_task(get_task(body['task_id']))
    assert payload['status'] == 'completed'
    assert len(_refunds(client, 'O10002')) == 1
