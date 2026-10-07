# Required negative-control actual-entry probes

rv3_positive: preserved changed reference through actual CLI

rv3_negative: EXPECTED_ORACLE_FAILURE: changed reference incorrectly reported up-to-date

rv9_negative: EXPECTED_ORACLE_FAILURE: default init overwrites changed user reference without --force

restored: actual CLI preserves fresh changed target after original snapshot code restored

snapshot: /Users/zata/.local/state/keda-supervision/issue-234-extra-negative/negative-4r2qvbas/source

source_head: 612493776dd7e74de5aa583968629b5992b8f529

original_module_sha256: 74dcfc109406273d6accaa8cd8c4b9bc204ae644b225bb123414c46f1aa95841

All invocations use actual kc init, actual orchestration, comparison/copytree and Git template download. Only isolated disposable source snapshot is altered; production worktree untouched. Fresh targets copied from successful real-init fixture contain a changed managed reference; no force supplied. GH executable remains deny-all boundary. Full stdout/stderr and before/after user bytes retained for each case. Original snapshot code restored and fresh positive rerun proves expected preservation again. Negative outcomes are expected oracle failures, not product failures or waived checks.
