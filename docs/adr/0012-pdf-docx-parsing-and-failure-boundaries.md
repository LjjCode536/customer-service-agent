# ADR-0012: PDF / DOCX 解析与失败边界

- **Status**: Accepted
- **Date**: 2026-11-10

## Context

ADR-0011 已交付文本格式上传与异步入库，并把解析抽象为纯函数 `parse_document(filename, data) -> ParsedDocument`。Ticket #19 需要把 PDF / DOCX 接入同一 seam，同时让二进制格式的失败可诊断、可重试，且不产生空正文的 `ready` 版本。

## Decision

- **PDF 只提取文本层**：使用纯 Python 的 `pypdf` 读取页面文本，按页序拼接。加密 PDF 在访问页面前直接返回 `encrypted_pdf`；没有文本层时返回 `no_extractable_text`；结构损坏返回 `invalid_pdf`。明确不支持 OCR 或扫描版 PDF。
- **DOCX 按正文顺序提取**：使用标准库 `zipfile` + `ElementTree` 读取 `word/document.xml`，按顺序遍历 `w:body`，并展开 `w:sdt` / `w:sdtContent` 等内容控件包装；段落输出为一行，表格逐行输出、单元格用 ` | ` 分隔，单元格内的嵌套表格也并入该行。页眉、页脚、图片、文本框与批注不进入正文。缺失正文部件或 XML 损坏返回 `invalid_docx`。
- **空正文不进入 `ready`**：PDF / DOCX 提取后为空统一返回 `no_extractable_text`；文本格式继续返回 `empty_content`。两类错误都由 Worker 写成显式 `failed` 版本。
- **上传阶段只做廉价校验**：保留扩展名、20MB 上限与文件头校验；`.pdf` 要求前 1024 字节内出现 `%PDF-`，`.docx` 要求 ZIP 头。头部合法但结构损坏的文件允许上传，随后由 Worker 以显式错误码失败，避免在上传请求里做完整解析。
- **复用既有存储与重试路径**：原始文件仍在内容寻址存储中保留；失败版本通过既有 `retry` 端点回到 `draft`，不复制文件、不需要重新上传。解析超时继续由 Worker 线程边界返回 `parse_timeout`（默认 60 秒）。
- **前端展示失败原因**：Knowledge Center 接受四种扩展名，版本列表与正文弹窗展示 `error_code` 和可读 `error_message`，`manager` 可直接重试失败版本。

## Consequences

- 新增运行时依赖 `pypdf`；它是纯 Python 包，不引入系统级 PDF 工具链。
- DOCX 解析覆盖标准 WordprocessingML 正文、内容控件与一层单元格嵌套表格；文本框、修订删除痕迹、脚注等非正文结构会被忽略而非报错。
- 扫描版 PDF 仍然不可用，这是显式产品边界，不是待修复的缺陷。
- 失败版本保留原文件与 checksum，重试幂等且可审计；但若文件本身不可解析，重试会再次得到同一错误码。
