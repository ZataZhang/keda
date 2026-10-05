# Verification Plan — run-daemon-autopilot-control-surface

PRD: `tasks/pending/P1-FEAT-20261005-161633-run-daemon-autopilot-control-surface.md`
（归档后为 `tasks/archive/P1-FEAT-20261005-161633-run-daemon-autopilot-control-surface.md`）

对应 PRD §7.6 Realistic Validation Plan 的 rv-1..rv-8。本计划描述每项 oracle
的验证入口、命令与期望观察值；证据见同目录 `evidence-report.md`，独立
verifier 结论见 `verifier-report.md`。

## 自动化层（tests/）

| oracle | 测试 |
|---|---|
| rv-4 | `test_agent_runner_run_targeting.py::test_typer_run_help_has_no_autopilot`（--help 快照：有目标旗标无 --autopilot）；`iar roadmap advance` 为既有入口未改动 |
| rv-5 | `test_main_run_without_target_is_usage_error`（无目标 exit 2）；`--all-ready` 与旧行为等价由既有 `test_main_run_passes_all_repositories_selector` 等迁移后用例 + dispatch 单路径保证 |
| rv-7 | `test_resolve_prd_target_issue_number_*`（回链两态）+ `test_main_run_rejects_issue_and_prd_path_together` |
| rv-2 | `test_main_run_with_daemon_running_rejects_by_default`（默认拒绝 exit 5）；negative control：无 daemon 时正常执行（既有 run 用例） |
| rv-6 | `test_main_run_takeover_yes_stops_daemon_and_runs` + `test_main_run_takeover_declined_leaves_daemon_alone`（negative control：拒绝确认不停 daemon） |
| rv-8 | `test_daemon_autopilot_flag_enables_scheduling_when_config_disabled` / `test_daemon_no_autopilot_flag_disables_scheduling_when_config_enabled` / `test_daemon_without_flag_follows_config_each_pass`；不 arm 合并由调度门控只落在 `run_agent_daemon`（`agent_runner_merge_queue.py` 未触碰）保证 |
| rv-1 | `test_run_once_targeted_requires_ready_label` + 定向收窄单测（只处理目标 Issue） |

## 真实入口层（本目录脚本产物）

- rv-1 / rv-5：真实 `iar` CLI（`uv run iar run` / `iar run --help` / 定向 dry-run 预览）
- rv-2 / rv-6：真实进程持锁 + 真实接管编排（`scripts rv2/rv6`，见 evidence-report）
- rv-7：真实 PRD 文件两态解析（有回链 / 无回链）

GitHub 边界按 PRD mock_boundary 允许 mock；run/daemon 进程入口真实。
