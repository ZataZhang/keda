# Issue #290 verification plan

## Acceptance checks

| Check | Required observation | Verification |
|---|---|---|
| Missing package-manager executable | Dependency preparation records the planned command and missing tool, returns control to the real runner, and passes the diagnostic to the Agent without declaring a host-toolchain prerequisite. | Real `kc run --issue` positive scenario; pre-fix negative control must reproduce the old early failure. |
| Manager and workspace discovery | Package-manager evidence, pnpm workspace roots without a root `package.json`, nested workspace membership/exclusions, and contradictory npm/pnpm evidence are represented without silently selecting the wrong installer or skipping an uncovered member. | Real entry fixtures plus package-manager and worktree unit tests. |
| Dependency reuse | Matching manifests, workspace inputs, and lockfiles allow safe reuse before installation; changed inputs do not reuse the main checkout's dependencies. | Real entry positive and reuse-order negative control; worktree unit tests. |
| Installation failures | A failed install remains `not_prepared` with its error detail; permission and credential failures are identified as human decisions. | Real entry permission and credential scenarios with controlled subprocess boundaries. |
| Dependency symlink safety | Existing and newly created frontend `node_modules` symlinks are excluded from git; an unreadable git exclude file does not abort preparation or remove a pre-existing link. | Worktree unit tests and real entry fixture. |
| Failure attribution | Only a non-zero command result whose argv identifies the recorded package manager and whose output matches a known permission/credential signature becomes an environment blocker. | `tests/test_worktree_dependency_context.py`, including shell-wrapped npm, pnpm substring, npm-like test path, and successful npm warning cases. |

## Commands

1. Run the production CLI entry against isolated local repository fixtures:

   ```bash
   bash tasks/evidence/issue-290/scripts/rv-1-real-entry.sh \
     tasks/evidence/issue-290/rv-1-real-entry.txt
   ```

   The script exercises seven positive and seven negative-control scenarios through `kc run --issue`. It must finish with `RESULT: ALL PASS (7 real kc run negative controls + 7 isolated green scenarios)`. Any negative-control red result must identify its intended oracle, not fixture setup or infrastructure failure. Final output is retained locally at `tasks/evidence/issue-290/rv-1-independent-final-rerun.txt`.

2. Run focused regression tests after production edits:

   ```bash
   uv run --group dev pytest --no-testmon \
     tests/test_worktree_frontend.py \
     tests/test_worktree_package_manager.py \
     tests/test_worktree_dependency_context.py \
     tests/test_worktree_cli.py \
     tests/test_agent_runner_orchestrate.py
   ```

   The matcher-specific change must also pass:

   ```bash
   uv run --group dev pytest --no-testmon tests/test_worktree_dependency_context.py
   ```

3. Run the repository checks on the final candidate:

   ```bash
   just lint --repo
   uv run mkdocs build --strict
   ```

   The final candidate also receives independent targeted runs for frontend/context/CLI and orchestration tests. The local core test suite and explicit reuse hooks are recorded in the evidence report. CI remains the full-suite check for the pushed PR.

## Real-entry boundary and limitations

The validation uses the real CLI, runner composition, worktree preparation code, subprocess runner, prompt rendering, and issue-state flow. It creates disposable local repositories and fixture state. The `gh` boundary and Agent executable are controlled fixture programs, so the test does not send issues, labels, or comments to GitHub and does not claim that a live model independently chose the observed actions. Credential behavior uses real npm against a local loopback registry fixture; permission failure is injected only into the disposable project directory. The oracle concerns Keda's preparation, diagnostics, prompt, and recovery behavior, not live registry availability.

The pre-fix negative control is pinned to the known pre-fix ref `e687f44b`; it must reproduce the original missing-executable failure. The other negative controls mutate one relevant condition at a time, including reuse ordering, workspace matching/exclusions, dangling `node_modules`, and the missing-root-manifest case.

## Final run record

- Candidate: `f53b4a0de54bedb10afca160a6b17de0b4a613c5`.
- Source tree: `78014345ef10cc7c17fbb6bdca853242760f8aed`.
- Test tree after the mechanical split: `7fa3bfa54c4f27193d125557b9d897301cfa8e26`.
- Independent real-entry run: 7/7 positive scenarios and 7/7 negative controls passed.
- Post-split targeted tests: 162 passed across frontend, package-manager, dependency-context, CLI, and orchestration modules.
- Remote CI and Install smoke passed for candidate `f53b4a0d`; CI Validate Template run `39542224258`, job `119032323064`, completed with 4722 passed and 1 skipped.
- Repository lint and strict documentation build passed; see evidence report for details.
