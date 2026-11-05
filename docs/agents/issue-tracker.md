# Issue tracker: GitHub

本仓库的 issue 和 spec 都以 GitHub issue 的形式存在。所有操作统一使用 `gh` CLI。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`。多行 body 使用 heredoc。
- **读取 issue**：`gh issue view <number> --json number,title,body,labels,comments`。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，按需加 `--label` 和 `--state` 过滤。
- **把 issue 设为某个父 issue 的 sub-issue**：`gh issue create --parent <parent> ...`，或事后执行 `gh issue edit <parent> --add-sub-issue <child>`（`gh` 2.94+）。旧版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`（数据库 id，见下文 **Blocking**）。若 sub-issue 不可用，则在子 issue body 顶部写 `Part of #<parent>`。
- **Blocking**：使用 GitHub 原生的 **issue dependencies**，这是标准且可在 UI 中看到的表示方式。用 `gh issue create --blocked-by <n>,<n> ...` 或 `gh issue edit <child> --add-blocked-by <blocker>`（`gh` 2.94+）添加依赖边。旧版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是 blocker 的数字 **数据库 id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，**不是** `#number` 或 `node_id`）。若 dependencies 不可用，则退化为在子 issue body 顶部写 `Blocked by: #<n>, #<n>`。当所有 blocker 都关闭时，该 ticket 视为 unblocked。
- **统计未关闭的 blocker 数量**：`gh api repos/<owner>/<repo>/issues/<n> --jq .issue_dependencies_summary.blocked_by`（只统计未关闭的 blocker；结果为 0 表示可以开工）。`gh issue view <n> --json blockedBy` 也会列出已关闭的 blocker，因此需要过滤：`--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'`。
- **评论 issue**：`gh issue comment <number> --body "..."`
- **添加 / 移除 label**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：先用 `gh issue comment <number> --body "..."` 发布说明，再 `gh issue close <number>`。对已关闭的 issue，`gh issue close --comment` 会丢弃评论但仍以 0 退出。

仓库从 `git remote -v` 推断；在 clone 目录内运行时 `gh` 会自动识别。

只向用户自己的仓库提交 issue。除非用户拥有 `mattpocock/skills`（这些 skills 的来源仓库），否则绝不向该仓库提交 issue。在 fork 中，`gh` 可能默认指向 upstream 父仓库，因此每次会话第一次执行 `gh issue create` 之前，先用 `gh repo view --json nameWithOwner` 确认目标仓库；如果不是用户的仓库，需与用户确认。

## 把 PR 作为 triage 入口

**PRs as a request surface: no.** _（若本仓库把外部 PR 视为 feature request，则改为 `yes`；`/triage` 会读取该标志。）_

当设为 `yes` 时，PR 与 issue 走相同的 label 和状态流程，使用对应的 `gh pr` 命令：

- **读取 PR**：`gh pr view <number>`，然后 `gh pr view <number> --comments`（非 TTY 环境下 `--comments` 只打印评论、不含 body），以及 `gh pr diff <number>` 获取 diff。
- **列出待 triage 的外部 PR**：`gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **评论 / 加 label / 关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 issue 与 PR 共用同一套编号，因此裸写的 `#42` 可能是两者之一：先用 `gh pr view 42` 解析，失败再回退到 `gh issue view 42`。

## 当某个 skill 说「publish to the issue tracker」

创建一个 GitHub issue。

## 当某个 skill 说「fetch the relevant ticket」

按上文 **读取 issue** 的方式读取。

## Wayfinding 操作

供 `/wayfinder` 使用。**map** 是一个 issue，其 **child** issue 即各个 ticket。

- **Map**：一个带 `wayfinder:map` label 的 issue，承载 Notes / Decisions-so-far / Fog 的 body。`gh issue create --label wayfinder:map`。
- **Child ticket**：作为 map 的 sub-issue 关联的 issue（见 **把 issue 设为某个父 issue 的 sub-issue**）。若 sub-issue 未启用，则把 child 加入 map body 中的 task list，并在 child body 顶部写 `Part of #<map>`。Label 使用 `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。一旦被认领，ticket 分配给负责的开发者。
- **Blocking**：见 **约定** 下的 **Blocking**。
- **Frontier 查询**：列出 map 下所有未关闭的 child（`gh issue list --state open`，范围限定为 map 的 sub-issues / task list），剔除存在未关闭 blocker 的（见 **统计未关闭的 blocker 数量**，或 `Blocked by` 行中仍有未关闭 issue）以及已有 assignee 的；按 map 顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`，这是本会话的第一次写操作。
- **解决**：`gh issue comment <n> --body "<answer>"`，然后 `gh issue close <n>`，最后把 context 指针（gist + 链接）追加到 map 的 Decisions-so-far。
