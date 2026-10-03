# Evidence Report — P1-FEAT-20260930-130445-agent-model-preset-switching

> 采集环境：worktree `keda-worktrees/feat/agent-model-presets`（分支 `feat/agent-model-presets`）。
> 全量门禁：`CI=true just test all` → **2697 passed, 1 skipped**（唯一 skip 为既有 alembic 迁移守卫，本仓无迁移文件，与本 PRD 无关）。lint 全量 pre-commit 全绿；`check_max_file_lines`（1000 行硬上限）通过。

## 按验收条目的证据

### 决策一 / Behavior：注入在 core 单一出口、零回归（rv-1、rv-4）

- `rv-1-agent-doctor-preset.txt`：真实 CLI 入口 `iar agent doctor codebuddy --json --preset plan`。argv 数组中 `--model` 后一项为 `glm-5.3-flash`，`--settings` 后一项为 `{"reasoningEffort":"max"}`，注入位置在 profile args 之后、提示词之前；条目额外携带 `preset` / `model` / `reasoning_effort` 三个观测字段。
- `rv-4-agent-doctor-claude.txt`：同一入口未传 `--preset` 时 argv 与改造前逐字节一致（无任何模型参数）。
- `tests/test_agent_invocation_golden.py` 34 条黄金快照**未改动任何期望值**且全部通过——零回归为硬承诺的直接证据。
- `architecture-rg-gates.txt` §1：`argv.append|argv.extend` 在 core/engines 的全部命中位于 `agent_invocation.py`（含既有 tail/prompt 组装），无旁路拼接。

### 决策二 / Behavior：绑定整体决定、遮蔽矩阵、显式 --agent 最高（rv-6、rv-9）

- `rv-9-agent-doctor-lifecycle.txt` 上半：IAR_CONFIG fixture（`lifecycle_presets.verifier = "plan"`）下 `iar agent doctor --lifecycle verifier` 打印 `agent=codebuddy` 且 argv 含模型与推理档参数（绑定遮蔽了矩阵的 verifier=qoder 声明）。
- 同文件下半（负控）：真实配置无绑定时回落矩阵既有值 `qoder`，argv 无任何模型参数。
- 单测 `test_resolve_lifecycle_agent_binding_shadows_matrix`（绑定遮蔽矩阵）、`test_resolve_lifecycle_agent_explicit_agent_beats_binding`（显式 --agent 最高）。

### 决策三 / Behavior：换人丢弃、executor 继承不算换人（rv-7、rv-10）

- 单测 `test_drop_model_selection_on_agent_switch`：预设 agent 与执行 agent 不一致返回 None（丢弃并记日志路径），一致时原样透传；executor 继承经 `test_executor_stage_inherits_implementation_selection`（同 agent 不丢）覆盖。
- 运行时同一判定收敛在 `run_agent_with_prompt_resilient` 的 `drop_model_selection_for_agent`，跨 agent 回退与显式换人都经过该函数。

### 决策四 / Behavior：模板缺失 fail-fast（rv-5）

- `rv-9-agent-doctor-lifecycle.txt` 末段：`iar agent doctor kimi --json --preset plan` 非零退出，报错指名 `agent 'kimi' has no model_args template` 并给出修复指引；无静默忽略路径。
- 单测 `test_build_agent_invocation_fails_fast_without_template`、`test_build_agent_invocation_effort_only_binding_requires_effort_template`。

### Behavior：PRD 块 / 覆盖字段 / 清单枚举 / 观测（rv-2、rv-3、rv-10、rv-11）

- rv-2：`rv-2-agent-presets.txt` 列出 `plan`（codebuddy · glm-5.3-flash · max）与 `work`（codebuddy · deepseek-v4.1-flash · high）。
- rv-3：`test_resolve_model_selection_cli_overrides_win_per_field`（覆盖后 model 变、effort 取预设值）+ parser/typer 双层旗标解析测试。
- rv-10：`test_parse_prd_lifecycle_presets_block`（含 planner 拒收、大小写保留）、`test_upsert_prd_lifecycle_presets_block_writes_and_keeps_body`、`test_resolve_lifecycle_model_selection_reads_issue_prd_block`（PRD 块 > 仓库层）。
- rv-11：`test_v5_database_migrates_to_v6_and_adds_attempt_preset_columns`（v5 旧库自动补列、旧数据两列为 NULL）、`test_attempt_preset_and_model_roundtrip`（绑定写入 / 未绑定 NULL）、`tests/test_lifecycle_agents_console_api.py` 全绿（视图行新增 `preset` / `model` / `reasoning_effort` 字段为纯追加）。

### Frontend Acceptance

`No frontend impact`：本 PRD 是 CLI/配置层特性；console 只读视图在**后端 API 返回 dict 中追加** `preset` / `model` / `reasoning_effort` 字段（向后兼容，前端不渲染新字段不受影响），前端矩阵页的绑定列渲染属 §12 已记录的后续跟进项。本 PRD 改动树不触及 `frontend-admin/` / `frontend-public/`。

### Documentation Acceptance（rv-8）

- 新增 `docs/guides/model-presets.md` 并加入 `mkdocs.yml` 导航（使用指南 → Agent 模型预设）。
- `docs/guides/lifecycle-agent-matrix.md`（相关指针 + 绑定遮蔽语义）、`docs/guides/configuration.md`（模型模板与预设段说明）、`docs/guides/agent-runner.md`（预设绑定与换人丢弃指引）已与最终设计一致。
- `uv run mkdocs build` 通过（仅既有的两条 INFO 级锚点提示，与本次改动无关）。
- `iar-operator` SKILL.md 命令表新增只读两条（`iar agent presets`、`iar agent doctor --preset/--lifecycle`）；守卫测试 `test_packaged_skill_command_examples_match_cli_help` 通过——SKILL 中每个命令示例的 flag 与真实 `--help` 逐一对账。

## 已知限制

1. **rv-12（opt-in）未执行**：绑定生效时真实跑一轮 runner 需要真实 GitHub Issue、真实 agent 子进程与模型额度；呈递人决定是否补采（账本列本身已由单测证明可写入）。
2. 模型 id 拼写错误只能由各 CLI 在运行时暴露（keda 只负责把 id 按声明式模板放进 argv）；文档已提示"模型名以各 CLI 支持列表为准"。
3. 一次性旗标 `--model` / `--reasoning-effort` 必须与 `--preset` 同用（它们是预设同名字段的覆盖，不是独立开关）；handler 层对缺 `--preset` 的组合显式报错。
