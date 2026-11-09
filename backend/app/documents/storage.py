from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


class DocumentStorageError(Exception):
    """Raised when a stored source file cannot be written or read safely."""


@dataclass(frozen=True)
class StoredSource:
    """Metadata persisted alongside a Document Version for its original file."""

    storage_key: str
    checksum: str
    size_bytes: int
    mime_type: str


def storage_root() -> Path:
    return Path(settings.document_storage_dir)


def store_text(
    text: str, *, mime_type: str = 'text/plain; charset=utf-8'
) -> StoredSource:
    return store_bytes(text.encode('utf-8'), mime_type=mime_type)


def store_bytes(
    data: bytes, *, mime_type: str = 'application/octet-stream'
) -> StoredSource:
    """Write bytes to a content-addressed path, deduplicating identical content.

    The original filename is display metadata only and never participates in the
    storage path.
    """
    checksum = hashlib.sha256(data).hexdigest()
    storage_key = f'{checksum[:2]}/{checksum}'
    path = source_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)
    return StoredSource(
        storage_key=storage_key,
        checksum=checksum,
        size_bytes=len(data),
        mime_type=mime_type,
    )


def source_path(storage_key: str) -> Path:
    root = storage_root().resolve()
    path = (root / storage_key).resolve()
    if path != root and root not in path.parents:
        raise DocumentStorageError(f'storage key escapes storage root: {storage_key!r}')
    return path


def read_source(storage_key: str) -> bytes:
    return source_path(storage_key).read_bytes()
