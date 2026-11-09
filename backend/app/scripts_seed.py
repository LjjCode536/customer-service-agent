from datetime import datetime, timedelta

from sqlalchemy import delete

from app.db.database import session_scope
from app.db.models import (
    ApprovalRecord,
    AuditLog,
    BadCase,
    Customer,
    Document,
    DocumentVersion,
    Logistics,
    Order,
    RefundRequest,
    Task,
    TaskEvent,
    Ticket,
    TraceStep,
)
from app.db.schema import init_schema
from app.documents.storage import store_text
from app.fixtures.mock_data import CUSTOMERS, KNOWLEDGE, LOGISTICS, ORDERS
from app.rag.hybrid import cache_document_embeddings

SEED_DOCUMENTS = [
    {
        'id': 'DOC-REFUND-ELECTRONICS',
        'title': '电子商品售后退款政策',
        'category': 'refund',
        'product_type': 'electronics',
        'visibility': 'tenant',
    },
    {
        'id': 'DOC-LOGISTICS-SOP',
        'title': '物流异常处理SOP',
        'category': 'logistics',
        'product_type': 'all',
        'visibility': 'staff_only',
    },
    {
        'id': 'DOC-INVOICE',
        'title': '电子发票开具说明',
        'category': 'invoice',
        'product_type': 'all',
        'visibility': 'tenant',
    },
]

# Explicit mapping from the legacy Knowledge rows to documents and versions.
SEED_VERSIONS = [
    {
        'id': 'KB-REFUND-V2',
        'document_id': 'DOC-REFUND-ELECTRONICS',
        'version_number': 1,
        'label': '2025-v2',
        'status': 'superseded',
    },
    {
        'id': 'KB-REFUND-V3',
        'document_id': 'DOC-REFUND-ELECTRONICS',
        'version_number': 2,
        'label': '2026-v3',
        'status': 'published',
    },
    {
        'id': 'KB-LOGISTICS-V1',
        'document_id': 'DOC-LOGISTICS-SOP',
        'version_number': 1,
        'label': '2026-v1',
        'status': 'published',
    },
    {
        'id': 'KB-INVOICE-V1',
        'document_id': 'DOC-INVOICE',
        'version_number': 1,
        'label': '2026-v1',
        'status': 'published',
    },
]


def seed(verbose: bool = True):
    init_schema()
    with session_scope() as session:
        # Child tables first so the logistics -> orders foreign key is never violated.
        for model in (
            ApprovalRecord,
            DocumentVersion,
            Document,
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
        knowledge_by_id = {x['id']: x for x in KNOWLEDGE}
        session.add_all(
            [
                Document(
                    id=x['id'],
                    tenant_id='TENANT-DEMO',
                    title=x['title'],
                    category=x['category'],
                    product_type=x['product_type'],
                    visibility=x['visibility'],
                    allowed_roles=None,
                    status='active',
                    owner_id='seed',
                )
                for x in SEED_DOCUMENTS
            ]
        )
        session.flush()  # documents must exist before versions are inserted

        versions = []
        for spec in SEED_VERSIONS:
            knowledge = knowledge_by_id[spec['id']]
            stored = store_text(knowledge['content'])
            published_at = (
                datetime.fromisoformat(knowledge['effective_from'])
                if spec['status'] in {'published', 'superseded'}
                else None
            )
            versions.append(
                DocumentVersion(
                    id=spec['id'],
                    document_id=spec['document_id'],
                    version_number=spec['version_number'],
                    label=spec['label'],
                    status=spec['status'],
                    effective_from=knowledge['effective_from'],
                    content_text=knowledge['content'],
                    source_filename=f"{spec['id']}.txt",
                    storage_key=stored.storage_key,
                    checksum=stored.checksum,
                    size_bytes=stored.size_bytes,
                    mime_type=stored.mime_type,
                    embedding=None,
                    embedding_model=None,
                    embedding_dimensions=None,
                    embedding_generated_at=None,
                    error_code=None,
                    error_message=None,
                    published_at=published_at,
                )
            )
        session.add_all(versions)
        session.flush()
        cache_document_embeddings(session)

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
                    id='T-DEMO-SLA-BREACHED',
                    tenant_id='TENANT-DEMO',
                    user_id='U1001',
                    order_id=None,
                    category='after_sales',
                    priority='P1',
                    status='open',
                    description='演示数据：已超过 SLA 的待处理售后工单',
                    assigned_group='after_sales',
                    sla_due_at=(datetime.now() - timedelta(minutes=15)).isoformat(
                        timespec='seconds'
                    ),
                    source='human',
                ),
                Ticket(
                    id='T-DEMO-SLA-RISK',
                    tenant_id='TENANT-DEMO',
                    user_id='U1002',
                    order_id=None,
                    category='payment_issue',
                    priority='P2',
                    status='open',
                    description='演示数据：临近 SLA 的待处理支付工单',
                    assigned_group='after_sales',
                    sla_due_at=(datetime.now() + timedelta(minutes=20)).isoformat(
                        timespec='seconds'
                    ),
                    source='human',
                ),
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
    if verbose:
        print('seed complete')


if __name__ == '__main__':
    seed()
