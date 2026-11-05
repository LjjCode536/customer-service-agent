from app.db.database import fetch_one

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
