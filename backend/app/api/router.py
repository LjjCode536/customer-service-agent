import asyncio
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
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
    RefundRequest,
    Task,
    Ticket,
    TraceStep,
)
from app.documents import lifecycle as document_lifecycle
from app.documents import uploads as document_uploads
from app.documents.catalog import (
    DocumentNotFound,
    get_document,
    get_version,
    knowledge_rows,
    list_documents,
    read_version_source,
)
from app.documents.errors import InvalidState as DocumentInvalidState
from app.documents.parsing import (
    MAX_DOCUMENT_BYTES,
    SUPPORTED_MIME_TYPES,
    DocumentParseError,
    DocumentTooLarge,
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
    except Exception:  # noqa: BLE001  # optional-dependency probe: any import failure means unavailable
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


SecurityContextDep = Annotated[SecurityContext, Depends(get_security_context)]

@router.post('/chat')
def chat(body: ChatRequest, ctx: SecurityContextDep): return _chat(body, ctx)


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


@router.get('/documents')
def documents(
    ctx: SecurityContextDep,
    status: str | None = None,
    category: str | None = None,
    product_type: str | None = None,
    visibility: str | None = None,
    keyword: str | None = None,
):
    return list_documents(
        ctx,
        status=status,
        category=category,
        product_type=product_type,
        visibility=visibility,
        keyword=keyword,
    )


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(
            f'Document is larger than the {MAX_DOCUMENT_BYTES}-byte limit.'
        )
    return data


def _duplicate_response(exc: document_uploads.DuplicateDocument) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            'detail': str(exc),
            'existing_document': {
                'document_id': exc.existing_document_id,
                'version_id': exc.existing_version_id,
                'title': exc.title,
                'checksum': exc.checksum,
            },
        },
    )


@router.post('/documents', status_code=202)
def create_document(
    ctx: SecurityContextDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    category: Annotated[str | None, Form()] = None,
    product_type: Annotated[str | None, Form()] = None,
    visibility: Annotated[str, Form()] = 'tenant',
    allowed_roles: Annotated[list[str] | None, Form()] = None,
    effective_from: Annotated[str | None, Form()] = None,
    label: Annotated[str | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
):
    try:
        return document_uploads.create_document(
            ctx,
            filename=file.filename,
            data=_read_upload(file),
            title=title,
            category=category,
            product_type=product_type,
            visibility=visibility,
            allowed_roles=allowed_roles,
            effective_from=effective_from,
            label=label,
            force=force,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except DocumentParseError as exc:
        raise HTTPException(415, str(exc)) from exc
    except document_uploads.DuplicateDocument as exc:
        return _duplicate_response(exc)
    except document_uploads.InvalidMetadata as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/documents/{document_id}/versions', status_code=202)
def create_document_version(
    document_id: str,
    ctx: SecurityContextDep,
    file: Annotated[UploadFile, File()],
    effective_from: Annotated[str | None, Form()] = None,
    label: Annotated[str | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
):
    try:
        return document_uploads.create_version(
            ctx,
            document_id,
            filename=file.filename,
            data=_read_upload(file),
            effective_from=effective_from,
            label=label,
            force=force,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except DocumentParseError as exc:
        raise HTTPException(415, str(exc)) from exc
    except document_uploads.DuplicateDocument as exc:
        return _duplicate_response(exc)
    except document_uploads.InvalidMetadata as exc:
        raise HTTPException(422, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/documents/{document_id}/versions/{version_id}/retry', status_code=202)
def retry_document_version(
    document_id: str, version_id: str, ctx: SecurityContextDep
):
    try:
        return document_uploads.retry_failed_version(ctx, document_id, version_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/documents/{document_id}/versions/{version_id}/publish')
def publish_document_version(
    document_id: str, version_id: str, ctx: SecurityContextDep
):
    try:
        return document_lifecycle.publish_version(ctx, document_id, version_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc
    except document_lifecycle.PublishConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post('/documents/{document_id}/versions/{version_id}/archive')
def archive_document_version(
    document_id: str, version_id: str, ctx: SecurityContextDep
):
    try:
        return document_lifecycle.archive_version(ctx, document_id, version_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/documents/{document_id}/versions/{version_id}/restore')
def restore_document_version(
    document_id: str, version_id: str, ctx: SecurityContextDep
):
    try:
        return document_lifecycle.restore_version(ctx, document_id, version_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/documents/{document_id}/archive')
def archive_document(document_id: str, ctx: SecurityContextDep):
    try:
        return document_lifecycle.archive_document(ctx, document_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/documents/{document_id}/restore')
def restore_document(document_id: str, ctx: SecurityContextDep):
    try:
        return document_lifecycle.restore_document(ctx, document_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentInvalidState as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/documents/{document_id}')
def document_detail(document_id: str, ctx: SecurityContextDep):
    try:
        return get_document(ctx, document_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/documents/{document_id}/versions/{version_id}')
def document_version(document_id: str, version_id: str, ctx: SecurityContextDep):
    try:
        return get_version(ctx, document_id, version_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/documents/{document_id}/versions/{version_id}/download')
def download_document_version(
    document_id: str, version_id: str, ctx: SecurityContextDep
):
    try:
        version, data = read_version_source(ctx, document_id, version_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    filename = (version.source_filename or f'{version.id}.txt').replace('"', '')
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_MIME_TYPES:
        extension = '.bin'
    ascii_name = f'{version.id}{extension}'
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=data,
        media_type=version.mime_type or 'application/octet-stream',
        headers={'Content-Disposition': disposition},
    )


@router.get('/knowledge')
def knowledge(ctx: SecurityContextDep):
    """Legacy read model: Published Versions in the old flat knowledge shape."""
    return knowledge_rows(ctx)


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
