# ADR-0004: Agent Loop 只读且有预算

- **Status**: Accepted
- **Date**: 2026-11-05

## Context

简历的高频问题包含"Agent Loop 如何停止"。项目主线刻意避免所有请求都走 Agent，但需要一个可演示、可测试的 Loop 来说明预算与终止条件。

## Decision

- 仅在 `intent=chat`、Router 置信度低于阈值，或多步只读查询时启用 Agent Loop。
- 多步只读查询指带订单号且同时需要业务事实与知识检索的请求；`refund` / `complaint` 永不进入 Loop。
- Loop 只允许只读工具：`get_order`、`get_logistics`、`retrieve_knowledge`。
- 不允许在 Loop 内创建工单、发起退款或修改订单。
- 预算：`max_steps=4`、`max_tool_calls=6`、`max_tokens` 上限。
- 终止条件：业务动作完成、证据充分、需要人工、预算耗尽、工具失败。
- 预算耗尽或证据不足时降级到 FAQ / 人工，而不是继续循环。

## Consequences

- 有副作用的动作仍然只由 Rule Engine + Workflow 执行，权限边界清晰。
- 面试可讲"什么时候不循环"与"循环如何停"。
