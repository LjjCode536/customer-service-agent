import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sse_starlette.sse import EventSourceResponse

from app.api.schemas import (
    ApprovalDecision,
    BadCaseCreate,
    BadCaseUpdate,
    ChatRequest,
    MockCaseRequest,
)
from app.core.config import settings
from app.core.security import SecurityContext, get_security_context
from app.db.database import dumps, engine, loads, session_scope
from app.db.models import (
    ApprovalRecord,
    Knowledge,
    RefundRequest,
    Task,
    Ticket,
    TraceStep,
)
from app.fixtures.mock_data import MOCK_CASES
from app.llm.provider import ProviderError, describe_provider
from app.services.agent_loop import READ_ONLY_TOOL_NAMES
from app.services.approvals import (
    ApprovalNotFound,
    ApprovalSequenceConflict,
    decide_approval,
)
from app.services.bad_cases import (
    create_bad_case,
    get_bad_case,
    list_bad_cases,
    update_bad_case,
)
from app.services.dispatch import OrderIdRequired, dispatch_message
from app.services.metrics import sla_risk
from app.services.tasking import get_task, list_events

router = APIRouter(prefix='/api/v1')


@router.get('/runtime')
def runtime():
    try:
        import langgraph
        import langgraph.checkpoint.sqlite  # noqa: F401
        lg = True
    except Exception:
        lg = False
    database_url = make_url(settings.sqlalchemy_url).render_as_string(hide_password=True)
    return {
        'mock_mode': settings.mock_mode,
        **describe_provider(),
        'langgraph_available': lg,
        'workflow_engine': settings.workflow_engine,
        'checkpoint_path': settings.checkpoint_file,
        'database': database_url,
        'database_type': engine.dialect.name,
        'agent_loop': {
            'max_steps': settings.agent_loop_max_steps,
            'max_tool_calls': settings.agent_loop_max_tool_calls,
            'max_tokens': settings.agent_loop_max_tokens,
            'confidence_threshold': settings.agent_loop_confidence_threshold,
            'allowed_tools': list(READ_ONLY_TOOL_NAMES),
        },
    }


@router.get('/mock-cases')
def mock_cases(): return MOCK_CASES


@router.post('/mock-cases/run')
def run_mock(body: MockCaseRequest):
    c = next((x for x in MOCK_CASES if x['case_key'] == body.case_key), None)
    if not c: raise HTTPException(404, 'mock case not found')
    ctx = SecurityContext(c['user_id'], 'TENANT-DEMO', 'customer')
    return _chat(ChatRequest(message=c['message']), ctx)


@router.post('/chat')
def chat(body: ChatRequest, ctx: SecurityContext = Depends(get_security_context)): return _chat(body, ctx)


def _chat(body, ctx):
    try:
        return dispatch_message(body.message, ctx)
    except OrderIdRequired as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get('/tasks/{task_id}')
def task(task_id):
    x = get_task(task_id)
    if not x: raise HTTPException(404, 'task not found')
    return x


TERMINAL_TASK_STATUSES = {'completed', 'failed'}


def _resume_cursor(request: Request) -> int:
    """Resume an SSE stream from ?after= or the standard Last-Event-ID header."""
    for raw in (request.query_params.get('after'), request.headers.get('last-event-id')):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return 0


def _sse_frame(event):
    return {
        'id': str(event['id']),
        'data': dumps(
            {
                'id': event['id'],
                'event_type': event['event_type'],
                'created_at': event['created_at'],
                **event['payload'],
            }
        ),
    }


@router.get('/tasks/{task_id}/events')
async def events(task_id: str, request: Request):
    if not get_task(task_id):
        raise HTTPException(404, 'task not found')

    after = _resume_cursor(request)

    async def stream():
        last = after
        while True:
            if await request.is_disconnected():
                return
            for event in list_events(task_id, last):
                last = event['id']
                yield _sse_frame(event)

            task = get_task(task_id)
            if task and task['status'] in TERMINAL_TASK_STATUSES:
                # Drain events committed together with the terminal status, then end.
                for event in list_events(task_id, last):
                    yield _sse_frame(event)
                return
            await asyncio.sleep(settings.sse_poll_interval)

    return EventSourceResponse(stream(), ping=settings.sse_ping_interval)


@router.get('/tickets')
def tickets():
    with session_scope() as s:
        return [row.to_dict() for row in s.scalars(select(Ticket).order_by(Ticket.created_at.desc()))]


@router.get('/refunds')
def refunds():
    with session_scope() as s:
        return [row.to_dict() for row in s.scalars(select(RefundRequest).order_by(RefundRequest.created_at.desc()))]


@router.get('/approvals')
def approvals():
    with session_scope() as s:
        rows = s.scalars(select(ApprovalRecord).order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.sequence_no))
        return [row.to_dict() for row in rows]


@router.post('/approvals/{approval_id}/decision')
def approve(approval_id, body: ApprovalDecision):
    try:
        return decide_approval(
            approval_id,
            body.status,
            approver_user_id=body.approver_user_id,
            comment=body.comment,
        )
    except ApprovalNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ApprovalSequenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/knowledge')
def knowledge():
    with session_scope() as s:
        rows = [row.to_dict() for row in s.scalars(select(Knowledge).order_by(Knowledge.category, Knowledge.effective_from.desc()))]
    for r in rows:
        r['keywords'] = loads(r.pop('keywords_json', '[]'), [])
        # Embedding cache is persistence internals, not part of the console payload.
        r.pop('embedding', None)
        r.pop('embedding_model', None)
        r.pop('embedding_generated_at', None)
    return rows


@router.get('/traces')
def traces(limit: int = 300, trace_id: str | None = None):
    with session_scope() as s:
        query = select(TraceStep).order_by(TraceStep.id.desc()).limit(min(limit, 1000))
        if trace_id:
            query = query.where(TraceStep.trace_id == trace_id)
        rows = [row.to_dict() for row in s.scalars(query)]
    for r in rows:
        r['input'] = loads(r.pop('input_json', '{}'), {})
        r['output'] = loads(r.pop('output_json', '{}'), {})
    return rows


@router.get('/dashboard')
def dashboard():
    with session_scope() as s:
        tickets_total = s.scalar(select(func.count(Ticket.id))) or 0
        refund_total = float(s.scalar(select(func.coalesce(func.sum(RefundRequest.amount), 0))) or 0)
        waiting_approval = s.scalar(select(func.count(Task.id)).where(Task.status == 'waiting_approval')) or 0
        ticket_by_priority = [
            {'priority': priority, 'count': count}
            for priority, count in s.execute(select(Ticket.priority, func.count()).group_by(Ticket.priority)).all()
        ]
        ticket_by_category = [
            {'category': category, 'count': count}
            for category, count in s.execute(select(Ticket.category, func.count()).group_by(Ticket.category)).all()
        ]
        open_ticket_rows = [
            row.to_dict() for row in s.scalars(select(Ticket).where(Ticket.status == 'open'))
        ]
        open_tickets = len(open_ticket_rows)
    return {
        'tickets_total': tickets_total,
        'open_tickets': open_tickets,
        'refund_total': refund_total,
        'waiting_approval': waiting_approval,
        'ticket_by_priority': ticket_by_priority,
        'ticket_by_category': ticket_by_category,
        'sla_risk': sla_risk(open_ticket_rows),
    }


@router.get('/evaluation')
def evaluation():
    from app.eval.run_eval import evaluate

    return evaluate()


@router.get('/bad-cases')
def bad_cases(trace_id: str | None = None):
    return list_bad_cases(trace_id=trace_id)


@router.get('/bad-cases/{bad_case_id}')
def bad_case(bad_case_id: int):
    row = get_bad_case(bad_case_id)
    if not row:
        raise HTTPException(404, 'bad case not found')
    return row


@router.post('/bad-cases')
def create_bad(body: BadCaseCreate):
    return create_bad_case(
        trace_id=body.trace_id,
        query=body.query,
        error_type=body.error_type,
        root_cause=body.root_cause,
        expected_result=body.expected_result,
    )


@router.patch('/bad-cases/{bad_case_id}')
def patch_bad(bad_case_id: int, body: BadCaseUpdate):
    row = update_bad_case(bad_case_id, body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(404, 'bad case not found')
    return row
