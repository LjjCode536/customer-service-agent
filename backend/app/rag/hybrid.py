from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Any

import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import select

from app.core.config import settings
from app.core.security import SecurityContext
from app.db.database import use_session, utcnow
from app.db.models import Document, DocumentVersion
from app.documents.catalog import version_to_evidence
from app.llm.provider import ModelProvider, ProviderError, get_provider

RRF_K = 60
CANDIDATE_LIMIT = 10
_TOKEN_RE = re.compile(r'[\w\u4e00-\u9fff]+')


def tokenize(text: str | None) -> list[str]:
    """Chinese-aware tokenization shared by BM25 indexing and querying."""
    return [
        token
        for token in jieba.lcut((text or '').lower())
        if _TOKEN_RE.fullmatch(token)
    ]


def knowledge_text(row: dict[str, Any]) -> str:
    title = row.get('title') or ''
    content = row.get('content') or ''
    keywords = row.get('keywords') or []
    return ' '.join(
        part for part in (title, content, ' '.join(keywords)) if part
    )


def _cache_embeddings(
    rows: Sequence[Any],
    provider: ModelProvider,
    text_for,
    *,
    action: str,
    subject: str,
) -> int:
    model = _embedding_model(provider)
    dimensions = getattr(provider, 'embedding_dimensions', None)
    pending = [
        row
        for row in rows
        if not row.embedding
        or row.embedding_model != model
        or (dimensions and len(row.embedding) != dimensions)
    ]
    if not pending:
        return 0
    vectors = _call_provider(
        action,
        lambda: provider.embed([text_for(row) for row in pending]),
    )
    if len(vectors) != len(pending):
        raise ProviderError(
            f'Embedding provider returned {len(vectors)} vectors for '
            f'{len(pending)} {subject}.'
        )
    generated_at = utcnow()
    for row, vector in zip(pending, vectors):
        if not vector:
            raise ProviderError(
                f'Embedding provider returned an empty vector for {subject} {row.id}.'
            )
        row.embedding = [float(value) for value in vector]
        row.embedding_model = model
        row.embedding_generated_at = generated_at
        # Document versions record the dimension explicitly for publish validation.
        if hasattr(row, 'embedding_dimensions'):
            row.embedding_dimensions = len(vector)
    return len(pending)


def document_version_text(
    version: DocumentVersion, document: Document | None
) -> str:
    return ' '.join(
        part
        for part in (
            document.title if document else '',
            version.label or '',
            version.content_text or '',
        )
        if part
    )


def cache_document_embeddings(
    session=None, provider: ModelProvider | None = None
) -> int:
    """Compute and persist full-text embeddings for document versions."""
    provider = provider or get_provider()
    with use_session(session) as active_session:
        rows = active_session.execute(
            select(DocumentVersion, Document).join(
                Document, DocumentVersion.document_id == Document.id
            )
        ).all()
        documents = {document.id: document for _version, document in rows}
        versions = [version for version, _document in rows]
        cached = _cache_embeddings(
            versions,
            provider,
            lambda version: document_version_text(
                version, documents[version.document_id]
            ),
            action='Embedding provider failed while caching document versions',
            subject='document versions',
        )
        if cached:
            active_session.flush()
        return cached


def cache_version_embedding(
    version: DocumentVersion,
    document: Document | None,
    *,
    provider: ModelProvider | None = None,
) -> bool:
    """Compute and cache the full-text embedding for one document version.

    The ingestion worker calls this after parsing, so an upload never has to
    embed the whole catalogue or hold a request-scoped transaction.
    """
    provider = provider or get_provider()
    return bool(
        _cache_embeddings(
            [version],
            provider,
            lambda candidate: document_version_text(candidate, document),
            action='Embedding provider failed while ingesting a document version',
            subject='document version',
        )
    )


def retrieve(
    query: str,
    category: str | None = None,
    product_type: str | None = None,
    top_n: int = 5,
    *,
    ctx: SecurityContext,
    provider: ModelProvider | None = None,
) -> list[dict[str, Any]]:
    """Run BM25 + embedding recall, RRF fusion and Cross-Encoder rerank.

    Only recall-eligible Published Versions are searched: the document must be
    active in the caller's tenant and the version must be effective now.
    """
    provider = provider or get_provider()
    now = utcnow()
    docs = _load_active_docs(ctx, category, product_type, now)
    if not docs:
        return []

    query_vector = _embed_query(provider, query)
    _score_bm25(query, docs)
    _score_vectors(query_vector, docs, _embedding_model(provider))

    candidate_limit = max(CANDIDATE_LIMIT, (top_n or 1) * 2)
    bm25_candidates = sorted(
        [doc for doc in docs if doc['bm25_score'] != 0.0],
        key=lambda doc: (-doc['bm25_score'], doc['id']),
    )[:candidate_limit]
    embedded_docs = [doc for doc in docs if doc.get('embedding')]
    vector_candidates = sorted(
        [doc for doc in embedded_docs if doc['vector_score'] > 0.0],
        key=lambda doc: (-doc['vector_score'], doc['id']),
    )[:candidate_limit]
    if not bm25_candidates and not vector_candidates:
        return []

    bm25_ranks = {
        doc['id']: rank for rank, doc in enumerate(bm25_candidates, start=1)
    }
    vector_ranks = {
        doc['id']: rank for rank, doc in enumerate(vector_candidates, start=1)
    }
    fused: list[dict[str, Any]] = []
    for doc in docs:
        rrf_score = 0.0
        if doc['id'] in bm25_ranks:
            rrf_score += 1 / (RRF_K + bm25_ranks[doc['id']])
        if doc['id'] in vector_ranks:
            rrf_score += 1 / (RRF_K + vector_ranks[doc['id']])
        if rrf_score == 0.0:
            continue
        doc['bm25_rank'] = bm25_ranks.get(doc['id'])
        doc['vector_rank'] = vector_ranks.get(doc['id'])
        doc['rrf_score'] = round(rrf_score, 8)
        fused.append(doc)
    fused.sort(key=lambda doc: (-doc['rrf_score'], doc['id']))

    reranked = _rerank(provider, query, fused, top_n)
    evidence = []
    for result in reranked:
        doc = dict(fused[result.index])
        doc['rerank_score'] = round(float(result.score), 8)
        for internal_field in ('embedding', 'embedding_model', 'embedding_generated_at'):
            doc.pop(internal_field, None)
        evidence.append(doc)
    evidence.sort(
        key=lambda doc: (-doc['rerank_score'], -doc['rrf_score'], doc['id'])
    )
    return evidence


def _load_active_docs(
    ctx: SecurityContext,
    category: str | None,
    product_type: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    with use_session() as session:
        rows = session.execute(
            select(DocumentVersion, Document)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(
                Document.tenant_id == ctx.tenant_id,
                Document.status == 'active',
                DocumentVersion.status == 'published',
            )
        ).all()
    docs = []
    for version, document in rows:
        effective_from = _parse_effective_from(version.effective_from)
        if effective_from is None or effective_from > now:
            continue
        if category and document.category != category:
            continue
        if product_type and document.product_type not in {product_type, 'all'}:
            continue
        doc = version_to_evidence(version, document)
        # Scoring internals stay on the working copy and are stripped before Evidence.
        doc['embedding'] = version.embedding
        doc['embedding_model'] = version.embedding_model
        doc['embedding_generated_at'] = version.embedding_generated_at
        docs.append(doc)
    return docs


def _parse_effective_from(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        # Python 3.11+ fromisoformat parses a trailing 'Z' directly.
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _embedding_model(provider: ModelProvider) -> str:
    return getattr(provider, 'embedding_model', None) or settings.model_embedding_model


def _call_provider(action: str, call):
    try:
        return call()
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f'{action}: {exc}') from exc


def _embed_query(provider: ModelProvider, query: str) -> list[float]:
    vectors = _call_provider(
        'Embedding provider failed for query',
        lambda: provider.embed([query]),
    )
    if len(vectors) != 1 or not vectors[0]:
        raise ProviderError('Embedding provider returned no query vector.')
    return [float(value) for value in vectors[0]]


def _score_bm25(query: str, docs: Sequence[dict[str, Any]]) -> None:
    corpus = [tokenize(knowledge_text(doc)) for doc in docs]
    if not any(corpus):
        for doc in docs:
            doc['bm25_score'] = 0.0
        return
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(tokenize(query))
    for doc, score in zip(docs, scores):
        doc['bm25_score'] = round(float(score), 8)


def _score_vectors(
    query_vector: Sequence[float],
    docs: Sequence[dict[str, Any]],
    embedding_model: str,
) -> None:
    for doc in docs:
        embedding = doc.get('embedding')
        if not embedding:
            raise ProviderError(
                f"Document version {doc['id']} has no cached embedding; "
                're-run embedding caching before retrieval.'
            )
        if doc.get('embedding_model') != embedding_model:
            raise ProviderError(
                f"Document version {doc['id']} was embedded with {doc.get('embedding_model')!r}, "
                f'not {embedding_model!r}; re-run embedding caching with the current model.'
            )
        if len(embedding) != len(query_vector):
            raise ProviderError(
                f"Document version {doc['id']} embedding dimension {len(embedding)} "
                f'does not match query dimension {len(query_vector)}; '
                're-run embedding caching with the current model.'
            )
        doc['vector_score'] = round(_cosine(query_vector, embedding), 8)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(a, b) / denominator)


def _rerank(
    provider: ModelProvider,
    query: str,
    candidates: Sequence[dict[str, Any]],
    top_n: int,
):
    results = _call_provider(
        'Rerank provider failed',
        lambda: provider.rerank(
            query,
            [knowledge_text(doc) for doc in candidates],
            top_n=top_n,
        ),
    )
    if not results:
        raise ProviderError('Rerank provider returned no results for non-empty candidates.')
    seen: set[int] = set()
    for result in results:
        if result.index < 0 or result.index >= len(candidates):
            raise ProviderError(
                f'Rerank provider returned out-of-range candidate index {result.index}.'
            )
        if result.index in seen:
            raise ProviderError(
                f'Rerank provider returned duplicate candidate index {result.index}.'
            )
        seen.add(result.index)
    return results
