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

This section records the findings against the `e87337f5` snapshot and the then-current read-only GitHub view. The later worktree repair and its final disposition are recorded in the final section below.

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

## Final local repair snapshot — 2026-11-20 (`1153018e` + worktree changes)

The local source now incorporates the runner admission wrapper already present on `main` and keeps the Issue #400 task-branch PRD lookup before worktree creation. The worktree has not been staged or committed, and no remote ref or PR metadata was changed.

| Finding | Final disposition | Evidence |
|---|---|---|
| Runtime conflict with `main` | **Fixed in the worktree; remote PR still awaits commit/push.** | The final `agent_runner_orchestration_runtime.py` was checked with `git merge-file -p` against merge base `f56f2d1f` and local `main`; exit 0. The runtime imports the shared admission module and routes each Issue through `_process_issue_if_admitted` while retaining the task-branch PRD resolution call. GitHub still reports PR head `1153018ec124f6318c4a9d9a1702054cb9f17a4c` / base `6dceaa0ba94c285776e76109a324051d1dd04d79` as `CONFLICTING`; local edits are not published. The local `main` ref is `51461449cb1069113b3d6a827297f92c16bcff2f`. |
| Stale PR body and validation comment | **Valid; cannot update under the worktree-only/no-push constraint.** | PR #405 body still names Round 2, `a856cf8`, and `174 passed`; comment #7016416947 remains tied to head `74ba5cb` / base `6dceaa0`. The current verification snapshot is recorded locally below and in verifier Round 5. |
| Lifecycle function summary | **Already fixed; retained.** | The function summary says published task-branch fetch/read failures re-raise and stop the pipeline (`lifecycle_agent_resolution.py:479–483`). |
| Lifecycle Returns contract | **Already fixed; retained.** | Returns distinguishes main-checkout read failures from task-branch fetch/read failures and parser failures (`lifecycle_agent_resolution.py:491–493`). |
| Independent verifier conclusion for final source | **Fixed with verifier Round 5.** | A separate read-only verifier reviewed the modified worktree and passed the code/local conflict resolution. Round 4 remains historical for `a914f022`; it does not cover this newer source/test tree. |

### Final worktree checks

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed, including duplicate-code, architecture, guideline consistency, and max-file-lines checks.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache SKIP=check-test-flag just lint --full`: passed; only `check-test-flag` was skipped because the worktree is unstaged.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: **77 passed, 266 deselected**.
- Final targeted tests: **99 passed** across admission, orchestration, Direct PR discovery, branch-source, and lifecycle routing tests.
- Final `kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main`: **4/4 passed**. RV-4 reports **176 passed** and a successful MkDocs build.
- `git merge-file -p` for `agent_runner_orchestration_runtime.py` against merge base `f56f2d1f` and `main`: exit 0. No branch merge, rebase, stage, commit, or push was run.
- `git diff --check`: passed after the evidence append.
- `jq -e . .agent-runner/commit-request.json`: passed; the ignored local commit request is ready for the runner.
- Current modified source/test/docs fingerprints:
  - runtime: `c01e0c4502470a4685974b003b3649737a0209bc09128567c9633eb88bc1903a`
  - admission model: `38b3fe05a2d889ca533fb8b7b9135faddd5fe72a540b4448045029c74ffa1b54`
  - shared admission module: `f41491ccb86615f3cd30c894aa156f943038b6c96d30d8c20f62fe9b4b40c376`
  - new admission tests: `f697e127e603c1b64e218809ceb88602a8e67e16b24c46b54911f69176fc38a5`
  - runner guide: `602d7466f5e73e9e194b1ffe71ea0cedcd149a8ef4ab845c4868edd9c05e55fa`
- Final raw RV output SHA-256: RV-1 `c2dd017caa414a3c1f2265a0f8e11ded0bde752b37ac95dae32453dc86761495`; RV-2 `2112b3c2d8efaab2433772f3257c1bfeccc2108a4877b19b7f4cf76930fbaa21`; RV-3 `4396a061e04dc37dde0509ea130a5fed0e8eba2ab44d2c090f77d3d1faca9744`; RV-4 `1198715a312cedee6612920af2367fa03dd6ebdd8e472c91f682f2f75eb0225f`.

## Prior reviewer repair audit — 2026-11-20 (`e2ac3012`)

The source and test tree matches verifier Round 5. This historical audit updated the integration and metadata reading against the local `main` snapshot at that time; it did not merge or rewrite branch history, publish GitHub metadata, or replace the independent verifier result.

| Finding | Current disposition | Evidence |
|---|---|---|
| Branch history is not synchronized to current `main`; runtime conflict was reported | **History sync remains pending; the runtime content-conflict claim is disputed for this snapshot.** | Current `main` is `51461449cb1069113b3d6a827297f92c16bcff2f`; HEAD is `e2ac3012e589adef357c6e605e2dbea4d91c63ec`. `git merge-tree --write-tree main HEAD` exited 0 and produced `f49ac9ffc89af8eae3b97aa0bdc0a2c6cd2750e2`, with no conflicted paths. The commit graph is still diverged 10 ahead / 15 behind from merge base `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`; this dry-run proves file-content mergeability, not ancestry sync or GitHub integration readiness. The explicit no-merge/no-rebase/no-push constraint prevented syncing the history. |
| PR description, validation comment, and PRD reconciliation cite old snapshots | **Valid; local PRD conclusion refreshed, GitHub metadata remains stale.** | Read-only PR #405 metadata reports head `e2ac3012e589adef357c6e605e2dbea4d91c63ec`, base branch `main`, base SHA `6dceaa0ba94c285776e76109a324051d1dd04d79`, and `mergeable=true`. The current `main` is `51461449`; therefore that mergeable value is for the stale base snapshot. The PR body still names verifier Round 2, `a856cf84008cb2261325844d678c46bd869bf9a5`, tree `d21fb64e29b9e2dec4a209e36ab89ca0f25e11af`, and 174 tests. Validation comment [#issuecomment-7016416947](https://github.com/ZataZhang/keda/pull/405#issuecomment-7016416947) still anchors to head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd` / base `6dceaa0ba94c285776e76109a324051d1dd04d79`. This worktree-only repair did not write either remote field. |
| Lifecycle summary says task-branch failures re-raise | **Already correct; retained.** | `lifecycle_agent_resolution.py:479–483` says a published task-branch fetch/read failure is re-raised and prevents the pipeline from continuing. |
| Lifecycle Returns conflates main and task-branch read failures | **Already correct; retained.** | `lifecycle_agent_resolution.py:491–493` distinguishes main-checkout read failures, task-branch fetch/read failures, and parse failures. Current SHA-256: `4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`. |
| Independent verifier conclusion missing from the current code tree | **Already satisfied by Round 5.** | Round 5 is `PASS` and its runtime (`c01e0c4502470a4685974b003b3649737a0209bc09128567c9633eb88bc1903a`), lifecycle (`4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`), admission module (`f41491ccb86615f3cd30c894aa156f943038b6c96d30d8c20f62fe9b4b40c376`), and admission test (`f697e127e603c1b64e218809ceb88602a8e67e16b24c46b54911f69176fc38a5`) fingerprints match this worktree. It covers the code and RV evidence, not main ancestry synchronization. |

### Checks against the current source tree

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed copy detection, duplicate-code, architecture, guideline consistency, and max-file-lines checks.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --full`: returned successfully but skipped the full lint because there were no uncommitted code changes; it reported only that all changes are non-code files.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: skipped pytest because the successful marker already matched `issue-400 @ e2ac3012` / tree `985d359d`.
- Direct tests with testmon disabled across `test_agent_runner_issue_admission.py`, `test_agent_runner_cli.py`, `test_agent_runner_direct_pr_discovery.py`, `test_issue_prd_branch_source.py`, and `test_lifecycle_agent_routing.py`: **203 passed** in 6.79 seconds.
- `kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main`: **4/4 passed** (RV-1 2.8s, RV-2 4.1s, RV-3 6.1s, RV-4 24.2s). RV-4 includes the compatibility tests, lint hooks, and MkDocs build.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . mkdocs build`: passed in 3.94s; MkDocs emitted its existing unlinked-page and optional signature-formatting INFO notices.
- Three-way content merge dry-run: `git merge-tree --write-tree main HEAD` exited 0; output tree `f49ac9ffc89af8eae3b97aa0bdc0a2c6cd2750e2`.
- Final raw RV output SHA-256 after the last evidence replay: RV-1 `9b1e6f56c73b537e074c7ed7c51df53fa0f25b5a2ebd9495d94f24125df03b9f`; RV-2 `44dd05c894a3c3af9b85a1064398197e732f9e84392dd88afe8815c670614450`; RV-3 `b84d48c327cd8922dc3dd675adb9f538981f4e0018b79e85705c371c3fae786f`; RV-4 `5530f753e65de6d11154d1f04dd1b7b3d572be682826809bcdebc06c72d0f66e`.
- `git diff --check`: passed after the current evidence record edits; `.agent-runner/commit-request.json` parses with `jq -e .`. GitHub reads were read-only; no PR metadata, branch refs, or Issue state changed.

## Current reviewer repair audit — 2026-11-20 (`6045419d` source/test snapshot)

The current worktree started at HEAD `6045419d9815b061e3db875b0f2665c1607af8c2`. Its complete commit tree is `fc02a8fa7d622aa8549d855e4f9e9edf8ae10fdd`; the source and test trees are `2e8ef0c3d07fb8fbabd398989002835a228f0ab1` and `202f5caa5939456e07d1755fa053baa2f502b82b`. Both source and test trees match `e2ac3012` and verifier Round 5 exactly; this audit reruns the affected checks and updates local records. The PRD/evidence edits in this repair do not modify source or tests.

| Finding | Current disposition | Evidence |
|---|---|---|
| PR branch history is not synchronized to current `main` | **Confirmed; pending because this pass cannot publish an updated branch.** | Local `main` is `51461449cb1069113b3d6a827297f92c16bcff2f`; HEAD is `6045419d9815b061e3db875b0f2665c1607af8c2`. `git rev-list --left-right --count main...HEAD` returns `15 11` (main-only / branch-only), merge base `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`, and `main` is not an ancestor of HEAD. `git merge-tree --write-tree main HEAD` exits 0 with tree `fa95256aa2abed978185fab290dae8a3641375be` and no conflict paths, which proves content mergeability only. No branch switch, merge, or history rewrite was performed. A local-only rebase would not update GitHub's PR head under the explicit no-push rule. |
| PR body, validation comment, and local PRD cite old snapshots | **Local PRD reconciliation fixed; GitHub body/comment remain stale and untouched.** | Read-only PR #405 metadata reports head `6045419d9815b061e3db875b0f2665c1607af8c2`, base branch `main`, base SHA `6dceaa0ba94c285776e76109a324051d1dd04d79`, and `mergeable=true` for that stale base. The body still records verifier Round 2, verified head `a856cf84008cb2261325844d678c46bd869bf9a5`, tree `d21fb64e29b9e2dec4a209e36ab89ca0f25e11af`, and 174 tests. Validation comment [#issuecomment-7016416947](https://github.com/ZataZhang/keda/pull/405#issuecomment-7016416947) still anchors to head `74ba5cb248dcf8ae73f5106ba4c46df4f2cc9afd` / base `6dceaa0ba94c285776e76109a324051d1dd04d79`. This pass refreshed the local PRD reconciliation only; worktree-only scope prohibited remote writes. |
| Runtime file content conflict was previously reported | **Disputed for this snapshot; no content conflict remains.** | The current `git merge-tree --write-tree main HEAD` returned no conflicted paths. The runtime file SHA-256 is `c01e0c4502470a4685974b003b3649737a0209bc09128567c9633eb88bc1903a`. A clean content merge does not establish history synchronization. |
| Independent verifier conclusion missing for current source/test tree | **Disputed; already satisfied by Round 5.** | Round 5 is `PASS`; its runtime (`c01e0c4502470a4685974b003b3649737a0209bc09128567c9633eb88bc1903a`), lifecycle (`4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`), admission module (`f41491ccb86615f3cd30c894aa156f943038b6c96d30d8c20f62fe9b4b40c376`), and admission test (`f697e127e603c1b64e218809ceb88602a8e67e16b24c46b54911f69176fc38a5`) hashes match this worktree. The current commit adds only PRD/evidence records relative to that code/test snapshot. |
| Lifecycle summary says published task-branch failures re-raise | **Disputed; documentation already matches behavior.** | `src/backend/core/use_cases/lifecycle_agent_resolution.py:479–483` states that fetch/read failure for a published task-branch PRD is re-raised and blocks pipeline continuation. SHA-256: `4bf17a9af070c8340b666155fefc91ddfd1e74e78e6d51a14eadffea27a2feaf`. |
| Lifecycle Returns conflates main-checkout and published-branch failures | **Disputed; documentation already distinguishes them.** | `lifecycle_agent_resolution.py:491–493` documents main-checkout read failure returning the original Issue, task-branch fetch/read failure raising, and parse failure returning the original Issue. The same lifecycle file fingerprint above confirms the reviewed source. |

### Checks rerun against the current source/test snapshot

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync pytest --no-testmon tests/test_agent_runner_issue_admission.py tests/test_agent_runner_cli.py tests/test_agent_runner_direct_pr_discovery.py tests/test_issue_prd_branch_source.py tests/test_lifecycle_agent_routing.py`: **203 passed** in 6.86s.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main`: **4/4 passed** (RV-1 3.2s, RV-2 4.1s, RV-3 6.2s, RV-4 25.1s). RV-4's embedded compatibility gate records its own focused 176-test run, full lint hooks, and MkDocs build.
- Current RV output SHA-256: RV-1 `3903ba975ef232db050f0f6d45c6c8cde556c796f4e8fc3f6d095465d3efc25f`; RV-2 `d7ccdc3d229feed38c619cca0e9d3937ac46039a1a0843a476cc97d20899c84b`; RV-3 `8716e0fac9f712bdeb1fea7c7f78b3874145bab8547de65555eedbfe159516fb`; RV-4 `e8cdba28f185fbf0b9f768af588052fc50590c5f2e9236668f59d7b1a2836369`.
- Code/test tree comparison: `git diff --quiet e2ac3012 HEAD -- src tests` exited 0. Current `HEAD:src` and `HEAD:tests` tree IDs match `e2ac3012:src` and `e2ac3012:tests` exactly.
- Integration check: `git merge-tree --write-tree main HEAD` exited 0, tree `fa95256aa2abed978185fab290dae8a3641375be`; this is not a branch sync or a check against the stale GitHub base SHA.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync python /Users/zata/.codex/skills/prd/scripts/check_prd_acceptance_checklist.py --repo-root . --check-provided --archive-ready tasks/archive/P1-FIX-20261119-180000-prd-publication-on-protected-main.md`: **PASS**.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . mkdocs build`: **passed** in 4.16s. MkDocs emitted its existing unlinked-page list and optional Black/Ruff signature-formatting INFO notice.
- `jq -e . .agent-runner/commit-request.json`: **passed**; the requested runner commit message is valid JSON.
- `git diff --check`: **passed** after the audit edits.
- The evidence runner emitted a non-fatal permission warning when it attempted to create `/Users/zata/code/keda/logs/app-2026-11-20.log` outside this worktree; all four evidence items still passed. The initial read-only PR snapshot and comment fetch confirmed stale remote records; no GitHub fields, refs, or Issue state were changed.

## Current reviewer repair verification — 2026-11-20 (`14f87c01` plus worktree changes)

This is the latest local snapshot for the pinned-commit repair. HEAD remains `14f87c0163b3e36a5d924958d1d1aea867fc6e1c99`; source, test, skill, and guide changes are uncommitted worktree content for the runner. The independent Round 6 verdict above predates these changes and does not certify this snapshot.

| Finding | Disposition | Evidence |
|---|---|---|
| Operator skill exceeds the 70-line merged-tree limit | **Fixed.** | The protected-base/recovery route terms moved into the existing `建 issue` row, removing one non-empty line. The worktree main file has 69 non-empty lines; read-only `git merge-file -p` against the current merge base and `main` also returns a clean merged file with 69 non-empty lines. |
| PR branch history is not synchronized with current `main` | **Confirmed; unresolved under the no-branch-change constraint.** | `main` is `51461449cb1069113b3d6a827297f92c16bcff2f`; HEAD is `14f87c0163b3e36a5d924958d1d1aea867fc6e1c99`; `git rev-list --left-right --count main...HEAD` is `15 13` and merge base is `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`. `main` is not an ancestor. No branch switch, merge, rebase, commit, or push was run. |
| PR description and validation comment cite old snapshots | **Confirmed; GitHub metadata untouched.** | The supplied review reports stale PR #405 head/base, verifier, and test claims. This worktree-only pass did not edit the PR body or comment; the local evidence below is not a substitute for updating them after the runner publishes the new head. |
| Archived PRD Final Reconciliation cites old head and divergence | **Fixed locally.** | Its current reconciliation names HEAD `14f87c01`, `main` `51461449`, and `15/13`, distinguishes content mergeability from ancestry sync, and records this repair's test and RV results plus outstanding verifier/remote follow-ups. |
| Runner reads branch tip instead of marker commit | **Fixed.** | `read_published_issue_prd_text()` validates the marker's branch and commit together, fetches the branch, then executes `git show <marker-commit>:<PRD-path>`. A real local remote regression advances the branch and changes PRD bytes; the function returns the text from the pinned commit. |
| Content conflict with current `main` | **Resolved for the current committed snapshot.** | `git merge-tree --write-tree main HEAD` exits successfully with tree `f96d7ca7a5a034d44278457e47c57b86f061f1b2` and no conflict paths. The branch history remains unsynchronized; this dry-run excludes worktree changes and does not establish GitHub PR integration. |
| Lifecycle summary and Returns docstrings | **Already correct.** | `lifecycle_agent_resolution.py:479–493` distinguishes main-checkout read failures from published task-branch failures, which raise. No docstring change was needed. |
| Independent verifier Round 6 finding | **Previously satisfied, but stale for this repair.** | Round 6 remains valid for its recorded source/test fingerprints. Since this repair changes `issue_prd_publication.py` and its test, no new independent verifier verdict is claimed here. |
| Invalid published anchors fail closed | **Already fixed and covered by the current RV replay.** | RV-4 includes lifecycle routing regression tests for missing, out-of-repository, and non-Markdown anchors. |
| Operator skill's top-level issue route | **Already fixed and retained.** | The existing route now carries both `--publish-to-issue-branch` and `--recover-issue <N>` within the line budget. |

### Checks on this worktree

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: passed jscpd, duplicate-code, architecture, guideline consistency, and max-file-lines.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache SKIP=check-test-flag just lint --full`: passed all remaining full-lint hooks. The first unsuppressed attempt correctly rejected the stale test marker; the following `just test` refreshed it, and a final unsuppressed `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --full` passed.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: **108 passed, 341 deselected**.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . pytest --no-testmon -q tests/test_issue_prd_publication.py tests/test_kedacode_operator_skill.py tests/test_lifecycle_agent_routing.py`: **64 passed**.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main`: **4/4 passed** (RV-1 2.7s, RV-2 4.3s, RV-3 6.8s, RV-4 25.7s). RV-4's embedded targeted suite reports **231 passed** and includes lint plus MkDocs build.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . mkdocs build`: passed in 4.31s. MkDocs emitted its existing unlinked-page and optional signature-formatting INFO notices.
- `git diff --check`: passed on the final worktree. Current source/test diff fingerprint: `3d508f3c02a29169b0ec7b5f8b1091d9809297f7d582f7246e637842dbec0cc3`; current source/test/docs diff fingerprint: `51e3a424e871755e7bd352319c69191326bd14e0cd731e65dde8803a4a415db0`.
- Final changed-file SHA-256: publication source `8d26b9a846b887299a0c5e4f0d179fa0997fc0af26c26d3f01378458e7808d09`; publication tests `f604a6343d355c837a57d17da39482ab24cdf4b3c84f8a3ffda9c35b9de2017f`; operator skill `7d63f676c2bc8e6b2700c35d8f4ebcea5b95a7b8c295a8da9edc62e25abf7ae4`; create-issue reference `fe3270915c5c04480f94ed634d65a76812e33b35dd70414b5fd605f576fc5250`; runner guide `54a0784fed817a69af6250aa53ef692b05ac2c928246ef0a596f997e9448c4be`.
- Refreshed raw RV SHA-256: RV-1 `e86804c6d89d6d00bd5fafb7163dad9c24f200e68102ed718c40f8dce8adaa1b`; RV-2 `c304ff7edede0767a84a994dcaf50ecbcf1dcc184dbb92e39056bbf70f1a0c36`; RV-3 `cf3f9dd72f84faffdde15a2ba5b6cdaf8e587f9c45f667a16c3915d2079d4cc0`; RV-4 `a221aed551ef0b7f4c84833de571bce274413767e70339d303fa822baceffd14`.
- `.agent-runner/commit-request.json` is valid JSON and runner-local, so it is not part of the source/test fingerprint. No GitHub metadata or branch refs were modified. A fresh independent verifier, synchronized history, and PR #405 metadata refresh remain outstanding.

## Current reviewer repair audit — 2026-11-20 (`f55efead` base with final worktree changes)

The worktree started from commit `f55efeadb41d282b6d1b662369e5dda4c64db689`. This repair adds a fail-closed check for invalid PRD anchors on published Issues, updates the packaged operator route and runner guide, adds three regression cases, and refreshes RV-4's targeted suite. The local `evidence.json` is ignored by Git and now records RV-4's 230-test result. The runner commit request is present at `.agent-runner/commit-request.json`.

| Finding | Disposition | Evidence |
|---|---|---|
| PR branch history is not synchronized to current `main` | **Confirmed; unresolved under the explicit no-merge/no-push instruction.** | Local `main` is `51461449cb1069113b3d6a827297f92c16bcff2f`; commit HEAD is `f55efeadb41d282b6d1b662369e5dda4c64db689`. `git rev-list --left-right --count main...HEAD` returns `15 12`; merge base is `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`; `main` is not a HEAD ancestor. `git merge-tree --write-tree main HEAD` yields `334d00ac3ffe75a047f1dfa23188800569838049` with no conflict paths. This checks committed content mergeability only; it does not synchronize history or include the uncommitted final worktree diff. |
| PR body and validation comment use old snapshots | **Locally reconciled; remote PR records remain unchanged.** | The previous read-only snapshot recorded PR #405 with an old head/base and old Round 2 / 174-test evidence, and comment #7016416947 with old head `74ba5cb` / base `6dceaa0`. This worktree-only task did not read or update GitHub metadata. Local RV-1–RV-4 outputs and PRD reconciliation now describe the current worktree validation, but no remote record can refer to it before the runner commits and publishes the work. |
| Published PRD marker with a missing, out-of-repository, or non-Markdown anchor is skipped | **Fixed.** | `attach_prd_lifecycle_overrides()` now parses the existing publication marker before path early returns and raises `ValueError` for each invalid published-anchor case. `tests/test_lifecycle_agent_routing.py` adds regression coverage for all three; RV-4 runs this file. |
| Top-level packaged operator skill omitted protected-base publication/recovery routing | **Fixed.** | The `建 issue` route names protected-main publication and existing-Issue recovery and points to `references/create-issue.md`; a short instruction routes to `--publish-to-issue-branch` or `--recover-issue <N>`. The RV-4 suite runs `tests/test_kedacode_operator_skill.py`. |
| Earlier runtime-content-conflict finding | **Disputed for committed HEAD; no conflict path remains in the current dry-run.** | The current `git merge-tree --write-tree main HEAD` exited successfully without conflict paths. The current worktree still is not based on main, and the dry-run is not an integration or ancestry verdict. |
| Earlier missing independent-verifier finding | **Fixed with Round 6 PASS for the new source/test tree and fresh RV evidence.** | The separate read-only verifier confirmed the invalid-anchor behavior, compatibility path, RV output hashes, and inclusion of lifecycle plus operator-skill tests. It independently ran focused tests (56 passed); the full Round 6 result is in the verifier report. |
| Lifecycle summary and Returns contract findings | **Disputed; retained as already correct.** | The docstring continues to distinguish recoverable main-checkout errors from published task-branch fetch/read errors, and now explicitly documents invalid published-anchor exceptions. |

### Checks on this worktree

- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just lint --reuse`: **passed** copy detection, duplicate-code, architecture, guideline-consistency, and max-file-lines checks.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache SKIP=check-test-flag just lint --full`: **passed**; only the `check-test-flag` hook was skipped because pre-commit hides unstaged files.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync pytest -q --no-testmon tests/test_lifecycle_agent_routing.py tests/test_issue_prd_branch_source.py`: **28 passed**.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache just test`: **102 passed, 319 deselected**.
- `UV_CACHE_DIR=/private/tmp/kc-issue-400-uv-cache uv run --no-sync --project . kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main`: **4/4 passed** (RV-1 2.6s, RV-2 4.0s, RV-3 5.9s, RV-4 19.1s). RV-4 reruns the publication, branch-source, CLI, packaged-skill, and lifecycle tests (**230 passed**), full lint, and MkDocs build.
- Final raw RV output SHA-256: RV-1 `c4a4c1294e0d444971f53517b2bac43fb91060f1b2e32e301d0048eed8a2f40e`; RV-2 `90bdf23f8480c26c8ec5fd94f3d48d2dcf4a5c663c52f38ceafbadfd340af292`; RV-3 `96d6e9bd24f99e6042c0adcfa5c4634110de71f590e5a82d9c400d90a2889a39`; RV-4 `935d05cda4652416e06280966107d9282758fb1f80165b810dd4d8eaa9d85ec8`.
- Final file SHA-256 fingerprints: lifecycle source `c7d456ceffa9705c50dd7860f5954b6cef7bc609a42a74d81f73791b29c9a19a`; lifecycle tests `598268e52497949b09f4624dc53c7cda5338f475d10c65a07fc46d98fb2618a4`; operator skill `d96486d6b39fa12285d4c779ab0e6ec043f993a666205be54bcc065b8a2ae9b4`; runner guide `2330b929a0db58e78f96ed70a5320a281a83823bc8db328d16ea2a4c1aa68c73`; RV-4 script `aa380e9761df5db254b2844b1cf555841fea045f08b837bcd88b1b67b130e005`.
- Final tracked source/test/docs diff fingerprint: `git diff --binary HEAD -- src tests docs | shasum -a 256` = `ea853ceaff94c838971727aa5285470743c7ccc012727ef987be70ad8b315c9d`. Independent verifier Round 6: **PASS**, with a separate focused run of **56 passed**.
- The previous line-numbered findings about the lifecycle summary and Returns docstring were verified against this worktree; the exact current lines are cited in the final repair report.
