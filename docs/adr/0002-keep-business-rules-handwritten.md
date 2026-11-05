# ADR-0002: 基础设施换库，业务判断保留手写

- **Status**: Accepted
- **Date**: 2026-11-05

## Context

项目要对齐简历并公开展示。大量手写代码可以被成熟库替代，但如果把业务判断也交给框架，项目将失去可讲的工程判断。

## Decision

替换基础设施：

- RAG：`rank_bm25` + `jieba` + BGE-M3 + Cross-Encoder Rerank
- 持久化：SQLAlchemy 2.0
- SSE：`sse-starlette`
- LLM 调用：`openai` SDK + `tenacity`
- 限流与日志：`slowapi` + `structlog`
- 前端数据层：TanStack Query + `fetch-event-source`

保留手写：

- Intent Router
- 退款 Eligibility / Risk 规则
- Ticket Priority / SLA / Assignment
- 审批链顺序与越级 409
- 幂等语义与 Agent Loop 预算终止

## Consequences

- 基础设施代码量下降，业务规则仍是主要阅读面。
- 面试讲解重点保持在"为什么这样分流、为什么模型不决定退款"。
