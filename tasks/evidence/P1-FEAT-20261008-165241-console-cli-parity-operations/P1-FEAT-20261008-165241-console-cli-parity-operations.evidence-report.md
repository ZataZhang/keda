# Realistic Validation 证据报告 — Console 网页版补齐 CLI 操作能力

PRD: `tasks/archive/P1-FEAT-20261008-165241-console-cli-parity-operations.md`（交付时由 runner 归档）
分支: `issue-247`（worktree `.iar-worktrees/issue-247`）
日期: 2026-10-08（执行侧交付 + recovery 尝试 2 收尾 + closeout 真实入口截图补采；自动化证据于最终代码树重采）

## 人审导航 / Human Review Navigation

**先读这里**：本报告是执行侧自证；人工第二触点的入口是 `just prd review tasks/archive/P1-FEAT-20261008-165241-console-cli-parity-operations.md`（打开同目录 `human-review-checklist.md`）。你只需对 4 项表态（决定一/二/三 + §9.1 呈递过目），其余由机器门禁与本报告承担。

| 看什么 | 呈递物与打开方式 | 预期值（10 秒自检） | 状态 |
|---|---|---|---|
| dashboard 一键执行与全量 Issue 视图（rv-1） | 下方真实入口截图（已补采）+ `open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-1-dashboard-actions.png"`；自动化层：`bash tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/scripts/rv-1-dashboard-actions.sh` | 进程页出现新托管进程记录且状态非幽灵（点「跑一轮」后，留人工）；「全部」列表可见未入队 Issue（截图已见） | 真实入口渲染截图已补采；「跑一轮」进程页核对与线上手测待人工 |
| 「加入就绪」入队不启动（rv-3） | 下方 backlog 真实入口截图（已补采）+ `rv-3-enqueue-ready.png`；`bash …/scripts/rv-3-enqueue-ready.sh`；独立核对 `gh issue view <n> --json labels` | Issue 带 `agent/ready`；进程页无新 runner 记录；运行中 Issue 调用返回冲突 | 自动化层 GREEN（含运行中冲突负例）；UI 入口宿主页面已截图（按钮态限制见下方披露）；线上手测待人工 |
| Issue 标签增删真实生效（rv-4） | 下方真实入口截图（已补采）+ `rv-4-issue-labels.png`；`bash …/scripts/rv-4-issue-labels.sh`；独立核对 `gh issue view <n> --json labels` | 页面徽章与 gh 查询一致；集合外标签（如拼错的 `agent/redy`）被拒且 GitHub 零变化 | 真实入口渲染截图已补采（编辑面板 + 标准集下拉展开）；线上增删手测待人工 |

### 真实入口截图（closeout 补采 2026-10-08；第 3 轮审查修复后于最终代码树全部重采 2026-10-09；第 4 轮详情作用域修复后再次全部重采并新增 1 张，共 8 张）

捕获方式：`uv run kc console --no-browser --port 8399`（worktree issue-247 最终代码树，前端经 `just console-sync` 重建）+ 无头 Chrome（playwright-core 经 `executablePath` 复用本机 Google Chrome）；捕获脚本 `scripts/capture_ui_screenshots.mjs`、`scripts/capture_backlog_screenshots.mjs`。**全部交互只读**——未点击「跑一轮 / 复核一轮 / 加入就绪 / 开始 / 创建 Issue」，未写任何标签，对共享 GitHub 状态零写入。2026-10-09 因本轮改动了标签候选下拉与启动高级选项抽屉，7 张截图已在重建后的真实入口全部重采；第 4 轮改动了 dashboard 详情面板的取数请求（`repo_id` 作用域），7 张截图与两份捕获日志（`rv-capture-ui.txt`、`rv-capture-backlog.txt`）再次在同一最终代码树重采。

![rv-1 dashboard 操作态（真实入口渲染截图）](rv-1-dashboard-actions.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-1-dashboard-actions.png"`）

验证层级：`real-entry render`——真实 kc console 入口 + 真实后端数据。可见：Runner 状态条（FR-2，`Runner gh 正常 前台模式 仓库3 队列上限1 默认agent claude …autopilot 关`）、仓库卡「跑一轮 / 复核一轮 / 刷新」操作行（FR-1）、「监控中 / 全部」切换、右侧 Issue 详情与标签编辑面板（FR-5）。

![rv-1 dashboard 全部 Issue 视图（真实入口渲染截图）](rv-1-dashboard-all-issues.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-1-dashboard-all-issues.png"`）

验证层级：`real-entry render`。可见：「全部」视图全量 Issue 列表（FR-4），无 agent 标签的 #239 标注「未入队」徽章。

![rv-3 backlog 列表视图与 PRD 详情（真实入口渲染截图）](rv-3-enqueue-ready.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-3-enqueue-ready.png"`）

验证层级：`real-entry render`。可见：backlog 列表视图三张 PRD 卡 + 右侧 PRD 详情面板（FR-3/FR-7 入口宿主页面）。**如实披露**：「加入就绪」按钮仅渲染于 `not_started` 态 PRD（`enqueueReadyVisible` / `canStartBacklogPrd` 状态门控），当前受管仓库全部 pending PRD 均已入队或运行中，无头收尾不得向共享仓库写入新 PRD 文件，故该按钮未在本截图中出现——按钮写路径由自动化层 `rv-3-enqueue-ready.txt` + 负控证据覆盖，PR review 阶段人工手测补齐。

![rv-4 Issue 标签编辑面板（真实入口渲染截图）](rv-4-issue-labels.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-4-issue-labels.png"`）

验证层级：`real-entry render`。可见：「全部」视图选中未入队 Issue #239 的轻量详情（`console-issue-detail`）+ 标签（可编辑，范围限标准集）编辑器 + 「添加标签」下拉展开展示已同步标准集（blocked/claude/codebuddy/codex/deliberate/failed/kimi/opencode/pi/qoder…）。下拉只读展开，未选中任何项，零写入。**第 3 轮审查修复后可见**：下拉顶部的知情提示已渲染——「候选项与 `kc labels sync` 同源；其中在途状态、直发档位与 validation 签收标签会被 daemon、依赖判定和验证门禁立即消费，点击即写入 GitHub。」（决定一补充呈递的操作者侧一半；另一半在 PRD §2 与 `docs/guides/agent-runner.md`）。

![rv-7 一句话建 Issue 对话框（真实入口渲染截图）](rv-7-from-prompt-dialog.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-7-from-prompt-dialog.png"`）

验证层级：`real-entry render`。可见：一句话建 Issue 对话框（FR-8）在生产 Dialog 边界中打开，类型选择器与「创建 Issue」按钮就位；输入为显式标注的示例文本，未提交。

![rv-8 登录页——注册链接已移除（真实入口渲染截图）](rv-8-login-no-register-link.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-8-login-no-register-link.png"`)

验证层级：`real-entry render`。可见：登录页（login-form.tsx 变更点）无「立即注册」链接；捕获脚本断言 `a[href="/register"]` 零命中后才落图。

![rv-8 已删除注册页 404（真实入口渲染截图）](rv-8-register-page-404.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-8-register-page-404.png"`)

验证层级：`real-entry render`。可见：真实入口访问 `/register` 返回 404（目录已删，FR-9）。

保真度披露：rv-1/3/4 的行为验收原文要求「真实浏览器 + 线上 GitHub fresh read」。本执行环境为无头 runner 且刻意避免共享 GitHub 写入：执行侧以 `mock_boundary` 允许边界内的替身链路（API→core 用例→fail-loud `gh` 假件→fresh read）+ 真实子进程启动验证核心语义，closeout 阶段又以无头 Chrome 驱动真实 `kc console` 入口补采了上述渲染层截图；**仍未做**的是会改动共享状态的动作手测（点「跑一轮」后的进程页记录、真实入队/标签写回后的线上 GitHub fresh read 交叉核对）——这部分留待 PR review 人工触点，对应 PRD §9.2 `[~]` 项与 §14「证据保真度披露」条目。**截图 `.png` 与原始 `.txt`/`.sh`/`.json` 均不入库**（`.gitignore` 只放行本目录 `*.md`），报告引用的原始文件在本 worktree 原地可开。

执行者已交叉核对（人不必重做）：10 个 rv 脚本 2026-10-09 在第 4 轮修复后的最终代码树全部重跑，负例均 RED、正例均 GREEN；`evidence.json` 全部 32 条 stdout 断言逐条对复跑 stdout 复核命中；机器门禁见下节。浏览器侧另有一条正向取证（见 rv-1c）：真实 dashboard 在缓存回落窗口实际发出 `GET /api/v1/agent-runner/issues/245?repo_id=keda`。普通全量捕获那一轮（`rv-capture-ui.txt`）里浏览器**没有**发出过任何裸详情请求——实况快照全程命中本地缓存，该回落窗口不保证出现，因此专门用 rv-1c 的受控回落来取证。

## 机器门禁

- `CI=true just test all`：结果与命令记录见 PRD §14「收尾修复」条目的门禁记录（本轮最终树重跑）。
- `just lint`：同上。
- PRD 结构合规：`prd_contract.py --json` 解析 Change Log 5 条目六字段完整、执行侧无残留 `- [ ]`、横幅与 §9 对齐（`awaiting_human`）。

## rv-1 dashboard 操作（R1, reviewer: human；自动化层）

负例（RED）：断言「run_once 未启动进程」必须在真实实现下失败——脚本抓到真实子进程 `pid=…`、kind=run_once，`[negative-control exit=1]` 确认控制有牙齿。
正例（GREEN）：`tests/test_console_actions.py`（run_once/review_once 拉起托管进程）+ `tests/test_console_cli_parity.py`（monitored 标注、坏 state 拒绝）+ `tests/test_agent_runner_console_issues_api.py` + `tests/test_agent_runner_monitor.py::test_api_issue_detail_scopes_lookup_to_repo_id` 共 6 passed，`[green-exit=0]`。verbatim 见 `rv-1-dashboard-actions.txt`。

## rv-1b Issue 详情按仓库作用域（R1, verifier；第 4 轮审查修复）

缺陷口径：`GET /api/v1/agent-runner/issues/{issue_number}` 此前不带仓库限定，按 registry 顺序返回**第一个**带该编号的仓库的监控快照；Issue 编号跨仓库撞号时，面板显示的 PR / worktree / 异常来自另一个仓库，而标签读写与动作却发往点击的那个仓库。

真实入口：`bash …/scripts/rv-1-issue-detail-repo-scope.sh` 起真实 `kc console`（FastAPI + 真实 `gh` 读路径），从本地持久化快照读出各仓库的监控中 Issue，再做三条请求：

- 负控 A（`rv-1-issue-detail-repo-scope-negative.txt`）：`?repo_id=not-a-registered-repo` → 404 且 detail 点名该仓库（`Issue #1 not found in repository 'not-a-registered-repo'.`）——证明参数确实被消费，不是摆设。
- 负控 B：`#245` 属于 `keda`，改问 `?repo_id=freshai` → 404（`Issue #245 not found in repository 'freshai'.`）。**修复前这条请求会被忽略作用域、返回 keda 的快照（200）**，正是审查描述的串仓泄漏。
- 正例：`?repo_id=keda` 命中并返回 `https://github.com/ZataZhang/keda/issues/245`，`url` 归属被请求仓库。

**如实披露**：本 registry 实况下 `keda/freshai/fsense/zata-docs/…` 的监控中 Issue 编号**没有重叠**（`colliding issue numbers across repos: []`），因此未能在真实数据上做出「同一编号两次答自不同仓库」的正面对撞；该分支由契约测试 `test_api_issue_detail_scopes_lookup_to_repo_id`（两个假仓库、同编号 7，断言 scoped→只查该仓库且 `repo-a` 连列 Issue 都不被调用；unscoped→保留旧的跨仓库行为；未知仓库→404 且零查询）覆盖。脚本据此走退化断言并在日志里写明 `GREEN confirmed (degraded)`。

## rv-1c 详情请求由发起端带上仓库（R1, verifier；第 4 轮审查修复的浏览器侧）

rv-1b 证明**服务端**按仓库作用域取值；本项证明**面板**确实把点击的仓库带在请求上——审查原话是这类串仓缺陷「此前只能靠人肉点击发现」，所以把请求 URL 取成机器断言。

`bash …/scripts/rv-1-issue-detail-repo-scope-ui.sh` 起真实 `kc console`，用无头 Chrome 走真实 `/login` → `/app/dashboard`：首帧快照让页面自动选中 `#245`，随后把 `/overview/snapshots` 的响应改成「keda 的 issues 变空」，制造审查描述的失效窗口（缓存查找落空 → 面板回落实时详情端点）。浏览器实际发出：

```
GET http://127.0.0.1:8632/api/v1/agent-runner/issues/245?repo_id=keda
```

脚本对「存在不带 `repo_id` 的详情请求」显式判失败（负控：把 `fetchIssueDetail` 的 `repoId` 去掉即 RED），并落图。

![rv-1c 真实 dashboard 在快照回落后带 repo_id 取详情（真实入口渲染截图）](rv-1-issue-detail-repo-scope.png)（local-only；`open "tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/rv-1-issue-detail-repo-scope.png"`）

验证层级：`real user flow`（真实 Next.js 路由 + 真实后端 + 真实浏览器请求），唯一 mock 边界是 `/overview/snapshots` 的响应体被改空以触发回落窗口——被断言的 canonical path/query 未经 mock。GitHub 零写入。

## rv-2 runner 状态条数据源（R0, verifier）

真实启动 `uv run kc console --port 8620 --no-browser`，curl 两端的返回 JSON 逐字记录于 `rv-2-runner-status.txt`：`{"status":"healthy","gh_cli_available":true}` 与完整 status 配置摘要（daemon_mode=false、标签集、仓库清单）。
负控：`/api/v1/agent-runner/health-typo` 返回 404（`rv-2-negative-control.txt`：`negative http_code=404`），证明 200 非 catch-all。

## rv-3 加入就绪不启动（R2, reviewer: human；自动化层）

负例（RED）：对 `agent/running` 的 Issue #21 调用 enqueue-ready，返回冲突错误文案且不改写状态（`[negative-control exit=1]`）。
正例（GREEN）：pytest 4 例——无 Issue 时建 Issue+打标签且不启动、运行中冲突、已就绪幂等、无 supervisor 形参结构性不启动。verbatim 见 `rv-3-enqueue-ready.txt` / `rv-3-negative-control.txt`。
critical_value_source：Issue 号与标签名取自 enqueue-ready 端点响应；fresh_state_probe：独立 fresh read（假件 registry 重读）确认标签与未启动。

## rv-4 标签编辑（R2, reviewer: human；自动化层）

负例（RED）：PUT 集合外标签 `agent/redy` 被拒，拒绝文案指明不在已同步标准集并引导 `kc labels sync`；断言 GitHub 零变化、`edits=0`（假件捕获）。
正例（GREEN）：pytest 7 例——读列表、增删成功、写回后 fresh read 一致、集合外拒绝、与 sync 同源校验、非法 Issue 编号拒绝、**被拒尝试也落审计**（`result="rejected"` 且 detail 含本次想写的标签）。verbatim 见 `rv-4-issue-labels.txt` / `rv-4-negative-control.txt`。

## rv-5 start 高级选项契约（R2, verifier）

负例（RED，两条）：① `build_runner_argv` 在 `fast_merge=True` 与缺省间断言逐字节相同——按预期 AssertionError（`argv` 实际差 `--fast-merge` 一项），证明选项确实进 argv；② 断言「PRD-backed Issue 带 `direct_pr` 也能启动」——按预期被 `start_prd` 在改动 GitHub 之前拒绝且 `github calls = [] / spawns = []`，证明发起端覆盖了 CLI 的 PRD 锚点目标域规则（2026-10-09 第 3 轮审查补，此前该组合只会被子进程用法错误退出）。
正例（GREEN）：pytest 28 例——缺省 `RunnerLaunchOptions()` 与 `options=None` argv 逐字节一致（旧契约不放宽）、非缺省旗标追加、**`direct_pr=True → ("--direct-pr",)` 的正例折算**（首轮只记了互斥负例）、`is_default()` 与 `cli_flags()==()` 同判据（含 `agent="auto"` 别名）、队列级 `--all-ready` 拒跳过闸门旗标、非法组合拒绝、start 链路端到端透传、`start_prd` 直出 PR 拒绝（已关联 Issue / 尚未建 Issue 两条路径）、HTTP 边界 400 且零 spawn 零标签写。verbatim 见 `rv-5-start-contract.txt`。

## rv-6 恢复发布（R1, verifier）

负例（RED）：不可恢复态（detached_head）返回明确失败并翻成 `ConsoleActionError`。正例：pytest 2 例成功迁移 + 错误映射。见 `rv-6-recover-action.txt`。

## rv-7 一句话建 Issue（R1, verifier）

负例（RED，两条）：空 `prompt_text` 被 `min_length` 守卫拒绝（422）；越界 `issue_type`（`"feature; rm -rf"`）在调用建 Issue 用例之前被 `type/*` 集合守卫拒绝且 GitHub 零调用（2026-10-09 第 3 轮审查补——这是 FR-5 标签纪律之外的第二条网页写路径）。正例：pytest 37 例含 route 201、拒绝留痕（`result="rejected"`）、类型候选与 sync 同源、from-prompt 用例行文来自输入、建后停在未入队态。见 `rv-7-from-prompt.txt`。

## rv-8 注册页移除（R0, verifier）

负例（RED）：向树注入 `auth/register` 引用时探测器必须命中（证明 `rg` 断言有牙齿）。正例：`frontend-public/app/(auth)/register` 目录不存在、`rg "auth/register"` 与 `rg "register\("` 零命中、前端静态导出构建通过且无死端点页面。见 `rv-8-register-removal.txt`。

## verifier 结论

独立 verifier 复核属 runner 门禁（`[~]`，本报告不自代判）。执行侧自查：以上全部按 PRD §7.6 的 `expected_fail`/`negative_control` 字段逐项落实，无 `|| true` 刷绿。

## 复跑

单个 oracle：`bash tasks/evidence/P1-FEAT-20261008-165241-console-cli-parity-operations/scripts/rv-<N>-<slug>.sh`（N=1..8，另加第 4 轮的 `rv-1-issue-detail-repo-scope.sh`；脚本名见目录）；全部命令与断言的机器可读版见同目录 `evidence.json`（本地文件，不入库）。全部脚本绿步均已 fail-loud（pytest/HTTP 断言不通过即 `exit` 非零），不再出现「退了非零仍报 DONE」。
