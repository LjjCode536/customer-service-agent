"""Deterministic, network-free provider.

Golden Dataset evaluation selects it through `use_provider()`. Docker Compose
can also select it explicitly with `MODEL_PROVIDER=offline` so a fresh clone
can start without a real API key. It lives in the Provider layer so Chat /
Embedding / Rerank still have exactly one implementation boundary.
"""

import hashlib

from app.llm.provider import RerankResult


class OfflineProvider:
    name = 'offline'
    chat_model = 'offline-chat-v1'
    embedding_model = 'offline-embedding-v1'
    rerank_model = 'offline-rerank-v1'
    embedding_dimensions = 16

    def __init__(self, dimensions: int = 16):
        self.dimensions = dimensions
        self.embedding_dimensions = dimensions

    def chat(self, system, user, *, temperature=0.2, mock_response=None):
        return (
            mock_response
            if mock_response is not None
            else '离线模式：仅验证业务流程，不比较最终文本措辞。'
        )

    def embed(self, texts):
        vectors = []
        for text in texts:
            block = hashlib.sha256(text.encode('utf-8')).digest()
            # Expand the digest deterministically so any configured dimension is
            # supported without a second hashing scheme.
            vector = []
            while len(vector) < self.dimensions:
                vector.extend(byte / 255 for byte in block)
                block = hashlib.sha256(block).digest()
            vectors.append(vector[: self.dimensions])
        return vectors

    def rerank(self, query, documents, *, top_n=None):
        # Stable order is enough: RRF already ranked candidates before rerank.
        results = [
            RerankResult(index=index, score=1.0 - index / 1000)
            for index in range(len(documents))
        ]
        return results[:top_n] if top_n is not None else results
