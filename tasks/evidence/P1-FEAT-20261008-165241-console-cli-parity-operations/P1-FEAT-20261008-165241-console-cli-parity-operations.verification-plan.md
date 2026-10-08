# Realistic Validation Plan — Console 网页版补齐 CLI 操作能力

事实源：PRD `tasks/pending/P1-FEAT-20261008-165241-console-cli-parity-operations.md` §7.6（本文件是其执行投影，oracle 定义以 PRD 为准）。

被测边界（真实）：console API 路由、core 用例、`kc` 子进程真实启动、前端构建为真实链路；GitHub 读写按各 oracle 的 `mock_boundary` 允许隔离 registry / `gh` 假件（假件 fail-loud）；rv-1/3/4 的浏览器手测与线上 GitHub fresh read 属人工第二触点（见证据报告保真度披露）。

## Oracle 清单

| id | tier | reviewer | behavior | real_entry | 证据 |
|---|---|---|---|---|---|
| rv-1 | R1 | human | dashboard「跑一轮/复核一轮」真实拉起托管子进程、进程可见可停；「全部」列表含未入队 Issue；详情按点击的仓库取快照 | `bash tasks/evidence/<stem>/scripts/rv-1-dashboard-actions.sh`（负例：不启动则断言必须红；正例：pytest 6 例，含撞号作用域契约测试） | `rv-1-dashboard-actions.txt`；浏览器手测留待 §9.1 呈递 |
| rv-1b | R1 | verifier | `GET /agent-runner/issues/{n}` 的 `repo_id` 作用域在真实 HTTP 面生效：未知仓库与他仓请求不得拿到别的仓库的快照 | `bash …/scripts/rv-1-issue-detail-repo-scope.sh`（负控 A/B 必须双双确认；正例 scoped 读取归属正确） | `rv-1-issue-detail-repo-scope.txt` + `rv-1-issue-detail-repo-scope-negative.txt` |
| rv-1c | R1 | verifier | 真实 dashboard 在快照回落窗口发出的详情请求必须带 `repo_id=<点击的仓库>` | `bash …/scripts/rv-1-issue-detail-repo-scope-ui.sh`（无头 Chrome 走真实 login→dashboard；负控：漏带作用域必须 RED） | `rv-1-issue-detail-repo-scope-ui.txt` + `rv-1-issue-detail-repo-scope.png` |
| rv-2 | R0 | verifier | 真实启动 `kc console`，status/health 端点返回既定 JSON；未知路径 404 负控 | `bash …/scripts/rv-2-runner-status.sh`（live HTTP + curl） | `rv-2-runner-status.txt` + `rv-2-negative-control.txt` |
| rv-3 | R2 | human | 「加入就绪」建 Issue/打标签但绝不启动 runner；运行中冲突；幂等 | `bash …/scripts/rv-3-enqueue-ready.sh`（负例：运行中 Issue 必须被拒） | `rv-3-enqueue-ready.txt` + `rv-3-negative-control.txt`；线上 GitHub 手测留待 §9.1 |
| rv-4 | R2 | human | 标签在已同步标准集内增删真实落 GitHub；集合外拒绝且零变化 | `bash …/scripts/rv-4-issue-labels.sh`（负例：`agent/redy` 拼错标签被拒、edits=0） | `rv-4-issue-labels.txt` + `rv-4-negative-control.txt`；线上手测留待 §9.1 |
| rv-5 | R2 | verifier | start 高级选项透传；缺省请求与旧契约 argv 逐字节一致；直出 PR 对 PRD 锚点目标在发起端被拒 | `bash …/scripts/rv-5-start-contract.sh`（负例：identity 断言必须红 + `direct_pr` 配 PRD 锚点必须被拒） | `rv-5-start-contract.txt`（28 例契约测试） |
| rv-6 | R1 | verifier | 「恢复发布」复用既有恢复逻辑；不可恢复态明确失败 | `bash …/scripts/rv-6-recover-action.sh` | `rv-6-recover-action.txt` |
| rv-7 | R1 | verifier | 一句话建 Issue 经 from-prompt 用例真实创建；空 prompt 与越界 `issue_type` 被拒 | `bash …/scripts/rv-7-from-prompt.sh`（负例：空 prompt + `"feature; rm -rf"` 类型都必须红） | `rv-7-from-prompt.txt`（37 例） |
| rv-8 | R0 | verifier | 死注册页与 `register()` 调用彻底移除且前端构建通过 | `bash …/scripts/rv-8-register-removal.sh`（负例：探测器放回引用必须命中） | `rv-8-register-removal.txt` |

机器门禁：`CI=true just test all` 与 `just lint`（见证据报告「机器门禁」节与 PRD §14「收尾修复」条目）。

证据脚本门禁（第 4 轮）：`rv-3`/`rv-6` 的绿步此前只把 `pytest` 退出码写进日志（`[green-exit=N]`）而不 `exit` 非零，pytest 退化时脚本仍报 `DONE`；现与 `rv-1`/`rv-4`/`rv-7` 一样 fail-loud，`[green-exit=0]` 同时进 `evidence.json` 断言。`capture_ui_screenshots.mjs` 对「加入就绪」按钮的硬等待已改为容忍缺席继续跑（该按钮仅渲染于 `not_started` 态 PRD），并新增浏览器侧 Issue 详情请求记录。

结构化证据 manifest：同目录 `evidence.json`（version 1，zh-CN，10 个 item、32 条 stdout 断言，本地复跑不入库）。
