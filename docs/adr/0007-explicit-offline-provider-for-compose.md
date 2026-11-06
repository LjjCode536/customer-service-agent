# ADR-0007: Docker Compose 默认使用显式离线 Provider

- **Status**: Accepted
- **Date**: 2026-11-06

## Context

`docker compose up --build` 是验收与面试演示的第一入口。项目同时要求 Mock Mode
只替换 Chat，Embedding / Rerank 走真实 API；而真实 API 需要 `.env` 中的
`MODEL_API_KEY`，公开仓库不能提交密钥。全新克隆如果直接启动，seed 会在真实
Embedding 处失败，API 无法就绪。

## Decision

- 新增显式的 `MODEL_PROVIDER=offline`：`create_provider()` 返回确定性的
  `OfflineProvider`，Chat、Embedding、Rerank 都不访问网络。
- Docker Compose 在 `MODEL_PROVIDER` 未设置时使用 `offline`；设置
  `MODEL_PROVIDER=siliconflow` 并填写 `MODEL_API_KEY` 后仍走真实 SiliconFlow。
- `.env` 在 Compose 中可选；镜像入口与启动脚本都会在 `MODEL_API_KEY` 为空时
  把本次启动显式切换为 `offline` 并打印提示，因此即使已经复制了
  `.env.example` 也不会在 seed 阶段失败。
- 离线模式不改变 Mock Mode 语义：Mock Mode 仍然只模拟 Chat；离线模式是独立的
  演示/冒烟模式，`/api/v1/runtime` 的 `mock_boundary` 会明确返回
  `chat/embedding/rerank = offline`。
- 离线 Embedding 按配置维度确定性扩展 SHA-256 摘要，保证 seed 缓存与查询向量
  维度一致；评测继续使用同一个 Provider 实现。

## Consequences

- 无 key 的全新环境可以直接启动并复现 5 个 Demo Case + FAQ。
- 真实检索演示只需改环境变量，不需要改代码或 Compose 文件。
- 离线 Embedding 只保证链路可运行与排序稳定，不代表真实语义检索质量；README
  在已知限制中明确这一点。
