# PRD: Agent 证据自检入口（kc evidence check）

- GitHub Issue: https://github.com/ZataZhang/keda/issues/341

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ⬜ **验收状态**：未开工。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

Part A 用于核对行为与验收结果；Part B 用于实施。本次只编写 PRD，尚未实施或取得运行证据。

## Feature Overview (功能一览)

本节是 §10 的投影；行为验收以 §1 的样例为准。

- **本地自检入口**（FR-1）：执行 Agent 在提交前用一条命令知道自己写的证据能不能过门禁。
- **判定同源**（FR-2）：自检与交付门禁用同一份解析、复跑和断言判定代码，不出现第二套实现。
- **一次报全**（FR-3）：所有题目的失败一次列完，而不是每轮只报第一条再等二十分钟。
- **断言可诊断**（FR-4）：断言不命中时直接给出期望串与最接近的实际输出行。
- **机器可读**（FR-5）：退出码与 `--json` 让 agent 和脚本都能稳定判定结果。
- **口径收紧**（FR-6）：声明了输出断言就必须自检通过，并说明单条命令 300 秒上限。
- **门禁与文档同步**（FR-7）：门禁改为聚合报错，文档与随包 skill 一起更新。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

执行 Agent 目前没有任何本地手段知道自己写的证据包能否通过交付门禁，只能提交后等门禁复跑，而门禁一次只报第一条失败。

Issue #334 是可直接核对的现场记录：该 Issue 连续七轮失败，其中六轮与功能无关，全部是证据簿记问题——manifest 必填字段为空、复跑命令超过 300 秒、命令用 `|| true` 兜底、断言串与脚本实际输出不一致。最后一次失败是断言要求 `LEGACY GREEN: v1 example-derived suite ...`，而脚本打印 `LEGACY GREEN: v1 suite ...`：断言是照着 PRD 散文手抄的，与实际 stdout 只差一个词，功能本身正确。第一轮 attempt 耗时约 4.5 小时，最后因为一个字段为空被判失败。

三个结构性原因让这类错误无法自查：证据目录被 `.gitignore` 忽略，代码评审看不到它（该轮 pre-PR review 给出 approved、0 findings，四分钟后门禁即红灯）；300 秒复跑上限只写在文档和配置项里，执行 prompt 没有提；`kc` 没有任何在本地复跑证据命令并核对断言的子命令。

### Interpretation (解读回显)

给执行 Agent 一个与交付门禁同源的本地自检命令：它按门禁的规则复跑证据命令、核对输出断言，但把全部失败一次列清，并告诉 Agent 断言差在哪个词。Agent 在提交前自己跑一遍，就能在花钱和时间之前发现证据错误。交付门禁的通过判据不变，只是报错改为聚合。

**行为样例**

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 🤖 自动验证 | 在证据正确的 worktree 里运行 `kc evidence check` | 逐项输出 PASS，汇总为 6/6 通过，退出码 0 |
| 🤖 自动验证 | 证据里有三个题目各写错一条断言，运行 `kc evidence check` | 一次列出三条失败（不是只报第一条），每条给出期望串与实际最接近的输出行，退出码 1 |
| 🤖 自动验证 | 某题命令运行超过 300 秒，其余题目正常 | 该题标为超时，其余题目继续执行完并给出各自结论，退出码 1 |
| 🤖 自动验证 | manifest 缺必填字段或版本号不是 1 | 以格式错误逐条列出，不执行任何证据命令，退出码 1 |
| 🤖 自动验证 | 同一份证据包先通过 `kc evidence check`，再进入交付门禁复跑 | 门禁不出现自检未报过的失败；两边的断言判定结论一致 |

上表中仍然成立的行为行就是 §7.6 的验收 oracle：改某一格的预期，等于改验收标准。

**我默默定了这些**（没问你、我自己定的歧义点）

- **入口形态 → 新增 `kc evidence check` 子命令**，而不是发一个独立 Python 脚本或让 Agent 自己拼命令。
- **证据目录解析 → 显式参数优先**：缺省在 `tasks/evidence/` 下寻找含 `evidence.json` 的目录；找到多个就报错并列出候选，不猜。
- **超时 → 默认取门禁配置值（当前 300 秒）**，允许 `--timeout` 调小以便快速迭代，且不得大于配置值。
- **退出码 → 沿用 kc 通用码**：0 全通过、1 有失败、2 用法错误。
- **门禁判据 → 完全不变**，只把"遇到第一条失败就停"改为"收集完全部失败再判定"。
- **不做自动修复**：自检只报告，不替 Agent 改断言或改脚本。

**我理解为不做**（你可能想要、但我读成不在范围内的）

- **在 Console 或网页上展示自检结果**：这是给 Agent 用的命令行工具，不做界面。
- **把证据目录纳入版本控制**：现有 `.gitignore` 策略不变，评审能看到的仍然是三份 Markdown 报告。
- **自动生成断言**：断言写什么依然由执行 Agent 决定，工具只负责如实判定。

读成：**把门禁的判定提前到 Agent 手边**——同一套代码、同一套超时、同样的断言语义，只是把反馈从"二十分钟一轮、一次一条"变成"本地几秒、一次全量"；而不是：新做一个验证系统、改变交付判据、或让工具替 Agent 写证据。

- [ ] 我确认这段解读与行为样例准确表达了我的需求。

### What The User Gets

执行 Agent 在提交前运行一条命令，就能看到自己写的每一条证据命令是否会通过、哪一条断言对不上、差在哪个词；交付维护者不再为纯簿记问题反复消耗整轮执行时间。门禁的判定标准没有任何放松。

### Measurable Objectives

- 证据包存在断言错误时，`kc evidence check` 在一次运行中列出全部失败项，退出码为 1。
- 同一份证据包，自检与门禁的断言判定结论完全一致，没有"自检绿、门禁红"的情形。
- 门禁失败信息中不再出现"只报第一条、下一轮才发现第二条"的情形：一次失败信息包含全部未通过项。
- 现有证据相关测试全部通过；旧证据包（无 `stdout_assertions`）的行为不变。

## 2. Human Review Map (介入与风险地图)

> 决策状态：用户已于 2026-10-30 在对话中确认两项均采用推荐项（见 Change Log 的"人工确认两项决策"条目）。PRD 内的选项框由人持有，执行 Agent 不改写。

### 决策一：本地自检是否允许比门禁更短的超时

推荐允许 `--timeout` 调小：门禁固定 300 秒是为了防止卡死，而 Agent 迭代时等满 300 秒没有收益；调小只会让自检更早失败，不会放过门禁会拦的东西。风险是 Agent 用很小的超时得到"超时失败"，误以为证据有错——因此自检输出必须写明"这是本地超时，门禁上限为 N 秒"。

**请确认：** 是否允许 `kc evidence check --timeout <秒>` 只用于本地快速迭代，且不得大于配置上限？

**验收：** 用 `--timeout 1` 跑一条需要数秒的题目，输出标注本地超时并提示门禁上限；不传该参数时行为与门禁完全一致。

- [ ] 确认：允许 `--timeout` 调小，且不得大于配置上限（推荐）
- [ ] 不允许：自检必须与门禁使用同一超时值，不提供该参数

### 决策二：门禁失败改为一次报全部

现状是遇到第一条失败就抛错。改成收集全部失败会让一次失败信息更长，但能消除"每轮只发现一条、反复烧整轮"的浪费。风险是失败详情变长后，Console 与 Issue 评论的可读性下降，需要保留逐条标题、限制每条的证据片段长度。

**请确认：** 是否接受门禁失败信息从"第一条失败"改为"全部失败项的聚合摘要"？

**验收：** 构造两条不相干失败（一条断言不匹配、一条超时），门禁一次报出两条，各自带命令与失败类别。

- [ ] 确认：门禁聚合报错，接受信息变长（推荐）
- [ ] 保持现状：门禁仍只报第一条失败

**自动门禁，不需要逐项人工审阅**：新增子命令的单元与集成测试、`just test`、`just lint`、文档与随包 skill 同步检查、既有证据相关测试回归。

**本次明确不涉及**：无前端界面改动、无数据库结构变更、无迁移、不改门禁的通过判据、不改证据目录的 `.gitignore` 策略。

## 3. Usage And Impact After Implementation

| 角色 | 变化 |
|---|---|
| 执行 Agent | 新增 `kc evidence check`：提交前在 worktree 内运行，得到逐项结论与可执行的修改提示。此前只能提交后等门禁。 |
| 交付维护者 | 看到的是聚合后的失败摘要，能一次判断是证据问题还是实现问题；不再出现"每轮一条"的排障循环。 |
| CI / 自动化 | 门禁行为不变（仍然阻断），只是失败信息内容变长；`--json` 可用于脚本消费。 |
| 普通 CLI 用户 | 未使用结构化证据的场景不受影响；新子命令在 `kc --help` 中可见。 |

向后兼容：没有 `evidence.json` 或没有 `stdout_assertions` 的旧证据包，自检只校验退出码，与门禁现状一致。

## 4. Requirement Shape

- **actor**：执行 Agent（主要）、交付维护者、CI 脚本。
- **trigger**：Agent 写完证据与 manifest，准备提交或请求评审之前；或维护者排查门禁失败。
- **expected behavior**：一条命令复跑证据命令、核对断言、汇总全部结论并给出可执行的诊断；退出码与 `--json` 可被脚本消费。
- **explicit scope boundary**：只读判定不改证据；不做自动修复；不改门禁通过判据；不做界面。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**现有最接近的代码路径**

- 门禁复跑：`src/backend/core/use_cases/agent_runner_validation.py` 的 `ensure_validation_commands_pass`（遍历 manifest 每个 item、用 `bash -lc` 在 worktree 内复跑、按 `validation.reexecute_timeout_seconds` 超时、调用 `_validate_stdout_assertions` 判定）。
- 断言判定：同文件 `_is_stdout_assertion_satisfied` / `_validate_stdout_assertions`，语义是子串匹配，`must_match=false` 表示禁止出现。
- manifest 解析与格式校验：`src/backend/core/use_cases/agent_runner_structured_evidence.py` 的 `load_evidence_manifest`（含 version=1、必填字段、`stdout_assertions` 结构校验）。
- 证据目录解析：`agent_runner_validation.py` 的 `resolve_issue_evidence_dir` / `evidence_dir_path`；配置项在 `src/backend/core/shared/models/agent_runner_config.py` 与 `src/backend/infrastructure/config/agent_runner_settings_models.py`。
- CLI 接线：`src/backend/api/cli_typer_app.py` 注册子 app，各子命令文件形如 `src/backend/api/cli_typer_*.py` 用 `@app.command(...)` 定义；命令树由 `kc schema --json` 自省。
- Agent 侧口径：`agent_runner_structured_evidence.build_structured_evidence_prompt_suffix` 生成证据写作提示；随包 skill 在 `src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md`；说明文档在 `docs/guides/agent-runner.md`（`stdout_assertions` 小节）与 `docs/guides/prd-standard.md`。

**架构约束**

- 依赖方向必须是 `api → core → engines → infrastructure`；新命令只能放在 `src/backend/api/`，判定逻辑放 `src/backend/core/use_cases/`，不得反向引用。
- 判定必须复用现有函数，不得复制一份断言逻辑——复制正是本次事故的成因。
- 单文件非空行不超过 1000 行；新模块各自独立，不塞进已有超大文件。

**frontend impact**：No frontend impact — 这是命令行工具，不改任何前端页面、路由或 API 契约。

**已有 PRD 关系**：检查 `tasks/pending/` 九个 PRD，无重复或依赖关系；本 PRD 独立可开工。触发事故的 M13 评测基线 PRD（Issue #334）只提供动机，不构成序列依赖。

## 6. Recommendation

**Recommended Approach**

1. 在 `src/backend/core/use_cases/` 新增一个自检编排模块：给定 worktree 与证据目录，复用 `load_evidence_manifest` 与 `_validate_stdout_assertions` 的同源判定，**收集**每个 item 的结果（通过／格式错误／超时／非零退出／断言未命中），返回结构化结果列表。
2. 在 `src/backend/api/` 新增 `evidence` 子 app 与 `check` 命令：解析参数、调用编排、渲染人读表格或 `--json`，按结果设置退出码。
3. 把 `ensure_validation_commands_pass` 的"第一条失败即抛"改为"收集全部失败后抛聚合错误"，并复用同一份结果结构，使自检与门禁输出同源。
4. 更新 `build_structured_evidence_prompt_suffix`：把 `stdout_assertions` 从"可选填"改为"声明了就必须先自检通过"，并写明单条命令 300 秒上限与自检命令。
5. 同步 `docs/guides/agent-runner.md`、`docs/guides/prd-standard.md` 与随包 `kedacode-operator` skill。

**为什么适配当前架构**：门禁已经具备全部判定能力，缺的只是"可提前调用"和"聚合报错"。复用现有函数即可，无新增存储、无新服务、无新状态机。

**拒绝的冗余抽象**：不做独立的 `scripts/check_evidence.py` 模板分发（第二份实现必然漂移）、不做证据校验服务、不为自检引入新的配置段。

### Proposed Solution Summary (实现机制)

核心机制：**同一判定代码的两个调用点**。判定逻辑留在 `agent_runner_validation.py`，新增的编排模块只负责"逐项跑、收集结果、不抛异常"，自检命令与门禁都调用它。输入由 Agent 提供的 `evidence.json` 决定（系统不推断、不生成断言）。接入点是 CLI 子命令与门禁调用处。主要变化是新增一条命令、门禁失败信息聚合、以及 Agent 提示词口径。刻意避免的是：新增存储、复制判定逻辑、改动证据格式、改动通过判据。

**Alternatives Considered**

- *分发一份独立自检脚本*：Agent 拿到的是脚本副本，与门禁实现各自演化，必然再次出现"两边判定不一致"。这正是 #334 的成因（PRD、manifest、脚本、落盘文本四处手写同一句话）。
- *让 pre-PR reviewer 负责核对证据*：证据目录被 `.gitignore` 忽略，reviewer 看不到；且反馈延迟在每轮二十分钟量级。
- *关掉门禁复跑*：等于放弃"Agent 自己写的证据不可信"这条防线，不可接受。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

1. 解析参数：worktree 默认为当前目录；证据目录按显式参数 → `tasks/evidence/` 下含 `evidence.json` 的唯一子目录 → 报错列出候选 的顺序解析。
2. 加载 manifest：调用 `load_evidence_manifest`；格式错误转成一条"格式错误"结果，**不执行任何命令**。
3. 逐项复跑：与门禁相同的 `bash -lc` + worktree cwd + 超时；异常分类为超时、非零退出、断言未命中。
4. 断言判定：调用同源函数；未命中时对 stdout 逐行做相似度比较，输出最接近的一行（限长）。
5. 聚合：所有 item 都跑完再返回；任一项未通过即整体失败。
6. 渲染：人读表格 + 结论行；`--json` 输出结构化结果。退出码 0/1/2。

门禁侧：`ensure_validation_commands_pass` 复用同一编排，收集完毕后若有失败则抛出包含全部条目的 `ValidationEvidenceError`；缓存写入规则不变（仅全通过才写）。

### Change Impact Tree

```text
src/backend/
├── api/
│   ├── cli_typer_app.py                  # 注册 evidence 子 app
│   └── cli_typer_evidence.py             # 新增：check 命令、参数、渲染、退出码
├── core/use_cases/
│   ├── agent_runner_evidence_check.py    # 新增：编排 + 结果模型 + 聚合渲染
│   ├── agent_runner_validation.py        # 改：门禁改为收集全部失败后聚合抛出
│   └── agent_runner_structured_evidence.py  # 改：prompt 口径与超时说明
docs/
├── guides/agent-runner.md                # 改：自检命令与聚合报错说明
└── guides/prd-standard.md                # 改：证据写作要求指向自检
src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md  # 改：CLI 表面同步
tests/
├── test_evidence_check_cli.py            # 新增：命令、退出码、--json、collect-all
└── test_agent_runner_validation.py       # 改：门禁聚合报错断言
```

### Risk Classification Register

| 变更点 | Tier | 理由 | Oracle |
|---|---|---|---|
| 自检与门禁判定同源 | R2 | 判据不一致会产生"本地绿、门禁红"，且会隐蔽地放过错误证据 | rv-1 |
| 聚合报错（自检与门禁） | R1 | 只影响失败信息完整性，不改变是否阻断 | rv-2、rv-3 |
| 格式错误不执行命令 | R1 | 影响是否有副作用，局部可验证 | rv-4 |
| 子命令接线、退出码、`--json` | R1 | 新入口，需真实 CLI 调用验证 | rv-5 |
| 文档与提示词口径 | R0 | 仅文字 | 文档检查 |

### Executor Drift Guard

- 若实现时发现 `_validate_stdout_assertions` 被移动到其他模块，改为从新位置导入，**不得复制**一份断言逻辑。
- 若门禁的失败消息被现有测试按固定字符串断言，更新这些测试而不是保留两条输出路径。
- 不要为"`--timeout` 校验"新增配置项；上限直接读 `validation.reexecute_timeout_seconds`。

### Flow or Architecture Diagram

```text
agent ──> kc evidence check ──┐
                              ├──> evidence_check 编排 ──> load_evidence_manifest
delivery gate (pre-commit) ───┘                        ──> 复跑命令（同超时）
                                                       ──> _validate_stdout_assertions
                              └──> 结果列表 ──> 表格 / --json / 聚合异常
```

### ER Diagram

本次无数据模型变更。

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 自检与交付门禁对同一份证据包给出相同的断言判定结论
  reviewer: verifier
  real_entry: "kc evidence check --evidence tasks/evidence/<fixture-stem> 对一份断言不匹配的 fixture 运行，随后用同一 fixture 走 tests 内的门禁加载路径"
  expected: "自检报出断言未命中并给出期望串；门禁在同一 fixture 上同样报断言未命中，两边命中的 pattern 与 item 序号一致"
  mock_boundary: "fixture 证据目录与其中的探测脚本是测试夹具；manifest 解析、复跑、断言判定全部走真实实现"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "fixture 中 evidence.json 的 stdout_assertions 原文，以及两条路径各自报告的失败 pattern 字符串"
  must_cross: "CLI 参数解析 -> 编排 -> 真实 bash 复跑 -> 断言判定 -> 结果渲染；门禁侧为 pre-commit 加载路径 -> 同一编排"
  forbidden_bypasses: "禁止在测试里复制一份断言比较；禁止为测试添加只在测试下生效的判定分支；禁止断言只比较退出码"
  fresh_state_probe: "门禁侧在新进程中加载同一 fixture 并比对失败 pattern，不复用自检进程的内存结果"
  final_tree_evidence: "证据来自最终提交树上的 CLI 运行；改实现后必须重跑"
  negative_control: "把自检编排暂时改成只判退出码（不调用断言判定），同一 fixture 应变为通过，证明该断言有判别力"
  expected_fail: "自检对断言不匹配的 fixture 报通过、退出码 0，而门禁仍报红，两者结论不一致"
- id: rv-2
  behavior: 断言错误一次列全，而不是只报第一条
  reviewer: verifier
  real_entry: "kc evidence check --evidence tasks/evidence/<fixture-stem>，fixture 内含三个题目各写错一条断言"
  expected: "输出三条断言失败，分别带 item 序号与期望串，退出码 1"
  mock_boundary: "fixture 证据与命令为夹具；判定与渲染为真实实现"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  negative_control: "把 fixture 改回全部正确，同一命令输出 0 条失败、退出码 0"
  expected_fail: "只输出一条失败，其余两条被跳过"
- id: rv-3
  behavior: 单题超时不中断其余题目，且被明确标为超时
  reviewer: verifier
  real_entry: "kc evidence check --timeout 1 --evidence tasks/evidence/<fixture-stem>，fixture 中一题命令 sleep 5、其余题目立即返回"
  expected: "该题标为本地超时并提示门禁上限，其余题目正常给出结论，退出码 1"
  mock_boundary: "sleep 为夹具命令；超时控制与结果分类为真实实现"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  negative_control: "去掉 --timeout 改用门禁默认值，该题应通过，证明超时来自本地参数"
  expected_fail: "一题超时后整个命令中断，其余题目没有任何结论"
- id: rv-4
  behavior: manifest 格式错误时不执行任何证据命令
  reviewer: verifier
  real_entry: "kc evidence check --evidence tasks/evidence/<fixture-stem>，fixture 的 evidence.json 缺少必填字段 risks"
  expected: "逐条列出格式错误、退出码 1；fixture 里的探针脚本没有产生任何执行记录"
  mock_boundary: "探针脚本写文件作为执行痕迹，这是夹具；解析与错误渲染为真实实现"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  negative_control: "补齐 risks 后同一命令进入正常的逐项判定流程"
  expected_fail: "格式错误的同时仍执行了探针，留下执行痕迹"
- id: rv-5
  behavior: 正确证据包通过自检并以机器可读形式输出
  reviewer: verifier
  real_entry: "kc evidence check --evidence <真实仓库证据目录> --json"
  expected: "退出码 0；JSON 含每个 item 的通过状态与汇总计数，可被 python -c 'import json,sys; json.load(sys.stdin)' 解析"
  mock_boundary: "使用仓库真实证据目录；无外部服务依赖"
  tier: R1
  test_layer: e2e
  required_for_acceptance: true
  negative_control: "把其中一条断言改错后重跑，退出码变为 1 且 JSON 中该 item 状态为失败"
  expected_fail: "退出码 0 但 JSON 不可解析，或失败时仍返回 0"
```

失败排查顺序：先看证据目录解析到的具体路径，再看 manifest 是否被解析成预期条数，再看单条命令的退出码与耗时，最后看断言串与实际 stdout 的差异。

### Low-Fidelity Prototype

不需要：输出是终端文本，验收以真实命令输出为准。

### Interactive Prototype Change Log

No interactive prototype file changes in this PRD.

### External Validation

未使用外部检索；全部结论来自仓库内代码与 Issue #334 记录。

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 独立交付；Issue #334 只是触发事故，不构成序列依赖。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

本次没有 `reviewer: human` 的 oracle：交付物是命令行工具，人看终端输出与看测试结论没有区别。所谓"人读"部分只剩 §2 的两个决策，已在人审层以勾选框形式给出。

不计入人读呈递的 verifier 分组：CLI 行为与退出码（rv-1 至 rv-5）、既有测试回归、文档与 skill 同步检查。

### 9.2 Acceptance Evidence Package

#### Behavior Acceptance

- [ ] `kc evidence check` 对断言不匹配的 fixture 报出断言失败并给出期望串与实际最接近输出行（rv-2）
- [ ] 三处断言错误在一次运行中全部列出，退出码 1（rv-2）
- [ ] 单题超时被标注且其余题目继续给出结论（rv-3）
- [ ] manifest 格式错误时不执行任何证据命令，探针无执行痕迹（rv-4）
- [ ] 正确证据包通过自检，`--json` 输出可被标准 JSON 解析器读取（rv-5）
- [ ] 旧证据包（无 `stdout_assertions`）自检行为与门禁一致：只校验退出码

#### Architecture Acceptance

- [ ] 新命令实现位于 `src/backend/api/`，判定与编排位于 `src/backend/core/use_cases/`，依赖方向未被反向引用
- [ ] 断言判定调用现有函数，仓库内仍只有一处实现（`rg -n "_validate_stdout_assertions\|_is_stdout_assertion_satisfied" src/backend` 只命中定义与其两个调用点）

#### Validation Acceptance

- [ ] 自检与门禁对同一 fixture 的失败 pattern 与 item 序号一致（rv-1），负控证明该断言有判别力
- [ ] 门禁失败信息一次包含全部未通过项（决策二验收）
- [ ] `--timeout 1` 的本地超时输出标注门禁上限，不传参时行为与门禁一致（决策一验收）
- [ ] `just test` 通过；既有证据相关测试全绿

#### Documentation Acceptance

- [ ] `docs/guides/agent-runner.md` 增加自检命令、collect-all 与超时说明
- [ ] `docs/guides/prd-standard.md` 的证据写作要求指向自检命令
- [ ] 随包 `kedacode-operator` skill 同步新子命令与用法
- [ ] `kc schema --json` 能自省到 `evidence check` 及其参数

#### Delivery Readiness

- [ ] PRD 已移至 `tasks/archive/`，§9 除 Human-Confirmed 外全部为 `[x]` 或 `[~]`
- [ ] 独立 verifier 结论为 PASS
- [ ] 交付说明中给出自检命令的一条可复制用法

### Human-Confirmed (来自 Part A 风险地图)

- [ ] §2 决策一：确认允许 `--timeout` 调小且不得大于配置上限（或选择另一种方案）
- [ ] §2 决策二：确认门禁聚合报错、接受失败信息变长（或选择保持现状）

## 10. Functional Requirements

- **FR-1**：本地自检入口——新增 `kc evidence check`，在指定 worktree 内按证据目录解析规则加载 `evidence.json` 并复跑全部证据命令。
- **FR-2**：判定同源——manifest 解析、命令复跑超时、断言判定必须调用交付门禁已使用的同一批函数；仓库内不得出现第二份断言实现。
- **FR-3**：一次报全——任一题目失败不中断其余题目；全部跑完后一次性返回所有结论。
- **FR-4**：断言可诊断——断言未命中时输出期望子串，并给出实际输出中最接近的一行作为对照。
- **FR-5**：退出码与机器输出——0 全通过、1 有失败、2 用法错误；`--json` 提供稳定的结构化结果。
- **FR-6**：口径收紧——证据写作提示要求"声明了 `stdout_assertions` 就必须先自检通过"，并写明单条命令 300 秒上限与自检命令。
- **FR-7**：门禁聚合与文档同步——门禁改为收集全部失败后聚合抛出；`docs/` 与随包 skill 同步更新。

## 11. Non-Goals

- 不做 Web/Console 界面的自检结果展示。
- 不把 `tasks/evidence/**` 纳入版本控制，不改变现有 `.gitignore` 策略。
- 不自动生成或自动修复断言；不替执行 Agent 决定"应该断言什么"。
- 不改变门禁的通过判据，不关闭复跑，不引入新的配置段。
- 不覆盖前端视觉效果类证据的检查（截图是否存在由现有门禁规则负责）。

## 12. Risks And Follow-Ups

- **聚合报错可能撑大 Issue 评论**：单条证据片段需限长（沿用现有失败详情截断口径），风险可接受。
- **自检与门禁仍可能因环境差异不一致**（例如网络、本机端口占用）：自检不能替代门禁，文档需明确"自检通过不等于交付通过"。
- **后续跟进**：若日后出现第二个消费方（例如 PR 提交前钩子），再考虑把编排抽成可复用服务，本次不做。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 自检入口形态 | 新增 `kc evidence check` 子命令 | 分发独立脚本 / 让 Agent 自拼命令 | 只有与门禁共用代码才不会出现两边判定漂移，#334 的四处手写正是反例 |
| D-02 | 证据目录解析 | 显式参数优先，缺省在 `tasks/evidence/` 唯一子目录 | 要求必填路径 / 递归猜测 | 常见场景无需参数，歧义时报错列候选而不是猜 |
| D-03 | 超时策略 | 默认取门禁配置，允许 `--timeout` 调小且不超过上限 | 固定与门禁同值 / 完全自由设置 | 迭代需要快速反馈，但不得比门禁更宽松 |
| D-04 | 门禁失败粒度 | 聚合全部失败 | 保持第一条失败即停 | 现状导致每轮只能发现一条，浪费整轮执行时间 |
| D-05 | 是否自动修复断言 | 不修复，只报告 | 自动改写断言使其匹配输出 | 自动改断言会把"证据错误"变成"证据永远正确"，摧毁判据 |

### Final Reconciliation

待归档时填写：逐项核对 §9 与 §7.6 的对应关系、记录最终验证树与 verifier 结论。

## Change Log

### 初版 PRD
- Type: scope
- Before: 无（新建）
- After: 建立 `kc evidence check` 本地自检入口、门禁聚合报错、提示词与文档同步的范围
- Reason: Issue #334 连续七轮失败中六轮为证据簿记问题，Agent 缺少本地自检手段
- Impact: 新增 CLI 子命令与自检编排模块；门禁判据不变，仅改失败信息聚合
- Review: 待人工确认 §1 解读与 §2 两个决策

### 人工确认两项决策
- Type: scope
- Before: §2 决策一、决策二未确认
- After: 用户在对话中确认决策一＝允许 `--timeout` 调小且不得超过配置上限；决策二＝门禁聚合全部失败项
- Reason: PRD 首轮人工确认，两项均采用推荐项
- Impact: 实施口径确定，无需再就这两点征求意见
- Review: 用户已确认（对话记录 2026-10-30）
