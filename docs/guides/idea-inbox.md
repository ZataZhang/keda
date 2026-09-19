# Idea Inbox（`tasks/inbox/` 随手想法）

本仓库用 `tasks/inbox/` 承接"随手想法"：先留下原话，再由 AI 维护一份总结。
它是任务生命周期的**第 0 阶段**：

```text
tasks/inbox/              →   tasks/pending/   →   tasks/archive/
随手想法：原话 + 总结            成形的 PRD          已交付的 PRD
```

> 注意区分同名概念：本页讲的是**开发流程**里的 `tasks/inbox/` 约定；
> 产品里跨项目采集想法的 `/ideas` 页面与 `/api/v1/agent-runner/idea-inbox/*`
> 端点见 [Agent Runner](agent-runner.md) 的 Idea Inbox 小节。

## 为什么放在 tasks/inbox/

- `tasks/*` 在模板同步里"永不同步"（`scripts/shared/template/sync_template.sh` 的 `_is_never_synced`），所以每个项目的想法天然私有，不会串到其他派生项目。
- PRD 验收 hook 只校验 `tasks/` 根目录和 `tasks/archive/` 下的 PRD 文件（`hooks/shared/check_prd_acceptance_checklist.py`），**不会扫描 `tasks/inbox/`**，因此这里可以自由记录、无需验收清单。
- 想法成熟后用 PRD 流程升级成 `tasks/pending/` 下的正式 PRD，形成闭环。

## 目录结构

| 路径 | 角色 | 谁来写 | 可否改写 |
|---|---|---|---|
| `tasks/inbox/ideas.md` | 原话引用 + 标注的 AI 背景 | 人（原话）+ AI（背景块）+ 产品 API | **只追加，永不改写** |
| `tasks/inbox/summary.md` | AI 总结 | AI | 可随时重写 / 再生 |
| `tasks/inbox/prd-drafts/` | 待审阅的 PRD 草稿 | `core/use_cases/idea_prd_drafts.py` | 由草稿状态机管理 |

想法的事实来源只有一个：`ideas.md`。`summary.md` 是派生产物，任何时候都可以从 `ideas.md` 重新生成。

## ideas.md 条目格式

解析器是 `src/backend/core/use_cases/idea_inbox.py`，它对标题有硬性要求：

```
## YYYY-MM-DD HH:MM[ · <tag>]
```

- 时间戳必须**零填充到分钟**（`2026-09-20 00:28`）。只写日期（历史遗留的 `## 2026-07-03 · 某标签`）或省略分钟，解析器**认不出这条条目**，它不会出现在 `/ideas` 页面里。
- `· <tag>` 可选。产品写入方会把 tag 用成 `· <source> · <author> (<entry-id>)`；agent 手写条目只给短标签时，解析器回退为 `source=manual`、`author=anonymous`，两者在同一份文件里共存。
- 条目正文只认紧随其后的 `> ...` 引用块（读到下一个标题为止）。

### agent 捕获的条目

`>` 引用块放**用户原话的逐字引用**，其后可跟一个**明确标注为 AI 派生**的背景块：

```markdown
## 2026-09-20 00:28 · prd-multi-agent-split-and-verify

> 我有一个想法，就是把PRD是不是可以拆分成若干个任务，让不同的Agent去做，然后有一个组的Agent去验收。

**AI 派生（非用户原话）**
- 背景/动机：单会话串行执行让 PRD 成为吞吐瓶颈。
- 边界/约束：拆出的任务仍需共享同一份验收标准。
- 待澄清：验收组是独立会话还是评审角色。
```

> **AI 背景块不要用 `>` 引用**。解析器把 H2 到下一个 H2 之间的所有 `> ...` 行并入 `text`，一旦加了引用符号，AI 的归纳就会被 `/ideas` 当成用户原话展示。

### 产品写入的条目

`/ideas` 页面、inbound 与 Feishu 适配器写入的条目长这样（`source` ∈ `frontend` / `inbound` / `feishu` / `manual`）：

```markdown
## 2026-09-20 00:28 · frontend · alice (idea-20260920002800-a1b2c3)

> 想法原文...
```

## 给 AI 的硬性规则

**什么时候写（触发规则）**——默认**不写**，不要把对话变成转写稿：

- **用户明确要求**时才写：`记一下`、`记录想法`、`帮我记下来`、`把这个想法记下来`、`capture this idea`，或等价的请求。
- **某个话题收敛到可以开 PRD 的状态**（有明确主张、边界与动机）时，可以追加一条，并且**必须告诉用户**记了什么（时间戳 + 短标签 + 一句话），方便用户改或删。
- 讨论中途的片段、纯问答、讨论中被否掉的方案、闲聊——都**不写**。用户说"别记 / 不用记"就不写，事后也不要再问。

**怎么写**：

- 在 `ideas.md` **末尾追加**一条新条目，时间戳用当前本地时间（`date "+%Y-%m-%d %H:%M"`）。
- `>` 引用块必须是用户**自己的措辞**，逐字保留。想法横跨多轮对话时，引用其中承载主张的原话；任何归纳都放进 AI 背景块。**不得编造引用**——没有可引用的用户原话就不要建条目。
- **禁止**编辑、合并、删除、重排或"优化"已有条目（包括其中的 AI 背景块）。原话是证据，不是草稿。
- 想法后续演进了就**追加新条目**并引用早先的时间戳，不要回头改写旧条目。
- 跨条目的聚类与评论放在 `summary.md`，不要混进 `ideas.md`。
- 一次对话通常一个想法只产出一条，不要把同一个想法拆成多条碎片。

## summary.md：AI 总结

当用户说"总结一下想法 / 整理 inbox"，或在合适时机，AI 读取整份 `ideas.md`，重写 `summary.md`。建议结构：

- **主题聚类**：把零散想法归并到几个主题下，每条标注来源时间戳，便于回溯到原话。
- **可执行候选**：哪些想法已成熟到可以开 PRD，给出建议的优先级 / 类型。
- **待澄清问题**：还需用户拍板或补充信息的点。
- **已升级**：已进入 `tasks/pending/` 的想法，链接到对应 PRD。

`summary.md` 顶部应标明它是 AI 派生、可被重写，事实以 `ideas.md` 为准。新增一条想法后 `summary.md` 即过期，AI 应在确认捕获时说明，并在用户要求时重写。

## 升级为 PRD

1. 在 `tasks/inbox/prd-drafts/` 生成草稿 `<YYYYMMDD-HHMMSS>-<slug>.md`，顶部 metadata 由 `core/use_cases/idea_prd_drafts.py` 注入。
2. 人在 `/ideas` 页面确认后，草稿复制到 `tasks/pending/`，命名为 `<PRIORITY>-<TYPE>-<YYYYMMDD-HHMMSS>-<slug>.md`，状态改为 `approved`。
3. 手工路径（不经产品页）则直接用 PRD skill 在 `tasks/pending/` 下创建正式 PRD（命名与规范见 `skills/prd/SKILL.md`），并在 `summary.md` 的"已升级"区标注。
4. 原话条目保留在 `ideas.md` 不动，作为需求溯源。

## 约定与边界

- 默认单文件 `ideas.md`；体量过大时可按年份滚动归档，例如把往年条目移到 `tasks/inbox/ideas-2025.md`，并在新文件顶部注明衔接。
- 想法内容是项目私有的，不随模板同步传播；`just copy` 只继承空的 `tasks/inbox/` 目录骨架和本约定，不继承任何已有想法。
- 本流程本身不引入新的命令或 CI，它就是两个 Markdown 文件加一套读写约定；产品侧的 `/ideas` 页面与 API 属于 Agent Runner，另见 [Agent Runner](agent-runner.md)。
