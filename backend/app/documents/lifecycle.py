"""Publish, replace, rollback, and archive lifecycle for document versions.

The version status is the source of truth for recall eligibility.  Publishing
is the only operation that creates a Published Version; it atomically supersedes
the previous one so the partial unique index never sees two published rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import SecurityContext
from app.db.database import session_scope, utcnow
from app.db.models import Document, DocumentVersion
from app.documents.catalog import load_document, load_version
from app.documents.errors import InvalidState, require_manager
from app.llm.provider import ModelProvider, ProviderError, get_provider
from app.rag.hybrid import (
    cache_version_embedding,
    embedding_is_current,
    parse_effective_from,
)
from app.services.tasking import record_audit

# ``superseded`` is publishable so re-publishing an old version performs a
# rollback.  Everything else must move through ready/archived first.
PUBLISHABLE_VERSION_STATUSES = {'ready', 'superseded'}


class PublishConflict(Exception):
    """Raised when a concurrent publish wins the single-published race (HTTP 409)."""


def publish_version(
    ctx: SecurityContext,
    document_id: str,
    version_id: str,
    *,
    provider: ModelProvider | None = None,
) -> dict:
    """Publish a ready or superseded version and supersede the current one."""
    require_manager(ctx)
    provider = provider or get_provider()
    try:
        with session_scope() as session:
            document = load_document(ctx, session, document_id)
            version = load_version(session, document, version_id)
            if document.status != 'active':
                raise InvalidState(
                    'Cannot publish a version of an archived document; '
                    'restore the document first.'
                )
            if (
                version.status not in PUBLISHABLE_VERSION_STATUSES
                and version.status != 'published'
            ):
                raise InvalidState(
                    f'Only ready or superseded versions can be published; '
                    f'{version.id} is {version.status}.'
                )
            _validate_effective_from(version)
            _ensure_current_embedding(version, document, provider)
            if version.status == 'published':
                return _transition_payload(document, version)

            superseded_ids = _supersede_current(session, document.id, version.id)
            version.status = 'published'
            version.published_at = utcnow()
            version.updated_at = utcnow()
            document.updated_at = utcnow()
            session.flush()  # the partial unique index adjudicates the race

            _record_superseded_audits(session, ctx, document, version, superseded_ids)
            _record_version_audit(
                session,
                ctx,
                document,
                version,
                'version.published',
                superseded_version_ids=superseded_ids,
            )
            return _transition_payload(
                document, version, superseded_version_ids=superseded_ids
            )
    except IntegrityError as exc:
        raise PublishConflict(
            'Another version of this document was published concurrently; retry.'
        ) from exc


def archive_version(
    ctx: SecurityContext, document_id: str, version_id: str
) -> dict:
    """Manually take one Published Version out of recall without deleting it."""
    require_manager(ctx)
    with session_scope() as session:
        document = load_document(ctx, session, document_id)
        version = load_version(session, document, version_id)
        if version.status != 'published':
            raise InvalidState(
                f'Only published versions can be archived; '
                f'{version.id} is {version.status}.'
            )
        version.status = 'archived'
        version.updated_at = utcnow()
        document.updated_at = utcnow()
        session.flush()
        _record_version_audit(session, ctx, document, version, 'version.archived')
        return _transition_payload(document, version)


def restore_version(
    ctx: SecurityContext, document_id: str, version_id: str
) -> dict:
    """Return an archived version to ready so it can be published again."""
    require_manager(ctx)
    with session_scope() as session:
        document = load_document(ctx, session, document_id)
        version = load_version(session, document, version_id)
        if version.status != 'archived':
            raise InvalidState(
                f'Only archived versions can be restored; '
                f'{version.id} is {version.status}.'
            )
        version.status = 'ready'
        version.updated_at = utcnow()
        document.updated_at = utcnow()
        session.flush()
        _record_version_audit(
            session, ctx, document, version, 'version.restored'
        )
        return _transition_payload(document, version)


def archive_document(ctx: SecurityContext, document_id: str) -> dict:
    """Archive a document so every version stops being recall-eligible."""
    require_manager(ctx)
    with session_scope() as session:
        document = load_document(ctx, session, document_id)
        if document.status != 'active':
            raise InvalidState(f'Document {document.id} is already archived.')
        published = _published_version(session, document.id)
        document.status = 'archived'
        document.updated_at = utcnow()
        session.flush()
        record_audit(
            None,
            ctx.user_id,
            'document.archived',
            {
                'tenant_id': ctx.tenant_id,
                'document_id': document.id,
                'title': document.title,
                'published_version_id': published.id if published else None,
            },
            session=session,
        )
        return _document_payload(document, published)


def restore_document(ctx: SecurityContext, document_id: str) -> dict:
    """Restore an archived document; its versions keep their own statuses."""
    require_manager(ctx)
    with session_scope() as session:
        document = load_document(ctx, session, document_id)
        if document.status != 'archived':
            raise InvalidState(f'Document {document.id} is not archived.')
        published = _published_version(session, document.id)
        document.status = 'active'
        document.updated_at = utcnow()
        session.flush()
        record_audit(
            None,
            ctx.user_id,
            'document.restored',
            {
                'tenant_id': ctx.tenant_id,
                'document_id': document.id,
                'title': document.title,
                'published_version_id': published.id if published else None,
            },
            session=session,
        )
        return _document_payload(document, published)


def _validate_effective_from(version: DocumentVersion) -> None:
    if parse_effective_from(version.effective_from) is None:
        raise InvalidState(
            f'Version {version.id} has an invalid effective_from; '
            'set an ISO date or datetime before publishing.'
        )


def _ensure_current_embedding(
    version: DocumentVersion,
    document: Document,
    provider: ModelProvider,
) -> None:
    """Validate the full-text embedding, recomputing it when the model is stale."""
    if not version.content_text or not version.content_text.strip():
        raise InvalidState(
            f'Version {version.id} has no parsed content and cannot be published.'
        )
    if embedding_is_current(version, provider):
        return
    cache_version_embedding(version, document, provider=provider)
    if not embedding_is_current(version, provider):
        raise ProviderError(
            f'Embedding provider did not produce a current vector for {version.id}.'
        )


def _supersede_current(
    session: Session, document_id: str, keep_version_id: str
) -> list[str]:
    """Flush the old superseded rows before publishing the target version.

    Doing the update first keeps the intermediate state valid for databases that
    check unique constraints per statement rather than per transaction.
    """
    current = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.status == 'published',
        )
    ).all()
    superseded_ids = []
    now = utcnow()
    for row in current:
        if row.id == keep_version_id:
            continue
        row.status = 'superseded'
        row.updated_at = now
        superseded_ids.append(row.id)
    if superseded_ids:
        session.flush()
    return superseded_ids


def _record_superseded_audits(
    session: Session,
    ctx: SecurityContext,
    document: Document,
    replacement: DocumentVersion,
    superseded_ids: list[str],
) -> None:
    for version_id in superseded_ids:
        record_audit(
            None,
            ctx.user_id,
            'version.superseded',
            {
                'tenant_id': ctx.tenant_id,
                'document_id': document.id,
                'version_id': version_id,
                'replacement_version_id': replacement.id,
            },
            session=session,
        )


def _record_version_audit(
    session: Session,
    ctx: SecurityContext,
    document: Document,
    version: DocumentVersion,
    action: str,
    *,
    superseded_version_ids: list[str] | None = None,
) -> None:
    detail = {
        'tenant_id': ctx.tenant_id,
        'document_id': document.id,
        'version_id': version.id,
        'version_number': version.version_number,
        'label': version.label,
        'effective_from': version.effective_from,
    }
    if superseded_version_ids is not None:
        detail['superseded_version_ids'] = superseded_version_ids
        detail['published_at'] = _datetime_text(version.published_at)
    record_audit(None, ctx.user_id, action, detail, session=session)


def _published_version(session: Session, document_id: str) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.status == 'published',
        )
    )


def _transition_payload(
    document: Document,
    version: DocumentVersion,
    *,
    superseded_version_ids: list[str] | None = None,
) -> dict:
    return {
        'document_id': document.id,
        'version_id': version.id,
        'version_number': version.version_number,
        'label': version.label,
        'status': version.status,
        'document_status': document.status,
        'effective_from': version.effective_from,
        'published_at': _datetime_text(version.published_at),
        'superseded_version_id': (
            superseded_version_ids[0] if superseded_version_ids else None
        ),
    }


def _document_payload(
    document: Document, published: DocumentVersion | None
) -> dict:
    return {
        'document_id': document.id,
        'status': document.status,
        'published_version_id': published.id if published else None,
    }


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat(sep=' ', timespec='seconds') if value else None
