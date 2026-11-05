import re
from app.core.config import settings
from app.llm.deepseek import client

def extract_order_id(text):
    m=re.search(r'\bO\d{5}\b', text.upper()); return m.group(0) if m else None

def route_intent(text: str) -> dict:
    t=text.lower(); order_id=extract_order_id(text)
    if any(x in t for x in ['退款','退掉','退钱']) or ('退' in t and '款' in t): intent='refund'
    elif any(x in t for x in ['投诉','坏第三次','太差','垃圾']): intent='complaint'
    elif any(x in t for x in ['物流','快递','到哪','没更新','没动']): intent='logistics_query'
    elif order_id and any(x in t for x in ['订单','状态','查一下']): intent='order_query'
    elif any(x in t for x in ['发票','保修','售后政策','怎么开']): intent='faq'
    else: intent='chat'
    if not settings.mock_mode and intent=='chat':
        try:
            ans=client.chat_text('只返回 faq/order_query/logistics_query/refund/complaint/chat 之一。','用户问题：'+text)
            for name in ['faq','order_query','logistics_query','refund','complaint','chat']:
                if name in ans: intent=name; break
        except Exception: pass
    return {'intent':intent,'order_id':order_id,'confidence':0.98 if intent!='chat' else 0.65}
