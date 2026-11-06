# ADR-0006: 评测使用隔离沙箱与离线 Provider

- **Status**: Accepted
- **Date**: 2026-11-06

## Context

Golden Dataset 必须走完整的 Router → Task → Worker → Workflow 链路，才能发现
Eligibility、Risk、审批链、幂等与工单路由的回归。但评测不能污染正在演示的业务库，
也不能依赖真实模型 key、外网或 SSE 等待。

## Decision

- 每次 `GET /evaluation` 在一个临时 SQLite 数据库中执行全部用例。
- 数据库与 LangGraph checkpointer 都使用临时路径；通过 context-local 的
  `use_database_engine()` / `use_graph()` 覆盖，不修改进程级全局配置。
- 用例使用 `app/llm/offline.py` 的 `OfflineProvider`，Chat、Embedding、Rerank 全部确定性离线实现；
  Provider 边界仍然只有 ModelProvider 层。
- 评测直接调用与生产相同的 `dispatch_message()`、`run_once()`、`decide_approval()`，
  同步驱动 Task；不依赖 SSE，也不比较最终话术。
- LangGraph 不可用时评测直接报错；只有显式 `WORKFLOW_ENGINE=fallback` 才使用确定性降级路径，
  避免“悄悄降级仍全绿”。
- 每个用例断言 `mode / intent / ticket / refund / approval / idempotency`，
  并返回逐项 `checks` 与 `trace_id`，失败样本可据此登记 Bad Case。
- Bad Case 保存在业务库，字段包含 `trace_id`、错误类型、根因、期望结果与修复状态。

## Consequences

- 评测无网络、无 key 可运行，结果稳定可复现。
- 线上数据与演示流程不受评测影响。
- 评测覆盖业务流程与副作用，不覆盖真实模型的回答质量；后者需要另一套评测。
