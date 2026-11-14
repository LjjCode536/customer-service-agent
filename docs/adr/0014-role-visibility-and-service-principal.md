# ADR-0014: 角色可见性矩阵与 Workflow 服务身份

- **Status**: Accepted
- **Date**: 2026-11-14

## Context

ADR-0010 把召回切到 Published Version，但只做了租户隔离与召回状态过滤；ADR-0013 把写操作收敛到 `manager`，读路径仍没有执行 `tenant / staff_only / restricted` 的 Visibility 规则。退款/投诉 Workflow 又必须读取 `staff_only` 的物流 SOP，如果直接沿用用户角色，内部检索会被客户权限挡住。

## Decision

- **读取矩阵**：`tenant` 对租户内所有角色可读；`staff_only` 只对 `agent / manager / service` 可读；`restricted` 只对 `allowed_roles` 中的角色可读，内部 `service` 身份始终可读。
- **写入边界**：上传、版本创建、发布、归档、恢复、重试只允许 `manager`；前端隐藏按钮只是体验，后端 `require_manager` 是权限边界。读写授权相互独立：`manager` 可以管理本租户任意文档，但读取仍服从 Visibility，未被 `restricted` 授权的详情/下载仍返回 `403`。
- **错误语义**：跨租户读取保持 `404`，不暴露存在性；同租户但 Visibility 拒绝的详情/下载返回 `403`；列表与召回直接过滤，不返回不可见文档。
- **统一过滤点**：文档列表、详情、版本详情、原文件下载、`/api/v1/knowledge`、FAQ、Agent Loop 与 Workflow 召回共用 `can_read_visibility`；`retrieve` 仍是关键字必填 `ctx`，没有绕过权限的默认值。
- **Service Principal**：Workflow 的 `retrieve_policy` 使用 `service_context(task.tenant_id)`，仅在任务租户内检索，优先获得 `staff_only` SOP。该身份不能被 HTTP 请求头伪造；`get_security_context` 只接受 `customer / agent / manager`，`service` 或未知角色返回 `403`。
- **不泄露正文**：`retrieve_policy` 的 Trace 输出、`node_completed` 事件与调用 LLM 的证据只保留 `title / version / rerank_score`；Workflow 的 task result 不持久化 knowledge 正文。客户可见的答案因此不会携带 `staff_only` 原文。
- **seed**：演示数据继续使用 3 个 Document、4 个 Version，其中 `DOC-LOGISTICS-SOP` 为 `staff_only`；已有本地数据库需要重新执行 `python -m app.scripts_seed` 才能得到新的可见性行为。

## Consequences

- 权限判断集中在 `app/core/security.py`，新增读路径必须显式调用，不能只依赖前端或单个 SQL 条件。
- BM25 语料只包含调用者可见的 Published Version，因此不同角色的召回排名可能不同；这是权限隔离的预期结果。
- 新增受限文档时，`allowed_roles` 仅支持 `customer / agent / manager` 三个角色，不引入用户级或部门级 ACL。
- Workflow 的节点顺序、退款规则与投诉规则不变，只是策略召回的身份与 Trace 投影发生变化。
