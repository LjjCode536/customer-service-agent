# Customer Service & Ticket Copilot

面向电商 / SaaS 售后场景的企业智能客服与工单 Agent：把**知识问答**、**实时业务查询**和**有副作用的退款/投诉流程**拆成三条路径，而不是把所有请求都塞进一个 LLM Agent Loop。

> 状态：教学版重构已完成，`main` 为可运行、可演示、可测试的版本。阶段进度见 [Roadmap](#roadmap)，决策见 `docs/adr/`。

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
    R -->|意图不明 / 多步只读查询| LOOP[Read-only Agent Loop<br/>max_steps / max_tool_calls / max_tokens]
    LOOP --> T
    LOOP --> RAG
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
| `MODEL_PROVIDER` | `siliconflow` | Provider 选择：真实 OpenAI 兼容服务或显式 `offline` |
| `MODEL_API_KEY` | 空 | 真实 Provider 必需；Chat / Embedding / Rerank 共用 |
| `MODEL_BASE_URL` | `https://api.siliconflow.cn/v1` | OpenAI 兼容 base URL |
| `MODEL_CHAT_MODEL` | `Qwen/Qwen3-8B` | Chat 模型 |
| `MODEL_EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding 模型 |
| `MODEL_EMBEDDING_DIMENSIONS` | `1024` | Embedding 维度；返回向量维度不一致时显式报错 |
| `MODEL_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Rerank 模型 |
| `MODEL_TIMEOUT_SECONDS` | `60` | Provider 请求超时 |

另有一个**显式的离线模式** `MODEL_PROVIDER=offline`：Chat、Embedding、Rerank 都由确定性 `OfflineProvider` 提供，不访问网络，适合没有 API key 的容器冒烟与 Golden Dataset。它不会静默替换真实 Provider；Docker Compose 在未设置 `MODEL_PROVIDER` 时使用该模式，填入真实 key 后仍走 SiliconFlow。

切换到其他 OpenAI 兼容服务只需改环境变量。Chat 可以切到 DeepSeek：

```dotenv
MODEL_PROVIDER=deepseek
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_CHAT_MODEL=deepseek-chat
```

但 DeepSeek 本身不提供 `/embeddings` 与 `/rerank`，因此 Embedding / Rerank 必须继续指向支持这两个端点的兼容服务（当前配置三者共用一个 base URL）。只切换 Chat 时，请在代码或部署侧保留原 Embedding / Rerank Provider。

`GET /api/v1/runtime` 返回当前 Provider、三个模型名、调用边界、API key 是否配置、LangGraph 可用性、数据库路径与数据库类型。`mock_boundary` 为 `chat: mock / embedding: live / rerank: live` 或全部 `offline`，不代表凭据一定可用；非离线模式下 `api_key_configured=false` 时真实 Embedding / Rerank 会显式报错而不是返回伪造向量。测试通过 `set_provider()` 注入 fake Provider，不访问网络、不读取真实 key。

## 混合检索

FAQ 请求走真实检索链路（`backend/app/rag/hybrid.py`）：

1. 只加载 `status=active` 且 `effective_from <= now` 的知识版本。
2. `jieba` 分词后，用 `rank_bm25` 的 `BM25Okapi` 做词法召回。
3. 用 BGE-M3 计算 query 向量，与入库时缓存的 Knowledge embedding 做 numpy cosine 召回。
4. 两路各取候选，用 RRF（`k=60`）按排名融合。
5. 融合候选交给 BGE Reranker Cross-Encoder 精排，返回稳定排序的 Evidence。

Evidence 包含 `title / version / content / bm25_score / vector_score / rrf_score / rerank_score` 以及两路排名。FAQ 响应同时返回 `citations` 和完整 `evidence`。Provider 失败时接口返回 503 或任务进入 failed，不会退回字符重合度或伪造向量。

## 文档上传与入库

`manager` 可在 Knowledge Center 上传 `.txt` / `.md` / `.pdf` / `.docx`，或为已有文档上传新版本。`POST /api/v1/documents` 与 `POST /api/v1/documents/{document_id}/versions` 立即返回 `202`，前后端都不等待解析：

- 原始文件按 SHA-256 内容寻址写入 `DOCUMENT_STORAGE_DIR`（默认 `./data/documents`；Compose 挂载在 `/app/data` volume）；原始文件名只用于展示。
- 单文件上限 20MB，扩展名与文件头双重校验；不支持格式返回 `415`，超大文件返回 `413`。
- Worker 以版本状态作为入库队列：`draft → processing → ready / failed`，解析后缓存整篇 embedding。失败版本记录 `error_code / error_message`，可在前端直接重试，复用原文件、无需重新上传。
- 同租户相同 SHA-256 返回 `409` 与已存在文档信息；`force=true` 允许作为新文档或新版本继续。
- 上传、版本创建、解析失败与重试均写 Audit；写操作只允许 `manager`，跨租户返回 `404`。

解析是独立纯函数 seam（`backend/app/documents/parsing.py`）：文本优先 UTF-8、回退 GB18030、去除 BOM、统一换行；PDF 只提取文本层，加密 PDF 返回 `encrypted_pdf`，无文本层返回 `no_extractable_text`，明确不支持 OCR；DOCX 按正文顺序提取段落与表格，忽略图片、页眉与页脚。解析超过 `DOCUMENT_PARSE_TIMEOUT_SECONDS`（默认 60 秒）返回 `parse_timeout`；失败版本保留原文件，可直接重试而无需重新上传。

## 只读 Agent Loop

`backend/app/services/agent_loop.py` 实现一个只读、有预算的 Agent Loop，用于意图不明确或多步只读查询。响应 `mode=agent_loop`，同时返回 `trace_id`、`stop_reason`、`handoff`、`steps`、`tool_calls`、`tokens_used` 与预算快照。

**触发条件**（满足任一）：

- Router 结果为 `chat`（无法归入 FAQ / 订单 / 物流 / 退款 / 投诉）；
- Router 置信度低于 `AGENT_LOOP_CONFIDENCE_THRESHOLD`（默认 `0.7`）：规则命中的意图为 `0.98`，由模型猜测的意图只有 `0.6`，不可信的二次路由会回到 Loop；
- 多步只读查询：带订单号，且同时包含业务事实与知识问题（例如“订单 O10001 到哪了，顺便问下发票怎么开”）。

`refund` / `complaint` 永远不会进入 Loop，仍由 Rule Engine + LangGraph Workflow 处理。

**只读边界**：Loop 的调度表只注册三个工具——`get_order`、`get_logistics`、`retrieve_knowledge`。`submit_refund`、`create_ticket` 或任何订单修改调用都会抛出 `AGENT_LOOP_TOOL_NOT_ALLOWED`，因此 Loop 不可能产生副作用。

**预算与配置**：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_LOOP_MAX_STEPS` | `4` | 最多执行多少个工具步骤 |
| `AGENT_LOOP_MAX_TOOL_CALLS` | `6` | 最多工具调用次数 |
| `AGENT_LOOP_MAX_TOKENS` | `1500` | token 预算（确定性估算，用于演示与测试） |
| `AGENT_LOOP_CONFIDENCE_THRESHOLD` | `0.7` | Router 置信度低于该值时进入 Loop |

**停止条件**：证据充分、无需继续查询时正常结束；预算耗尽（`budget_exhausted_steps / budget_exhausted_tool_calls / budget_exhausted_tokens`）、证据不足（`insufficient_evidence`）、工具失败（`tool_failure`）或用户明确要求转人工（`needs_human`）时立即终止，设置 `handoff=true` 并给出降级回答/转人工提示；意图不明确时返回澄清问题（`clarification`），打招呼则返回引导话术。响应 `status` 为 `completed`（证据充分）或 `degraded`（其余终止原因），真实异常不会混在 `error` 字段里返回。无论哪种终止，都不会继续循环，也不会在 Loop 内执行补偿性副作用。

每个工具步骤写一条 Trace（`agent_loop.tool.get_order` / `agent_loop.tool.get_logistics` / `agent_loop.tool.retrieve_knowledge`），终止时写 `agent_loop.stop`，可通过 `GET /api/v1/traces?trace_id=<trace_id>` 按链路回放。订单归属校验失败仍保持 `403`、订单不存在保持 `404`，与直连 Tool 路径一致，不会被当作普通工具失败吞掉。`GET /api/v1/runtime` 也会返回当前预算与允许的工具列表。

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
| FAQ | 电子发票怎么开 | 走 RAG，引用当前有效版本《电子发票开具说明》 |

这 6 个 Case 都出现在 Agent Lab 的 Mock Case 下拉框与 `backend/app/eval/golden_dataset.py`；`mock_data/*.json` 是 5 个业务 Case 的可读输入快照，运行时以 `backend/app/fixtures/mock_data.py` 与 Golden Dataset 为准。

低风险退款等副作用 Case 在同一个数据库里只应成功一次；重复运行会命中幂等/`DUPLICATE_REFUND_REQUEST` 路径，这也是演示点。需要重置时执行 `python -m app.scripts_seed`，Docker 环境重启 API 容器即可重新 seed。

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

## 前端 Console

前端数据层使用 TanStack Query 统一请求、缓存与轮询；SSE 通过 `@microsoft/fetch-event-source` 携带 Security Context 头订阅任务事件；Zustand 管理用户、租户、角色与 Mock/Real 上下文。顶栏可切换 Security Context，`/chat` 等读取上下文的接口会立即使用新身份；Dashboard、Trace 等全局运营视图是演示数据，不按租户过滤。

三个重点页面：

- **Dashboard**：工单优先级、退款金额、待审批、SLA 风险（超时 / 临近 / 正常），每 10 秒轮询刷新。
- **Agent Lab**：选择 Mock Case 或输入自定义消息，运行后实时展示 Workflow 节点时间线、任务状态与结果，并可一键跳转 Trace。
- **Trace Explorer**：按 `trace_id` 查看节点耗时柱状图、执行顺序，以及每个节点的输入、输出与错误。

![Dashboard](docs/screenshots/dashboard.png)

![Agent Lab](docs/screenshots/agent-lab.png)

![Trace Explorer](docs/screenshots/trace-explorer.png)

## Docker Compose

无 API key 也能直接启动：Compose 在 `MODEL_PROVIDER` 未设置时使用显式离线模式，运行完整 Router / Tool / Workflow / SSE 链路。

```bash
docker compose up --build
```

前端 `http://localhost:3000`，API `http://localhost:8000`，Swagger `http://localhost:8000/docs`。

要使用真实 Embedding / Rerank，先准备 `.env`，再启动：

```bash
cp .env.example .env        # 填写 MODEL_API_KEY，保留 MODEL_PROVIDER=siliconflow
docker compose up --build
```

PyPI 访问较慢时可以先执行 `docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`，再 `docker compose up`。

如果是从旧版本升级且数据库 schema 已变化，先执行 `docker compose down -v` 清空旧的 `service_data` volume（会同时清空演示数据），再重新启动。

即使先执行了 `cp .env.example .env` 但没填 key，API / Worker 镜像入口也会在检测到 `MODEL_API_KEY` 为空时显式切换为 `MODEL_PROVIDER=offline` 并打印提示；填了 key 则继续使用 `.env` 中的真实 Provider。`scripts/start-mac.sh` 与 `scripts/start-windows.ps1` 在启动前做同样的判断。SQLite 数据库与 LangGraph checkpoint 放在 `service_data` volume；API 容器启动时会执行 `scripts_seed` 重建演示数据，因此重启后工单、退款与任务会回到 seed 状态，这是教学版的可重复演示取舍。

## 目录

```
backend/
  app/
    api/          # HTTP 接口、SSE
    core/         # 配置、Security Context
    db/           # SQLAlchemy engine、session、models 与 create_all
    documents/    # 上传、解析、内容寻址存储与异步入库
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
- Refund 幂等与重复申请拦截

`GET /api/v1/evaluation` 会为 5 类核心场景 + FAQ 各跑一遍完整的
Router → Task → Worker → Workflow 链路，断言 `mode / intent / ticket / refund /
approval / idempotency`。评测在临时 SQLite 沙箱和 `OfflineProvider` 中运行，
无网络、无真实 key、不读写业务库，也不等待 SSE。

失败样本可通过 `POST /api/v1/bad-cases` 登记；`GET /api/v1/bad-cases`（支持
`trace_id` 过滤）、`GET /api/v1/bad-cases/{id}` 查询，`PATCH /api/v1/bad-cases/{id}`
回填根因、期望结果和 `fixed` 状态。

## 验证

```bash
cd backend && python -m pytest -q          # 167 个测试，含上传入库、解析失败重试、RAG 召回与预算终止
cd frontend && npm ci && npm run build     # TypeScript 类型检查 + 生产构建
curl http://localhost:8000/api/v1/evaluation   # 5 个核心 Case + FAQ 的端到端评测
docker compose up --build                  # API / Worker / 前端
```

## Roadmap

- [x] 阶段 0：README、GLOSSARY、ADR、基线提交
- [x] 阶段 1：SQLAlchemy、SiliconFlow Provider、真 BM25 / Embedding / Rerank、SSE / Trace
- [x] 阶段 2：LangGraph 节点重构、interrupt + checkpointer、原子幂等
- [x] 阶段 3：只读 Agent Loop + 预算终止
- [x] 阶段 4：Golden Dataset 端到端评测、Bad Case 闭环
- [x] 阶段 5：前端 3 个重点页面 + 其余 6 页可用性
- [x] 阶段 6：重复 Resume、越级 409、RAG 召回、预算终止等回归测试
- [x] 阶段 7：最小 CI（GitHub Actions：ruff、pytest、前端构建、compose 镜像构建，PR 与 main 触发）
- [ ] 阶段 8：公网 Demo 与 HTTPS（暂缓）

## 后续部署计划

当前版本定位为单机教学版，公网部署按以下顺序推进：

1. 持久化：切换 Postgres，并把 LangGraph checkpointer 从 SQLite 换成 Postgres Saver，解除单 Worker 限制。
2. 配置与密钥：由部署平台注入 `MODEL_API_KEY` 等变量，不把 `.env` 打进镜像；启动时不再自动 seed。
3. 任务消费：单 Worker 循环替换为队列或多副本消费者，保留原子领取语义。
4. CI：已上线最小 CI（`.github/workflows/ci.yml`：ruff、pytest、`npm ci && npm run build`、compose 镜像构建）；评测作为独立 job 后续补上。
5. 入口：补 HTTPS、反向代理、限流与 CORS 白名单；SSE 继续关闭代理缓冲。
6. 可观测性：接入结构化日志与指标，Trace / Audit 保持现有表结构。

## 已知限制

- 教学版单租户、单 Worker，未做 RBAC 与多副本调度
- SQLite 为默认存储；Postgres 通过 `DATABASE_URL` 切换。SQLite checkpointer 只适合单 Worker，多副本需换 Postgres checkpointer
- Docker Compose 默认使用显式离线 Provider，便于无 key 启动；真实检索质量依赖 SiliconFlow 的可用性与免费额度
- 没有 Alembic，修改已有表结构需要重建本地 SQLite 或手工迁移
- Compose 启动会执行 seed 并重建演示数据，不能直接当作生产部署方案
- 公网 Demo、HTTPS 与域名暂缓；本地与 Compose 是当前验收路径（lint / 测试 / 构建已由 GitHub Actions 在 PR 上验证）
- 退款网关为状态模拟，未对接真实支付/退款渠道
- 评测验证业务链路与副作用，不评价真实模型的最终话术质量

## License

MIT
