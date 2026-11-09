import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import SecurityContext
from app.db.database import session_scope
from app.db.models import Document, DocumentVersion
from app.llm.provider import ProviderError, RerankResult, reset_provider, set_provider
from app.main import app
from app.rag.hybrid import cache_document_embeddings, retrieve
from app.scripts_seed import seed

CONCEPTS = {
    'refund': ('退款', '退钱', '退掉', '退货', '售后'),
    'logistics': ('物流', '快递', '运输', '没送到', '没更新', '派送', '签收'),
    'invoice': ('发票', '税号', '开票'),
    'vip': ('vip', '投诉', '专席'),
}

CTX = SecurityContext('U1001', 'TENANT-DEMO', 'customer')


def _concepts(text: str) -> set[str]:
    lowered = (text or '').lower()
    return {
        name
        for name, words in CONCEPTS.items()
        if any(word in lowered for word in words)
    }


class ConceptProvider:
    """Deterministic provider whose vectors encode coarse domain concepts."""

    name = 'concept'
    embedding_model = 'concept-embedding-v1'

    def __init__(self):
        self.embedding_dimensions = len(CONCEPTS)
        self.rerank_calls: list[tuple[str, list[str]]] = []
        self.rerank_top_n: list[int | None] = []

    def chat(self, system, user, *, temperature=0.2, mock_response=None):
        return mock_response if mock_response is not None else 'mock'

    def embed(self, texts):
        return [
            [1.0 if name in _concepts(text) else 0.0 for name in CONCEPTS]
            for text in texts
        ]

    def rerank(self, query, documents, *, top_n=None):
        self.rerank_calls.append((query, list(documents)))
        self.rerank_top_n.append(top_n)
        query_concepts = _concepts(query)
        results = [
            RerankResult(index=index, score=float(len(query_concepts & _concepts(document))))
            for index, document in enumerate(documents)
        ]
        results.sort(key=lambda result: (-result.score, result.index))
        return results[:top_n] if top_n is not None else results


class ZeroVectorProvider(ConceptProvider):
    """Keeps rerank semantics but removes any signal from the vector route."""

    def embed(self, texts):
        return [[0.0] * self.embedding_dimensions for _ in texts]


class FailingEmbedProvider(ConceptProvider):
    def embed(self, texts):
        raise ProviderError('fake embedding service unavailable')


class FailingRerankProvider(ConceptProvider):
    def rerank(self, query, documents, *, top_n=None):
        raise ProviderError('fake rerank service unavailable')


class EmptyRerankProvider(ConceptProvider):
    def rerank(self, query, documents, *, top_n=None):
        return []


class ReverseRerankProvider(ConceptProvider):
    """Scores later candidates higher, so rerank order cannot be a no-op."""

    def rerank(self, query, documents, *, top_n=None):
        self.rerank_calls.append((query, list(documents)))
        self.rerank_top_n.append(top_n)
        results = [
            RerankResult(index=index, score=float(index))
            for index in range(len(documents))
        ]
        results.sort(key=lambda result: result.score, reverse=True)
        return results[:top_n] if top_n is not None else results


class DuplicateRerankProvider(ConceptProvider):
    def rerank(self, query, documents, *, top_n=None):
        return [RerankResult(index=0, score=1.0), RerankResult(index=0, score=0.5)]


class ResizedConceptProvider(ConceptProvider):
    def __init__(self):
        super().__init__()
        self.embedding_dimensions = len(CONCEPTS) + 1

    def embed(self, texts):
        return [vector + [0.0] for vector in super().embed(texts)]


@pytest.fixture()
def concept_provider():
    provider = ConceptProvider()
    set_provider(provider)
    yield provider
    reset_provider()


@pytest.fixture()
def rag_db(concept_provider):
    seed()
    return concept_provider


def _add_document_version(
    version_id: str,
    *,
    document_id: str | None = None,
    category: str = 'general',
    product_type: str = 'all',
    title: str = '占位文档',
    content: str = '无关内容占位文档，不包含业务关键词。',
    status: str = 'published',
    effective_from: str = '2020-01-01',
    label: str = '2026-v1',
    tenant_id: str = 'TENANT-DEMO',
    document_status: str = 'active',
    cache: bool = True,
) -> None:
    document_id = document_id or f'DOC-{version_id}'
    with session_scope() as session:
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                title=title,
                category=category,
                product_type=product_type,
                visibility='tenant',
                status=document_status,
            )
        )
        session.flush()
        session.add(
            DocumentVersion(
                id=version_id,
                document_id=document_id,
                version_number=1,
                label=label,
                status=status,
                effective_from=effective_from,
                content_text=content,
            )
        )
    if cache:
        cache_document_embeddings()


def _add_distractors(count: int = 12):
    for index in range(count):
        _add_document_version(f'KB-DIST-{index:02d}', cache=False)
    cache_document_embeddings()


def test_seed_caches_embeddings_with_model_identifier(rag_db):
    with session_scope() as session:
        rows = session.scalars(select(DocumentVersion)).all()

    assert rows
    for row in rows:
        assert row.embedding
        assert len(row.embedding) == len(CONCEPTS)
        assert row.embedding_model == 'concept-embedding-v1'
        assert row.embedding_generated_at is not None


def test_only_published_versions_effective_now_are_recalled(rag_db):
    _add_document_version(
        'KB-INVOICE-READY',
        category='invoice',
        content='订单完成后可在订单详情申请电子发票，企业抬头需填写税号。',
        status='ready',
        cache=False,
    )
    _add_document_version(
        'KB-INVOICE-FUTURE',
        category='invoice',
        content='订单完成后可在订单详情申请电子发票，企业抬头需填写税号。',
        effective_from='2999-01-01',
        cache=False,
    )

    evidence = retrieve('电子发票怎么开', category='invoice', top_n=5, ctx=CTX)

    assert [item['id'] for item in evidence] == ['KB-INVOICE-V1']


def test_archived_document_versions_are_not_recalled(rag_db):
    _add_document_version(
        'KB-INVOICE-ARCHIVED',
        category='invoice',
        content='订单完成后可在订单详情申请电子发票，企业抬头需填写税号。',
        document_status='archived',
        cache=False,
    )

    evidence = retrieve('电子发票怎么开', category='invoice', top_n=5, ctx=CTX)

    assert [item['id'] for item in evidence] == ['KB-INVOICE-V1']


def test_bm25_route_ranks_exact_keyword_match(rag_db):
    set_provider(ZeroVectorProvider())

    evidence = retrieve('电子发票怎么开', top_n=1, ctx=CTX)

    assert evidence[0]['id'] == 'KB-INVOICE-V1'
    assert evidence[0]['bm25_rank'] == 1
    assert evidence[0]['bm25_score'] > 0


def test_embedding_route_recalls_synonym_without_lexical_overlap(rag_db):
    _add_distractors()

    evidence = retrieve('东西没送到', top_n=1, ctx=CTX)

    assert evidence[0]['id'] == 'KB-LOGISTICS-V1'
    assert evidence[0]['bm25_score'] == 0
    assert evidence[0]['bm25_rank'] is None
    assert evidence[0]['vector_rank'] == 1
    assert evidence[0]['vector_score'] > 0.5


def test_rrf_score_is_the_sum_of_both_route_ranks(rag_db):
    evidence = retrieve('退款 发票', top_n=4, ctx=CTX)

    assert evidence
    for item in evidence:
        expected = 0.0
        if item['bm25_rank'] is not None:
            expected += 1 / (60 + item['bm25_rank'])
        if item['vector_rank'] is not None:
            expected += 1 / (60 + item['vector_rank'])
        assert item['rrf_score'] == pytest.approx(expected)


def test_rrf_prefers_documents_recalled_by_both_routes(rag_db):
    _add_document_version(
        'KB-A-BOTH',
        title='退款 特殊关键词',
        content='退款 特殊关键词',
        cache=False,
    )
    _add_document_version(
        'KB-Z-LEXICAL',
        title='特殊关键词',
        content='特殊关键词',
        cache=False,
    )
    cache_document_embeddings()

    ids = [item['id'] for item in retrieve('退款 特殊关键词', top_n=4, ctx=CTX)]

    assert 'KB-A-BOTH' in ids
    assert 'KB-Z-LEXICAL' in ids
    assert ids.index('KB-A-BOTH') < ids.index('KB-Z-LEXICAL')


def test_rerank_controls_final_order_and_repeats_stably(rag_db):
    baseline = retrieve('退款', top_n=1, ctx=CTX)
    provider = ReverseRerankProvider()
    set_provider(provider)

    first = retrieve('退款', top_n=2, ctx=CTX)
    second = retrieve('退款', top_n=2, ctx=CTX)

    candidates = provider.rerank_calls[-1][1]
    assert baseline[0]['id'] == 'KB-REFUND-V3'
    assert candidates[-1].startswith(first[0]['title'])
    assert first[0]['rerank_score'] == len(candidates) - 1
    assert [item['id'] for item in first] == [item['id'] for item in second]
    scores = [item['rerank_score'] for item in first]
    assert scores == sorted(scores, reverse=True)


def test_duplicate_rerank_indices_are_explicit(rag_db):
    set_provider(DuplicateRerankProvider())

    with pytest.raises(ProviderError, match='duplicate candidate index'):
        retrieve('电子发票怎么开', ctx=CTX)


def test_embedding_provider_failure_is_explicit(rag_db):
    set_provider(FailingEmbedProvider())

    with pytest.raises(ProviderError, match='embedding'):
        retrieve('电子发票怎么开', ctx=CTX)


def test_rerank_provider_failure_is_explicit(rag_db):
    set_provider(FailingRerankProvider())

    with pytest.raises(ProviderError, match='rerank'):
        retrieve('电子发票怎么开', ctx=CTX)


def test_empty_rerank_response_is_explicit(rag_db):
    set_provider(EmptyRerankProvider())

    with pytest.raises(ProviderError, match='no results'):
        retrieve('电子发票怎么开', ctx=CTX)


def test_unrelated_query_returns_no_evidence(rag_db):
    assert retrieve('完全无关的一句话', ctx=CTX) == []


def test_cross_tenant_retrieval_returns_no_evidence(rag_db):
    other = SecurityContext('U1001', 'TENANT-OTHER', 'customer')

    assert retrieve('电子发票怎么开', ctx=other) == []


def test_embedding_cache_recomputes_when_model_changes(rag_db):
    rag_db.embedding_model = 'concept-embedding-v2'

    assert cache_document_embeddings(provider=rag_db) == 4

    with session_scope() as session:
        rows = session.scalars(select(DocumentVersion)).all()
    assert {row.embedding_model for row in rows} == {'concept-embedding-v2'}


def test_embedding_cache_recomputes_when_dimensions_change(rag_db):
    resized = ResizedConceptProvider()

    assert cache_document_embeddings(provider=resized) == 4

    with session_scope() as session:
        rows = session.scalars(select(DocumentVersion)).all()
    assert {len(row.embedding) for row in rows} == {len(CONCEPTS) + 1}

    set_provider(resized)
    assert retrieve('电子发票怎么开', top_n=1, ctx=CTX)[0]['id'] == 'KB-INVOICE-V1'


def test_stale_embedding_model_is_reported_instead_of_mixed(rag_db):
    rag_db.embedding_model = 'concept-embedding-v2'

    with pytest.raises(ProviderError, match='re-run embedding caching'):
        retrieve('电子发票怎么开', ctx=CTX)


def test_missing_embedding_is_reported_instead_of_silently_degraded(rag_db):
    _add_document_version(
        'KB-NO-EMBEDDING',
        category='invoice',
        content='订单完成后可在订单详情申请电子发票，企业抬头需填写税号。',
        cache=False,
    )

    with pytest.raises(ProviderError, match='no cached embedding'):
        retrieve('电子发票怎么开', category='invoice', ctx=CTX)


def test_empty_token_corpus_does_not_crash(rag_db):
    _add_document_version(
        'KB-EMPTY-1',
        category='empty',
        title='',
        content='',
    )

    assert retrieve('随便问一句', category='empty', ctx=CTX) == []


def test_faq_chat_reports_provider_failure_explicitly(rag_db):
    set_provider(FailingEmbedProvider())

    with TestClient(app) as client:
        response = client.post('/api/v1/chat', json={'message': '电子发票怎么开？'})

    assert response.status_code == 503
    assert 'embedding' in response.json()['detail']


def test_faq_chat_returns_direct_rag_with_versioned_evidence(rag_db):
    with TestClient(app) as client:
        response = client.post('/api/v1/chat', json={'message': '电子发票怎么开？'})

    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'direct_rag'
    assert body['intent'] == 'faq'
    assert '电子发票' in body['answer']
    assert body['citations'][0] == {'title': '电子发票开具说明', 'version': '2026-v1'}
    assert all(item['title'] and item['version'] for item in body['citations'])

    evidence = body['evidence'][0]
    assert evidence['id'] == 'KB-INVOICE-V1'
    assert evidence['title'] == '电子发票开具说明'
    assert evidence['version'] == '2026-v1'
    assert evidence['content']
    assert set(evidence) >= {
        'bm25_score',
        'vector_score',
        'rrf_score',
        'rerank_score',
        'bm25_rank',
        'vector_rank',
    }
