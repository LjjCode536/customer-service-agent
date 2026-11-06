from sqlalchemy import delete

from app.db.database import dumps, session_scope
from app.db.models import (
    ApprovalRecord,
    AuditLog,
    BadCase,
    Customer,
    Knowledge,
    Logistics,
    Order,
    RefundRequest,
    Task,
    TaskEvent,
    Ticket,
    TraceStep,
)
from app.db.schema import init_schema
from app.fixtures.mock_data import CUSTOMERS, KNOWLEDGE, LOGISTICS, ORDERS


def seed():
    init_schema()
    with session_scope() as session:
        # Child tables first so the logistics -> orders foreign key is never violated.
        for model in (
            ApprovalRecord,
            TaskEvent,
            Task,
            Ticket,
            TraceStep,
            AuditLog,
            BadCase,
            RefundRequest,
            Logistics,
            Order,
            Customer,
            Knowledge,
        ):
            session.execute(delete(model))

        session.add_all(
            [
                Customer(
                    id=x['id'],
                    tenant_id='TENANT-DEMO',
                    user_name=x['user_name'],
                    vip_level=x['vip_level'],
                    account_age_days=x['account_age_days'],
                    recent_ticket_count=x['recent_ticket_count'],
                    recent_refund_count=x['recent_refund_count'],
                    complaint_count_30d=x['complaint_count_30d'],
                )
                for x in CUSTOMERS
            ]
        )
        session.add_all(
            [
                Order(
                    id=x['id'],
                    tenant_id='TENANT-DEMO',
                    user_id=x['user_id'],
                    amount=x['amount'],
                    status=x['status'],
                    payment_status=x['payment_status'],
                    shipment_status=x['shipment_status'],
                    tracking_no=x['tracking_no'],
                    product_type=x['product_type'],
                    created_at=x['created_at'],
                    delivered_at=x['delivered_at'],
                )
                for x in ORDERS
            ]
        )
        session.flush()  # orders must exist before logistics is inserted
        session.add_all(
            [
                Logistics(
                    order_id=x['order_id'],
                    status=x['status'],
                    last_event=x['last_event'],
                    last_update_hours=x['last_update_hours'],
                    updated_at=x['updated_at'],
                )
                for x in LOGISTICS
            ]
        )
        session.add_all(
            [
                Knowledge(
                    id=x['id'],
                    category=x['category'],
                    product_type=x['product_type'],
                    title=x['title'],
                    version=x['version'],
                    status=x['status'],
                    effective_from=x['effective_from'],
                    content=x['content'],
                    keywords_json=dumps(x['keywords']),
                    embedding=None,
                    embedding_model=None,
                    embedding_generated_at=None,
                )
                for x in KNOWLEDGE
            ]
        )
        session.add(
            RefundRequest(
                id='RR-HISTORY-10004',
                tenant_id='TENANT-DEMO',
                user_id='U1004',
                order_id='O10004',
                idempotency_key='refund:TENANT-DEMO:O10004',
                amount=899,
                status='accepted',
                external_refund_id='RF-HISTORY-10004',
                reason='historical_refund',
            )
        )
        session.add_all(
            [
                Ticket(
                    id='T-HISTORY-U1005-1',
                    tenant_id='TENANT-DEMO',
                    user_id='U1005',
                    order_id='O10005',
                    category='after_sales',
                    priority='P2',
                    status='resolved',
                    description='商品故障第一次售后',
                    assigned_group='after_sales',
                    sla_due_at='2026-09-06T10:00:00',
                    source='human',
                ),
                Ticket(
                    id='T-HISTORY-U1005-2',
                    tenant_id='TENANT-DEMO',
                    user_id='U1005',
                    order_id='O10005',
                    category='after_sales',
                    priority='P2',
                    status='resolved',
                    description='商品故障第二次售后',
                    assigned_group='after_sales',
                    sla_due_at='2026-09-10T10:00:00',
                    source='human',
                ),
            ]
        )
    print('seed complete')


if __name__ == '__main__':
    seed()
