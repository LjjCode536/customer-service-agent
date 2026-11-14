"""Write-side document management: upload, new version, and retry.

All writes are tenant-scoped and restricted to the ``manager`` role.  Files are
stored content-addressed before the database row is committed; filenames remain
display metadata only.  Uploads create ``draft`` versions and leave parsing to
the asynchronous ingestion worker.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import USER_ROLES, VALID_VISIBILITIES, SecurityContext
from app.db.database import session_scope, utcnow
from app.db.models import Document, DocumentVersion
from app.documents.catalog import DocumentNotFound, load_document
from app.documents.errors import InvalidState, require_manager
from app.documents.parsing import mime_type_for, validate_upload
from app.documents.storage import StoredSource, store_bytes
from app.services.tasking import record_audit

MAX_TITLE_LENGTH = 255
MAX_METADATA_LENGTH = 64


class InvalidMetadata(ValueError):
    """Raised when upload metadata fails validation (HTTP 422)."""


class DuplicateDocument(Exception):
    """Raised when the same tenant already stores bytes with this checksum."""

    def __init__(self, version: DocumentVersion, document: Document):
        self.existing_document_id = document.id
        self.existing_version_id = version.id
        self.checksum = version.checksum
        self.title = document.title
        super().__init__(
            f'Document content already exists in this tenant '
            f'(document {document.id}, version {version.id}).'
        )


def create_document(
    ctx: SecurityContext,
    *,
    filename: str | None,
    data: bytes,
    title: str,
    category: str | None = None,
    product_type: str | None = None,
    visibility: str = 'tenant',
    allowed_roles: list[str] | None = None,
    effective_from: str | None = None,
    label: str | None = None,
    force: bool = False,
) -> dict:
    require_manager(ctx)
    display_name = _display_filename(filename)
    title = _required_text(title, 'title', MAX_TITLE_LENGTH)
    category = _optional_text(category, 'category', MAX_METADATA_LENGTH)
    product_type = _optional_text(product_type, 'product_type', MAX_METADATA_LENGTH)
    visibility = _normalize_visibility(visibility)
    roles = _normalize_roles(allowed_roles)
    _validate_visibility_roles(visibility, roles)
    effective = _normalize_effective_from(effective_from)
    label = _optional_text(label, 'label', MAX_METADATA_LENGTH)
    validate_upload(display_name, data)
    checksum = hashlib.sha256(data).hexdigest()
    mime_type = mime_type_for(display_name)

    with session_scope() as session:
        duplicate = _duplicate_version(session, ctx.tenant_id, checksum)
        if duplicate and not force:
            raise DuplicateDocument(duplicate[0], duplicate[1])
        stored = store_bytes(data, mime_type=mime_type, checksum=checksum)
        document = Document(
            id=_new_document_id(),
            tenant_id=ctx.tenant_id,
            title=title,
            category=category,
            product_type=product_type,
            visibility=visibility,
            allowed_roles=roles,
            status='active',
            owner_id=ctx.user_id,
        )
        session.add(document)
        session.flush()
        version = _draft_version(
            document_id=document.id,
            version_number=1,
            label=label,
            effective_from=effective,
            display_name=display_name,
            stored=stored,
        )
        session.add(version)
        session.flush()
        _audit_document_created(session, ctx, document, version)
        _audit_version_created(session, ctx, document, version)
        return _version_payload(document, version)


def create_version(
    ctx: SecurityContext,
    document_id: str,
    *,
    filename: str | None,
    data: bytes,
    effective_from: str | None = None,
    label: str | None = None,
    force: bool = False,
) -> dict:
    require_manager(ctx)
    display_name = _display_filename(filename)
    effective = _normalize_effective_from(effective_from)
    label = _optional_text(label, 'label', MAX_METADATA_LENGTH)
    validate_upload(display_name, data)
    checksum = hashlib.sha256(data).hexdigest()
    mime_type = mime_type_for(display_name)

    with session_scope() as session:
        document = load_document(ctx, session, document_id)
        if document.status != 'active':
            raise InvalidState('Cannot upload a version to an archived document.')
        duplicate = _duplicate_version(session, ctx.tenant_id, checksum)
        if duplicate and not force:
            raise DuplicateDocument(duplicate[0], duplicate[1])
        version_number = (
            session.scalar(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == document.id
                )
            )
            or 0
        ) + 1
        stored = store_bytes(data, mime_type=mime_type, checksum=checksum)
        version = _draft_version(
            document_id=document.id,
            version_number=version_number,
            label=label,
            effective_from=effective,
            display_name=display_name,
            stored=stored,
        )
        session.add(version)
        document.updated_at = utcnow()
        session.flush()
        _audit_version_created(session, ctx, document, version)
        return _version_payload(document, version)


def retry_failed_version(
    ctx: SecurityContext, document_id: str, version_id: str
) -> dict:
    require_manager(ctx)
    with session_scope() as session:
        document = load_document(ctx, session, document_id)
        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document.id,
            )
        )
        if version is None:
            raise DocumentNotFound(f'Document version {version_id} not found')
        if version.status != 'failed':
            raise InvalidState(
                f'Only failed versions can be retried; {version.id} is {version.status}.'
            )
        version.status = 'draft'
        version.error_code = None
        version.error_message = None
        version.updated_at = utcnow()
        document.updated_at = utcnow()
        record_audit(
            None,
            ctx.user_id,
            'version.retried',
            {
                'tenant_id': ctx.tenant_id,
                'document_id': document.id,
                'version_id': version.id,
                'source_filename': version.source_filename,
                'checksum': version.checksum,
            },
            session=session,
        )
        session.flush()
        return _version_payload(document, version)


def _duplicate_version(
    session: Session, tenant_id: str, checksum: str
) -> tuple[DocumentVersion, Document] | None:
    row = session.execute(
        select(DocumentVersion, Document)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(
            Document.tenant_id == tenant_id,
            DocumentVersion.checksum == checksum,
        )
        .order_by(DocumentVersion.created_at, DocumentVersion.id)
        .limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def _draft_version(
    *,
    document_id: str,
    version_number: int,
    label: str | None,
    effective_from: str,
    display_name: str,
    stored: StoredSource,
) -> DocumentVersion:
    return DocumentVersion(
        id=_new_version_id(),
        document_id=document_id,
        version_number=version_number,
        label=label,
        status='draft',
        effective_from=effective_from,
        content_text=None,
        source_filename=display_name,
        storage_key=stored.storage_key,
        checksum=stored.checksum,
        size_bytes=stored.size_bytes,
        mime_type=stored.mime_type,
        embedding=None,
        embedding_model=None,
        embedding_dimensions=None,
        embedding_generated_at=None,
        error_code=None,
        error_message=None,
        published_at=None,
    )


def _version_payload(document: Document, version: DocumentVersion) -> dict:
    return {
        'document_id': document.id,
        'version_id': version.id,
        'version_number': version.version_number,
        'status': version.status,
        'document_status': document.status,
        'source_filename': version.source_filename,
        'checksum': version.checksum,
        'size_bytes': version.size_bytes,
        'mime_type': version.mime_type,
        'effective_from': version.effective_from,
        'label': version.label,
    }


def _audit_document_created(
    session: Session,
    ctx: SecurityContext,
    document: Document,
    version: DocumentVersion,
) -> None:
    record_audit(
        None,
        ctx.user_id,
        'document.created',
        {
            'tenant_id': ctx.tenant_id,
            'document_id': document.id,
            'version_id': version.id,
            'title': document.title,
            'category': document.category,
            'product_type': document.product_type,
            'visibility': document.visibility,
            'allowed_roles': document.allowed_roles,
            'source_filename': version.source_filename,
            'checksum': version.checksum,
            'size_bytes': version.size_bytes,
            'mime_type': version.mime_type,
        },
        session=session,
    )


def _audit_version_created(
    session: Session,
    ctx: SecurityContext,
    document: Document,
    version: DocumentVersion,
) -> None:
    record_audit(
        None,
        ctx.user_id,
        'version.created',
        {
            'tenant_id': ctx.tenant_id,
            'document_id': document.id,
            'version_id': version.id,
            'version_number': version.version_number,
            'label': version.label,
            'effective_from': version.effective_from,
            'source_filename': version.source_filename,
            'checksum': version.checksum,
            'size_bytes': version.size_bytes,
            'mime_type': version.mime_type,
        },
        session=session,
    )


def _display_filename(filename: str | None) -> str:
    if not filename:
        return ''
    return Path(filename.replace('\\', '/')).name


def _required_text(value: str | None, field: str, max_length: int) -> str:
    text = (value or '').strip()
    if not text:
        raise InvalidMetadata(f'{field} is required.')
    if len(text) > max_length:
        raise InvalidMetadata(f'{field} must be at most {max_length} characters.')
    return text


def _optional_text(
    value: str | None, field: str, max_length: int
) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise InvalidMetadata(f'{field} must be at most {max_length} characters.')
    return text


def _normalize_visibility(value: str | None) -> str:
    visibility = (value or 'tenant').strip().lower()
    if visibility not in VALID_VISIBILITIES:
        allowed = ', '.join(sorted(VALID_VISIBILITIES))
        raise InvalidMetadata(f'visibility must be one of {allowed}.')
    return visibility


def _normalize_roles(raw: list[str] | None) -> list[str] | None:
    roles: list[str] = []
    for item in raw or []:
        for part in str(item).split(','):
            role = part.strip().lower()
            if role and role not in roles:
                roles.append(role)
    invalid = sorted(set(roles) - USER_ROLES)
    if invalid:
        allowed = ', '.join(sorted(USER_ROLES))
        raise InvalidMetadata(f'allowed_roles must be within {allowed}.')
    return roles or None


def _validate_visibility_roles(visibility: str, roles: list[str] | None) -> None:
    if visibility == 'restricted' and not roles:
        raise InvalidMetadata(
            'allowed_roles is required when visibility is restricted.'
        )


def _normalize_effective_from(value: str | None) -> str:
    if value is None or not str(value).strip():
        return date.today().isoformat()
    text = str(value).strip()
    try:
        datetime.fromisoformat(text)
    except ValueError:
        try:
            date.fromisoformat(text)
        except ValueError as exc:
            raise InvalidMetadata(
                'effective_from must be an ISO date or datetime.'
            ) from exc
    return text


def _new_document_id() -> str:
    return f'DOC-{uuid4().hex[:16].upper()}'


def _new_version_id() -> str:
    return f'DOCV-{uuid4().hex[:16].upper()}'
