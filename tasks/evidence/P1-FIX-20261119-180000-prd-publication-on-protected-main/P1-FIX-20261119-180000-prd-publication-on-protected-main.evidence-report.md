## 人审导航 / Human Review Navigation

| 人工审查项 | 呈递物 | 打开方式 | 核对结果 |
|---|---|---|---|
| 检查 #398 是否已复用原 Issue 发布 PRD | [GitHub Issue #398](https://github.com/ZataZhang/keda/issues/398) | `gh issue view 398 --json url,labels,body` | Issue URL 仍为 #398；正文只有一个 `PRD path:` 锚点；可点击链接固定到包含 PRD 的 `issue-398` 发布提交；`agent/ready` 在发布完成后出现。 |

- PRD：[`tasks/pending/P1-FIX-20261119-180000-prd-publication-on-protected-main.md`](../../pending/P1-FIX-20261119-180000-prd-publication-on-protected-main.md)。
- GitHub Issue：[#400](https://github.com/ZataZhang/keda/issues/400)。
- PR / CI：实现尚未由 runner 发布；PR 与必需 CI 链接由后续发布流程补入。
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
- Tests: the changed publication, branch-source, and CLI test files ran with testmon disabled: **174 passed**.
- Repository test entry: `just test` accepted the current success marker and skipped as designed; the targeted pytest command above executed the relevant cases directly rather than relying on that shortcut.
- Lint: `SKIP=check-test-flag just lint --full` passed all remaining full-file hooks. `check-test-flag` alone was skipped because pre-commit temporarily hides unstaged files, which changes the marker tree; the relevant tests were executed directly afterward.
- Documentation: `uv run mkdocs build` passed. MkDocs emitted existing INFO notices for unlinked pages and an optional signature formatter.
- Manifest replay: `kc evidence check --evidence tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main` passed **4/4** on the executor run and independent verifier rerun; the latest per-item runtimes were 4.6s, 12.8s, 12.2s, and 71.7s, all under the 300s gate.
- PRD archive-readiness check passed after final reconciliation; the runner owns the actual PRD archive move.

All Git operations used temporary repositories and real Git processes. GitHub Issue API traffic was handled by a fail-loud local `gh` fixture; no real account, Issue, branch, or business data was contacted. The evidence files and scripts contain no credentials. Raw evidence and scripts are ignored by Git; only the three Markdown reports and PRD belong in the code change.
