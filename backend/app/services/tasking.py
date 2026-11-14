import time
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.database import dumps, loads, session_scope, use_session, utcnow
from app.db.models import AuditLog, Task, TaskEvent, TraceStep

# A losing claimer may have raced on the same candidate; retry a few times before reporting idle.
CLAIM_ATTEMPTS = 10


def new_id(prefix):
    return f'{prefix}_{uuid4().hex[:16]}'


def create_task(ctx, intent, payload):
    tid = new_id('task')
    trace = new_id('trace')
    with session_scope() as s:
        s.add(
            Task(
                id=tid,
                trace_id=trace,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                intent=intent,
                status='pending',
                current_step='queued',
                payload_json=dumps(payload),
                result_json='{}',
            )
        )
        s.add(
            TaskEvent(
                task_id=tid,
                event_type='task_created',
                payload_json=dumps({'trace_id': trace, 'intent': intent}),
            )
        )
    return {'task_id': tid, 'trace_id': trace, 'status': 'pending'}


def get_task(tid, session: Session | None = None):
    with use_session(session) as s:
        row = s.get(Task, tid)
        if not row:
            return None
        data = row.to_dict()
        data['payload'] = loads(data.pop('payload_json'), {})
        data['result'] = loads(data.pop('result_json'), {})
        return data


def update_task(
    tid, status=None, current_step=None, result=None, error=None, session: Session | None = None
):
    with use_session(session) as s:
        row = s.get(Task, tid)
        if not row:
            return
        if status:
            row.status = status
        if current_step:
            row.current_step = current_step
        if result is not None:
            row.result_json = dumps(result)
        row.error = error
        row.updated_at = utcnow()
        s.flush()


def _claim_once(session: Session):
    waiting = ('pending', 'resume_pending')
    candidate = (
        select(Task.id)
        .where(Task.status.in_(waiting))
        .order_by(Task.created_at)
        .limit(1)
        .scalar_subquery()
    )
    tid = session.scalar(
        update(Task)
        .where(Task.id == candidate, Task.status.in_(waiting))
        .values(status='running', updated_at=func.current_timestamp())
        .returning(Task.id)
    )
    if not tid:
        return None
    session.flush()
    return get_task(tid, session=session)


def claim_next_task(session: Session | None = None):
    waiting = ('pending', 'resume_pending')
    for _ in range(CLAIM_ATTEMPTS):
        with use_session(session) as s:
            task = _claim_once(s)
            if task is not None:
                return task
            pending = s.scalar(
                select(func.count()).select_from(Task).where(Task.status.in_(waiting))
            )
            if not pending:
                return None
        if session is not None:
            break
    return None


def emit_event(tid, event_type, payload, session: Session | None = None):
    with use_session(session) as s:
        s.add(TaskEvent(task_id=tid, event_type=event_type, payload_json=dumps(payload)))
        s.flush()


def list_events(tid, after=0, session: Session | None = None):
    with use_session(session) as s:
        rows = s.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == tid, TaskEvent.id > after)
            .order_by(TaskEvent.id)
        ).all()
        result = []
        for row in rows:
            event = row.to_dict()
            event['payload'] = loads(event.pop('payload_json'), {})
            result.append(event)
        return result


def record_trace(
    trace,
    node,
    *,
    status,
    input_data=None,
    output=None,
    error=None,
    latency_ms=0,
    session: Session | None = None,
):
    """Persist one trace step. Callers may join an existing transaction."""
    with use_session(session) as s:
        s.add(
            TraceStep(
                trace_id=trace,
                node=node,
                status=status,
                latency_ms=latency_ms,
                input_json=dumps(input_data or {}),
                output_json=dumps(output or {}),
                error=error,
            )
        )
        s.flush()


def trace_step(trace, node, input_data, fn, skip_exceptions=(), output_map=None):
    """Run one node, persist its Trace row, and return the untransformed output.

    ``output_map`` lets a caller persist a customer-safe projection of the
    output without changing what the workflow itself consumes.
    """
    st = time.perf_counter()
    try:
        out = fn()
    except skip_exceptions:
        # Control-flow exceptions (e.g. LangGraph interrupt) are not node failures.
        raise
    except Exception as e:
        record_trace(
            trace,
            node,
            status='failed',
            input_data=input_data,
            error=str(e),
            latency_ms=int((time.perf_counter() - st) * 1000),
        )
        raise
    record_trace(
        trace,
        node,
        status='success',
        input_data=input_data,
        output=output_map(out) if output_map else out,
        latency_ms=int((time.perf_counter() - st) * 1000),
    )
    return out


def record_audit(trace_id, actor_id, action, detail, session: Session | None = None):
    """Persist an immutable audit record for a side effect or approval decision."""
    with use_session(session) as s:
        s.add(
            AuditLog(
                trace_id=trace_id,
                actor_id=actor_id,
                action=action,
                detail_json=dumps(detail),
            )
        )
        s.flush()
