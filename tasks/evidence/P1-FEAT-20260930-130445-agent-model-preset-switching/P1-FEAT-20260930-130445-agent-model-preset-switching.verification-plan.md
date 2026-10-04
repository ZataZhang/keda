# Verification Plan — P1-FEAT-20260930-130445-agent-model-preset-switching

> 交付基线：worktree `keda-worktrees/feat/agent-model-presets`，分支 `feat/agent-model-presets`，重定基后冻结 HEAD `04361e3d`（记录路径排除口径树 `63c7d85a`）。
> 全量门禁：`CI=true just test all` = **2849 passed, 1 skipped**（skip 为既有 alembic 迁移守卫：本仓 `alembic/versions` 为空，环境性跳过，与本 PRD 无关）。重定基后的独立 verifier（REBASE 复核轮）在冻结 HEAD 上独立复跑一致，报告见 `P1-FEAT-20260930-130445-agent-model-preset-switching.verifier-report-rebase.md`。

## rv 对应验证命令

| rv-id | 行为 | 验证方式 | 证据文件 |
|---|---|---|---|
| rv-1 | doctor --preset 打印 argv 含模型与推理档 | 真实 CLI 入口 `iar agent doctor codebuddy --json --preset plan` | `rv-1-agent-doctor-preset.txt` |
| rv-2 | 预设清单可枚举且字段正确 | 真实 CLI 入口 `iar agent presets` | `rv-2-agent-presets.txt` |
| rv-3 | --model/--reasoning-effort 覆盖同名字段 | 单测 `test_resolve_model_selection_cli_overrides_win_per_field` + parser 测试 | `unit-test-evidence.txt` |
| rv-4 | 零回归：未启用时 argv 逐字节一致 | 真实入口 `iar agent doctor claude --json` + `tests/test_agent_invocation_golden.py` 全量黄金快照（34 例，未改动任何期望元组） | `rv-4-agent-doctor-claude.txt` |
| rv-5 | 未声明模板的 agent 命中绑定 fail-fast | 真实入口 `iar agent doctor kimi --json --preset plan`（非零退出、指名 kimi）+ 单测 `test_build_agent_invocation_fails_fast_without_template` | `rv-9-agent-doctor-lifecycle.txt` |
| rv-6 | 无绑定阶段解析与改动前一致（负控） | 黄金快照全绿 + `test_doctor_lifecycle_view_unbound_stage_has_no_model_args` + console API 既有用例全绿 | `unit-test-evidence.txt` |
| rv-7 | 换人丢弃模型绑定 | 单测 `test_drop_model_selection_on_agent_switch`；运行时由 resilient 层同一函数兜底 | `unit-test-evidence.txt` |
| rv-8 | 文档 + skill 同步 | `uv run mkdocs build` 通过；`tests/test_iar_operator_skill.py::test_packaged_skill_command_examples_match_cli_help`（SKILL 命令示例与真实 CLI help 对账）通过 | `unit-test-evidence.txt` |
| rv-9 | 绑定生效 / 解绑回落 | 真实入口 `iar agent doctor --lifecycle verifier`，IAR_CONFIG fixture（绑 plan）vs 仓库真实配置（无绑定）对照 | `rv-9-agent-doctor-lifecycle.txt` |
| rv-10 | PRD 块覆盖仓库层；executor 继承 | 单测 `test_parse_prd_lifecycle_presets_block` 系列、`test_binding_layer_priority_prd_block_beats_layers`、`test_executor_stage_inherits_implementation_selection` | `unit-test-evidence.txt` |
| rv-11 | 视图字段 + 账本 v5→v6 幂等 | 单测 `test_v5_database_migrates_to_v6_and_adds_attempt_preset_columns`、`test_attempt_preset_and_model_roundtrip`、`tests/test_lifecycle_agents_console_api.py` | `unit-test-evidence.txt` |
| rv-12 | 绑定生效时真实跑一轮（opt-in） | **未执行**——需要真实 GitHub Issue + 真 agent 子进程消耗模型额度，属 opt-in 项，呈递人决定是否补采 | —（见证据报告"已知限制"） |

## 架构验收命令

- `rg -n "argv\.append|argv\.extend" src/backend/core src/backend/engines` → 全部命中位于 `agent_invocation.py` 组装器（黄金快照守护其形状），无旁路拼接。
- `rg -n "model_args|reasoning_effort_args" src config.toml` → settings / agent_spec / config.toml / invocation 成对存在。
- `rg -n "build_agent_invocation" src` → 全部调用点已透传 model_selection（或显式 None）。

输出见 `architecture-rg-gates.txt`。

## mock 边界

- CLI 证据（rv-1/2/4/5/9）走真实 Typer 入口 `backend.api.cli_typer_app.main`，配置读取真实 config.toml / IAR_CONFIG fixture；无 agent 子进程被拉起（doctor 是只读 argv 解析，不需要真实模型调用）。
- 单测层 `IProcessRunner` / 内容生成器为测试替身——被替换的边界是进程执行，而非本 PRD 的解析 / 注入 / 丢弃逻辑本身。
