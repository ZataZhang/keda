# Evidence Report — run-daemon-autopilot-control-surface

PRD: `tasks/pending/P1-FEAT-20261005-161633-run-daemon-autopilot-control-surface.md`
交付分支：`feat/run-daemon-control-surface`（基于 main 7d9d9c2）

## 交付范围

- **FR-1/FR-2/FR-7（目标必填 + `--all-ready` 迁移）**：`iar run` 新增位置参数
  PRD 路径与 `--issue` / `--all-ready` / `--takeover` / `--yes`；无目标即
  usage error（exit 2）；PRD 回链解析复用 `- GitHub Issue:` 既有正则（新增
  `core/use_cases/run_target_resolve.py`）；`run_once` 定向收窄
  （`RunOnceRequest.target_issue`，只处理目标 Issue，仍走依赖门禁与 claim）。
- **FR-4（默认互斥）**：dispatch 层按 daemon 单实例锁探测
  （`daemon_single_instance.find_live_daemon_pid`），同仓 daemon 在跑时
  拒绝（exit 5 conflict），提示 stop 命令或 `--takeover`。
- **FR-5（显式接管）**：`--takeover` 强警告（点名 repo、daemon PID、在途
  Issue 数量）+ 交互确认（`--json` 模式必须 `--yes`）；编排
  `engines/agent_runner/run_takeover.py`：停前快照 daemon 后代进程组 →
  托管路径走 `PidfileProcessSupervisor.stop` / 未托管按 PID SIGTERM → 等待
  → 兜底 SIGKILL（僵尸进程经 psutil 判定不误判）→ 孤儿组补扫 →
  `reclaim_stale_running_issues`。daemon 侧新增 SIGTERM 优雅停机钩子
  （`run_agent_daemon.py`：整组终止在途 agent 子进程树后退出，锁由
  dispatcher finally 释放）。
- **FR-8（daemon autopilot 按次覆盖）**：`iar daemon [--autopilot |
  --no-autopilot]`，优先级 flag > repo `.iar.toml` > 全局，锁定本次常驻
  进程；只覆盖调度门控（`backlog_store_factory is not None and
  autopilot_enabled`），**不**触碰 `agent_runner_merge_queue.py`——合并仍由
  `safety.auto_merge` + `autopilot.enabled` 双开关决定。
- **Console 迁移（FR-7）**：`build_runner_argv` RUN_ONCE 带 issue →
  `iar run --issue N`（start_prd「开始此 PRD」路径）；不带 issue → 显式
  `--all-ready`（仓库级 run_once 动作，等价旧行为）。
- **明确不做**（§11 全部遵守）：run 无 `--autopilot` / `--concurrency`；
  不默认接管；SIGKILL 非首手段；不改 HTTP API / 前端 / 数据库结构。

## 自动化层证据（CI=true just test all）

- 全量测试通过：`3012 passed`（含本交付新增
  `tests/test_agent_runner_run_targeting.py` 20 项、
  `tests/test_iar_operator_skill.py` skill↔CLI 漂移守卫更新为
  `("run",)` 新旗标 + `("daemon","run")` / `("backlog","advance")` 白名单）。
- 迁移的旧用例：`tests/test_agent_runner_cli.py` 中 9 处"无目标
  `iar run`"按新契约迁到 `--all-ready`（旧行为等价迁移，rv-5）。

## 真实入口层证据（rv-real-entry.py，9/9 PASS）

原始结果：`rv-real-entry-results.json`；脚本 `rv_real_entry.py` 可复跑
（`uv run python tasks/evidence/run-daemon-autopilot-control-surface/rv_real_entry.py`）。

| rv | 检查 | 结果 | 关键观察值 |
|---|---|---|---|
| rv-5 | 真实 CLI `iar run`（无目标） | PASS | exit 2，`usage_error: iar run requires a target...`，suggestion 含 `--issue`/PRD 路径/`--all-ready` |
| rv-4 | 真实 CLI `iar run --help` | PASS | 有 `--issue`/`--all-ready`/`--takeover`；**无** `--autopilot` |
| rv-8 | `iar schema --json` | PASS | `daemon run` flags 含 `--autopilot`/`--no-autopilot` |
| rv-7 | PRD 两态解析（真实文件） | PASS | 有回链 → `#417`；无回链 → 报错提示 `iar issue create`，不静默捞队列 |
| rv-2 | 真实进程持 daemon 锁 + 真实 CLI run | PASS | exit 5，提示 stop 命令与 `--takeover`；negative control：持锁进程未被拒绝路径破坏（仍存活） |
| rv-6 | 真实接管编排（真实 SIGTERM 信号、真实进程组） | PASS | `final_signal=sigterm`，daemon 优雅退出；negative control：后代 agent 组被清扫，无孤儿进程 |
| rv-1 | 真实 CLI 定向 dry-run（`--issue 42 --json`） | PASS | 机器预览携带 `"target_issue": 42, "all_ready": false`（GitHub 边界按 PRD 允许 mock，ready 扫描失败为预期，预览在失败前发出；rv-1 真实入口为 dry-run 预览，完整定向执行由单测覆盖，呈递时如实标注） |

mock 边界与披露：

- 需要真实 GitHub 的阶段（ready 扫描、claim、relabel）由单测 Fake 客户端
  覆盖（PRD rv-1/rv-2/rv-7 mock_boundary 允许）；CLI 解析、锁探测、
  SIGTERM/进程组清理、接管编排、调度门控均为**真实进程入口**。
- rv-6 的 daemon 以真实 `/bin/sh -c "sleep 120 & wait"`（独立会话/进程组）
  持锁模拟，接管走的是交付的真实 `take_over_daemon` 编排（含托管注册表
  探测、SIGTERM、孤儿组补扫、reclaim 调用）。

## 自动化层补充：rv-8 调度门控三态（单测，fake 单轮 pass）

- `--autopilot` 在配置关时开调度；`--no-autopilot` 在配置开时关调度；缺省跟随配置；合并侧文件未触碰（verifier 已核对 `agent_runner_merge_queue.py` 不在本提交 diff 中）。

## 呈递物缺口披露（verifier 指出，非阻塞）

- PRD §9.1 表格要求 `rv-1-targeted-run.png` / `rv-6-takeover.png` 人读呈递图。
  本交付以真实进程入口的文本证据（`rv-real-entry-results.json`，9/9）+ 单测
  呈递，未生成 PNG 截图（无交互终端捕获渠道；按仓库规则不伪造截图）。
  人工验收前如需 PNG，可在本地复跑 `rv_real_entry.py` 于交互终端截屏补齐。

## Gate Summary

- `CI=true just test all`：PASS（3012 passed, 1 skipped）
- `just lint --full`：PASS（除 check-test-flag 时序门，提交前满足）
- 独立 verifier：见 `verifier-report.md`
