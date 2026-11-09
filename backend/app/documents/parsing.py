"""Pure text parsing for uploaded documents.

The parser is intentionally a seam of its own: it takes the original filename and
bytes, performs extension/header validation, and returns normalized text without
touching the database or the filesystem.  PDF and DOCX support arrives in a later
ticket; for now only ``txt`` and ``md`` are accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

SUPPORTED_MIME_TYPES = {
    '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
}


class DocumentParseError(Exception):
    """Base class for deterministic, user-visible document failures."""

    code = 'parse_failed'

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class UnsupportedDocumentFormat(DocumentParseError):
    code = 'unsupported_format'


class DocumentTooLarge(DocumentParseError):
    code = 'file_too_large'


class InvalidDocumentContent(DocumentParseError):
    code = 'invalid_content'


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    mime_type: str
    extension: str


def validate_upload(filename: str | None, data: bytes) -> str:
    """Validate the cheap upload-time constraints and return the extension.

    Extension and a light content-header check happen before the file is stored,
    so a disguised binary or an unsupported format fails with ``415`` instead of
    becoming a failed version later.
    """
    extension = _extension(filename)
    if extension not in SUPPORTED_MIME_TYPES:
        supported = ', '.join(sorted(SUPPORTED_MIME_TYPES))
        raise UnsupportedDocumentFormat(
            f'Unsupported document format {extension or "(none)"}; expected {supported}.'
        )
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(
            f'Document is {len(data)} bytes; the limit is {MAX_DOCUMENT_BYTES} bytes.'
        )
    if _looks_binary(data):
        raise InvalidDocumentContent(
            'File header looks binary; upload a UTF-8 or GB18030 text file.'
        )
    return extension


def parse_document(filename: str | None, data: bytes) -> ParsedDocument:
    """Parse text bytes into normalized content.

    ``txt`` and ``md`` are decoded as UTF-8 first, then GB18030.  A UTF-8 BOM is
    removed, CRLF/CR are normalized to LF, and empty content is an explicit
    failure rather than an empty ``ready`` version.
    """
    extension = validate_upload(filename, data)
    text = _decode_text(data)
    text = normalize_newlines(text)
    if not text.strip():
        raise InvalidDocumentContent(
            'Document has no extractable text.', code='empty_content'
        )
    return ParsedDocument(
        text=text,
        mime_type=SUPPORTED_MIME_TYPES[extension],
        extension=extension,
    )


def mime_type_for(filename: str | None) -> str:
    extension = _extension(filename)
    try:
        return SUPPORTED_MIME_TYPES[extension]
    except KeyError as exc:
        raise UnsupportedDocumentFormat(
            f'Unsupported document format {extension or "(none)"}.'
        ) from exc


def normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _extension(filename: str | None) -> str:
    return Path(filename or '').suffix.lower()


def _decode_text(data: bytes) -> str:
    try:
        # utf-8-sig removes a leading UTF-8 BOM when present.
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    try:
        return data.decode('gb18030')
    except UnicodeDecodeError as exc:
        raise InvalidDocumentContent(
            'Document is neither UTF-8 nor GB18030 text.',
            code='unsupported_encoding',
        ) from exc


def _looks_binary(data: bytes) -> bool:
    sample = data[:8192]
    if not sample:
        return False
    if b'\x00' in sample:
        return True
    control = sum(
        1 for byte in sample if byte < 9 or 14 <= byte < 32
    )
    return control / len(sample) > 0.05
