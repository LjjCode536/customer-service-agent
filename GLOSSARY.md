# Glossary

本项目统一使用的领域词汇。issue、spec、ADR、测试与面试话术都应使用这里的定义。

## 入口与分流

- **Customer Service Agent（客服 Agent）**：面向客服/售后场景的对话入口，负责理解诉求并选择 RAG、Tool 或 Workflow。
- **Intent Router（意图路由）**：把用户请求分流到 `faq`、`order_query`、`logistics_query`、`refund`、`complaint`、`chat` 的确定性组件。它不是 Agent Loop。
- **Direct RAG**：FAQ、退款政策、物流 SOP 等知识问题直接检索并回答，不创建 Task。
- **Direct Tool**：订单、物流等确定性实时事实直接调用业务 Tool，不进入 LLM Agent Loop。
- **Dispatch（统一入口）**：把一条用户消息分流到 Direct RAG、Direct Tool、Agent Loop 或 Workflow 的单一入口；HTTP API 与评测共用它。
- **Workflow**：退款、投诉等多步骤、有分支、可能有副作用的流程，创建后台 Task 后由 Worker 驱动。

## 检索

- **Knowledge（知识）**：退款政策、物流 SOP、发票说明等相对稳定的规则文本。
- **Active Version（有效版本）**：`status=active` 且 `effective_from <= now` 的知识版本；历史版本不参与召回。
- **Hybrid Retrieval（混合召回）**：BM25 词法召回与向量语义召回并行执行。
- **RRF（Reciprocal Rank Fusion）**：按排名而非分数融合两路召回结果。
- **Rerank（精排）**：用 Cross-Encoder 对粗排候选重新打分，返回最终 Evidence。
- **Evidence（证据）**：进入回答或规则判断的检索结果，必须带标题、版本与正文。

## 安全与业务事实

- **Security Context（安全上下文）**：由服务端从请求头解析出的 `user_id / tenant_id / role`，Tool 只能信任它，不能信任模型传入的身份字段。
- **Tool Runtime（工具运行时）**：执行 Order / Logistics / Refund / Ticket 操作的边界层，负责归属校验、租户隔离与错误映射。
- **Order Ownership（订单归属）**：订单的 `user_id` 与 `tenant_id` 必须与 Security Context 一致；客服角色可代查，客户不可越权。

## 退款与审批

- **Eligibility（退款资格）**：由确定性规则判断是否可退，例如 `paid + not_shipped = full_refund`。
- **Risk Level（风险等级）**：基于金额、VIP、重复退款等信号计算 `low / medium / high`；LLM 不参与定级。
- **Human Review（人工审核）**：高风险退款进入的人工节点，暂停 Workflow 等待审批。
- **Approval Chain（审批链）**：高风险退款的审批角色顺序，例如 `refund_specialist → service_manager`。
- **Sequence No（审批序号）**：审批链中的顺序号；越级审批返回 409。
- **Resume（恢复）**：审批完成后，Worker 用 LangGraph `Command(resume=...)` 从暂停点继续执行。
- **Idempotency Key（幂等键）**：由 `tenant_id + order_id` 构造的退款唯一键，配合数据库 UNIQUE 约束防止重复退款。
- **Refund Request（退款单）**：一次退款副作用的持久化记录，包含外部退款号与状态。

## 工单与 SLA

- **Ticket（工单）**：客服问题在工单系统中的记录，包含分类、优先级、指派组与 SLA。
- **Priority（优先级）**：`P0–P3`，由问题类型、VIP、投诉、物流异常时长等确定性规则计算。
- **SLA**：工单的响应/处理时限，由优先级推导。
- **Assignment Group（指派组）**：`logistics_support / after_sales / complaint_specialist` 等客服组。
- **Complaint Path（投诉路径）**：投诉请求在 Workflow 中的独立分支：加载客户/订单/物流、检索物流 SOP、评估 VIP 与物流异常信号、创建投诉工单并回答；不进入退款节点。

## 模型接入

- **ModelProvider（模型 Provider）**：统一暴露 Chat、Embedding、Rerank 三个能力的接入层；默认指向 OpenAI 兼容的硅基流动端点，可通过环境变量切换。
- **Mock Boundary（Mock 边界）**：Mock Mode 下只有 Chat 输出被替换，Embedding、Rerank 与业务事实仍走真实链路；边界以 Provider 层为唯一实现点。

## 任务运行时

- **Task（任务）**：一次后台 Workflow 执行的持久化实体，具有状态、当前节点、结果与错误。
- **Event Store（事件存储）**：节点事件的持久化记录；SSE 只读它，不驱动任务。
- **Worker**：独立消费 Task 的进程；浏览器断开不影响任务执行。
- **Trace（链路）**：一次任务内各节点的输入、输出、耗时与错误记录，可按 `trace_id` 回放。
- **Audit（审计）**：对退款、审批等有副作用动作的不可变记录。

## Agent Loop 与评测

- **Agent Loop**：只读的受预算约束工具循环；只在意图不明确或多步查询时启用。
- **Budget（预算）**：Agent Loop 的 `max_steps / max_tool_calls / max_tokens` 上限，耗尽即终止并降级。
- **Fallback**：LangGraph 不可用时的确定性降级路径，保持相同节点语义，仅用于测试与演示。
- **Golden Dataset（黄金数据集）**：覆盖物流异常、低风险退款、高额退款、重复退款、VIP 投诉与 FAQ 的端到端评测用例。
- **Evaluation Sandbox（评测沙箱）**：每次评测创建的临时 SQLite 数据库与临时 checkpoint，评测不读写业务库。
- **Offline Provider**：Golden Dataset 使用的确定性离线 ModelProvider，Chat、Embedding、Rerank 都不访问网络。
- **Bad Case（坏例）**：线上或评测中发现的失败样本，通过 `trace_id` 关联 Trace，记录错误类型、根因、期望结果与修复状态。
- **Mock Mode**：默认演示模式，只模拟文本生成；检索与业务事实仍走真实链路。
