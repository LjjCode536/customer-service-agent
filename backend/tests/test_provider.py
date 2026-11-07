import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.core.config import Settings
from app.llm.provider import (
    MOCK_CHAT_REPLY,
    LangChainProvider,
    MockChatProvider,
    OpenAICompatibleProvider,
    ProviderError,
    RerankResult,
    create_provider,
    describe_provider,
    reset_provider,
    set_provider,
)
from app.main import app
from app.services.router import route_intent


class FakeProvider:
    name = 'fake'

    def __init__(self, chat_reply='faq'):
        self.chat_reply = chat_reply

    def chat(self, system, user, *, temperature=0.2, mock_response=None):
        return self.chat_reply

    def embed(self, texts):
        return [[0.5, 0.25] for _ in texts]

    def rerank(self, query, documents, *, top_n=None):
        return [RerankResult(index=i, score=1.0 - i / 10) for i in range(len(documents))]


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_provider()
    yield
    reset_provider()


def test_runtime_exposes_provider_models_and_mock_boundary(
    monkeypatch: pytest.MonkeyPatch,
):
    # The runtime endpoint must not depend on a developer's local .env
    # (which may flip MOCK_MODE); pin the expected default explicitly.
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, 'mock_mode', True)
    with TestClient(app) as client:
        response = client.get('/api/v1/runtime')

    assert response.status_code == 200
    runtime = response.json()
    assert runtime['provider'] == 'siliconflow'
    assert runtime['models'] == {
        'chat': 'Qwen/Qwen3-8B',
        'embedding': 'BAAI/bge-m3',
        'rerank': 'BAAI/bge-reranker-v2-m3',
    }
    assert runtime['mock_boundary'] == {
        'chat': 'mock',
        'embedding': 'live',
        'rerank': 'live',
    }
    assert isinstance(runtime['api_key_configured'], bool)
    assert isinstance(runtime['langgraph_available'], bool)


def test_provider_defaults_and_overrides_come_from_environment(monkeypatch):
    for name in (
        'MODEL_PROVIDER',
        'MODEL_API_KEY',
        'MODEL_BASE_URL',
        'MODEL_CHAT_MODEL',
        'MODEL_EMBEDDING_MODEL',
        'MODEL_EMBEDDING_DIMENSIONS',
        'MODEL_RERANK_MODEL',
        'MODEL_TIMEOUT_SECONDS',
    ):
        monkeypatch.delenv(name, raising=False)

    defaults = Settings(_env_file=None)
    assert defaults.model_provider == 'siliconflow'
    assert defaults.model_base_url == 'https://api.siliconflow.cn/v1'
    assert defaults.model_chat_model == 'Qwen/Qwen3-8B'
    assert defaults.model_embedding_model == 'BAAI/bge-m3'
    assert defaults.model_embedding_dimensions == 1024
    assert defaults.model_rerank_model == 'BAAI/bge-reranker-v2-m3'
    assert defaults.model_timeout_seconds == 60

    monkeypatch.setenv('MODEL_PROVIDER', 'compatible-cloud')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://models.example.test/v1')
    monkeypatch.setenv('MODEL_CHAT_MODEL', 'chat-x')
    monkeypatch.setenv('MODEL_EMBEDDING_MODEL', 'embedding-x')
    monkeypatch.setenv('MODEL_RERANK_MODEL', 'rerank-x')
    monkeypatch.setenv('MODEL_TIMEOUT_SECONDS', '15')

    overridden = Settings(_env_file=None)
    assert overridden.model_provider == 'compatible-cloud'
    assert overridden.model_base_url == 'https://models.example.test/v1'
    assert overridden.model_chat_model == 'chat-x'
    assert overridden.model_embedding_model == 'embedding-x'
    assert overridden.model_rerank_model == 'rerank-x'
    assert overridden.model_timeout_seconds == 15


def test_mock_mode_only_mocks_chat_not_embeddings_or_rerank():
    config = Settings(_env_file=None, mock_mode=True, model_api_key='')
    provider = create_provider(config)

    assert provider.chat('system', 'user') == MOCK_CHAT_REPLY
    assert provider.chat('system', 'user', mock_response='业务结果') == '业务结果'
    with pytest.raises(ProviderError, match='MODEL_API_KEY'):
        provider.embed(['hello'])
    with pytest.raises(ProviderError, match='MODEL_API_KEY'):
        provider.rerank('hello', ['world'])


def test_mock_chat_provider_passes_embedding_and_rerank_to_live_provider():
    live = FakeProvider()
    provider = MockChatProvider(live)

    assert provider.embed(['a', 'b']) == [[0.5, 0.25], [0.5, 0.25]]
    assert provider.rerank('q', ['doc-a', 'doc-b']) == live.rerank('q', ['doc-a', 'doc-b'])


def test_offline_provider_is_explicit_and_never_needs_a_key():
    config = Settings(
        _env_file=None,
        model_provider='offline',
        model_api_key='',
        model_embedding_dimensions=48,
    )
    provider = create_provider(config)

    assert provider.name == 'offline'
    assert provider.embedding_model == 'offline-embedding-v1'
    first = provider.embed(['hello'])
    assert len(first[0]) == 48
    assert provider.embed(['hello']) == first
    assert provider.rerank('hello', ['a', 'b'])[0].index == 0

    runtime = describe_provider(config)
    assert runtime['provider'] == 'offline'
    assert runtime['mock_boundary'] == {
        'chat': 'offline',
        'embedding': 'offline',
        'rerank': 'offline',
    }
    assert runtime['api_key_configured'] is False
    assert runtime['models']['embedding'] == 'offline-embedding-v1'


def _fake_rerank_handler(request):
    assert request.url.path.endswith('/rerank')
    return httpx.Response(
        200,
        json={
            'results': [
                {'index': 0, 'relevance_score': 0.2},
                {'index': 1, 'relevance_score': 0.9},
            ]
        },
    )


def test_create_provider_uses_langchain_access_layer():
    live_config = Settings(_env_file=None, mock_mode=False)
    provider = create_provider(live_config)
    assert isinstance(provider, LangChainProvider)
    assert provider.chat_model == 'Qwen/Qwen3-8B'
    assert provider.embedding_model == 'BAAI/bge-m3'
    assert provider.embedding_dimensions == 1024
    assert provider.rerank_model == 'BAAI/bge-reranker-v2-m3'

    mock_config = Settings(_env_file=None, mock_mode=True)
    wrapped = create_provider(mock_config)
    from app.llm.provider import MockChatProvider as MockLayer
    assert isinstance(wrapped, MockLayer)
    assert isinstance(wrapped._delegate, LangChainProvider)


def test_langchain_provider_speaks_chat_embedding_and_rerank():
    provider = LangChainProvider(
        name='siliconflow',
        api_key='test-key',
        base_url='https://example.test/v1',
        chat_model='chat-model',
        embedding_model='embedding-model',
        embedding_dimensions=3,
        rerank_model='rerank-model',
        timeout_seconds=5,
        chat_model_instance=FakeListChatModel(responses=['hello']),
        embedding_instance=DeterministicFakeEmbedding(size=3),
        transport=httpx.MockTransport(_fake_rerank_handler),
    )

    assert provider.chat('系统', '用户') == 'hello'
    assert provider.chat('系统', '用户') == 'hello'

    vectors = provider.embed(['a', 'b'])
    assert len(vectors) == 2
    assert all(len(vector) == 3 for vector in vectors)

    assert [(result.index, result.score) for result in provider.rerank('q', ['d0', 'd1'])] == [
        (1, 0.9),
        (0, 0.2),
    ]
    assert provider.rerank('q', []) == []
    assert provider.embed([]) == []


def test_langchain_provider_validates_embedding_dimensions():
    provider = LangChainProvider(
        name='siliconflow',
        api_key='test-key',
        base_url='https://example.test/v1',
        chat_model='chat-model',
        embedding_model='embedding-model',
        embedding_dimensions=2,
        rerank_model='rerank-model',
        timeout_seconds=5,
        embedding_instance=DeterministicFakeEmbedding(size=3),
    )

    with pytest.raises(ProviderError, match='MODEL_EMBEDDING_DIMENSIONS=2'):
        provider.embed(['hello'])


def test_langchain_provider_requires_api_key():
    provider = LangChainProvider(
        name='siliconflow',
        api_key='',
        base_url='https://example.test/v1',
        chat_model='chat-model',
        embedding_model='embedding-model',
        embedding_dimensions=2,
        rerank_model='rerank-model',
        timeout_seconds=5,
        chat_model_instance=FakeListChatModel(responses=['hello']),
        embedding_instance=DeterministicFakeEmbedding(size=2),
        transport=httpx.MockTransport(_fake_rerank_handler),
    )

    with pytest.raises(ProviderError, match='MODEL_API_KEY'):
        provider.chat('system', 'user')
    with pytest.raises(ProviderError, match='MODEL_API_KEY'):
        provider.embed(['hello'])
    with pytest.raises(ProviderError, match='MODEL_API_KEY'):
        provider.rerank('hello', ['world'])


def test_openai_compatible_provider_speaks_chat_embedding_and_rerank():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path == '/v1/chat/completions':
            assert body['model'] == 'chat-model'
            return httpx.Response(200, json={'choices': [{'message': {'content': 'hello'}}]})
        if request.url.path == '/v1/embeddings':
            assert body['model'] == 'embedding-model'
            return httpx.Response(
                200,
                json={
                    'data': [
                        {'index': 1, 'embedding': [0.3, 0.4]},
                        {'index': 0, 'embedding': [0.1, 0.2]},
                    ]
                },
            )
        if request.url.path == '/v1/rerank':
            assert body['model'] == 'rerank-model'
            return httpx.Response(
                200,
                json={
                    'results': [
                        {'index': 0, 'relevance_score': 0.2},
                        {'index': 1, 'relevance_score': 0.9},
                    ]
                },
            )
        return httpx.Response(404)

    provider = OpenAICompatibleProvider(
        name='siliconflow',
        api_key='test-key',
        base_url='https://example.test/v1',
        chat_model='chat-model',
        embedding_model='embedding-model',
        embedding_dimensions=2,
        rerank_model='rerank-model',
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    assert provider.chat('system', 'user') == 'hello'
    assert provider.embed(['a', 'b']) == [[0.1, 0.2], [0.3, 0.4]]
    assert [(result.index, result.score) for result in provider.rerank('q', ['d0', 'd1'])] == [
        (1, 0.9),
        (0, 0.2),
    ]
    assert seen == ['/v1/chat/completions', '/v1/embeddings', '/v1/rerank']


def test_router_uses_injected_fake_provider():
    set_provider(FakeProvider(chat_reply='faq'))

    assert route_intent('随便聊聊')['intent'] == 'faq'
