import hashlib
from datetime import datetime

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.db.database import session_scope
from app.db.models import Document, DocumentVersion
from app.documents.storage import read_source
from app.scripts_seed import seed


def test_create_all_creates_document_tables(seeded_db):
    tables = set(inspect(seeded_db).get_table_names())

    assert {'documents', 'document_versions'} <= tables


def test_seed_maps_knowledge_to_three_documents_and_four_versions(seeded_db):
    with session_scope() as session:
        documents = {
            document.id: document
            for document in session.scalars(select(Document)).all()
        }
        versions = {
            version.id: version
            for version in session.scalars(select(DocumentVersion)).all()
        }

    assert set(documents) == {
        'DOC-REFUND-ELECTRONICS',
        'DOC-LOGISTICS-SOP',
        'DOC-INVOICE',
    }
    assert set(versions) == {
        'KB-REFUND-V2',
        'KB-REFUND-V3',
        'KB-LOGISTICS-V1',
        'KB-INVOICE-V1',
    }

    refund = documents['DOC-REFUND-ELECTRONICS']
    assert refund.title == '电子商品售后退款政策'
    assert refund.category == 'refund'
    assert refund.product_type == 'electronics'
    assert refund.visibility == 'tenant'
    assert refund.status == 'active'
    assert refund.tenant_id == 'TENANT-DEMO'
    assert refund.owner_id

    logistics = documents['DOC-LOGISTICS-SOP']
    assert logistics.visibility == 'staff_only'
    assert logistics.category == 'logistics'
    assert logistics.product_type == 'all'

    invoice = documents['DOC-INVOICE']
    assert invoice.visibility == 'tenant'
    assert invoice.category == 'invoice'

    refund_v1 = versions['KB-REFUND-V2']
    assert refund_v1.document_id == 'DOC-REFUND-ELECTRONICS'
    assert refund_v1.version_number == 1
    assert refund_v1.label == '2025-v2'
    assert refund_v1.status == 'superseded'
    assert refund_v1.effective_from == '2025-01-01'
    assert refund_v1.published_at == datetime(2025, 1, 1)

    refund_v2 = versions['KB-REFUND-V3']
    assert refund_v2.document_id == 'DOC-REFUND-ELECTRONICS'
    assert refund_v2.version_number == 2
    assert refund_v2.label == '2026-v3'
    assert refund_v2.status == 'published'
    assert refund_v2.effective_from == '2026-01-01'
    assert refund_v2.published_at == datetime(2026, 1, 1)

    logistics_v1 = versions['KB-LOGISTICS-V1']
    assert logistics_v1.document_id == 'DOC-LOGISTICS-SOP'
    assert logistics_v1.version_number == 1
    assert logistics_v1.label == '2026-v1'
    assert logistics_v1.status == 'published'

    invoice_v1 = versions['KB-INVOICE-V1']
    assert invoice_v1.document_id == 'DOC-INVOICE'
    assert invoice_v1.version_number == 1
    assert invoice_v1.label == '2026-v1'
    assert invoice_v1.status == 'published'

    with session_scope() as session:
        published = session.execute(
            select(DocumentVersion.document_id, func.count())
            .where(DocumentVersion.status == 'published')
            .group_by(DocumentVersion.document_id)
        ).all()

    assert dict(published) == {
        'DOC-REFUND-ELECTRONICS': 1,
        'DOC-LOGISTICS-SOP': 1,
        'DOC-INVOICE': 1,
    }


def test_seed_is_repeatable_for_documents(seeded_db):
    seed()
    seed()

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 3
        assert (
            session.scalar(select(func.count()).select_from(DocumentVersion)) == 4
        )


def test_seed_versions_cache_full_text_embeddings(seeded_db, fake_provider):
    with session_scope() as session:
        versions = session.scalars(select(DocumentVersion)).all()

    assert len(versions) == 4
    for version in versions:
        assert version.embedding
        assert len(version.embedding) == fake_provider.dimensions
        assert version.embedding_dimensions == fake_provider.dimensions
        assert version.embedding_model == fake_provider.embedding_model
        assert version.embedding_generated_at is not None


def test_seed_versions_have_downloadable_source_files(seeded_db):
    with session_scope() as session:
        versions = session.scalars(select(DocumentVersion)).all()

    for version in versions:
        assert version.source_filename == f'{version.id}.txt'
        assert version.storage_key
        assert version.checksum == hashlib.sha256(
            version.content_text.encode('utf-8')
        ).hexdigest()
        assert version.size_bytes == len(version.content_text.encode('utf-8'))
        assert version.mime_type.startswith('text/plain')
        assert read_source(version.storage_key).decode('utf-8') == version.content_text


def test_single_published_version_per_document_is_enforced(seeded_db):
    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(
            DocumentVersion(
                id='KB-REFUND-V4',
                document_id='DOC-REFUND-ELECTRONICS',
                version_number=3,
                label='2027-v1',
                status='published',
                effective_from='2027-01-01',
                content_text='下一版退款政策。',
            )
        )

    with session_scope() as session:
        still_published = session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.document_id == 'DOC-REFUND-ELECTRONICS',
                DocumentVersion.status == 'published',
            )
        ).all()

    assert [version.id for version in still_published] == ['KB-REFUND-V3']
