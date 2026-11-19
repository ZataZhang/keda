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
- Evidence: `rv-1-cli-skill-flag-check.txt` and `rv-1-cli-schema-check.txt` are complete 12-line raw outputs in this worktree (ignored by Git). Their SHA-256 values are respectively `4c9c5431474c34c0e859c537382fd6d8e2098ff269081b3fba218367bca76b2e` and `6a2afb1586216c3a7fe3d04cbb2a629d1f3ab7605032bea82c37d4ce335f278b`. The runner must publish both to `kc-evidence/issue-366` and present them in the PR evidence comment before human review. This repair is limited to worktree files and prohibits push, so remote publication and PR writes remain pending.

## Reviewer repair snapshot — 2026-11-20

- The independent verifier report already records `PASS` for rv-1 and the runtime-schema check; both complete raw outputs remain present locally with the hashes above.
- Read-only PR #404 snapshot: head `e1b242d1f349905004cac3ab44af0e2d5b665a47`, 7 changed files, 500 additions, and 105 deletions. The remote title still lacks `cleanup(ci):`, its body still cites the old publication SHA and 99-file list, and its comment timeline is empty.
- Local Markdown review materials have been refreshed to this snapshot. PR metadata, orphan evidence-branch publication, and the evidence comment remain runner-owned under PRD §11 and the worktree-only/no-push constraint.

## Regression and repository gates

- `just test`
- `just lint --reuse`
- `just lint --full`
- `git diff --check`
- `uv run mkdocs build --strict`

The nightly docs audit, duplicate scan, and dependency review are not run after the higher-priority CI scope is selected. The docs range remains unconfirmed because the previous closed loop Issue has no valid checkpoint record.
