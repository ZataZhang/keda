# 证据报告：跨 claim 交接失败上下文，并发布人可审阅的失败 Draft PR

PRD：`tasks/pending/P1-FEAT-20260922-000431-blocked-draft-pr-validation-failure.md`

## 人审导航 / Human Review Navigation

本 PRD **无前端改动、无可截图的界面**（`No frontend impact`：只改 runner 耗尽路径的接线、
continuation prompt 与 marker 解析）。因此本目录没有静态图需要内联嵌入——机器门禁的产物是
文本证据，人审面是 GitHub 上的 Issue 评论与 Draft PR 本身，见下表第 1、2 行。

| # | 你要看什么 | 怎么看（可直接执行） | 预期观察到的值 | 状态 |
|---|---|---|---|---|
| 1 | 交接记录**实际写出来长什么样**（本功能全部对外呈现的实质内容） | `open "<abs>/rv-1b-rendered-handoff-record.txt"`<br>或 `cat tasks/evidence/<stem>/rv-1b-rendered-handoff-record.txt` | 三个场景各一段：① marker 为 `verifier=red` 且正文含 `formed an explicit **red** verdict`；② marker 为 `verifier=no-verdict` 且正文含 `**no verdict was formed**`、**不含**判红措辞；③ `checkpoint=none` 且 Snapshot nature 改写为"本轮根本没有留下 commit" | 已由执行器交叉核对 ✅ |
| 2 | 真实 GitHub 上：耗尽后出现 Draft PR，正文说清"做到哪/卡在哪/缺什么/下一步"并回链交接评论；Issue 上有带 marker 的交接记录；PRD 仍在 `tasks/pending/` | 需真实 sandbox 仓：把一个 PRD 跑到 recovery 耗尽，然后 `gh issue view <N> --comments`、`gh pr list --head issue-<N>` | 交接评论存在且可被 `iar:failure-context` 定位；Draft PR 存在且正文尾部有 `<!-- iar:failure-context-ref -->` 段与 `#issuecomment-` 链接 | **⚠️ 待 PRD 作者本人手动执行**（PRD §9.1 第 1 行，runner 不产出该证据） |
| 3 | 该 Draft PR 不能被签核、合并或归档，即使手动取消 Draft 标志 | `gh pr view <N> --json labels,mergeable,state` + 走一次既有 merge queue / 归档入口 | labels 中**无** `validation/verifier-passed`；签核与合并被既有门禁明确拒绝；PRD 未被归档 | **⚠️ 待 PRD 作者本人手动执行**（PRD §9.1 第 2 行） |
| 4 | 上一轮结论确实回灌给了下一轮 | `cat tasks/evidence/<stem>/rv-2-context-reflow.txt` | 8 个用例全 PASS；其中真实入口用例断言 prompt 含 `rv-3 not satisfied…` 与 `Checkpoint commit: 'a1b2c3d4'`，且**不含**过期记录的 `rv-1 not satisfied` | 已由执行器交叉核对 ✅ |

绝对路径前缀：`/Users/zata/code/keda-worktrees/blocked-draft-pr-validation-failure/tasks/evidence/P1-FEAT-20260922-000431-blocked-draft-pr-validation-failure/`

## 绑定最终树

| 项 | 值 |
|---|---|
| 分支 | `blocked-draft-pr-validation-failure` |
| 首版实现提交 | `9b362669a176ba272bc948ff0fd2fac1bbbad561`（含全部生产代码改动） |
| 追加回归断言提交 | `18fecbea36fe2d69c3d231e4a457e7c48220d9ca`（**仅测试文件**） |
| base commit | `87ab96ee149eae7240f7ecd16eb1de81d7ef4dae` |
| base tree | `07efe13a172eeea10067645abdbfdde3347bae22` |
| 变更集指纹 | `8a3dc39acad9c78c5d6a34a5a0138a1a538e7627`（15 文件，含 3 份证据报告） |

行为证据绑定到代码提交 `18fecbea`（tree `454a1e5f`）；其后的提交只动证据与文档文件、不改代码树，故 rv 证据无需重采。

最后一次实现变更来自**独立 verifier 审查轮**（报告见
`<prd-stem>.verifier-report.md`）：修掉 1 个 MAJOR（渲染步骤未受守卫，可掩盖原始异常）、
1 个零判别力的测试（rv-2 场景③是同义反复）、verifier 判定只读末轮 attempt、缺失呈递物无独立
字段、PR 标签未负向断言、rv-3 未走真实安全筛选，以及两处措辞失效。
**rv-1 / rv-2 / rv-3 / rv-4 的证据全部在该修正之后于同一最终树重采。**

## 机器门禁结果

| oracle | 结果 | 证据 |
|---|---|---|
| rv-1 | 7 passed（判红 / 未形成结论 / 限流与中断零副作用 / 发布失败不掩盖 / **渲染失败不掩盖** / **不给 verifier-passed 标签**） | `rv-1-handoff-and-draft-pr.txt` |
| rv-2 | 8 passed（另 12 例与本 oracle 无关被 deselect）；含**改动前实现实跑采集的 prompt 金标准** | `rv-2-context-reflow.txt` |
| rv-3 | 4 passed（含在真实 git 仓上只改 `.env` 走**真实**安全筛选的一例） | `rv-3-no-safe-commit.txt` |
| rv-4 | `mkdocs build --strict` EXIT=0（0 ERROR）；`just lint --repo` EXIT=0 全 Passed；全量 `pytest` **2493 passed** | `rv-4-static-gates.txt` |

`just lint --repo` 中值得一提的钩子：`ruff` Passed、`Check Python duplicate code` Passed、
`Check copy-paste duplication` Passed、`Check architecture layer dependencies` Passed、
`Check max file lines (non-empty)` Passed、`Check PRD acceptance checklist` Passed。

## 交付内容（改了什么）

| 文件 | 变化 |
|---|---|
| `agent_runner_issue_handlers.py` | 耗尽 `except` 分支在既有 checkpoint 之后，**仅对 `MaxRetriesExceededError`** 调 `_record_failure_handoff`：写交接评论 →（有安全 commit 才）`publish_changes(require_prd_archived=False)` → 把回链附到 PR 正文尾部。续作 prompt 构造处新增 `_read_previous_failure_context` 回灌 |
| `agent_runner_failure.py` | 新增 `format_failure_context_comment`：marker + 事实转述正文，复用既有 `format_attempt_history` 与 `truncate_recovery_output`，verifier 结论经既有 `parse_verifier_verdict` 从 attempt detail 读出 |
| `agent_runner_events.py` | 新增 `iar:failure-context` marker 的格式化、`has_failure_context_marker` 与 latest-wins 的 `find_latest_failure_context_comment` |
| `agent_runner_feedback.py` | `build_progress_continuation_prompt` 增加 `previous_failure_context` 入参（**空值时整段不插入**，保证无记录的 claim 拿到与本功能存在前逐字相同的 prompt）；新增 `truncate_failure_context_for_prompt`（8000 字符保头截尾并显式标注） |
| `agent_runner_commit.py` | 更正已失效的 docstring 不变量："checkpoint 永远不会被推送"→ 成功路径仍不推送，唯一例外是耗尽交接发布的 Draft PR，但缺 `validation/verifier-passed` 使其永不被合入 |
| 6 个测试文件 | **新增 20 个测试函数**覆盖 rv-1…rv-4（含改动前实现的 prompt 金标准、真实 git 仓的禁改路径筛选、渲染/发布失败不掩盖原始异常、标签负向断言）；两条既有 `run_once` 用例的失败评论计数按 marker 排除交接记录 |
| 2 个文档 | `docs/guides/agent-runner.md` 新增「跨 claim 失败交接与失败 Draft PR」；`docs/ai-standards/testing.md` 新增该类改动的取证边界 |

**未改**：`agent_runner_publish.py` 等发布原语、任何标签定义、`agent_runner_merge_queue.py`
等既有门禁、数据库 schema、前端。

## 与 PRD 的偏离（均已记入 PRD §14 Change Log）

1. PR 正文的交接上下文与回链改由**发布后一次正文补写**实现，而非喂给 `IContentGenerator`——
   后者在 `generated_content.enabled=false` 时根本不会被调用，覆盖不了 FR-6 自己要求的场景。
   发布原语签名保持不变。
2. 一次 claim 可能留下**多条**交接记录：agent fallback 阶梯为每个候选 agent 各耗尽一次。
   每条对应一次真实快照，latest-wins 只读最近一条，PR 仍按分支复用同一个。
3. marker 的 `checkpoint=` 取值放宽为单 token，避免取值形状不符时静默退回"没有上下文"。
4. 测试落点从 `test_agent_runner_orchestrate.py`（已 1195 非空行，超仓库 1000 行约定）改分到
   recovery / feedback / checkpoint / publish 四个文件。

## 残留风险（PRD §12，本报告如实记账）

- **失败性质不可机器核对**：产品失败 vs 审核事故只存在于 Agent 写的自由文本里，这是
  决策 D-03 主动接受的代价；缓解只有"回链原始诊断"与"改不了标签与合并态"两条既有硬约束。
- **回灌会继承上一轮的错误判断**：这条风险在有回灌之后比没有回灌时更高，因此限量、
  截断、"陈述而非裁定"措辞与回链复核都是硬要求。
- **第 2、4 行人审无自动化兜底**：若人工未实际执行或未回填记录，交付链路上不会有环节报错。
- **限流打断后下一轮仍无上下文**：`ProviderCapacityError` 刻意不写交接记录（D-11），
  是已接受的边界而非漏写。
