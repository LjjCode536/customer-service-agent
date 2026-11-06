from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.security import SecurityContext
from app.db.database import session_scope, use_session
from app.db.models import Customer, Logistics, Order, RefundRequest, Ticket
from app.services.rules import sla_due, ticket_assignment, ticket_priority
from app.services.tasking import new_id

# Read-only tools may retry transient database errors with bounded exponential backoff.
QUERY_RETRY_ATTEMPTS = 3
QUERY_RETRY_WAIT_MULTIPLIER = 0.05
QUERY_RETRY_WAIT_MAX = 0.5

_retry_transient = retry(
    reraise=True,
    stop=stop_after_attempt(QUERY_RETRY_ATTEMPTS),
    wait=wait_exponential(
        multiplier=QUERY_RETRY_WAIT_MULTIPLIER, max=QUERY_RETRY_WAIT_MAX
    ),
    retry=retry_if_exception_type(OperationalError),
)


@_retry_transient
def _run_query(operation):
    with session_scope() as session:
        return operation(session)


def _query(session: Session | None, operation):
    """Run a read-only operation. Caller-owned transactions are never retried."""
    if session is not None:
        return operation(session)
    return _run_query(operation)


def _get_customer_once(session: Session, ctx: SecurityContext):
    row = session.get(Customer, ctx.user_id)
    if not row or row.tenant_id != ctx.tenant_id:
        raise PermissionError('CUSTOMER_NOT_FOUND')
    return row.to_dict()


def get_customer(ctx: SecurityContext, session: Session | None = None):
    return _query(session, lambda s: _get_customer_once(s, ctx))


def _get_order_once(session: Session, order_id, ctx: SecurityContext):
    row = session.get(Order, order_id)
    # Cross-tenant lookups are indistinguishable from missing orders so tenants cannot probe data.
    if not row or row.tenant_id != ctx.tenant_id:
        raise ValueError('ORDER_NOT_FOUND')
    if row.user_id != ctx.user_id and ctx.role not in {'agent', 'admin'}:
        raise PermissionError('ORDER_ACCESS_DENIED')
    return row.to_dict()


def get_order(order_id, ctx: SecurityContext, session: Session | None = None):
    return _query(session, lambda s: _get_order_once(s, order_id, ctx))


def _get_logistics_once(session: Session, order_id, ctx: SecurityContext):
    _get_order_once(session, order_id, ctx)
    row = session.get(Logistics, order_id)
    return row.to_dict() if row else None


def get_logistics(order_id, ctx, session: Session | None = None):
    return _query(session, lambda s: _get_logistics_once(s, order_id, ctx))


def _get_refunds_once(session: Session, order_id, ctx: SecurityContext):
    _get_order_once(session, order_id, ctx)
    rows = session.scalars(
        select(RefundRequest)
        .where(RefundRequest.order_id == order_id)
        .order_by(RefundRequest.created_at.desc())
    ).all()
    return [row.to_dict() for row in rows]


def get_refunds(order_id, ctx, session: Session | None = None):
    return _query(session, lambda s: _get_refunds_once(s, order_id, ctx))


def refund_idempotency_key(tenant_id, order_id):
    return f'refund:{tenant_id}:{order_id}'


def create_ticket(
    ctx,
    order_id,
    category,
    description,
    complaint=False,
    stale_hours=0,
    session: Session | None = None,
):
    """Side-effect tool: never blindly retried; returns a visible open ticket when one exists."""
    with use_session(session) as s:
        if order_id is not None:
            # Never trust a model-supplied order id; ownership/tenant come from Security Context.
            _get_order_once(s, order_id, ctx)

        query = select(Ticket).where(
            Ticket.tenant_id == ctx.tenant_id,
            Ticket.user_id == ctx.user_id,
            Ticket.category == category,
            Ticket.status == 'open',
        )
        query = query.where(
            Ticket.order_id.is_(None) if order_id is None else Ticket.order_id == order_id
        )
        existing = s.scalars(query.order_by(Ticket.created_at.desc()).limit(1)).first()
        if existing:
            return existing.to_dict()

        customer = _get_customer_once(s, ctx)
        p = ticket_priority(category, customer['vip_level'], complaint, stale_hours)
        tid = new_id('ticket')
        row = Ticket(
            id=tid,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            order_id=order_id,
            category=category,
            priority=p,
            status='open',
            description=description,
            assigned_group=ticket_assignment(category),
            sla_due_at=sla_due(p),
            source='agent',
        )
        s.add(row)
        s.flush()
        s.refresh(row)
        return row.to_dict()


def _insert_refund_if_absent(session: Session, values: dict) -> bool:
    """Insert with ON CONFLICT DO NOTHING semantics, so concurrent retries stay idempotent."""
    dialect = session.get_bind().dialect.name
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        insert = None

    if insert is not None:
        result = session.execute(
            insert(RefundRequest)
            .values(**values)
            .on_conflict_do_nothing(index_elements=['idempotency_key'])
        )
        return result.rowcount == 1

    try:
        with session.begin_nested():
            session.add(RefundRequest(**values))
            session.flush()
        return True
    except IntegrityError:
        return False


def submit_refund(ctx, order, reason, session: Session | None = None):
    """Side-effect tool: the UNIQUE idempotency key is the only retry-safety mechanism."""
    with use_session(session) as s:
        # Re-read the order through the Tool Runtime boundary; never trust caller-supplied facts.
        current = _get_order_once(s, order['id'], ctx)
        key = refund_idempotency_key(ctx.tenant_id, current['id'])
        rid = new_id('refund')
        values = {
            'id': rid,
            'tenant_id': ctx.tenant_id,
            'user_id': ctx.user_id,
            'order_id': current['id'],
            'idempotency_key': key,
            'amount': current['amount'],
            'status': 'accepted',
            'external_refund_id': 'RF-' + rid[-8:].upper(),
            'reason': reason,
        }
        inserted = _insert_refund_if_absent(s, values)
        refund = s.scalar(
            select(RefundRequest).where(RefundRequest.idempotency_key == key)
        )
        if refund is None:
            raise RuntimeError('REFUND_IDEMPOTENCY_CONFLICT')

        if inserted:
            order_row = s.get(Order, current['id'])
            if order_row:
                order_row.status = 'refund_processing'
            s.flush()
        return {**refund.to_dict(), 'idempotent_reuse': not inserted}
