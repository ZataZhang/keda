# 验证计划：跨 claim 交接失败上下文，并发布人可审阅的失败 Draft PR

> 本文件是 `tasks/pending/P1-FEAT-20260922-000431-blocked-draft-pr-validation-failure.md` §7.6
> Realistic Validation Plan 的执行副本。**判据以 PRD §7.6 为唯一事实源**，本文件只记录
> "怎么跑、跑在哪棵树上、结果落在哪个文件"。

## 复现环境

| 项 | 值 |
|---|---|
| worktree | `/Users/zata/code/keda-worktrees/blocked-draft-pr-validation-failure` |
| 分支 | `blocked-draft-pr-validation-failure` |
| base commit | `87ab96ee149eae7240f7ecd16eb1de81d7ef4dae` |
| base tree | `07efe13a172eeea10067645abdbfdde3347bae22` |
| 变更集指纹（`git diff \| git hash-object --stdin`） | `8a3dc39acad9c78c5d6a34a5a0138a1a538e7627` |
| 采集时间 | 2026-09-22T19:13Z 起，最终树采集于 renderer 修正之后 |

测试命令统一为 `.venv/bin/python -m pytest -o addopts="" -v <target>`。
`-o addopts=""` 是必须的：仓库默认 `addopts = "--testmon"` 会做增量选择，取证时必须无视
`.testmondata` 强制真跑，否则"通过"可能只是被跳过。

## Oracle 与执行入口

| id | 判据（摘要） | 实际执行 | 证据文件 |
|---|---|---|---|
| rv-1 | 耗尽时写出带 marker 的交接记录；有安全 commit 时发布/复用 Draft PR 并回链该评论；PR 不含 `validation/verifier-passed`；`require_prd_archived=False` 不外泄；限流与中断零副作用 | `tests/test_agent_runner_recovery.py` 5 个用例（`test_exhaustion_writes_failure_context_handoff_and_draft_pr` × 2 参数、`test_capacity_and_interrupt_exhaustion_branch_have_zero_side_effects` × 2 参数、`test_handoff_publish_failure_does_not_mask_original_exhaustion`） | `rv-1-handoff-and-draft-pr.txt`；关键值原文见 `rv-1b-rendered-handoff-record.txt` |
| rv-2 | 下一轮 continuation prompt 注入最近一条记录，限量 + 截断；无记录时不注入、不报错 | `tests/test_agent_runner_feedback.py`（4 例：注入 / 基线逐字相等 / 超长截断 / latest-wins 与 marker 往返）+ `tests/test_agent_runner_checkpoint.py`（2 例走 `_process_ready_issue` 真实入口） | `rv-2-context-reflow.txt` |
| rv-3 | 无安全 commit 或只含 forbidden paths 时不创建任何 PR，但交接记录仍写出 | `tests/test_agent_runner_recovery.py::test_exhaustion_without_safe_commit_writes_handoff_but_publishes_nothing` | `rv-3-no-safe-commit.txt` |
| rv-4 | `uv run mkdocs build --strict`、`just lint --repo`、全量 `pytest` 全绿；无新增 blocked 标签或失败判定器 | 三条命令真实执行，另加两条常驻静态审计断言（在 `tests/test_agent_runner_publish.py`） | `rv-4-static-gates.txt` |

## rv-1 的三个场景与判别方式

1. **verifier 明确判红后耗尽** — attempt detail 内嵌 `iar:verifier-verdict risk=red`，
   断言 marker 为 `verifier=red`、正文出现 `formed an explicit **red** verdict` 与 verifier 的
   发现原文。
2. **verifier 未形成结论后耗尽** — attempt detail 不含可解析 marker，断言 marker 为
   `verifier=no-verdict`、正文出现 `**no verdict was formed**`，且**不**出现判红措辞。
   两场景共用同一条链路，差别只在措辞——这正是 PRD 行为样例第 3 行要求的格子。
3. **限流与用户中断** — 同一个 `except` 元组，`ProviderCapacityError` 与
   `KeyboardInterrupt` 各跑一次，断言：无交接评论、无 `create_draft_pr`、无
   `update_pull_request_body`、`publish_changes` 未被调用、原异常按既有类型上抛
   （`KeyboardInterrupt` 不得退化成 `AttributeError`）。

**关键值来源**：`rv-1b-rendered-handoff-record.txt` 由 `scripts/render_handoff_record.py`
用**真实** `format_failure_context_comment` + 真实 `parse_verifier_verdict` + 真实
`format_attempt_history` 直接渲染，不经任何 mock——被呈递的文本就是生产路径会写的文本。

**禁止的旁路（已在断言中封死）**：`require_prd_archived=False` 泄漏到成功路径（白名单静态审计）、
正文伪造 verifier PASS（`"verifier passed" not in body.lower()`）、绕过 forbidden paths
（走真实 `checkpoint_uncommitted_progress` 返回值路由）、发布失败掩盖原始异常（显式断言原异常仍上抛）。

## fresh-state 探针

| oracle | 探针 |
|---|---|
| rv-1 | 重跑同一次耗尽，Draft PR 由 `create_draft_pr` 按分支复用既有 open PR，不新建；交接记录按 latest-wins 被读回，PR 正文回链段被替换而非叠加 |
| rv-2 | 清空 Issue 上的交接评论后重建 prompt，断言回到无上下文形态（`test_next_claim_without_any_handoff_record_continues_without_context`） |
| rv-3 | 记录型 fake GitHub client 未收到任何 PR 创建调用，但收到了带 marker 的 Issue 评论 |
| rv-4 | 全量 `pytest` + `mkdocs --strict` + `just lint --repo` 在同一最终树上一次跑完 |

## 明确不由机器取证的两格

PRD §9.1 第 1、2 行（真实 GitHub 仓上的失败 Draft PR 呈现，以及它与既有签核/合并/归档门禁的
组合行为）**刻意标注为 PRD 作者本人手动验证**：

- 前者需要真实 sandbox 仓与写凭据，属被移除的最重交付卡点；
- 后者依赖的门禁本 PRD **根本不修改**，已由既有 `tests/test_agent_runner_merge_queue.py` 覆盖，
  再写一套会成为第二个事实源。

因此 runner 不因缺这两项证据而拦下交付；其残留风险记于 PRD §12。
