# ADR-0008: 模型接入改用 LangChain（langchain-openai），替换手写 httpx Provider

- **Status**: Accepted
- **Date**: 2026-11-07

## Context

自 ADR-0003 起，模型接入由手写的 `OpenAICompatibleProvider`（httpx 直连
`/chat/completions`、`/embeddings`、`/rerank`）实现。现在要求改用 LangChain /
LangGraph 的官方模型接入方案（`langchain-openai`），不再自己写 HTTP 调用。

## Decision

- Chat 走 `langchain_openai.ChatOpenAI`，Embedding 走
  `langchain_openai.OpenAIEmbeddings`，且关闭
  `check_embedding_ctx_length`，避免 tiktoken 对非 OpenAI 模型做错误的客户端
  分词；硅基流动是 OpenAI 兼容端点，二者直接可用。
- `/v1/rerank` 不是 OpenAI 标准端点，LangChain 无对应实现（community 里的
  reranker 是本地 Cross-Encoder，与 ADR-0003 的"镜像精简"冲突），因此 Rerank
  保留最小 httpx 调用，并在代码注释中说明原因。
- 对外边界不变：`ModelProvider` Protocol（同步 `chat/embed/rerank` 与
  `RerankResult`）、`get_provider / set_provider / use_provider /
  describe_provider` 全部保持原样；LangChain 只作为 Provider 内部的模型客户端，
  业务调用点、Mock 边界（只 mock Chat）与 OfflineProvider（ADR-0006/0007）语义不变。
- 环境变量（`MODEL_PROVIDER / MODEL_API_KEY / MODEL_BASE_URL / MODEL_CHAT_MODEL /
  MODEL_EMBEDDING_MODEL / MODEL_EMBEDDING_DIMENSIONS / MODEL_RERANK_MODEL /
  MODEL_TIMEOUT_SECONDS`）保持不变，新增依赖 `langchain-openai`。
- 旧的手写实现 `OpenAICompatibleProvider` 按约定保留不删，但 `create_provider()`
  不再引用它；回滚时把 factory 换回该类即可。其 transport 测试继续保留执行。

## Consequences

- 依赖新增 `langchain-openai==1.7.4`（连带 `openai`、`tiktoken`），
  `langchain-core` 维持 1.7.6。
- LangChain 模型客户端的构造失败时机与手写 httpx 不同：`openai>=2.x` 在构造
  client 时即校验 api_key，故在未配置 key 时用占位值构造，真正的清晰报错由
  `ProviderError("MODEL_API_KEY 未配置…")` 在调用前给出（与旧行为一致）。
- 新 Provider 的测试用 langchain-core 自带的 fake 模型
  （`FakeListChatModel` / `DeterministicFakeEmbedding`）注入，评测与 CI 仍无
  网络可运行。
