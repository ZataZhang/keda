# Evidence Report

Published draft [#404](https://github.com/ZataZhang/keda/pull/404) has seven changed files, head `9c21d528bca57af6950f63b9109d098fa09e8dcd` and GitHub-reported base `508001b09077a863f27b9491f60ba75123536d9d`. The branch merge base with live main `4f4fbffc22f7faf3c45f58f5922c9177db134569` is `51461449cb1069113b3d6a827297f92c16bcff2f`.

The original [failing CI run](https://github.com/ZataZhang/keda/actions/runs/44548773786) omitted the supported `kc agent setup --local` flag from the test allowlist. Main already contains the correction. The local repair restores the exact current-main process-runner fixture (12 progress iterations, 1-second sleep, 20-second ceiling and 3-second inactivity timeout). The earlier timeout result used a different fixture. The latest main also changes the packaged skill and allowlist, so the local rv-1 results apply to the inspected worktree.

The executor ran the checks on HEAD `9c21d528bca57af6950f63b9109d098fa09e8dcd` with tracked code patch SHA-256 `bd0d5bb30504a6ca73afc11cd57d4b00ff015239d897ff697f1199aa67b8acb4` (output of `git diff HEAD -- tests/test_process_runner.py`). The evidence Markdown was refreshed after the runs.

| Check | Command | Result | Local raw output | SHA-256 |
|---|---|---|---|---|
| rv-1 | `uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_command_examples_match_cli_help -v --no-testmon` | 1 passed in 0.46s | `rv-1-cli-skill-flag-check.txt` | `6dfffc8f22ec1c49e631d60e6cd6b3e3c145d8766d8852ccad94d7c4850c3410` |
| runtime-schema | `uv run pytest tests/test_kedacode_operator_skill.py::test_packaged_skill_whitelist_matches_runtime_schema -v --no-testmon` | 1 passed in 0.58s | `rv-1-cli-schema-check.txt` | `fdc10606925cfc6da565068b60121fd00173f681a99472cec15a47c79b3dbed0` |
| rv-2 | `uv run pytest tests/test_process_runner.py::test_captured_output_includes_stdout_written_when_process_exits -v --no-testmon` | 1 passed in 12.95s | `rv-2-process-runner-timeout-check.txt` | `f2a1d20b347ea3bd97eed36e335015db020bde9b50b0d78555f3b062598c3336` |

`just test` passed full lint and the testmon selection (1 passed, 68 deselected). `git diff --check 51461449cb1069113b3d6a827297f92c16bcff2f` passed on the local comparison. The test file matches current main. Docs, duplicate code and dependency scans were skipped. The docs checkpoint remains unconfirmed.

The draft title and body were updated. Issue #366 has the `scope/ci` label and a [round comment](https://github.com/ZataZhang/keda/issues/366#issuecomment-7028329484).

Runner must synchronize the branch with main, run independent verification against the final commit, publish the three complete outputs to `kc-evidence/issue-366`, and link them in a PR evidence comment. Refresh the PR body and review records with the published head and actual diff before human review.
