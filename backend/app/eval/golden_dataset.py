"""Structured Golden Dataset.

Every case asserts the outcome of the complete service chain for the five core
scenarios plus FAQ. Expected values are independent business facts, not a
re-computation of the implementation.
"""

TENANT = 'TENANT-DEMO'


def _expect(
    *,
    mode: str,
    intent: str,
    task_status: str | None = None,
    ticket: dict | None = None,
    refund: dict | None = None,
    refund_count: int = 0,
    approval_chain: list[str] | None = None,
    approval_status: str = 'not_required',
    idempotency_key: str | None = None,
    idempotency_reused: bool = False,
    duplicate_blocked: bool = False,
    **extra,
) -> dict:
    return {
        'mode': mode,
        'intent': intent,
        'task_status': task_status,
        'ticket': ticket,
        'refund': refund,
        'refund_count': refund_count,
        'approval_chain': approval_chain or [],
        'approval_status': approval_status,
        'idempotency_key': idempotency_key,
        'idempotency_reused': idempotency_reused,
        'duplicate_blocked': duplicate_blocked,
        **extra,
    }


GOLDEN_CASES = [
    {
        'id': 'logistics_stale',
        'category': 'logistics_abnormal',
        'title': '物流38小时未更新',
        'user_id': 'U1001',
        'tenant_id': TENANT,
        'order_id': 'O10001',
        'message': '订单 O10001 物流两天没更新，到底怎么回事？',
        'expected': _expect(
            mode='direct_tool',
            intent='logistics_query',
            ticket={
                'category': 'logistics_abnormal',
                'priority': 'P2',
                'assigned_group': 'logistics_support',
            },
        ),
    },
    {
        'id': 'low_risk_refund',
        'category': 'low_risk_refund',
        'title': '399元未发货低风险退款',
        'user_id': 'U1002',
        'tenant_id': TENANT,
        'order_id': 'O10002',
        'message': '订单 O10002 还没发货，我不想要了，帮我退款。',
        'expected': _expect(
            mode='workflow',
            intent='refund',
            task_status='completed',
            refund={'order_id': 'O10002', 'amount': 399.0, 'status': 'accepted'},
            refund_count=1,
            idempotency_key='refund:TENANT-DEMO:O10002',
            idempotency_reused=True,
        ),
    },
    {
        'id': 'high_risk_refund',
        'category': 'high_risk_refund',
        'title': '12999元高额退款人工审核',
        'user_id': 'U1003',
        'tenant_id': TENANT,
        'order_id': 'O10003',
        'message': '订单 O10003 还没发货，12999元帮我直接退掉。',
        'expected': _expect(
            mode='workflow',
            intent='refund',
            task_status='completed',
            refund={'order_id': 'O10003', 'amount': 12999.0, 'status': 'accepted'},
            refund_count=1,
            approval_chain=['refund_specialist', 'service_manager'],
            approval_status='approved',
            idempotency_key='refund:TENANT-DEMO:O10003',
            idempotency_reused=True,
        ),
    },
    {
        'id': 'duplicate_refund',
        'category': 'duplicate_refund',
        'title': '重复退款申请拦截',
        'user_id': 'U1004',
        'tenant_id': TENANT,
        'order_id': 'O10004',
        'message': '订单 O10004 再帮我退一次款。',
        'expected': _expect(
            mode='workflow',
            intent='refund',
            task_status='completed',
            ticket={
                'category': 'refund_duplicate',
                'priority': 'P3',
                'assigned_group': 'after_sales',
            },
            refund={'order_id': 'O10004', 'amount': 899.0, 'status': 'accepted'},
            refund_count=1,
            idempotency_key='refund:TENANT-DEMO:O10004',
            idempotency_reused=True,
            duplicate_blocked=True,
        ),
    },
    {
        'id': 'vip_complaint',
        'category': 'vip_complaint',
        'title': 'VIP重复售后强投诉',
        'user_id': 'U1005',
        'tenant_id': TENANT,
        'order_id': 'O10005',
        'message': '订单 O10005 的东西坏第三次了，再不给我解决我要投诉！',
        'expected': _expect(
            mode='workflow',
            intent='complaint',
            task_status='completed',
            ticket={
                'category': 'complaint',
                'priority': 'P1',
                'assigned_group': 'complaint_specialist',
            },
        ),
    },
    {
        'id': 'faq_invoice',
        'category': 'faq',
        'title': '普通FAQ：电子发票',
        'user_id': 'U1001',
        'tenant_id': TENANT,
        'order_id': None,
        'message': '电子发票怎么开？',
        'expected': _expect(
            mode='direct_rag',
            intent='faq',
            citations={'contains': ['电子发票开具说明']},
        ),
    },
]
