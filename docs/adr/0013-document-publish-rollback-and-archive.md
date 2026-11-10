# ADR-0013: 发布、替换、回滚与归档

- **Status**: Accepted
- **Date**: 2026-11-10

## Context

ADR-0009 已经用部分唯一索引固定“同一文档最多一个 `published` 版本”，ADR-0010 把召回与 `/api/v1/knowledge` 切到 Published Version，ADR-0011 / 0012 交付了上传、解析与失败重试。Ticket #20 需要补齐版本生命周期的写路径：显式发布、替换、回滚、版本级与文档级归档/恢复，以及 Knowledge Center 上的操作入口。

## Decision

- **发布入口只接受 `ready` 与 `superseded`**：`ready` 首次上线；`superseded` 重新发布即回滚。重复发布当前 `published` 版本按幂等处理，返回当前状态且不重复写审计；`draft / processing / failed / archived` 一律 `400`。
- **发布前显式校验**：`effective_from` 必须可解析，正文必须非空，整篇 embedding 必须存在且模型/维度与当前 Provider 一致。缺失或过期时在发布动作内重算；Provider 失败返回 `503`，事务回滚，版本保持 `ready`，旧 Published Version 不受影响。
- **替换是单事务内的两步写**：先把同一文档当前 `published` 版本更新为 `superseded` 并 flush，再把目标版本写为 `published`。这样中间状态也不会违反部分唯一索引（按语句检查唯一性的 SQLite / Postgres 都安全）。
- **并发冲突由数据库裁决**：发布事务提交时若唯一索引冲突，`IntegrityError` 映射为 `409`；失败方回滚，不留下审计或半发布状态。
- **版本归档只针对 `published`**：人工下架后版本变 `archived`，立即退出召回；`archived` 可恢复为 `ready`，再通过发布重新上线。`ready` 等状态不能直接下架。
- **文档归档独立于版本状态**：Document 变 `archived` 后版本状态保持原样，但召回与 `/api/v1/knowledge` 因 `Document.status = active` 过滤而全部退出；恢复文档后原 `published` 版本重新具备召回资格。
- **未来 `effective_from` 允许发布**：发布本身不等待生效日；召回仍要求 `effective_from <= now`，因此到点前不参与召回。Knowledge Center 对未生效版本显示“未生效”标签。
- **审计动作**：`version.published`、`version.superseded`、`version.archived`、`version.restored`、`document.archived`、`document.restored`。详情包含 `tenant_id / document_id / version_id / effective_from`，替换时双向记录旧版本与 replacement，文档级审计记录当前 Published Version。
- **权限**：发布、版本归档/恢复、文档归档/恢复全部只允许 `manager`；跨租户仍返回 `404`，与读路径一致。
- **前端**：Knowledge Center 版本表在 `ready` 显示“发布”、`superseded` 显示“发布（回滚）”、`published` 显示“下架”、`archived` 显示“恢复”；文档抽屉提供“归档文档 / 恢复文档”。写操作只对 `manager` 可见，归档动作要求二次确认。

## Consequences

- 发布是唯一能产生 Published Version 的入口，召回状态不再依赖隐式写入或 seed 特例。
- Provider 故障不会让版本卡在中间态；重算成功后才提交发布，失败由调用方重试。
- 版本级归档与文档级归档语义分离：前者只停用单个版本，后者停用整个文档，二者可以叠加但互不改写状态。
- 并发发布失败方需要重试；本层不实现自动重试或合并策略。
- 发布重算 embedding 会在 HTTP 请求内调用 Provider，耗时高于普通写操作；当前整篇 embedding 体量下可接受，Layer 2 切 chunk 后需要重新评估。
