"""Builders for binary document fixtures used by parser and API tests.

The fixtures are intentionally tiny but structurally valid, so the tests exercise
real PDF / DOCX parsing without depending on a document-authoring library.
"""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO
from typing import TypeAlias
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfReader, PdfWriter

WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
DRAWING_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'

TableRows: TypeAlias = Sequence[Sequence[str]]
DocxBlock: TypeAlias = tuple[str, str | TableRows]


def build_pdf(text: str = 'Refund policy PDF body.') -> bytes:
    """Return a single-page PDF with ``text`` in its text layer.

    Pass an empty string to build a page with no text layer at all.
    """
    content = f'BT /F1 12 Tf 72 720 Td ({_pdf_escape(text)}) Tj ET' if text else ''
    content_bytes = content.encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        (
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'
        ),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(content_bytes), content_bytes),
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n'.encode('ascii') + obj + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n'.encode('ascii')
    out += b'0000000000 65535 f \n'
    for offset in offsets[1:]:
        out += f'{offset:010d} 00000 n \n'.encode('ascii')
    out += (
        f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
        f'startxref\n{xref}\n%%EOF\n'
    ).encode('ascii')
    return bytes(out)


def build_encrypted_pdf(text: str = 'Encrypted policy.') -> bytes:
    reader = PdfReader(BytesIO(build_pdf(text)))
    writer = PdfWriter(clone_from=reader)
    writer.encrypt('secret')
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build_docx(
    blocks: list[DocxBlock],
    *,
    header_text: str | None = None,
    body: bool = True,
    wrap_in_content_control: bool = False,
) -> bytes:
    """Build a minimal DOCX from ordered body blocks.

    Blocks are ``('p', text)``, ``('table', rows)``, or ``('textbox', text)``.
    ``header_text`` is written to ``word/header1.xml`` to prove headers are not
    part of the extracted body.  ``body=False`` is used to exercise malformed
    packages; ``wrap_in_content_control=True`` puts the body inside ``w:sdt``.
    """
    body_xml = ''
    for kind, value in blocks:
        if kind == 'p' and isinstance(value, str):
            body_xml += _paragraph_xml(value)
        elif kind == 'table' and not isinstance(value, str):
            body_xml += _table_xml(value)
        elif kind == 'textbox' and isinstance(value, str):
            body_xml += _textbox_xml(value)
        else:
            raise ValueError(f'unknown docx block: {kind}')
    if wrap_in_content_control:
        body_xml = f'<w:sdt><w:sdtContent>{body_xml}</w:sdtContent></w:sdt>'

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NS}" xmlns:wp="{DRAWING_NS}">'
        f'<w:body>{body_xml}<w:sectPr/></w:body>'
        '</w:document>'
    )

    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'
            '</Types>',
        )
        if body:
            archive.writestr('word/document.xml', document)
        if header_text is not None:
            archive.writestr(
                'word/header1.xml',
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<w:hdr xmlns:w="{WORD_NS}">{_paragraph_xml(header_text)}</w:hdr>',
            )
    return buffer.getvalue()


def _pdf_escape(text: str) -> str:
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _paragraph_xml(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _textbox_xml(text: str) -> str:
    return (
        '<w:p><w:r><w:drawing><wp:inline>'
        '<wp:docPr name="图片" descr="图片说明"/>'
        f'<w:txbxContent>{_paragraph_xml(text)}</w:txbxContent>'
        '</wp:inline></w:drawing></w:r></w:p>'
    )


def _table_xml(rows: TableRows) -> str:
    row_xml = ''
    for row in rows:
        cell_xml = ''.join(
            f'<w:tc>{_paragraph_xml(str(cell))}</w:tc>' for cell in row
        )
        row_xml += f'<w:tr>{cell_xml}</w:tr>'
    return f'<w:tbl>{row_xml}</w:tbl>'
