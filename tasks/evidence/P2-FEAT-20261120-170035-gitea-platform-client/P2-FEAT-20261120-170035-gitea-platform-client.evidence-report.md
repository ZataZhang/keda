## 人审导航 / Human Review Navigation

| # | 人需要看的结果 | 呈递物与打开方式 | 精确自查 | PR / CI | 已由执行器交叉核对 |
|---|---|---|---|---|---|
| 1 | Gitea 上从 Issue、标签到 WIP PR、转正式和合并的呈递 | **阻塞：** `rv-1-gitea-pr-wip.png`、`rv-1-gitea-pr-merged.png` 与 `rv-1-demo.mp4` 均未生成。可打开 [浏览器阻塞记录](rv-1-browser-capture.txt)：`open "/Users/zata/code/keda/.kc-worktrees/issue-416/tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/rv-1-browser-capture.txt"`。该文本只解释阻塞，不替代 UI 截图或录屏。 | 要复核的值是 WIP 标题及合并保护、合并后 `Merged` 和 Issue 关闭。真实 API 查询已在 [完整流程输出](rv-1-gitea-full-loop.txt) 中通过；UI 仍未由人查看。 | [GitHub Issue #416](https://github.com/ZataZhang/keda/issues/416)；实现 PR 与 CI 尚未由 runner 发布。 | CLI 与独立 Gitea API 检查通过，`gh_invocations=0`；浏览器页面没有被渲染或交叉核对。 |
| 2 | 未登记主机不能收到令牌或请求 | [rv-2 终端证据](rv-2-unregistered-host.txt)：`open "/Users/zata/code/keda/.kc-worktrees/issue-416/tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/rv-2-unregistered-host.txt"`。 | `UNREGISTERED_EXIT_CODE=1 requests=0`；已登记负向控制为 `requests=1`。 | [GitHub Issue #416](https://github.com/ZataZhang/keda/issues/416)；实现 PR 与 CI 尚未由 runner 发布。 | 真实 CLI、配置加载与监听器计数均已检查。 |
| 3 | Gitea CI 状态汇总与 rv-5 截图格式门禁 | **阻塞：** `rv-5-gitea-status.png` 未生成；可查看 [rv-5 浏览器阻塞记录](rv-5-browser-capture.txt)：`open "/Users/zata/code/keda/.kc-worktrees/issue-416/tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/rv-5-browser-capture.txt"`。 | `rv-5-status.txt` 的真实服务端测试验证失败并带 context、成功为 SUCCESS、无状态为未知；该文本不能满足 screenshot 标记。 | [GitHub Issue #416](https://github.com/ZataZhang/keda/issues/416)；实现 PR 与 CI 尚未由 runner 发布。 | 状态 API 测试与负向控制通过；真实 Gitea 页面未被渲染，截图格式门禁仍开着。 |

## Delivery Summary

- Acceptance status: `⏸️ 验证阻塞` because rv-1's real Gitea UI screenshots and demo are missing, and rv-5's PRD screenshot-format marker is not satisfied.
- `rv-1-gitea-full-loop.txt` records the real Gitea 1.21 runner flow, the unknown-check no-merge control, successful merge/Issue closure, and zero `gh` calls. `rv-1-browser-capture.txt` records why the UI artifacts could not be produced.
- `rv-2` through `rv-4` and `rv-6` through `rv-8` have item-specific evidence files and executed negative controls. The rv-5 Gitea status tests and negative control passed, but its screenshot-format marker remains unsatisfied; `rv-5-browser-capture.txt` records the renderer failure. The complete test suite passed with testmon disabled: 5660 passed, 3 skipped in 427.80s. The ignored worktree-only `.kedacode.local.toml` sets the command replay timeout to 540s for this suite; no product or repository default changed. Reuse lint and full lint passed after rerun with the permitted temporary uv cache.
- The rv-3 script searched the complete evidence directory for the local Gitea test token value and found no match; the token value is not printed or included in this report.
- The initial `just lint --reuse` attempt used the user's default uv cache and failed because that path was not accessible in the filesystem sandbox. Rerunning with `UV_CACHE_DIR=/private/tmp/issue416-uv-cache` passed.
- Strict MkDocs build passed. It printed existing navigation notices for pages already outside `mkdocs.yml` navigation.

## Evidence and Provenance

- Structured manifest: [evidence.json](evidence.json)
- Architecture and dependency search: [arch-search.txt](arch-search.txt)
- Commit: `8b29d751471a8201825e3329745f2763d0c2303d`
- `HEAD^{tree}` when collected: `033c8a364d6384e4f07dddeb6357da925d29c1c9`
- Final worktree delivery fingerprint: `2ab4a8186931c230f16e348fa7996821906be05ad254763d21fabbfba2209d57` (33 changed delivery files; evidence directory excluded)
- All Gitea UI test data was created in a disposable local `gitea/gitea:1.21` container on `127.0.0.1:3300`; no external business system was used.

## Reconciliation Notes

- The CLI does not print the created PR URL. After real `kc run`, rv-1 obtains the PR from a fresh `GET /pulls` filtered by the run's head branch and WIP title, then independently rereads the PR by number.
- `kc init` selects Gitea when the publishing remote matches a machine-local registration so initial label sync follows the same platform path.
- Gitea 1.21 reports `mergeable=false` for its WIP PR state. The adapter maps that draft-only value to unknown and reads mergeability again after the title prefix is removed.

## Validation Blocker and Follow-up

- Blocker: CUA had no available browser host and Microsoft Edge desktop access was not approved. Isolated Chromium was denied at `MachPortRendezvousServer`; isolated WebKit exited with `Abort trap: 6`. No screenshot or video is claimed. This blocks rv-1's two screenshots and demo, plus rv-5's `kind=screenshot` evidence-format marker.
- Follow-up: track under Issue #416. When browser startup is available, run `RV_CAPTURE_BROWSER=1 bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv1_full_loop.sh`, capture both required real UI screenshots and the captioned MP4, and rerun `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv5_status.sh` before capturing the real Gitea commit status page as `rv-5-gitea-status.png`; then update the manifest and rerun delivery evidence checks.
