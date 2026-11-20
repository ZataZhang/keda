# Verification Plan

- [Archived PRD](../../archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md)
- Original CI trigger SHA: `4c58ad0df1b1437ea2336303a64d486ada00ea89`
- Current remote main: `4f4fbffc22f7faf3c45f58f5922c9177db134569` at inspection. Recheck before synchronizing.
- Acceptance requires an independent rerun on the final tree. Use the commands and raw filenames in the [evidence report](P1-FEAT-20261113-121113-nightly-cleanup-keda.evidence-report.md).
- rv-1 checks the packaged CLI examples and compares the allowlist with the real runtime schema. rv-2 launches the real child process and checks final stdout and progress on stderr using upstream's timeouts.
- Run `just test` and `git diff --check <final-base> <final-head>`. Record base, verified HEAD, tree, results and output hashes in the verifier and evidence reports.

Runner must synchronize the branch with main, run independent verification against the final commit, publish the three complete outputs to `kc-evidence/issue-366`, and link them in a PR evidence comment. Refresh the PR body and review records with the published head and actual diff before human review.
