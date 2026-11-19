# Verification Plan

- PRD: `tasks/archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md`
- Nightly trigger `head_sha`: `4c58ad0df1b1437ea2336303a64d486ada00ea89` (historical CI failure selection)
- Reviewer-required rebase target: `51461449cb1069113b3d6a827297f92c16bcff2f`
- Selected scope: CI, because the nightly trigger SHA's latest `CI` workflow failed.
- Product impact: test-only reliability, with no production behavior change. The `--local` option already exists in the CLI and the packaged operator skill.

## rv-1 — packaged CLI examples accept only real flags

- Reviewer: verifier
- Critical value: the actual packaged skill examples and flags exposed by the Typer command schema.
- Entry point: `uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_command_examples_match_cli_help -v`; the sibling runtime-schema test ties the allowlist to the real CLI.
- Discriminating assertion: the pre-fix test fails on `--local`; after the allowlist fix is present in the base, the packaged example passes and unknown options remain rejected.
- External services or credentials: none.
- Evidence: `rv-1-cli-skill-flag-check.txt` and `rv-1-cli-schema-check.txt` are complete 12-line raw outputs in this worktree (ignored by Git). Their SHA-256 values are `ce2bf1437f64ba9f0ea55993b55e4243022f4d84b05f68f562630fac33dd1b8e` and `86cdb73759396920fcafec463b6f0bccbba4798ad2bdca6a738a9d7870b3083d`. Both must be published to `kc-evidence/issue-366` and presented in a PR evidence comment before human acceptance.

## rv-2 — captured output test survives a slow host

- Reviewer: verifier
- Critical value: the process-runner output test still captures the final stdout when its child emits progress for about 11 seconds under a slow or loaded host.
- Entry point: `uv run pytest tests/test_process_runner.py::test_captured_output_includes_stdout_written_when_process_exits -v --no-testmon`.
- Discriminating assertion: the 20-second process ceiling timed out during the local core-suite run; with the ceiling raised to 60 seconds while retaining the 3-second inactivity timeout, the focused test passes and captures `FINAL VERDICT`.
- External services or credentials: none.
- Evidence: the complete 12-line passing output is `rv-2-process-runner-timeout-check.txt` (ignored by Git), SHA-256 `2dee2645d4a4fdd027fdf38bc33c194fca9c634fd1061ae2c8a2e8cb03d4ecd8`. Publish it with the rv-1 outputs before human acceptance.

## Regression and repository gates

- `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache just test`
- `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache just lint --reuse`
- `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache just lint --full`
- `git diff --check 51461449`
- `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache uv run mkdocs build --strict`
- `UV_CACHE_DIR=/private/tmp/issue-366-uv-cache uv run python hooks/shared/check_prd_acceptance_checklist.py --check-provided tasks/archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md`

The first post-rebase `just test` used the original 20-second timeout and reported one failure in rv-2; the second run after changing it to 60 seconds selected and passed that test. The nightly docs audit, duplicate scan, and dependency review remain unrun because CI is the selected scope. The docs range remains unconfirmed because the previous closed loop Issue has no valid checkpoint record.
