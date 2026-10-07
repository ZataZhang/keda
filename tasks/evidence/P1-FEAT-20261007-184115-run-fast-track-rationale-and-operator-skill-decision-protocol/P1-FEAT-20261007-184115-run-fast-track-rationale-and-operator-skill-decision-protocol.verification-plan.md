# Issue #235 verification plan

## Review map

- rv-1: independent CLI and daemon discover Issue labels, preserve DIRECT marker, consume label; neighboring NORMAL remains NORMAL; PRD negative dispatches no builder or PR.
- rv-2: durable GitHub adapter writes PR before lost response; reconstruct a fresh client and recover without a second PR. Cover deletion failure, successful deletion followed by workflow crash, success response without deletion, same-head historical PR, identity mismatch and claimant loser. Initial publication and cleanup recovery must finish in review with inline supervisor enabled or disabled; final transition failure must leave the checkpoint pending. Actual comment adapter must filter unauthorized forged checkpoints before stage selection and fail closed on comment/permission query errors.
- rv-3: actual configuration loading/sync, custom and disabled label, workflow preservation; read final hub/run/daemon/guide semantics against actual stage behavior.

## Commands and identity

1. `SKIP=check-test-flag just lint --full` before test gate (only cyclic test flag skipped); `just lint --reuse`.
2. `CI=1 just test all` (full collection, no incremental skip).
3. `uv run mkdocs build --strict` and `git diff --check`.
4. `CI=1 uv run pytest tests/test_agent_runner_direct_pr*.py tests/test_recover_publish.py tests/test_agent_runner_run_once.py tests/test_kedacode_operator_skill.py -o addopts='' -q` for targeted diagnosis.
5. On a clean committed product tree, execute isolated opt-in private GitHub probe with the source worktree and dependency virtualenv Python. Record source HEAD/tree and fresh GitHub branch SHA, PR URL/body/head/draft, Issue labels, builder and verification counters.
6. Independent verifier reviews that committed product tree and fresh evidence. Delivery-only PRD/evidence commits are separately reconciled against unchanged product tree.

## Real boundaries and limits

- Live probe uses actual command parser, subprocess runner, claim arbitration, worktree, commit proxy, git push, GitHub Draft PR and label API; no `--direct-pr` is passed.
- Deterministic external builder replaces the LLM; memory, generated body AI, pre-PR reviewer, independent runner validation and inline supervisor are disabled in the isolated fixture. NORMAL repository verification remains real and counted. This proves label publication plumbing, not LLM quality.
- Fault adapter persists comments, labels and PR state on disk and reconstructs independent clients. Git command output is a replacement boundary; actual creation, association and ready/running/blocked/recover handlers run.
- Trusted checkpoint tests run the actual GitHub adapter with server metadata and permission responses replaced; self author, other writer/triage, read-only author, malformed metadata, failed reads and bounded permission queries are covered. Live validates actual self-author metadata; no second-account credentials are assumed.
- Read-only discovery association is rechecked after acquiring claim/local ownership; arbitrary marker/history cannot grant cleanup or new work.
- Human semantic review is not reduced to keyword tests. Existing skill tests check packaging contracts; final full-text review checks obligations.
- No new UI: fresh API text and real URLs replace requested screenshot ceremony under the user's explicit preference. API evidence is not browser user-flow evidence.
- Standalone `kc recover` retains its existing requirement for a local clean worktree. Cross-machine Issue selection is supported; the evidence does not claim recovery on a machine without the worktree.
- GitHub PR creation/label deletion are not atomic. Concurrent re-addition of the same label within cleanup is unsupported and documented. All potential claimants need upgrading/restarting.
- Failures stay failures; credentials/network failure blocks rv-1 and archive. Owned test PRs are closed and fixture repo archived, never merged or deleted.
