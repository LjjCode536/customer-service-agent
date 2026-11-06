import re
from app.llm.provider import get_provider

# Shared vocabulary: the Intent Router and the read-only Agent Loop must not drift apart.
LOGISTICS_KEYWORDS = ('物流', '快递', '到哪', '没更新', '没动')
KNOWLEDGE_KEYWORDS = ('发票', '保修', '售后政策', '怎么开')
ORDER_KEYWORDS = ('订单', '状态', '查一下')

def extract_order_id(text):
    m=re.search(r'\bO\d{5}\b', text.upper()); return m.group(0) if m else None

def route_intent(text: str) -> dict:
    t=text.lower(); order_id=extract_order_id(text); confidence=0.98
    if any(x in t for x in ['退款','退掉','退钱']) or ('退' in t and '款' in t): intent='refund'
    elif any(x in t for x in ['投诉','坏第三次','太差','垃圾']): intent='complaint'
    elif any(x in t for x in LOGISTICS_KEYWORDS): intent='logistics_query'
    elif order_id and any(x in t for x in ORDER_KEYWORDS): intent='order_query'
    elif any(x in t for x in KNOWLEDGE_KEYWORDS): intent='faq'
    else: intent='chat'; confidence=0.65
    if intent=='chat':
        try:
            ans=get_provider().chat('只返回 faq/order_query/logistics_query/refund/complaint/chat 之一。','用户问题：'+text)
            for name in ['faq','order_query','logistics_query','refund','complaint','chat']:
                if name in ans:
                    # A model-guessed intent is not trustworthy evidence: it routes to the
                    # read-only Agent Loop via the confidence threshold, never straight to a tool.
                    intent=name
                    confidence=0.6 if name!='chat' else 0.65
                    break
        except Exception: pass
    return {'intent':intent,'order_id':order_id,'confidence':confidence}
