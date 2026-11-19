## 人审导航 / Human Review Navigation

| 人工审查项 | 呈递物 | 打开方式 | 核对结果 |
|---|---|---|---|
| 检查 #398 是否已复用原 Issue 发布 PRD | [GitHub Issue #398](https://github.com/ZataZhang/keda/issues/398) | `gh issue view 398 --json url,labels,body` | Issue URL 仍为 #398；正文只有一个 `PRD path:` 锚点；可点击链接固定到包含 PRD 的 `issue-398` 发布提交；`agent/ready` 在发布完成后出现。 |

- PRD：[`tasks/archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md`](../../archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md)。
- GitHub Issue：[#400](https://github.com/ZataZhang/keda/issues/400)。
- PR / CI：[Draft PR #405](https://github.com/ZataZhang/keda/pull/405)。已发布 head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd` 的 `Validate Template` 因单文件非空行数超限失败；本轮已在修复工作树通过同一硬行数检查。GitHub 必需检查需在修复提交发布后重跑；本轮按要求不推送。
- 清单入口：`just prd review P1-FIX-20261119-180000-prd-publication-on-protected-main.md`。
- 执行侧已交叉核对临时 Issue #42 的创建、分支发布、恢复、幂等和 ready 顺序；没有读取或修改真实 #398/#399。上表仍需维护者按真实 #398 状态验收。

## Automated validation

### rv-1 — protected main publication

- Evidence: `rv-1-protected-main-publish.txt`.
- Negative control: a real `kc issue create` process attempted the protected main push; the local bare-remote hook rejected it with `GH013`, the Issue stayed not ready, and the command printed `--recover-issue 42`.
- Green path: the explicit task-branch option returned 0, published only the PRD to `issue-42`, preserved main, pinned the Issue link to the commit, then added ready.
- Fresh probe: the script read branch refs and file contents from the bare remote and write order from the persistent `gh` fixture state.

### rv-2 — recovery and conflict handling

- Evidence: `rv-2-recovery-idempotent-conflict.txt`.
- Negative control: recovery against a pre-seeded remote PRD with different bytes exited 1 and stated that remote content was preserved; branch SHA/content and Issue state were unchanged.
- Green path: a rejected first branch push left one Issue not ready; recovery reused that Issue, repeated recovery kept the same commit SHA, and a later external branch change was refused without overwrite.

### rv-3 — runner branch source

- Evidence: `rv-3-runner-branch-prd.txt`.
- Negative control: after deleting `issue-42` from the local bare remote, real `kc run --issue 42` failed before Agent launch.
- Green path: from a clean checkout with no main-branch PRD, the real runner fetched the branch and created an `issue-42` worktree. The observable Agent saw the branch-only PRD and its `kimi` lifecycle override; the `codex` sentinel did not run.
- Limit: the Agent fixture intentionally exits 23 after recording its actual worktree input. This proves PRD routing and executor selection; it does not claim paid Agent implementation or Draft PR publication.

### rv-4 — compatibility and repository gates

- Evidence: `rv-4-compatibility-gates.txt`.
- Negative control: the unchanged default publication path was rejected by the protected-main hook and left the Issue not ready.
- Compatibility: default publication still writes only the target PRD to an unprotected main and marks ready after success. `--no-publish-prd` succeeded with protected main, did not push, and left the Issue not ready.
- Tests: the changed publication, branch-source, and CLI test files ran with testmon disabled: **176 passed**. Added regressions confirm recovery retains the original PRD commit link after the task branch advances and publication stays on the configured base when a concurrent fetch changes `FETCH_HEAD`.
- Repository test entry: `just test` accepted the current success marker and skipped as designed; the targeted pytest command above executed the relevant cases directly rather than relying on that shortcut.
- Lint: `just lint --reuse` and `SKIP=check-test-flag just lint --full` passed. `check-test-flag` alone was skipped because pre-commit temporarily hides unstaged files, which changes the marker tree; the relevant tests were executed directly afterward.
- Documentation: `uv run mkdocs build` passed. MkDocs emitted existing INFO notices for unlinked pages and an optional signature formatter.
- Manifest replay: `kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main` passed **4/4** on the repaired code tree; the latest per-item runtimes were 4.0s, 7.1s, 9.9s, and 44.9s, all under the 300s gate. Independent verifier Round 3 reviewed the final tree and passed.
- PRD archive-readiness check passed after final reconciliation; the runner owns the actual PRD archive move.

All Git operations used temporary repositories and real Git processes. GitHub Issue API traffic was handled by a fail-loud local `gh` fixture; no real account, Issue, branch, or business data was contacted. The evidence files and scripts contain no credentials. Raw evidence and scripts are ignored by Git; only the three Markdown reports and PRD belong in the code change.

## Post-PR hard-line repair validation

- The read-only Direct PR handoff predicate now lives beside its round-state rule in `agent_runner_direct_pr_round.py`; `agent_runner_orchestration_runtime.py` still imports and exposes the same name for its discovery call sites and existing monkeypatch seam.
- `uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 src/backend/core/use_cases/agent_runner_orchestration_runtime.py src/backend/core/use_cases/agent_runner_direct_pr_round.py` passed. The orchestration runtime is now **998 non-empty lines**.
- Targeted publication, branch-source, CLI, and Direct PR discovery tests: **187 passed** with testmon disabled.
- `just test`: **239 passed, 562 deselected**.
- `just lint --reuse` and `SKIP=check-test-flag just lint --full` passed; the latter skipped only the unstaged-tree marker hook. `git diff --check` passed.
- This repair validation ran on a worktree based on the published PR head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd`. The issue runner assigns the repair commit SHA after this request; no post-repair commit hash is claimed here. The archived RV-1 through RV-4 feature evidence above remains intact.

## Review repair snapshot — 2026-11-20

- Rebased worktree head before this review patch: `434f9977b2a3e7387b6d7c74fa4f19f9dc7e2c4e` (`fix(issue): keep orchestration runtime under line limit`). The lifecycle-resolution source patch validated here has SHA-256 `38b6770010fc827a70e982f9c71dbe9dba58692921f9bfaadfdd8d0e749603cd`.
- `src/backend/core/use_cases/agent_runner_orchestration_runtime.py` remains at **998 non-empty lines**; the line-limit hook passed in both lint runs.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache SKIP=check-test-flag just lint --full`: passed; only `check-test-flag` was skipped because this worktree is unstaged and pre-commit hides unstaged files.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: **78 passed, 340 deselected** in 33.34 seconds. `git diff --check` passed.
- The existing RV-1 through RV-4 evidence and Round 3 independent verifier remain the feature-level validation record; those four RV scripts were not replayed for this docstring-only correction. This snapshot does not claim a new independent verifier pass.
- The GitHub PR #405 description and validation comment are outside this worktree. They remain pending refresh by the runner after it commits and publishes this snapshot; no GitHub state was changed here.
