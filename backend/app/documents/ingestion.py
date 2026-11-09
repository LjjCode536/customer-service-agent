"""Asynchronous ingestion for uploaded document versions.

A version is the queue item: ``draft`` versions are claimed atomically into
``processing``, parsed, embedded, and committed as ``ready``.  Any deterministic
or provider failure moves the version to ``failed`` with an error code/message so
the API can expose a retry action without another file upload.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from sqlalchemy import func, select, update

from app.core.config import settings
from app.db.database import session_scope, use_session, utcnow
from app.db.models import Document, DocumentVersion
from app.documents.parsing import DocumentParseError, ParsedDocument, parse_document
from app.documents.storage import DocumentStorageError, read_source
from app.llm.provider import ProviderError, get_provider
from app.rag.hybrid import cache_version_embedding
from app.services.tasking import record_audit

PARSE_TIMEOUT_SECONDS = settings.document_parse_timeout_seconds


class IngestionFailure(Exception):
    """A version-level failure that should become a ``failed`` version."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def claim_next_ingestion(session=None) -> str | None:
    """Atomically claim the oldest draft version for this worker."""
    with use_session(session) as active_session:
        candidate = (
            select(DocumentVersion.id)
            .where(DocumentVersion.status == 'draft')
            .order_by(DocumentVersion.created_at, DocumentVersion.id)
            .limit(1)
            .scalar_subquery()
        )
        version_id = active_session.scalar(
            update(DocumentVersion)
            .where(
                DocumentVersion.id == candidate,
                DocumentVersion.status == 'draft',
            )
            .values(status='processing', updated_at=func.current_timestamp())
            .returning(DocumentVersion.id)
        )
        if version_id:
            active_session.flush()
        return version_id


def run_ingestion_once() -> bool:
    """Claim and process at most one version; return False when idle."""
    version_id = claim_next_ingestion()
    if not version_id:
        return False
    try:
        _ingest(version_id)
    except IngestionFailure as exc:
        _mark_failed(version_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001  # worker boundary: never crash the loop
        _mark_failed(version_id, 'ingestion_failed', str(exc))
    return True


def _ingest(version_id: str) -> None:
    with session_scope() as session:
        version = session.get(DocumentVersion, version_id)
        if version is None or version.status != 'processing':
            return
        document = session.get(Document, version.document_id)
        if document is None:
            raise IngestionFailure(
                'document_missing',
                f'Document {version.document_id} for version {version.id} is missing.',
            )
        if not version.storage_key:
            raise IngestionFailure(
                'source_unavailable',
                f'Document version {version.id} has no source file.',
            )
        try:
            data = read_source(version.storage_key)
        except (OSError, DocumentStorageError) as exc:
            raise IngestionFailure(
                'source_unavailable',
                f'Source file for document version {version.id} is unavailable: {exc}',
            ) from exc

    try:
        parsed = _parse_with_timeout(version.source_filename, data)
    except DocumentParseError as exc:
        raise IngestionFailure(exc.code, exc.message) from exc

    version.content_text = parsed.text
    try:
        cache_version_embedding(version, document, provider=get_provider())
    except ProviderError as exc:
        raise IngestionFailure('embedding_failed', str(exc)) from exc
    if not version.embedding:
        raise IngestionFailure(
            'embedding_missing',
            'Embedding provider returned no vector for the parsed document.',
        )

    with session_scope() as session:
        row = session.get(DocumentVersion, version_id)
        if row is None or row.status != 'processing':
            return
        row.content_text = version.content_text
        row.mime_type = parsed.mime_type
        row.embedding = version.embedding
        row.embedding_model = version.embedding_model
        row.embedding_dimensions = version.embedding_dimensions
        row.embedding_generated_at = version.embedding_generated_at
        row.status = 'ready'
        row.error_code = None
        row.error_message = None
        row.updated_at = utcnow()
        session.flush()


def _parse_with_timeout(filename: str | None, data: bytes) -> ParsedDocument:
    """Parse in a worker thread so a pathological file cannot pin the worker."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='document-parse')
    future = executor.submit(parse_document, filename, data)
    try:
        return future.result(timeout=PARSE_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        future.cancel()
        raise IngestionFailure(
            'parse_timeout',
            f'Parsing exceeded the {PARSE_TIMEOUT_SECONDS:g}-second limit.',
        ) from exc
    finally:
        # A timed-out parser may still be running; abandon it without blocking
        # the worker on a thread that cannot be cancelled safely.
        executor.shutdown(wait=False, cancel_futures=True)


def _mark_failed(version_id: str, code: str, message: str) -> None:
    with session_scope() as session:
        version = session.get(DocumentVersion, version_id)
        if version is None or version.status != 'processing':
            return
        document = session.get(Document, version.document_id)
        version.status = 'failed'
        version.error_code = code
        version.error_message = message
        version.updated_at = utcnow()
        record_audit(
            None,
            'system:worker',
            'version.failed',
            {
                'tenant_id': document.tenant_id if document else None,
                'document_id': version.document_id,
                'version_id': version.id,
                'source_filename': version.source_filename,
                'checksum': version.checksum,
                'error_code': code,
                'error_message': message,
            },
            session=session,
        )
        session.flush()
