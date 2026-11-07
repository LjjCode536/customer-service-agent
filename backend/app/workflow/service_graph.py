from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypedDict

from sqlalchemy import func, select

from app.core.config import settings
from app.core.context import scoped
from app.core.security import SecurityContext
from app.db.database import dumps, session_scope, use_session
from app.db.models import ApprovalRecord
from app.llm.provider import get_provider
from app.rag.hybrid import retrieve
from app.services.rules import (
    approval_chain,
    complaint_assessment,
    refund_eligibility,
    refund_risk,
)
from app.services.tasking import (
    emit_event,
    get_task,
    new_id,
    record_audit,
    record_trace,
    trace_step,
    update_task,
)
from app.services.tools import (
    create_ticket,
    get_customer,
    get_logistics,
    get_order,
    get_refunds,
    submit_refund,
)
from app.workflow.checkpointer import create_checkpointer

HIGH_RISK_REVIEW_MESSAGE = '该退款金额较高，已进入人工审核，审核通过后系统会继续原退款流程。'
REJECTION_MESSAGE = '人工审核未通过，本次退款不会执行。'


class ServiceState(TypedDict, total=False):
    task_id: str; trace_id: str; tenant_id: str; user_id: str; role: str; intent: str; message: str; order_id: str
    customer: dict; order: dict; logistics: dict; knowledge: list[dict]; complaint: dict; refunds: list[dict]; eligibility: dict; risk: dict; approval_chain: list[str]; review_decision: dict; ticket: dict; refund: dict; answer: str


def ctx(s): return SecurityContext(s['user_id'], s['tenant_id'], s.get('role', 'customer'))


def _node_customer(s): return {'customer': get_customer(ctx(s))}
def _node_order(s): return {'order': get_order(s['order_id'], ctx(s))}
def _node_logistics(s): return {'logistics': get_logistics(s['order_id'], ctx(s)) or {}}


def _node_knowledge(s):
    category = 'logistics' if s['intent'] == 'complaint' else 'refund'
    return {'knowledge': retrieve(s['message'], category=category, product_type=s.get('order', {}).get('product_type'), top_n=4)}


def _node_refunds(s): return {'refunds': get_refunds(s['order_id'], ctx(s))}
def _node_eligibility(s): return {'eligibility': refund_eligibility(s['order'], s.get('refunds', []))}


def _node_risk(s):
    r = refund_risk(s['order'], s['customer'], s['eligibility']); return {'risk': r, 'approval_chain': approval_chain(r)}


def _node_human_review(s):
    """Pause the graph with a durable checkpoint and wait for the whole approval chain."""
    ensure_approvals(s['task_id'], s['order_id'], s['approval_chain'])
    from langgraph.types import interrupt

    decision = interrupt(_waiting_payload(s))
    return {'review_decision': decision or {}}


def _submit_refund_recorded(s, order, reason):
    """Submit a refund and persist its Audit record in the same transaction."""
    with session_scope() as session:
        refund = submit_refund(ctx(s), order, reason, session=session)
        payload = {
            'refund_id': refund['id'],
            'order_id': refund['order_id'],
            'external_refund_id': refund['external_refund_id'],
            'amount': refund['amount'],
            'reason': reason,
            'idempotent_reuse': refund['idempotent_reuse'],
        }
        if not refund['idempotent_reuse']:
            # Repeated resumes reuse the existing refund; only the real side effect is audited.
            record_audit(s['trace_id'], s['user_id'], 'refund_submitted', payload, session=session)
            emit_event(s['task_id'], 'refund_submitted', payload, session=session)
    return refund


def _node_submit_refund(s):
    reason = 'approved_high_value_refund' if s['risk']['risk_level'] == 'high' else 'agent_refund'
    return {'refund': _submit_refund_recorded(s, s['order'], reason)}


def _node_assess_complaint(s):
    return {'complaint': complaint_assessment(s['customer'], s.get('logistics', {}))}


def _create_ticket(s, category, reason, complaint=False, stale_hours=0, priority=None):
    """Create a ticket and its side-effect records in one transaction."""
    with session_scope() as session:
        ticket = create_ticket(
            ctx(s),
            s.get('order_id'),
            category,
            s['message'],
            complaint=complaint,
            stale_hours=stale_hours,
            priority=priority,
            session=session,
        )
        payload = {
            'ticket_id': ticket['id'],
            'order_id': ticket.get('order_id'),
            'category': ticket['category'],
            'priority': ticket['priority'],
            'assigned_group': ticket['assigned_group'],
            'reason': reason,
            'reasons': s.get('complaint', {}).get('reasons', []),
        }
        emit_event(s['task_id'], 'ticket_created', payload, session=session)
        record_audit(s['trace_id'], s['user_id'], 'ticket_created', payload, session=session)
    return {'ticket': ticket}


def _node_complaint_ticket(s):
    assessment = s['complaint']
    return _create_ticket(
        s,
        'complaint',
        'complaint_workflow',
        complaint=True,
        stale_hours=assessment['stale_hours'],
        priority=assessment['priority'],
    )


def _node_refund_ticket(s):
    category = 'refund_duplicate' if s.get('eligibility', {}).get('reason') == 'DUPLICATE_REFUND_REQUEST' else 'refund_review'
    return _create_ticket(s, category, s.get('eligibility', {}).get('reason', 'refund_review'))


def _node_answer(s):
    if s.get('review_decision') and not s['review_decision'].get('approved'):
        base = REJECTION_MESSAGE
    elif s.get('ticket'):
        base = f"已为你创建工单 {s['ticket']['id']}，优先级 {s['ticket']['priority']}，已分派到 {s['ticket']['assigned_group']}。"
    elif s.get('refund'):
        base = f"退款申请已提交，金额 {float(s['refund']['amount']):.2f} 元，退款单号 {s['refund']['external_refund_id']}。"
    elif s.get('eligibility', {}).get('reason') == 'DUPLICATE_REFUND_REQUEST':
        base = '该订单已经存在退款申请，为避免重复退款，本次不会再次创建退款。'
    else:
        base = '客服流程已完成。'
    evidence = {'message': s['message'], 'order': s.get('order'), 'logistics': s.get('logistics'), 'complaint': s.get('complaint'), 'eligibility': s.get('eligibility'), 'risk': s.get('risk'), 'ticket': s.get('ticket'), 'refund': s.get('refund'), 'knowledge': [{'title': x['title'], 'content': x['content']} for x in s.get('knowledge', [])]}
    return {'answer': get_provider().chat('你是企业客服助手。只能解释系统已经完成的业务结果，不得改变退款结果、风险等级或工单优先级。', f'请用简洁自然的中文回复用户：{dumps(evidence)}', mock_response=base)}


NODES = {
    'load_customer': _node_customer,
    'load_order': _node_order,
    'load_logistics': _node_logistics,
    'assess_complaint': _node_assess_complaint,
    'retrieve_policy': _node_knowledge,
    'load_refunds': _node_refunds,
    'check_eligibility': _node_eligibility,
    'risk_check': _node_risk,
    'human_review': _node_human_review,
    'submit_refund': _node_submit_refund,
    'create_complaint_ticket': _node_complaint_ticket,
    'create_refund_ticket': _node_refund_ticket,
    'answer': _node_answer,
}


# Trace a compact, replayable slice of state for each node.
NODE_INPUT_KEYS = {
    'load_customer': ('user_id', 'tenant_id'),
    'load_order': ('order_id',),
    'load_logistics': ('order_id',),
    'assess_complaint': ('customer', 'logistics'),
    'retrieve_policy': ('message', 'intent', 'order'),
    'load_refunds': ('order_id',),
    'check_eligibility': ('order', 'refunds'),
    'risk_check': ('order', 'customer', 'eligibility'),
    'human_review': ('order_id', 'risk', 'eligibility', 'approval_chain'),
    'submit_refund': ('order', 'risk'),
    'create_complaint_ticket': ('order_id', 'message', 'complaint'),
    'create_refund_ticket': ('order_id', 'message', 'eligibility'),
    'answer': ('message', 'ticket', 'refund', 'eligibility', 'review_decision'),
}


def _trace_input(name, s):
    data = {'intent': s.get('intent'), 'order_id': s.get('order_id')}
    for key in NODE_INPUT_KEYS.get(name, ()):
        data[key] = s.get(key)
    return data


def run_node(name, s):
    trace_input = _trace_input(name, s)
    emit_event(s['task_id'], 'node_start', {'node': name})
    if name == 'human_review':
        # interrupt() is control flow, not a failure: never record it as a failed trace.
        from langgraph.errors import GraphInterrupt

        try:
            out = trace_step(
                s['trace_id'],
                name,
                trace_input,
                lambda: NODES[name](s),
                skip_exceptions=(GraphInterrupt,),
            )
        except GraphInterrupt:
            emit_event(s['task_id'], 'node_paused', {'node': name})
            raise
        except Exception as exc:
            emit_event(s['task_id'], 'node_failed', {'node': name, 'error': str(exc)})
            raise
    else:
        try:
            out = trace_step(s['trace_id'], name, trace_input, lambda: NODES[name](s))
        except Exception as exc:
            emit_event(s['task_id'], 'node_failed', {'node': name, 'error': str(exc)})
            raise
    emit_event(s['task_id'], 'node_completed', {'node': name, 'output': out})
    return out


def _after_policy(s): return 'complaint' if s['intent'] == 'complaint' else 'refund'


def _after_eligibility(s):
    return 'duplicate' if s['eligibility']['reason'] == 'DUPLICATE_REFUND_REQUEST' else 'risk'


def _after_risk(s):
    if not s['eligibility']['eligible']:
        return 'ticket'
    return 'human' if s['risk']['risk_level'] == 'high' else 'submit'


def _after_human_review(s):
    return 'submit' if s.get('review_decision', {}).get('approved') else 'rejected'


def compile_graph(checkpointer=None):
    """Compile the real refund workflow; a caller may pass a fresh checkpointer to test restart recovery."""
    from langgraph.graph import END, StateGraph

    g = StateGraph(ServiceState)
    for name in NODES:
        def make(n):
            return lambda s: run_node(n, s)
        g.add_node(name, make(name))

    g.set_entry_point('load_customer')
    g.add_edge('load_customer', 'load_order')
    g.add_edge('load_order', 'retrieve_policy')
    g.add_conditional_edges('retrieve_policy', _after_policy, {'complaint': 'load_logistics', 'refund': 'load_refunds'})
    g.add_edge('load_logistics', 'assess_complaint')
    g.add_edge('assess_complaint', 'create_complaint_ticket')
    g.add_edge('create_complaint_ticket', 'answer')
    g.add_edge('load_refunds', 'check_eligibility')
    g.add_conditional_edges('check_eligibility', _after_eligibility, {'duplicate': 'create_refund_ticket', 'risk': 'risk_check'})
    g.add_conditional_edges('risk_check', _after_risk, {'human': 'human_review', 'submit': 'submit_refund', 'ticket': 'create_refund_ticket'})
    g.add_conditional_edges('human_review', _after_human_review, {'submit': 'submit_refund', 'rejected': 'answer'})
    g.add_edge('submit_refund', 'answer')
    g.add_edge('create_refund_ticket', 'answer')
    g.add_edge('answer', END)
    return g.compile(checkpointer=checkpointer or create_checkpointer())


def _build_graph():
    try:
        return compile_graph()
    except Exception:  # noqa: BLE001  # optional-dependency boundary: absence degrades to None, never crashes import
        # LangGraph (or its SQLite checkpointer) is unavailable. process_task refuses to
        # silently downgrade unless WORKFLOW_ENGINE=fallback is set explicitly.
        return None


GRAPH = _build_graph()

# Evaluation compiles its own graph against an isolated checkpoint store.
_NO_GRAPH_OVERRIDE = object()
_graph_override: ContextVar[Any] = ContextVar(
    'workflow_graph_override', default=_NO_GRAPH_OVERRIDE
)


@contextmanager
def use_graph(graph: Any) -> Iterator[Any]:
    with scoped(_graph_override, graph):
        yield graph


def _graph_for_execution():
    override = _graph_override.get()
    if override is not _NO_GRAPH_OVERRIDE:
        return override
    if GRAPH is not None:
        return GRAPH
    if settings.workflow_engine == 'fallback':
        return None
    if settings.workflow_engine != 'langgraph':
        raise RuntimeError(f'UNKNOWN_WORKFLOW_ENGINE:{settings.workflow_engine}')
    raise RuntimeError('LANGGRAPH_UNAVAILABLE')


def _fallback(state):
    s = dict(state)
    for n in ['load_customer', 'load_order', 'retrieve_policy']:
        s.update(run_node(n, s))
    if s['intent'] == 'complaint':
        for n in ['load_logistics', 'assess_complaint', 'create_complaint_ticket', 'answer']:
            s.update(run_node(n, s))
        return s
    for n in ['load_refunds', 'check_eligibility', 'risk_check']:
        s.update(run_node(n, s))
    if s['eligibility']['reason'] == 'DUPLICATE_REFUND_REQUEST':
        s.update(run_node('create_refund_ticket', s)); s.update(run_node('answer', s)); return s
    if not s['eligibility']['eligible']:
        s.update(run_node('create_refund_ticket', s)); s.update(run_node('answer', s)); return s
    if s['risk']['risk_level'] == 'high':
        return s
    s.update(run_node('submit_refund', s)); s.update(run_node('answer', s)); return s


def _config(task_id):
    return {'configurable': {'thread_id': task_id}}


def _interrupt_value(result):
    interrupts = result.get('__interrupt__') if isinstance(result, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, 'value', first)


def _needs_human_review(result):
    return (
        result.get('risk', {}).get('risk_level') == 'high'
        and result.get('eligibility', {}).get('eligible') is True
        and not result.get('refund')
    )


def ensure_approvals(task_id, order_id, chain, session=None):
    with use_session(session) as s:
        old = s.scalar(
            select(func.count()).select_from(ApprovalRecord).where(ApprovalRecord.task_id == task_id)
        )
        if old:
            return
        s.add_all(
            [
                ApprovalRecord(
                    id=new_id('approval'),
                    task_id=task_id,
                    order_id=order_id,
                    approver_role=role,
                    sequence_no=index,
                    status='pending',
                )
                for index, role in enumerate(chain, 1)
            ]
        )
        s.flush()


def _waiting_payload(result):
    return {
        'status': 'waiting_approval',
        'risk': result.get('risk'),
        'approval_chain': result.get('approval_chain') or approval_chain(result.get('risk') or {}),
        'eligibility': result.get('eligibility'),
        'answer': HIGH_RISK_REVIEW_MESSAGE,
    }


def _mark_waiting(task, result):
    payload = _waiting_payload(result)
    with session_scope() as s:
        ensure_approvals(task['id'], result['order_id'], payload['approval_chain'], session=s)
        update_task(task['id'], status='waiting_approval', current_step='human_review', result=payload, session=s)
        emit_event(task['id'], 'human_review_required', payload, session=s)
        record_trace(
            task['trace_id'],
            'human_review',
            status='waiting_approval',
            input_data=_trace_input(
                'human_review', {**result, 'intent': task['intent']}
            ),
            output=payload,
            session=s,
        )
    return payload


def _completed_payload(result):
    return {
        'status': 'completed',
        'answer': result.get('answer'),
        'ticket': result.get('ticket'),
        'refund': result.get('refund'),
        'risk': result.get('risk'),
        'eligibility': result.get('eligibility'),
        'complaint': result.get('complaint'),
    }


def _mark_completed(task, result, current_step='done', payload=None):
    payload = payload or _completed_payload(result)
    with session_scope() as s:
        update_task(task['id'], status='completed', current_step=current_step, result=payload, session=s)
        emit_event(task['id'], 'task_completed', payload, session=s)
        record_audit(
            task['trace_id'],
            task['user_id'],
            'service_workflow_completed',
            payload,
            session=s,
        )
    return payload


def process_task(task):
    p = task['payload']; state: ServiceState = {'task_id': task['id'], 'trace_id': task['trace_id'], 'tenant_id': task['tenant_id'], 'user_id': task['user_id'], 'role': p.get('role', 'customer'), 'intent': task['intent'], 'message': p['message'], 'order_id': p.get('order_id')}
    with session_scope() as s:
        update_task(task['id'], status='running', current_step='workflow', session=s)
        emit_event(task['id'], 'workflow_start', {'intent': task['intent']}, session=s)

    graph = _graph_for_execution()
    if graph is None:
        result = _fallback(state)
    else:
        result = graph.invoke(state, _config(task['id']))

    if _interrupt_value(result) is not None or _needs_human_review(result):
        return _mark_waiting(task, result)
    return _mark_completed(task, result)


def _approvals(task_id):
    with use_session() as s:
        return [
            row.to_dict()
            for row in s.scalars(
                select(ApprovalRecord)
                .where(ApprovalRecord.task_id == task_id)
                .order_by(ApprovalRecord.sequence_no)
            )
        ]


def _fallback_resume(task, approved):
    if not approved:
        return {'answer': REJECTION_MESSAGE, 'refund': None}
    p = task['payload']
    state = {
        'task_id': task['id'],
        'trace_id': task['trace_id'],
        'user_id': task['user_id'],
        'tenant_id': task['tenant_id'],
        'role': p.get('role', 'customer'),
    }
    order = get_order(p['order_id'], ctx(state))
    refund = _submit_refund_recorded(state, order, 'approved_high_value_refund')
    return {'refund': refund, 'answer': f"人工审核已通过，退款 {float(refund['amount']):.2f} 元已提交，退款单号 {refund['external_refund_id']}。"}


def resume_after_approval(task):
    existing = get_task(task['id'])
    if existing and existing['status'] == 'completed':
        return existing['result']

    rows = _approvals(task['id'])
    approved = not any(r['status'] == 'rejected' for r in rows) and not any(r['status'] == 'pending' for r in rows)
    if any(r['status'] == 'pending' for r in rows):
        update_task(task['id'], status='waiting_approval', current_step='human_review')
        return {'status': 'waiting_approval'}

    graph = _graph_for_execution()
    if graph is None:
        result = _fallback_resume(task, approved)
    else:
        from langgraph.types import Command

        decision = {'approved': approved, 'approvals': rows}
        result = graph.invoke(Command(resume=decision), _config(task['id']))

    if _interrupt_value(result) is not None:
        return _mark_waiting(task, result)
    if not approved:
        payload = {'status': 'rejected', 'answer': result.get('answer') or REJECTION_MESSAGE, 'refund': None}
        return _mark_completed(task, result, current_step='rejected', payload=payload)
    return _mark_completed(task, result, current_step='refund_submitted')
