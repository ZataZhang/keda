# PRD: Agent Runner 威胁模型与确定性攻击回归

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ⬜ **验收状态**：未开工。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

> 本 PRD 分两个 altitude，分别服务不同读者，自上而下阅读：
>
> - **Part A · 人审层 (Review Layer)** — 需求方 / 验收人读这部分，确认安全基线要回答的问题、公开边界和验收结果。
> - **Part B · 执行器层 (Build Layer)** — 实现者读这部分，按现有入口补齐威胁模型、确定性回归和验证证据。

## Feature Overview (功能一览)

> 本节是 §10 Functional Requirements 的白话投影；行为验收以 §1 的行为样例为准。

- **盘点八类信任边界**（FR-1）：列出 Agent Runner 涉及的不可信输入、凭据、权限、文件、网络、Console、本地记忆和自动合并。
- **统一状态与证据字段**（FR-2）：逐类记录资产、攻击者可控输入、已有控制、证据状态、定性严重度和覆盖边界。
- **限制公开披露**（FR-3）：公开材料只展示架构、控制、缺口类别、状态和严重度；未修复问题的具体复现留在公开仓库之外。
- **穿过真实执行边界验证**（FR-4）：离线场景经真实 Runner、Git 与本地 HTTP 入口，只替换必要的外部命令。
- **让缺口状态随行为变化而失败**（FR-5）：回归锁定已登记的当前行为；行为变化时测试失败，要求同步审阅状态。
- **明确未测试范围**（FR-6）：真实模型抵御注入、外网隔离和真实 GitHub 合并不由模拟结果代替。
- **使用现有 pytest / CI 路径**（FR-7）：不接入真实凭据，新增测试进入现有测试收集范围。
- **让文档在站点可发现**（FR-8）：安全模型进入 MkDocs 导航并通过严格构建。
- **保持产品范围受限**（FR-9）：只建立模型与证据，不改变生产行为或数据结构。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

Agent Runner 会把外部 Issue、评论、日志和仓库指令交给有本机执行能力的 Agent。安全控制已经分布在 Agent 启动参数、子进程环境、提交与发布检查、Console 路由和记忆晋升逻辑中，但仓库没有一份把这些边界、现有控制、未覆盖部分和回归证据连起来的安全模型，也没有专门的离线安全场景套件。

代码和测试显示，安全控制分布在 Agent 启动、进程环境、文件变更、Console 路由和记忆晋升等位置，现有测试分别覆盖了其中一部分。本 PRD 会在执行时重新核对覆盖范围；公开材料按已选披露边界只呈现控制类别、状态与证据范围，不公开未修复问题的具体行为或复现细节。

### Interpretation (解读回显)

这是本 PRD 的前置审核对象。下面仍然成立的行为行会成为 §7.6 的验收 oracle；修改任一行的期望结果，就是修改验收标准。

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | 打开公开安全模型，逐项查看八类攻击面及其状态。 | 每类都能看出信任边界、已有控制和证据状态；未测试项不会被写成“已验证”，公开页不含未修缺口的复现步骤。 |
| 🤖 自动验证 | 在隔离临时状态中通过真实 Runner 入口运行合成场景。 | 记录能说明所选边界的实际观察；没有读取真实凭据，也没有调用真实模型或 GitHub。 |
| 🤖 自动验证 | 对一项当前登记为“已知缺口”的行为运行回归。 | 测试观察到与公开登记一致的当前行为并通过；若产品行为后来改变，断言失败，要求把登记状态和测试期望一起改为“已防护”。 |
| 🤖 自动验证 | 从独立客户端向本地 Console 发送合成来源请求。 | 真实本地 HTTP 服务的观察与登记状态一致；该结果只覆盖实际测试的类别。 |
| 🤖 自动验证 | 由后续任务读取已晋升技能。 | 回归能区分持久化前后的状态；不会只根据原进程中的对象声称持久化成功。 |
| 🤖 自动验证 | 回归桩收到未声明的外部命令或输入形状。 | 桩以非零退出并报告未识别输入；不会用默认成功掩盖未覆盖路径。 |

**我默默定了这些**

- **本期只建立模型与证据，不修复产品控制**：按你上一轮选择的推荐项，任何确认后的缺口都登记为待修项，由各自的后续 PRD 决定修复。
- **攻击模拟方式**：假设 Agent 已被不可信内容说服，用脚本化可执行文件测 Keda 的外围控制；不让真实模型替 Keda 的边界背书。
- **攻击面覆盖**：模型记录八类边界；确定性回归只覆盖可离线验证的子集。真实模型易受提示注入的程度、网络隔离和真实合并链路在本期标为“未测试”。
- **已知缺口状态**：测试直接断言当前观察到的行为，不把用例永久跳过或默认容忍意外通过。行为翻转时测试失败，直到维护者同步改状态与预期。
- **严重度口径**：公开页采用高 / 中 / 低的定性分级，依据潜在影响和信任边界，不称为 CVSS 评分。
- **文件变更保护现状**：代码已有文件边界检查。本 PRD 会核对其覆盖范围，但公开材料不列出未修复问题的具体路径或操作细节，也不增补规则。
- **路线图范围**：不改 ROADMAP。安全 PRD 与 M13 的确定性回归可以独立实施；真实模型重复评测是依赖 M13 方差结论的后续事项。

**我理解为不做**

- 本 PRD 不改变 Agent 权限、沙箱、网络、环境变量、Console 鉴权、禁改路径、自动晋升或自动合并行为。
- 本 PRD 不判断真实模型能否识别恶意 Issue、评论、日志或仓库指令。
- 本 PRD 不公开未修复问题的具体端点、复现载荷、敏感路径或逐步利用过程。

本 PRD 将形成一份可审查的 Agent Runner 安全基线和一组确定性外围控制回归。公开结论只覆盖明确跑过的控制；未运行真实模型、网络隔离或合并环境的部分会持续标为未测试，不得据此宣称 Keda 或其 Agent 已通过全面安全评估。

### What The User Gets

Keda 维护者得到一页公开安全模型，能快速看出每类输入可能跨过哪些边界、已有哪种控制、回归证据到哪里为止。开发者得到一条离线、可重复的攻击回归命令；它会在代码改变已登记缺口时失败，提醒维护者更新安全状态，不需要模型额度或真实凭据。

### Measurable Objectives

- 八类攻击面都记录攻击者控制的输入、受影响资产、已有控制、证据状态和定性严重度。
- 公开安全模型只包含架构、控制和缺口类别；未修复问题的具体复现保存在公开仓库之外。
- 独立安全测试目录至少通过真实 Runner / Git / 本地 HTTP 中与目标类别对应的入口验证权限参数、环境、文件变更、Console 来源和技能持久化边界。
- 每个“已知缺口”都有稳定 ID 与可观察的当前行为断言；代码行为变化而登记未更新时，测试退出码非零。
- 安全测试全程使用合成值和本地临时状态；不读取真实密钥、不调用模型、不访问 GitHub。
- 公开文档和安全测试不修改生产行为、配置默认值或数据库结构。

## 2. Human Review Map (介入与风险地图)

上一轮决策页中的六项范围选择已按你的回复收敛。本节只保留后来才发现、代码和仓库规范无法替你决定的事项。

### 私有缺口记录是否只保存在本机忽略目录

仓库没有已登记的私有安全问题跟踪库；现有本机状态目录已经由 Git 忽略。推荐把详细复现保存在该目录下的私有安全记录文件，公开页只保留不含利用信息的类别和状态。这样没有外部服务与权限配置，但记录不会随 Git 同步或自动备份。若以后需要多维护者共同处理，届时迁到专用私有跟踪库。

**请确认：** 是否同意本期将详细缺口记录放在本机忽略目录下的私有文件，并接受它不自动跨机器同步？

**验收：** 公开页面和 Git 变更中不出现私有记录里的复现内容；本地私有记录不进入 Git，公开目录只留下缺口类别、严重度、状态和不透明编号。

### 自动门禁，不需要逐项人工审阅

- 真实入口安全场景使用临时仓库、合成环境值、脚本化 Agent 和 fail-loud 外部命令桩。
- 已登记的已知缺口测试断言当前观察；行为与登记不一致时失败，保护状态不会静默过期。
- 外部网络、真实模型抗注入和 GitHub 合并不会被模拟结果冒充为已验证。
- 新页面经过 MkDocs 严格构建；新增测试进入现有 CI 的 tests/ 全量收集。

### 本次明确不涉及

不修改生产代码、配置、API、CLI、前端、数据库或 CI 工作流；不修复威胁模型识别出的缺口；不增加模型、网络或真实合并服务调用。

## 3. Usage And Impact After Implementation

### Keda 维护者

维护者通过开发者文档中的“Agent Runner 安全模型”页查看边界与当前证据；在配置、调用方式、输入信任或记忆晋升逻辑改变后更新对应类别。需要研究未修缺口时，从本机忽略的私有记录查看详细复现；公开 PR 只引用不透明编号和类别。

### 代码贡献者与 PR 审阅者

贡献者可在提交前运行安全场景套件，或等 CI 自动运行。审阅者看到公开的控制状态与类别级缺口，以及测试是否与状态一致；不需要依靠公开复现步骤理解当前安全结论。

### Agent Runner 使用者与操作人员

Issue 执行、Console、配置、自动合并和报告行为均保持不变。用户不会看到新增页面或设置，也不会因本 PRD 的安全测试改变运行权限。

### 兼容性与迁移影响

没有 API / CLI / 配置契约变化，不迁移数据。测试仅创建并销毁本地临时 Git 仓库、临时 HOME 与临时 Console 进程；不触碰真实仓库、密钥或状态目录。

## 4. Requirement Shape

- **Actor：** Keda 维护者、代码贡献者和 PR 审阅者。
- **Trigger：** 维护者需要回答“Agent Runner 当前有哪些信任边界、哪些控制已有证据、哪些类别仍待验证”，或代码变更影响这些边界。
- **Expected behavior：** 公开文档呈现八类攻击面及证据状态；离线回归穿过真实 Runner / Git / 本地 HTTP 边界，明确区分已防护、已知缺口和未测试。
- **Scope boundary：** 只测量和记录，不改生产控制；未修复缺口的逐步复现留在私有记录；模型抵御注入、外网隔离和真实 GitHub 合并不属于本期验收。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### 当前入口与可复用路径

- Agent 调用：真实入口为 pyproject.toml 注册的 kc CLI；单目标执行路径是 kc run --issue。命令参数由 Agent invocation 路径构造，子进程由 infrastructure/process_runner.py 启动。
- 子进程环境：src/backend/infrastructure/child_env.py 的 build_sanitized_child_env() 统一构造 Agent 与工具进程环境；E2E 验证进程另有默认关闭的白名单路径。
- 文件保护：src/backend/core/use_cases/agent_runner_commit.py 在提交时筛选禁改路径；src/backend/core/use_cases/agent_runner_publish.py 暴露共用路径判断，发布和合并队列也会检查。
- Console：src/backend/api/routes/agent_runner_console*.py 承接本地 Console API；src/backend/api/routes/agent_runner_console_runtime.py 对桌面小窗状态变更动作校验当前 loopback 来源。该控制不能推导其他写操作也有同等保护。
- 技能记忆：src/backend/core/use_cases/agent_runner_skill_distillation.py 触发草稿晋升；src/backend/core/agent/memory/skill_distillation.py 判断阈值；后续任务在 src/backend/core/agent/memory/memory_loader.py 读取已晋升技能。

### 现有测试与应复用的证据

- tests/test_agent_runner_agent_invocation.py 与 tests/test_process_runner.py 覆盖部分调用参数和进程环境行为。
- tests/test_agent_runner_commit.py 覆盖提交时禁改路径检查；tests/test_desktop_companion.py 覆盖已有桌面小窗 Origin 保护。
- tests/test_agent_runner_skill_distillation.py、tests/test_agent_runner_memory.py 覆盖现有技能晋升和记忆读取逻辑。
- 本 PRD 增加的是跨边界安全场景，不重写这些单元测试，也不复制一套通用测试框架。具体写之前先确认现有断言是否已经覆盖目标场景，缺什么才新增什么。

### 架构约束

- 不新增 src/backend/ 生产模块或修改运行行为；新增逻辑只在 tests/security/。
- 后端仍遵守 api → core → engines → infrastructure 的依赖方向。安全测试从真实 CLI / HTTP 入口启动，不能绕过入口直接调用被测用例。
- Agent 子进程、Git、本地 Console HTTP 服务和本机记忆存储是真实边界；只替换外部 Agent CLI 与 GitHub CLI。未知参数必须使桩失败。
- **No frontend impact**：不改 frontend-admin/ 或 frontend-public/，本 PRD 不添加用户交互界面。
- **No data model changes in this PRD.**

### 相关 PRD

- tasks/pending/P1-FEAT-20261019-140531-m13-golden-eval-baseline.md：当前确定性安全测试不依赖 M13 harness，可独立交付。真实模型的易受攻击程度测试暂不纳入；未来需等 M13 重复运行和方差口径稳定后另行决策。
- tasks/archive/P1-FEAT-20260911-010513-agent-cli-adapter-layer.md：已定 Agent profile 与 argv 构造边界；本 PRD 观察现状，不重做适配器决策。
- tasks/archive/P1-FEAT-20260626-093933-agent-runner-memory-persistence.md：已定技能草稿、阈值晋升和 prompt 读取行为；本 PRD 只把它们纳入安全威胁模型，不改变晋升策略。
- tasks/archive/P1-FEAT-20260703-105322-autopilot-merge-queue-fast-profile.md：自动合并仍沿用原有授权和 GitHub 分支保护；本 PRD 只记录该边界，不引入真实合并测试。

ROADMAP 当前没有对应安全里程碑；按此前讨论，本 PRD 不修改 ROADMAP，也不把安全工作并入 M13。

## 6. Recommendation

### Recommended Approach

新增一页安全模型和一个小型 tests/security/ 场景套件。模型表覆盖八类攻击面，逐项记录攻击者控制的输入、受影响资产、代码已有控制、证据状态与定性严重度。公开页只呈现架构、控制、缺口类别和状态；本机忽略目录保存确有必要的详细复现，公开记录只使用不透明编号。

测试以真实 KedaCode 入口为边界：为每个场景建临时 HOME 与临时 Git 仓库，启动真实 kc run 或 kc console，本机真实执行 Git 和 HTTP。仅将外部 Agent 替换为会记录探针的脚本，将 GitHub CLI 替换为只接受已知 argv 的 fail-loud 桩。所有外部凭据使用合成标记，不连接模型或 GitHub。

“已知缺口”测试直接断言当前观察到的行为，并以登记编号关联公开类别。若实现后来改变，断言随之失败；修复该类别的独立 PRD 必须把状态改成“已防护”、更新断言并重新验证。不要用 skip 或非严格 xfail 让缺口静默消失。测试通过只证明登记状态与观察一致，不代表缺口已修复。

这个规模已经覆盖目标证据：一个公开清单、一个本机私有细节文件和复用现有 pytest / Git / CLI 测试约定的场景组。新的安全测试框架、模型评测服务、外网沙箱、数据库或 CI 工作流都没有独立收益依据。

### Alternatives Considered

| 方案 | 决定 | 原因 |
|---|---|---|
| 在这份 PRD 中同时修复所有当前缺口 | 不采用 | Agent 权限、环境变量、Console 来源控制、文件策略和自动晋升都是各自的 R3 产品取舍；合并修复会扩大回滚面，也与已选的“先度量”范围冲突。 |
| 公开详细利用步骤以最大化透明度 | 不采用 | 用户已选择未修缺口只公开类别与严重度；可公开审查控制和方法，修复后再发布具体细节。 |
| 让真实模型反复处理恶意 Issue | 暂缓 | 结果是概率事件且消耗真实模型额度；在 M13 方差口径确认之前，不能可靠区分控制退化与模型波动。 |
| 新建通用攻击框架或独立安全服务 | 不采用 | 本期是本地 Runner / API 行为验证，现有 pytest、临时 Git 仓库和 CLI 足够承载，不需要新服务边界。 |

### Proposed Solution Summary (实现机制)

- **核心机制：** 用一份公开威胁模型表作为可读总览，用隔离的 pytest 场景在实际 Keda CLI / 本地 HTTP 边界上观察外围控制。
- **输入来源：** 攻击面来自已有代码与文档；测试输入来自临时仓库、合成 Issue 和合成环境标记。系统不推断新的策略，只记录显式测试结果。
- **接入位置：** 新测试放在 tests/security/；公开说明放在 docs/dev/security-threat-model.md，并加入 MkDocs 的“开发者”导航；详细复现放在 .kedacode/security/private-known-gaps.md 本机忽略文件。
- **状态与输出：** 公开表项为“已防护 / 已知缺口 / 未测试”；已知缺口断言与当前实际行为不一致时测试失败，提醒维护者同步修复状态。
- **刻意不增加：** 不新增生产配置、API、CLI、数据库、扫描器、测试服务或网络隔离器；不把“已知缺口”误写成安全通过。

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### 7.1 Core Logic

1. 先复核当前八类攻击面的生产入口和已有测试。复用已有测试结论；只有缺少真实跨边界观察时才新增场景。
2. 建立本机忽略的详细记录文件。每项包含不透明 ID、攻击面、具体复现、当前可见结果、严重度依据、公开类别映射和后续处理引用。不得把这些逐步复现或敏感值复制到跟踪文件。
3. 公开安全模型只保留类别级信息：资产、攻击者控制输入类别、已有防线类别、状态、定性严重度、证据范围与明确未测试原因。安全模型中的状态表是公开状态的唯一事实源；回归用稳定类别 ID 关联状态，不维护第二份状态清单。严重度使用高 / 中 / 低，不使用 CVSS 名称。
4. 测试夹具为每个场景创建全新临时目录、临时 HOME、真实 Git 仓库和仓库配置。运行进程使用最小环境加合成标记；不从开发 shell 环境带入真实凭据。
5. 对外部 Agent 使用 PATH 注入的脚本化 CLI，按明确计划记录 argv、cwd、合成环境标记与尝试的仓库操作；桩不得打印传入的真实环境值。GitHub CLI 桩只接受测试实际需要的命令，其他 argv 非零失败。
6. 选定的文件与权限场景必须通过真实 kc run 的工作目录、进程启动和提交阶段；不能只调用 is_forbidden_path() 或参数构造函数来宣称 Runner 边界已验证。
7. Console 来源场景通过 kc console --no-browser 启动真实本机服务，再从独立 HTTP 客户端发送正常和合成跨源请求。被测 Console 业务路由不可用 TestClient 或直接函数调用替代。
8. 技能持久化场景从真实本地存储读取晋升结果，并以一个新的 Runner 进程观察下一次任务上下文。不得只检查发起晋升的进程里的对象。
9. 对当前缺口，测试对照具名当前行为；对已防护行为，测试对照拒绝结果；未测试项只出现在模型中。代码状态改变时，测试必须先失败，再在对应修复交付里更新类别状态和期望。
10. 本机 .kedacode/security/private-known-gaps.md 通过 git check-ignore 确认不会进入提交。公开页面、测试名、fixture 值、stdout/stderr 和 CI 日志均不得带真实复现步骤或凭据。

### 7.2 Change Impact Tree

~~~text
.
├── tests/
│   └── security/
│       ├── conftest.py [新增]
│       │   【总结】为安全场景提供临时 HOME、真实 Git 仓库、合成环境和 fail-loud 外部命令桩。
│       ├── test_runner_boundaries.py [新增]
│       │   【总结】通过真实 kc run 覆盖 Agent 参数、进程环境和提交文件边界的类别级观察。
│       ├── test_console_boundary.py [新增]
│       │   【总结】启动真实 kc console 服务，验证本机写操作的来源控制及已登记状态。
│       └── test_memory_persistence.py [新增]
│           【总结】验证本地技能草稿、阈值晋升及新进程 prompt 读取之间的持久化边界。
├── docs/
│   └── dev/
│       └── security-threat-model.md [新增]
│           【总结】公开列出八类攻击面、已有控制、状态和未测试范围，不放未修缺口复现。
├── mkdocs.yml [修改]
│   【总结】将 Agent Runner 安全模型加入开发者文档导航。
└── .kedacode/
    └── security/
        └── private-known-gaps.md [本机忽略文件，不进 Git]
            【总结】保留公开仓库之外的具体缺口复现和跟踪细节。
~~~

**架构适配：** 本次只扩展测试与文档，不触碰四层后端生产实现；测试通过公开 CLI / HTTP 入口观察现有分层组合，避免第二套安全策略或内部直调旁路。

### 7.3 Executor Drift Guard

- 开始实现前重新搜索环境过滤、Agent argv、禁改路径、Console 写路由、技能晋升和自动合并入口；不要沿用过时的文件保护覆盖范围描述。
- 可复制运行：`rg -n "AGENT_CHILD_ENV_DENYLIST|build_sanitized_child_env|forbidden_path_patterns|is_forbidden_path|validate_safe_changes|_trusted_console_origin|should_auto_promote|promote_draft_to_skills" src/backend config.toml tests`
- 可复制运行：`rg --files tests | rg "agent_runner_(agent_invocation|commit|skill_distillation|console)|desktop_companion|process_runner|security"`
- 已有测试可能在实现期间扩展。若某个既有测试已通过真实边界覆盖目标，不再新建等价场景；更新 Change Impact Tree 和 §7.6 oracle 后再继续。
- 不要在 PRD、公共文档、测试数据或证据中写入本机私有文件的具体内容。只允许公开不透明编号与类别。

### 7.4 Flow Or Architecture Diagram

~~~mermaid
flowchart LR
    untrusted["Issue、评论、日志与仓库指令"] --> runner["真实 kc run 与 Runner 编排"]
    runner --> process["真实子进程、环境和工作目录"]
    process --> agent["脚本化外部 Agent"]
    runner --> git["真实临时 Git 仓库与提交路径"]
    browser["独立本机 HTTP 客户端"] --> console["真实 kc console 服务"]
    console --> route["Console 写路由和来源检查"]
    memory["本地技能草稿与观测记录"] --> promotion["技能阈值晋升"]
    promotion --> next_prompt["新 Runner 进程构造后续 prompt"]
    suite["tests/security 场景套件"] --> runner
    suite --> console
    suite --> memory
    runner --> report["类别级结果与状态"]
    console --> report
    next_prompt --> report
    report --> public["公开安全模型：控制、状态与证据范围"]
    report --> private["本机忽略记录：具体复现与细节"]
~~~

### 7.5 ER Diagram

No data model changes in this PRD.

### 7.6 Realistic Validation Plan (Oracle 块)

以下是未来实现的验收 oracle；本轮只写 PRD，不执行这些命令。

```yaml
- id: rv-1
  behavior: 公开安全模型完整呈现八类攻击面和证据状态，具体未修复复现留在本机忽略记录
  reviewer: human
  real_entry: "打开 docs/dev/security-threat-model.md 与本机 .kedacode/security/private-known-gaps.md；从仓库根目录运行 git check-ignore -v .kedacode/security/private-known-gaps.md"
  expected: "公开页可见八类、已有控制、状态和定性严重度；具体复现只在本机文件；本机文件命中 Git 忽略规则且未进入 git ls-files"
  mock_boundary: "文档内容来自当前生产代码与既有测试；不使用模型生成安全结论；公开 diff 与本机私有文件均检查"
  tier: R3
  test_layer: manual
  required_for_acceptance: true
  presentation: "打开 docs/dev/security-threat-model.md；自检公开表中每项只有类别、控制、状态和严重度，本机细节文件可用 open .kedacode/security/private-known-gaps.md 查看"
  critical_value_source: "公开类别来自仓库代码与既有测试；具体复现只由维护者写入本机忽略文件"
  must_cross: "代码和既有证据 -> 本机私有记录 -> 公开类别级页面 -> 最终 Git tree 与站点导航"
  forbidden_bypasses: "不能只依赖文件名或 .gitignore 规则；必须核对 git check-ignore、git ls-files、公开 diff 与页面正文；不得把私有复现改写成可还原的公开步骤"
  fresh_state_probe: "从新 Git index 查询私有路径不在跟踪文件中，并在公开文档、测试与最终 diff 搜索合成私有标记均无命中"
  final_tree_evidence: "记录验证时最终 Git tree；修改公开文档、测试或忽略规则后重做披露检查"
  negative_control: >-
    scratch=$(mktemp);
    trap 'rm -f "$scratch"' EXIT;
    canary=$(printf '%s%s' 'SECURITY-' 'CANARY');
    cp docs/dev/security-threat-model.md "$scratch";
    printf '\n%s\n' "$canary" >> "$scratch";
    if rg -nF "$canary" "$scratch"; then
      exit 1;
    else
      scan_exit=$?;
      if [ "$scan_exit" -eq 1 ]; then exit 0; else exit "$scan_exit"; fi;
    fi
  expected_fail: "负控脚本应以非零退出码拒绝包含生成 canary 的页面副本；正常公开页面扫描应无命中且退出 0"
- id: rv-2
  behavior: 真实 Runner 进程、Git 工作目录与提交边界的观察和安全模型登记一致
  reviewer: verifier
  real_entry: "uv run pytest tests/security/test_runner_boundaries.py -q -o addopts=''"
  expected: "场景通过 kc run --repo <临时仓库> --issue <合成编号> 启动真实 Runner；已知缺口断言与具名公开状态一致，行为翻转会使断言失败；未知外部 argv 非零退出"
  mock_boundary: "真实 kc CLI、配置解析、进程启动、worktree、Git 与提交路径；只替换外部 Agent 和必要的 GitHub CLI；不使用真实仓库、token、模型或网络服务"
  tier: R3
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "Runner argv 来自临时仓库实际配置；环境标记由验证进程显式生成；Git 变更从真实临时仓库读取"
  must_cross: "kc CLI -> 配置合并 -> worktree -> ProcessRunner -> 脚本化 Agent 子进程 -> 真实 Git staging / commit / publish 前检查"
  forbidden_bypasses: "禁止 FakeProcessRunner、直接调用用例、替代配置解析、预先写入预期结果、使用当前用户 HOME 或真实远端"
  fresh_state_probe: "用新 Git 子进程检查临时仓库的文件状态、index 与提交历史；从探针 JSONL 读取 Agent 实际 argv、cwd 和合成标记状态"
  final_tree_evidence: "记录运行时测试代码的 Git tree；任何 Runner、配置、fixture 或断言改动后重跑该命令"
  negative_control: "把某个已知缺口断言临时改成相反的合成状态，再运行同一条命令"
  expected_fail: "对应场景断言失败并点名不匹配的类别编号，恢复正确的当前行为断言后通过"
- id: rv-3
  behavior: Console 来源检查和技能持久化观察穿过真实服务及新 Runner 进程
  reviewer: verifier
  real_entry: "uv run pytest tests/security/test_console_boundary.py tests/security/test_memory_persistence.py -q -o addopts=''"
  expected: "测试启动临时配置下的 kc console --no-browser 与真实 loopback HTTP 请求；已有来源保护动作拒绝跨源请求；新 Runner 进程能观察技能晋升后的合成标记，测试输出与类别状态一致"
  mock_boundary: "真实 kc console 进程、uvicorn、HTTP 路由、本地文件存储和下一轮 Runner 进程；不使用 FastAPI TestClient、直接 handler 调用、真实 Agent、GitHub 或外网"
  tier: R3
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "Console URL 与端口取自临时服务的实际启动输出；技能标记来自测试创建的本机临时草稿"
  must_cross: "kc console -> loopback TCP -> FastAPI 路由 -> 状态消费者；真实技能晋升存储 -> 新 kc run 进程 -> prompt 构造"
  forbidden_bypasses: "禁止 TestClient、直接调用路由函数、只检查写入进程内存、复用开发者的 Console、仓库或记忆目录"
  fresh_state_probe: "新 HTTP 请求读取 Console 操作后的状态；新 Runner 进程在探针记录中显示 prompt 是否读取到合成技能标记"
  final_tree_evidence: "记录服务路由、记忆代码、测试代码和导航的最终 Git tree；改变任一入口、状态写入或消费路径后重新运行"
  negative_control: "在隔离源码副本里临时移除来源保护，再以真实 loopback HTTP 客户端向应受保护动作发送合成跨源请求"
  expected_fail: "该场景观察到的状态或响应与受保护基线不同，测试以非零退出；恢复真实校验断言后通过"
```

**失败排查顺序：** 先确认命令从仓库根目录运行且 addopts 被显式清空；再看隔离配置、PATH 注入桩日志、真实 Runner argv / cwd、Git 新进程探针、Console 启动输出和后续 prompt 探针。任何未知桩输入都应直接失败，不添加默认成功分支。

### 7.7 Low-Fidelity Prototype

Not required. 本 PRD 不改动用户界面；交付物是开发者文档和测试。

### 7.8 Interactive Prototype Change Log

No interactive prototype file changes in this PRD.

### 7.9 External Validation

No external validation required; repository evidence was sufficient.

### 7.10 Risk Classification Register

| 变更点 | 档位 | 决定性维度 / 覆盖规则 | 介入 | oracle / 门禁 |
|---|---|---|---|---|
| 公开威胁模型与本机私有记录分界 | R3 | 安全披露边界；误放复现会向公开仓库传播 | 人工确认（私有存储位置） | rv-1 |
| Runner 环境、权限参数和文件边界场景 | R3 | Agent 可执行不可信仓库内容；错误结论会掩盖权限或凭据风险 | 执行器 + 真实入口强 oracle | rv-2 |
| Console 写操作来源与技能持久化场景 | R3 | 本地写接口与未来 prompt 属于信任边界 | 执行器 + 真实入口强 oracle | rv-3 |
| 八类攻击面状态表与导航 | R2 | 跨模块现状汇总；陈旧表格会误导后续修复和验收 | 执行器 + 页面核对及 MkDocs 严格构建 | rv-1 |
| 常规测试目录与 testmon 全量命令 | R1 | 测试发现和日常运行路径局部变化 | 执行器 + pytest 收集结果 | rv-2、rv-3 |
| ROADMAP 不变、M13 依赖只留给模型评测后续 | R0 | 规划文字；当前实现无需顺序依赖 | 执行器 | §8 与 §12 人工对照 |

R2 / R3 可执行 oracle 共三条（rv-1、rv-2、rv-3），未超过 PRD 范围信号上限。安全修复不属于本 PRD 的变更点。

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 当前确定性安全基线可独立于 M13 实施。M13 的重复运行与方差结论只作为未来“真实模型易受攻击程度”评测的前置，不是本 PRD 的交付依赖。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 结果 | 呈递物 | 10 秒自检 |
|---|---|---|
| 八类攻击面、已有控制、证据状态和未测试范围都能在公开文档中找到；详细复现只留在忽略文件。 | 打开 `docs/dev/security-threat-model.md`；本机私有记录可用 `open .kedacode/security/private-known-gaps.md` 查看。 | 公开表每项只含类别、控制、状态、定性严重度；本机文件命中忽略规则且不在 Git index。 |

`reviewer: verifier` 的 runner 参数 / 环境 / 文件场景、Console HTTP 场景、技能持久化探针、pytest 输出和 MkDocs 构建结果不在人读区呈递；它们由 verifier 核对，仅在失败时升级给人。

### 9.2 Acceptance Evidence Package（机器证据 · verifier 入口，人默认跳过）

下列 `tasks/evidence/<prd-stem>/` 文件保存命令输出、页面核对和最终 tree 证据；`<prd-stem>` 使用本 PRD 文件名去掉 `.md` 后的部分。可复用同一份原始输出，但不得省略对应证据文件。

#### Human-Confirmed

- [ ] **确认私有缺口位置：** 同意详细复现只保存在本机忽略文件 `.kedacode/security/private-known-gaps.md`，并接受不会自动跨机器同步；证据写入 `tasks/evidence/<prd-stem>/rv-1-private-location.txt`，包含 `git check-ignore -v` 与 `git ls-files` 输出。（rv-1）
- [ ] **审阅交付表面：** §9.1 的公开安全模型呈递物已打开；确认八类覆盖、状态边界和未修缺口没有可执行复现细节；记录在人审报告 `tasks/evidence/<prd-stem>/rv-1-human-review.md`。（rv-1）

#### Architecture Acceptance

- [ ] 新增测试只在 `tests/security/`；未修改 `src/backend/`、配置默认值、API、CLI、数据库或前端；文件清单保存在 `tasks/evidence/<prd-stem>/file-scope.txt`。
- [ ] 场景只替换外部 Agent 与必要的 GitHub CLI；真实 Runner、Git、HTTP 和本地记忆路径均未被 mock 或直接 handler 调用替代；入口记录保存在 `tasks/evidence/<prd-stem>/rv-2-runner-boundaries.txt` 与 `tasks/evidence/<prd-stem>/rv-3-console-memory.txt`。
- [ ] `git diff --name-only` 与 §7.2 Change Impact Tree 一致；本机忽略文件未出现在 diff 或 index；输出保存在 `tasks/evidence/<prd-stem>/git-tree-and-scope.txt`。

#### Documentation Acceptance

- [ ] `docs/dev/security-threat-model.md` 列出八类攻击面、已有控制、状态、严重度、证据范围和未测试原因；没有把未测试描述为已验证；页面核对记录保存在 `tasks/evidence/<prd-stem>/rv-1-public-surface.md`。
- [ ] 公开表只包含类别级缺口信息和不透明 ID；没有未修缺口的具体复现步骤、真实敏感值或可还原的攻击载荷；披露扫描输出保存在 `tasks/evidence/<prd-stem>/rv-1-disclosure-scan.txt`。
- [ ] `mkdocs.yml` 的开发者导航包含 Agent Runner 安全模型；`uv run mkdocs build --strict` 通过，输出保存在 `tasks/evidence/<prd-stem>/mkdocs-build.txt`。
- [ ] 文档对文件变更保护的覆盖范围描述与实现核对一致，不沿用已纠正的旧说法；依据及复核文件清单保存在 `tasks/evidence/<prd-stem>/source-fact-check.md`。

#### Validation Acceptance

- [ ] `uv run pytest tests/security -q -o addopts=""` 收集并运行安全场景，输出用例数大于零；没有因 testmon、skip 或外部服务缺失而空跑；命令输出保存在 `tasks/evidence/<prd-stem>/pytest-security.txt`。
- [ ] `tests/security/` 被 CI 现有 `uv run pytest tests/ -v -m "not expensive" --no-testmon --durations=25 --timeout=120` 自动收集；没有新建 CI job 或依赖凭据；CI run URL / 收集摘要保存在 `tasks/evidence/<prd-stem>/ci-collection.md`。
- [ ] rv-2 从真实 `kc run` 到真实 Git 子进程完成；fresh-state 探针读取 Git index、文件和提交，不从执行进程内存推断结果；原始记录保存在 `tasks/evidence/<prd-stem>/rv-2-runner-boundaries.txt`。
- [ ] rv-3 从真实 `kc console --no-browser` 到 loopback HTTP 路由完成；技能晋升通过新 Runner 进程观察后续 prompt；原始记录保存在 `tasks/evidence/<prd-stem>/rv-3-console-memory.txt`。
- [ ] 所有测试使用合成标记；没有真实模型、GitHub API、真实 token、开发者 HOME、仓库或共享 Console；隔离配置与进程环境摘要保存在 `tasks/evidence/<prd-stem>/isolation-summary.txt`。
- [ ] 已知缺口测试直接断言当前观察；行为变化未同步更新类别状态和断言时，目标测试返回非零；没有 skip 或非严格 xfail 隐藏状态；负控结果保存在 `tasks/evidence/<prd-stem>/known-gap-negative-control.txt`。
- [ ] rv-1 的合成隐私 canary 负控能被精确搜索命中；公开文档、测试数据、证据和最终 diff 中无真实私有复现或敏感值；扫描记录保存在 `tasks/evidence/<prd-stem>/rv-1-disclosure-scan.txt`。
- [ ] `uv run mkdocs build --strict` 与安全场景验证都在最后一次相关文档 / 测试改动后的最终 Git tree 上重新运行；tree 身份与命令结果保存在 `tasks/evidence/<prd-stem>/final-tree-and-validation.md`。

#### Documentation Impact

- [ ] 未修改 ROADMAP；ROADMAP 当前瘦身讨论仍与本 PRD 分开，M13 只作为未来模型方差评测的前置说明；对照记录保存在 `tasks/evidence/<prd-stem>/roadmap-scope-check.md`。
- [ ] 未把本 PRD 确认的缺口修复并入安全基线；每个生产修复由独立后续 PRD 决定范围、风险和验收；范围核对保存在 `tasks/evidence/<prd-stem>/scope-reconciliation.md`。

#### Delivery Readiness

- [ ] 独立 verifier 对 rv-1 至 rv-3 及本清单给出 `PASS`；若 verifier 环境 / 协议 / 能力失败，记录 `REVIEW_INCIDENT / INCONCLUSIVE`，不得标成产品 `PASS`；verifier 输出保存在 `tasks/evidence/<prd-stem>/verifier-report.md`。
- [ ] 验收证据报告 `tasks/evidence/<prd-stem>/acceptance-evidence.md` 以 §9.1 人审导航开头，并逐条说明命令、观察值、fresh-state 探针和最终 tree 身份。
- [ ] 若交付 PR，PR body 唯一关联本 PRD 并说明 merge-as-acceptance；PR evidence comment 呈递 §9.1、verifier 结果、必需门禁与证据树身份，链接记录在 `tasks/evidence/<prd-stem>/pr-evidence-reference.md`。
- [ ] 完成归档前 Final Reconciliation；执行侧证据完成后 PRD 归档于 `tasks/archive/`，未确认的人审项仍保留在 `Human-Confirmed` 并按横幅显示待人工验收；核对记录保存在 `tasks/evidence/<prd-stem>/final-reconciliation.md`。

## 10. Functional Requirements

- **FR-1：** 公开安全模型必须盘点八类攻击面：不可信文本入口、凭据与环境、Agent 权限 / 沙箱、文件与配置自改、外联网络、本地 Console API、记忆与技能持久化、自动合并链路。
- **FR-2：** 每类必须列出资产、攻击者可控输入类别、已有控制类别、证据状态、定性严重度和覆盖边界；状态值统一为“已防护”“已知缺口”或“未测试”。安全模型中的状态表是状态唯一事实源，不再维护第二份状态清单。
- **FR-3：** 公开文档与公开测试对未修缺口只标类别、不透明编号和类别级行为，不含精确利用步骤、敏感路径或真实凭据。具体复现由维护者保存在确认后的本机忽略位置。
- **FR-4：** 确定性攻击回归必须在隔离临时状态内经过真实 Runner / Git / HTTP 边界，只将外部 Agent 和必要的 GitHub CLI 换为 fail-loud 桩。
- **FR-5：** 已知缺口的回归必须断言登记的实际状态；代码行为翻转而状态未更新时，断言失败。修复该缺口的后续 PRD 将测试更新为安全行为断言，并同步更新公开类别状态。
- **FR-6：** 网络外联、真实模型抵御提示注入和真实 GitHub 合并必须明确标为未测试，不允许从模拟的 Agent 或本地远端结论外推。
- **FR-7：** 安全测试全程离线且不读取真实凭据；加入 `tests/` 后由现有 CI 全量 pytest 命令自动发现，并提供显式清空 testmon 的独立运行命令。
- **FR-8：** 新增公开安全模型页必须加入 `mkdocs.yml` 导航，并通过严格 MkDocs 构建。
- **FR-9：** 本 PRD 不改变生产行为、公开 API / CLI / 配置默认值、数据库结构或前端。

## 11. Non-Goals

- 修复 Agent 权限参数、子进程凭据范围、沙箱或网络访问。
- 扩大默认禁改路径，或改变文件提交、发布、Console、技能晋升、自动合并行为。
- 建立针对真实模型的提示注入评分集或统计模型受骗概率。
- 真实网络隔离测试、GitHub API 测试或真实自动合并试验。
- 把具体未修漏洞细节、敏感端点 / 文件路径、载荷和步骤放进公开文档、测试或 CI 日志。
- 新建通用安全测试平台、远程扫描服务、数据库或控制面。
- 修改 ROADMAP。

## 12. Risks And Follow-Ups

- **公开状态被误读为安全认证：** 页首和表格必须声明这是有限范围的工程基线；“已防护”只表示所列控制经过本地确定性回归，不代表全部攻击路径安全。
- **本机私有记录丢失：** `.kedacode/` 不随 Git 同步且不会自动备份。若出现第二位维护者、必须跨机协同或需要长期留档，迁移到已有的私有跟踪库，再更新 §2 决策；不得因此把复现复制进公开仓库。
- **已知缺口改变时状态短暂不同步：** 现状断言会在行为改变时失败；修复 PR 必须把状态更新与安全行为断言放在同一交付中。
- **模型攻击耐受性尚未测量：** 等 M13 重复运行与方差结果可解释后，再评估是否建立真实模型攻击评测；在此之前，本 PRD 不给模型安全性下结论。
- **网络与自动合并缺少真实回归：** 本地隔离无法证明外网 egress 和 GitHub 分支保护；后续只有在可构造离线真实边界且不需要秘密凭据时才补回归，或由单独的安全修复 PRD 定义受控试验。
- **独立修复排序：** 本 PRD 只提供问题类别和本机详细记录；每个修复候选都需要自己的风险决策、兼容性判断和验收 oracle，不预先把它们排入 ROADMAP。

## 13. Decision Log

| ID | 决策问题 | 选定方案 | 被拒绝的具体方案 | 原因 |
|---|---|---|---|---|
| D-01 | 本期建立基线还是同时修复缺口 | 只建模型和确定性回归；修复另开 PRD | 在本 PRD 一次修复所有发现项 | 权限、凭据、Console、文件策略与自动晋升是不同 R3 产品取舍，不能在没有分别确认的情况下捆绑。 |
| D-02 | 攻击模拟要测 Keda 控制还是模型抗注入 | 假定外部 Agent 已被说服，用脚本测外围控制 | 让真实模型处理恶意输入并把结果当作安全结论 | 当前缺少经验证的重复运行方差口径，模型结果会把模型波动与产品控制混为一谈。 |
| D-03 | 公开安全材料披露到什么程度 | 公开架构、控制、缺口类别、状态与定性严重度；复现细节留私有 | 把未修缺口的端点、载荷和步骤完整放进公共文档与测试 | 公开复现会降低攻击成本，而类别级状态仍能展示安全工程方法与覆盖边界。 |
| D-04 | 已知缺口怎样保持与代码一致 | 用具名断言锁定当前观察；行为变化时测试失败并要求同步转状态 | 使用 skip 或非严格 xfail 让用例静默略过 | 当前行为可以被 CI 如实观察，状态翻转不能在没有审阅的情况下通过。 |
| D-05 | 安全回归的执行位置 | 新增 tests/security/，无真实凭据，加入现有 pytest / CI 收集 | 另设手动脚本或独立在线安全服务 | 离线确定性回归适合进入常规全量 CI，维护一套测试入口成本更低。 |
| D-06 | 本机私有记录的存储位置 | **推荐待确认：** 本机忽略文件 .kedacode/security/private-known-gaps.md | 直接写进公开 tasks/ 或新建私有远程服务 | .kedacode/ 已被忽略，本地文件满足本期隐私边界且无需接入服务；代价是不能自动同步或备份，需由 §2 人工确认。 |
| D-07 | 威胁模型覆盖哪些攻击面 | 盘点八类，自动测试只覆盖可稳定离线验证的子集 | 只记录恶意 Issue、凭据与文件修改三类 | 漏掉本地 API 与持久技能这两个跨任务信任边界会让模型无法解释后续 prompt 污染风险。 |
