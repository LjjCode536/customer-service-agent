"""Read-only, budget-bounded Agent Loop.

The loop exists for ambiguous chat requests and multi-step read-only queries.
It can only call three read-only tools; any side effect still goes through the
Rule Engine + Workflow path. Every step is written to Trace.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.security import SecurityContext
from app.rag.hybrid import retrieve
from app.services.router import (
    KNOWLEDGE_KEYWORDS as ROUTER_KNOWLEDGE_KEYWORDS,
)
from app.services.router import (
    LOGISTICS_KEYWORDS as ROUTER_LOGISTICS_KEYWORDS,
)
from app.services.router import (
    ORDER_KEYWORDS as ROUTER_ORDER_KEYWORDS,
)
from app.services.router import (
    extract_order_id,
)
from app.services.tasking import new_id, record_trace
from app.services.tools import get_logistics, get_order

READ_ONLY_TOOL_NAMES = ('get_order', 'get_logistics', 'retrieve_knowledge')

LOGISTICS_KEYWORDS = ROUTER_LOGISTICS_KEYWORDS + (
    '发货',
    '签收',
    '派送',
    '运输',
    '运费',
)
KNOWLEDGE_KEYWORDS = ROUTER_KNOWLEDGE_KEYWORDS + (
    '售后',
    '政策',
    '规则',
    'sop',
    '税号',
    '开票',
    '流程',
    '说明书',
)
ORDER_KEYWORDS = ROUTER_ORDER_KEYWORDS + ('支付', '金额', '下单', '买的')
MULTI_STEP_MARKERS = ('顺便', '另外', '还有', '以及', '同时', '再帮我', '再查', '再看', '并')

BUDGET_STOP_REASONS = {
    'budget_exhausted_steps',
    'budget_exhausted_tool_calls',
    'budget_exhausted_tokens',
}
HANDOFF_STOP_REASONS = BUDGET_STOP_REASONS | {
    'insufficient_evidence',
    'tool_failure',
    'needs_human',
}
HUMAN_HANDOFF_KEYWORDS = ('转人工', '人工客服', '找人工', '真人客服', '人工处理')
GREETING_KEYWORDS = ('你好', '您好', 'hi', 'hello', '在吗')


class ToolNotAllowedError(RuntimeError):
    """Raised when the loop tries to use a tool outside the read-only allowlist."""


@dataclass(frozen=True)
class AgentBudget:
    max_steps: int
    max_tool_calls: int
    max_tokens: int

    @classmethod
    def from_settings(cls) -> AgentBudget:
        return cls(
            max_steps=settings.agent_loop_max_steps,
            max_tool_calls=settings.agent_loop_max_tool_calls,
            max_tokens=settings.agent_loop_max_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            'max_steps': self.max_steps,
            'max_tool_calls': self.max_tool_calls,
            'max_tokens': self.max_tokens,
        }


@dataclass
class AgentState:
    query: str = ''
    order: dict[str, Any] | None = None
    order_loaded: bool = False
    logistics: dict[str, Any] | None = None
    logistics_loaded: bool = False
    evidence: list[dict[str, Any]] | None = None


def _tool_get_order(arguments: Mapping[str, Any], ctx: SecurityContext):
    return get_order(arguments['order_id'], ctx)


def _tool_get_logistics(arguments: Mapping[str, Any], ctx: SecurityContext):
    return get_logistics(arguments['order_id'], ctx)


def _tool_retrieve_knowledge(arguments: Mapping[str, Any], ctx: SecurityContext):
    return retrieve(
        arguments['query'],
        top_n=arguments.get('top_n', 3),
    )


# The allowlist is the only dispatch table. Side-effect tools are intentionally absent.
READ_ONLY_TOOL_REGISTRY: dict[
    str, Callable[[Mapping[str, Any], SecurityContext], Any]
] = {
    'get_order': _tool_get_order,
    'get_logistics': _tool_get_logistics,
    'retrieve_knowledge': _tool_retrieve_knowledge,
}


def execute_read_only_tool(
    name: str,
    arguments: Mapping[str, Any],
    ctx: SecurityContext,
):
    handler = READ_ONLY_TOOL_REGISTRY.get(name)
    if name not in READ_ONLY_TOOL_NAMES or handler is None:
        raise ToolNotAllowedError(f'AGENT_LOOP_TOOL_NOT_ALLOWED: {name}')
    return handler(arguments, ctx)


def should_use_agent_loop(route: Mapping[str, Any], message: str) -> bool:
    """Trigger on ambiguous chat, low router confidence, or a multi-step read-only ask."""
    intent = route.get('intent')
    if intent in {'refund', 'complaint'}:
        return False
    if intent == 'chat':
        return True
    confidence = route.get('confidence')
    if isinstance(confidence, (int, float)) and confidence < settings.agent_loop_confidence_threshold:
        return True
    return is_multi_step_query(message)


def is_multi_step_query(message: str) -> bool:
    """An order plus a second read-only domain needs more than one tool."""
    if not extract_order_id(message):
        return False
    domains = _read_only_domains(message)
    if len(domains) < 2:
        return False
    if 'knowledge' in domains:
        return True
    lowered = (message or '').lower()
    return any(marker in lowered for marker in MULTI_STEP_MARKERS)


def _read_only_domains(text: str) -> set[str]:
    lowered = (text or '').lower()
    domains: set[str] = set()
    if extract_order_id(text) or any(word in lowered for word in ORDER_KEYWORDS):
        domains.add('order')
    if any(word in lowered for word in LOGISTICS_KEYWORDS):
        domains.add('logistics')
    if any(word in lowered for word in KNOWLEDGE_KEYWORDS):
        domains.add('knowledge')
    return domains


def _wants_logistics(query: str) -> bool:
    lowered = (query or '').lower()
    return any(word in lowered for word in LOGISTICS_KEYWORDS)


def _wants_knowledge(query: str) -> bool:
    lowered = (query or '').lower()
    return any(word in lowered for word in KNOWLEDGE_KEYWORDS)


def _wants_human(query: str) -> bool:
    lowered = (query or '').lower()
    return any(word in lowered for word in HUMAN_HANDOFF_KEYWORDS)


def _is_greeting(query: str) -> bool:
    lowered = (query or '').lower()
    return any(word in lowered for word in GREETING_KEYWORDS)


def estimate_tokens(value: Any) -> int:
    """Cheap deterministic token estimate; the budget is a guardrail, not billing."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else str(value)
    return max(1, len(text) // 4) if text else 0


def _plan_next(
    query: str,
    order_id: str | None,
    state: AgentState,
) -> dict[str, Any] | None:
    """Pick the next read-only action; None means the loop should finalize."""
    if order_id and not state.order_loaded:
        return {'tool': 'get_order', 'arguments': {'order_id': order_id}}
    if (
        order_id
        and state.order_loaded
        and not state.logistics_loaded
        and _wants_logistics(query)
    ):
        return {'tool': 'get_logistics', 'arguments': {'order_id': order_id}}
    if state.evidence is None and _wants_knowledge(query):
        return {
            'tool': 'retrieve_knowledge',
            'arguments': {'query': query, 'top_n': 3},
        }
    return None


def _apply_observation(state: AgentState, tool: str, output: Any) -> None:
    if tool == 'get_order':
        state.order = output
        state.order_loaded = True
    elif tool == 'get_logistics':
        state.logistics = output
        state.logistics_loaded = True
    elif tool == 'retrieve_knowledge':
        state.evidence = output or []


def _stop_reason(query: str, order_id: str | None, state: AgentState) -> str:
    if _wants_knowledge(query) and state.evidence is not None and not state.evidence:
        return 'insufficient_evidence'
    if state.order or state.logistics or state.evidence:
        return 'evidence_sufficient'
    if order_id:
        return 'insufficient_evidence'
    return 'clarification'


def _compose_answer(state: AgentState, stop_reason: str) -> str:
    parts: list[str] = []
    order = state.order
    if order:
        parts.append(
            f"订单 {order['id']} 当前状态 {order.get('status')}，"
            f"支付状态 {order.get('payment_status')}，发货状态 {order.get('shipment_status')}。"
        )
    logistics = state.logistics
    if logistics:
        parts.append(
            f"物流状态 {logistics.get('status')}，最后节点：{logistics.get('last_event')}。"
        )
    evidence = state.evidence or []
    if evidence:
        top = evidence[0]
        parts.append(f"根据《{top.get('title')}》（{top.get('version')}）：{top.get('content')}")

    if stop_reason == 'clarification':
        if _is_greeting(state.query):
            parts.append('你好，我可以帮你查询订单、物流或售后政策。请告诉我订单号或具体问题。')
        else:
            parts.append('我还需要更多信息：请提供订单号，或说明要查询订单、物流还是售后政策。')
    elif stop_reason in BUDGET_STOP_REASONS:
        parts.append('查询步骤或预算已达上限，已停止自动查询并转接人工客服继续处理。')
    elif stop_reason == 'insufficient_evidence':
        parts.append('当前没有足够的知识库依据，已转接人工客服继续处理。')
    elif stop_reason == 'tool_failure':
        parts.append('查询工具暂时不可用，已停止自动查询并转接人工客服。')
    elif stop_reason == 'needs_human':
        parts.append('正在为你转接人工客服。')

    return ''.join(parts) or '我暂时无法确认你的诉求，已转接人工客服继续处理。'


def run_agent_loop(
    query: str,
    ctx: SecurityContext,
    *,
    intent: str = 'chat',
    budget: AgentBudget | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Run the bounded read-only loop and always return a terminal answer."""
    budget = budget or AgentBudget.from_settings()
    trace_id = trace_id or new_id('trace')
    order_id = extract_order_id(query)
    state = AgentState(query=query)
    started_run = time.perf_counter()

    steps = 0
    tool_calls = 0
    tokens_used = estimate_tokens(query)
    stop_reason: str | None = None
    error: str | None = None

    if tokens_used > budget.max_tokens:
        stop_reason = 'budget_exhausted_tokens'
    elif _wants_human(query):
        stop_reason = 'needs_human'

    while stop_reason is None:
        if steps >= budget.max_steps:
            stop_reason = 'budget_exhausted_steps'
            break
        # One planning round is one step; only executed tools increment tool_calls.
        steps += 1

        action = _plan_next(query, order_id, state)
        if action is None:
            stop_reason = _stop_reason(query, order_id, state)
            break

        if tool_calls >= budget.max_tool_calls:
            stop_reason = 'budget_exhausted_tool_calls'
            break

        tool = action['tool']
        arguments = action['arguments']
        if tokens_used + estimate_tokens(arguments) > budget.max_tokens:
            stop_reason = 'budget_exhausted_tokens'
            break
        started = time.perf_counter()
        try:
            output = execute_read_only_tool(tool, arguments, ctx)
        except (PermissionError, ValueError) as exc:
            # Ownership/tenant/not-found errors must stay 403/404 for the API caller,
            # exactly like the direct-tool path; they are not generic tool failures.
            record_trace(
                trace_id,
                f'agent_loop.tool.{tool}',
                status='failed',
                input_data={'tool': tool, 'arguments': arguments},
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - any other tool error terminates the loop
            error = str(exc)
            record_trace(
                trace_id,
                f'agent_loop.tool.{tool}',
                status='failed',
                input_data={'tool': tool, 'arguments': arguments},
                error=error,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            stop_reason = 'tool_failure'
            break

        tool_calls += 1
        tokens_used += estimate_tokens(arguments) + estimate_tokens(output)
        _apply_observation(state, tool, output)
        record_trace(
            trace_id,
            f'agent_loop.tool.{tool}',
            status='success',
            input_data={'tool': tool, 'arguments': arguments},
            output={'result': output},
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        if tokens_used > budget.max_tokens:
            stop_reason = 'budget_exhausted_tokens'

    handoff = stop_reason in HANDOFF_STOP_REASONS
    answer = _compose_answer(state, stop_reason)
    record_trace(
        trace_id,
        'agent_loop.stop',
        status='degraded' if handoff else 'success',
        input_data={'query': query, 'intent': intent, 'budget': budget.to_dict()},
        output={
            'stop_reason': stop_reason,
            'steps': steps,
            'tool_calls': tool_calls,
            'tokens_used': tokens_used,
            'handoff': handoff,
        },
        error=error,
        latency_ms=int((time.perf_counter() - started_run) * 1000),
    )

    return {
        'mode': 'agent_loop',
        'intent': intent,
        'trace_id': trace_id,
        'status': 'completed' if stop_reason == 'evidence_sufficient' else 'degraded',
        'stop_reason': stop_reason,
        'handoff': handoff,
        'answer': answer,
        'order': state.order,
        'logistics': state.logistics,
        'evidence': state.evidence or [],
        'steps': steps,
        'tool_calls': tool_calls,
        'tokens_used': tokens_used,
        'budget': budget.to_dict(),
        'error': error,
    }
