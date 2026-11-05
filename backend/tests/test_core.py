from app.services.router import route_intent
from app.services.rules import refund_eligibility,refund_risk

def test_router():
    assert route_intent('订单 O10001 物流两天没更新')['intent']=='logistics_query'
    assert route_intent('订单 O10002 帮我退款')['intent']=='refund'
def test_refund_rules():
    assert refund_eligibility({'payment_status':'paid','shipment_status':'not_shipped'},[])['eligible'] is True
    assert refund_eligibility({'payment_status':'paid','shipment_status':'not_shipped'},[{'id':'x'}])['reason']=='DUPLICATE_REFUND_REQUEST'
def test_risk():
    assert refund_risk({'amount':399},{'vip_level':'normal'},{'eligible':True})['risk_level']=='low'
    assert refund_risk({'amount':12999},{'vip_level':'vip'},{'eligible':True})['risk_level']=='high'
