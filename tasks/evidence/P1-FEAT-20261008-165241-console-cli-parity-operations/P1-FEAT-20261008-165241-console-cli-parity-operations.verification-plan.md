# Realistic Validation Plan — Console 网页版补齐 CLI 操作能力

事实源：PRD `tasks/pending/P1-FEAT-20261008-165241-console-cli-parity-operations.md` §7.6（本文件是其执行投影，oracle 定义以 PRD 为准）。

被测边界（真实）：console API 路由、core 用例、`kc` 子进程真实启动、前端构建为真实链路；GitHub 读写按各 oracle 的 `mock_boundary` 允许隔离 registry / `gh` 假件（假件 fail-loud）；rv-1/3/4 的浏览器手测与线上 GitHub fresh read 属人工第二触点（见证据报告保真度披露）。

## Oracle 清单

| id | tier | reviewer | behavior | real_entry | 证据 |
|---|---|---|---|---|---|
| rv-1 | R1 | human | dashboard「跑一轮/复核一轮」真实拉起托管子进程、进程可见可停；「全部」列表含未入队 Issue | `bash tasks/evidence/<stem>/scripts/rv-1-dashboard-actions.sh`（负例：不启动则断言必须红；正例：pytest 5 例） | `rv-1-dashboard-actions.txt`；浏览器手测留待 §9.1 呈递 |
| rv-2 | R0 | verifier | 真实启动 `kc console`，status/health 端点返回既定 JSON；未知路径 404 负控 | `bash …/scripts/rv-2-runner-status.sh`（live HTTP + curl） | `rv-2-runner-status.txt` + `rv-2-negative-control.txt` |
| rv-3 | R2 | human | 「加入就绪」建 Issue/打标签但绝不启动 runner；运行中冲突；幂等 | `bash …/scripts/rv-3-enqueue-ready.sh`（负例：运行中 Issue 必须被拒） | `rv-3-enqueue-ready.txt` + `rv-3-negative-control.txt`；线上 GitHub 手测留待 §9.1 |
| rv-4 | R2 | human | 标签在已同步标准集内增删真实落 GitHub；集合外拒绝且零变化 | `bash …/scripts/rv-4-issue-labels.sh`（负例：`agent/redy` 拼错标签被拒、edits=0） | `rv-4-issue-labels.txt` + `rv-4-negative-control.txt`；线上手测留待 §9.1 |
| rv-5 | R2 | verifier | start 高级选项透传；缺省请求与旧契约 argv 逐字节一致 | `bash …/scripts/rv-5-start-contract.sh`（负例：identity 断言必须红） | `rv-5-start-contract.txt`（12 例契约测试） |
| rv-6 | R1 | verifier | 「恢复发布」复用既有恢复逻辑；不可恢复态明确失败 | `bash …/scripts/rv-6-recover-action.sh` | `rv-6-recover-action.txt` |
| rv-7 | R1 | verifier | 一句话建 Issue 经 from-prompt 用例真实创建；空 prompt 被拒 | `bash …/scripts/rv-7-from-prompt.sh` | `rv-7-from-prompt.txt`（34 例） |
| rv-8 | R0 | verifier | 死注册页与 `register()` 调用彻底移除且前端构建通过 | `bash …/scripts/rv-8-register-removal.sh`（负例：探测器放回引用必须命中） | `rv-8-register-removal.txt` |

机器门禁：`CI=true just test all` 与 `just lint`（见证据报告「机器门禁」节与 PRD §14「收尾修复」条目）。

结构化证据 manifest：同目录 `evidence.json`（version 1，zh-CN，8 个 item、20 条 stdout 断言，本地复跑不入库）。
