"""Deterministic, network-free provider used by Golden Dataset evaluation.

It lives in the Provider layer so Chat / Embedding / Rerank still have exactly
one implementation boundary. Evaluation selects it through `use_provider()`.
"""

import hashlib

from app.llm.provider import RerankResult


class OfflineProvider:
    name = 'offline'
    embedding_model = 'offline-embedding-v1'
    embedding_dimensions = 16

    def __init__(self, dimensions: int = 16):
        self.dimensions = dimensions
        self.embedding_dimensions = dimensions

    def chat(self, system, user, *, temperature=0.2, mock_response=None):
        return (
            mock_response
            if mock_response is not None
            else '离线评测模式：仅验证业务流程，不比较最终文本措辞。'
        )

    def embed(self, texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode('utf-8')).digest()
            vectors.append([byte / 255 for byte in digest[: self.dimensions]])
        return vectors

    def rerank(self, query, documents, *, top_n=None):
        # Stable order is enough: RRF already ranked candidates before rerank.
        results = [
            RerankResult(index=index, score=1.0 - index / 1000)
            for index in range(len(documents))
        ]
        return results[:top_n] if top_n is not None else results
