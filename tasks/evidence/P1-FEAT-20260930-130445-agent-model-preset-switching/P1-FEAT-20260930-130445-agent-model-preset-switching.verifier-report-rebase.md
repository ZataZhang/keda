VERDICT: PASS

# 独立 Verifier 报告（Rebase 复核，fresh eyes）

- 验证人：独立 verifier（只读复核 + 只读测试重跑；除本报告外未改动任何源码 / 测试 / 证据 / PRD）
- 复核对象：worktree `keda-worktrees/feat/agent-model-presets`，分支 `feat/agent-model-presets`
- 冻结 HEAD：`04361e3d992f1a674d2aa8d8256ad9f87aa518cf`（= 重定基到 `6fd39c63`「#182 token 账本」之后的提交链）
- 复核日期：2026-10-04
- 本轮定位：分支刚从 `6fd39c63` 重定基，六文件有真实冲突经人/agent 手工解决。本轮专门独立核验**冲突解决在语义上是否正确**（两套特性共存、无丢分支、无双注入），而非只看语法。

## Frozen-State Check

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| HEAD | `04361e3d992f1a674d2aa8d8256ad9f87aa518cf` | `04361e3d992f1a674d2aa8d8256ad9f87aa518cf` | ✅ |
| `git diff HEAD -- src tests \| shasum -a 256`（开始时） | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空输入） | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | ✅ |
| `git diff HEAD -- src tests \| shasum -a 256`（结束时） | 同上 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | ✅ |
| working tree | clean | clean（`git status --porcelain` 无输出；`site/` 为 gitignore，构建后已清） | ✅ |
| 冲突标记残留 | 无 | `rg "^<<<<<<<\|^=======$\|^>>>>>>>" src tests` 无命中 | ✅ |

## Findings

| id | severity | finding | evidence | required action |
|---|---|---|---|---|
| R-01 | MEDIUM | PRD §7.6 中 rv-6 / rv-7 / rv-11 的 `real_entry` 指向不存在或选不中用例的测试路径，按字面执行拿不到声称的 oracle：（a）rv-6 `tests/test_lifecycle_agent_resolution.py -q -k preset` → `26 deselected`（0 用例运行），预设断言测试实际在 `tests/test_agent_model_presets.py`；（b）rv-7 `tests/test_agent_invocation.py` 文件不存在（实际 `tests/test_agent_runner_agent_invocation.py`，drop 断言在 `tests/test_agent_model_presets.py`）；（c）rv-11 `tests/test_lifecycle_agents_console.py` 文件不存在（实际 `tests/test_lifecycle_agents_console_api.py`），命令因缺文件整体 `ERROR/no tests ran`。**行为本身全部由改名后的真实测试验证通过**（见 Per-rv）。非本次重定基引入——`6fd39c63` 上同样只有 `test_agent_invocation_golden.py` / `test_lifecycle_agents_console_api.py`，系 PRD 成稿期遗留路径漂移；前两轮 verifier 在自己的重跑里用了正确文件名但未回写 PRD | `tasks/archive/...md:478/488/536`；实跑见下方 Per-rv；`git ls-tree 6fd39c63 tests` 佐证非重定基引入 | 归档 PRD 与 `verification-plan.md` 的 rv-6/7/11 `real_entry` 更正为真实文件路径；不阻塞（行为已独立验证），但字面可复现性受损 |
| R-02 | LOW | `_attempt_delivery_closeout` 与 fix 的 `_emit_agent_usage` 重新解析 `resolve_lifecycle_agent("closeout"/"fix", ...)` 时只透传 `prd_overrides`、**未透传 `prd_preset_overrides`**，而实际执行调用（`:348` / `:822`）透传了。当 closeout/fix 的 agent 仅由「显式传入的 preset 覆盖」（既非 Issue 携带、也非 config 层绑定）切换时，用量归因的 agent 名可能与实际执行 agent 不一致。纯观测性（不影响 argv / 执行 agent），且与 main 上「emit 不透传新参数」的既有形态同型 | `run_agent_execution_loop.py:348-354` vs `:381-387`；`:822-829` vs `:852-858`；main `6fd39c63` 同函数 emit 段 | 后续把 `prd_preset_overrides` 一并透传给两处 emit 的 `resolve_lifecycle_agent`；不阻塞 |
| R-03 | LOW | rv-1 在归档 PRD 中声明的 `negative_control`（置空 model_args 重跑）与 `fresh_state_probe`（独立新进程重跑两次）未单独留痕，证据文件仅含单次正向运行。实质覆盖已由 rv-5 真实入口 fail-fast 与单测补齐 | `rv-1-agent-doctor-preset.txt`；本轮 verifier 已在冻结树复跑正向 | 沿袭 round1 V-02，as-noted；不阻塞 |

无 HIGH 发现。R-01 为可复现性文档缺陷，非代码 / 重定基缺陷，不改判本交付。

## Rebase 冲突解决专项核验（本轮核心）

六文件逐项核验，两套特性（#182 token 账本 / 本 PRD 预设）语义共存、无丢分支：

| 文件 | 核验点 | 结论 |
|---|---|---|
| `core/shared/models/agent_runner.py` | `AttemptResult` 同时含 `token_usage` 与 `preset`/`model`；`AppConfig` 含 `agent_presets`/`lifecycle_presets`；`IssueSummary.lifecycle_preset_overrides` | ✅ 两者并存 |
| `core/use_cases/agent_runner_attempt.py` | `_make_attempt_result` 形参同时含 `token_usage` 与 `preset`/`model`，构造两处都写 | ✅ |
| `core/use_cases/agent_runner_issue_handlers.py` | `_process_blocked_resolution` / `_process_ready_issue` 两个调用点均同时传 `on_agent_usage=` 与 `model_selection=`；model_selection 先过 `drop_model_selection_for_agent` | ✅ |
| `core/use_cases/run_agent_execution_loop.py` | `AgentExecutionRequest` 同时含 `on_agent_usage`/`model_selection`；`_AttemptRecordContext` 同时含 `token_usage`/`effective_model_selection`；`_record_attempt` 写 `token_usage` + `preset`/`model`；`_attempt_delivery_closeout` 返回 `_CloseoutAttemptResult(revalidated, discarded_detail)`（#182 形态）且接受 `prd_preset_overrides`；证据/fix 收尾路径保留 `revalidated`/usage 发射 | ✅ |
| `core/use_cases/run_agent_once.py` | `run_agent_until_committed` 同时传 `on_agent_usage`/`model_selection`；`drop_model_selection_for_agent` 为换人丢弃的单一 choke point（resilient 层入口统一施加） | ✅ |
| `core/use_cases/run_verifier_agent.py` | main 的候选回退循环（快照 + `try/finally` 恢复 + 超时 re-raise）完整保留；每个候选 `model_selection=drop_model_selection_for_agent(candidate_agent, verifier_model_selection)` | ✅ 见下细分 |
| `infrastructure/persistence/console_store.py` | `_SCHEMA_VERSION = 6`，`PRAGMA table_info(attempt_records)` 探测补 `preset`/`model`（幂等），INSERT/SELECT 均携带；与 #182 无列冲突（#182 未改动 `attempt_records` 列） | ✅ |

verifier 候选回退四点细验：
- (a) 候选 == 配置的 verifier agent（`_verifier_candidate_agents` 首选恒在首位）→ `verifier_model_selection` 已按 `verifier_agent` 校验过，agent 匹配即保留绑定；`test_verifier_candidate_agents_excludes_builder_and_respects_the_switch_cap` 通过。 ✅
- (b) 回退候选 ≠ verifier agent → `drop_model_selection_for_agent` 返回 None 并记 WARN "model binding dropped on agent switch"。 ✅
- (c) `finally: restore_evidence_snapshot(...)` 逐候选快照 + 恢复语义未动；`test_run_verifier_gate_restores_evidence_the_verifier_overwrote` / `..._when_verifier_times_out` / `..._keeps_files_the_verifier_created` 全绿。 ✅
- (d) 无括号错配 / 悬空逻辑——`run_verifier_agent.py:669-717` 结构完整，全量套件与定向套件均通过。 ✅

无双注入：`config.toml` 内 agent profile 的 `args` 不含 `--model`/推理档（模板只出现在 `model_args`/`reasoning_effort_args`），`build_agent_invocation` 只在 `model_selection is not None` 时注入一次。

## Executor Drift Guard rg-gates（冻结树原始结果）

| Check | 命令 | 结果 |
|---|---|---|
| Legacy argv 旁路 | `rg -n "argv\.append\|argv\.extend" src/backend/core src/backend/engines` | 仅 `agent_invocation.py:311/313/339/341/344/351`（组装器本体）与 `container_ops.py:174/191`（容器日志旗标，既有，与 main 零 diff）；无模型参数旁路 |
| 新字段引用 | `rg -n "model_args\|reasoning_effort_args" src config.toml` | settings(`agent_runner_settings.py:176-177`) / agent_spec(`:136-137,264,420-421`) / invocation(`:236-261`) / factory_config_builder(`:340-348`) / config.toml(`:261,370-371`) 成对存在 |
| 预设与绑定引用 | `rg -n "presets\|ModelSelection\|resolve_lifecycle_model_selection\|lifecycle_presets" src` | 174 命中；解析收敛 `agent_model_preset.py` + `lifecycle_agent_resolution.py`，未见复制到 caller |
| 隐藏入口 | `rg -n "build_agent_invocation" src` | 4 个真实调用点（`run_agent_once.py:674`、`content_generators.py:81/157`、`transcript_runner.py:70`、`cli_parsed_commands/agent.py:324`）全部透传 `model_selection=`（已逐点读验） |
| 九阶段消费点 | `rg -n "resolve_lifecycle_agent\|resolve_lifecycle_model_selection" src` | 27 命中；model_selection 消费覆盖 implementation(issue_handlers ×2) / fix+closeout(execution_loop) / verifier / review(agent_review) / supervisor / deliberate / content_generation(create_prd_from_issue, labels_issue, publish) / planner(agent.py)；console 视图同步 |
| skill 同步 | `rg -n "preset" src/backend/engines/agent_runner/templates/skills/` | `iar-operator/SKILL.md:17-18` 含 `iar agent presets` 与 doctor `--preset/--lifecycle`；守卫 `test_packaged_skill_command_examples_match_cli_help` 通过 |
| 文档同步 | `rg -n "lifecycle_presets\|preset" docs mkdocs.yml` | 27 命中；`docs/guides/model-presets.md` 存在且入 `mkdocs.yml` 导航；`uv run mkdocs build --strict` 退出码 0 |

## Per-rv Judgment

| rv-id | 判定 | 依据（本轮在冻结 HEAD 上的独立执行 / 代码交叉核对） |
|---|---|---|
| rv-1 | PASS | `uv run iar agent doctor codebuddy --json --preset plan`：argv 中 `"--model"`→`"glm-5.3-flash"`、`"--settings"`→`{"reasoningEffort":"max"}`，条目含 `preset/model/reasoning_effort`；注入位置在 profile args 后、`golden-prompt` 前（`agent_invocation.py:310-321`）。负控留痕缺口见 R-03 |
| rv-2 | PASS | `uv run iar agent presets`：`plan`→codebuddy/glm-5.3-flash/max、`work`→codebuddy/deepseek-v4.1-flash/high，与 config.toml 逐字段一致 |
| rv-3 | PASS | 真实入口：`--preset plan --model custom-model --reasoning-effort low` → argv/字段均取覆盖值；仅 `--model only-model` → model 覆盖、effort 仍取预设 `max` |
| rv-4 | PASS | `iar agent doctor claude --json` argv 无任何 model/effort 参数；`tests/test_agent_invocation_golden.py` 相对 `6fd39c63` **零 diff**（两 dot/三 dot 均 0 行），34 用例全绿 |
| rv-5 | PASS | `iar agent doctor kimi --json --preset plan` 退出码 1，报错指名 `agent 'kimi' has no model_args template ...`（`agent_invocation.py:236-242`），无静默忽略 |
| rv-6 | PASS（行为）；命令路径见 R-01 | PRD 字面命令选 0 用例（`-k preset` 26 deselected）。行为经 `tests/test_agent_model_presets.py -k 'binding or preset or executor'`（21 passed）验证：绑定遮蔽矩阵、显式 --agent 最高、executor 继承；未绑定阶段零变化由黄金快照 + `test_lifecycle_agent_resolution.py -k 'prd_override or executor'`（14 passed）负控 |
| rv-7 | PASS（行为）；命令路径见 R-01 | drop 断言 `test_drop_model_selection_on_agent_switch` 通过（`tests/test_agent_model_presets.py`）；`drop_model_selection_for_agent`（`run_agent_once.py`）跨 agent 返回 None + WARN；resilient 层统一入口施加 |
| rv-8 | PASS | `mkdocs build --strict` 退出码 0；`model-presets.md` + 导航存在；SKILL 命令守卫通过 |
| rv-9 | PASS | 真实入口 + IAR_CONFIG 绑定 fixture：绑定 `verifier=plan` → `agent=codebuddy` 且 argv 含模型/推理档、`preset=plan`（exit 0）；解绑（真实 config）→ `agent=qoder`、argv 无模型参数（exit 0）。对照方向符合期望 |
| rv-10 | PASS | `tests/test_lifecycle_agent_resolution.py -k 'prd_override or executor'`：14 passed；含 PRD 块 > 仓库层、executor 继承实现者、planner 拒收（`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS` 剔除 planner） |
| rv-11 | PASS（行为）；命令路径见 R-01 | 迁移/往返 `tests/test_console_store.py`（20）+ console API `tests/test_lifecycle_agents_console_api.py`（14）合计 34 passed；v5 库自动补列、旧记录 NULL、绑定写入 preset/model |
| rv-12 | NOT CAPTURED（如实披露） | PRD 标 `required_for_acceptance: false`（opt-in 消耗额度）；`evidence-report.md` 已知限制第 1 条、PRD §12 与 §14 Change Log（decision-board Q6=skip）均显式声明不采。账本写入能力由 rv-11 单测证明。无隐瞒，不因 rv-12 单独判负 |

## Independent Re-run（verifier 在冻结 HEAD `04361e3d` 上只读执行）

| 命令 | 结果 |
|---|---|
| `CI=true just test all`（worktree 内） | **2849 passed, 1 skipped**（151.10s；唯一 skip 为既有 alembic 迁移守卫，本仓无迁移文件，与 PRD 无关）——与 PRD §14 Change Log 声称完全一致 |
| `uv run pytest -o addopts="" tests/test_agent_invocation_golden.py tests/test_lifecycle_agent_routing.py tests/test_agent_doctor_cli.py tests/test_iar_operator_skill.py -q` | 83 passed |
| `uv run pytest -o addopts="" tests/test_agent_model_presets.py -q -k 'binding or preset or executor'` | 21 passed |
| `uv run pytest -o addopts="" tests/test_console_store.py tests/test_lifecycle_agents_console_api.py -q` | 34 passed |
| `uv run pytest -o addopts="" tests/test_agent_token_stats.py tests/test_agent_token_usage_flow.py tests/test_agent_stream_usage.py -q`（#182 回归） | 27 passed |
| `uv run pytest -o addopts="" tests/test_lifecycle_agent_resolution.py -q -k 'prd_override or executor'` | 14 passed |
| `uv run mkdocs build --strict` | 退出码 0 |

## Verdict Rationale

冻结校验通过（HEAD、起止空 diff 凭证、working tree 全一致）；六文件重定基冲突解决经逐项语义核验，两套特性（#182 token 账本 / 本 PRD 预设）完整共存，无丢分支、无双注入、无悬空/括号错配；verifier 候选回退四点语义（首选保留绑定、回退丢弃并记日志、finally 证据恢复、结构完整）均成立；全量套件 2849 passed / 1 skipped、#182 token 回归 27 passed、mkdocs strict 通过。三条 required rv（6/7/11）的行为在冻结树上以改名后的真实测试独立验证通过；其 PRD 字面 `real_entry` 路径漂移记为 MEDIUM 可复现性缺陷（R-01，非重定基引入、非代码缺陷），不改变行为判定。rv-12 按 opt-in 决策如实披露不采。无 HIGH 发现。**PASS。**
