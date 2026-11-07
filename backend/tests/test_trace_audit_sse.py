import asyncio
import json
import threading

from fastapi.testclient import TestClient
from langgraph.types import Command
from sqlalchemy import select

from app.core.config import settings
from app.core.security import SecurityContext
from app.db.database import loads, session_scope
from app.db.models import AuditLog
from app.main import app
from app.services.tasking import create_task, list_events, update_task
from app.worker import run_once
from app.workflow import service_graph

LOW_RISK_NODES = {
    'load_customer',
    'load_order',
    'retrieve_policy',
    'load_refunds',
    'check_eligibility',
    'risk_check',
    'submit_refund',
    'answer',
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


def _audits(trace_id):
    with session_scope() as session:
        return session.scalars(
            select(AuditLog)
            .where(AuditLog.trace_id == trace_id)
            .order_by(AuditLog.id)
        ).all()


def _approvals(client, task_id):
    return sorted(
        [a for a in client.get('/api/v1/approvals').json() if a['task_id'] == task_id],
        key=lambda a: a['sequence_no'],
    )


def _sse_frames(body):
    """Parse the data frames from an SSE response body, ignoring ping comments."""
    frames = []
    for block in body.replace('\r\n', '\n').split('\n\n'):
        data = [
            line[len('data:'):].strip()
            for line in block.splitlines()
            if line.startswith('data:')
        ]
        if data:
            frames.append(json.loads('\n'.join(data)))
    return frames


def test_post_only_queues_task_and_worker_traces_every_node(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'low_refund')
    assert body['mode'] == 'workflow'

    # POST only creates the Task; no workflow node has run yet.
    queued = _task(client, body['task_id'])
    assert queued['status'] == 'pending'
    assert queued['current_step'] == 'queued'
    assert [event['event_type'] for event in list_events(body['task_id'])] == [
        'task_created'
    ]

    assert run_once() is True
    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'

    steps = _trace_steps(client, task['trace_id'])
    by_node = {step['node']: step for step in steps}
    assert LOW_RISK_NODES <= set(by_node)
    for node in LOW_RISK_NODES:
        step = by_node[node]
        assert step['status'] == 'success'
        assert step['latency_ms'] >= 0
        assert step['input']
        assert step['output']
        assert step['error'] is None

    # Trace input is the node's real input, not just a constant envelope.
    assert by_node['load_order']['input']['order_id'] == 'O10002'
    assert by_node['load_refunds']['output']['refunds'] == []

    events = list_events(body['task_id'])
    started = {
        event['payload']['node']
        for event in events
        if event['event_type'] == 'node_start'
    }
    completed = {
        event['payload']['node']
        for event in events
        if event['event_type'] == 'node_completed'
    }
    assert LOW_RISK_NODES <= started
    assert LOW_RISK_NODES <= completed


def test_failed_node_records_error_trace_and_node_failed_event(seeded_db):
    client = TestClient(app)
    task = create_task(
        SecurityContext('U1001', 'TENANT-DEMO', 'customer'),
        'refund',
        {
            'message': '订单 O-MISSING 帮我退款',
            'order_id': 'O-MISSING',
            'role': 'customer',
        },
    )

    assert run_once() is True
    body = _task(client, task['task_id'])
    assert body['status'] == 'failed'
    assert 'ORDER_NOT_FOUND' in body['error']

    failed = [step for step in _trace_steps(client, task['trace_id']) if step['status'] == 'failed']
    assert [step['node'] for step in failed] == ['load_order']
    assert 'ORDER_NOT_FOUND' in failed[0]['error']
    assert failed[0]['latency_ms'] >= 0

    events = list_events(task['task_id'])
    node_failed = [event for event in events if event['event_type'] == 'node_failed']
    assert node_failed
    assert node_failed[0]['payload']['node'] == 'load_order'
    assert 'ORDER_NOT_FOUND' in node_failed[0]['payload']['error']
    task_failed = [event for event in events if event['event_type'] == 'task_failed']
    assert task_failed
    assert 'ORDER_NOT_FOUND' in task_failed[0]['payload']['error']


def test_approval_and_refund_side_effects_write_audit(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    assert _task(client, body['task_id'])['status'] == 'waiting_approval'

    approvals = _approvals(client, body['task_id'])
    for approval in approvals:
        response = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={'status': 'approved', 'approver_user_id': 'manager'},
        )
        assert response.status_code == 200

    assert run_once() is True
    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    refund = task['result']['refund']

    audits = _audits(task['trace_id'])
    actions = [audit.action for audit in audits]
    assert actions.count('approval_approved') == 2
    assert 'refund_submitted' in actions

    approval_audits = [a for a in audits if a.action == 'approval_approved']
    assert [loads(a.detail_json, {})['approver_role'] for a in approval_audits] == [
        'refund_specialist',
        'service_manager',
    ]
    assert all(a.actor_id == 'manager' for a in approval_audits)

    refund_audit = next(a for a in audits if a.action == 'refund_submitted')
    detail = loads(refund_audit.detail_json, {})
    assert detail['refund_id'] == refund['id']
    assert detail['order_id'] == 'O10003'
    assert detail['external_refund_id'] == refund['external_refund_id']


def test_rejected_approval_writes_audit_without_refund_audit(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    approvals = _approvals(client, body['task_id'])

    response = client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decision",
        json={'status': 'rejected', 'comment': '金额异常'},
    )
    assert response.status_code == 200
    assert run_once() is True

    task = _task(client, body['task_id'])
    assert task['result']['status'] == 'rejected'
    actions = [audit.action for audit in _audits(task['trace_id'])]
    assert 'approval_rejected' in actions
    assert 'refund_submitted' not in actions
    assert [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == 'O10003'] == []


def test_direct_tool_ticket_writes_audit(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'logistics_stale')
    assert body['mode'] == 'direct_tool'
    ticket = body['ticket']

    audits = [
        loads(audit.detail_json, {})
        for audit in _audits(None)
        if audit.action == 'ticket_created'
    ]
    assert any(
        audit.get('ticket_id') == ticket['id']
        and audit.get('source') == 'direct_tool'
        and audit.get('order_id') == 'O10001'
        for audit in audits
    )


def test_repeated_resume_does_not_duplicate_refund_audit(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'high_refund')
    assert run_once() is True
    for approval in _approvals(client, body['task_id']):
        response = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={'status': 'approved'},
        )
        assert response.status_code == 200
    assert run_once() is True

    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'
    assert [a.action for a in _audits(task['trace_id'])].count('refund_submitted') == 1

    # A duplicate resume reuses the same refund and must not add a second audit row.
    service_graph.GRAPH.invoke(
        Command(resume={'approved': True}),
        {'configurable': {'thread_id': body['task_id']}},
    )
    assert [a.action for a in _audits(task['trace_id'])].count('refund_submitted') == 1
    assert len(
        [r for r in client.get('/api/v1/refunds').json() if r['order_id'] == 'O10003']
    ) == 1


def test_sse_replays_completed_task_events_then_ends(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'low_refund')
    assert run_once() is True
    task = _task(client, body['task_id'])
    assert task['status'] == 'completed'

    response = client.get(f"/api/v1/tasks/{body['task_id']}/events")
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')

    frames = _sse_frames(response.text)
    event_types = [frame['event_type'] for frame in frames]
    assert event_types[0] == 'task_created'
    assert event_types[-1] == 'task_completed'
    assert 'workflow_start' in event_types
    assert 'node_start' in event_types
    assert 'node_completed' in event_types

    ids = [frame['id'] for frame in frames]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_sse_reconnect_resumes_after_last_event_id(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'low_refund')
    assert run_once() is True

    full = _sse_frames(
        client.get(f"/api/v1/tasks/{body['task_id']}/events").text
    )
    cursor = full[3]['id']

    by_header = client.get(
        f"/api/v1/tasks/{body['task_id']}/events",
        headers={'Last-Event-ID': str(cursor)},
    )
    resumed = _sse_frames(by_header.text)
    assert resumed
    assert all(frame['id'] > cursor for frame in resumed)
    assert [frame['id'] for frame in resumed] == [
        frame['id'] for frame in full if frame['id'] > cursor
    ]

    by_query = client.get(
        f"/api/v1/tasks/{body['task_id']}/events",
        params={'after': cursor},
    )
    assert [frame['id'] for frame in _sse_frames(by_query.text)] == [
        frame['id'] for frame in resumed
    ]


def test_sse_missing_task_returns_404(seeded_db):
    client = TestClient(app)
    response = client.get('/api/v1/tasks/task_missing/events')
    assert response.status_code == 404


def test_sse_sends_heartbeat_and_ends_when_task_finishes(seeded_db, monkeypatch):
    monkeypatch.setattr(settings, 'sse_ping_interval', 0.05)
    monkeypatch.setattr(settings, 'sse_poll_interval', 0.01)
    client = TestClient(app)

    body = _run_case(client, 'low_refund')
    # No worker runs here: the stream waits on the pending task, emits a heartbeat,
    # then closes normally once another process terminalizes the task.
    timer = threading.Timer(
        0.2,
        lambda: update_task(
            body['task_id'], status='failed', current_step='failed', error='stop'
        ),
    )
    timer.start()
    try:
        with client.stream(
            'GET', f"/api/v1/tasks/{body['task_id']}/events"
        ) as response:
            text = ''.join(response.iter_text())
    finally:
        timer.cancel()

    assert ': ping' in text
    frames = _sse_frames(text)
    assert [frame['event_type'] for frame in frames] == ['task_created']


def _disconnect_after_first_sse_chunk(task_id):
    """Drive the ASGI app directly so the client can disconnect mid-stream."""

    async def scenario():
        sent = []
        disconnected = asyncio.Event()
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            sent.append(message)
            if message['type'] == 'http.response.body' and message.get('body'):
                disconnected.set()

        path = f'/api/v1/tasks/{task_id}/events'
        scope = {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'GET',
            'scheme': 'http',
            'path': path,
            'raw_path': path.encode(),
            'query_string': b'',
            'headers': [],
            'server': ('testserver', 80),
            'client': ('testclient', 123),
            'root_path': '',
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        return b''.join(
            m.get('body', b'')
            for m in sent
            if m['type'] == 'http.response.body'
        )

    return asyncio.run(scenario())


def test_closing_sse_does_not_cancel_queued_task(seeded_db):
    client = TestClient(app)

    body = _run_case(client, 'low_refund')
    streamed = _disconnect_after_first_sse_chunk(body['task_id'])
    assert b'task_created' in streamed

    # The browser is gone, but the Task is still queued for the independent worker.
    assert _task(client, body['task_id'])['status'] == 'pending'
    assert run_once() is True
    assert _task(client, body['task_id'])['status'] == 'completed'
