from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, Sequence

import httpx

from app.core.config import Settings, settings

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


class OpenAICompatibleProvider:
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


def describe_provider(config: Settings | None = None) -> dict:
    cfg = config or settings
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
    live = OpenAICompatibleProvider(
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


def get_provider() -> ModelProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = create_provider()
        return _provider


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
