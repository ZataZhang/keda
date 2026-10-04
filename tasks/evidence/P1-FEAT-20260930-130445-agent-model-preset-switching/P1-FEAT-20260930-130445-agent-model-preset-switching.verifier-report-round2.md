VERDICT: PASS

# 独立 Verifier 报告（Round 2，fresh eyes）

- PRD: `tasks/pending/P1-FEAT-20260930-130445-agent-model-preset-switching.md`
- 冻结态: 分支 `feat/agent-model-presets`，HEAD `0a8248e9e4489a1e8a5c019c615d1b3aeaecdbe8`（= `0a8248e9`），tracked 文件 working tree 干净（`git status --short` 输出为空，含 untracked 亦为空）。
- 本轮定位: 独立复核 Round 1（PASS，V-01..V-05 全 LOW）之后的整改提交 `0a8248e9`（`docs(agent-runner): fix stale schema-version docstring and evidence wording`），并以新鲜视角重做抽验。全程只读，除本报告外未写任何文件。

## 一、冻结态检查

| 检查项 | 期望 | 实测 | 结论 |
|---|---|---|---|
| 分支 | `feat/agent-model-presets` | `feat/agent-model-presets` | 一致 |
| HEAD | `0a8248e9` | `0a8248e9e4489a1e8a5c019c615d1b3aeaecdbe8` | 一致 |
| working tree（tracked） | 干净 | `git status --short --untracked-files=no` 为空 | 一致 |
| 分支提交数 | 仅实现 + 整改 | `git log --oneline main..HEAD`：`22a0914e`（实现）、`0a8248e9`（整改），共 2 条 | 一致 |

## 二、Round 1 整改复核

| Finding | 整改声明 | 独立验证 | 结论 |
|---|---|---|---|
| V-01（LOW） | `console_store.py` 模块 docstring 更新为 schema v6 | `src/backend/infrastructure/persistence/console_store.py:8-10`：「通过 `PRAGMA user_version` 做就地迁移（当前版本 6：…v6 为 `attempt_records` 附加可空 `preset` / `model` 观测列）」；`:122` `_SCHEMA_VERSION = 6`；`:157-160` v5→v6 附加式迁移语句与 `:306-308` 幂等列检查一致 | 已整改，属实 |
| V-05（LOW） | 证据报告收窄 `argv.append` 断言口径 | `P1-FEAT-20260930-130445-agent-model-preset-switching.evidence-report.md:13`：「本 PRD 新增的注入点全部位于 `agent_invocation.py`；既有的 `container_ops.py:174` 命中属容器命令组装（本次未改动，与 main 逐字节一致），不在模型注入路径上」。独立验证：`git diff main...HEAD --name-only` 中无 `container_ops.py`（确实与 main 一致）；`container_ops.py:168-175` 为 `docker compose up` 的 argv 组装（`-d`/`--build`），与模型注入无关；`architecture-rg-gates.txt:6-13` 的命中清单与该口径吻合 | 已整改，属实 |
| V-02 / V-03 / V-04 | 按ROUND 1口径 accepted as-noted | 本轮无新动作要求；V-03 的冻结 HEAD 全量结论（2697 passed / 1 skipped）在 `unit-test-evidence.txt:6` 有留存 | 维持 as-noted |

## 三、独立抽验（不采信 Round 1 结论）

### 3.1 定向测试重跑

命令（worktree 内执行）：

```
uv run pytest tests/test_agent_model_presets.py tests/test_console_store.py tests/test_lifecycle_agent_routing.py tests/test_iar_operator_skill.py -o addopts="" -q
```

结果：**70 passed**（1.87s）。分文件：`test_agent_model_presets.py` 21、`test_console_store.py` 20、`test_lifecycle_agent_routing.py` 22、`test_iar_operator_skill.py` 7（21+20+22+7=70，合并跑与分跑一致）。

### 3.2 对抗性审计：模型 flag 是否可能注入到「执行 agent ≠ 预设 agent」

结论：**未发现任何此类路径**。`build_agent_invocation` 全仓仅 5 处调用（`run_agent_once.py:673`、`content_generators.py:81/157`、`agent.py:324`、`transcript_runner.py:70`），逐条核验：

| 阶段 / 入口 | 防线（file:line） | 判定 |
|---|---|---|
| 统一丢弃规则 | `run_agent_once.py:705-729` `drop_model_selection_for_agent`：`model_selection.agent != agent_name` 时返回 None 并记 warning | 规则本体正确 |
| resilient 层 | `run_agent_once.py:778-783`：透传前先过 drop；原地瞬态重试不换 agent，绑定保持 | 覆盖 |
| implementation | `agent_runner_issue_handlers.py:191/579` 先 drop；执行循环 `run_agent_execution_loop.py:439` 对 `selected_agent` 再 drop（回退换候选覆盖） | 覆盖 |
| fix | `run_agent_execution_loop.py:743-751`（fix 自绑或继承实现者）→ `run_fix_agent`（`run_agent_once.py:630-642`）→ resilient drop | 覆盖 |
| closeout | `run_agent_execution_loop.py:309-317`（closeout 自绑或继承）→ `agent_runner_closeout.py:537` → resilient drop | 覆盖 |
| verifier | `run_verifier_agent.py:565` drop（只认 verifier 自己的绑定，不继承）→ `:353` 注入 | 覆盖 |
| review | `agent_review.py:572` drop（只认 review 自己的绑定）→ `:663` 注入 | 覆盖 |
| supervisor / repair | `agent_runner_supervisor.py:97-103`：supervisor 与 repair 各自 drop → `pr_supervisor.py:709/886`、`pr_supervisor_repair.py:93` 注入 | 覆盖 |
| deliberate（含 synthesizer） | `agent_runner_deliberation_issues.py:313` 解析绑定；`transcript_runner.py:69` 对每个参与者与 synthesizer 逐个 drop（`run_agent_deliberation.py:218/282/476/499/544` 全部经 transcript runner） | 覆盖 |
| planner（`iar ask`） | `agent.py:79-84`：显式 `--agent` 时 drop；auto 时 `agent = resolve_lifecycle_agent("planner", …)`（有绑定时返回预设声明的 agent，构造上相等） | 覆盖 |
| content_generation | `generated_content.py:595-601/805-812`、`generated_prd_content.py:411-412`：`agent_name = model_selection.agent`（绑定整体决定，构造上安全）；无绑定时走既有解析且不注入 | 构造安全 |
| doctor `--preset` 视角 | `agent.py:461-501`：what-if 工具按用户显式指定 agent 打印，模板缺失如实报错（符合 FR-7）；`--model`/`--reasoning-effort` 无 `--preset` 时 fail-fast（`:478-483`） | 符合设计 |
| 绕过 resilient 的直呼 `run_agent_with_prompt` | `pr_supervisor.py:702/878`、`pr_supervisor_repair.py:86` 收到的 selection 已在 `agent_runner_supervisor.py:97-103` 按同 agent drop；`agent_runner_worktree_branch.py:142`、`agent_runner_verification_recovery.py:80` 不传 selection | 无绕过风险 |
| fail-fast（模板缺失） | `agent_invocation.py:236-242/253-260` `ModelNotSupportedError`，绝不静默忽略 | 覆盖 |

CLI 锚定 fail-fast：`api/cli_model_preset_anchor.py:59-65`（`--model`/`--reasoning-effort` 缺 `--preset` 抛 ValueError）、`:91-95`（未知预设名 fail-fast）；六个锚定入口（run/daemon/review/review-daemon/ask/issue create）均经 `apply_cli_model_preset(_to_config)`。

planner 键位核对：矩阵键含 planner（`lifecycle_agent.py:42`，九键闭集）；PRD 头部 `lifecycle_presets` 覆盖块校验键排除 planner（`lifecycle_agent.py:59-61`，`LIFECYCLE_AGENT_PRD_OVERRIDE_KEYS`），与 PRD FR-4「除 planner 外八键」一致。

### 3.3 PRD §9.2 与 Change Log

- §9.2 **Human-Confirmed 五项全部 `[x]`**（决策一~四 + 9.1 呈递区认可）；Architecture / Dependency / Behavior / Frontend / Documentation / Validation / Delivery Readiness 各条均 `[x]` 且带 rv 佐证指向。
- Change Log 两条 2026-10-03 条目均在：`### 2026-10-03 · 人读呈递区确认（decision-board）`（§14 首条）与 `### 2026-10-03 · 实现交付（模型预设接入生命周期）`，均为六字段 bullet 形态（Type/Scope、Changed、Invariant、Reason），非表格。
- decision-board 底账核对：`.iar/decisions/answers.json` `submitted_at=2026-10-03T15:16:16.763Z`，`accepted_recommendations=6`，Q1–Q4 confirm、Q5 approve，`notes={}`、`changed=[]`——与 Change Log「6/6 按推荐确认，无偏离、无备注」逐字吻合。

### 3.4 Per-rv 确认

| rv | 内容 | 证据 | 本轮结论 |
|---|---|---|---|
| rv-1 | doctor `--preset` 注入 | `rv-1-agent-doctor-preset.txt`（存在）；§9.1 自检口径与 argv 注入位置（profile args 之后、提示词之前）与 `agent_invocation.py:309-351` 组装顺序一致 | 确认 |
| rv-2 | 预设清单可枚举 | `rv-2-agent-doctor-preset.txt`（存在）；单测覆盖 | 确认 |
| rv-3 | `--model`/`--reasoning-effort` 同名覆盖 | `cli_model_preset_anchor.py:96-102` 覆盖折叠实现 + 单测 | 确认 |
| rv-4 | 无旗标零回归 | `rv-4-agent-doctor-claude.txt`（存在）；黄金快照 34 例未改期望值（evidence-report:12） | 确认 |
| rv-5 | 模板缺失 fail-fast | `agent_invocation.py:236-242/253-260` + 单测 | 确认 |
| rv-6 | 绑定遮蔽矩阵 / 显式 --agent 最高 | `rv-9` 上半 + `lifecycle_agent_resolution.py:534-540`、单测 | 确认 |
| rv-7 | 换人丢弃绑定 | `drop_model_selection_for_agent` 全链路审计（§3.2）+ `test_drop_model_selection_on_agent_switch` | 确认 |
| rv-8 | 文档 / SKILL 同步 | evidence-report:47（守卫测试对账 SKILL 命令与真实 `--help`） | 确认 |
| rv-9 | lifecycle 绑定 / 负控回落 | `rv-9-agent-doctor-lifecycle.txt`（存在，含绑定与无绑定两半） | 确认 |
| rv-10 | PRD 块覆盖仓库层 / executor 继承 | `lifecycle_agent_resolution.py`（PRD 块最高优先）+ `run_agent_execution_loop.py:309-317/743-751` 继承逻辑 + 单测 | 确认 |
| rv-11 | console 视图 / 账本 v6 | `console_store.py` v6 迁移 + 单测（test_console_store 20 例含迁移幂等） | 确认 |
| rv-12 | 绑定生效真实跑一轮（opt-in） | 未执行；decision-board Q6 明确本轮不采，evidence-report「已知限制」第 1 条如实标注 | 按人不采口径确认 |

## 四、观察项（LOW，不阻塞）

1. PRD §14 中 `### 2026-09-30 · 按需求方口径改写：预设接入生命周期矩阵` 标题出现两次（第一次为空标题、第二次才带正文，PRD 第 717/719 行）。系 09-30 改写期遗留的排版问题，非本次整改引入，不影响验收。可在后续 PRD 维护时顺手去重。

## 五、最终判定

**PASS。** 整改提交 `0a8248e9` 如实修复 V-01/V-05 且未引入新变更面（该提交仅触 docstring 与证据报告文字，`git show --stat 0a8248e9` 口径与整改声明一致）；独立抽验（70 例定向测试、九阶段对抗性审计、CLI fail-fast、§9.2 与 Change Log、decision-board 底账）全部通过，无新增 finding 超出上述 LOW 观察项。
