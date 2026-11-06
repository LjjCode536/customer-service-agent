import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.core.security import SecurityContext
from app.db.database import session_scope
from app.db.models import RefundRequest, Task
from app.services import tools
from app.services.tasking import claim_next_task, create_task
from app.services.tools import get_order, refund_idempotency_key, submit_refund
from app.worker import run_once


def _transient_error():
    return OperationalError('SELECT 1', {}, Exception('temporary database failure'))


def test_query_tool_retries_transient_errors(seeded_db, monkeypatch):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    real_fetch = tools._get_order_once
    attempts = []

    def flaky(session, order_id, ctx):
        attempts.append(order_id)
        if len(attempts) < tools.QUERY_RETRY_ATTEMPTS:
            raise _transient_error()
        return real_fetch(session, order_id, ctx)

    monkeypatch.setattr(tools, '_get_order_once', flaky)

    assert get_order('O10002', context)['id'] == 'O10002'
    assert len(attempts) == tools.QUERY_RETRY_ATTEMPTS


def test_query_tool_gives_up_after_bounded_retries(seeded_db, monkeypatch):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    attempts = []

    def always_fails(session, order_id, ctx):
        attempts.append(order_id)
        raise _transient_error()

    monkeypatch.setattr(tools, '_get_order_once', always_fails)

    with pytest.raises(OperationalError):
        get_order('O10002', context)
    assert len(attempts) == tools.QUERY_RETRY_ATTEMPTS


def test_side_effect_tool_does_not_retry(seeded_db, monkeypatch):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    order = get_order('O10002', context)
    attempts = []

    def always_fails(*args, **kwargs):
        attempts.append(1)
        raise _transient_error()

    monkeypatch.setattr(tools, '_insert_refund_if_absent', always_fails)

    with pytest.raises(OperationalError):
        submit_refund(context, order, 'agent_refund')
    assert len(attempts) == 1


def test_refund_idempotency_key_uses_tenant_and_order():
    assert refund_idempotency_key('TENANT-DEMO', 'O10002') == 'refund:TENANT-DEMO:O10002'


def test_refund_tool_rejects_cross_tenant_order(seeded_db):
    context = SecurityContext('U1002', 'TENANT-OTHER', 'customer')

    with pytest.raises(ValueError, match='ORDER_NOT_FOUND'):
        submit_refund(context, {'id': 'O10002', 'amount': 399}, 'agent_refund')

    with session_scope() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == 'O10002')
            )
            == 0
        )


def test_refund_tool_rejects_another_users_order(seeded_db):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')

    with pytest.raises(PermissionError, match='ORDER_ACCESS_DENIED'):
        submit_refund(context, {'id': 'O10003', 'amount': 12999}, 'agent_refund')


def test_concurrent_refund_submission_keeps_one_record(seeded_db):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    order = get_order('O10002', context)
    workers = 5
    barrier = threading.Barrier(workers)

    def attempt(_):
        barrier.wait()
        return submit_refund(context, order, 'agent_refund')

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, range(workers)))

    assert sum(not result['idempotent_reuse'] for result in results) == 1
    assert len({result['id'] for result in results}) == 1
    with session_scope() as session:
        rows = session.scalars(
            select(RefundRequest).where(RefundRequest.order_id == 'O10002')
        ).all()
        assert len(rows) == 1
        assert rows[0].idempotency_key == 'refund:TENANT-DEMO:O10002'


def test_concurrent_workers_claim_each_task_once(seeded_db):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    task_ids = {
        create_task(
            context, 'refund', {'message': '帮我退款', 'order_id': 'O10002', 'role': 'customer'}
        )['task_id']
        for _ in range(6)
    }
    barrier = threading.Barrier(len(task_ids))

    def claim(_):
        barrier.wait()
        task = claim_next_task()
        return task['id'] if task else None

    with ThreadPoolExecutor(max_workers=len(task_ids)) as pool:
        claimed = list(pool.map(claim, range(len(task_ids))))

    assert None not in claimed
    assert len(set(claimed)) == len(task_ids)
    assert set(claimed) == task_ids
    with session_scope() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Task).where(Task.status == 'running')
            )
            == len(task_ids)
        )


def test_concurrent_worker_loops_consume_each_task_once(seeded_db, monkeypatch):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    task_ids = {
        create_task(
            context, 'refund', {'message': '帮我退款', 'order_id': 'O10002', 'role': 'customer'}
        )['task_id']
        for _ in range(4)
    }
    workers = 8
    consumed = []
    consumed_lock = threading.Lock()

    def fake_process(task):
        with consumed_lock:
            consumed.append(task['id'])

    monkeypatch.setattr('app.worker.process_task', fake_process)
    barrier = threading.Barrier(workers)

    def work(_):
        barrier.wait()
        return run_once()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, range(workers)))

    assert len(consumed) == len(task_ids)
    assert set(consumed) == task_ids
    assert len(set(consumed)) == len(consumed)
