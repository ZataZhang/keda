# Issue #290 evidence report

## Current candidate

- Candidate commit: `f53b4a0de54bedb10afca160a6b17de0b4a613c5`.
- Base: merged `zata/main` commit `852217cc9269267974a513713c85f48b4b4bef0d`.
- The implementation candidate changes the Agent Runner guide, four production Python modules, and two test modules. A subsequent test-only cleanup extracts package-manager tests and shared test helpers; it does not change production code or the real-entry fixtures.
- Source tree: `78014345ef10cc7c17fbb6bdca853242760f8aed`.
- Test tree after the mechanical split: `7fa3bfa54c4f27193d125557b9d897301cfa8e26`.
- Independent review: [verifier report](issue-290.verifier-report.md), PASS.

## Collected implementation checks

| Evidence | Result | Scope and interpretation |
|---|---|---|
| Full local core suite before the final failure-attribution tightening | `4718 passed, 1 skipped` in 367.99 seconds. | Ran after merging current main and before the final small matcher/token-boundary change. This is not evidence for the final matcher behavior by itself. |
| Final matcher regression tests | `uv run --group dev pytest --no-testmon tests/test_worktree_dependency_context.py` — `42 passed` in 0.12 seconds. | Runs the changed file with testmon disabled; includes valid `bash -lc 'npm ci …'` attribution and negative cases for pnpm, an npm-like pytest path, and a successful npm warning. |
| Post-split targeted tests | `uv run --group dev pytest --no-testmon tests/test_worktree_frontend.py tests/test_worktree_package_manager.py tests/test_worktree_dependency_context.py tests/test_worktree_cli.py tests/test_agent_runner_orchestrate.py` — `162 passed` in 2.05 seconds. | Runs all five relevant modules after the package-manager test extraction and grouped-case cleanup; raw output: `rv-6-post-split-pytest.txt`. |
| Post-split lint | `pre-commit run ruff-format --files …` and `pre-commit run ruff --files …` — passed for the four modified/new test Python files. | The final test tree was formatted and linted before the post-split targeted run. The production tree remains the already checked candidate tree. |
| Final local gate | `just test` — full lint passed and `104 passed` in 7.48 seconds. | Ran after staging the mechanical test split and evidence package, with no custom target override. Raw output: `rv-7-final-local-gate.txt`. |
| Repository lint and reuse checks | `GUARD_UPDATE_ACK=1 just lint --repo` — exit 0. Ruff, formatting, jscpd, pylint duplicate-code, architecture, guidelines, and max-file-lines checks passed. | Ran after committing the candidate. No production files were changed by formatting. |
| Strict documentation build | `uv run mkdocs build --strict` — exit 0. | The build printed the repository's existing list of unlinked prototype/standard pages; no build error. |
| Diff hygiene | `git diff --check` passed after the test-only split. | Production source remains at the candidate tree; test changes are limited to the mechanical extraction and grouped-case cleanup. |
| Commit checks | All configured pre-commit hooks passed for candidate `f53b4a0d`, including Ruff, formatting, architecture, max-file-lines, test-flag, and guard-test modification checks. | The merge acknowledgement covers guard-test changes inherited from `zata/main`; no guard test appears in the candidate's diff against main. |
| Remote CI and install smoke | Candidate `f53b4a0d` passed CI and Install smoke. CI Validate Template run `39542224258`, job `119032323064`: `4722 passed, 1 skipped` in 294.40 seconds. | This ran on the pushed implementation candidate before the test-only extraction; the extracted tests were then rerun locally as recorded above. |

The full local core run after merging current main completed before the final matcher tightening: `4718 passed, 1 skipped` in 367.99 seconds. It is not evidence for the final matcher behavior by itself. The post-commit local `just test` invocation used `JUST_LOCAL_TEST_TARGET=tests/test_worktree_dependency_context.py` but pytest deselected all 42 tests due to the repository's default marker configuration. It only refreshed the local quality flag and is not counted as test evidence. The direct `--no-testmon` run and independent targeted tests above cover the final matcher change.

## Real-entry evidence

Planned command:

```bash
bash tasks/evidence/issue-290/scripts/rv-1-real-entry.sh \
  tasks/evidence/issue-290/rv-1-real-entry.txt
```

Final output is in the local raw artifact `tasks/evidence/issue-290/rv-1-independent-final-rerun.txt`. It ends with `RESULT: ALL PASS (7 real kc run negative controls + 7 isolated green scenarios)` and reports the preserved fixture scene at `/var/folders/n8/x_smwvn16b3b30f7wnl4s2kh0000gn/T/rv290-real-entry-rcxmf2ht`.

The preceding full run passed all seven negative controls but stopped at the manifest-less workspace positive oracle because it required the literal phrase `was not run`; the production detail used the equivalent `was never run`. The fixture report already contained the expected missing `pnpm` record, planned command, and covered member. The oracle was corrected to check that the command was not run (`exit_code is None`) and that pnpm was unavailable, while accepting either wording. No production or test source changed; the final run above then passed all fourteen scenarios.

The seven negative controls all turned red at their intended oracle:

| Negative control | Expected red observation |
|---|---|
| Pre-fix source, pinned to `e687f44b` | Real `kc run --issue` fails on missing `npm` before starting the Agent. |
| Upfront host-toolchain requirement injected | Agent prompt oracle rejects the incorrect prerequisite. |
| Safe dependency reuse forced to miss | `npm ci` runs instead of reusing matching dependencies; reuse oracle rejects it. |
| Workspace glob overmatches a nested standalone project | Agent oracle catches the incorrectly skipped nested package. |
| pnpm workspace exclusion ignored | Agent oracle catches the excluded independent project being skipped. |
| Dangling `node_modules` reported ready | Agent oracle catches the missing dependency diagnostic. |
| Manifest-less pnpm workspace root omitted | Agent oracle catches the absent root diagnostic. |

The seven positive scenarios passed through the same production CLI: missing tools and manager conflicts remain recoverable and reach the Agent; a root pnpm workspace without `package.json` is discovered; dangling dependency links and excluded workspace members retain diagnostics; matching dependencies are reused before installation; and real npm permission/credential failures remain `not_prepared`, reach the Agent, and produce the correct human-action blocker. Credential handling used real npm against the local loopback unauthorized registry; permission failure was injected only into a temporary project directory.

## AI coding self-check

Checked against `docs/ai-standards/code-reuse.md` after implementation:

| Item | Answer |
|---|---|
| Reuse existing code; no copy-paste followed by small edits | **Yes.** The current-main dependency-input matcher and all-symlink protection were preserved/adapted; the existing user-action text classifier is reused. |
| Reuse business rules rather than duplicate loading operations | **Yes.** The existing failure classifier and dependency report reader remain the sources of those rules and records. |
| Functions have at most four parameters; grouped inputs use an object | **Yes.** New production helpers take at most two parameters. The multi-scenario test passes a single `case` tuple and destructures it inside the test. |
| Files stay below the line guideline; avoid endless additions | **Yes.** The four production modules have 291, 719, 673, and 362 non-empty lines. The test files have 784 (`test_worktree_frontend.py`), 221 (`test_worktree_package_manager.py`), and 52 (`tests/support/worktree_frontend.py`) non-empty lines. |
| Imports follow `api -> core -> engines -> infrastructure` | **Yes.** Architecture checks passed and the dependency handling remains in core use cases. |
| Names communicate source and state | **Yes.** New locals identify manager, shell options, parsed command tokens, and exit status. |
| Run reuse and full lint before tests | **Yes for the implementation candidate; the post-split test-only changes were Ruff-checked before their targeted run.** The supported `just test` run then ran full lint first and passed 42 targeted tests on the implementation tree. The separate repo-wide lint also passed on that same production tree. |
| Update docs and navigation when documentation changes | **Yes.** `docs/guides/agent-runner.md` was updated; `uv run mkdocs build --strict` passed. |
| Avoid a one-use small helper | **Yes.** `_command_mentions_package_manager` has one caller but owns the non-trivial argv and shell `-c` token parsing, keeping failure attribution readable. |

The test-only split preserves the extracted package-manager test class and assertion behavior; the AST comparison and 162-test rerun confirm the extraction. No production source or real-entry fixture changed after the independent real-entry run.

## Verification boundaries

The real-entry harness uses disposable fixture repositories, a deterministic Agent plan executor, and a file-backed `gh` boundary. It exercises the production CLI and runner plumbing without writing to a real GitHub issue or comment. Credential handling invokes real npm against a local loopback unauthorized-registry fixture; the permission failure is injected only into a temporary project directory. It proves that the Agent receives the dependency context and that the real runner handles these fixture outcomes; it does not prove autonomous model selection or live registry availability. No images or screenshots are produced by this backend/CLI change.
