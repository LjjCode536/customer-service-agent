"""Single message entry point shared by the HTTP API and the evaluation runner.

Keeping dispatch here means Golden Dataset cases exercise the same routing,
direct-tool and workflow-creation path as a real customer message.
"""

from app.core.security import SecurityContext
from app.rag.hybrid import retrieve
from app.services.agent_loop import run_agent_loop, should_use_agent_loop
from app.services.router import route_intent
from app.services.tasking import create_task, record_audit
from app.services.tools import create_ticket, get_logistics, get_order


class OrderIdRequired(ValueError):
    """Raised when a direct-tool intent has no order id to look up."""


def dispatch_message(message: str, ctx: SecurityContext) -> dict:
    route = route_intent(message)
    if should_use_agent_loop(route, message):
        return run_agent_loop(message, ctx, intent=route['intent'])

    intent = route['intent']
    order_id = route.get('order_id')

    if intent == 'faq':
        docs = retrieve(message, top_n=3, ctx=ctx)
        answer = docs[0]['content'] if docs else '当前知识库没有足够依据。'
        return {
            'mode': 'direct_rag',
            'intent': intent,
            'answer': answer,
            'citations': [
                {'title': doc['title'], 'version': doc['version']} for doc in docs
            ],
            'evidence': docs,
        }

    if not order_id:
        raise OrderIdRequired('请提供订单号，例如 O10001')

    if intent == 'order_query':
        return {'mode': 'direct_tool', 'intent': intent, 'order': get_order(order_id, ctx)}

    if intent == 'logistics_query':
        logistics = get_logistics(order_id, ctx)
        if not logistics:
            return {
                'mode': 'direct_tool',
                'intent': intent,
                'answer': '该订单暂无物流信息。',
            }
        ticket = None
        if int(logistics['last_update_hours']) >= 24:
            ticket = create_ticket(
                ctx,
                order_id,
                'logistics_abnormal',
                message,
                stale_hours=int(logistics['last_update_hours']),
            )
            record_audit(
                None,
                ctx.user_id,
                'ticket_created',
                {
                    'ticket_id': ticket['id'],
                    'order_id': ticket.get('order_id'),
                    'category': ticket['category'],
                    'priority': ticket['priority'],
                    'assigned_group': ticket['assigned_group'],
                    'source': 'direct_tool',
                },
            )
        return {
            'mode': 'direct_tool',
            'intent': intent,
            'logistics': logistics,
            'ticket': ticket,
            'answer': (
                f"当前物流状态 {logistics['status']}，最后节点：{logistics['last_event']}。"
                + (f" 已创建工单 {ticket['id']}。" if ticket else '')
            ),
        }

    return {
        'mode': 'workflow',
        'intent': intent,
        **create_task(
            ctx,
            intent,
            {'message': message, 'order_id': order_id, 'role': ctx.role},
        ),
    }
