from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import SecurityContext
from app.db.database import use_session
from app.db.models import Customer, Logistics, Order, RefundRequest, Ticket
from app.services.tasking import new_id


def get_customer(ctx: SecurityContext, session: Session | None = None):
    with use_session(session) as s:
        row = s.get(Customer, ctx.user_id)
        if not row or row.tenant_id != ctx.tenant_id:
            raise PermissionError('CUSTOMER_NOT_FOUND')
        return row.to_dict()


def get_order(order_id, ctx: SecurityContext, session: Session | None = None):
    with use_session(session) as s:
        row = s.get(Order, order_id)
        if not row or row.tenant_id != ctx.tenant_id:
            raise ValueError('ORDER_NOT_FOUND')
        if row.user_id != ctx.user_id and ctx.role not in {'agent', 'admin'}:
            raise PermissionError('ORDER_ACCESS_DENIED')
        return row.to_dict()


def get_logistics(order_id, ctx, session: Session | None = None):
    with use_session(session) as s:
        get_order(order_id, ctx, session=s)
        row = s.get(Logistics, order_id)
        return row.to_dict() if row else None


def get_refunds(order_id, ctx, session: Session | None = None):
    with use_session(session) as s:
        get_order(order_id, ctx, session=s)
        rows = s.scalars(
            select(RefundRequest)
            .where(RefundRequest.order_id == order_id)
            .order_by(RefundRequest.created_at.desc())
        ).all()
        return [row.to_dict() for row in rows]


def priority(category, vip_level, complaint=False, stale_hours=0):
    if complaint and vip_level == 'vip':
        return 'P1'
    if category in {'refund_failed', 'payment_issue'} or stale_hours >= 24:
        return 'P2'
    return 'P3'


def sla_due(p):
    mins = {'P0': 15, 'P1': 30, 'P2': 120, 'P3': 480}[p]
    return (datetime.now() + timedelta(minutes=mins)).isoformat(timespec='seconds')


def assigned_group(category):
    return {
        'logistics_abnormal': 'logistics_support',
        'refund_review': 'after_sales',
        'complaint': 'complaint_specialist',
        'refund_duplicate': 'after_sales',
    }.get(category, 'customer_service')


def create_ticket(
    ctx, order_id, category, description, complaint=False, stale_hours=0, session: Session | None = None
):
    with use_session(session) as s:
        query = select(Ticket).where(
            Ticket.tenant_id == ctx.tenant_id,
            Ticket.user_id == ctx.user_id,
            Ticket.category == category,
            Ticket.status == 'open',
        )
        query = query.where(Ticket.order_id.is_(None) if order_id is None else Ticket.order_id == order_id)
        existing = s.scalars(query.order_by(Ticket.created_at.desc()).limit(1)).first()
        if existing:
            return existing.to_dict()

        customer = get_customer(ctx, session=s)
        p = priority(category, customer['vip_level'], complaint, stale_hours)
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
            assigned_group=assigned_group(category),
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
    key = f"refund:{ctx.tenant_id}:{order['id']}"
    with use_session(session) as s:
        old = s.scalar(
            select(RefundRequest).where(RefundRequest.idempotency_key == key)
        )
        if old:
            return {**old.to_dict(), 'idempotent_reuse': True}

        rid = new_id('refund')
        values = {
            'id': rid,
            'tenant_id': ctx.tenant_id,
            'user_id': ctx.user_id,
            'order_id': order['id'],
            'idempotency_key': key,
            'amount': order['amount'],
            'status': 'accepted',
            'external_refund_id': 'RF-' + rid[-8:].upper(),
            'reason': reason,
        }
        if not _insert_refund_if_absent(s, values):
            old = s.scalar(
                select(RefundRequest).where(RefundRequest.idempotency_key == key)
            )
            if old is None:
                raise RuntimeError('REFUND_IDEMPOTENCY_CONFLICT')
            return {**old.to_dict(), 'idempotent_reuse': True}

        order_row = s.get(Order, order['id'])
        if order_row:
            order_row.status = 'refund_processing'
        s.flush()

        refund = s.scalar(
            select(RefundRequest).where(RefundRequest.idempotency_key == key)
        )
        return {**refund.to_dict(), 'idempotent_reuse': False}
