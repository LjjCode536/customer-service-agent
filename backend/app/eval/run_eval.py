from app.services.router import route_intent
from app.services.rules import refund_eligibility,refund_risk

def evaluate():
    cases=[]
    def add(name,ok,actual,expected):cases.append({'name':name,'passed':bool(ok),'actual':actual,'expected':expected})
    add('物流异常意图',route_intent('订单 O10001 物流两天没更新')['intent']=='logistics_query',route_intent('订单 O10001 物流两天没更新'),'logistics_query')
    add('低风险退款',refund_risk({'amount':399},{'vip_level':'normal'},{'eligible':True})['risk_level']=='low',refund_risk({'amount':399},{'vip_level':'normal'},{'eligible':True}),'low')
    add('高额退款',refund_risk({'amount':12999},{'vip_level':'vip'},{'eligible':True})['risk_level']=='high',refund_risk({'amount':12999},{'vip_level':'vip'},{'eligible':True}),'high')
    dup=refund_eligibility({'payment_status':'paid','shipment_status':'not_shipped'},[{'id':'old'}]); add('重复退款拦截',not dup['eligible'] and dup['reason']=='DUPLICATE_REFUND_REQUEST',dup,'duplicate blocked')
    add('VIP投诉意图',route_intent('订单 O10005 东西坏第三次了，我要投诉')['intent']=='complaint',route_intent('订单 O10005 东西坏第三次了，我要投诉'),'complaint')
    return cases
