# Domain Docs

说明工程类 skills 在探索代码库时，应如何消费本仓库的领域文档。

## 探索前先读这些

- 仓库根目录的 **`GLOSSARY.md`**，或者
- 仓库根目录的 **`GLOSSARY-MAP.md`**（如果存在）：它指向每个上下文各自的 `GLOSSARY.md`。读取与当前话题相关的每一个。
- **`docs/adr/`**：读取与即将改动的区域相关的 ADR。在多上下文仓库中，还要检查 `src/<context>/docs/adr/` 中的上下文级决策。

如果这些文件不存在，**静默继续**。不要指出它们缺失，也不要主动建议创建。`/domain-modeling` skill（通过 `/grill-with-docs` 和 `/improve-codebase-architecture` 触达）会在术语或决策真正需要落定时按需创建它们。

## 文件结构

单上下文仓库（大多数仓库）：

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录存在 `GLOSSARY-MAP.md`）：

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← 系统级决策
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← 上下文级决策
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## 使用 glossary 的词汇

当你的产出需要命名某个领域概念时（issue 标题、重构提案、假设、测试名称），使用 `GLOSSARY.md` 中定义的术语。不要漂移到 glossary 明确避免的同义词。

如果你需要的概念还不在 glossary 中，这本身是个信号：要么你在发明项目并不使用的语言（重新考虑），要么确实存在缺口（记录下来交给 `/domain-modeling`）。

## 标记 ADR 冲突

如果你的产出与既有 ADR 冲突，显式指出，而不是悄悄覆盖：

> _与 ADR-0007（event-sourced orders）冲突，但值得重新讨论，因为……_
