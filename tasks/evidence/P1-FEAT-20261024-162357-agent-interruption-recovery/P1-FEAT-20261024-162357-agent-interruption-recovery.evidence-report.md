## 人审导航 / Human Review Navigation

| 要看的结果 | 呈递入口与打开命令 | 十秒自查 |
|---|---|---|
| 无法自动恢复时，报告能指导人处理；离线通知不会假成功 | `tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery/rv-3-human-handoff.txt`；运行 `open "tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery/rv-3-human-handoff.txt"`。归档后可运行 `just prd review tasks/archive/P1-FEAT-20261024-162357-agent-interruption-recovery.md`。 | 查看嵌入的真实报告原文，确认它写明失败步骤、已完成成果、未知 PR 创建、已尝试处理、下一步和恢复入口；通知在 429 后仍是待投递。证据另记录同一正文恢复后只投递一次。 |

执行者已交叉核对该呈递产物。当前尚无 PR 或 CI 链接：发布由 runner 负责，文件落盘不代表人工验收。

**证据可访问性限制：** 上表中的原始文件和重放脚本仅存在于执行机器，不包含在 Git 提交中；干净检出无法打开或重放。尚未提供可访问的证据分支或等效产物，不能将下方摘要视为独立验证。下面是本地 rv-3 文件中实际保存的报告摘录，供审阅时直接阅读：

> - 状态：⏸️ 自动恢复预算耗尽或需要人工处理；业务写入已停止。
> - 失败步骤：`ready` / `outcome_unknown`
> - 已完成成果：保留故障前已落盘的 worktree、commit 与通过验证的阶段；未持久化的内容不会被视为已完成。
> - 未知或未完成的对外写入：`draft_pull_request_create` `repos/owner/repo/pulls:issue-73:main`：`unknown`。
> - 已尝试处理：单次 gh 调用最多 5 次 / 60 秒；恢复总预算最多 30 分钟、6 次调度恢复。
> - 下一步：按操作键核对 GitHub 对端对象；确认已创建则保留现状，确认未创建且目标可安全补发后，运行 `kc run --issue 73`。
> - 恢复入口：`kc run --issue 73`；发布恢复也可用 `kc recover --issue 73`。
> - 通知状态：待投递；本地报告已保存，通知成功前不会标记为已送达。

本摘录只改善可读性，不替代完整原始证据、最终代码树重验或独立 verifier 的结论。

## Delivery and source identity

- Issue #457 continues from the existing WIP checkpoint `6328f89ddb762a22fbcdcc9d7a54ae954709bf9b`; no prior commits were reverted.
- The following identity describes the earlier executor collection, not the current committed tree; it must not be reused as final-tree evidence:
  - `HEAD` tree: `3076e113312f05ffb0249a1f6f46aaaebed01e54`
  - Worktree diff SHA-256: `c6a91ae436c337c147cc53d0a4c262096d6a5dfc53f0d8152e4364a8666a6973`
  - `run_recovery_rv.py`: `0a4764459e17fbc42e4b856efffeace6b498c9ce511f34cd68be00dbb837b7c2`
  - `rv_harness.py`: `38fdb61698b305478bdb5687f565cd6f08324084002ca3f0548eda6cc864e9b0`
  - `rv_setup.py`: `67b53f0abebb32a036e94879312d135a0372384918fc14f1a08363fbd3b9ea86`

## RV outcomes

- **rv-1 — stage resume:** Real `kc run --issue 73` persisted a waiting record after five observed TLS socket drops during PR reads. A fresh daemon reused the completed validation stage and published one PR; the Agent invocation count stayed at 1. Verification stayed at 2 invocations across unchanged resume, then rose to 3 after changing the validation command contract without rerunning the implementation Agent. A fresh SQLite probe showed the recovery run `completed`, one confirmed `git_push`, one confirmed draft PR create, and a passed validation stage. See `rv-1-stage-resume.txt`.
- **rv-2 — uncertain creation:** A PR persisted before its response was dropped; after restart the operation changed from `unknown` to `confirmed`, with one remote PR and one create request. The operation key, payload digest and budget deadline were retained. A test-side refusal before TLS/API bytes safely reached one confirmed PR. A server-side no-create result without an authoritative negative proof and a 403 reconciliation query both remained unknown with one create request and no PR. The claim marker was found on page 2 after 100 existing comments. The negative control bypassed the ledger, received successful `gh` exit status, produced two PRs, and failed the unique-object assertion as required. See `rv-2-unknown-write.txt`.
- **rv-3 — human handoff:** The real runner saved the local report with status, failed step, unknown operation key, completed work, next step, recovery entry and pending notification. The first notification received HTTP 429; after a fresh daemon start the same saved body was delivered once with its stable operation marker. The budget deadline remained unchanged and the PR create request count stayed at 1. See `rv-3-human-handoff.txt` for the complete captured report and fresh SQLite state.

## Red controls and structured evidence

- `rv-1-stage-resume-negative-control.txt`: after the green real-entry run, the test boundary injects a duplicate invocation observation (`1` → `2`) into the same assertion used by the green path; it exits 1 with the expected single-Agent assertion failure. This proves the observation oracle detects a duplicate count; it does not inject a fault into production code.
- `rv-2-unknown-write-negative-control.txt`: a real `gh` write bypasses the ledger and creates a second PR; the remote count is 2, and the unique-object assertion exits 1 as required.
- `rv-3-human-handoff-negative-control.txt`: after validating the actual runner-produced report, the test boundary deletes a copy and the same report-presence assertion exits 1. The production report remains untouched.
- `evidence.json` contains the three numeric checklist blocks, green commands, red-control commands, item-specific artifacts, expected failures, and exact stdout assertions. `UV_CACHE_DIR=/private/tmp/uv-cache uv run kc evidence check --evidence tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery` replayed all three green commands and passed the stdout checks (3/3).

The test forge is not GitHub and does not prove GitHub's internal propagation timing, production DNS/TLS behavior, or carrier-network causes. It does prove that real `gh` subprocesses crossed an actual local TLS socket and that the server independently observed the requests and resulting objects.

## Regression and repository checks

| Check | Result |
|---|---|
| PRD-directed recovery suite: `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/test_github_client.py tests/test_agent_runner_recovery.py tests/test_agent_runner_publish.py tests/test_recover_publish.py tests/test_runner_recovery_store.py -q --no-testmon` | **142 passed** in 19.88 s. |
| Supplemental changed-caller suite: validation, supervisor repair/rebase, merge queue, worktree branch, recovery publish, and recovery-store tests with `--no-testmon` | **316 passed** in 3.26 s. |
| `UV_CACHE_DIR=/private/tmp/uv-cache just test` | Command used the documented `.last_tested_commit` fast path and **skipped pytest**; it is not counted as test evidence. The prior WIP full-suite result predates the changes in this diff. |
| `UV_CACHE_DIR=/private/tmp/uv-cache just lint --repo` | **Passed**: pre-commit/Ruff, duplication, architecture, guidance consistency, file-line limits, and strict documentation build. Its local test flag also skipped pytest. |
| `UV_CACHE_DIR=/private/tmp/uv-cache uv run mkdocs build --strict` | **Passed** in 6.22 s. |
| `UV_CACHE_DIR=/private/tmp/uv-cache uv run kc evidence check --evidence tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery` | **Passed 3/3** after the final manifest edit; all green commands and stdout assertions replayed within the 300 s item limit. |
| PRD skill `check_prd_acceptance_checklist.py --check-provided --archive-ready ...` | **Passed** the explicit validation-blocked path; the PRD remained in `tasks/pending/` and was not moved. |
| `git diff --check` | **Passed**. |
| Python compile check | **Passed** for modified Python files and all three ignored RV scripts. |

## Change and test inventory

- `recover_publish.py` now uses the shared strict journaled `push_changes` operation. Its default PRD archive gate remains enabled. A regression test reads the SQLite operation row and requires `git_push=confirmed` after `kc recover` completes.
- New `agent_runner_git_recovery.py` journals issue-scoped Git ref updates. Production call paths pass the GitHub client for post-PR repair pushes, post-PR rebase pushes, validation evidence-branch updates, and closed-Issue evidence-branch deletion. Rebase pushes retain the pre-rebase head lease; evidence updates use `--force-with-lease`; branch deletion is constrained to the SHA observed during enumeration. Unknown outcomes only reconcile and do not blindly resend.
- Store tests cover confirmed and unknown Git push outcomes, no-resend after ambiguous results, and preservation of the explicit rebase lease. The worktree-branch regression now expects the guarded ref/SHA command.
- The autouse test fixture clears the active runner-claim guard before and after each test, eliminating the cross-test ContextVar leakage behind the three failures recorded in the previous `just test all` handoff.
- Recovery-store tests now reject reusing an operation key with a changed payload and distinguish a rate-limit 403 from an authorization 403.
- The guide, architecture page, and packaged operator triage reference describe ledgered manual recovery push behavior.
- The canonical PRD's §9.1 now points out that the real rv-3 artifact exists; its Change Log records evidence collection without marking checklist items.
- `tests/guards/` was not changed. No CLI flag, subcommand, exit code or machine-output contract was changed.

### Write-entry inventory and remaining scope

| Entry family | Current observed handling | Status |
|---|---|---|
| Issue comments, label changes, Issue creation, Draft PR creation, PR merge/close | Central GitHub adapters use explicit operation identities and reconciliation; RVs exercise comments, labels and Draft PR creation. | Covered in the tested runner path; other endpoint-specific states still require independent review. |
| Normal branch push and `kc recover` push | `agent_runner_publish.push_changes` persists a `git_push` intent and reconciles the expected remote ref; manual recovery reuses this function. | Implemented and regression-tested. |
| Post-PR repair/rebase and validation evidence branch updates | Runtime call paths use `agent_runner_git_recovery.mutate_git_ref_with_recovery`; rebase uses the pre-rebase head as an explicit lease, evidence update uses `--force-with-lease`, and ambiguous outcomes reconcile against `ls-remote`. | Implemented for issue-scoped production callers; helper retains a no-client branch for isolated legacy/unit callers. Store tests plus RVs cover the primary normal push path. |
| Closed-Issue evidence-branch deletion | `cleanup_closed_issue_evidence_branches` journals the delete and sends `--force-with-lease=<ref>:<observed-sha>` after enumerating the remote refs. | Implemented for the daemon validation gate; focused validation tests verify the lease command. |
| Initial PRD publication | `issue_prd_publication.py::_publish_prd_commit` and `create_issue_from_prd_helpers.py::publish_prd_file` still push directly during `kc issue create` / PRD publication recovery. These are outside `kc run`, daemon scheduling and `kc recover`, but the PRD wording “runner-initiated writes” needs an explicit boundary review before counting the inventory complete. | Open scope review; no claim of coverage. |
| Evidence retention cleanup | `agent_runner_evidence_prune.py::delete_remote_branch` is invoked by the separate explicit `kc evidence prune` maintenance command and still pushes a branch deletion directly. It is not a task recovery/scheduling path; if the Issue scope includes all `kc`-initiated maintenance, this needs a follow-up ledger design. | Open boundary review; no claim of coverage. |

The table is a source scan, not a claim that every runner-managed write is complete. Keep the broad write-inventory acceptance item open until the PRD publication and evidence-retention paths are explicitly classified or covered with evidence.

## Incomplete acceptance and review status

- No Acceptance Checklist item has been marked complete. These RVs do not execute the full rv-2 cross-machine claim-loss/concurrency/payload-update/strict-storage-failure matrix or the rv-3 permission-denied, sustained-budget, strict-storage-failure and cancellation matrix.
- No independent verifier verdict is available in this session. The separate report records `REVIEW_INCIDENT / INCONCLUSIVE`; executor-run tests are not represented as independent verification.
- Human-Confirmed items remain for the human reviewer. The runner has since archived the canonical PRD under `tasks/archive/` with `⏸️ 验证阻塞`; this does not establish validation or human acceptance.
