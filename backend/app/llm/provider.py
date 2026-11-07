from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import Settings, settings
from app.core.context import scoped

MOCK_CHAT_REPLY = 'Mock 模式：仅模拟文本生成，真实检索与业务结果仍由系统提供。'


class ProviderError(RuntimeError):
    """Raised when the configured model provider cannot serve a request."""


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class ModelProvider(Protocol):
    name: str
    embedding_model: str
    embedding_dimensions: int

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        mock_response: str | None = None,
    ) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]: ...


class LangChainProvider:
    """LangChain 接入层：Chat 与 Embedding 走 langchain-openai，Rerank 走 /rerank。

    硅基流动是 OpenAI 兼容端点，Chat 用 `ChatOpenAI`，Embedding 用
    `OpenAIEmbeddings`（关闭 tiktoken 上下文切分，避免对非 OpenAI 模型做错误的
    客户端分词）；`/v1/rerank` 不是 OpenAI 标准端点，LangChain 没有对应实现，故
    保留最小 httpx 调用。测试通过注入 langchain-core 的 fake 模型实例，不访问网络。
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dimensions: int = 1024,
        rerank_model: str,
        timeout_seconds: int,
        transport: httpx.BaseTransport | None = None,
        chat_model_instance=None,
        embedding_instance=None,
    ):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self.rerank_model = rerank_model
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)
        if chat_model_instance is not None:
            self._chat = chat_model_instance
        else:
            # openai>=2.x 会在构造 client 时立刻校验 api_key；这里用占位值绕过，
            # 真正缺失 key 时的清晰报错由 _require_api_key() 在发起调用前给出。
            placeholder_key = api_key or 'unconfigured'
            self._chat = ChatOpenAI(
                model=chat_model,
                api_key=placeholder_key,
                base_url=base_url,
                timeout=timeout_seconds,
            )
        if embedding_instance is not None:
            self._embeddings = embedding_instance
        else:
            self._embeddings = OpenAIEmbeddings(
                model=embedding_model,
                api_key=api_key or 'unconfigured',
                base_url=base_url,
                timeout=timeout_seconds,
                check_embedding_ctx_length=False,
            )

    def close(self) -> None:
        self._client.close()

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise ProviderError(
                'MODEL_API_KEY 未配置：真实 Provider 的 Chat / Embedding / Rerank 需要 API key。'
            )

    def _post(self, path: str, payload: dict) -> dict:
        try:
            response = self._client.post(
                f'{self.base_url}{path}',
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f'{self.name} 调用 {path} 失败：{exc}') from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f'{self.name} 调用 {path} 返回了非 JSON 响应。') from exc

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        mock_response: str | None = None,
    ) -> str:
        self._require_api_key()
        try:
            result = self._chat.bind(temperature=temperature).invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except Exception as exc:
            raise ProviderError(f'{self.name} 的 Chat 调用失败：{exc}') from exc
        content = getattr(result, 'content', result)
        return str(content)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._require_api_key()
        if not texts:
            return []
        try:
            vectors = [list(vector) for vector in self._embeddings.embed_documents(list(texts))]
        except Exception as exc:
            raise ProviderError(f'{self.name} 的 Embedding 调用失败：{exc}') from exc
        for vector in vectors:
            if len(vector) != self.embedding_dimensions:
                raise ProviderError(
                    f'{self.name} 的 Embedding 维度为 {len(vector)}，与配置的 '
                    f'MODEL_EMBEDDING_DIMENSIONS={self.embedding_dimensions} 不一致。'
                )
        return vectors

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]:
        self._require_api_key()
        if not documents:
            return []
        payload = {
            'model': self.rerank_model,
            'query': query,
            'documents': list(documents),
        }
        if top_n is not None:
            payload['top_n'] = top_n
        data = self._post('/rerank', payload)
        try:
            results = [
                RerankResult(
                    index=int(row['index']),
                    score=float(row['relevance_score'] if 'relevance_score' in row else row['score']),
                )
                for row in data['results']
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f'{self.name} 的 Rerank 响应缺少 results[].relevance_score。') from exc
        results.sort(key=lambda result: result.score, reverse=True)
        return results[:top_n] if top_n is not None else results


class OpenAICompatibleProvider:
    # [已弃用] 旧的手写 OpenAI 兼容实现，已被上面的 LangChainProvider（langchain-openai
    # 接入）替代；create_provider() 不再引用它。按约定保留不删，回滚时把
    # create_provider() 中的 LangChainProvider(...) 换回本类即可，测试
    # test_openai_compatible_provider_speaks_chat_embedding_and_rerank 仍验证其行为。
    """OpenAI 兼容 Provider：Chat / Embedding 走标准端点，Rerank 走 /rerank。"""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dimensions: int = 1024,
        rerank_model: str,
        timeout_seconds: int,
        transport: httpx.BaseTransport | None = None,
    ):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self.rerank_model = rerank_model
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: dict) -> dict:
        if not self.api_key:
            raise ProviderError(
                'MODEL_API_KEY 未配置：真实 Provider 的 Chat / Embedding / Rerank 需要 API key。'
            )
        try:
            response = self._client.post(
                f'{self.base_url}{path}',
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f'{self.name} 调用 {path} 失败：{exc}') from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f'{self.name} 调用 {path} 返回了非 JSON 响应。') from exc

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        mock_response: str | None = None,
    ) -> str:
        data = self._post(
            '/chat/completions',
            {
                'model': self.chat_model,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
                'temperature': temperature,
            },
        )
        try:
            return data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f'{self.name} 的 Chat 响应缺少 choices[0].message.content。') from exc

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        data = self._post(
            '/embeddings',
            {'model': self.embedding_model, 'input': list(texts)},
        )
        try:
            rows = sorted(data['data'], key=lambda row: row['index'])
            vectors = [list(row['embedding']) for row in rows]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f'{self.name} 的 Embedding 响应缺少 data[].embedding。') from exc
        for vector in vectors:
            if len(vector) != self.embedding_dimensions:
                raise ProviderError(
                    f'{self.name} 的 Embedding 维度为 {len(vector)}，与配置的 '
                    f'MODEL_EMBEDDING_DIMENSIONS={self.embedding_dimensions} 不一致。'
                )
        return vectors

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]:
        if not documents:
            return []
        payload = {
            'model': self.rerank_model,
            'query': query,
            'documents': list(documents),
        }
        if top_n is not None:
            payload['top_n'] = top_n
        data = self._post('/rerank', payload)
        try:
            results = [
                RerankResult(
                    index=int(row['index']),
                    score=float(row['relevance_score'] if 'relevance_score' in row else row['score']),
                )
                for row in data['results']
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f'{self.name} 的 Rerank 响应缺少 results[].relevance_score。') from exc
        results.sort(key=lambda result: result.score, reverse=True)
        return results[:top_n] if top_n is not None else results


class MockChatProvider:
    """Mock Mode 边界：只替换 Chat 输出，Embedding 与 Rerank 仍委托真实 Provider。"""

    def __init__(self, delegate: ModelProvider):
        self.name = delegate.name
        self._delegate = delegate

    @property
    def embedding_model(self) -> str:
        return getattr(self._delegate, 'embedding_model', settings.model_embedding_model)

    @property
    def embedding_dimensions(self) -> int:
        return getattr(
            self._delegate,
            'embedding_dimensions',
            settings.model_embedding_dimensions,
        )

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        mock_response: str | None = None,
    ) -> str:
        return mock_response if mock_response is not None else MOCK_CHAT_REPLY

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._delegate.embed(texts)

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]:
        return self._delegate.rerank(query, documents, top_n=top_n)

    def close(self) -> None:
        _close_provider(self._delegate)


def _offline_requested(config: Settings) -> bool:
    return config.model_provider.strip().lower() == 'offline'


def describe_provider(config: Settings | None = None) -> dict:
    cfg = config or settings
    if _offline_requested(cfg):
        from app.llm.offline import OfflineProvider

        return {
            'provider': 'offline',
            'base_url': '',
            'models': {
                'chat': OfflineProvider.chat_model,
                'embedding': OfflineProvider.embedding_model,
                'rerank': OfflineProvider.rerank_model,
            },
            'embedding_dimensions': cfg.model_embedding_dimensions,
            'mock_boundary': {
                'chat': 'offline',
                'embedding': 'offline',
                'rerank': 'offline',
            },
            'api_key_configured': False,
        }
    chat_mode = 'mock' if cfg.mock_mode else 'live'
    return {
        'provider': cfg.model_provider,
        'base_url': cfg.model_base_url,
        'models': {
            'chat': cfg.model_chat_model,
            'embedding': cfg.model_embedding_model,
            'rerank': cfg.model_rerank_model,
        },
        'embedding_dimensions': cfg.model_embedding_dimensions,
        'mock_boundary': {'chat': chat_mode, 'embedding': 'live', 'rerank': 'live'},
        'api_key_configured': bool(cfg.model_api_key),
    }


def create_provider(config: Settings | None = None) -> ModelProvider:
    cfg = config or settings
    if _offline_requested(cfg):
        from app.llm.offline import OfflineProvider

        return OfflineProvider(dimensions=cfg.model_embedding_dimensions)
    live = LangChainProvider(
        name=cfg.model_provider,
        api_key=cfg.model_api_key,
        base_url=cfg.model_base_url,
        chat_model=cfg.model_chat_model,
        embedding_model=cfg.model_embedding_model,
        embedding_dimensions=cfg.model_embedding_dimensions,
        rerank_model=cfg.model_rerank_model,
        timeout_seconds=cfg.model_timeout_seconds,
    )
    return MockChatProvider(live) if cfg.mock_mode else live


_provider: ModelProvider | None = None
_provider_lock = Lock()
# Evaluation runs an offline provider without touching the process-wide live provider.
_provider_override: ContextVar[ModelProvider | None] = ContextVar(
    'provider_override', default=None
)


def get_provider() -> ModelProvider:
    override = _provider_override.get()
    if override is not None:
        return override
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = create_provider()
        return _provider


@contextmanager
def use_provider(provider: ModelProvider) -> Iterator[ModelProvider]:
    """Scope a provider to the current execution context (thread/async task)."""
    with scoped(_provider_override, provider):
        yield provider


def set_provider(provider: ModelProvider) -> None:
    global _provider
    with _provider_lock:
        if _provider is not None and _provider is not provider:
            _close_provider(_provider)
        _provider = provider


def reset_provider() -> None:
    global _provider
    with _provider_lock:
        if _provider is not None:
            _close_provider(_provider)
        _provider = None


def _close_provider(provider: ModelProvider) -> None:
    close = getattr(provider, 'close', None)
    if callable(close):
        close()
