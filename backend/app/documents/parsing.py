"""Pure parsing for uploaded documents.

The parser is intentionally a seam of its own: it takes the original filename and
bytes, performs extension/header validation, and returns normalized text without
touching the database or the filesystem.

Supported formats are plain text (``txt`` / ``md``), PDF (text layer only, no
OCR), and DOCX (body paragraphs and tables in document order; headers, footers,
and images are ignored).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

TEXT_MIME_TYPES = {
    '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
}

BINARY_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': (
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    ),
}

SUPPORTED_MIME_TYPES = {**TEXT_MIME_TYPES, **BINARY_MIME_TYPES}
TEXT_EXTENSIONS = frozenset(TEXT_MIME_TYPES)

_WORD_NAMESPACE = (
    'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
)
_W_BODY = f'{{{_WORD_NAMESPACE}}}body'
_W_P = f'{{{_WORD_NAMESPACE}}}p'
_W_T = f'{{{_WORD_NAMESPACE}}}t'
_W_TAB = f'{{{_WORD_NAMESPACE}}}tab'
_W_BR = f'{{{_WORD_NAMESPACE}}}br'
_W_CR = f'{{{_WORD_NAMESPACE}}}cr'
_W_TBL = f'{{{_WORD_NAMESPACE}}}tbl'
_W_TR = f'{{{_WORD_NAMESPACE}}}tr'
_W_TC = f'{{{_WORD_NAMESPACE}}}tc'
_W_SDT = f'{{{_WORD_NAMESPACE}}}sdt'
_W_SDT_CONTENT = f'{{{_WORD_NAMESPACE}}}sdtContent'
_W_CUSTOM_XML = f'{{{_WORD_NAMESPACE}}}customXml'
_W_INS = f'{{{_WORD_NAMESPACE}}}ins'
_W_MOVE_TO = f'{{{_WORD_NAMESPACE}}}moveTo'
_W_TXBX_CONTENT = f'{{{_WORD_NAMESPACE}}}txbxContent'
_BLOCK_CONTAINERS = frozenset(
    {_W_SDT, _W_SDT_CONTENT, _W_CUSTOM_XML, _W_INS, _W_MOVE_TO}
)


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


class EncryptedPdf(DocumentParseError):
    code = 'encrypted_pdf'


class NoExtractableText(DocumentParseError):
    code = 'no_extractable_text'


class InvalidPdf(InvalidDocumentContent):
    code = 'invalid_pdf'


class InvalidDocx(InvalidDocumentContent):
    code = 'invalid_docx'


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
    _validate_header(extension, data)
    return extension


def parse_document(filename: str | None, data: bytes) -> ParsedDocument:
    """Parse a supported document into normalized content.

    ``txt`` and ``md`` are decoded as UTF-8 first, then GB18030.  A UTF-8 BOM is
    removed, CRLF/CR are normalized to LF, and empty content is an explicit
    failure rather than an empty ``ready`` version.

    PDF only extracts the existing text layer; scanned pages are not OCR'd.
    DOCX walks the body in order so paragraphs and tables keep their relative
    position, while headers, footers, and embedded images are ignored.
    """
    extension = validate_upload(filename, data)
    if extension in TEXT_EXTENSIONS:
        text = normalize_newlines(_decode_text(data))
        if not text.strip():
            raise InvalidDocumentContent(
                'Document has no extractable text.', code='empty_content'
            )
    elif extension == '.pdf':
        text = _parse_pdf(data)
    elif extension == '.docx':
        text = _parse_docx(data)
    else:  # pragma: no cover - validate_upload rejects other extensions
        raise UnsupportedDocumentFormat(
            f'Unsupported document format {extension or "(none)"}.'
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


def _validate_header(extension: str, data: bytes) -> None:
    if extension in TEXT_EXTENSIONS:
        if _looks_binary(data):
            raise InvalidDocumentContent(
                'File header looks binary; upload a UTF-8 or GB18030 text file.'
            )
        return
    if extension == '.pdf':
        if b'%PDF-' not in data[:1024]:
            raise InvalidDocumentContent(
                'File does not look like a PDF; expected a %PDF- header.',
                code='invalid_content',
            )
        return
    if extension == '.docx' and not data.startswith(b'PK\x03\x04'):
        raise InvalidDocumentContent(
            'File does not look like a DOCX package; expected a ZIP header.',
            code='invalid_content',
        )


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception as exc:  # third-party parser boundary
        raise InvalidPdf(
            'PDF could not be read; the file may be corrupted.'
        ) from exc
    if encrypted:
        raise EncryptedPdf(
            'Encrypted PDFs are not supported; upload a PDF without password '
            'protection.'
        )
    try:
        pages = [page.extract_text() or '' for page in reader.pages]
    except Exception as exc:  # third-party parser boundary
        raise InvalidPdf(
            'PDF text could not be extracted; the file may be corrupted.'
        ) from exc
    text = normalize_newlines('\n'.join(pages))
    if not text.strip():
        raise NoExtractableText(
            'PDF has no extractable text layer; scanned PDFs are not supported '
            '(OCR is not implemented).'
        )
    return text


def _parse_docx(data: bytes) -> str:
    try:
        with ZipFile(BytesIO(data)) as archive:
            xml = archive.read('word/document.xml')
    except KeyError as exc:
        raise InvalidDocx('DOCX is missing word/document.xml.') from exc
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise InvalidDocx('DOCX could not be read; the file may be corrupted.') from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise InvalidDocx('DOCX word/document.xml is not valid XML.') from exc
    body = root.find(_W_BODY)
    if body is None:
        raise InvalidDocx('DOCX document.xml has no body.')

    text = normalize_newlines('\n'.join(_docx_block_texts(body)))
    if not text.strip():
        raise NoExtractableText('DOCX has no extractable body text.')
    return text


def _docx_block_texts(container: ElementTree.Element) -> Iterator[str]:
    """Yield body text in document order, unwrapping content controls."""
    for child in container:
        if child.tag == _W_P:
            text = _docx_paragraph_text(child)
            if text.strip():
                yield text
        elif child.tag == _W_TBL:
            text = _docx_table_text(child)
            if text.strip():
                yield text
        elif child.tag in _BLOCK_CONTAINERS:
            yield from _docx_block_texts(child)


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []

    def walk(node: ElementTree.Element) -> None:
        for child in node:
            if child.tag == _W_TXBX_CONTENT:
                continue
            if child.tag == _W_T:
                if child.text:
                    parts.append(child.text)
            elif child.tag == _W_TAB:
                parts.append('\t')
            elif child.tag in (_W_BR, _W_CR):
                parts.append('\n')
            else:
                walk(child)

    walk(paragraph)
    return ''.join(parts)


def _docx_table_text(table: ElementTree.Element) -> str:
    lines: list[str] = []
    for row in table.findall(_W_TR):
        cells = [_docx_cell_text(cell) for cell in row.findall(_W_TC)]
        if any(cell for cell in cells):
            lines.append(' | '.join(cells))
    return '\n'.join(lines)


def _docx_cell_text(cell: ElementTree.Element) -> str:
    blocks = [block.replace('\n', ' ') for block in _docx_block_texts(cell)]
    return ' '.join(block for block in blocks if block)


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
