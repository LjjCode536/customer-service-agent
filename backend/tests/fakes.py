import hashlib

from app.llm.provider import RerankResult


class DeterministicProvider:
    """Offline provider for tests: deterministic vectors and stable rerank order."""

    name = 'fake'
    embedding_model = 'fake-embedding-v1'

    def __init__(self, dimensions: int = 8):
        self.dimensions = dimensions
        self.embedding_dimensions = dimensions
        self.rerank_calls: list[tuple[str, list[str]]] = []

    def chat(self, system, user, *, temperature=0.2, mock_response=None):
        return mock_response if mock_response is not None else 'mock'

    def embed(self, texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode('utf-8')).digest()
            vectors.append([byte / 255 for byte in digest[: self.dimensions]])
        return vectors

    def rerank(self, query, documents, *, top_n=None):
        self.rerank_calls.append((query, list(documents)))
        results = [RerankResult(index=index, score=1.0 - index / 1000) for index in range(len(documents))]
        return results[:top_n] if top_n is not None else results
