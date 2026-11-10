import pytest

from app.documents.parsing import (
    MAX_DOCUMENT_BYTES,
    DocumentTooLarge,
    EncryptedPdf,
    InvalidDocumentContent,
    NoExtractableText,
    UnsupportedDocumentFormat,
    parse_document,
)
from tests.binary_fixtures import (
    build_docx,
    build_encrypted_pdf,
    build_pdf,
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
        parse_document('policy.xlsx', b'PK\x03\x04')

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


def test_parse_pdf_extracts_only_the_text_layer():
    parsed = parse_document('policy.pdf', build_pdf('Refund policy body.'))

    assert parsed.text.strip() == 'Refund policy body.'
    assert parsed.mime_type == 'application/pdf'
    assert parsed.extension == '.pdf'


def test_parse_encrypted_pdf_reports_encrypted_pdf():
    with pytest.raises(EncryptedPdf) as exc:
        parse_document('policy.pdf', build_encrypted_pdf())

    assert exc.value.code == 'encrypted_pdf'
    assert 'OCR' not in exc.value.message


def test_parse_pdf_without_a_text_layer_reports_no_extractable_text():
    with pytest.raises(NoExtractableText) as exc:
        parse_document('policy.pdf', build_pdf(''))

    assert exc.value.code == 'no_extractable_text'
    assert 'OCR' in exc.value.message


def test_parse_rejects_pdf_without_a_pdf_header():
    with pytest.raises(InvalidDocumentContent) as exc:
        parse_document('policy.pdf', b'not a pdf')

    assert exc.value.code == 'invalid_content'


def test_parse_rejects_a_malformed_pdf():
    with pytest.raises(InvalidDocumentContent) as exc:
        parse_document('policy.pdf', b'%PDF-1.4\nnot really a pdf\n%%EOF')

    assert exc.value.code == 'invalid_pdf'


def test_parse_docx_preserves_paragraph_and_table_order():
    data = build_docx(
        [
            ('p', '退款总则'),
            ('textbox', '文本框内容'),
            ('table', [['商品', '政策'], ['耳机', '七天可退']]),
            ('p', '补充说明'),
        ],
        header_text='内部页眉不应出现',
    )

    parsed = parse_document('policy.docx', data)

    assert parsed.text == (
        '退款总则\n'
        '商品 | 政策\n'
        '耳机 | 七天可退\n'
        '补充说明'
    )
    assert parsed.mime_type == (
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )
    assert '页眉' not in parsed.text
    assert '文本框内容' not in parsed.text
    assert '图片说明' not in parsed.text


def test_parse_docx_unwraps_content_controls_in_body_order():
    data = build_docx(
        [
            ('p', '受控段落'),
            ('table', [['字段', '值'], ['渠道', '线上']]),
        ],
        wrap_in_content_control=True,
    )

    parsed = parse_document('policy.docx', data)

    assert parsed.text == '受控段落\n字段 | 值\n渠道 | 线上'


def test_parse_docx_without_body_reports_invalid_docx():
    with pytest.raises(InvalidDocumentContent) as exc:
        parse_document('policy.docx', build_docx([], body=False))

    assert exc.value.code == 'invalid_docx'


def test_parse_docx_without_text_reports_no_extractable_text():
    with pytest.raises(NoExtractableText) as exc:
        parse_document('policy.docx', build_docx([('p', '   ')]))

    assert exc.value.code == 'no_extractable_text'


def test_parse_rejects_docx_without_a_zip_header():
    with pytest.raises(InvalidDocumentContent) as exc:
        parse_document('policy.docx', b'not a zip')

    assert exc.value.code == 'invalid_content'
