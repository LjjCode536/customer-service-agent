# ADR-0009: Document / Document Version 持久化

- **Status**: Accepted
- **Date**: 2026-11-09

## Context

知识库此前只有 `Knowledge` 单表：每一行同时是“文档”和“版本”，`status=active` 同时表达生命周期与可召回性，`KB-REFUND-V2` 与 `KB-REFUND-V3` 互不关联。文档管理层需要先落定“文档 1—N 版本”的持久化基座，再逐步接入上传、解析、发布、权限与召回切换。

本阶段是 pre-factor：不新增 API/UI，现有召回与测试必须保持绿色。

## Decision

- 新增 `Document` 与 `DocumentVersion` 两个实体（`documents` / `document_versions`），沿用 ADR-0005 的 SQLAlchemy + `create_all` 基线，不引入 Alembic。
- `Document` 承载租户、标题、分类、产品类型、Visibility、`allowed_roles`、`active / archived` 状态、owner 与时间戳。
- `DocumentVersion` 承载文档外键、每文档自增的 `version_number`、可选 `label`、版本状态、`effective_from`、解析正文、原文件元数据、整篇 embedding（模型、维度、生成时间）、错误码/错误信息、发布时间与时间戳。
- `UNIQUE(document_id, version_number)` 保证同一文档的版本号唯一。
- 用部分唯一索引 `UNIQUE(document_id) WHERE status = 'published'` 强制“同一文档同一时刻最多一个 Published Version”；SQLite 与 Postgres 各自生成对应方言的索引。
- seed 显式映射旧 `KB-*` 行为 3 个 Document、4 个 Document Version，保留原 ID、label、`effective_from` 与 Visibility；同时继续双写旧 `Knowledge` 表，直到读路径单独切换。
- 每个 seed 版本把正文写入 `DOCUMENT_STORAGE_DIR` 下的内容寻址文件（SHA-256，`storage_key` 与原始文件名解耦），数据库保存 `storage_key / checksum / size_bytes / mime_type`。
- seed 为每个版本缓存整篇 embedding，并记录模型与维度。

## Consequences

- 读路径仍走 `Knowledge`，本阶段不改变召回行为；双写会在切换 ticket 中移除。
- 已有本地 SQLite 数据库由 `create_all` 自动补新表，但不会自动迁移数据；重新 seed 即可得到文档与版本。
- 文件写入与数据库事务无法跨介质原子提交；内容寻址使重复写入幂等，失败最多留下可安全清理的孤儿文件。
- 发布动作后续必须用事务把旧 `published` 版本改为 `superseded`，数据库唯一索引负责裁决并发发布冲突。
