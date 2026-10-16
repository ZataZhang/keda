# Verification Plan — Main CI 夜间巡检 2026-10-15

- PRD: `tasks/pending/P1-FEAT-20261015-215412-nightly-main-ci.md`
- Issue: https://github.com/ZataZhang/keda/issues/288
- 失败入口（真实）：GitHub Actions `CI` workflow，main 分支最新 run
  `38745391997`（2026-10-15T13:45:15Z），job `Validate Template` step
  `Check max file lines (hard limit)` 失败：
  - `src/backend/core/use_cases/agent_runner_failure.py: 1002 非空行`
  - `src/backend/core/use_cases/agent_runner_orchestration_runtime.py: 1001 非空行`
- 最高可行保真度说明：本任务的真实入口是 main 分支 CI 本身；修复后该入口由
  runner 创建 PR 后在 CI 上复跑确认（runner-owned gate，本地无法替代 GitHub 侧
  执行）。本地可复现的最强等价证据 = 以 CI 逐字相同的命令在最终代码树上运行。

## Oracles（均为 reviewer: verifier）

- rv-1 硬门禁复现（与 CI 逐步相同的命令）：
  `uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend --allow-list-file hooks/max_file_lines.allowlist.txt`
  - expected: exit 0，无 `[ERROR]` 行（allowlist 为空名单，不豁免任何文件）。
  - negative_control: 修复前同一命令在 main HEAD 报出上述两个文件并 exit 1（见 CI run 38745391997）。
  - mock_boundary: 无 mock，直接跑仓库 hook。
  - test_layer: real entry（CI step 等价）。
- rv-2 拆分后回归：agent_runner / backlog / verifier / supervisor 相关测试全量定向运行
  `uv run pytest tests/test_agent_runner_*.py tests/test_backlog_*.py tests/test_pr_supervisor.py tests/test_run_agent_deliberation.py tests/test_run_verifier_agent.py -q --no-testmon`
  - expected: 全部通过且用例数 > 0（禁用 testmon 增量跳过）。
  - negative_control: 若 re-export 或 DI seam 漏接，`tests/test_agent_runner_orchestrate.py`、`tests/test_agent_runner_failure.py` 的既有断言会立即失败（拆分错误必然触达公共导入面）。
  - mock_boundary: 无新增 mock；沿用既有 FakeGitHubClient/SubprocessRunner 边界。
  - test_layer: integration + behavior。
- rv-3 公共 API 与 monkeypatch 接缝保持：
  `uv run python -c` 冒烟：从 `agent_runner_failure` 导入全部既有消费名、经
  `agent_runner_orchestrate.refresh_runtime_dependencies()` 后 `agent_runner_orchestration_prd_rework.create_or_reuse_worktree` 可注入。
  - expected: import OK + dep sync OK。
  - negative_control: 未接线的依赖名会在 setattr 时 KeyError / AttributeError。
- rv-4 架构方向检查：`uv run python hooks/shared/check_architecture.py` → 无违规。
- rv-5 文档一致性：`uv run mkdocs build --strict`（docs/ai-standards/time-display.md 指向新模块）。
- rv-6 全量本地门禁：`just lint --repo`（含 ruff / docstring / 复用 + 全量 pytest）。
  - expected: 除 rv-7 外无失败——用于捕获「门禁先失败掩盖的、同一 CI job 后续 pytest 步」的存量问题。
  - negative_control: 实测在 rv-7 修复前，全量套件报 `test_packaged_skill_main_file_keeps_shared_baselines` 失败（主文件 71 > 70），正是 CI 门禁转绿后会立即命中的那条。
  - test_layer: real entry（与 CI `Validate Template` job 的 pytest 步等价命令集）。
- rv-7 随包 operator skill 常驻预算恢复：`grep -cv` 计 SKILL.md 非空行 ≤ 70，并运行读取该文件的 4 个测试模块。
  - expected: 70 行；`test_kedacode_operator_skill` / `test_cli_skill_install` / `test_agent_runner_init` / `test_product_identity` 全绿。
  - negative_control: 修复前守卫断言 `71 <= 70` 失败；若误删被断言 token（退出码表 / `never start` / `kc schema --json` / `requires a target` / `## Routes`），同模块其余用例会失败。
  - mock_boundary: 无 mock，直接读模板文件。
  - test_layer: behavior + content guard。
