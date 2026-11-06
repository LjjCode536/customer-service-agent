# Customer Service & Ticket Copilot

面向电商 / SaaS 售后场景的企业智能客服与工单 Agent：把**知识问答**、**实时业务查询**和**有副作用的退款/投诉流程**拆成三条路径，而不是把所有请求都塞进一个 LLM Agent Loop。

> 状态：教学版基线重构中。`main` 当前为可运行的教学版，目标架构见 [Roadmap](#roadmap) 与 `docs/adr/`。

## 为什么不是普通 AI 客服 Demo

真正难的不是聊天框，而是：

- 订单、物流、退款状态是实时事实，不能进向量库
- 退款能否执行是确定性规则，不能由模型决定
- 退款有副作用，必须幂等
- 高风险退款需要人工审核，且能跨进程恢复
- 任务生命周期不能依赖浏览器连接
- 出问题时要能回放是哪一层错了

## 架构

```mermaid
flowchart LR
    U[用户 / 客服工作台] --> API[FastAPI]
    API --> R[Intent Router]
    R -->|FAQ| RAG[Hybrid RAG<br/>BM25 + BGE-M3 + RRF + Rerank]
    R -->|订单 / 物流| T[Business Tools<br/>Security Context 校验]
    R -->|退款 / 投诉| TASK[Task + Event Store]
    TASK --> W[Worker]
    W --> G[LangGraph Workflow]
    G --> RULE[Rule Engine<br/>Eligibility / Risk]
    RULE -->|高风险| HITL[Human Review<br/>interrupt + checkpointer]
    RULE -->|低风险| REFUND[Refund Submit<br/>Idempotency Key]
    HITL -->|Command resume| REFUND
    G --> TRACE[Trace / Audit]
    API --> SSE[SSE]
    SSE --> UI[React Console]
```

## 核心设计

| 决策 | 原因 |
|---|---|
| 订单/物流直连 Tool | 确定性实时事实，更快、更便宜、更稳定 |
| 退款/投诉进 Workflow | 多步骤、有分支、可能有人工审核和副作用 |
| 模型只做理解与解释 | 资格、金额、优先级必须确定性、可审计 |
| `refund:{tenant_id}:{order_id}` + UNIQUE | 防止重试、重复 Resume 造成重复退款 |
| Task 落库 + Worker 独立执行 | 浏览器断开不中止后台任务 |
| LangGraph `interrupt` + checkpointer | 人工审核暂停点可持久化、可恢复 |

详细决策见 `docs/adr/`，术语见 [GLOSSARY.md](GLOSSARY.md)。

## 模型接入

Chat、Embedding、Rerank 统一通过 `ModelProvider`（`backend/app/llm/provider.py`）访问，默认使用 OpenAI 兼容的硅基流动端点：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MOCK_MODE` | `true` | 只替换 Chat 输出；Embedding 与 Rerank 仍调用真实 Provider |
| `MODEL_PROVIDER` | `siliconflow` | Provider 标签，仅用于展示与日志 |
| `MODEL_API_KEY` | 空 | 真实 Provider 必需；Chat / Embedding / Rerank 共用 |
| `MODEL_BASE_URL` | `https://api.siliconflow.cn/v1` | OpenAI 兼容 base URL |
| `MODEL_CHAT_MODEL` | `Qwen/Qwen3-8B` | Chat 模型 |
| `MODEL_EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding 模型 |
| `MODEL_EMBEDDING_DIMENSIONS` | `1024` | Embedding 维度；返回向量维度不一致时显式报错 |
| `MODEL_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Rerank 模型 |
| `MODEL_TIMEOUT_SECONDS` | `60` | Provider 请求超时 |

切换到其他 OpenAI 兼容服务只需改环境变量。Chat 可以切到 DeepSeek：

```dotenv
MODEL_PROVIDER=deepseek
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_CHAT_MODEL=deepseek-chat
```

但 DeepSeek 本身不提供 `/embeddings` 与 `/rerank`，因此 Embedding / Rerank 必须继续指向支持这两个端点的兼容服务（当前配置三者共用一个 base URL）。只切换 Chat 时，请在代码或部署侧保留原 Embedding / Rerank Provider。

`GET /api/v1/runtime` 返回当前 Provider、三个模型名、Mock 边界、API key 是否配置、LangGraph 可用性、数据库路径与数据库类型。`mock_boundary` 表示是否模拟调用（`chat: mock`），不代表凭据一定可用；`api_key_configured=false` 时真实 Embedding / Rerank 会显式报错而不是返回伪造向量。测试通过 `set_provider()` 注入 fake Provider，不访问网络、不读取真实 key。

## 混合检索

FAQ 请求走真实检索链路（`backend/app/rag/hybrid.py`）：

1. 只加载 `status=active` 且 `effective_from <= now` 的知识版本。
2. `jieba` 分词后，用 `rank_bm25` 的 `BM25Okapi` 做词法召回。
3. 用 BGE-M3 计算 query 向量，与入库时缓存的 Knowledge embedding 做 numpy cosine 召回。
4. 两路各取候选，用 RRF（`k=60`）按排名融合。
5. 融合候选交给 BGE Reranker Cross-Encoder 精排，返回稳定排序的 Evidence。

Evidence 包含 `title / version / content / bm25_score / vector_score / rrf_score / rerank_score` 以及两路排名。FAQ 响应同时返回 `citations` 和完整 `evidence`。Provider 失败时接口返回 503 或任务进入 failed，不会退回字符重合度或伪造向量。

## 持久化

数据层使用 SQLAlchemy 2.0 ORM（`backend/app/db/models.py`），所有领域实体都有对应模型。默认零依赖使用本地 SQLite，`DATABASE_URL` 可切换到 Postgres：

```dotenv
# 默认：sqlite:///{DATABASE_PATH}
DATABASE_PATH=./data/customer_service.db

# 切换 Postgres
DATABASE_URL=postgresql://user:password@localhost:5432/customer_service
```

- `create_all` 在启动与 `python -m app.scripts_seed` 时补齐缺失表，不引入 Alembic。
- seed 在单个事务内清空并重建演示数据，可重复执行。
- 退款幂等键有 UNIQUE 约束，写入使用 `ON CONFLICT DO NOTHING` 语义。
- Task 状态、审批顺序、Trace 查询、工单查询等热点路径都有索引；多步写入使用 `session_scope()` 事务。
- Knowledge 在 seed 入库时批量计算并缓存 `embedding`、`embedding_model`、`embedding_generated_at`；模型标识变化时自动重算，查询时只计算 query 向量。

退款 Workflow 由 LangGraph 主路径执行，`human_review` 节点用 `interrupt()` 暂停，checkpoint 默认写入 `{DATABASE_PATH}.checkpoints`（SQLite）或 `./data/checkpoints.db`（非 SQLite），可用 `CHECKPOINT_PATH` 覆盖。审批全部通过后 Task 进入 `resume_pending`，Worker 以 `Command(resume=...)` 从暂停点继续。`WORKFLOW_ENGINE=fallback` 只用于测试或 LangGraph 不可用时的显式降级，默认不会静默回退。

## Demo Cases

| Case | 输入 | 预期 |
|---|---|---|
| 物流异常 | `O10001` 运输中 38 小时未更新 | 直连 Tool，自动创建 P2 `logistics_abnormal` 工单 |
| 低风险退款 | `O10002` 399 元未发货 | Rule Engine 判定可退，自动提交退款 |
| 高额退款 | `O10003` 12999 元 VIP 未发货 | 进入两级人工审核，Resume 后提交退款 |
| 重复退款 | `O10004` 已有退款记录 | 返回 `DUPLICATE_REFUND_REQUEST`，不再创建 |
| VIP 投诉 | `O10005` VIP 强投诉 | P1 工单，指派 `complaint_specialist` |
| FAQ | 电子发票怎么开 | 走 RAG，返回当前有效版本 |

## Quickstart

### 本地（Mock Chat + 真实检索，需 API key）

Mock 只替换 Chat 文本生成；Embedding 与 Rerank 始终走真实 Provider。未配置 `MODEL_API_KEY` 时 seed 与 FAQ 请求会显式失败，而不是退回假向量。

```bash
cp .env.example .env        # 填写 MODEL_API_KEY：真实 Embedding / Rerank 必需
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m app.scripts_seed
uvicorn app.main:app --reload --port 8000

# 另开终端：后台 Worker
python -m app.worker
```

```bash
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`，在 Agent Lab 选择 Mock Case 运行。

### Docker Compose

```bash
docker compose up --build
```

前端 `http://localhost:3000`，API `http://localhost:8000`。

## 目录

```
backend/
  app/
    api/          # HTTP 接口、SSE
    core/         # 配置、Security Context
    db/           # SQLAlchemy engine、session、models 与 create_all
    llm/          # ModelProvider：Chat / Embedding / Rerank
    rag/          # 混合检索与精排
    services/     # Router、规则、Tool、Task
    workflow/     # LangGraph 编排
    eval/         # Golden Dataset 评测
    worker.py     # 后台任务消费
frontend/
  src/pages/      # Dashboard、Agent Lab、Ticket、Refund、Review、Trace...
docs/
  adr/            # 架构决策
  agents/         # 协作流程
mock_data/        # 演示输入
```

## 评测

目标是用 Golden Dataset 做端到端评测，而不是只看最终话术：

- Intent 路由
- Retrieval：Active Version、BM25、Embedding、Rerank
- Tool Selection 与参数
- 权限与租户边界
- Eligibility / Risk
- Ticket Route / Priority / SLA
- Human Review 顺序与恢复

## Roadmap

- [x] 阶段 0：README、GLOSSARY、ADR、基线提交
- [ ] 阶段 1：SQLAlchemy、SiliconFlow Provider、真 BM25 / Embedding / Rerank、SSE / 日志
- [ ] 阶段 2：LangGraph 节点重构、interrupt + checkpointer、原子幂等
- [ ] 阶段 3：只读 Agent Loop + 预算终止
- [ ] 阶段 4：Golden Dataset 端到端评测、Bad Case 闭环
- [ ] 阶段 5：前端 3 个重点页面
- [ ] 阶段 6：测试补齐
- [ ] 阶段 7：公网 Demo 与 CI（暂缓）

## 已知限制

- 教学版单租户、单 Worker，未做 RBAC 与多副本调度
- SQLite 为默认存储；Postgres 通过 `DATABASE_URL` 切换
- 公网部署暂缓，Docker Compose 保持可直接部署

## License

MIT
