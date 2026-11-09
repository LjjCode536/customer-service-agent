# ADR-0011: 文档上传与异步入库

- **Status**: Accepted
- **Date**: 2026-11-09

## Context

Ticket #18 需要让 `manager` 上传 `txt / md`，并保证上传请求不被解析与 embedding 阻塞。版本生命周期已经在 ADR-0009 中落定（`draft / processing / ready / failed / ...`），但还没有上传入口、入库队列、失败重试和用户可见的失败原因。

## Decision

- **版本状态就是入库队列**：上传只创建 `draft` 版本；Worker 用 `UPDATE ... WHERE status = 'draft' RETURNING` 原子领取为 `processing`，同一版本不会被多个 Worker 重复处理。
- **上传时做便宜校验**：扩展名、20MB 上限与二进制文件头在落盘前校验；不支持格式返回 `415`，超大文件返回 `413`。解码失败、空正文与 embedding 失败留在 Worker 中，版本进入 `failed`，不阻塞上传。
- **内容寻址存储**：原始文件按 SHA-256 写入 `DOCUMENT_STORAGE_DIR`（默认 `./data/documents`），数据库保存 `storage_key / checksum / size_bytes / mime_type`；原始文件名只用于展示，不参与路径拼接。Docker Compose 把 `/app/data` 挂载为持久化 volume，因此文档目录随之持久化。
- **重复内容**：同租户相同 checksum 的上传返回 `409` 并附带已存在文档与版本信息；显式 `force=true` 时，`POST /documents` 允许创建新的 Document（其 v1 即新版本），`POST /documents/{document_id}/versions` 允许在已有 Document 下创建新版本。重复判断只在本租户内进行。
- **解析 seam**：解析是纯函数 `parse_document(filename, data) -> ParsedDocument`。文本格式优先 UTF-8（含 BOM 去除），失败回退 GB18030，统一 CRLF/CR 为 LF；空正文显式失败。PDF / DOCX 在后续 ticket 接入同一 seam。
- **解析超时**：Worker 在独立线程中执行解析；超过 `DOCUMENT_PARSE_TIMEOUT_SECONDS`（默认 60 秒）标记 `parse_timeout`。文本解析在 20MB 上限下通常很快，该边界主要供后续 PDF / DOCX 复用。
- **入库与 embedding**：Worker 解析正文后调用 `cache_version_embedding` 缓存整篇 embedding，再提交 `ready`。失败记录 `error_code / error_message`；重试把版本重新排入 `draft` 队列并清空旧错误，Worker 领取后进入 `processing`，复用原文件，不要求重新上传。
- **权限与租户隔离**：上传、版本创建与重试只允许 `manager`；所有文档查询按 `tenant_id` 过滤，跨租户返回 `404`，不暴露存在性。
- **审计**：`document.created`、`version.created`、`version.failed`、`version.retried` 写入 Audit；detail 包含 `tenant_id / document_id / version_id` 与 checksum、文件名、错误码等关键元数据。
- **前端轮询**：Knowledge Center 只在文档详情或版本详情存在 `draft / processing` 状态时每 2 秒轮询，完成后停止；文档列表由上传成功后的失效刷新，不做常驻轮询。失败版本展示错误码与原因，并提供仅 `manager` 可见的重试按钮。

## Consequences

- Worker 在领取后崩溃会让版本停留在 `processing`；本层没有超时回收，需要人工重置或等待后续运维增强。失败重试只覆盖 `failed` 版本。
- 文件写入与数据库事务无法跨介质原子提交；内容寻址让重复写入幂等，最坏情况只留下可安全清理的孤儿文件。
- 上传接口读取整个文件到内存中以完成 20MB 校验，当前单文件上限使这一取舍可接受；若后续提升上限，应改为流式校验与分块写入。
- PDF / DOCX 解析、发布 / 替换 / 回滚、Visibility 读权限和 Workflow `service` 身份仍由后续 ticket 交付。
