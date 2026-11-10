"""Tenant-scoped read paths for Documents and Document Versions.

This module owns the mapping from the Document / Document Version model back to
the flat shapes the HTTP API and retrieval layer consume.  The legacy
``Knowledge`` table is intentionally not read here; its compatibility payload is
derived from Published Versions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.security import SecurityContext
from app.db.database import use_session
from app.db.models import Document, DocumentVersion
from app.documents.storage import DocumentStorageError, read_source


class DocumentNotFound(Exception):
    """Raised when a document, version, or source file is absent for this tenant."""


def _datetime_text(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=' ', timespec='seconds')
    return value


def version_dict(
    version: DocumentVersion, *, include_content: bool = False
) -> dict[str, Any]:
    """Serialize a version without embedding internals or other cache state."""
    data: dict[str, Any] = {
        'id': version.id,
        'document_id': version.document_id,
        'version_number': version.version_number,
        'label': version.label,
        'status': version.status,
        'effective_from': version.effective_from,
        'source_filename': version.source_filename,
        'checksum': version.checksum,
        'size_bytes': version.size_bytes,
        'mime_type': version.mime_type,
        'error_code': version.error_code,
        'error_message': version.error_message,
        'published_at': _datetime_text(version.published_at),
        'created_at': _datetime_text(version.created_at),
        'updated_at': _datetime_text(version.updated_at),
        'has_source_file': bool(version.storage_key),
    }
    if include_content:
        data['content_text'] = version.content_text
    return data


def _published_version(versions: list[DocumentVersion]) -> DocumentVersion | None:
    return next((version for version in versions if version.status == 'published'), None)


def _document_summary(
    document: Document, versions: list[DocumentVersion]
) -> dict[str, Any]:
    published = _published_version(versions)
    return {
        'id': document.id,
        'tenant_id': document.tenant_id,
        'title': document.title,
        'category': document.category,
        'product_type': document.product_type,
        'visibility': document.visibility,
        'allowed_roles': document.allowed_roles,
        'status': document.status,
        'owner_id': document.owner_id,
        'created_at': _datetime_text(document.created_at),
        'updated_at': _datetime_text(document.updated_at),
        'version_count': len(versions),
        'published_version': version_dict(published) if published else None,
    }


def _matches_keyword(
    keyword: str, document: Document, versions: list[DocumentVersion]
) -> bool:
    needle = keyword.strip().lower()
    if not needle:
        return True
    haystack = [
        document.id,
        document.title,
        document.category,
        document.product_type,
        document.visibility,
        *(version.label for version in versions),
        *(version.id for version in versions),
        *(version.content_text for version in versions),
    ]
    return any(needle in str(value).lower() for value in haystack if value)


def list_documents(
    ctx: SecurityContext,
    *,
    status: str | None = None,
    category: str | None = None,
    product_type: str | None = None,
    visibility: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """List documents in the caller's tenant, applying metadata filters."""
    with use_session() as session:
        documents = list(
            session.scalars(
                select(Document)
                .where(Document.tenant_id == ctx.tenant_id)
                .order_by(Document.category, Document.id)
            )
        )
        versions_by_document: dict[str, list[DocumentVersion]] = {
            document.id: [] for document in documents
        }
        if documents:
            versions = session.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id.in_(
                        [document.id for document in documents]
                    )
                )
                .order_by(DocumentVersion.version_number.desc())
            )
            for version in versions:
                versions_by_document.setdefault(version.document_id, []).append(version)

    rows = []
    for document in documents:
        versions = versions_by_document.get(document.id, [])
        if status and document.status != status:
            continue
        if category and document.category != category:
            continue
        if product_type and document.product_type != product_type:
            continue
        if visibility and document.visibility != visibility:
            continue
        if keyword and not _matches_keyword(keyword, document, versions):
            continue
        rows.append(_document_summary(document, versions))
    return rows


def load_document(ctx: SecurityContext, session, document_id: str) -> Document:
    document = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == ctx.tenant_id,
        )
    )
    if document is None:
        raise DocumentNotFound(f'Document {document_id} not found')
    return document


def get_document(ctx: SecurityContext, document_id: str) -> dict[str, Any]:
    """Return one document plus its full version history."""
    with use_session() as session:
        document = load_document(ctx, session, document_id)
        versions = list(
            session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_number.desc())
            )
        )
    return {
        **_document_summary(document, versions),
        'versions': [version_dict(version) for version in versions],
    }


def load_version(
    session,
    document: Document,
    version_id: str,
) -> DocumentVersion:
    """Load one version after the caller has tenant-scoped the document."""
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document.id,
        )
    )
    if version is None:
        raise DocumentNotFound(f'Document version {version_id} not found')
    return version


def get_version(
    ctx: SecurityContext, document_id: str, version_id: str
) -> dict[str, Any]:
    """Return one version including its parsed text."""
    with use_session() as session:
        document = load_document(ctx, session, document_id)
        version = load_version(session, document, version_id)
        data = version_dict(version, include_content=True)
    data['document_title'] = document.title
    data['category'] = document.category
    data['product_type'] = document.product_type
    data['visibility'] = document.visibility
    return data


def read_version_source(
    ctx: SecurityContext, document_id: str, version_id: str
) -> tuple[DocumentVersion, bytes]:
    """Load the immutable source file for a version after tenant scoping."""
    with use_session() as session:
        document = load_document(ctx, session, document_id)
        version = load_version(session, document, version_id)
        if not version.storage_key:
            raise DocumentNotFound(f'Document version {version_id} has no source file')
        try:
            data = read_source(version.storage_key)
        except (OSError, DocumentStorageError) as exc:
            raise DocumentNotFound(
                f'Source file for document version {version_id} is unavailable'
            ) from exc
    return version, data


def knowledge_rows(ctx: SecurityContext) -> list[dict[str, Any]]:
    """Build the legacy ``GET /knowledge`` read model from Published Versions."""
    with use_session() as session:
        rows = session.execute(
            select(DocumentVersion, Document)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(
                Document.tenant_id == ctx.tenant_id,
                Document.status == 'active',
                DocumentVersion.status == 'published',
            )
            .order_by(
                Document.category,
                DocumentVersion.effective_from.desc(),
                DocumentVersion.id,
            )
        ).all()
    return [
        version_to_evidence(version, document) for version, document in rows
    ]


def version_to_evidence(
    version: DocumentVersion, document: Document
) -> dict[str, Any]:
    """Map a version to the stable Evidence shape used by retrieval and chat."""
    return {
        'id': version.id,
        'category': document.category,
        'product_type': document.product_type,
        'title': document.title,
        'version': version.label,
        'status': version.status,
        'effective_from': version.effective_from,
        'content': version.content_text,
        'keywords': [],
    }
