# Verification Plan

- PRD: `tasks/archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md`
- Fixed base `head_sha`: `4c58ad0df1b1437ea2336303a64d486ada00ea89`
- Selected scope: CI, because the latest `CI` workflow for the fixed base failed.
- Product impact: test metadata only. The `--local` option already exists in the CLI and is documented by the packaged operator skill.

## rv-1 — packaged CLI examples accept only real flags

- Reviewer: verifier
- Critical value: the actual packaged skill examples and flags exposed by the Typer command schema.
- Entry point: `uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_command_examples_match_cli_help -v`; the test traverses the real packaged skill files and compares examples with the curated allowlist. The sibling runtime-schema test keeps allowlisted values tied to the real CLI.
- Discriminating assertion: the pre-fix test must fail on `--local`; after adding that already-supported flag, it must pass while still rejecting unknown options.
- External services or credentials: none.
- Evidence: `rv-1-cli-skill-flag-check.txt` and `rv-1-cli-schema-check.txt` (complete local raw outputs, ignored by Git). The runner must publish both to `kc-evidence/issue-366` and present them in the PR evidence comment before human review. GitHub comment creation was blocked by the connector's approval requirement under the active `never` approval policy; this worktree also cannot push the evidence branch.

## Regression and repository gates

- `just test`
- `just lint --reuse`
- `just lint --full`
- `git diff --check`
- `uv run mkdocs build --strict`

The nightly docs audit, duplicate scan, and dependency review are not run after the higher-priority CI scope is selected. The docs range remains unconfirmed because the previous closed loop Issue has no valid checkpoint record.
