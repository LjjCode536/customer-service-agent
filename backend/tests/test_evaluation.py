from fastapi.testclient import TestClient

from app.eval.golden_dataset import GOLDEN_CASES
from app.main import app

GOLDEN_CATEGORIES = {
    'logistics_abnormal',
    'low_risk_refund',
    'high_risk_refund',
    'duplicate_refund',
    'vip_complaint',
    'faq',
}


def _evaluate(client):
    response = client.get('/api/v1/evaluation')
    assert response.status_code == 200
    return response.json()


def test_golden_dataset_covers_five_core_scenarios_plus_faq():
    categories = {case['category'] for case in GOLDEN_CASES}
    assert categories == GOLDEN_CATEGORIES
    for case in GOLDEN_CASES:
        assert case['id']
        assert case['message']
        assert case['user_id']
        assert case['expected']['mode'] in {'direct_tool', 'direct_rag', 'workflow'}
        assert case['expected']['intent']


def test_evaluation_runs_full_chain_and_asserts_business_outcomes(seeded_db):
    body = _evaluate(TestClient(app))

    assert {case['category'] for case in body['cases']} == GOLDEN_CATEGORIES
    assert all(case['passed'] for case in body['cases']), body['cases']

    by_id = {case['id']: case for case in body['cases']}
    fields = {
        check['field']
        for case in body['cases']
        for check in case['checks']
    }
    assert {'mode', 'intent', 'ticket', 'refund_count', 'approval_chain'} <= fields

    assert by_id['logistics_stale']['actual']['ticket']['category'] == 'logistics_abnormal'
    assert by_id['low_risk_refund']['actual']['task_status'] == 'completed'
    assert by_id['low_risk_refund']['actual']['refund_count'] == 1
    assert by_id['high_risk_refund']['actual']['approval_status'] == 'approved'
    assert by_id['high_risk_refund']['actual']['approval_chain'] == [
        'refund_specialist',
        'service_manager',
    ]
    assert by_id['duplicate_refund']['actual']['duplicate_blocked'] is True
    assert by_id['vip_complaint']['actual']['ticket']['priority'] == 'P1'
    assert by_id['faq_invoice']['actual']['mode'] == 'direct_rag'


def test_evaluation_is_isolated_from_live_database(seeded_db):
    client = TestClient(app)
    before_tickets = client.get('/api/v1/tickets').json()
    before_refunds = client.get('/api/v1/refunds').json()
    before_tasks = client.get('/api/v1/tasks/task_missing')

    body = _evaluate(client)
    assert all(case['passed'] for case in body['cases'])

    assert client.get('/api/v1/tickets').json() == before_tickets
    assert client.get('/api/v1/refunds').json() == before_refunds
    assert before_tasks.status_code == 404


def test_evaluation_runs_without_network_or_real_api_key(seeded_db, monkeypatch):
    from app.llm import provider as provider_module

    provider_module.reset_provider()
    monkeypatch.setattr(
        provider_module,
        'create_provider',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError('evaluation must not build the live provider')
        ),
    )
    monkeypatch.setattr(provider_module.settings, 'mock_mode', False)
    monkeypatch.setattr(provider_module.settings, 'model_api_key', '')

    body = _evaluate(TestClient(app))
    assert all(case['passed'] for case in body['cases']), body['cases']


def test_evaluation_detects_a_broken_business_rule(seeded_db, monkeypatch):
    from app.workflow import service_graph

    # A green evaluation must come from the real workflow. Breaking the deterministic
    # risk rule must surface as a failed high-risk case, not stay green because the
    # evaluator only compares final wording.
    monkeypatch.setattr(
        service_graph,
        'refund_risk',
        lambda order, customer, eligibility: {
            'score': 0,
            'risk_level': 'low',
            'reasons': [],
        },
    )

    body = _evaluate(TestClient(app))
    by_id = {case['id']: case for case in body['cases']}
    assert by_id['high_risk_refund']['passed'] is False
    assert any(
        check['field'] == 'approval_chain' and not check['passed']
        for check in by_id['high_risk_refund']['checks']
    )


def test_bad_case_can_be_created_queried_and_backfilled(seeded_db):
    client = TestClient(app)

    created = client.post(
        '/api/v1/bad-cases',
        json={
            'trace_id': 'trace_demo',
            'query': '订单 O10003 帮我直接退款',
            'error_type': 'wrong_approval_chain',
            'root_cause': '风险规则未触发人工审核',
            'expected_result': {'approval_chain': ['refund_specialist']},
        },
    )
    assert created.status_code == 200
    bad_case = created.json()
    assert bad_case['id']
    assert bad_case['trace_id'] == 'trace_demo'
    assert bad_case['fixed'] is False
    assert bad_case['expected_result'] == {'approval_chain': ['refund_specialist']}

    listed = client.get('/api/v1/bad-cases').json()
    assert [row['id'] for row in listed] == [bad_case['id']]

    detail = client.get(f"/api/v1/bad-cases/{bad_case['id']}")
    assert detail.status_code == 200
    assert detail.json()['error_type'] == 'wrong_approval_chain'

    updated = client.patch(
        f"/api/v1/bad-cases/{bad_case['id']}",
        json={
            'root_cause': '已定位：阈值配置错误',
            'expected_result': {'approval_chain': ['refund_specialist', 'service_manager']},
            'fixed': True,
        },
    )
    assert updated.status_code == 200
    patched = updated.json()
    assert patched['fixed'] is True
    assert patched['root_cause'] == '已定位：阈值配置错误'
    assert patched['expected_result']['approval_chain'] == [
        'refund_specialist',
        'service_manager',
    ]
    assert patched['trace_id'] == 'trace_demo'

    other = client.post(
        '/api/v1/bad-cases',
        json={
            'trace_id': 'trace_other',
            'query': '另一个失败样本',
            'error_type': 'wrong_intent',
        },
    ).json()
    filtered = client.get('/api/v1/bad-cases', params={'trace_id': 'trace_demo'}).json()
    assert [row['id'] for row in filtered] == [bad_case['id']]
    assert other['id'] != bad_case['id']


def test_bad_case_detail_returns_404_for_unknown_id(seeded_db):
    client = TestClient(app)
    response = client.get('/api/v1/bad-cases/999999')
    assert response.status_code == 404
