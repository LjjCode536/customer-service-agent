# ADR-0001: 用 LangGraph interrupt + checkpointer 实现人工审核暂停与恢复

- **Status**: Accepted
- **Date**: 2026-11-05

## Context

退款 Workflow 在风险等级为 high 时需要暂停，等待 `refund_specialist → service_manager` 两级审批。教学版通过在 `invoke` 之后手工补副作用、再由 Worker 读取 `resume_pending` 恢复，流程分散且难以回放。

## Decision

- LangGraph 作为 Workflow 主路径，编译时挂持久化 checkpointer（`SqliteSaver`），`thread_id = task_id`。
- `human_review` 节点内调用 `interrupt()` 暂停；一次 interrupt 等待整条审批链。
- 审批记录仍落 `approval_records`，由 `sequence_no` 保证顺序，越级返回 409。
- 全部通过后 Task 置 `resume_pending`，Worker 调用 `graph.invoke(Command(resume=decision), config)` 从断点继续。
- deterministic fallback 仅保留为 LangGraph 不可用时的测试/降级路径。

## Consequences

- 暂停点持久化，进程重启不丢；恢复点与图结构一致。
- 副作用必须放在 `interrupt()` 之后，且仍需幂等键保护。
- SQLite checkpointer 只适合单 Worker；多副本需换 Postgres checkpointer（当前不做）。
