# Verifier Report — P1-FEAT-20260930-130445-agent-model-preset-switching

VERDICT: PASS

- 验证人：独立 verifier（只读复核 + 只读测试重跑；未改动任何源码 / 测试 / 证据 / PRD）
- 复核对象：worktree `keda-worktrees/feat/agent-model-presets`，分支 `feat/agent-model-presets`
- 复核日期：2026-10-03

## Frozen-State Check

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| HEAD | `22a0914e9366045997f725f7683c06d76034e3db` | `22a0914e9366045997f725f7683c06d76034e3db` | ✅ |
| `git diff HEAD -- src tests \| shasum -a 256` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空输入） | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | ✅ |
| working tree | clean | clean（`git status --porcelain` 无输出） | ✅ |

## Findings

| id | severity | finding | evidence | required action |
|---|---|---|---|---|
| V-01 | LOW | `console_store.py` 模块 docstring 仍写"当前版本 5：v5 新增 prd_lifecycle_runs…"，与 `_SCHEMA_VERSION = 6` 不一致（陈旧注释，代码行为正确） | `src/backend/infrastructure/persistence/console_store.py:8-9` vs `:121` | 下次触碰该文件时顺手更正 docstring；不阻塞 |
| V-02 | LOW | rv-1 的 PRD `negative_control`（置空 model_args 重跑）与 `fresh_state_probe`（独立新进程重跑两次）未按协议单独留痕。实质覆盖已由 rv-5 真实入口 fail-fast 捕获、`test_build_agent_invocation_fails_fast_without_template` 单测与本次 verifier 在冻结树上重跑补齐 | `rv-1-agent-doctor-preset.txt` 仅含单次正向运行；`verification-plan.md` 未列负控命令 | 后续 PRD 证据采集时对声明了 negative_control/fresh_state_probe 的 rv 条目按协议留痕；不阻塞 |
| V-03 | LOW | `unit-test-evidence.txt` 全量门禁 banner 绑定 `d5ec78ca`（证据采集时为 d5ec78ca + 未提交交付改动），冻结 HEAD 为其后继提交 `22a0914e`。二者树内容一致（`22a0914e` 即该批改动的提交化，`git diff d5ec78ca 22a0914e --stat` 恰为交付范围），但严格说全量门禁不是在冻结提交上跑的 | `unit-test-evidence.txt:7-8`；`git log`：d5ec78ca 为 22a0914e 直接父提交 | 已由 verifier 在冻结 HEAD 上独立重跑全量 Python 套件闭环（见下方"Independent Re-run"），无残留风险 |
| V-04 | LOW | rv-2 捕获存在终端宽度换行伪影（`model: ` 与值被拆到两行），不影响内容判读，但复制引用时易误读 | `rv-2-agent-presets.txt:3-6` | 后续捕获加 `COLUMNS` 固定或关闭 rich 换行；不阻塞 |
| V-05 | LOW | evidence-report 称 `argv.append|argv.extend` "全部命中位于 agent_invocation.py"，漏提 `architecture-rg-gates.txt` 自己列出的 `container_ops.py:174/191` 两处命中。经查 `container_ops.py` 相对 main 零改动（容器日志 `--follow` 旗标，既有代码），架构主张本身成立，仅措辞不严谨 | `architecture-rg-gates.txt:12-13`；`git diff main...HEAD -- src/backend/engines/agent_runner/container_ops.py` 为空 | 措辞修正即可；不阻塞 |

无 HIGH / MEDIUM 发现。

## Per-rv Judgment

| rv-id | 判定 | 依据（原始证据 + 代码交叉核对） |
|---|---|---|
| rv-1 | PASS | `rv-1-agent-doctor-preset.txt`（`# date: 2026-10-03T14:56:17Z`，真实 Typer 入口）：argv 中 `"--model"` 后邻位为 `"glm-5.3-flash"`、`"--settings"` 后邻位为 `"{\"reasoningEffort\":\"max\"}"`（JSON 转义与实际 argv 元素 `{"reasoningEffort":"max"}` 一致）；注入位置在 profile args 之后、`golden-prompt` 之前，与 `agent_invocation.py:312-321`（args 循环后、expand/tail_args 前）吻合；条目携带 `preset`/`model`/`reasoning_effort` 观测字段。config.toml:168-171（plan = codebuddy/glm-5.3-flash/max）与输出一致。负控留痕缺口见 V-02 |
| rv-2 | PASS | `rv-2-agent-presets.txt`：plan（codebuddy · glm-5.3-flash · max）与 work（codebuddy · deepseek-v4.1-flash · high），与 `config.toml:168-176` 逐字段一致（换行伪影见 V-04） |
| rv-3 | PASS（经单测） | `test_resolve_model_selection_cli_overrides_win_per_field`、`test_parser_accepts_runner_model_preset_flags` 存在且通过；代码 `agent_model_preset.py:86-89` 逐字段取 override 优先，与声称语义一致 |
| rv-4 | PASS | `rv-4-agent-doctor-claude.txt`：claude argv 无任何 model/effort 参数，形状与黄金快照同源；`tests/test_agent_invocation_golden.py` 相对 main 分支零 diff（整分支），34 个用例在冻结树 collect 34、全部通过——"未改动任何期望元组"属实 |
| rv-5 | PASS | `rv-9-agent-doctor-lifecycle.txt` 末段：`kimi --preset plan` 退出码 1，报错文本与 `agent_invocation.py:236-242` 的 `ModelNotSupportedError` 消息逐字吻合（含修复指引），无静默忽略路径（代码在模板为空时必抛） |
| rv-6 | PASS（负控） | 黄金快照 34 例全绿；`test_doctor_lifecycle_view_unbound_stage_has_no_model_args` 通过；rv-9 下半（真实 config 无绑定）回落 `qoder`（`config.toml:1019 verifier = "qoder"`，bin=`qodercn` 与捕获 argv 一致）且 argv 无模型参数 |
| rv-7 | PASS | `drop_model_selection_for_agent`（`run_agent_once.py:705-729`）：agent 不一致时 WARN 日志 + 返回 None；resilient 层入口统一施加（`run_agent_once.py:778-783`，原地瞬态重试在检查之后、同 agent 不换人）；`test_drop_model_selection_on_agent_switch` 通过 |
| rv-8 | PASS | `docs/guides/model-presets.md` 存在且入 `mkdocs.yml:67` 导航；`lifecycle-agent-matrix.md:212-214` 有绑定层小节；iar-operator SKILL.md:17-18 含 `iar agent presets` 与 doctor `--preset/--lifecycle`；守卫测试 `test_packaged_skill_command_examples_match_cli_help` 通过 |
| rv-9 | PASS | 绑定态（IAR_CONFIG fixture `verifier=plan`）：`agent=codebuddy` + argv 含 `--model glm-5.3-flash --settings '{"reasoningEffort":"max"}'` + `preset: plan`，exit 0；解绑态（真实 config）：`agent=qoder`、argv 无模型参数，exit 0。对照方向与 PRD 期望一致；fixture 文件本体不在证据包（头注仅记路径），属轻微留痕弱点，输出自证 |
| rv-10 | PASS | `test_parse_prd_lifecycle_presets_block` 系列、`test_resolve_lifecycle_model_selection_reads_issue_prd_block`、`test_binding_layer_priority_prd_block_beats_layers`、`test_executor_stage_inherits_implementation_selection` 全部存在且通过；代码：presets 块键闭集排除 planner（`lifecycle_agent.py:59-61` + `_validate_prd_override_entry`，`lifecycle_agent_resolution.py:287-296`）；executor 继承为 `or` 表达式（`run_agent_execution_loop.py:309-317` closeout、`:743-751` fix）；AST 守卫 `test_fix_and_closeout_call_sites_resolve_through_lifecycle_function`（`test_lifecycle_agent_routing.py:420-462`）断言两处调用点首参是 inline `resolve_lifecycle_agent(...)` |
| rv-11 | PASS | `_SCHEMA_VERSION = 6`（`console_store.py:121`）；迁移用 `PRAGMA table_info(attempt_records)` 探测补列（`:299-307`，幂等）；`AttemptRecord`/INSERT/SELECT 均携带 preset/model（`:64-65`、`:343-359`、`:431-451`）；core 侧 `runner_console.py:88-95` 同构含 v6 字段；`test_v5_database_migrates_to_v6_and_adds_attempt_preset_columns`、`test_attempt_preset_and_model_roundtrip`、console API 14 例全部通过；console 视图 `preset/model/reasoning_effort` 为纯追加键（`lifecycle_agents_console.py:124-129`），未绑定时为 None（向后兼容） |
| rv-12 | NOT CAPTURED（如实披露） | PRD 标注 `required_for_acceptance: false`（opt-in，消耗模型额度）；evidence-report「已知限制」第 1 条显式声明未执行；PRD §14 Change Log 记录 decision-board Q6=skip（以 `.iar/decisions/answers.json` 为准）。无隐瞒。账本写入能力已由 rv-11 单测证明 |

## Adversarial Sweep（跨 CLI 注入面 / 旁路）

- `build_agent_invocation` 全部 4 个调用点（`run_agent_once.py:673`、`transcript_runner.py:70`、`content_generators.py:81/157`、`cli_parsed_commands/agent.py:324`）均透传 `model_selection`，无旁路拼接；`container_ops.py` 两处 argv.extend 为既有容器日志旗标（相对 main 零改动）。
- 换人丢弃的守护覆盖：resilient 层统一入口（`run_agent_once.py:778-783`）、transcript_runner 逐次调用（`transcript_runner.py:69`）、supervisor 对 supervisor 与 repair 双重显式检查（`agent_runner_supervisor.py:97-103`）；direct `run_agent_with_prompt` 的 3 处带 `model_selection` 调用（`pr_supervisor_repair.py:86`、`pr_supervisor.py:702/878`）执行 agent 均为上游已 drop-check 过的同一 agent；内容生成路径以 `agent_name = model_selection.agent` 反向构造（`generated_content.py:595-601`），结构上不可能跨 CLI。未发现未守护路径。
- `--model` / `--reasoning-effort` 缺 `--preset` 显式报错：`cli_model_preset_anchor.py:58-63`（ValueError）与 `cli_parsed_commands/agent.py:480`（doctor），evidence-report「已知限制」第 3 条属实。
- planner 排除：`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS` 显式剔除 planner（`lifecycle_agent.py:59-61`），presets 块校验使用该闭集。
- 零依赖：`pyproject.toml` / `uv.lock` 相对 main 零 diff；`No frontend impact` 属实（改动树未触及 `frontend-admin/` / `frontend-public/`）。
- 配置链：`agent_runner_settings.py:176-177` 声明模板字段 → `factory_config_builder.py:340-348` merge → `AppConfig.agent_presets` / `lifecycle_presets`（`agent_runner.py:838-842`）→ 解析单点消费，与 §9.2 Architecture Acceptance 一致。

## Independent Re-run（verifier 在冻结 HEAD 22a0914e 上只读执行）

| 命令 | 结果 |
|---|---|
| `CI=true uv run pytest tests/test_agent_model_presets.py tests/test_console_store.py -o addopts="" -q` | 41 passed |
| `CI=true uv run pytest tests/test_agent_invocation_golden.py tests/test_lifecycle_agent_routing.py tests/test_agent_doctor_cli.py -o addopts="" -q` | 76 passed |
| `CI=true uv run pytest tests/test_lifecycle_agents_console_api.py -o addopts="" -q` | 14 passed |
| `CI=true uv run pytest tests -o addopts="" -q --ignore=tests/playwright-e2e -p no:randomly` | **2697 passed, 1 skipped**（16m36s；唯一 skip 为既有 alembic 迁移守卫，与本 PRD 无关——与证据报告声称完全一致） |
| 声称的 17 个关键测试函数存在性 | 17/17 命中（`tests/test_agent_model_presets.py`、`tests/test_console_store.py`、`tests/test_agent_doctor_cli.py`、`tests/test_iar_operator_skill.py`） |

## Verdict Rationale

冻结状态核验通过；12 条 rv 中 11 条证据在声称的保真层（真实 CLI 入口 / 单测）上成立且与源码交叉一致，rv-12 按 opt-in 决策如实披露不采；PRD §9.2 抽验的 12 项勾选均有底层证据支撑；对抗性排查（旁路拼接、跨 CLI 注入、planner 排除、console 向后兼容、依赖零新增）未发现实质缺陷。5 个 LOW 发现均不阻塞验收，其中 V-03 已由本次冻结树全量重跑闭环。
