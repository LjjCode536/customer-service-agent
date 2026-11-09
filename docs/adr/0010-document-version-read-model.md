# ADR-0010: Document Version 作为知识读模型

- **Status**: Accepted
- **Date**: 2026-11-09

## Context

ADR-0009 落定 Document / Document Version 持久化时，召回与 `/api/v1/knowledge` 仍读取旧 `Knowledge` 表，seed 双写两张表。Ticket #17 需要把读取事实来源切到 Published Version，并交付文档目录读路径，同时保持 FAQ、Evidence 与旧接口兼容。

## Decision

- 召回只读取满足 Recall Eligibility 的 Document Version：Document 属于调用者租户且 `active`，版本为 `published`，`effective_from <= now`。
- `retrieve` 显式接收 `SecurityContext`，调用点（Direct RAG、Agent Loop、Workflow）各自传入当前上下文；不提供绕过租户过滤的默认值。
- 保持现有整篇 embedding 的 BM25 + 向量 + RRF + Rerank 管线与 Evidence 字段形状不变；版本 `label` 映射为 Evidence `version`，`id` 继续沿用 `KB-*`。
- 新增 `/api/v1/documents` 读路径：列表、详情、版本详情与源文件下载。列表支持 `status / category / product_type / visibility / keyword` 过滤。
- 所有文档读路径按 `tenant_id` 过滤；跨租户读取返回 `404`，列表只返回本租户数据。
- `GET /api/v1/knowledge` 改为从 Published Version 生成兼容读模型，保持旧字段集合，`version` 映射为版本 `label`，`status` 直接取版本状态（Published Version 即 `published`）；不再读取旧 `Knowledge` 表。
- seed 不再双写 `Knowledge`，只重建 Document / Document Version；旧表保留用于历史数据库兼容，但既不写入也不读取。
- Knowledge Center 改为 Document 列表 + 版本抽屉，本阶段只读。

## Consequences

- 旧本地 SQLite 数据库中的 `knowledge` 行不再影响行为；重新 seed 即可得到文档目录。
- `keywords` 不再由 Document Version 提供，兼容读模型与 Evidence 返回空列表；词法召回继续依赖标题、版本 label 与正文。
- 角色级 Visibility 校验（`staff_only` / `restricted`）与 Workflow `service` 身份留给后续权限 ticket；本阶段只保证租户隔离与召回状态/生效时间过滤。
