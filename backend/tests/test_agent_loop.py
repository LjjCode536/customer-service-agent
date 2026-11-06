import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import SecurityContext
from app.db.database import session_scope
from app.db.models import RefundRequest, Ticket, TraceStep
from app.main import app
from app.services import agent_loop
from app.services.agent_loop import (
    READ_ONLY_TOOL_NAMES,
    AgentBudget,
    ToolNotAllowedError,
    execute_read_only_tool,
    is_multi_step_query,
    run_agent_loop,
    should_use_agent_loop,
)
from app.services.router import route_intent

MULTI_STEP_QUERY = '订单 O10001 物流到哪了，另外电子发票怎么开？'


def _chat(client, message, user='U1001'):
    return client.post('/api/v1/chat', json={'message': message}, headers={'X-User-Id': user})


def _trace_nodes(trace_id):
    with session_scope() as session:
        return [
            row.node
            for row in session.scalars(
                select(TraceStep)
                .where(TraceStep.trace_id == trace_id)
                .order_by(TraceStep.id)
            ).all()
        ]


def _side_effect_counts():
    with session_scope() as session:
        return (
            session.scalar(select(func.count()).select_from(Ticket)),
            session.scalar(select(func.count()).select_from(RefundRequest)),
        )


def test_chat_intent_runs_agent_loop_instead_of_a_canned_reply(seeded_db):
    client = TestClient(app)

    response = _chat(client, '你好，我想咨询点事情')

    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'agent_loop'
    assert body['intent'] == 'chat'
    assert body['trace_id']
    assert body['answer']
    assert body['budget'] == {'max_steps': 4, 'max_tool_calls': 6, 'max_tokens': 1500}
    assert _trace_nodes(body['trace_id'])[-1] == 'agent_loop.stop'


def test_multi_step_query_returns_agent_loop_with_read_only_evidence(seeded_db):
    client = TestClient(app)
    before = _side_effect_counts()

    response = _chat(client, MULTI_STEP_QUERY)

    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'agent_loop'
    assert body['intent'] == 'logistics_query'
    assert body['stop_reason'] == 'evidence_sufficient'
    assert body['handoff'] is False
    assert body['order']['id'] == 'O10001'
    assert body['logistics']['order_id'] == 'O10001'
    assert body['evidence']
    # 3 tools + 1 finalize decision fit the default max_steps=4 budget.
    assert body['steps'] == 4
    assert body['tool_calls'] == 3
    assert set(_trace_nodes(body['trace_id'])) >= {
        'agent_loop.tool.get_order',
        'agent_loop.tool.get_logistics',
        'agent_loop.tool.retrieve_knowledge',
        'agent_loop.stop',
    }
    assert _side_effect_counts() == before


def test_low_router_confidence_routes_to_agent_loop(seeded_db, monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(settings, 'agent_loop_confidence_threshold', 0.99)

    response = _chat(client, '电子发票怎么开？')

    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'agent_loop'
    assert body['intent'] == 'faq'
    assert body['evidence']


def test_should_use_agent_loop_skips_side_effect_intents():
    refund = {'intent': 'refund', 'confidence': 0.2, 'order_id': 'O10002'}
    complaint = {'intent': 'complaint', 'confidence': 0.2, 'order_id': 'O10005'}

    assert should_use_agent_loop(refund, '订单 O10002 帮我退款') is False
    assert should_use_agent_loop(complaint, '订单 O10005 我要投诉') is False
    assert should_use_agent_loop(route_intent('随便聊聊'), '随便聊聊') is True


def test_multi_step_detection_requires_a_second_domain():
    assert is_multi_step_query(MULTI_STEP_QUERY) is True
    assert is_multi_step_query('订单 O10001 状态，还有物流到哪了') is True
    assert is_multi_step_query('订单 O10001 物流两天没更新') is False
    assert is_multi_step_query('电子发票怎么开？') is False


def test_agent_loop_only_exposes_read_only_tools():
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

    assert set(READ_ONLY_TOOL_NAMES) == {
        'get_order',
        'get_logistics',
        'retrieve_knowledge',
    }
    assert set(agent_loop.READ_ONLY_TOOL_REGISTRY) == set(READ_ONLY_TOOL_NAMES)

    for forbidden in ('submit_refund', 'create_ticket', 'update_order'):
        with pytest.raises(ToolNotAllowedError):
            execute_read_only_tool(forbidden, {'order_id': 'O10001'}, context)


def test_step_budget_stops_the_loop_and_hands_off(seeded_db):
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

    result = run_agent_loop(
        MULTI_STEP_QUERY,
        context,
        budget=AgentBudget(max_steps=1, max_tool_calls=6, max_tokens=10000),
    )

    assert result['mode'] == 'agent_loop'
    assert result['stop_reason'] == 'budget_exhausted_steps'
    assert result['handoff'] is True
    assert result['steps'] == 1
    assert result['tool_calls'] == 1


def test_tool_call_budget_stops_the_loop_and_hands_off(seeded_db):
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

    result = run_agent_loop(
        MULTI_STEP_QUERY,
        context,
        budget=AgentBudget(max_steps=10, max_tool_calls=1, max_tokens=10000),
    )

    assert result['stop_reason'] == 'budget_exhausted_tool_calls'
    assert result['handoff'] is True
    assert result['tool_calls'] == 1


def test_token_budget_stops_before_any_tool_call(seeded_db):
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

    result = run_agent_loop(
        MULTI_STEP_QUERY,
        context,
        budget=AgentBudget(max_steps=4, max_tool_calls=6, max_tokens=0),
    )

    assert result['stop_reason'] == 'budget_exhausted_tokens'
    assert result['handoff'] is True
    assert result['tool_calls'] == 0
    assert result['steps'] == 0


def test_insufficient_evidence_falls_back_to_a_human(seeded_db, monkeypatch):
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')
    monkeypatch.setitem(
        agent_loop.READ_ONLY_TOOL_REGISTRY,
        'retrieve_knowledge',
        lambda arguments, ctx: [],
    )

    result = run_agent_loop('电子发票怎么开？', context)

    assert result['stop_reason'] == 'insufficient_evidence'
    assert result['handoff'] is True
    assert result['evidence'] == []
    assert '人工' in result['answer']


def test_tool_failure_stops_the_loop_and_records_a_failed_trace(seeded_db, monkeypatch):
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

    def broken_tool(arguments, ctx):
        raise RuntimeError('database unavailable')

    monkeypatch.setitem(agent_loop.READ_ONLY_TOOL_REGISTRY, 'get_order', broken_tool)

    result = run_agent_loop('查一下订单 O10001', context)

    assert result['stop_reason'] == 'tool_failure'
    assert result['handoff'] is True
    assert 'database unavailable' in result['error']
    with session_scope() as session:
        failed = session.scalars(
            select(TraceStep).where(
                TraceStep.trace_id == result['trace_id'],
                TraceStep.status == 'failed',
            )
        ).all()
    assert [row.node for row in failed] == ['agent_loop.tool.get_order']


def test_explicit_human_handoff_terminates_without_tool_calls(seeded_db):
    context = SecurityContext('U1001', 'TENANT-DEMO', 'customer')

    result = run_agent_loop('别查了，我要转人工', context)

    assert result['mode'] == 'agent_loop'
    assert result['stop_reason'] == 'needs_human'
    assert result['handoff'] is True
    assert result['steps'] == 0
    assert result['tool_calls'] == 0


def test_authorization_and_missing_order_errors_are_not_tool_failures(seeded_db):
    customer = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    other_tenant = SecurityContext('U1001', 'TENANT-OTHER', 'customer')

    with pytest.raises(PermissionError):
        run_agent_loop('订单 O10001 物流到哪了，另外电子发票怎么开？', customer)

    with pytest.raises(ValueError, match='ORDER_NOT_FOUND'):
        run_agent_loop('订单 O99999 物流到哪了，另外电子发票怎么开？', other_tenant)


def test_authorization_error_returns_403_via_api(seeded_db):
    client = TestClient(app)

    response = _chat(client, '订单 O10001 物流到哪了，另外电子发票怎么开？', user='U1002')

    assert response.status_code == 403


def test_trace_endpoint_filters_by_trace_id(seeded_db):
    client = TestClient(app)

    body = _chat(client, MULTI_STEP_QUERY).json()
    rows = client.get('/api/v1/traces', params={'trace_id': body['trace_id']}).json()

    assert rows
    assert {row['trace_id'] for row in rows} == {body['trace_id']}
    assert rows[-1]['node'] == 'agent_loop.tool.get_order'


def test_model_guessed_intent_keeps_low_confidence_and_routes_to_the_loop(monkeypatch):
    class GuessingProvider:
        def chat(self, system, user, **kwargs):
            return 'faq'

    monkeypatch.setattr('app.services.router.get_provider', lambda: GuessingProvider())

    route = route_intent('随便聊聊')

    assert route['intent'] == 'faq'
    assert route['confidence'] < settings.agent_loop_confidence_threshold
    assert should_use_agent_loop(route, '随便聊聊') is True


def test_budget_defaults_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, 'agent_loop_max_steps', 7)
    monkeypatch.setattr(settings, 'agent_loop_max_tool_calls', 9)
    monkeypatch.setattr(settings, 'agent_loop_max_tokens', 321)

    budget = AgentBudget.from_settings()

    assert budget.to_dict() == {'max_steps': 7, 'max_tool_calls': 9, 'max_tokens': 321}
