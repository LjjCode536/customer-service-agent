import pytest

from app.documents.parsing import (
    MAX_DOCUMENT_BYTES,
    DocumentTooLarge,
    InvalidDocumentContent,
    UnsupportedDocumentFormat,
    parse_document,
)


def test_parse_txt_prefers_utf8_and_strips_bom():
    parsed = parse_document(
        'policy.txt', '\ufeff第一行\r\n第二行\r第三行\n'.encode('utf-8')
    )

    assert parsed.text == '第一行\n第二行\n第三行\n'
    assert parsed.mime_type.startswith('text/plain')


def test_parse_txt_falls_back_to_gb18030():
    content = '退款政策：七天内可退，超过七天需人工审核。'

    parsed = parse_document('policy.txt', content.encode('gb18030'))

    assert parsed.text == content


def test_parse_markdown_preserves_structure_and_normalizes_newlines():
    parsed = parse_document(
        'policy.md', '# 标题\r\n\r\n- 第一条\r- 第二条\r\n'.encode()
    )

    assert parsed.text == '# 标题\n\n- 第一条\n- 第二条\n'
    assert parsed.mime_type.startswith('text/markdown')


def test_parse_rejects_unsupported_extension():
    with pytest.raises(UnsupportedDocumentFormat) as exc:
        parse_document('policy.pdf', b'%PDF-1.7')

    assert exc.value.code == 'unsupported_format'


def test_parse_rejects_binary_content_disguised_as_text():
    with pytest.raises(InvalidDocumentContent) as exc:
        parse_document('policy.txt', b'\x00\x01\x02not-text')

    assert exc.value.code == 'invalid_content'


def test_parse_rejects_empty_content():
    with pytest.raises(InvalidDocumentContent) as exc:
        parse_document('policy.txt', b'  \r\n\t ')

    assert exc.value.code == 'empty_content'


def test_parse_rejects_files_over_the_20mb_limit():
    with pytest.raises(DocumentTooLarge) as exc:
        parse_document('policy.txt', b'a' * (MAX_DOCUMENT_BYTES + 1))

    assert exc.value.code == 'file_too_large'


def test_parse_accepts_a_file_at_the_20mb_limit():
    parsed = parse_document('policy.txt', b'a' * MAX_DOCUMENT_BYTES)

    assert len(parsed.text) == MAX_DOCUMENT_BYTES
