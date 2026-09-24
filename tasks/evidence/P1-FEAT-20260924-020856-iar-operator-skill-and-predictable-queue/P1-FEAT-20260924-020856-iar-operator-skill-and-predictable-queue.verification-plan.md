# Verification Plan — IAR 操作 Skill 与可预期队列

- PRD: `tasks/pending/P1-FEAT-20260924-020856-iar-operator-skill-and-predictable-queue.md`
- Branch: `feat/iar-operator-skill-and-predictable-queue`
- Validation applies to the final implementation tree in this PR. The PR evidence comment records the verified commit and tree.

## Commands and coverage

| Validation | Command | Result |
|---|---|---|
| Reuse, architecture and code checks | `just lint --reuse` | PASS |
| Full lint | `SKIP=check-test-flag just lint --full` | PASS; formatter was rerun after its initial formatting change |
| Full Python suite | `just test all` | PASS, 2508 passed |
| Documentation | `uv run mkdocs build --strict` | PASS; existing nav/anchor notices remain |
| Distribution resource | `uv build --wheel --out-dir /tmp/iar-operator-dist` | PASS; wheel contains packaged `iar-operator/SKILL.md` |

## PRD oracle mapping

- **rv-1**: `tests/test_agent_runner_orchestrate.py::test_run_once_dry_run_orders_ready_issues_by_priority_then_number` and `::test_run_once_execution_uses_the_same_priority_order_as_dry_run` exercise the shared runner selection path with deliberately scrambled priorities. Expected order is `#7 (P0), #10 (P1), #11 (P1), #8 (P3), #9 (unset)`; dry-run reports `priority=unset (after P3)` and the 100-candidate window. GitHub inputs are deterministic fixtures; the core ordering and dry-run/execution paths are real.
- **rv-2**: `tests/test_issue_list.py::test_issue_list_real_cli_filters_fixture_results_by_state_and_label` invokes `main`/Typer and the real use case with a filtering GitHub fixture. Only open `#41` with `agent/ready` is returned; open `#42` without the label and closed `#43` are excluded. The test also confirms both filters reach the GitHub boundary.
- **rv-3**: `tests/test_prd_skill_preflight.py` covers v3 acceptance, v1 rejection, missing marker, missing Skill, and unknown v99 rejection. `tests/test_agent_runner_orchestrate.py::test_run_once_unknown_machine_contract_performs_no_github_writes` traverses the real non-dry-run runner preflight and confirms the v99 case returns failure before any GitHub client call.
- **rv-4**: built wheel was installed into an isolated target and the installed `iar init --dry-run` was run with isolated HOME/repository fixtures. Clean HOME planned installation to `.codex/skills/iar-operator`; conflicting HOME reported `Would preserve existing user skill (conflict; use --force to replace)` at the exact target. Dry-run did not overwrite the pre-existing sentinel.
- **rv-5**: final Skill content is in `src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md`; command/lifecycle claims were reviewed against the CLI and `docs/guides/agent-runner.md`.

## Boundaries and limitations

- GitHub uses hermetic in-memory fixtures; no live GitHub write or live smoke was run.
- Runner ordering applies to the candidate window returned by GitHub (maximum 100), as documented.
- No database schema or frontend behavior changed. The `just worktree` setup noted that no relational `DATABASE_URL` was available, so no database was provisioned or used.
