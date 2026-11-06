"""Golden Dataset evaluation over the real dispatch + Worker + workflow chain.

Each case gets an isolated sandbox database and an offline ModelProvider, so a
run is reproducible, has no network dependency and never mutates live demo data.
"""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import SecurityContext
from app.db.database import create_engine_for_url, session_scope, use_database_engine
from app.db.models import ApprovalRecord, RefundRequest, Ticket
from app.eval.golden_dataset import GOLDEN_CASES
from app.llm.offline import OfflineProvider
from app.llm.provider import use_provider
from app.scripts_seed import seed
from app.services.approvals import decide_approval
from app.services.dispatch import dispatch_message
from app.services.tasking import get_task
from app.services.tools import get_order, submit_refund
from app.worker import run_once
from app.workflow.checkpointer import create_checkpointer
from app.workflow.service_graph import compile_graph, use_graph


def evaluate() -> dict:
    results = []
    with _sandbox() as sandbox, use_provider(OfflineProvider()), use_database_engine(
        sandbox.engine
    ), use_graph(sandbox.graph):
        for case in GOLDEN_CASES:
            results.append(_evaluate_case(case))

    passed = sum(1 for case in results if case['passed'])
    return {
        'dataset': {'name': 'golden', 'size': len(results)},
        'workflow_engine': sandbox.workflow_engine,
        'summary': {
            'total': len(results),
            'passed': passed,
            'failed': len(results) - passed,
            'pass_rate': round(passed / len(results), 4) if results else 0.0,
        },
        'cases': results,
    }


@contextmanager
def _sandbox():
    tmpdir = Path(tempfile.mkdtemp(prefix='customer-service-eval-'))
    sandbox_engine = create_engine_for_url(
        f"sqlite:///{(tmpdir / 'evaluation.db').as_posix()}"
    )
    try:
        try:
            graph = compile_graph(create_checkpointer(str(tmpdir / 'checkpoints.db')))
            workflow_engine = 'langgraph'
        except Exception as exc:
            # Never report a green run while silently using the fallback engine.
            if settings.workflow_engine != 'fallback':
                raise RuntimeError('EVALUATION_LANGGRAPH_UNAVAILABLE') from exc
            graph = None
            workflow_engine = 'fallback'
        yield SimpleNamespace(
            engine=sandbox_engine,
            graph=graph,
            workflow_engine=workflow_engine,
        )
    finally:
        sandbox_engine.dispose()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _evaluate_case(case: dict) -> dict:
    ctx = SecurityContext(case['user_id'], case['tenant_id'], 'customer')
    response: dict = {}
    task: dict | None = None
    error: str | None = None
    actual = _empty_actual()

    try:
        seed(verbose=False)
        response = dispatch_message(case['message'], ctx)
        task = _drive_workflow(response)
        actual = _actual_outcome(case, ctx, response, task, error)
        if actual['refund_count']:
            actual['idempotency_reused'] = _verify_refund_idempotency(case, ctx)
            actual['refund_count'] = _refund_count(case['order_id'])
    except Exception as exc:  # noqa: BLE001 - a case failure must not stop the suite
        error = f'{type(exc).__name__}: {exc}'
        actual['error'] = error

    checks = _checks(case['expected'], actual)
    return {
        'id': case['id'],
        'name': case['title'],
        'category': case['category'],
        'passed': error is None and all(check['passed'] for check in checks),
        'error': error,
        'task_id': task['id'] if task else None,
        'trace_id': task['trace_id'] if task else None,
        'expected': case['expected'],
        'actual': actual,
        'checks': checks,
    }


def _drive_workflow(response: dict) -> dict | None:
    """Run the same Worker/resume path production uses; never waits on SSE."""
    task_id = response.get('task_id')
    if not task_id:
        return None

    run_once()
    task = get_task(task_id)
    if task and task['status'] == 'waiting_approval':
        with session_scope() as session:
            pending = session.scalars(
                select(ApprovalRecord)
                .where(
                    ApprovalRecord.task_id == task_id,
                    ApprovalRecord.status == 'pending',
                )
                .order_by(ApprovalRecord.sequence_no)
            )
            approval_ids = [row.id for row in pending]
        for approval_id in approval_ids:
            decide_approval(approval_id, 'approved', 'evaluation-runner')
        run_once()
        task = get_task(task_id)
    return task


def _actual_outcome(
    case: dict,
    ctx: SecurityContext,
    response: dict,
    task: dict | None,
    error: str | None,
) -> dict:
    order_id = case['order_id']
    ticket = None
    with session_scope() as session:
        if order_id:
            ticket_row = session.scalars(
                select(Ticket)
                .where(Ticket.order_id == order_id, Ticket.status == 'open')
                .order_by(Ticket.created_at.desc(), Ticket.id.desc())
                .limit(1)
            ).first()
            if ticket_row:
                ticket = {
                    'category': ticket_row.category,
                    'priority': ticket_row.priority,
                    'assigned_group': ticket_row.assigned_group,
                    'status': ticket_row.status,
                }

    refunds = _refunds(order_id)
    refund = None
    if refunds:
        row = refunds[0]
        refund = {
            'order_id': row.order_id,
            'amount': float(row.amount),
            'status': row.status,
        }

    approvals = _approvals(task['id']) if task else []
    approval_statuses = [row.status for row in approvals]
    if not approval_statuses:
        approval_status = 'not_required'
    elif any(status == 'rejected' for status in approval_statuses):
        approval_status = 'rejected'
    elif any(status == 'pending' for status in approval_statuses):
        approval_status = 'pending'
    else:
        approval_status = 'approved'

    result = (task or {}).get('result') or {}
    eligibility = result.get('eligibility') or {}

    return {
        'mode': response.get('mode'),
        'intent': response.get('intent'),
        'task_status': task['status'] if task else None,
        'ticket': ticket,
        'refund': refund,
        'refund_count': len(refunds),
        'approval_chain': [row.approver_role for row in approvals],
        'approval_status': approval_status,
        'idempotency_key': refunds[0].idempotency_key if refunds else None,
        'idempotency_reused': False,
        'duplicate_blocked': eligibility.get('reason')
        == 'DUPLICATE_REFUND_REQUEST',
        'citations': [
            citation['title'] for citation in response.get('citations', [])
        ],
        'error': error,
    }


def _refunds(order_id: str | None) -> list[RefundRequest]:
    if not order_id:
        return []
    with session_scope() as session:
        return list(
            session.scalars(
                select(RefundRequest)
                .where(RefundRequest.order_id == order_id)
                .order_by(RefundRequest.created_at.desc())
            )
        )


def _refund_count(order_id: str | None) -> int:
    if not order_id:
        return 0
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == order_id)
            )
            or 0
        )


def _approvals(task_id: str) -> list[ApprovalRecord]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(ApprovalRecord)
                .where(ApprovalRecord.task_id == task_id)
                .order_by(ApprovalRecord.sequence_no)
            )
        )


def _verify_refund_idempotency(case: dict, ctx: SecurityContext) -> bool:
    """Re-submit outside the workflow; the UNIQUE key must reuse the same refund."""
    order = get_order(case['order_id'], ctx)
    again = submit_refund(ctx, order, 'evaluation_idempotency_probe')
    return bool(again['idempotent_reuse'])


def _empty_actual() -> dict:
    return {
        'mode': None,
        'intent': None,
        'task_status': None,
        'ticket': None,
        'refund': None,
        'refund_count': 0,
        'approval_chain': [],
        'approval_status': 'not_required',
        'idempotency_key': None,
        'idempotency_reused': False,
        'duplicate_blocked': False,
        'citations': [],
        'error': None,
    }


def _checks(expected: dict, actual: dict) -> list[dict]:
    return [
        {
            'field': field,
            'expected': value,
            'actual': actual.get(field),
            'passed': _subset_match(value, actual.get(field)),
        }
        for field, value in expected.items()
    ]


def _subset_match(expected, actual) -> bool:
    """Expected dicts describe a subset. {'contains': [...]} means list contains."""
    if isinstance(expected, dict):
        if set(expected) == {'contains'} and isinstance(actual, list):
            return all(item in actual for item in expected['contains'])
        return isinstance(actual, dict) and all(
            key in actual and _subset_match(value, actual[key])
            for key, value in expected.items()
        )
    return expected == actual
