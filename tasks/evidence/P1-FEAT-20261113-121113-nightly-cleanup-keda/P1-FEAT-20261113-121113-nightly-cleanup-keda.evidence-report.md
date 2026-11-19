## 人审导航 / Human Review Navigation

本轮没有用户可见界面或产品行为变化；审阅对象是测试白名单的一行差异与 CI 证据。

| 审阅内容 | 可操作入口 | 预期结果 |
|---|---|---|
| 检查代码差异和证据 | [测试文件](../../../tests/test_kedacode_operator_skill.py)；[人审清单](human-review-checklist.md)（`/Users/zata/code/keda/.kc-worktrees/issue-366/tasks/evidence/P1-FEAT-20261113-121113-nightly-cleanup-keda/human-review-checklist.md`）；[验收记录](P1-FEAT-20261113-121113-nightly-cleanup-keda.evidence-report.md)；命令：`open "/Users/zata/code/keda/.kc-worktrees/issue-366/tasks/evidence/P1-FEAT-20261113-121113-nightly-cleanup-keda/human-review-checklist.md"` | 只新增 CLI 已支持的 `--local`；不改 CLI 实现或文档行为描述；文档范围仍明确未确认。执行方已交叉核对 CI 原始失败、CLI 参数定义和前后测试结果。 |

- CI 失败记录：[GitHub Actions run 44548773786](https://github.com/ZataZhang/keda/actions/runs/44548773786)。
- Draft PR snapshot (read-only, 2026-11-20, before this repair): [#404](https://github.com/ZataZhang/keda/pull/404) is open with head branch `issue-366` at `e1b242d1f349905004cac3ab44af0e2d5b665a47`; GitHub reports 7 changed files, 500 additions, and 105 deletions. The actual title is `[Agent] [Feature] Nightly cleanup (CI / duplicated code / docs / dependencies) — 2026-11-13`; the required title is `cleanup(ci): align skill flag allowlist with CLI`.
- The remote PR description still identifies `525a3549ead8a4ada725b7916c694ba215fb8fa8` as its publication HEAD and lists 99 files. The current seven changed paths are `tasks/archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md`, the four Markdown files under `tasks/evidence/P1-FEAT-20261113-121113-nightly-cleanup-keda/`, the removed `tasks/pending/P1-FEAT-20261113-121113-nightly-cleanup-keda.md`, and `tests/test_kedacode_operator_skill.py`. The PR discussion timeline is empty. The title/body refresh, evidence-branch publication, and PR evidence comment remain runner follow-ups under PRD §11 and this task's worktree-only/no-push constraints; the body should reference the runner's resulting commit and the seven-file diff.
- 执行方已核对：真实 CLI 命令定义含 `--local`，修改前测试因该旗标失败，修改后目标测试和核心测试通过。

## 本轮选择

- 固定 `head_sha`: `4c58ad0df1b1437ea2336303a64d486ada00ea89` (`main`, captured before edits).
- GitHub Actions `CI` run `44548773786` failed in job `Validate Template` (`135646693320`). The suite reported `1 failed, 5517 passed, 1 skipped`.
- Failing test: `tests/test_kedacode_operator_skill.py::test_packaged_skill_command_examples_match_cli_help`.
- Failure detail: `references/setup-and-config.md: kc agent setup --local --dry-run --json` was rejected because `_ALLOWED_FLAGS[('agent', 'setup')]` omitted `--local`.
- Source evidence: `src/backend/api/cli_typer_agent.py::agent_setup_command` declares `--local`; the legacy argparse entry also declares the same option in `src/backend/api/cli_parser_session_commands.py`. Commit `9454dcf79fcd0dd5c3394868f70a02dc65aa2586` introduced the local setup behavior and updated the packaged skill example, but did not update the test allowlist.
- Fix: add `--local` to the existing curated allowlist in `tests/test_kedacode_operator_skill.py`. The check still derives actual runtime flags and still rejects flags outside the curated set.
- Scope label `scope/ci` and the required Issue comment were not published from this worktree because the task's execution rules restrict changes to worktree files. They remain for runner publication.

## Lower-priority scopes and docs checkpoint

- Duplicated-code scan: not executed; CI was selected as the sole scope.
- Dependency review: not executed; CI was selected as the sole scope.
- Docs sync: not executed; CI was selected as the sole scope.
- The most recently closed nightly-cleanup Issue is [#325](https://github.com/ZataZhang/keda/issues/325). Its only comment closes the stale Issue and contains no doc-check record or `checked_through_sha`.
- Docs checkpoint: unconfirmed. This is not treated as a first run. `checked_through_sha` was not advanced, no commit is claimed checked, and the remaining range is unconfirmed through `head_sha`.
- No docs finding was established this round. This does not mean the range was checked and found in sync.

## Verification results

| Command or observation | Result |
|---|---|
| `uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_command_examples_match_cli_help -v` before the fix | Failed as expected: unknown flag `--local`. |
| Same focused test after the fix | Passed (`1 passed`); captured in local ignored `rv-1-cli-skill-flag-check.txt`. |
| Independent rv-1 rerun on implementation SHA `525a3549ead8a4ada725b7916c694ba215fb8`: `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_command_examples_match_cli_help -v --no-testmon` | Passed (`1 passed`); complete 12-line output is present in the ignored local raw file. SHA-256: `4c9c5431474c34c0e859c537382fd6d8e2098ff269081b3fba218367bca76b2e`. Remote publication and PR presentation remain pending. |
| Runtime schema confirmation: `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_whitelist_matches_runtime_schema -v --no-testmon` | Passed (`1 passed`); complete 12-line output is present in the ignored local raw file. SHA-256: `6a2afb1586216c3a7fe3d04cbb2a629d1f3ab7605032bea82c37d4ce335f278b`. Remote publication and PR presentation remain pending. |
| `just lint --reuse` | Passed: jscpd, pylint duplicate-code, architecture, guidelines consistency, and file-line checks. |
| First `just lint --full` attempt | All lint hooks passed except `check-test-flag`, which correctly required `just test` first. |
| `just test` | Passed: `5517 passed, 1 skipped, 1 deselected in 681.99s`. No testmon selection baseline was available in this worktree. |
| Final `just lint --full` | Passed after `just test` recorded the required flag. |
| `git diff --check` | Passed. |
| PRD skill checker with `--check-provided` | Passed after adding the required Part A / Part B structure, oracle block, evidence-bearing checklist, and Final Reconciliation. |
| Final `uv run mkdocs build --strict` | Passed in 8.65s. It emitted the repository's existing not-in-navigation page list; no formal docs page or navigation entry changed. |

The initial `uv` command could not open the default cache outside the allowed filesystem. Re-running with `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache` completed successfully.

Raw-output SHA-256 for runner publication checks: `rv-1-cli-skill-flag-check.txt` = `4c9c5431474c34c0e859c537382fd6d8e2098ff269081b3fba218367bca76b2e`; `rv-1-cli-schema-check.txt` = `6a2afb1586216c3a7fe3d04cbb2a629d1f3ab7605032bea82c37d4ce335f278b`.

## Final tree and remaining gates

- The verified source diff is limited to `tests/test_kedacode_operator_skill.py` plus the current-format PRD and its evidence reports / human review checklist.
- No CLI, API, configuration, persistence, or user-visible behavior changed.
- The two raw pytest outputs are local and ignored by Git. They must be published to `kc-evidence/issue-366` and presented in a PR evidence comment before human acceptance. Their hashes are recorded above and both 12-line outputs are present in this worktree. This repair does not publish the evidence branch or change PR metadata/comments under PRD §11 and the worktree-only/no-push constraints. The latest read-only PR check confirms the stale title/body and empty comment timeline; the Markdown reports are tracked with the archived PRD.
- Independent verifier verdict: **PASS** against implementation SHA `525a3549ead8a4ada725b7916c694ba215fb8fa8`; see the verifier report for the diff review and both focused checks.
- PRD status: `🧍 待人工验收`; all evidence-backed executor items are checked in the PRD, while human acceptance remains unchecked. Draft PR #404 exists; title/description refresh, PR evidence comment, Issue round comment, `scope/ci` label, and raw evidence-branch publication remain pending for the runner. The latest remote snapshot before this repair is head `e1b242d1f349905004cac3ab44af0e2d5b665a47`; the runner's commit will become the next PR head.
- This review repair's tracked diff is limited to the archived PRD and four Markdown evidence/review files; no executable code or tests changed. Baseline branch head before this local repair: `e1b242d1f349905004cac3ab44af0e2d5b665a47`.
- Repair checks: `git diff --check` passed; `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache uv run mkdocs build --strict` passed. MkDocs emitted only the repository's existing not-in-navigation page list and the optional Black/Ruff signature-formatting notice. The existing rv-1 and runtime-schema raw results remain intact; no code test was rerun for this documentation-only repair.
- Time spent: approximately 25 minutes wall-clock from the runner claim, including the 11m21s `just test` run.

## Reviewer repair record — 2026-11-20

| Finding | Disposition | Evidence / follow-up |
|---|---|---|
| Independent rv-1 is still pending | Disputed as stale. The verifier report says `PASS`; both recorded commands passed, and the two complete 12-line raw outputs remain present with the recorded SHA-256 values. No executable code changed after implementation SHA `525a3549ead8a4ada725b7916c694ba215fb8fa8`. |
| Raw outputs are not published to `kc-evidence/issue-366` or presented in a PR comment | Locally preserved, remote publication pending. Both files and hashes are intact. The PR timeline is empty. Publishing the orphan branch is a remote repository write and remains runner-owned under the worktree-only/no-push constraints. |
| PR title/body and review material contain stale metadata | Local materials refreshed to the live PR snapshot (head `e1b242d1f349905004cac3ab44af0e2d5b665a47`, 7 files, 500 additions, 105 deletions). The PR title/body remain stale remotely; changing PR metadata is runner-owned under PRD §11. Proposed title: `cleanup(ci): align skill flag allowlist with CLI`. |

The final executable tree is unchanged from the independently verified implementation. This repair only updates the archived PRD and Markdown review/evidence records; the runner's resulting commit is the next final published tree.

- Repair checks: `git diff --check` passed. `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache uv run mkdocs build --strict` passed; MkDocs listed the repository's existing pages outside navigation and noted optional Black/Ruff signature formatting. No test was rerun because no executable file changed; the prior rv-1 and runtime-schema results remain applicable.
- Final-tree evidence: the repair started from branch head `e1b242d1f349905004cac3ab44af0e2d5b665a47`; the tracked repair diff contains only the archived PRD and four evidence/review Markdown files. The runner commit will establish the next published head. The ignored `.agent-runner/commit-request.json` requests `docs(evidence): refresh issue 366 review metadata`.
