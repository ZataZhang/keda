## 人审导航 / Human Review Navigation

- PRD：`tasks/archive/P1-FEAT-20261012-103222-unified-runtime-dashboard.md`
- PR / CI：尚未创建；本目录是 executor 侧实现交付证据。
- 当前验证层级：**real entry**。rv-1..rv-4 均在一次性真实 `kc console`（隔离 HOME + 源库只读副本或删表副本）+ 真实浏览器上采集；早期 component preview 目标图仍留在 `docs/prototypes/assets/`，仅作 design intent 对照，不再是验收依据。
- 采集绑定：被验证代码为 commit `3d65d008` 之上的本工作树（未提交实现改动）；rv-1/rv-2/rv-4 隔离档与断连档生成于 2026-10-13 08:51–08:54（+08:00）；rv-3 于 09:27 增补 390×844 companion 窄视口断言，rv-3 与 rv-4 view-degradation 在 09:3x 完成负控注入变红→还原→重建→复跑绿的闭环；其后再无实现改动。

### 结果总览

| RV | 结论 | 真实入口 | 关键证据 |
|---|---|---|---|
| rv-1 默认看板/日志续读/调用折叠 | ✅ 绿 | `http://127.0.0.1:8803/app/dashboard/`（`with-rv-console.sh auto copy`） | `rv-1-run.log`、`rv-1-endpoint-provenance.json`（46 任务、label→bucket 一致）、`rv-1-runtime-dashboard.png`、`rv-1-task-detail-expanded.png`、`rv-1-negative-control.log` |
| rv-2 桌面小窗链路 + 跨源拒绝 | ✅ 绿（两分支） | 真实 helper/pywebview 进程 + WindowServer 探针；无 helper 分支临时移出 site-packages | `rv-2-run.log`、`rv-2-companion-window-chain.json`、`rv-2-companion-page.png`、`rv-2-missing-helper.{json,png}`、`rv-2-negative-control.log` |
| rv-3 触达比例与不可比降级 | ✅ 绿 | 真实 progress API → Dashboard/companion DOM | `rv-3-run.log`、`rv-3-impact-readings.json`（34 任务实测）、`rv-3-impact-*.png`、`rv-3-negative-control.log` |
| rv-4 缺来源/断连只降级受影响视图 | ✅ 绿（三档） | 三 Console 并排：参考 `8801` / 删表 degraded `8802` / 零仓库 fresh `8803` | `rv-4-isolated-run.log`、`rv-4-companion-offline.{json,png}`、`rv-4-isolated-board.json`、`rv-4-source-partial.png`、`rv-4-empty-board.png`、`rv-4-negative-control.log`、`rv-4-isolated-negative-control.log` |

### 1. rv-1 — 真实生命周期任务看板

命令：`bash scripts/with-rv-console.sh auto copy -- bash scripts/run-rv.sh rv1-real-entry.spec.mjs`（exit 0）。
断言链：runtime summary 的 `sync_status=ready`、状态计数（running 15 / waiting 1 / blocked 14 / completed 16）与 Issue 标签逐条对账；选中 Issue 后走真实 `GET .../issues/{n}/logs` 的 offset 续读（端点路径含选中 Issue 身份）、新行出现；结构化调用初始折叠、展开保留原始输出。负控：`rv-1-negative-control.log` 为开发期「默认展开」缺陷被同一 spec 捕获的变红记录（修复后 08:51 复跑绿）；PRD 设计的「缺日志注入」负控在最终构建由 `rv-4-view-degradation` 的空/缺日志不伪造断言承接（`rv-4-negative-control.log` 伪造注入变红）。

### 2. rv-2 — 桌面 helper 真实窗口与来源边界

分支 A（真实链路）：Dashboard 同源 POST `.../desktop-companion/window` 返回 `status=launched`；helper 以固定 argv `-m backend.engines.desktop_companion.helper --origin http://127.0.0.1:8800` 起进程；WindowServer 探针列出一个 420×620、layer 25（浮动置顶）的窗口归属该 pid；Console 访问日志出现 helper WebView 对 `GET /app/companion/` 的 200 请求；重复开启聚焦（focus_ticket 递增不新建窗口）、关闭后窗口消失。跨源负控（`rv-2-negative-control.log`）：非 Console origin 的启动请求被拒绝且无窗口。

分支 B（未安装）：把 webview 临时移出 site-packages 后，Dashboard 保持可用，窗口状态显式返回 `not_installed` 并提示安装 `kedacode[desktop]`（`rv-2-missing-helper.json` / `rv-2-missing-helper-hint.png`）。

**披露**：PRD presentation 期望的 `rv-2-native-window.mp4` 与原生窗口截屏在本执行环境不可采集——`screencapture` 无屏幕录制权限（`could not create image from display`），见 `rv-2-companion-window-chain.json` 的 `screencapture_ok=false`。系统窗口存在性以 WindowServer 探针（pid 绑定 + 尺寸 + 浮动层）与 Console 请求日志为准；窗口内容渲染证据为 companion 路由的页面截图 `rv-2-companion-page.png`。人工验收（§2 决策项）仍需在桌面肉眼确认置顶/关闭观感，`Human-Confirmed` 保持空框。

### 3. rv-3 — 改动树触达比例逐字段一致

命令同 rv-1 的 `with-rv-console.sh auto copy`。真实 progress API 与 DOM 对账 34 个任务：可比任务（如 keda#284 `touched=14/judgeable=16/unresolvable=4`）渲染 `14/16 · ?4` 与"改动树触达 · 弱信号"，`progressbar` 的 `aria-valuenow/valuemax` 与端点一致，无完成率、无成功色；`no_prd`、`reason:no_tree`、`read_failure` 各按状态显示原因且不画条。同一运行内以真实浏览器 viewport 390×844 打开 `/app/companion/`：可见任务行 ≤4、`scrollWidth` 无横向溢出、可见可比行的触达条 `aria-label=改动树触达 · 弱信号：14/16，另有 4 个无法判定节点`（`rv-3-impact-companion-390.png`、`rv-3-impact-readings.json` 的 `companion_390x844`）。负控（`rv-3-negative-control.log`，最终构建复跑）：把触达条文案临时改写为「任务完成度」后同一 spec 立即变红（期望「改动树触达 · 弱信号」，实际渲染完成度口径），还原后绿；PRD 设计的 no_base 注入（删除 merge-base）未破坏真实 worktree 复现——live 数据本无 no_base 样本，该状态文案由后端契约测试与 mocked 回归 spec 覆盖，注入式鉴别力由上述口径负控证明。

### 4. rv-4 — 缺来源只降级受影响字段

三档一次性 Console 并排（`rv4-isolated.sh`：copy 参考 / 删 `prd_lifecycle` 表的 degraded / 零仓库 fresh）：degraded 档任务行从 46 降到 33，横幅显式披露"部分来源本轮不可读：prd_lifecycle。受影响字段留空，不会折算成零"；触达视图改为"未关联 PRD · 改动树触达不适用"且 0 条比例条；fresh 档空看板不编造任务。断连链路（`rv4-companion-offline.spec.mjs`，50.6s）：kill Console 进程后小窗进入"读不到本机 Console：Network Error"并标注断连前读数为残留，停止冒充最新；恢复后自动跟上、错误与残留提示清除。负控（`rv-4-negative-control.log`）：曾以注入假 0%/空任务成功色的反例复现"注入即失败"，结论并入隔离档。

### 执行者已替你核对过什么（自动档）

- `just test`（本地核心档）绿；本功能后端定向测试 `tests/test_runtime_dashboard.py` + `tests/test_desktop_companion.py` 30 passed。
- `just frontend-public typecheck` / `build` 绿（静态导出含 `/app/dashboard`、`/app/companion`）；`just console-sync` 已刷新随包 console 静态资源；`uv run mkdocs build` 绿。
- `tests/playwright-e2e`：`npx tsc --noEmit` 对改动文件无错误（`idea-inbox.spec.ts` 的 1 处 TS2339 为 main 既有、非本改动文件）。新增 `runtime-dashboard.no-auth.spec.ts` 10/10 绿；`console-pages.no-auth.spec.ts` 在本树 15 passed / 5 failed——这 5 项（stats ×2 `运行时间` strict 命中 3 元素、backlog live-output ×3 超时）在**移除全部本交付改动的干净 HEAD** 上同样失败（基线复跑 7 failed，且其中 dashboard-queue 与 retry 两项由本交付改动修复），属于 main 既有漂移（与 issue-282 stats Tab PR 的 CI 红灯同期），不由本 PRD 引入，也不在本交付顺手修。
- 随包 skill 守卫 `test_packaged_skill_command_examples_match_cli_help`：main 上既有失败（HEAD `fef5cf04` 引入 `kc daemon --all` 裸示例，schema 只注册 `daemon run`）。本交付以最小改动把两处示例改为等价且已注册的 `kc daemon run --all`，守卫转绿；与本 PRD 功能无关，单独说明于此。
- 无新增存储、无第二套检测逻辑：日志走既有 Issue log offset 端点、调用走既有 invocation ledger、触达走既有 `backlog/prds/{path}/progress`（`rv-1-endpoint-provenance.json`、`rv-3-impact-readings.json` 记录了实际请求 URL）。

### 文件索引

- 运行脚本：`scripts/`（`rv-console.sh`、`with-rv-console.sh`、`rv4-isolated.sh`、`run-rv.sh`、各 `rv*.spec.mjs`、负控与探针脚本）
- 每个 RV 的原始命令输出保留在同名 `*-run.log` / `*-negative-control.log`，均含 exit_code 与可复跑命令。
