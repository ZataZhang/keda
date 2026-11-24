# Independent Verifier Report

**Status: PASS — executor-owned acceptance items are proven on the final merged tree; the 3 Human-Confirmed items remain open for the maintainer.**

- Verifier: a Claude Code session acting at the maintainer's request on 2026-11-24. It did not implement Issue #416 (the implementer was the `codex` runner) and judged by re-executing every oracle, not by reading the implementer's evidence.
- Final tree: merge commit `59429463edb1a6dac6f71a75c181d6cd547d2c62` (PR #422), `HEAD^{tree}` `bbf6fce00232d163a2783136c0650f9ba984220d`.
- Supersedes the earlier `PENDING` report, which was written while browser capture was blocked.

## Item Results

| Item | Result on final tree | Evidence |
|---|---|---|
| rv-1 真实 Gitea runner 完整流程 | PASS by direct run (`RESULT=PASS`, codex shim 3 calls, `gh` 0 calls, independent API reread `merged=true`); WIP and merged pages captured with WebKit; demo recorded | `rv-1-gitea-full-loop.txt`, `rv-1-gitea-pr-wip.png`, `rv-1-gitea-pr-merged.png`, `rv-1-demo.mp4` |
| rv-2 未登记主机零请求 | PASSED (`kc evidence check`) | `final-tree-recheck.txt` |
| rv-3 缺失或空令牌发请求前停止 | PASSED | `final-tree-recheck.txt` |
| rv-4 Gitea URL 安全策略 | PASSED | `final-tree-recheck.txt` |
| rv-5 提交状态汇总 | PASS (live test 1 passed, API reread `state=failure`, real status page captured) | `rv-5-status.txt`, `rv-5-gitea-status.png` |
| rv-6 真实 Gitea 客户端操作覆盖 | PASSED | `final-tree-recheck.txt` |
| rv-7 PR URL 解析与完整回归 | PASSED (252.7s) | `final-tree-recheck.txt` |
| rv-8 配置与 runner 文档同步 | PASSED | `final-tree-recheck.txt` |
| Architecture / dependency checks | PASS — merge vs. first parent: no change to `pyproject.toml`/`uv.lock`, `IGitHubClient`, or `github_*.py`; Gitea files 804 / 517 non-empty lines | `final-tree-recheck.txt` |
| §7.9 swagger verification | PASS — 22/22 called endpoints exist in Gitea 1.21.11 swagger | `swagger-check.txt` |

## Incidents Found During Verification

1. **Shim mode bits (tooling defect, fixed locally).** The evidence branch stores `scripts/codex` and `scripts/gh` as `100644`. On the first recheck the real `codex` CLI ran instead of the deterministic shim and the runner fell back to real `claude`/`kimi`. That run was stopped and discarded; no conclusion is drawn from it. After `chmod +x`, rv-1 passed with the shim (`gh` probe empty).
2. **rv-1 exceeds the 300s re-exec cap.** With browser capture the full loop took 769s, dominated by video encoding, so `kc evidence check` reports item 1 as `TIMEOUT`. The PASS for rv-1 comes from direct execution. A future gate re-run of item 1 will time out until capture is made faster or split from the functional loop.
3. **`arch_search.sh` checks are vacuous on a clean checkout.** Its `git diff --quiet` compares the working tree with HEAD. The verifier replaced those three checks with a merge-vs-parent comparison.

## Not Verified Here

The Human-Confirmed items (decision one, decision two, and the §9.1 review) are the maintainer's to answer and were not ticked.
