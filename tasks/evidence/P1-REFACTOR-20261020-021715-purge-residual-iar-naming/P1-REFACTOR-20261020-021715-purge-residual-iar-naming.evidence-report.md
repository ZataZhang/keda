## 人审导航 / Human Review Navigation

GitHub Issue：[Issue #313](https://github.com/ZataZhang/keda/issues/313)。本工作没有创建 PR，因此目前没有 PR 或 CI 链接。

| 人审决定 | 证据与打开方式 | 逐项预期值 | 执行侧交叉核对 |
|---|---|---|---|
| 决策一：只认 `kc:`，升级时迁移在途标记 | RV-1：`/Users/zata/code/keda/.iar-worktrees/issue-313/tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-1-github-post-merge.txt`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-313/tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-1-github-post-merge.txt"`。RV-2：同目录 `rv-2-marker-migration.txt`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-313/tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-2-marker-migration.txt"` | RV-1 应看到新 Issue 正文中的 `<!-- kc:depends-on #292 -->` 且没有新写出的旧前缀。RV-2 预期只有前缀发生变化，runner 不会重复认领，重跑为 0，runner 运行时拒绝写入。当前 RV-1 未获得 GitHub 凭据；RV-2 apply 被进程枚举权限阻断，未声称迁移成功。 | 执行侧读回代码、测试和 CLI 输出；rv-1 与 rv-2 的未完成部分已明确留作 runner gate。 |
| 决策二：新目录默认名、老目录继续复用 | RV-3：`/Users/zata/code/keda/.iar-worktrees/issue-313/tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-3-clean-repository.txt`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-313/tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-3-clean-repository.txt"`。RV-4：同目录 `rv-4-legacy-directory.txt`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-313/tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-4-legacy-directory.txt"` | 干净仓库创建前 `.kc-worktrees` 不存在，创建后新工作树位于 `.kc-worktrees/<branch>` 且 `git worktree list` 可见。旧目录场景中工作树位于 `.iar-worktrees/<branch>` 且仓库根没有 `.kc-worktrees`。 | 两项均由真实 CLI 写入独立 Git 仓库，再用 Git porcelain 与目录清单读回。 |

没有前端或视觉界面变更，因此本次没有截图；以上证据是 CLI / filesystem 真实入口输出，不用 UI 图代替。

## 验证结果

| 项目 | 状态 | 证据 |
|---|---|---|
| rv-1 新建 GitHub Issue | 🟡 runner post-merge | `rv-1-github-post-merge.txt` 记录凭据缺失；未用单元测试冒充真实 GitHub 验证。 |
| rv-2 在途标记迁移 | 🟡 runner-owned gate | `rv-2-marker-migration.txt` 记录未迁移旧标记负控、预演无写及 apply 的 fail-closed 阻断；`rv-2-process-scanner.txt` 记录宿主权限错误。 |
| rv-3/rv-4 工作树目录 | ✅ 通过 | 各有负控和真实 CLI 绿测文件。 |
| rv-5 签名头 | ✅ 通过 | 新旧 HMAC 头成功，缺头 401；alias 故障注入使新头测试失败。 |
| rv-6 命名守卫 | ✅ 通过 | 白名单外注入使 guard 失败，移除后通过。 |
| 最终 `just test` / `just lint` gate | 🟡 runner-owned staged-tree gate | 本轮 `UV_CACHE_DIR=.uv-cache just test all` 的完整 lint 与全量 pytest 通过（4963 passed、1 skipped）；证据包 staged 后仍需 runner 在最终 staged tree 重跑 `just test`、`just lint` 与 MkDocs build。 |

额外验证：最终代码修复后的定向后端套件通过（`153 passed in 7.16s`），命令带 `--no-testmon`；之前实际执行的本地核心档为 `19 passed, 199 deselected`。恢复门禁时，目标用例 `tests/test_backlog_api.py::test_force_refresh_requests_rescan_and_reports_stale` 连续 6 次通过。先前完整运行偶发失败的原因是该测试的 mock builder 没有返回必需的 `scanned_at`，异步重建先完成时会写出不可渲染的快照并诱发第二次重扫；修复只补齐 mock 契约，不改生产逻辑。随后 `UV_CACHE_DIR=.uv-cache just test all` 完整通过：lint passed，`4963 passed, 1 skipped in 454.72s`，并更新 test 与 full-lint 标记。相关测试与实现文件在基线 `f3bc2da3` 未变化，基线上的一次定向重跑通过。`just lint --reuse` 与 MkDocs 构建的既有验证通过，三处此前修改的 Python 文件由 Ruff format 检查通过，manifest schema 与其引用的证据文件完整性检查通过。RV-3 / RV-4 的 manifest 命令已改为先跑负控、再跑真实 CLI 绿测。

standalone `just lint` 之前被 `check-test-flag` 拒绝：pre-commit 暂存未跟踪文件期间，untracked `evidence.json` 从工作视图隐藏，因而计算的 test tree 与工作区标记不一致。没有跳过该检查；runner 将 evidence reports / manifest stage 后需在最终 staged tree 重跑 `just test`、`just lint` 与 MkDocs build。独立 verifier 仍为 PENDING，且 `git rev-parse HEAD^{tree}` 的最终交付 tree 需由提交后的 runner 记录。

证据 JSON manifest 位于同目录 `evidence.json`，含六个 Realistic Validation 项的复现命令、负控预期、输出文件及风险说明。原始 `.txt` 与 scripts 为本地证据，不应进入源码 diff。
