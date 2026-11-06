from datetime import datetime, timedelta

SLA_MINUTES = {'P0': 15, 'P1': 30, 'P2': 120, 'P3': 480}

ASSIGNMENT_GROUPS = {
    'logistics_abnormal': 'logistics_support',
    'refund_review': 'after_sales',
    'refund_duplicate': 'after_sales',
    'complaint': 'complaint_specialist',
}


def refund_eligibility(order, refunds):
    if refunds: return {'eligible':False,'reason':'DUPLICATE_REFUND_REQUEST'}
    if order['payment_status']!='paid': return {'eligible':False,'reason':'ORDER_NOT_PAID'}
    if order['shipment_status']=='not_shipped': return {'eligible':True,'reason':'PAID_NOT_SHIPPED_FULL_REFUND'}
    return {'eligible':False,'reason':'RETURN_FLOW_REQUIRED'}

def refund_risk(order,customer,eligibility):
    score=0; reasons=[]
    if float(order['amount'])>=5000: score+=60; reasons.append('HIGH_AMOUNT')
    if customer['vip_level']=='vip' and float(order['amount'])>=10000: score+=20; reasons.append('VIP_HIGH_VALUE')
    if not eligibility['eligible'] and eligibility['reason']=='DUPLICATE_REFUND_REQUEST': score=100; reasons.append('DUPLICATE_REFUND')
    return {'score':score,'risk_level':'high' if score>=50 else 'medium' if score>=20 else 'low','reasons':reasons}

def approval_chain(risk):
    return ['refund_specialist','service_manager'] if risk['risk_level']=='high' else []


def ticket_priority(category, vip_level, complaint=False, stale_hours=0):
    """Deterministic Priority: VIP strong complaint is P1, refund/payment/stale logistics is P2."""
    if complaint and vip_level == 'vip':
        return 'P1'
    if category in {'refund_failed', 'payment_issue'} or stale_hours >= 24:
        return 'P2'
    return 'P3'


def sla_due(priority, now=None):
    base = now or datetime.now()
    return (base + timedelta(minutes=SLA_MINUTES[priority])).isoformat(timespec='seconds')


def ticket_assignment(category):
    return ASSIGNMENT_GROUPS.get(category, 'customer_service')
