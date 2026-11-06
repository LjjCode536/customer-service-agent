# ADR-0003: 统一使用 OpenAI 兼容 Provider，默认硅基流动模型

- **Status**: Accepted
- **Date**: 2026-11-05

## Context

教学版使用 `deepseek-v4-flash`，不是有效模型名，且本地未安装 LangGraph。项目需要一个可运行、可公开展示、成本可控的模型接入方式。

## Decision

- 通过统一的 OpenAI 兼容 Provider 接入，base_url 默认 `https://api.siliconflow.cn/v1`。
- Chat 模型：`Qwen/Qwen3-8B`。
- Embedding 模型：`BAAI/bge-m3`（1024 维）。
- Rerank 模型：`BAAI/bge-reranker-v2-m3`，通过 `/v1/rerank` 调用。
- DeepSeek 或其他兼容服务可通过环境变量切换，不写死供应商。
- Mock Mode 只模拟文本生成；Embedding 与 Rerank 始终走真实 API。测试注入 fake provider，不访问网络。
- 另设显式 `MODEL_PROVIDER=offline` 供无 key 的 Compose / 评测使用，详见 ADR-0007；它不改变 Mock Mode 的语义。

## Consequences

- 不再需要本地下载 embedding 模型，镜像保持精简。
- 公网 Demo 依赖 SiliconFlow 可用性与免费额度。
- API key 只放 `.env`，不进入仓库。
