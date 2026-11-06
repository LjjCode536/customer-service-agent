import time
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.database import dumps, loads, session_scope, use_session, utcnow
from app.db.models import Task, TaskEvent, TraceStep


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


def claim_next_task(session: Session | None = None):
    with use_session(session) as s:
        waiting = ('pending', 'resume_pending')
        candidate = (
            select(Task.id)
            .where(Task.status.in_(waiting))
            .order_by(Task.created_at)
            .limit(1)
            .scalar_subquery()
        )
        tid = s.scalar(
            update(Task)
            .where(Task.id == candidate, Task.status.in_(waiting))
            .values(status='running', updated_at=func.current_timestamp())
            .returning(Task.id)
        )
        if not tid:
            return None
        s.flush()
        return get_task(tid, session=s)


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


def trace_step(trace, node, input_data, fn):
    st = time.perf_counter()
    try:
        out = fn()
        ms = int((time.perf_counter() - st) * 1000)
        with session_scope() as s:
            s.add(
                TraceStep(
                    trace_id=trace,
                    node=node,
                    status='success',
                    latency_ms=ms,
                    input_json=dumps(input_data),
                    output_json=dumps(out),
                )
            )
        return out
    except Exception as e:
        ms = int((time.perf_counter() - st) * 1000)
        with session_scope() as s:
            s.add(
                TraceStep(
                    trace_id=trace,
                    node=node,
                    status='failed',
                    latency_ms=ms,
                    input_json=dumps(input_data),
                    output_json='{}',
                    error=str(e),
                )
            )
        raise
