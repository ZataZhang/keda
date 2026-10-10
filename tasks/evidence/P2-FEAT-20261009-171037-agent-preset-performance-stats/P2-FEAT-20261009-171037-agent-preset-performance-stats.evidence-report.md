## 人审导航 / Human Review Navigation

| 人审呈递 | 实际路径与打开方式 | 需要核对的值 | 状态与已交叉检查 |
|---|---|---|---|
| rv-2：Stats 页面 Agent / preset 表、整项任务与窄屏布局 | 截图已生成：桌面 [rv-2-stats-agent-performance.png](rv-2-stats-agent-performance.png)（1440x900，卡片与整页）与窄屏 [rv-2-stats-agent-performance-narrow.png](rv-2-stats-agent-performance-narrow.png)（375x812，收起导航后横向滚动）。打开方式：worktree 内 `just console-sync` 后运行 `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run kc console`，访问 `/app/stats`；HTML 入口 `just prd review tasks/pending/P2-FEAT-20261009-171037-agent-preset-performance-stats.md` 已通过真实浏览器呈现检查（见 [rv-2-prd-review-browser-check.txt](rv-2-prd-review-browser-check.txt)）。 | fixture 预期显示 `codex` 4 次、成功 3 / 非成功 1、75% / 25%、`verification_failed: 1`；预设 `removed-preset` / `provider/model-x` 4 次；未绑定 2 次；整项任务 `completed / failed / blocked` 为 2 / 1 / 1；全部仓库行显示 `keda-main`，375px 表格可横向滚动。 | **执行侧证据已完成。** 目标 E2E **5 passed**（含窄屏 375px 用例，见 [rv-2-e2e-green-final.txt](rv-2-e2e-green-final.txt)）；PRD 指定的样本数负控在真实浏览器转红（期望 4、收到 5）并恢复转绿（RED_EXIT=1 / GREEN_EXIT=0，见 [rv-2-e2e-negative-control.txt](rv-2-e2e-negative-control.txt)）；桌面与窄屏截图取自 kc console 静态分发的真实页面，桌面截图为 GitHub overview 实时数据。Human-Confirmed 两项与独立 verifier 仍由人 / runner 处理。Issue: [GitHub #263](https://github.com/ZataZhang/keda/issues/263)。PR / CI 由 runner 后续处理。 |

真实页面截图已生成并嵌入配套人审 HTML（桌面 + 375px 窄屏，均采自 worktree `kc console` 静态分发；图片被 `.gitignore` 排除，属本地图片，仅随证据目录分发）。

![rv-2 桌面 1440x900 真实 /app/stats 页面（kc console 静态分发，GitHub overview 实时数据）](rv-2-stats-agent-performance.png)

![rv-2 桌面整页视图（含原有趋势 / lifecycle / Token 区块与新统计区共存）](rv-2-stats-agent-performance-desktop-page.png)

![rv-2 窄屏 375x812 收起导航后表格横向滚动](rv-2-stats-agent-performance-narrow.png)

![人审 HTML 第 2 项截图呈递页（真实浏览器检查）](rv-2-prd-review-checklist-page2.png)

## rv-1 — API 与 SQLite 结果

- 负控：临时将集成测试的 `success_count` 期望由 2 改成 999。真实 API 返回 2，测试在该断言失败；输出见 [rv-1-api-negative-control.txt](rv-1-api-negative-control.txt)。
- 两个额外负控分别把 `blocked` run P50 和 `completed` run P90 期望改为 999；API 分别返回 30 秒与 58 秒，两个错误期望都触发真实路由断言失败。输出见 [rv-1-run-duration-negative-control.txt](rv-1-run-duration-negative-control.txt) 和 [rv-1-run-percentile-negative-control.txt](rv-1-run-percentile-negative-control.txt)。
- 恢复正确期望后，`UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run pytest --no-testmon tests/test_console_stats.py tests/test_agent_runner_console_api.py -k agent_performance -q` 通过：**4 passed, 31 deselected**；输出见 [rv-1-api-green.txt](rv-1-api-green.txt)。
- 路由集成测试写入临时 SQLite 并 commit，通过独立 SQLite connection fresh-read 后请求真实 FastAPI 路由。窗口内 `keda-main` 的 `codex` 组有 3 个 attempt：成功 2、非成功 1、成功率 2/3、非成功率 1/3、P50 30 秒、P90 46 秒，失败分类为 `verification_failed: 1`。空 preset 计数为 1；已删除的 `removed-preset` 快照按两个模型分组保留。按同一集成用例重新采集的三份 HTTP 200 JSON 响应在 [rv-1-api-json.json](rv-1-api-json.json)，采集命令输出在 [rv-1-api-json-capture.txt](rv-1-api-json-capture.txt)。本次 rv-1 源码文件哈希见 [rv-1-final-tree.txt](rv-1-final-tree.txt)，其工作树源码摘要为 `0f61c4dc9cd1797e53cc0028fba988b3349086677f93347a2370906148f5610a`。
- Run 汇总单独包含 `completed` / `failed` / `blocked`。窗口内两条 `completed` 耗时为 40、60 秒，真实 API 分别返回 `(run_count, P50, P90)=(2, 50, 58)`；`failed=(1,20,20)`、`blocked=(1,30,30)`。全部仓库响应断言同名预设与 run 均保留 `keda-main` / `other-repo` 区分，run DTO 不含 `agent` 字段；1 天窗口排除 30 天前样本。
- 该测试输出未打印整份 JSON 正文；上述字段与数值通过测试中的 JSON 断言逐项核对，原始样本及独立查询也在测试中可复核。
- 本次还运行了 `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run pytest --no-testmon tests/test_prd_lifecycle.py::test_stats_percentiles_over_completed_runs_only -q`，**1 passed**；该项覆盖共享 percentile helper 替换后，既有 PRD lifecycle median / P90 计算仍保持预期。输出见 [rv-1-lifecycle-percentile-regression.txt](rv-1-lifecycle-percentile-regression.txt)。

## rv-2 — Stats 页面验证状态

- PRD 的产品负控要求把 E2E fixture 的 `removed-preset.attempt_count` 暂改为 5，同时保留行级期望 4。此负控没有执行。此前 E2E 的 auth setup 与三个 Stats 页面检查通过，但窄屏检查在 `stats-agent-performance-preset-table` 的 `toBeVisible()` 处失败；实际截图见 Playwright 临时结果 `tests/playwright-e2e/test-results/2026-10-10T02-59-55/smoke-stats-agent-performa-d42e3-llable-at-a-narrow-viewport-chromium/test-failed-1.png`，原始输出见 [rv-2-e2e-narrow-red.txt](rv-2-e2e-narrow-red.txt)。为适应现有可折叠侧栏，测试现先点击“收起导航栏”再测 375px 表格溢出；本轮重跑在 auth setup 因 Chromium MachPort 权限失败，未验证修正，最新输出见 [rv-2-e2e-final-red.txt](rv-2-e2e-final-red.txt)。不得将浏览器启动失败算作产品负控或 E2E 通过。
- CUA 当前结果为 IAB 不可用、浏览器 inventory 为空、访问 Google Chrome 未获准；详情见 [rv-2-browser-access-final.txt](rv-2-browser-access-final.txt)。没有打开浏览器或页面截图。
- 最终 worktree 的 `frontend-public` `pnpm typecheck` 与 `pnpm build` 均通过，见 [rv-2-typecheck-final.txt](rv-2-typecheck-final.txt) 和 [rv-2-build-final.txt](rv-2-build-final.txt)。真实页面此前筛选断言通过，配合当前类型 / build 证据支持前端 API / TypeScript Acceptance；最新运行时请求断言仍未重跑。
- 最终 `just console-sync` 后通过当前 worktree `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run kc console --no-browser --port 8879` 做 fresh HTTP read：`/app/stats/` 与 12 个引用 bundle 全部返回 200，目标 bundle 含新增区块，见 [rv-2-console-sync-final.txt](rv-2-console-sync-final.txt) 与 [rv-2-console-http-final.txt](rv-2-console-http-final.txt)。这只证明静态文件可服务，不证明浏览器渲染。
- 更新后的 E2E spec 对 API method / canonical path、仓库 / days 参数及 375px 横向滚动添加断言，并在窄屏验证前点击现有导航收起控件；最新单文件 TypeScript / ESLint 通过，见 [rv-2-e2e-spec-checks-attempt5.txt](rv-2-e2e-spec-checks-attempt5.txt)。E2E package 全量 `npm run typecheck` 仍报告未修改的 `tests/smoke/idea-inbox.spec.ts:123` 错误。修改后的真实 E2E 最新尝试在 auth setup 阶段因 Chromium `MachPortRendezvousServer` `Permission denied (1100)` 退出，4 条 Stats 页面用例未运行。此前 auth setup 与三个页面断言通过，窄屏断言失败；因此当前窄屏修改仍需一次可运行的浏览器验证。
- 为使本环境的 `just run` 在 `pgrep` 不可用时走脚本已声明的“无法计数则跳过进程上限”分支，修复了 `process_guard.sh` 在 `set -e -o pipefail` 下提前退出的问题；分支执行证据见 [rv-2-process-guard.txt](rv-2-process-guard.txt)。进程枚举成功时原有进程上限计算保持不变。

## 其他验证与环境诊断

- `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache uv run mkdocs build --strict`：退出码 0；仅有仓库既有的未纳入导航页面与锚点提示。
- `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache just lint --reuse`：重复检测、架构依赖、指南一致性和最大文件行数检查均通过。
- 单独运行 `just lint --full` 时，源码、格式、文档、架构和守卫相关 hooks 通过；唯一失败为 `check-test-flag`，报告本地 `just test` 标记 tree 过期。
- 本轮运行 `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache just test`：lint 阶段通过；测试结果 **1 passed、14 failed、149 deselected**。失败来自 daemon lock 写入 `/Users/zata/.kedacode/daemon-locks/repo.lock` 被环境拒绝，以及 `config migrate` 因进程扫描器不可用而按安全策略拒绝继续；这些与统计实现无关。由于核心档未全绿，`just test` 标记未刷新，`just lint --full` 的 `check-test-flag` 仍报告 tree 过期，不能声称总门禁通过。
- `frontend-admin/` 没有本次改动。
- 验证未覆盖真实页面及窄屏显示，Human-Confirmed 仍由人处理，独立 verifier 未运行。PRD 留在 `tasks/pending/`；本证据报告不声称验收通过或允许归档。

## 交付收尾补充（2026-10-10 closeout addendum）

- **历史静态证据更正（rv-2 局部）：** 恢复尝试 2 / 3 的裸 `kc console` 解析到全局安装入口，故其 HTTP / bundle 输出不证明当前 worktree 的页面产物，已由恢复尝试 4 的 [rv-2-kc-console-worktree.txt](rv-2-kc-console-worktree.txt) 取代。当前 worktree `uv run kc console` fresh-read `/app/stats/` 与 12 个 bundle 均为 200，目标 chunk 含新增区块；这只证明静态分发文件，不证明浏览器渲染、交互或窄屏视觉。
- **前端契约复核（Frontend Acceptance 局部）：** 复核提交树中的 `frontend-public/lib/api/console.ts` `fetchAgentPerformanceStats`（以 `repo_id` / `days` 查询参数请求 `/console/stats/agent-performance`，与 `agent_runner_console.py` 路由签名一致）、`frontend-public/lib/api/types.ts` 的 `AgentPerformanceStats` DTO，以及 Stats 页将现有 `trendRepoId` / `trendDays` 筛选传入该客户端；`tsc --noEmit` 通过（rv-2-typecheck.txt），字段形状与 rv-1 真实路由 JSON 断言一致（rv-1-api-green.txt）。E2E 对参数传递的运行时断言仍未执行。
- **rv-2 其余部分维持受阻结论：** 真实页面展示、筛选交互、空态 / 错误态、窄屏滚动、页面级负控与截图仍未完成；本补充不改变上述状态，也不构成验收或归档授权。恢复尝试 5 更新窄屏用例后重新生成的 rv-2 源文件哈希见 [rv-2-final-tree.txt](rv-2-final-tree.txt)，其工作树源码摘要为 `0a2db9cacb1935ded8729fc059f2dedfad195ffea99e229a9351a3fba28654aa`。

## 恢复尝试 2 补充（2026-10-10）

- rv-1 在最终相关改动后重新运行：API / SQLite 定向测试 **4 passed、31 deselected**；并从该集成用例实际捕获 repo-filter、all-repos 和 1-day 三份 HTTP 200 JSON 响应，输出见 [rv-1-api-json.json](rv-1-api-json.json)。
- rv-2 在最终静态构建后曾通过裸 `kc console` 检查；恢复尝试 4 确认它解析到全局安装版，此历史证据已废弃，由 [rv-2-kc-console-worktree.txt](rv-2-kc-console-worktree.txt) 取代。
- 浏览器状态仍是阻塞：Playwright 的既有精确失败为 setup 阶段 Chromium `MachPortRendezvousServer` `Permission denied (1100)`、4 个页面用例未运行；本轮 CUA 的 IAB 不可用且 Chrome 未获准，见 [rv-2-cua-browser-blocker.txt](rv-2-cua-browser-blocker.txt)。所以预设数值负控未触达页面断言，真实页面、筛选、空态/错误态、375px 窄屏、截图及 `just prd review` 浏览器呈现仍未完成。manifest 如实将 rv-2 标为 `blocked`；不勾选未执行验收项，也不把静态路由检查说成 E2E 通过。

## 恢复尝试 3 补充（2026-10-10）

- 按 PRD 原命令重跑 E2E 时，先遇到 uv 默认缓存目录位于 worktree 外且不可写；将 `UV_CACHE_DIR` 指到 `/tmp/keda-issue-263-uv-cache` 后，同一 E2E 命令启动了 backend 与 public 前端并进入 Playwright，但 Chromium 在 auth setup 阶段以 `MachPortRendezvousServer` `Permission denied (1100)` / `SIGTRAP` 退出，4 条 Stats 页面用例均未运行。两次命令输出分别见 [rv-2-e2e-cache-blocker.txt](rv-2-e2e-cache-blocker.txt) 与 [rv-2-e2e-chromium-blocker.txt](rv-2-e2e-chromium-blocker.txt)。
- 本次 CUA 重新探测得到 `Browser is not available: iab`，`getState()` 的浏览器列表为空；请求打开 Google Chrome 返回 `Computer Use was not approved to use Google Chrome`。这不是产品断言的红灯。预设样本数负控未触达页面断言，也没有恢复后的页面绿灯，故不能据此勾选 rv-2；浏览器访问结果见 [rv-2-browser-access.txt](rv-2-browser-access.txt)。
- 重新运行 `UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache just console-sync` 成功。通过当前 worktree 的 `kc console --no-browser --port 8763` 和新的 HTTP 请求读取 `/app/stats` 及该页引用的 JavaScript bundles：页面和所有 12 个 bundle 返回 200，新增区块文案出现在对应 bundle，见 [rv-2-console-sync-latest.txt](rv-2-console-sync-latest.txt) 与 [rv-2-kc-console-http-latest.txt](rv-2-kc-console-http-latest.txt)。此证据只证明静态分发内容与 HTTP 路由，不证明页面渲染、交互或视觉。
- 证据 manifest 已保留 rv-1 的实际失败负控及恢复后通过记录；rv-2 保持 `blocked`，清楚标出其产品负控和正向页面断言均未执行。当前 rv-1 / rv-2 源文件摘要分别见 [rv-1-final-tree.txt](rv-1-final-tree.txt) 和 [rv-2-final-tree.txt](rv-2-final-tree.txt)。页面截图、窄屏检查与 `just prd review` 浏览器呈现仍待获准的真实浏览器环境；没有生成占位图，也没有将 HTTP 检查冒充视觉证据。
- 重新执行 rv-1 定向 pytest 后结果为 `4 passed, 31 deselected`，并重采了三份 HTTP 200 API JSON。随后使用仓库的 `validate_evidence_manifest` 解析 `evidence.json`：两项均为正整数编号，8 个 rv-2 / 7 个 rv-1 引用文件存在且命名、路径格式通过；两项都保留 `negative_control` 与 `expected_fail` 字段。此结构校验不代表 rv-2 产品负控已执行，rv-2 仍为 `blocked`。

## 恢复尝试 5 补充（2026-10-10）

- 先从实际输出确认最近一次执行到页面断言的失败：此前 E2E 的 auth setup 与三个 Stats 页面检查通过、窄屏项失败，`stats-agent-performance-preset-table` 的 `toBeVisible()` 收到 `hidden`。截图显示展开的侧栏占用 256px，剩余内容区不足以呈现表格。该精确失败保留于 [rv-2-e2e-narrow-red.txt](rv-2-e2e-narrow-red.txt)；Playwright 原截图留在该次测试结果目录。
- E2E 窄屏用例现在先通过生产页面既有的“收起导航栏”按钮腾出内容区，再断言表容器可见、内容宽于视口且 `overflow-x` 为 `auto`。当前改动后的单文件 TypeScript 与 ESLint 检查通过，见 [rv-2-e2e-spec-checks-attempt5.txt](rv-2-e2e-spec-checks-attempt5.txt)。
- 按 PRD 命令重跑完整目标 spec，services readiness 成功，但 Chromium 在 auth setup 启动时再次因 `MachPortRendezvousServer` `Permission denied (1100)` 退出，4 个 Stats 用例未运行；完整输出见 [rv-2-e2e-attempt5.txt](rv-2-e2e-attempt5.txt)。因此无法确认窄屏调整是否变绿，也未执行 PRD 规定的预设样本数负控、静态 `kc console` 浏览器冒烟或截图采集。
- `evidence.json` 的 rv-2 仍为 `blocked`，现含窄屏真实失败和本次启动阻塞两份证据；`human-review-checklist.md` 与 §9.1 保持未完成。此前通过的页面展示、空态和错误隔离检查只支持对应 Acceptance Checklist，不等同于完整 E2E 通过。

## 本轮最终复核（2026-10-10）

- 以 manifest 中的 `bash tasks/evidence/P2-FEAT-20261009-171037-agent-preset-performance-stats/scripts/rv-1-validation.sh` 重跑 rv-1：统计 API / SQLite 用例 **4 passed、31 deselected**，现有 lifecycle 分位数回归 **1 passed**，重新采集三份 HTTP 200 API JSON 与 `rv-1-final-tree.txt`。树摘要仍为 `0f61c4dc9cd1797e53cc0028fba988b3349086677f93347a2370906148f5610a`。
- 按 PRD 原命令重跑 `PLAYWRIGHT_STACK_TIMEOUT_MS=45000 UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache just e2e tests/playwright-e2e/tests/smoke/stats-agent-performance.spec.ts`。backend 与 public 前端启动后，Playwright auth setup 的 Chromium 进程因 `MachPortRendezvousServer Permission denied (1100)` 退出，4 条 Stats 页面用例未运行；完整输出见 [rv-2-e2e-final-red.txt](rv-2-e2e-final-red.txt)。该失败没有到达产品断言，PRD 的 `attempt_count=5` 负控未执行。
- CUA 首次尝试 IAB 返回 `Browser is not available: iab`，inventory 显示 `browsers=[]`；请求 Chrome 返回 `Computer Use was not approved to use Google Chrome`。状态见 [rv-2-browser-access-final.txt](rv-2-browser-access-final.txt)。没有浏览器截图，也没有运行 `just prd review` 做 HTML 首卡、翻页或结果卡检查。
- 最终 `pnpm typecheck` 和 `pnpm build` 均通过；`just console-sync` 后以 worktree `uv run kc console --no-browser --port 8879` 启动真实服务，并用新 HTTP 请求检查 `/app/stats/` 与全部 12 个引用 bundle，均为 200，目标 bundle 含 Agent / 预设区块。命令输出分别见 [rv-2-typecheck-final.txt](rv-2-typecheck-final.txt)、[rv-2-build-final.txt](rv-2-build-final.txt)、[rv-2-console-sync-final.txt](rv-2-console-sync-final.txt) 和 [rv-2-console-http-final.txt](rv-2-console-http-final.txt)。这是静态分发 / HTTP 证据，不是浏览器视觉验收。
- 修复 `capture_final_tree_evidence.py` 使它每次只写入所选 RV 的一份文件；按 rv-1 和 rv-2 分别刷新摘要。仓库 `validate_evidence_manifest` 校验 `evidence.json` 通过：item 1 引用 8 个 `rv-1-*` 文件，item 2 引用 9 个 `rv-2-*` 文件，均存在且没有目录前缀；manifest 含版本 1、`zh-CN`、正整数 item 编号及 `negative_control` / `expected_fail`。rv-2 仍诚实标为 `blocked`，其产品负控和页面绿跑未观察到。
- 最终 worktree 执行 `just test` 时 lint 阶段通过，核心测试 **1 passed、14 failed、149 deselected**；失败来自进程扫描被环境禁止及写入 `/Users/zata/.kedacode/daemon-locks/repo.lock` 被拒绝。另一次 `just lint --full` 因本地 `just test` 标记 tree 过期而失败，不能声称总门禁通过。
- 更新了 §9 中已实际验证的前端 API / TypeScript 项及最新证据引用；其余依赖窄屏绿跑、真实截图和 HTML 浏览器呈现的项保持未勾选。`human-review-checklist.md` 与配套 HTML 已更新为引用 PRD 原文并说明当前阻塞，但两者尚未在真实浏览器检查。Human-Confirmed 两项仍开放，验收横幅为 `🧍 待人工验收`；PRD 保持 pending，不声称 verifier `PASS` 或归档完成。

## 恢复尝试 6 收口（2026-10-10）

本节是 rv-2 的当前权威状态；此前各节中的 `blocked` / 浏览器受阻描述均已被推翻，仅保留为历史记录。

- 目标 E2E 按 PRD 原命令 `PLAYWRIGHT_STACK_TIMEOUT_MS=45000 UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache just e2e tests/playwright-e2e/tests/smoke/stats-agent-performance.spec.ts` 全绿：auth setup + 4 个 Stats 页面用例共 **5 passed（35.8s）**，覆盖 attempt 分组 / 历史模型快照 / 独立任务 outcome、空态与时间窗口筛选（仓库下拉走 GitHub 实时 overview 的耐心等待 / 刷新重试路径）、端点失败时原 Stats 区块隔离，以及 375px 窄屏先点击“收起导航栏”后表容器可见、scrollWidth > clientWidth、`overflow-x: auto`。历史窄屏红灯保留于 [rv-2-e2e-narrow-red.txt](rv-2-e2e-narrow-red.txt)，本轮已反转；上一轮的 Chromium `MachPortRendezvousServer Permission denied (1100)` 未在本会话复现（最小启动探测通过），视为该会话的瞬态环境故障。输出见 [rv-2-e2e-green-final.txt](rv-2-e2e-green-final.txt)。
- PRD 指定的产品负控已执行并触达真实浏览器断言：`PERFORMANCE_STATS.presets[0].attempt_count` 4→5 后，历史预设行 `toContainText('4')` 失败（Received 行文本含样本数 5），1 failed、RED_EXIT=1；恢复 fixture 后同一命令 2 passed、GREEN_EXIT=0。完整红绿对照见 [rv-2-e2e-negative-control.txt](rv-2-e2e-negative-control.txt)。
- 真实页面截图已从 kc console 静态分发采集：桌面 1440x900 卡片与整页（[rv-2-stats-agent-performance.png](rv-2-stats-agent-performance.png)，GitHub overview 实时数据）与 375x812 窄屏收起导航后的横向滚动状态（[rv-2-stats-agent-performance-narrow.png](rv-2-stats-agent-performance-narrow.png)）；采集脚本与断言维度见 `scripts/capture-rv2-console-screens.js`。`just console-sync` 与 worktree `uv run kc console` 的 `/app/stats/` + 引用 bundle HTTP 200 复核见 [rv-2-console-sync-green.txt](rv-2-console-sync-green.txt) 与 [rv-2-console-http-green.txt](rv-2-console-http-green.txt)。
- 人审 HTML 通过真实浏览器呈现检查：`just prd review --print` 解析到同目录 `human-review-checklist.html`，浏览器断言首屏卡片可见、单选与翻页、结果卡生成 Markdown、两张内嵌截图 `naturalWidth > 0`、控制台零 pageerror；第 2 项截图呈递页存图见 [rv-2-prd-review-checklist-page2.png](rv-2-prd-review-checklist-page2.png)，输出见 [rv-2-prd-review-browser-check.txt](rv-2-prd-review-browser-check.txt)。`human-review-checklist.md` 同步更新为已呈递状态。
- 修改后的目标 spec 单文件 TypeScript / ESLint 通过，见 [rv-2-e2e-spec-checks-final.txt](rv-2-e2e-spec-checks-final.txt)；`frontend-admin/` 无改动（`git diff HEAD -- frontend-admin` 为空）。E2E 仅改测试 spec，未改任何产品代码。
- `evidence.json` 的 rv-2 已按上述结果重写（移除 `blocked` 状态，14 个 `rv-2-*` 证据文件、已执行负控与 `5 passed` stdout 断言），并用仓库 `validate_evidence_manifest` 校验通过：item 1 引用 8 个 `rv-1-*`、item 2 引用 14 个 `rv-2-*`，均存在且命名合规，两项均含 `negative_control` / `expected_fail`。本轮收尾后重采最终源码树：rv-1 摘要 `0f61c4dc9cd1797e53cc0028fba988b3349086677f93347a2370906148f5610a`（不变），rv-2 摘要 `f181829f0a7b444adb6a5e3d8dfac534d5017d2c58ee4f66fe7b8fcf8e950dbc`（含本轮 spec 修改），见 [rv-1-final-tree.txt](rv-1-final-tree.txt) 与 [rv-2-final-tree.txt](rv-2-final-tree.txt)。
- Acceptance Checklist 中依赖 rv-2 的窄屏、E2E 绿跑 + 截图、证据重采关联、§9.1 呈递与 HTML 浏览器检查已全部勾选并关联上述证据；两项 `Human-Confirmed` 仍留给人，独立 verifier 未运行。验收横幅保持 `🧍 待人工验收`，PRD 留在 `tasks/pending/`，本报告不声称 verifier `PASS` 或归档完成。

## 门禁被拒后的恢复：clean-tree 检查修复与 rv-1 / rv-2 复跑（2026-10-10）

- 上一轮独立 verifier 已给出 green verdict（见 [verifier-response.txt](verifier-response.txt)），但 runner 拒绝接受：`run_verifier_agent` 的 post-verifier clean-tree 检查（`src/backend/core/use_cases/run_verifier_agent.py:787`）报「verifier 改动了已提交代码树」。核查后确认 verifier 的两处负控源码编辑（`console_stats.py`、`agent-performance-section.tsx`）均已恢复且本分支恢复后复跑为绿；实际脏的只有被 WIP checkpoint 误提交的 worktree 局部会话记录 `.iar/agent-runner/sessions/qoder.json`——verifier 自身运行会按 crash-reconciliation 设计覆盖该文件，使其与提交内容不一致，从而在任何 verifier 轮次都必然打红该门禁。
- 修复（不改产品源码）：镜像 issue-262 分支 `e202b23d` 的 `.gitignore` 规则新增 `.iar/agent-runner/`，并从工作树移除被跟踪的 `sessions/qoder.json` 与 `sessions/codebuddy.json`（已备份至 `/tmp/issue-263-session-backup/`），使 runner 会话簿记永不进入代码树；`e202b23d` 的 verifier-phase 落盘守卫属该分支交付范围，本分支不重复修改源码。移除后 `git check-ignore --no-index` 确认规则命中，`git status --porcelain` 仅剩本意图内差异。
- 按 manifest 原命令复跑验证计划：rv-1 `scripts/rv-1-validation.sh` 全绿（统计 API / SQLite 用例 **4 passed、31 deselected**，lifecycle 分位数回归 **1 passed**，三份 HTTP 200 API JSON 数值与既定口径一致，codex 组 3/2、P50 30s、P90 46s、unbound=1），`rv-1-final-tree.txt` 摘要 `0f61c4dc…` 不变；rv-2 `PLAYWRIGHT_STACK_TIMEOUT_MS=45000 UV_CACHE_DIR=/tmp/keda-issue-263-uv-cache just e2e tests/playwright-e2e/tests/smoke/stats-agent-performance.spec.ts` 全绿（**5 passed（25.3s）**，exit 0），`rv-2-e2e-green-final.txt` 与 `rv-2-final-tree.txt` 摘要 `f181829f…` 已重采。`evidence.json` item 2 的 output_summary 同步为该次运行时长。
- 交付门禁复跑：`git diff --check` 通过；`just test` **14 passed**；`uv run mkdocs build --strict` exit 0。红→绿证据链沿用已提交的负控文件（`rv-1-api-negative-control.txt` 等与 `rv-2-e2e-negative-control.txt`），且 verifier 本轮已独立在源码层面复现过红/绿。
