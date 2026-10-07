# PRD: `iar run --quick` 轻量任务档位与行数红线清零

> ⏸️ **Hold（暂缓）**：2026-10-06 移入 `tasks/hold/`。原因：方向被更上层的架构决策取代 —— 使用者明确「Issue 是真相源，不是 PRD」，要做的是「一句话 → Issue」（见 `P1-FEAT-20261006-*` 的 issue-first 交付 PRD），而不是「出了 PRD 之后跑得快」。本 PRD 解决的是后者的时间成本，在新架构下其前提（每件事都先有 PRD）本身不再成立。**保留不删**：其中「拆分 `cli_parser.py` / `run_agent_execution_loop.py` 以清零 `#208` 遗留的 CI 红债」是独立的既存债务，与架构方向无关，可随时单独取出交付。

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ⬜ **验收状态**：未开工。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文分两层：Part A 是给人审的行为与决策层，不含实现机制、文件路径与命令；Part B 是给执行器的实现层。两层都是投影，`§8` 与 `§9` 才是对应事实源。

## Feature Overview (功能一览)

以下条目是 §10 Functional Requirements 的投影，行为验收请看 §1 的行为样例表。

- **小任务能走轻量档**（FR-1）：`iar run` 接受 `--quick`，对「改已有函数、补一张表」这类小需求，把 agent 花在自证与返工上的时间压到最低，同时**不降低交付物质量**。
- **交付物一步不少**（FR-2）：代码、测试、commit、Draft PR、人读呈递物截图、验收证据包 —— 轻量档与标准档产出的东西种类完全相同，只是不再多花时间反复验证已经成立的事实。
- **旗标可组合**（FR-3）：`--quick` 与 `--fast-merge` 可同时使用，也可各自单独使用，互不排斥。
- **PR 上看得出这轮走了轻量档**（FR-4）：走轻量档发布的 PR，正文带机器可读标注，reviewer 不必翻日志就知道这轮的验证强度。
- **顺手修掉 CI 红债**（FR-5）：本次把两个已超 1000 硬上限的源文件拆回限额内，让 `--quick` 自身不会被行数门禁挡住。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

用 iAR 跑一个真实需求（Issue #209：Stats 页补一张 token 汇总表）实测：**47 分钟**，其中 agent 花 **22 次工具调用**在验证一份交互式 HTML 审查清单的按钮能不能点、能不能作答、进度存没存进 localStorage。这 22 次不是 agent 偷懒或走神。

链条是这样的：`docs/ai-standards/tooling.md` 规定「清单携带截图或选项项时，同目录再生成自包含交互版 `human-review-checklist.html`」，且「Markdown 为静态底稿，两者内容必须一致」。于是 agent 写完清单后，必须实际打开页面、点一遍每个按钮、确认没有 JS 报错，才能声称「两者内容一致」。而 `--fast-merge` 恰好管不到这里 —— 它旁路的是 Phase 4.5 的重验与 verifier，HTML 清单的自我验证发生在更早的交付阶段。

更结构性的问题：**iAR 目前只有「全速」和「跳过验证门禁」两档，没有「活儿本身不大」这一档。** 一个改动 360 行、5 个源文件、22 分钟就能写完的需求，和一个需要重验与独立复核的架构改造，跑的是同一条流水线。当前唯一能压缩的维度是「验证强度」，压不掉「探索与自证成本」—— 而后者在简单任务里占了大头。

还有一处与本次直接相关的事实：`#208` 合并时把 `cli_parser.py`（1007 非空行）和 `run_agent_execution_loop.py`（1015 非空行）推过了 1000 行硬上限，`hooks/max_file_lines.allowlist.txt` 明确写着「本文件保留为空名单：不再豁免任何文件」。也就是说 **main 从#208 合并那一刻起 CI 就是红的**，而任何要改 CLI 旗标的改动都会撞上这道门。

### Interpretation (解读回显)

**行为样例**

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | 对一个「改已有函数 + 加一张表」的小需求跑 `iar run <PRD> --quick` | 交付物种类与标准档完全相同：有代码、有测试、有 commit、有 Draft PR、有人读呈递物截图、有验收证据包 |
| 🤖 自动验证 | 同一需求分别跑 `--quick` 与标准档，对比产物清单 | 两者产出的文件种类逐一相同；`--quick` 不产出任何标准档没有的东西，也不缺任何标准档有的东西 |
| 👀 人审 | 走 `--quick` 发布的 PR，打开正文 | 首部有一行机器可读的轻量档标注，写明这轮哪些验证强度被收窄了 |
| 🤖 自动验证 | `--quick --fast-merge` 一起跑 | 两个旗标各自生效，PR 正文同时带两处标注；单独跑任一个也能正常工作 |
| 🤖 自动验证 | 对一个确实需要重验与独立复核的大需求跑 `--quick` | 该跑的门禁仍然被跳过，行为与标准档一致；轻量档不会**加强**任何门禁 |
| 🤖 自动验证 | 查 `main` 分支的 CI 状态 | 之前由超限文件导致的「Validate Template」失败消失，`main` 恢复绿 |

上表的活跃行为行会被 §7.6 逐条转成验收断言，**改一个单元格就是在改验收标准**。

**我默默定了这些**

- **切agent 的自证，不切交付物**：轻量档**仍然生成**交互式 HTML 清单和真实截图，只是不再要求 agent 反复打开页面确认按钮能点、内容一致。理由：那 22 次里有价值的部分是「确认 HTML 真的能用」这件事本身，它已经被标准档的门禁覆盖过一次就够了；重复验证不产生新信息。
- **用「阶段集合」而不是再加一个布尔**：仓库里已有 11 个 per-stage `enabled` 配置开关，但`--fast-merge` 走的是 per-run 一次性布尔、不持久化。`--quick` 属于同一族，若再加一个布尔，穿透链上那 9 个文件、约 30 处签名镜像会再翻一倍（历史上 `fast_merge` 已经触发过重复检测告警）。用一个「要跳过的阶段名集合」同时容纳两种语义，并给未来留扩展位。
- **不落`.iar.toml`**：轻量档是「这一次我赶时间」的临时决定，持久化到配置会变成仓库的长期状态，与 `fast_merge` 的模型不一致。
- **顺手拆两个超限文件**：拆 `cli_parser.py` 的 run 段是必须的（加旗标必然要动它），而 `run_agent_execution_loop.py` 已经在 1015 行、`--quick` 的阶段集合判定也要动它 —— 两个都在本次触碰面内，一起拆比分两次做更省事，也让 main 从红变绿。
- **保留 allowlist 为空**：不把超限文件加进白名单。仓库已经明确「新增文件不得进入本名单 —— 超限即应拆分」，加白名单等于推翻这个决定。

**我理解为不做**

- **不做「跳过实现」或「跳过测试」档**：轻量档不碰 Phase 1/2。代码没写对、测试没过，跑得再快也是白搭。
- **不做「跳过人读呈递物」档**：截图和清单照旧生成。人是这次唯一真正的验收者，把给人的东西砍掉是本末倒置。
- **不改 `tooling.md` 的清单契约**：`human-review-checklist.html` 仍然要生成，只是 agent 不再被要求反复自证它可用。
- **不做自动判定「这个任务算不算小任务」**：由人显式加旗标。自动判断会让「这轮到底轻不轻」变得不可预测。

**证伪式理解**：读作「给`iar run` 增加一个 per-run 的轻量档，让小需求的交付时间显著下降，同时交付物一个不少，顺手把已经超限的两个源文件拆回限额内」。任何人读完这句话如果认为本次要跳过测试、要砍掉人读呈递物、要把行数超限加进白名单、或者要实现「自动判断任务大小」，那都是读错了。

### What The User Gets

小需求跑 `iar run <PRD> --quick`：拿到与标准档**完全同种**的交付物（代码、测试、Draft PR、截图、证据包），只是不用再等 agent 花二十来分钟反复验证一份 HTML 清单的按钮。同时 main 分支的 CI 从红回到绿。

### Measurable Objectives

- `--quick` 产出的交付物种类与标准档逐一相同（可核对文件清单）。
- Issue #209 这类规模的需求，走轻量档的端到端时间明显低于 47 分钟。
- 走轻量档发布的 PR，正文首部有机器可读标注说明本轮验证强度。
- `--quick` 与 `--fast-merge` 单独用、组合用，行为均正确。
- `main` 上「Validate Template」通过；`cli_parser.py` 与 `run_agent_execution_loop.py` 均回到 1000 非空行以内，且 `hooks/max_file_lines.allowlist.txt` 仍为空名单。

## 2. Human Review Map (介入与风险地图)

**本次没有需要人工拍板的分歧点。** 下面是理由，不是待办清单。

需要你确认的只有一件事本身是否值得做 —— 也就是 §1 的行为样例表是否就是你想要的结果，尤其第一条：**轻量档的交付物一个都不能少**。这一条是整个轻量档的成立前提，如果你的实际需求是「连截图都别生成」，那 `--quick` 就不是这个形状，该另说。

剩下的都是**执行器 + 自动门禁**范围：CLI 旗标与阶段集合的透传（`api` 适配层）、两个源文件的拆分（机械重构，有行数与测试双重门禁）、以及交付物清单的对照断言。

**本次明确不涉及**：没有数据库结构或迁移变更；没有安全或权限边界变更；没有并发生效顺序问题（per-run 旗标只影响单次进程内的一次执行）。唯一新增的对外表面是`iar run` 的一个旗标，因此需要同步随包 `iar-operator` skill 与 `docs/`（见 §5）。

## 3. Usage And Impact After Implementation

**用 iAR 跑小需求的人（主要受众，CLI）**：命令多一个可选旗标 `--quick`。不加旗标时行为**逐字不变** —— 这是硬约束，不是「尽量不变」。

**`iar run` 的既有调用方**：`skip_stages` 阶段集合默认**空集合**，等价于今天的行为。现有脚本、CI 里调 `iar run` 的地方、autopilot、daemon 都不受影响。

**`--fast-merge` 的使用者**：语义不变。轻量档与它是两个正交维度：前者收窄 agent 的自证强度，后者旁路验证门禁。

**随包 `iar-operator` skill 的消费方**：需要知道 `--quick` 存在、何时该用。这是 CLI 表面变更的既有同步义务。

**`main` 分支的 CI**：从红（`Validate Template` 因行数超限失败）恢复为绿。这是本次的一个附带但真实的效果。

向后兼容性：向后兼容 —— 新旗标可选且默认关闭，既有行为逐字不变。

## 4. Requirement Shape

- **actor**：用 iAR 跑小需求的人（CLI）；`iar-operator` skill 的消费方
- **trigger**：对「改动不大、不需要重验与独立复核」的需求，在 `iar run` 上显式加 `--quick`
- **expected behavior**：交付物种类与标准档完全相同；agent 不再花大量工具调用反复自证已成立的呈递物；PR 正文标注本轮验证强度
- **scope boundary**：不跳过实现与测试、不砍人读呈递物、不改清单文档契约、不落配置、不实现任务大小的自动判定

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### 相关模块

| 角色 | 位置 |
|---|---|
| typer 旗标定义 | `src/backend/api/cli_typer_runner.py:134-144`（`--fast-merge`）、`:178`（转发） |
| argparse 旗标定义 | `src/backend/api/cli_parser.py:300-310`（`--fast-merge`） |
| 两套 parser 的取值与门禁 | `src/backend/api/cli_parsed_commands/runner.py:74`、`:76-83`（`--all-ready` 冲突）、`:35,44`（dry-run 预览）、`:193`（传入）、`:215-257`（`_reject_fast_merge_on_stack_issue` fail-closed 实现） |
| core 入口转发 | `run_agent_repositories_once.py:60,84,158`、`run_agent_once.py:969,990`、`agent_runner_orchestrate.py:545,565` |
| 执行请求对象 | `run_agent_execution_loop.py:113-116`（`AgentExecutionRequest.fast_merge`） |
| 编排层转发 | `agent_runner_orchestration_runtime.py:197`、`:342/417/435`、`:560-563`、`:824` |
| handler 三条路径镜像 | `agent_runner_issue_handlers.py:164/179/243/257`、`:542/563/658/679/745`、`:910/926/962` |
| **Phase 4.5 唯一分支点** | `run_agent_execution_loop.py:986`（`if request.fast_merge:`，跳过 `rv_reexec` + `verifier`；`:981-995` 有注释与审计日志） |
| 前端视觉证据门禁 | `agent_runner_validation.py:470-501`（`FRONTEND_VISUAL_EVIDENCE_MISSING`） |
| 门禁修复指令 | `agent_runner_closeout.py:423-428` |
| per-stage 配置开关（11 个） | `agent_runner_settings.py:388/402/435/446/550/587/619/638/722/759/850`（`validation.enabled` 在 `:446`） |
| marker 工具 | `agent_runner_dependencies.py:332-351` `format_fast_merge_marker()`、`:354-` `parse_fast_merge_marker()` |
| PR 正文注入 | `agent_runner_publish.py:222/240/340-349` |
| 清单契约（文档） | `docs/ai-standards/tooling.md:196`（`human-review-checklist` 契约原文） |
| 审查清单打开器 | `scripts/shared/just/prd_review.py:33-36`（槽位优先级）、`:268`（文件名校验） |
| 行数门禁 | `hooks/check_max_file_lines.py`、allowlist `hooks/max_file_lines.allowlist.txt`（当前为空名单） |
| 既有测试 | `tests/test_agent_runner_fast_merge.py`（~471 行，10 用例） |
| CLI 表面守卫 | `tests/test_iar_operator_skill.py:107-110`（`_ALLOWED_FLAGS["run"]`） |

### 依赖方向

四层方向 `api/ -> core/ -> engines/ -> infrastructure/` 不变。改动落在 `api`（旗标与parser 拆分）、`core`（请求对象字段与阶段判定）、`core`（交付阶段提示词收窄）。`infrastructure` 不改 —— `skip_stages` 是 per-run 一次性参数，不进配置模型。

### 现有模式

`--fast-merge` 已经把「per-run 旗标 → 跳过某阶段 → PR 正文带机器可读标注」这条完整链路走通了一遍，包含 fail-closed 的前置拒绝、审计日志、以及 marker 的解析/生成函数。本次沿用同一模式，区别只在于跳过的是「agent 自证强度」而不是「验证门禁」。

### 所有权与边界

- **关键发现**：`human-review-checklist` 在 `src/` 与 `frontend-public/` 里**零引用**（全仓 grep 无命中）。它不是任何代码门禁的产物，而是 agent 阅读 `tooling.md:196` 的文档契约后自行生成的。这决定了 `--quick` 的切点必然在**提示词 / 契约暴露层**，而不可能是绕过某个代码门禁 —— 没有门禁可绕。
- `FRONTEND_VISUAL_EVIDENCE_MISSING`（`agent_runner_validation.py:470`）**保持不动**。它的修复指令（`agent_runner_closeout.py:423-428`）只要求「启动应用、走真实入口、存一张真实截图」，本来就不要求验证 HTML 清单按钮 —— 这条门禁不是 22 次Playwright 的来源，不该被误伤。
- 清单生成契约（`tooling.md:196`）**保持不动**。轻量档只是让 agent 不必反复自证它。

### 前端影响

无。`--quick` 不改任何前端代码，也不改任何前端交付物要求。

### 约束

- `cli_parser.py` 当前 **1007 非空行**、`run_agent_execution_loop.py` 当前 **1015 非空行**，均已超 1000 硬上限。`hooks/max_file_lines.allowlist.txt` 明确「保留为空名单，不再豁免任何文件」。**加旗标前必须先拆分**，否则本次自己的 CI 会挂。
- `generated_content.py` 是历史红线（768 行），PRD 明令禁止改动。
- 新增/修改公共 Python API 需 Google Style docstring（`D100`–`D107` 强制）。
- 提交信息用英文 Conventional Commits。
- `tests/playwright-e2e` 需要 `pnpm install --ignore-workspace`。

### 相关 PRD

- 已合并 `P1-FEAT-20261005-215933-fast-merge-run-flag.md`（`2b7cae4c`）：`--fast-merge` 的来源。本次是它的正交兄弟档位，**不修改它的任何行为**。
- 已合并 `P1-REFACTOR-20260705-210702-file-line-split-seven-files.md`：`max_file_lines` 机制的历史与「allowlist 保留为空名单」这个决定，本次沿用。
- `P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md`：与本次无关。
- Issue #209（`P1-FEAT-20261006-013227-stats-token-usage-by-prd`）：本次的实测来源，**不重复其目标状态**。

本次**不重复**任何 pending 工作：`tasks/pending/` 中除本次新增外无相关 PRD。

## 6. Recommendation

### Recommended Approach

**给 `iar run` 增加一个 per-run 的「跳过阶段集合」参数 `--quick`，收窄交付阶段对 agent 自证强度的要求；同时把两个已超限的源文件拆回1000 行以内。**

具体机制：

1. **参数形态**：用「要跳过的阶段名集合」承载轻量档语义，而不是再加一个布尔。默认空集合 ≡ 今天的行为。`--fast-merge` 的语义也可表达为该集合的一个成员，但**本次不改`--fast-merge` 的现有实现**，避免动到已验证的分支。
2. **切点定位**：轻量档作用于交付阶段（Phase 3/3.5）**给agent 的指令**：不再要求它反复打开并操作已生成的交互式清单来自证可用。清单与截图照旧生成。
3. **可观测**：`--quick` 单独使用、或与 `--fast-merge` 组合使用，都要在 PR 正文留机器可读标注，写明本轮被收窄的验证强度。
4. **顺带拆分**：把 `cli_parser.py` 的 run 子命令 parser 定义抽成独立模块（`add_model_preset_options` / `add_machine_output_options` / `add_common_options` 等已是天然 helper 边界），并把 `run_agent_execution_loop.py` 拆到限额内。

为什么这是最贴合现有架构的做法：

- **切点只能在提示词层**：`human-review-checklist` 全仓零引用，没有任何代码门禁可以「跳过」。唯一能让 agent 少做自证的办法是收窄它被要求做到什么程度。
- **不动 `FRONTEND_VISUAL_EVIDENCE_MISSING`**：读代码可知这条门禁只要求「真实截图」，不要求验证清单按钮。它不是那22 次的来源，误伤它会真的降低验收质量。
- **集合而非布尔**：`fast_merge` 的穿透链是 9 个文件约 30 处签名镜像，再加一个布尔会再翻一倍，且历史上已触发重复检测告警。集合参数同时容纳两种语义并留扩展位。
- **不落配置**：`fast_merge` 是 per-run 一次性语义，落配置会让「这轮赶时间」变成仓库长期状态。
- **拆分而非白名单**：allowlist 自己写明「新增文件不得进入本名单 —— 超限即应拆分」。

### Proposed Solution Summary (实现机制)

- **核心机制**：为 `iar run` 增加 per-run 阶段跳过参数，`--quick` 作为其命令行入口，作用于交付阶段的自证强度要求；同时拆分两个超限源文件。
- **谁提供数据**：不涉及数据来源推断。`--quick` 是人显式给的运行期决定，**由人负责选**。
- **接入点**：typer 与 argparse 两套 CLI 定义都要改（既有模式）；执行请求对象新增阶段集合字段；交付阶段的 agent 指令消费该字段。
- **系统状态变化**：不落配置、不改账本、不改 schema。仅影响单次进程内的一次执行。
- **用户可见行为变化**：`iar run` 接受新旗标；走轻量档的 PR 正文多一行标注；`main` 的 CI 由红转绿。
- **刻意避免的复杂度**：不加新配置项、不做任务大小自动判定、不改清单文档契约、不做第二个验证旁路开关。

### Alternatives Considered

**替代方案 B：把收窄落到 `.iar.toml` 配置层（复用既有 11 个 per-stage `enabled` 开关）。** 不采用。配置是仓库长期状态，而「这次赶时间」是 per-run 决定；且 `--fast-merge` 已经确立了 per-run 一次性模型，另立一套会让人搞不清「改了配置到底影响下次还是这轮」。

**替代方案 C：干脆不要新档位，把那22 次的自证要求直接从 `tooling.md` 的清单契约里删掉。** 不采用。契约本身是对的 —— 人读清单确实需要能点、能存进度。问题在于让**agent** 去自证它，而不是契约存在。把全局契约改弱会伤到所有认真交付的场景，而轻量档的需求只针对「赶时间的简单活」。

**替代方案 D：给 `human-review-checklist.html` 加个 `--no-html` 之类的高级开关。** 不采用。那是换个轴（砍交付物）而不是收窄自证强度，与你选的第一条样例直接冲突。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

```
iar run <PRD> [--quick] [--fast-merge]
   │
   ├─ cli_typer_runner.py / cli_parser.py        两套定义（既有模式，两处都要改）
   │    └─ runner.py:74  取值 → :76-83 与 --all-ready 冲突检查 → :193 传入
   │
   ├─ 【新增】阶段跳过集合参数
   │    └─ 默认空集合 ≡ 今天行为（硬约束：既有调用方逐字不变）
   │
   ├─ run_agent_execution_loop.py:113-116   AgentExecutionRequest 持有该集合
   │
   ├─ run_agent_execution_loop.py:986       Phase 4.5 判定（--fast-merge 的既有分支，不动）
   │
   └─ 【新增】交付阶段自证强度收窄
        ├─ 收窄：不再要求 agent 反复打开并操作已生成的交互式清单来自证可用
        ├─ 保留：清单（.md + .html）与真实截图照旧生成
        └─ 不动：FRONTEND_VISUAL_EVIDENCE_MISSING（agent_runner_validation.py:470）
                 与tooling.md:196 的清单契约原文
   │
   └─ agent_runner_publish.py:340-349  PR 正文注入轻量档机器可读标注
```

**关键点**：轻量档的切点不在任何代码门禁上（`human-review-checklist` 全仓零引用），而在**给 agent 的指令强度**上。`FRONTEND_VISUAL_EVIDENCE_MISSING` 读代码可知只要求「真实截图」，本来就不要求验证清单按钮 —— 误伤它会真的降低验收质量，必须明确不动。

### Change Impact Tree

```
iar run --quick 轻量档 + 行数红线清零
├── 拆分（前置条件，不做则本次 CI 必挂）
│   ├── src/backend/api/cli_parser.py
│   │   └── 把 run 子命令 parser 定义（:270-320 区段）抽到独立模块；
│       既有 add_model_preset_options / add_machine_output_options /
│       add_common_options / add_all_repositories_option 已是天然 helper 边界
│       目标：主文件回到 1000 非空行以内
│   └── src/backend/core/use_cases/run_agent_execution_loop.py
│       └── 拆出 Phase 编排中可独立成模块的部分（当前 1015 非空行）
│       目标：回到 1000 非空行以内
├── CLI 表面（两套定义 + 取值门禁）
│   ├── src/backend/api/cli_typer_runner.py
│   │   └── :134-144 区段新增 --quick 定义 + help；:178 转发
│   ├── src/backend/api/cli_parser.py（拆分后的模块）
│   │   └── 新增 --quick argparse 定义
│   └── src/backend/api/cli_parsed_commands/runner.py
│       ├── :74 区段取值，:35,44 dry-run 预览字段
│       ├── 与 --all-ready / --fast-merge 的组合规则（正交，不互斥）
│       └── :215-257 区段：若需 fail-closed 前置拒绝，照既有实现模式
├── core 请求对象与透传
│   ├── run_agent_execution_loop.py:113-116  AgentExecutionRequest 持阶段集合
│   ├── run_agent_repositories_once.py:60,84,158
│   ├── run_agent_once.py:969,990
│   ├── agent_runner_orchestrate.py:545,565
│   ├── agent_runner_orchestration_runtime.py:197,342/417/435,560-563,824
│   └── agent_runner_issue_handlers.py（:164/179/243/257、:542/563/658/679/745、:910/926/962 三条路径镜像）
├── 交付阶段自证强度收窄
│   ├── 收窄点：交付阶段给 agent 的指令（不绕过任何代码门禁）
│   ├── 保持不动：agent_runner_validation.py:470-501 FRONTEND_VISUAL_EVIDENCE_MISSING
│   └── 保持不动：docs/ai-standards/tooling.md:196 清单契约原文
├── 可观测
│   ├── agent_runner_dependencies.py:332-  marker 工具（照 format_fast_merge_marker 模式）
│   └── agent_runner_publish.py:222/240/340-349  PR 正文标注注入
├── CLI 表面同步义务
│   ├── src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md
│   │   └── 命令表 :21 + 注意小节 :104
│   └── tests/test_iar_operator_skill.py:107-110  _ALLOWED_FLAGS["run"] 白名单必须登记
├── 测试
│   ├── tests/test_agent_runner_quick.py（新建，照 test_agent_runner_fast_merge.py 结构）
│   └── tests/test_iar_operator_skill.py 旗标白名单
└── 文档
    ├── docs/guides/agent-runner.md（--quick 功能小节）
    ├── docs/api/references.md（marker 机读契约小节）
    └── 须过 uv run mkdocs build --strict
```

### Risk Classification Register

| 变更点 | 层级 | 风险档 | 决定性维度 | 介入方式 | oracle / 门禁 |
|---|---|---|---|---|---|
| 阶段跳过集合默认空时的行为 | core | R0 | 既有 `iar run` 调用方逐字不变 | 执行器 + 自动门禁 | rv-1：不加旗标跑一遍，既有阶段行为与改动前逐项相同（差分断言） |
| 轻量档交付物种类完整 | core | R1 | 交付物少一项就等于降低了交付质量（本次的核心红线） | 执行器 + 强 oracle | rv-2：轻量档与标准档产物清单逐一相同 |
| `tooling.md:196` 清单契约未被改动 | 文档 | R0 | 明确不改 | 执行器 + diff 断言 | rv-3：`git diff -- docs/ai-standards/tooling.md` 无 hunk |
| `FRONTEND_VISUAL_EVIDENCE_MISSING` 未被误伤 | core | R2 | 若为了让 agent 少做事而放宽这条门禁，会真的丢掉「真实截图」要求 | 执行器 + 强 oracle | rv-3：前端改动缺视觉证据时门禁仍抛 `ValidationEvidenceError` |
| `cli_parser.py` / `run_agent_execution_loop.py` 拆分 | api/core | R1 | 机械重构，但触碰 CLI 与执行主循环，回归面大 | 执行器 + 自动化门禁 | rv-4：拆分后行数均≤ 1000；全量测试绿；CLI surface 契约测试通过 |
| `--quick` 与 `--fast-merge` 组合语义 | core | R1 | 两个正交维度若互相覆盖会产生意外组合 | 执行器 + 自动门禁 | rv-5：单独用、组合用，各自标注均正确 |
| PR 正文标注可读性 | api | R1 | reviewer 依赖它判断验证强度 | 执行器 + 门禁 | rv-2 同批核验 |
| 随包 skill 与 CLI 表面漂移 | engines | R1 | agent 侧知识漂移 | 执行器 + 守卫测试 | rv-6：`tests/test_iar_operator_skill.py` 绿 |

`R2` 档只有一处（不得为了让 agent 少做事而误伤真实截图门禁），有针对性 oracle 覆盖。未出现 `R3`；无 schema/迁移、无安全边界、无并发事务、无破坏性数据操作。`main` 当前的 CI 红债是既有问题，本次顺带清零，不构成新风险。因此 §2 无人工确认项。

### Executor Drift Guard

- **本文列出的文件是起点，不是全集。** 用下面这些检索确认没有漏掉隐藏引用：

```bash
# --fast-merge 的完整穿透链（新增参数要照它逐层补齐）
rg -n 'fast_merge' src/backend tests

# 确认 human-review-checklist 仍然零代码引用（这是 --quick 切点判断的前提）
rg -n 'human-review-checklist' src/ frontend-public/app frontend-public/lib frontend-public/components

# 不得被误伤的门禁
rg -n 'FRONTEND_VISUAL_EVIDENCE_MISSING' src/backend

# 行数红线（拆分前先量，加旗标前必须清零）
uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend

# allowlist 必须保持为空名单
cat hooks/max_file_lines.allowlist.txt

# CLI 表面守卫白名单
rg -n '_ALLOWED_FLAGS' tests/test_iar_operator_skill.py
```

- **不要放宽 `FRONTEND_VISUAL_EVIDENCE_MISSING`**。读 `agent_runner_closeout.py:423-428` 就知道它只要求「真实截图」，本来就不要求验证清单按钮 —— 它不是那 22 次的来源。轻量档收窄的是「agent 反复自证清单可用」，不是「真实截图」。
- **不要改 `docs/ai-standards/tooling.md:196` 的清单契约**。清单仍然要生成（含交互版 HTML）；本次只是不让 agent 反复验证它。
- **不要把超限文件加进 `hooks/max_file_lines.allowlist.txt`**。该文件自己写明「新增文件不得进入本名单 —— 超限即应拆分」。
- **不要动 `src/backend/core/use_cases/generated_content.py`**（768 行，历史红线，PRD 明令禁止改动）。
- **不要改 `--fast-merge` 的既有分支判定**（`run_agent_execution_loop.py:986`）。轻量档与它是正交维度。
- **默认必须空集合**：不传旗标时行为与今天逐字相同。
- **失败排查**：若 `--quick` 看起来没生效，先确认旗标是否真的穿透到了 `AgentExecutionRequest`（在 Phase 判定处打日志查；`--fast-merge` 在 `:986` 有审计日志先例可照抄）；若 agent 仍然跑 Playwright，说明收窄的指令没有进入它的提示词 —— 检查是否只改了门禁而没改指令（本次切点就在指令层，改门禁无效）。
- **提交信息用英文 Conventional Commits**（`docs/ai-standards/tooling.md`）。
- 本仓根使用单一 pnpm lockfile。

### Flow Diagram

见上方 Core Logic 的数据流图（两套 CLI → 取值门禁 → 请求对象 → 交付阶段自证收窄 → PR 正文标注）。

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 不加任何新旗标时，iar run 的既有阶段行为与改动前完全相同
  reviewer: verifier
  real_entry: "真实 CLI 入口：改动前后各跑一次 iar run --dry-run（真实进程），对比阶段计划输出"
  expected: 不加 --quick 时阶段计划、dry-run 预览字段、既有门禁判定与改动前逐项相同；无新增的未预期阶段被跳过
  mock_boundary: none —— 真实 CLI 进程
  tier: R0
  test_layer: CLI 差分断言（改动前后输出对照）
  required_for_acceptance: true
  negative_control: 临时把阶段集合默认值从空改成含一个阶段名，断言必须失败（仅验证阶段临时改动，不得留在最终 tree）
  expected_fail: 默认值非空时既有行为断言失败

- id: rv-2
  behavior: 轻量档产出的交付物种类与标准档逐一相同，只是不再多花时间反复自证
  reviewer: human
  real_entry: "真实端到端：选一个 Issue #209 规模的小需求，分别以 --quick 与标准档各跑一次真实 iar run，核对两边交付物清单"
  expected: 两边交付物种类完全相同——代码改动、测试、Draft PR、人读呈递物截图、验收证据包，一个不少；--quick 的 PR 正文首部有机器可读轻量档标注；端到端时间明显低于标准档
  mock_boundary: none —— 真实 FastAPI 入口 + 真实账本 + 真实浏览器
  tier: R1
  test_layer: 端到端产物清单对照
  required_for_acceptance: true
  presentation: "两个 PR 的正文首部并排截图（一个带轻量档标注、一个不带），以及两边产物文件清单的对照表"
  must_cross: 真实 CLI 进程 + 真实 git 提交 + 真实 PR + 真实截图
  forbidden_bypasses: 用 fixture 响应替代真实账本；直接读 SQLite 绕过端点；手工编造产物清单
  fresh_state_probe: 采集前重新请求端点确认页面/统计与刚跑完的 run 一致
  final_tree_evidence: 两次 run 各自的 commit SHA 与 git tree 记录在证据报告
  negative_control: 临时让 --quick 跳过一类交付物生成，产物清单对照必须失败
  expected_fail: 轻量档少产出某类交付物时对照失败

- id: rv-3
  behavior: tooling.md 清单契约与 FRONTEND_VISUAL_EVIDENCE_MISSING 门禁均未被改动或放宽
  reviewer: verifier
  real_entry: "源码 diff 断言 + 真实门禁触发（前端改动但证据目录无截图）"
  expected: git diff 对 docs/ai-standards/tooling.md 与 FRONTEND_VISUAL_EVIDENCE_MISSING 相关代码无放宽性 hunk；构造「前端有改动但证据目录无视觉证据」时门禁仍抛 ValidationEvidenceError
  mock_boundary: 仅故障注入在测试夹具（不读账本），不在生产代码加开关
  tier: R2
  test_layer: diff 静态断言 + 失败路径测试
  required_for_acceptance: true
  negative_control: 临时放宽该门禁（允许无截图通过），失败路径测试必须失败
  expected_fail: 门禁被放宽后不再抛 ValidationEvidenceError，测试失败

- id: rv-4
  behavior: 两个超限源文件拆分后回到 1000 非空行以内，且拆分未破坏功能
  reviewer: verifier
  real_entry: "真实行数门禁脚本 + 全量测试 + 真实 CLI surface 契约"
  expected: check_max_file_lines.py 对 src/backend 全部通过；CI=true just test all 全绿；tests/test_cli_schema.py 与 test_cli_output_contract.py 通过；main 的 Validate Template 转绿
  mock_boundary: none
  tier: R1
  test_layer: 行数脚本 + 全量测试 + CLI 契约测试
  required_for_acceptance: true
  negative_control: 本 oracle 本身即负向用例（拆分前该脚本对这两个文件报ERROR，拆分后不报）
  expected_fail: 拆分不彻底时行数脚本仍报超限

- id: rv-5
  behavior: --quick 与 --fast-merge 单独使用、组合使用时行为均正确且标注互不覆盖
  reviewer: verifier
  real_entry: "真实 CLI 入口：四种组合（无旗标 / --quick / --fast-merge / 两者同时）的参数解析与 dry-run 输出"
  expected: 四种组合均正常解析；--fast-merge 单独时仍只旁路 Phase 4.5；两者同时时两处标注都出现在 PR 正文且不互相覆盖；与 --all-ready 的既有冲突规则未被破坏
  mock_boundary: none
  tier: R1
  test_layer: 参数解析测试 + dry-run 预览断言 + PR 正文标注断言
  required_for_acceptance: true
  negative_control: 临时让两个旗标共用同一个标注分支，两者同时使用时断言必须失败
  expected_fail: 标注被覆盖时 PR 正文只出现一处标注，断言失败

- id: rv-6
  behavior: 随包 iar-operator skill 与 CLI 表面保持同步
  reviewer: verifier
  real_entry: "真实守卫测试：tests/test_iar_operator_skill.py"
  expected: _ALLOWED_FLAGS["run"] 已登记新旗标且测试通过；SKILL.md 的命令表与注意小节含新旗标说明；iar schema --json 能列出新旗标
  mock_boundary: none
  tier: R1
  test_layer: 守卫测试 + schema 契约测试
  required_for_acceptance: true
  negative_control: 从 _ALLOWED_FLAGS 移除新旗标后守卫测试必须失败
  expected_fail: skill 未同步时守卫测试失败
```

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 独立目标状态。`--fast-merge` 已在main（`2b7cae4c`），本次沿其模式扩展一个正交档位，不修改其行为。**注意**：`main` 当前因 `cli_parser.py`（1007 行）与 `run_agent_execution_loop.py`（1015 行）超限而 CI 红色（`#208` 遗留），本次拆分即为清零该债。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 看什么 | 呈递物 | ~10 秒自检 |
|---|---|---|
| 走轻量档发布的 PR，正文首部有机器可读的验证强度标注 | 两个 PR 正文首部并排截图（带标注 / 不带标注）：<br>截图 `tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-2-pr-header-compare.png`（gitignored，本地专用）<br>打开：`open "tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-2-pr-header-compare.png"` | 打开 <https://github.com/ZataZhang/keda/pulls> 找两个 PR，一个首部有 `<!-- iar:quick -->` 类标注、一个没有 |
| 轻量档交付物一个不少 | 两边产物清单对照表：`rv-2-deliverables-parity.md`（同目录，gitignored）<br>打开：`open "tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-2-deliverables-parity.md"` | 打开对照表，逐行确认「交付物种类」那一列两栏完全相同 |
| main 的 CI 从红回到绿 | <https://github.com/ZataZhang/keda/actions> 上 main最新一次 CI 全绿截图：`rv-4-main-ci-green.png` | 打开 Actions 页，看 main 最新一次 run 的 Validate Template 是绿色 |

> 验证层级说明：以上均为**真实入口** —— PR 是 GitHub 上真实发布的 PR，CI 是真实运行的流水线，产物清单来自真实执行的两次 `iar run`。都不是模拟或声明。
>
> 以下 `reviewer: verifier` 组**刻意不在上方呈递**，只在失败时需要人工介入：rv-1（不加旗标时行为差分）、rv-3（清单契约与截图门禁未被放宽）、rv-4（行数门禁 + 全量测试 + CLI 契约）、rv-5（四种旗标组合）、rv-6（skill 与 CLI 表面同步）。

### 9.2 Acceptance Evidence Package

**Human-Confirmed (来自 Part A 风险地图)**

- [ ] §1 行为样例表六行逐行确认，**尤其第一条「交付物种类与标准档完全相同」** —— 这是整个轻量档的成立前提。回答方式：对每一行回复「符合」或指出哪一行与预期不符。
- [ ] 确认「轻量档不落 `.iar.toml`、仍是 per-run 一次性决定」这一设计（§1 我默默定了这些第4 条）。回答方式：「可接受」或说明你希望它可持久化。

**Architecture Acceptance**

- [ ] 阶段跳过集合参数默认空，且空集合时行为与改动前逐字相同（rv-1 的结构性前提）。
- [ ] `--fast-merge` 的既有分支判定（`run_agent_execution_loop.py:986`）未被修改。
- [ ] `hooks/max_file_lines.allowlist.txt` 仍为空名单，两个超限文件是通过拆分而非豁免解决的。
- [ ] 依赖方向合法：新增 import 无逆向依赖（`just lint` 架构检查通过）；`infrastructure` 层零改动。

**Behavior Acceptance**

- [ ] rv-1 PASS：不加旗标时 `iar run` 阶段行为与改动前逐项相同。证据：`tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-1-no-flag-parity.txt`
- [ ] rv-2 PASS：轻量档与标准档产物清单逐一相同；轻量档 PR 首部有机器可读标注；端到端时间明显低于 47 分钟。证据：`tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-2-deliverables-parity.md` + `rv-2-pr-header-compare.png`
- [ ] rv-3 PASS：`tooling.md:196` 清单契约与 `FRONTEND_VISUAL_EVIDENCE_MISSING` 均未被放宽；构造无截图场景门禁仍抛错。证据：`tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-3-gates-intact.txt`
- [ ] rv-4 PASS：两个文件均≤ 1000 非空行；`CI=true just test all` 全绿；CLI schema/契约测试通过；main 的 Validate Template 转绿。证据：`tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-4-line-limit-and-tests.txt`
- [ ] rv-5 PASS：四种旗标组合行为正确，两处标注互不覆盖，与 `--all-ready` 的既有冲突规则未破坏。证据：`tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-5-flag-matrix.txt`
- [ ] rv-6 PASS：随包 skill 已同步，`tests/test_iar_operator_skill.py` 绿，`iar schema --json` 列出新旗标。证据：`tasks/evidence/P1-FEAT-20261006-024434-iar-run-quick-stage-skips/rv-6-skill-sync.txt`

**Documentation Acceptance**

- [ ] `docs/guides/agent-runner.md` 新增 `--quick` 功能小节，含与 `--fast-merge` 的正交关系说明。
- [ ] `docs/api/references.md` 新增轻量档 marker 的机读契约小节。
- [ ] 随包 `iar-operator/SKILL.md` 的命令表与注意小节已同步（命令表 `:21`、注意小节 `:104`）。
- [ ] `uv run mkdocs build --strict` 通过。
- [ ] `docs/ai-standards/tooling.md` 的**清单契约段未被改动**（这是 rv-3 的一部分；本次只加`--quick` 旗标的 CLI 表面同步条目，不动清单契约）。

**Validation Acceptance**

- [ ] 真实入口验证已执行：rv-2 走真实两次 `iar run`（真实 CLI 进程 + 真实 PR + 真实截图），非仅单元测试。
- [ ] `CI=true just test all` 通过；`just lint` 通过。
- [ ] 负向对照已跑：rv-1 / rv-2 / rv-3 / rv-5 / rv-6 各自的负向对照均已实际执行并记录在对应证据文件中（不是只声明做过）。
- [ ] 证据目录下 `human-review-checklist.md`（含 HTML 伴生页，若含截图）已生成。

**Delivery Readiness**

- [ ] 交付 PR 正文按 `prd-evidence-and-merge-acceptance` 契约写：唯一链接本 PRD、明确声明「合并即验收」的含义、投影 §2 的人审决策、投影 §9.1 的人读呈递、给出 verified head 与 git tree。
- [ ] PR 证据评论包含 §9.1 呈递内容、verifier 结论、必跑门禁汇总、证据链接与可复现命令。
- [ ] 原始证据（截图等非 .md 产物）不进入代码 diff；按需通过证据分支或 PR 评论发布。
- [ ] 完成消息原样携带 §9.1 的呈递内容（截图相对路径 + `open` 命令 + 打开 URL），而不是只说「证据已归档」。

## 10. Functional Requirements

- **FR-1**：`iar run` 接受 `--quick` 旗标；对小需求显著降低端到端时间。
- **FR-2**：轻量档交付物种类与标准档逐一相同（代码、测试、Draft PR、人读呈递物截图、验收证据包），一个不少。
- **FR-3**：`--quick` 与 `--fast-merge` 是正交维度，可单独用、组合用，互不覆盖；与 `--all-ready` 的既有冲突规则不被破坏。
- **FR-4**：走轻量档发布的 PR 正文带机器可读标注，写明本轮被收窄的验证强度。
- **FR-5**：`cli_parser.py` 与 `run_agent_execution_loop.py` 拆分回 1000 非空行以内，让本次改动自身不被行数门禁挡住，并使 main 的 CI 由红转绿。
- **FR-6**：不传新旗标时 `iar run` 行为与改动前逐字相同。
- **FR-7**：`docs/ai-standards/tooling.md:196` 的清单契约与 `FRONTEND_VISUAL_EVIDENCE_MISSING` 门禁均不被改动或放宽。
- **FR-8**：`--fast-merge` 的既有分支判定与 PR 正文标注不被修改。
- **FR-9**：`hooks/max_file_lines.allowlist.txt` 保持为空名单，不豁免任何文件。
- **FR-10**：新增旗标同步到随包 `iar-operator` skill 与 `docs/`，并通过 CLI 表面守卫测试。

## 11. Non-Goals

- 跳过实现阶段或测试阶段（Phase 1/2）的档位。
- 砍掉人读呈递物（截图、交互式清单）的档位。
- 修改 `tooling.md` 的清单生成契约。
- 把轻量档落到 `.iar.toml` / `config.toml` 成为持久化配置。
- 自动判定「这个任务算不算小任务」并自动加旗标。
- 改动 `iar run` 以外的任何子命令。
- 为超限文件添加 allowlist 豁免。
- 改动 `--fast-merge` 的既有行为或 PR 标注。

## 12. Risks And Follow-Ups

- **`run_agent_execution_loop.py` 拆分风险较高**：它是执行主循环，机械拆分触及面大。缓解：只做无逻辑变更的物理拆分（提取纯函数 / 搬移常量），不改分支判定；用 rv-4 的全量测试 + rv-1 的行为差分双重兜底。
- **`--quick` 收窄指令后agent 可能真的跳过必要自证**：若收窄写得含糊，agent 可能连真实截图也一起省了。缓解：PRD §7 明确「真实截图照旧生成」，并由 rv-3 从代码侧锁住 `FRONTEND_VISUAL_EVIDENCE_MISSING` 不被放宽。
- **`skip_stages` 集合的命名与边界可能后续再变**：若将来阶段名体系调整，集合成员名会漂移。缓解：本次不把它暴露到配置文件，per-run 参数坏了只影响单次运行。
- **证据自证性产物的时间成本无精确基线**：47 分钟是单次实测，`--quick` 能压到多少需要实测确认。缓解：rv-2 要求记录两边的端到端时间，若降幅不明显应如实回填 PRD 而非宣称达成。
- **`#208` 的 CI 红债清零后可能暴露其他被掩盖的问题**：main 变绿是个新基线，可能带出此前被红 CI 挡住的失败。缓解：合并后关注 main 的首次完整 CI。

## 13. Decision Log

| ID | 决定 | 选择 | 否决 | 理由 |
|---|---|---|---|---|
| D-01 | 轻量档切什么 | 只收窄 agent 的自证强度要求，交付物一项不少 | 跳过实现与测试 / 砍掉人读呈递物 | 交付物是给人验收的，砍它就是本末倒置；本次痛点是重复自证已成立的事实，不是产出本身 |
| D-02 | 参数形态 | per-run 阶段名集合（`skip_stages`），默认空集合 | 再加一个 `--quick` 布尔 | `fast_merge` 的穿透链已是 9 文件约 30 处签名镜像，再加布尔会翻倍并重复触发重复检测告警；集合还能容纳 `fast_merge` 语义与未来扩展 |
| D-03 | 配置落点 | per-run 一次性，不落 `.iar.toml` | 复用既有 11 个 per-stage `enabled` 开关 | 「这次赶时间」是临时决定，配置是长期状态；且 `fast_merge` 已确立 per-run 模型，另立一套会让人搞不清影响范围 |
| D-04 | `FRONTEND_VISUAL_EVIDENCE_MISSING` | 保持不动 | 为了让 agent 少做事而放宽它 | 读 `agent_runner_closeout.py:423-428` 可知它只要求「真实截图」，本来就不要求验证清单按钮—— 它不是那 22 次的来源，误伤会真的丢掉真实截图要求 |
| D-05 | `tooling.md` 清单契约 | 不改 | 直接删掉「交互版 HTML 必须与 md 一致」的要求 | 契约本身是对的（人读清单需要能点、能存进度）；问题在让 agent 去自证它，而不是契约存在 |
| D-06 | 行数超限的处理 | 拆分两个文件 | 加进 `hooks/max_file_lines.allowlist.txt` 白名单 | allowlist 自己写明「新增文件不得进入本名单 —— 超限即应拆分」；加白名单等于推翻该决定并让问题继续恶化 |
| D-07 | 拆分时机 | 纳入本 PRD 一并做 | 另开 Issue 单独还债 | 两个文件都在本次触碰面内（加旗标必然要动 `cli_parser.py`），一起做比两次做更省事，且顺带让 main 由红转绿 |
| D-08 | 任务大小判定 | 由人显式加旗标 | 自动判断 | 自动判断会让「这轮到底轻不轻」不可预测，也难以复现 |

## Change Log

### 移入 tasks/hold/（暂缓执行）
- Type: doc
- Before: 位于 `tasks/pending/`，验收横幅 `⬜ 未开工`，参与当前排期
- After: 移入 `tasks/hold/`，顶部补 `⏸️ Hold（暂缓）` 说明行，不再进任何自动看板
- Reason: 使用者的架构定位改为「Issue 是真相源，不是 PRD」，交付入口应是「一句话 → Issue」；本 PRD 的前提（每件事先有 PRD）在新方向下不再成立
- Impact: 本 PRD 的 `--quick` 档位与 rv-1~rv-6 全部 oracle 暂不交付；其中「拆分两个超限源文件以清零 CI 红债」被识别为独立可取出项
- Review: 使用者于本轮对话确认「搁置，先做 Issue 真相源」
