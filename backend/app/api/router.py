import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sse_starlette.sse import EventSourceResponse

from app.api.schemas import ApprovalDecision, BadCaseCreate, ChatRequest, MockCaseRequest
from app.core.config import settings
from app.core.security import SecurityContext, get_security_context
from app.db.database import dumps, engine, loads, session_scope, utcnow
from app.db.models import ApprovalRecord, BadCase, Knowledge, RefundRequest, Task, Ticket, TraceStep
from app.fixtures.mock_data import MOCK_CASES
from app.llm.provider import ProviderError, describe_provider
from app.rag.hybrid import retrieve
from app.services.agent_loop import (
    READ_ONLY_TOOL_NAMES,
    run_agent_loop,
    should_use_agent_loop,
)
from app.services.router import route_intent
from app.services.tasking import create_task, get_task, list_events, record_audit
from app.services.tools import create_ticket, get_customer, get_logistics, get_order

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
    r = route_intent(body.message)
    if should_use_agent_loop(r, body.message):
        try:
            return run_agent_loop(body.message, ctx, intent=r['intent'])
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(404, str(e))
    intent = r['intent']; oid = r.get('order_id')
    if intent == 'faq':
        try:
            docs = retrieve(body.message, top_n=3)
        except ProviderError as exc:
            raise HTTPException(503, str(exc)) from exc
        answer = docs[0]['content'] if docs else '当前知识库没有足够依据。'
        return {'mode': 'direct_rag', 'intent': intent, 'answer': answer, 'citations': [{'title': x['title'], 'version': x['version']} for x in docs], 'evidence': docs}
    if not oid: raise HTTPException(400, '请提供订单号，例如 O10001')
    try:
        if intent == 'order_query': return {'mode': 'direct_tool', 'intent': intent, 'order': get_order(oid, ctx)}
        if intent == 'logistics_query':
            log = get_logistics(oid, ctx)
            if not log: return {'mode': 'direct_tool', 'intent': intent, 'answer': '该订单暂无物流信息。'}
            ticket = None
            if int(log['last_update_hours']) >= 24:
                ticket = create_ticket(ctx, oid, 'logistics_abnormal', body.message, stale_hours=int(log['last_update_hours']))
                record_audit(
                    None,
                    ctx.user_id,
                    'ticket_created',
                    {
                        'ticket_id': ticket['id'],
                        'order_id': ticket.get('order_id'),
                        'category': ticket['category'],
                        'priority': ticket['priority'],
                        'assigned_group': ticket['assigned_group'],
                        'source': 'direct_tool',
                    },
                )
            return {'mode': 'direct_tool', 'intent': intent, 'logistics': log, 'ticket': ticket, 'answer': f"当前物流状态 {log['status']}，最后节点：{log['last_event']}。" + (f" 已创建工单 {ticket['id']}。" if ticket else '')}
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {'mode': 'workflow', 'intent': intent, **create_task(ctx, intent, {'message': body.message, 'order_id': oid, 'role': ctx.role})}


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
    if body.status not in {'approved', 'rejected'}: raise HTTPException(400, 'status must approved/rejected')
    with session_scope() as s:
        a = s.get(ApprovalRecord, approval_id)
        if not a: raise HTTPException(404, 'approval not found')
        prior = s.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.task_id == a.task_id,
                ApprovalRecord.sequence_no < a.sequence_no,
                ApprovalRecord.status == 'pending',
            )
            .order_by(ApprovalRecord.sequence_no)
            .limit(1)
        ).first()
        if prior: raise HTTPException(409, f"请先完成前置审批：{prior.approver_role}")

        task_row = s.get(Task, a.task_id)
        a.status = body.status
        a.approver_user_id = body.approver_user_id
        a.comment = body.comment
        a.updated_at = utcnow()
        cancelled = 0
        if body.status == 'rejected':
            result = s.execute(
                update(ApprovalRecord)
                .where(
                    ApprovalRecord.task_id == a.task_id,
                    ApprovalRecord.status == 'pending',
                    ApprovalRecord.id != approval_id,
                )
                .values(status='cancelled', updated_at=func.current_timestamp())
            )
            cancelled = result.rowcount or 0
        s.flush()
        allrows = [
            row.to_dict()
            for row in s.scalars(
                select(ApprovalRecord).where(ApprovalRecord.task_id == a.task_id)
            )
        ]
        record_audit(
            task_row.trace_id if task_row else None,
            body.approver_user_id,
            f'approval_{body.status}',
            {
                'approval_id': a.id,
                'task_id': a.task_id,
                'order_id': a.order_id,
                'approver_role': a.approver_role,
                'sequence_no': a.sequence_no,
                'comment': body.comment,
                'cancelled_approvals': cancelled,
            },
            session=s,
        )
        if any(r['status'] == 'rejected' for r in allrows) or not any(r['status'] == 'pending' for r in allrows):
            if task_row:
                task_row.status = 'resume_pending'
                task_row.current_step = 'approval_resume'
                task_row.updated_at = utcnow()
                s.flush()
        return {'approval': a.to_dict(), 'all': allrows}


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
        open_tickets = s.scalar(select(func.count(Ticket.id)).where(Ticket.status == 'open')) or 0
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
    return {
        'tickets_total': tickets_total,
        'open_tickets': open_tickets,
        'refund_total': refund_total,
        'waiting_approval': waiting_approval,
        'ticket_by_priority': ticket_by_priority,
        'ticket_by_category': ticket_by_category,
    }


@router.get('/evaluation')
def evaluation():
    from app.eval.run_eval import evaluate
    return {'cases': evaluate()}


@router.get('/bad-cases')
def bad_cases():
    with session_scope() as s:
        rows = [row.to_dict() for row in s.scalars(select(BadCase).order_by(BadCase.id.desc()))]
    for r in rows:
        r['expected_result'] = loads(r.pop('expected_result_json', '{}'), {})
        r['fixed'] = bool(r['fixed'])
    return rows


@router.post('/bad-cases')
def bad(body: BadCaseCreate):
    with session_scope() as s:
        s.add(
            BadCase(
                trace_id=body.trace_id,
                query=body.query,
                error_type=body.error_type,
                root_cause=body.root_cause,
                expected_result_json=dumps(body.expected_result),
            )
        )
    return {'status': 'created'}
