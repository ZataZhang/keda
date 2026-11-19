## 人审导航 / Human Review Navigation

| 人工审查项 | 呈递物 | 打开方式 | 核对结果 |
|---|---|---|---|
| 检查 #398 是否已复用原 Issue 发布 PRD | [GitHub Issue #398](https://github.com/ZataZhang/keda/issues/398) | `gh issue view 398 --json url,labels,body` | Issue URL 仍为 #398；正文只有一个 `PRD path:` 锚点；可点击链接固定到包含 PRD 的 `issue-398` 发布提交；`agent/ready` 在发布完成后出现。 |

- PRD：[`tasks/archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md`](../../archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md)。
- GitHub Issue：[#400](https://github.com/ZataZhang/keda/issues/400)。
- PR / CI：[Draft PR #405](https://github.com/ZataZhang/keda/pull/405)。原始发布 head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd` 的 `Validate Template` 曾因单文件非空行数超限失败；后续拆出 handoff predicate 后，本地硬行数检查通过，文件为 998 个非空行。GitHub 必需检查仍需在最终提交发布后重跑；本任务不推送。
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

- Review input identifies PR #405 head `3177b598` while its description still names verified head `a856cf8` and its evidence comment is pinned to `74ba5cb`. This worktree starts at `3177b598d19c72e927908522e59fc1711e4f4274`; the final local source/evidence diff is pending the runner-assigned commit SHA.
- The branch still diverges from local `main` (`51461449`): merge base `f56f2d1`, with 5 branch-only and 15 main-only commits. The reported conflict is valid. Rebase/merge and PR metadata edits are not performed under this task's worktree-only, no-merge, no-staging/commit, and no-push constraints; do not treat this snapshot as an updated GitHub PR description/comment or as conflict-resolved.
- Corrected `attach_prd_lifecycle_overrides()`'s `Returns` contract: main-checkout read failures return the original Issue; failures fetching or reading a published task-branch PRD raise; parse failures return the original Issue.
- `src/backend/core/use_cases/agent_runner_orchestration_runtime.py` has **998 non-empty lines**; the previous extraction finding is already resolved and remains within the hard limit.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed, including copy detection, duplicate-code, architecture, guideline consistency, and max-file-lines checks.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache SKIP=check-test-flag just lint --full`: passed; only `check-test-flag` was skipped because pre-commit hides unstaged files and changes its marker tree.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: **78 passed, 340 deselected** in 33.99 seconds.
- `git diff --check`: passed on the final worktree diff. Corrected `lifecycle_agent_resolution.py` SHA-256: `4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`. The source correction and these local checks are tied to the runner-assigned commit; no post-repair commit SHA is claimed.
- The RV-1 through RV-4 scenarios and Round 3 verifier remain historical feature-level evidence; they were not re-described as newly run for this docstring correction, and this snapshot does not claim a new independent verifier pass.
- PR #405's description and evidence comment require refresh by the runner after the branch conflict is resolved and a new snapshot is published. This task does not alter GitHub state.

## Current finding audit and local validation — 2026-11-20

- Audited PR #405 and the listed lifecycle docstrings again. The remote PR is still open at head `5676be1f90dc931ffe616b7b369d58daad0dcf4f`, based on `6dceaa0ba94c285776e76109a324051d1dd04d79`; GitHub reports `mergeable=false`. The current local `main` is `51461449`.
- `git merge-tree --write-tree main issue-400` reports one content conflict in `src/backend/core/use_cases/agent_runner_orchestration_runtime.py` (merge base `f56f2d1`). The conflict is valid. It remains unresolved because this repair explicitly forbids merging/syncing `main`; no branch ref was changed.
- The remote PR description still records verified head `a856cf84008cb2261325844d678c46bd869bf9a5`, tree `d21fb64e29b9e2dec4a209e36ab89ca0f25e11af`, and `174 passed`. The existing [validation comment](https://github.com/ZataZhang/keda/pull/405#issuecomment-7016416947) is still anchored to head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd` and base `6dceaa0ba94c285776e76109a324051d1dd04d79`. This worktree-only task did not edit GitHub metadata; the local evidence below is refreshed for the runner to publish after the branch conflict is resolved.
- The two lifecycle docstring findings are already corrected at head `5676be1`: main-checkout read failures return the original Issue, task-branch fetch/read failures raise, and parse failures return the original Issue. See `lifecycle_agent_resolution.py` lines 479–493. Source SHA-256: `4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`.
- Final source commit/tree for these checks: `5676be1f90dc931ffe616b7b369d58daad0dcf4f` / `9c7a9065ff9d19a405792ebf8f0b249527584241`. No source files were changed during this repair; the tracked diff is this evidence update, and the runner commit request is a local ignored file.

### Checks rerun against the current source tree

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache SKIP=check-test-flag just lint --full`: passed; only `check-test-flag` was skipped because pre-commit hides unstaged files and changes its marker tree.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: **81 passed, 296 deselected** in 31.32 seconds.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . pytest --no-testmon -q tests/test_issue_prd_branch_source.py tests/test_lifecycle_agent_routing.py tests/test_agent_runner_direct_pr_discovery.py`: **36 passed**.
- RV-1 through RV-4 were rerun successfully with their commands in the verification plan. RV-4 also reran full lint, the related publication/branch-source/CLI tests (**176 passed**) and `uv run mkdocs build`.
- Refreshed raw evidence SHA-256:
  - `rv-1-protected-main-publish.txt`: `9d1c8a499a0ce69e7657335aea8c1e3e8e266cd0f38885f161e6eaf0255c0c41`
  - `rv-2-recovery-idempotent-conflict.txt`: `42b6a7aaa1b860ba7f640c17a03af10f8510b5108a19f611b1857f1668835a6a`
  - `rv-3-runner-branch-prd.txt`: `14042bea4a4eb2bcd974281015061a8d059796cd0815c43b6c6b6ff69759ebb3`
  - `rv-4-compatibility-gates.txt`: `8c9354a712c8fbf1363637c3bc4fd338b2833e4361a4b07b27202ef215f07b3c`
- All RV runs used temporary local Git remotes and a fail-loud local `gh` fixture. No real Issue #398/#399 or GitHub branch was changed; no independent verifier was rerun.

## Current review repair verification — 2026-11-20

- Verified code snapshot: PR #405 head `a914f022af9c3583752914f93900448346ab565c`, commit tree `50aa69692472cd061070385debd462f76a428b8f`. The tracked source tree has no code changes after this head; this report and the verifier report are local evidence-record updates.
- The lifecycle documentation findings are fixed in that source snapshot: main-checkout PRD read failures return the original Issue, task-branch fetch/read failures re-raise, and parse failures return the original Issue. See `src/backend/core/use_cases/lifecycle_agent_resolution.py` lines 479–493; SHA-256 `4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`.
- Replayed RV-1, RV-2, RV-3, and RV-4 from the worktree root. `kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main` passed **4/4** (2.8s, 4.1s, 6.4s, and 23.0s). RV-4 reports **176 passed** for the targeted publication/branch-source/CLI tests, full lint passed with only `check-test-flag` skipped for the documented unstaged-tree marker issue, and `uv run mkdocs build` passed.
- Refreshed raw evidence SHA-256:
  - `rv-1-protected-main-publish.txt`: `f5998ced91f50e07c676fdc90291845dddb0964e8a1ca4a1ab598f2566bbecaf`
  - `rv-2-recovery-idempotent-conflict.txt`: `cece810b2bedd3b2920abbc2d81668e356138087519da95db1b6b62badc6a338`
  - `rv-3-runner-branch-prd.txt`: `03bc19e9b1c9a3ab4be550db49b75b18511adcc96ed1d170fa8bb84921eccfff`
  - `rv-4-compatibility-gates.txt`: `20a2a162d315eb9a0b7af04a71f3a393ad28b806e2de7e13aae008c70c628782`
- Independent verifier Round 4: **PASS for the current implementation and RV evidence**; no product defect or evidence defect was found. Integration is still blocked: `main` is `51461449cb1069113b3d6a827297f92c16bcff2f`, the branch is 7 commits ahead / 15 behind, and `git merge-tree --write-tree main issue-400` reports a content conflict in `src/backend/core/use_cases/agent_runner_orchestration_runtime.py` (merge base `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`). The conflict finding is valid and remains unresolved because this task forbids merging main, staging/committing, and pushing.
- GitHub's read-only PR snapshot still reports head `a914f022af9c3583752914f93900448346ab565c`; remote `git ls-remote zata refs/heads/main refs/heads/issue-400` confirms PR head `a914f022` and base branch `51461449`. The verifier run covers the current source head, not a post-conflict-resolution tree. #398/#399 and real GitHub issue/branch state were not changed.
- PR #405 description and validation-comment refreshes were attempted after validation, with intended metadata `head=a914f022`, `base=51461449`, `patch=b044634fce4341fa522736e118ca7fcf767ecac3`, and verified source tree `50aa69692472cd061070385debd462f76a428b8f`; the comment payload included all four raw outputs and their hashes. Both GitHub tool writes were rejected with `MCP tool call requires approval, but approval policy is never`. No PR metadata changed: the description still names the older verified snapshot, and the existing validation comment remains anchored to head `74ba5cb` / base `6dceaa0`.

## Reviewer repair audit — current snapshot (`e87337f5`)

This section supersedes the older PR-head and remote-status values above. It records all five findings against the current worktree and the read-only GitHub snapshot; it does not claim that the two integration findings were repaired.

| Finding | Disposition | Evidence |
|---|---|---|
| Main and PR branch conflict | **Valid; unresolved under the explicit no-merge constraint.** No branch integration or source conflict resolution was performed. | Current local `main` is `51461449cb1069113b3d6a827297f92c16bcff2f`; `issue-400` is `e87337f5ca8479632f8433b7d01109b2f859ad68` (15 main-only / 8 branch-only commits from merge base `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`). `git merge-tree --write-tree main HEAD` reports a content conflict in `src/backend/core/use_cases/agent_runner_orchestration_runtime.py`. Read-only `gh pr view 405` reports `mergeable=CONFLICTING`. |
| PR body and validation comment use old verification snapshots | **Valid; remote metadata left unchanged by the worktree-only constraint.** | PR #405 still lists verifier Round 2, verified head `a856cf84008cb2261325844d678c46bd869bf9a5`, tree `d21fb64e29b9e2dec4a209e36ab89ca0f25e11af`, and `174 passed`. Its validation comment [#issuecomment-7016416947](https://github.com/ZataZhang/keda/pull/405#issuecomment-7016416947) still identifies head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd` and base `6dceaa0ba94c285776e76109a324051d1dd04d79`. This report is a local handoff, not a refresh of either GitHub field. |
| Lifecycle function summary omits task-branch read failures that raise | **Already fixed in the current source.** | `src/backend/core/use_cases/lifecycle_agent_resolution.py:479–483` says a published task-branch fetch/read failure is re-raised and stops the pipeline. |
| Lifecycle Returns says all read failures return the original Issue | **Already fixed in the current source.** | `src/backend/core/use_cases/lifecycle_agent_resolution.py:491–493` distinguishes main-checkout read failures from task-branch fetch/read failures and parser failures. File SHA-256: `4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`. |
| Missing independent verifier conclusion for the current code | **Already satisfied by verifier Round 4; the current source is unchanged.** | Round 4 in the verifier report records PASS for implementation and RV evidence while excluding integration readiness. `git diff --quiet a914f022af9c3583752914f93900448346ab565c HEAD -- src tests` found no source/test changes. The `src/backend` and `tests` tree IDs match at both commits: `c0135e6d82d2a569f9aec1b45e51ea1dc114ed2d` and `5efe42c0a60b7e33f901d125a2d8eb61497499a8`. |

### Current-snapshot checks

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main`: **4/4 passed** (RV-1 2.9s, RV-2 4.1s, RV-3 6.1s, RV-4 24.4s). RV-4 reran the documented compatibility checks, targeted tests, lint hooks, and `uv run mkdocs build`.
- Refreshed raw RV output SHA-256: RV-1 `65ebb97520bfa9c8e7fc221b3bc806049bb20d2c8ad92d5fbb4aa16f9514b80b`; RV-2 `08033e007bf25003394ebe35673527863ffb96ff81cefb6ae9c151f84ccd34ec`; RV-3 `1ca29b5ad6d1f5bc27f5b6601df173fda5b6b481641912e9e91cd858a20c4c0d`; RV-4 `e3364302180f2117421b255c85e51b38ae469f48b7606a0fbb416fb6a341d650`.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: current `issue-400 @ e87337f5` success marker validated; the recipe skipped pytest as designed. RV-4 executed the targeted publication/branch-source/CLI tests.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed copy detection, duplicate-code, architecture, guideline consistency, and max-file-lines checks.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . mkdocs build`: passed in 4.28s; MkDocs emitted its existing INFO notices for unlinked pages and optional signature formatting.
- `git diff --check`: passed for the final tracked evidence-report change. `.agent-runner/commit-request.json` parses as JSON and remains ignored/local for the runner.
- Current code snapshot: commit `e87337f5ca8479632f8433b7d01109b2f859ad68`, commit tree `25622cf6e178f4840aa44f0adde868e09f470d07`; `agent_runner_orchestration_runtime.py` SHA-256 `3936925d581ac34c84787f2006221a06bcb1932ea51776581de894fec8b4364c`.
- GitHub reads and merge-tree inspection were read-only. No source edits, GitHub writes, branch changes, staging, commits, or pushes were made in this pass.
