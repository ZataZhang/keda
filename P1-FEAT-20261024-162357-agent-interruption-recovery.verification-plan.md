# Verification Plan — Agent Runner Interruption Recovery

- Issue: [#457](https://github.com/ZataZhang/keda/issues/457)
- Canonical PRD: `tasks/pending/P1-FEAT-20261024-162357-agent-interruption-recovery.md`
- Evidence mode: isolated real `kc` / `gh` / Git / SQLite with a test-side TLS forge; no production network or injection switch.
- Source baseline: WIP checkpoint `6328f89ddb762a22fbcdcc9d7a54ae954709bf9b` plus the current worktree diff.

## Real-entry oracles

| ID | Command | Required observations |
|---|---|---|
| rv-1 | `UV_CACHE_DIR=/private/tmp/uv-cache uv run python tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery/scripts/run_recovery_rv.py --scene stage-resume` | Real `kc run --issue 73` suspends after actual TLS socket drops; a fresh daemon resumes the existing release work, keeps one Agent invocation and the validation result when its inputs match, re-runs validation after its contract changes, and leaves one remote PR. Probe Git ref, recovery rows, labels, and operation rows from fresh processes. |
| rv-2 | `UV_CACHE_DIR=/private/tmp/uv-cache uv run python tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery/scripts/run_recovery_rv.py --scene unknown-write` | Persist-and-drop reconciles to one PR after restart; a refusal before TLS/API bytes safely retries; absence without authoritative proof and an unavailable reconciliation query remain unknown without a second create. |
| rv-3 | `UV_CACHE_DIR=/private/tmp/uv-cache uv run python tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery/scripts/run_recovery_rv.py --scene human-handoff` | Real CLI/daemon preserve a local actionable report after an unknown write; a notification 429 remains pending, and a fresh daemon later delivers the same report body once with the same operation marker. Probe the local report, notification object, operation table, and budget from fresh state. |

The harness uses a real local bare Git remote and SQLite. `gh` is a real subprocess routed through a test-only local HTTPS TLS proxy; the proxy accepts no outside GitHub route and drops real sockets for the required network-fault cases. The external Agent and verification commands are deterministic subprocess fixtures. The test boundary may accelerate a saved `resume_at` or expire a budget in the isolated SQLite database; it does not change recovery policy or production configuration.

## Regression and repository checks

| Check | Command | Acceptance evidence |
|---|---|---|
| Recovery regressions | `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/test_github_client.py tests/test_agent_runner_recovery.py tests/test_agent_runner_publish.py tests/test_recover_publish.py tests/test_runner_recovery_store.py -q --no-testmon` | Nonzero test count; include storage transaction/CAS, operation identity, recovery stage, GitHub classification, publish and manual recovery. |
| Core repository test entry | `UV_CACHE_DIR=/private/tmp/uv-cache just test` | Count only a run whose pytest summary reports executed tests. If the documented local flag makes it skip, record the skip and use the nonzero PRD-directed `--no-testmon` suites below; never use the cached exit code as execution evidence. |
| Lint and architecture/reuse gates | `UV_CACHE_DIR=/private/tmp/uv-cache just lint --repo` | Reuse, layer-boundary, guidance-consistency, maximum-file-line, and repository checks pass. |
| Documentation | `UV_CACHE_DIR=/private/tmp/uv-cache uv run mkdocs build --strict` | Strict documentation build exits 0. |
| Final diff hygiene | `git diff --check` | No whitespace errors. |

## Review boundary

Run each scene's `--negative-control` form separately before its green form. Those commands must exit nonzero for the expected oracle failure; their isolated captures are `rv-<n>-*-negative-control.txt`. Preserve unproven executor-owned `[ ]` and Human-Confirmed `[ ]` items; runner-owned verifier/PR/archive gates are represented by `[~]` in the PRD. The structured `evidence.json` binds each scene to its green command, red control, and item-specific artifacts; `kc evidence check --evidence tasks/evidence/P1-FEAT-20261024-162357-agent-interruption-recovery` replays green commands and checks stable stdout assertions. Do not run `just test all` locally for this ordinary delivery. Keep raw RV scripts and captures under the ignored evidence directory; the evidence package and three Markdown review reports remain separate deliverables. No live GitHub write test is part of this plan.
