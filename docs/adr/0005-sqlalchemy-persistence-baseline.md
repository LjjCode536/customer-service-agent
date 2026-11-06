# ADR-0005: SQLAlchemy 持久化基线与双数据库支持

- **Status**: Accepted
- **Date**: 2026-11-06

## Context

教学版使用手写 `sqlite3` 连接与内联 SQL。它无法直接支持 Postgres，容易把多步写入拆成多个自动提交，也没有稳定的索引与实体定义。项目需要保留本地零依赖演示，同时让部署侧能切换到 Postgres。

## Decision

- 使用 SQLAlchemy 2.0 ORM 定义 Customer、Order、Logistics、Knowledge、RefundRequest、Ticket、Task、TaskEvent、ApprovalRecord、TraceStep、AuditLog、BadCase。
- 默认 `DATABASE_URL` 为空时由 `DATABASE_PATH` 构造 SQLite URL；填入 `postgresql://` 时自动使用 `psycopg` 驱动。
- 用 `Base.metadata.create_all()` 初始化 schema，不引入 Alembic；当前阶段由 seed 与全新的本地数据库保证结构一致。
- 每个逻辑写入单元（退款插入 + 订单状态、Task + Event、审批决策、seed）通过 `session_scope()` 组成单个事务，异常整体回滚。Workflow 跨步骤失败时依靠退款幂等键安全重试，而不是长时间持有事务。
- 退款写入使用 `INSERT ... ON CONFLICT DO NOTHING` 语义，依赖 `idempotency_key` 的 UNIQUE 约束消除先查后插竞态。
- Knowledge 增加 `embedding`、`embedding_model`、`embedding_generated_at`，把向量与生成它的模型绑定，避免模型切换后误用缓存。
- 为 Task 状态、审批顺序、Trace 查询、工单查询与幂等键建立索引。

## Consequences

- 本地仍是一条命令启动，部署只需改环境变量。
- 没有迁移工具，修改已有表结构需要重建本地 SQLite 或手工迁移；这在当前单 Worker、演示优先的阶段是可接受的取舍。
- 后续 Embedding / RAG ticket 可以直接读写缓存字段，不需要再改表。
