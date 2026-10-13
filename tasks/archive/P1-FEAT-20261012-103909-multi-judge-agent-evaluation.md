# PRD: 多评审模型的 Agent 与模型效果评测

- GitHub Issue: https://github.com/ZataZhang/keda/issues/284

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。
> 🧍 **验收状态**：待人工验收 — 执行侧已完成（§9 除 2 项 Human-Confirmed 与 2 项 runner-owned `[~]` gate 外全部勾选，rv-1/rv-2/rv-3 证据见 `tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/evidence.json`），仅剩 2 项 Human-Confirmed 未确认。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文分为人审层与执行器层：前四节确认评分的含义、用户可见结果与数据边界；后九节定义评测集、评审器、持久化、执行隔离和验收证据。

## Feature Overview (功能一览)

> 本节是 §10 Functional Requirements 的白话投影；行为验收以 §1 的行为样例为准。

- **公平比较执行模型与 Agent**（FR-1、FR-2）：选择同一版本评测集、同一基线和多个执行预设，对每个固定样本独立执行。
- **配置多个评审模型**（FR-3）：使用者可设置多个评审模型预设，在一次评测中选择一个或多个评审器；保留每个评审器对每个样本的原始评分结果。
- **评分与确定性检查分开展示**（FR-4）：LLM 评分、自动检查通过率、失败率、时延与可得成本分别展示；评审分歧可见，不默认折算成单一总分。
- **评审前检查外发内容**（FR-5）：候选执行结束后，用户先检查将发送给评审模型的内容、目标提供方和费用信息，再明确授权调用；默认不自动评分。
- **可追溯的本地历史**（FR-6）：本地保存评测配置快照、样本结果、每位评审者的评分与状态，支持查看和删除；不保存密钥或完整原始对话。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

目前项目有 Agent / 模型预设和按历史 attempt 汇总的 Stats，但两者回答不了“在同一组任务、同一个基线上，哪个执行模型或 Agent 的产出更好”。项目的评测指南已建议使用黄金样本、记录模型与 Prompt 版本、观察成功率/时延/成本；评测集格式和自动化入口仍待建立。单次运行的历史统计不能控制样本和输入差异，也没有多位评分者对同一个结果的逐样本判断。

用户希望比较不同预测模型与 Agent 实现，并允许多个评分模型各自评审。LLM 评分会受主观偏差、评审模型差异、提示注入和外发代码内容影响；因此本功能必须把每位评审者的结果单独保留、与确定性测试分开，并在任何外部模型调用前让用户检查实际发送内容。

### Interpretation (解读回显)

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 👀 人审 + 自动验证 | 在评测页选择一个已版本化样本集、固定基线、至少两个执行预设，并启动对比。 | 每个执行预设的每个样本都从同一基线的独立 worktree 开始；页面列出样本、执行预设、确定性检查、运行时间和结果状态。 |
| 👀 人审 + 自动验证 | 选择两个或更多评审模型预设，并对已完成候选结果发起评审。 | 在调用前展示每个提供方/模型将收到的确切任务说明、执行摘要、差异与检查结果，并展示可用的 token / 成本估计；只有明确确认后才发送。 |
| 👀 人审 + 自动验证 | 多个评审模型分别完成同一批结果的打分。 | 每个模型对每个样本、每个评分维度的分数和简短依据单独可查；汇总显示各维度中位数、有效评审数及分歧范围，不隐藏失败评审或离群分数。 |
| 🤖 自动验证 | 评审模型超时、拒答、返回无法解析的结果，或确定性检查失败。 | 对应记录标为超时 / 拒答 / 无效 / 失败 / 未评分；不把未评分写成 0 分，也不丢失同批其他结果。 |
| 🤖 自动验证 | 使用者拒绝外发评审内容或取消评测。 | 不发生对应评审请求；已得到的执行结果和确定性检查仍保留，评审状态为未授权 / 已取消。 |

**我默默定了这些**

- 一次对比固定评测集版本、基线 commit、执行预设快照和评分 rubric 版本；每个执行预设与样本组合从干净隔离 worktree 启动，避免前一个候选结果污染后一个。
- 执行模型从已有 Agent / 模型预设中选择；评审模型使用多个独立的命名评分预设，通过只处理文本、不使用代码工具的评分调用与执行器隔离。
- 候选 Agent 执行会按用户选择的执行预设联系其配置的模型提供方；开始运行前列出执行预设、provider/model 和可得费用估算。此运行启动动作与候选完成后对评分模型 payload 的逐次授权分开，评审未授权时不发送任何 judge 请求。
- 默认 rubric 采用 1–5 分锚定量表，按正确性/任务要求、完整性与范围、实现质量、验证证据分别给分和简短理由；不默认生成加权总分。若某维度少于两位有效评审者，不展示“多数意见”或合成结论。
- 汇总以各维度的中位数、最小—最大范围和有效评分数呈现；单个评审者、单个样本与任何无效/失败状态都可展开查看。
- 自动化确定性检查、Agent 最终状态、运行时长、稳定性和实际可得的 token/成本是独立指标。不能从不完整 usage 或未知价格推算精确成本。
- 评审请求只包含完成评分所需的样本任务、执行摘要、代码差异与确定性检查摘要；不自动发送环境变量、密钥、原始工具日志、完整终端历史或整个仓库。代码差异仍可能含敏感内容，所以发送前要能查看确切 payload。
- 评测默认由人显式启动，执行结束后暂停在“等待评审授权”；不后台自动评分、不在 CI / 合并 / Agent 路由中使用分数。

**我理解为不做**

- 不把 LLM 评分当作测试、独立 verifier、人工验收或发布门禁，不自动据此选择默认模型或修改执行路由。
- 不为任意仓库自动生成黄金集、不从历史任务中静默抽取 Prompt / Diff，也不提供大规模基准托管和跨组织排行榜。
- 不在 v1 中做通用 rubric 编辑器、统计显著性检验、模型价格抓取或 token 价格推断。
- 不允许评审模型调用工具、访问被评代码仓库或更改候选产物；不将评测任务提交 PR、写回正式 Issue 生命周期或影响实际 Agent Runner 队列。

请把它读成：使用者用固定且可追溯的样本与基线并行比较执行 Agent / 模型预设；候选执行结果先经过确定性检查，再由用户选择的多个纯文本评审模型独立评分。网页按样本呈现自动检查和每个评审者的结果，按评分维度呈现中位数与分歧。评分可以帮助比较，但永不代替测试或验收。执行结束后，系统先呈现确切外发内容，用户确认后才联系评审模型。

### What The User Gets

使用者可以比较多个 Agent / 模型预设在同一固定样本集上的表现，而不是从不相同的历史任务中猜测模型优劣。多位用户选定的评审模型会逐项独立打分；页面会保留每位评审者的评分、依据和缺失状态，并把它们与检查通过率、稳定性、耗时及成本分开展示。使用者可查看这次评测实际比较了什么、评审模型收到什么、结果如何产生，以及删除本地评测记录。

### Measurable Objectives

- 开始候选执行前，页面准确列出每个执行预设对应的 provider/model 与可得费用估算；未知费用明确显示为未知。
- 一个评测 run 内的每个执行预设 × 样本组合都记录相同 suite version、rubric version 和 base commit；worktree 从该基线独立创建。
- 多个评审预设均能对同一执行结果独立评分；逐评审的字段、理由和失败态可查，汇总值能从留存明细重新计算。
- 确定性检查状态与 LLM 评分分开展示；超时、拒答、无效 JSON、未授权和未评分永不伪装为 0 分。
- 未经明确确认时评审请求发送数为 0；确认后的请求内容与预览内容 hash 一致，provider、model 和有效评分数有记录。
- 删除评测 run 后，其 SQLite 结果与暂存 payload 一并删除；API key、环境变量和完整原始执行 transcript 不写入评测表。

## 2. Human Review Map (介入与风险地图)

### 决策一：评审分数是比较参考，不是任务验收

不同评审模型可能对同一产出意见不一；确定性测试也有范围边界。最小有效交付是把每位评审者的逐样本打分留在原处，按评分维度汇总中位数与分歧，并同时显示可重复的检查结果。该做法能支持模型间比较，但不能宣称评分是客观真值或单一总分。若之后需要质量门禁，应另行定义有明确校准数据的 rubric 和独立验收协议。

**请确认：** 评审分数只用于同批结果的辅助比较；逐评审结果与确定性检查分开展示；本功能不会将评分用于 CI、合并、发布或自动模型路由。

**验收：** 页面显示各评审模型、各维度分数、依据、中位数与分歧范围；确定性检查独立列示；任一有效分数、失败状态或样本明细可追溯，且没有由 LLM 分数驱动的通过 / 失败门禁。

### 决策二：代码差异外发前必须逐次预览并授权

为了让模型判断实现效果，评审器通常需要看到任务要求和候选代码差异。它们可能含用户代码或敏感文本；一旦发给所选模型提供方，无法由本机撤回。本方案只在候选执行完成后显示逐模型发送目标与完整 payload 预览，用户每次评测主动确认；拒绝时保留本地检查结果但不打分。这里的授权专用于评分请求：候选 Agent 可能在执行期间调用其选定 provider，这是用户在启动运行时选择并确认的行为；启动前页面需单列执行 provider/model 与可得费用估算。评测 run 的评分和配置快照保存在本地 Console SQLite，供本机对比和删除。

**请确认：** 候选执行按启动时显示并选择的 Agent provider/model 运行；评分模型只有当前页面确认的评审模型会接收预览中列明的内容，确认前不发起 judge 请求。评测暂存 payload 只在等待授权 / 评分期间本地保存，完成、拒绝或取消后删除；长期保留结构化分数、简短依据、来源快照与 hash，不保留 API key、原始工具日志或完整代码差异。

**验收：** 拒绝/取消的负控中没有发出评分请求；确认的实际请求逐字节对应预览 payload；用户可读到目标 provider、模型和费用信息（费用未知时明确标为未知）；本地记录删除后，run、分数与临时 payload 均消失。

### 自动门禁，不需要逐项人工审阅

Executor 与 verifier 负责评测样本格式和版本校验、干净 worktree 隔离、并发上限、确定性 check recipe 执行、每位评审响应解析与错误态、汇总重算、SQLite 重启读取/删除、日志和密钥不落库、窄屏布局以及默认无外发的网络边界。真实入口覆盖 Console 页面、API、SQLite 和真实模型 provider adapter；只有外部模型本身可以在常规自动验证中替换为边界 fixture，live provider 只在用户 opt-in 沙箱中运行。

## 3. Usage And Impact After Implementation

### Console 使用者

使用者进入 Console 的「模型评测」页面，选一个已版本化 suite、相同基线 commit、两个或更多执行预设、评分 rubric 和一个或多个评审预设。开始前页面逐项显示执行预设将使用的 provider/model 与可得费用估算；用户明确开始后，这些 Agent 预设可按原有配置调用各自 provider。系统为每个组合创建独立 worktree，显示排队、执行、检查与失败状态。评测运行不改变 Issue、PR 或主工作区。

候选执行结束后，页面分开呈现检查结果与 Agent 运行信息，并进入“等待评审授权”。使用者逐项展开外发内容，看到 provider、模型、任务、执行摘要、Diff、检查结论及当前可得费用；确认后，评审模型只返回结构化分数和短理由。结果页允许按样本或执行预设过滤，查看每个评审者、分歧、失败记录和成本缺口；使用者可删除整次本地 run。

### 评测集维护者

维护者在仓库中的版本化 suite 文件里定义稳定样本 id、任务说明、仓库 fixture/base ref 和获准的验证 recipe。新版本产生新的 suite hash；已执行历史仍保留原版本与基线快照。维护者可用文档化的 suite 校验入口发现格式错误和不安全 check 配置。系统不自动把真实 Issue 或私有仓库内容加入 suite。

### Agent / 模型配置维护者

维护者继续在现有 Agent Runner 设置中定义执行预设；评测页面从该清单选择执行候选。评审模型使用独立的命名模型预设，引用已有 provider / model 配置；页面允许配置多个默认评审者，也允许单次 run 覆盖。实际密钥仍只从既有环境注入，不通过浏览器或评测数据库回显。

### 兼容与迁移影响

- Console SQLite 增加独立的 evaluation run、case result 和 judge score 记录；启动时沿用现有 `PRAGMA user_version` 路径做向前兼容 schema 升级，不改已有运行日志、Issue、Stats 或 invocation 表。
- 原有 Agent 执行、Stats 成功率、Verifier/验收流程和 `kc` 命令行为不变；评测是新的手动 Console 页面/API，不作为 daemon 队列任务。
- 新增的是评测集 YAML 格式与评审模型 preset 引用，不新增密钥变量或复制一份 provider 凭据；空评测配置仍允许 Console 正常启动，评审调用需用户显式配置。
- 每次 run 按明确动作创建本地隔离工作区；已完成/已取消 run 自动清理临时工作区和暂存 payload，结果数据库按用户选择删除。

## 4. Requirement Shape

- **Actor：** Console 使用者、评测集维护者、Agent / provider 配置维护者、开发者与独立 verifier。
- **Trigger：** 使用者从 Console 明确创建一次模型 / Agent 对比；候选执行结束后再确认是否将具体 payload 发给评审模型。
- **Expected behavior：** 同一版本样本与基线下隔离执行多个预设；独立展示确定性检查和逐评审分数；允许配置/选择多个评审模型，并把外发授权、持久化和删除范围呈现清楚。
- **Scope boundary：** 固定本地 suite 的手动批量评测、逐样本评审和本地对比历史；不把分数接入发布门禁、正式 Agent 生命周期或远端排行榜。

# Part B · 执行器层 (Build Layer)

> 以下供实现者使用；Part A 的公平对比、逐评审保留、授权边界与“分数非验收”语义是行为契约。

## 5. Repository Context And Architecture Fit

### 当前可复用路径

- `docs/dev/evaluation.md` 已写黄金样本、版本跟踪、质量/时延/稳定性/成本指标，当前 TODO 明确缺 suite 格式与自动入口；本 PRD 需要把建议变成一条本地 Console 产品路径。
- 执行 Agent / 模型选择已有 `AgentModelPreset`，形态为 `(agent, model, reasoning_effort)`，来源为 `[agent_runner.presets.<name>]`；评测候选复用此快照，不重新定义执行模型目录。
- infrastructure 已有 `create_chat_model`、provider 配置解析及环境变量密钥加载，适合构造无工具的评分模型调用。评审预设只命名并引用模型 id，不把 Agent CLI 当作评审 API，不向评分模型绑定仓库工具。
- Console 持久层是 SQLite 独立账本并使用 `PRAGMA user_version` 管理 schema；评测新增表要按该存储自身版本增量升级，不能修改或假设 Alembic 版本。
- 历史 Agent / 预设 Stats PRD `tasks/archive/P2-FEAT-20261009-171037-agent-preset-performance-stats.md` 汇总不受控的历史 attempt；它回答频次、结果与时长，不提供相同样本的对照实验。本 PRD 新建受控评测，不重算或覆盖其统计。

### 架构边界与相关 PRD

- suite 解析、样本约束、状态迁移、评分校验与汇总放 `core`；HTTP 与 DTO 在 `api`；执行器适配和只读评审调用在 `engines`；SQLite/provider 调用等基础设施留在 `infrastructure`。依赖方向保持 `api -> core -> engines -> infrastructure`。
- `tasks/pending/P1-FEAT-20261012-103222-unified-runtime-dashboard.md` 添加 Dashboard 与导航入口，可能与评测页使用同一导航/前端 API 类型文件，但两者交付无前后置行为依赖；实施时协调共享文件。
- `tasks/archive/P1-FEAT-20261009-133425-lifecycle-agent-model-settings.md` 与当前 lifecycle settings 提供执行预设定义；本 PRD 只读取执行预设，并增加评审模型 preset 引用配置，不更改生命周期九阶段绑定。
- `tasks/archive/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling.md` 定义现有 runner 并发资源约束；离线评测必须尊重同一主机 capacity，不能另开一组无界 worker。
- `frontend-admin/` 不承载 Console 用户入口；改动仅落在 `frontend-public/` 与 Console API。

## 6. Recommendation

### Recommended Approach

在现有 Console 增加手动、版本化的对照评测闭环：suite 以仓库内 YAML 固定样本与检查 recipe；每个执行 preset × case 组合都从固定 base commit 的干净隔离 worktree 启动，结束后独立运行检查。候选结果先本地持久化并展示确定性数据，默认暂停，不调用任何 judge。用户确认具体 payload 和选定评审模型后，用现有 `create_chat_model` provider 工厂逐个执行无工具请求；结构化校验每位评审的量表、依据和错误态。页面比较每个执行预设、样本和评审模型，并显示维度中位数与分歧范围。

执行侧继续读取已有 `[agent_runner.presets.<name>]`。评审侧在 `config.toml` 中新增命名 judge preset 清单（model id + 可选温度/参数），只引用现有 provider 注册和 `OPENAI_API_KEY` 等环境密钥；允许配置多个默认评审预设，并可在每次运行时覆盖选择。Provider 不可用、API key 缺失或模型不在 provider registry 时启动前 fail-fast，不写入 0 分。

Suite 文件只接受明确结构：稳定 suite id / semver、版本、repo fixture、固定 base ref、rubric id / version、case id / task prompt 和命名 validation recipe。Recipe 映射到受限 argv，不允许 shell 拼接；限制超时、并发、环境继承与输出大小。评测工作区只允许写在运行专用根目录，不提交 PR/commit，也不触碰用户主工作区；完成、取消、失败恢复后按策略清理。Console 全局退出或崩溃后的 run 标为 interrupted，由用户显式恢复或删除，不自动重跑可能产生外部副作用的 Agent。

### 为什么该方案适合当前架构

项目已有执行预设、provider 模型构造器、只读 Console 和本地 SQLite，成本主要在固定样本格式、隔离执行与新的结果记录。把 benchmark 控制在本地 YAML、手动启动、小并发和用户授权的评审请求范围，可以首先得到可重复比较，又避免建设云端数据集服务、模型计费系统或 CI 门禁。历史 Stats 仍独立回答日常运行表现。

**ROI 判断：现在做一个受控 v1。** 使用者能在需要选型或改 prompt 时重复运行同一批样本，收益来自可复验的同条件差异、逐评审分歧和失败证据；成本主要是隔离 Agent 执行、Console SQLite 新表与 provider 授权 UI。更便宜的替代是手工跑一份固定任务并把结果贴进表格，但它不能绑定基线/执行配置、不方便保留多评审者的失败态，也不能从历史记录重算比较。为限制维护负担，先采用仓库 YAML、手动触发、多个文本 judge 与本地历史；只有 suite 被持续复用、人工复核量明显上升时再评估自动排程或 CI 阈值。

### Proposed Solution Summary (实现机制)

1. Suite registry 从固定仓库目录加载和校验 suite；内容 hash + 版本进入 run 快照。
2. 创建 run 时固定 case、基线 ref、执行 preset 的解析快照、rubric 版本和所选 judge presets；空 suite / 失效 preset / dirty base 等问题在执行前阻止启动。
3. `evaluation_run` 排程 `evaluation_result`；每个 case/preset 对应独立 worktree，运行 Agent 时使用已有 runner adapter，但评测 run 不进入 Issue lifecycle。完成后记录 agent exit/status、命名验证 recipe 结果、wall time 和可得 usage/cost。
4. Agent 完成后生成分评审模型的 payload。精确待审 payload 存在 Console 私有临时目录且仅在 waiting-consent/judging 期间保留；数据库留 hash。预览后用户确认，服务端重新核对 run/payload hash，再调用无工具的 `create_chat_model`。拒绝/取消不发出请求。
5. 每个 `evaluation_score` 存 judge preset 与解析后模型快照、prompt/rubric 版本、每维分数、短理由、状态、耗时、usage、response hash。中位数/范围从数据库明细实时派生，避免保存双份统计事实。
6. UI 提供 suite、执行 preset、judge 多选和基线配置；比较页展示每个候选、case 检查、每位 judge 和维度汇总；运行删除操作级联删除本地结果并清理该 run 的残余临时 payload/worktree。

### Scope Cohesion Gate

执行预设公平对照、多个评审器评分、检查结果对照和评分本地留存共同构成一次评价闭环：单独交付其中任一部分，都无法回答“同一任务条件下哪个实现效果更好”。所以保留在同一 PRD。默认 Dashboard / 桌面窗口 PRD 只需提供导航入口和可选摘要，不依赖本评测工作流，可独立交付。

### Alternatives Considered

- **直接把历史 Stats 增加 LLM 分数：** 不采用。历史 attempt 样本、基线和提示不一致，不可作为同条件实验；也会混淆运营统计与主观评审。
- **将任务 Diff 自动发给默认强模型并在后台评分：** 不采用。代码外发不可撤回，成本受数据规模影响；每次发送都必须展示目标和准确内容并经用户确认。
- **用平均总分替代逐评审结果：** 不采用。平均可能隐藏分歧和评审失败；当前没有用户确认的权重或校准样本。
- **评审模型复用能运行 repo tools 的 Agent CLI：** 不采用。评分调用读取的是显式摘要和 Diff，不需要命令/文件工具；无工具模型请求减少权限边界和提示注入面。
- **云端基准、自动 CI 回归门槛：** 暂缓。现阶段先验证固定样本的本地对照是否能帮助用户选型；只有同一套样本被持续复用、人工评审成本明显时，再评估自动调度或 CI 阈值。

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, or a better persistence/provider boundary, update this PRD before proceeding.

### 7.1 Core Logic

1. 创建 run 前验证 suite 版本、case id 唯一性、repo fixture/base commit 可达、checks 属于允许的 recipe，执行 preset/judge preset 全部可解析；没有满足条件的配置时 fail-fast。
2. 运行矩阵键为 `(run_id, case_id, execution_preset_snapshot_id)`；任何两个候选不得共享 worktree 或上一候选生成的文件。固定同一基线树 hash、工作目录映射、允许的环境变量和超时；遵守既有 Runner 并发 ceiling。
3. 检查结果记录命名 recipe、exit code、通过/失败、超时或未运行及有限输出摘要。检查命令只允许 argv + 受限 cwd，禁止 `shell=True`、suite 提供任意解释器脚本、继承完整用户环境或访问主工作区。
4. 评分 rubric v1 具备稳定 id/version、四个 1–5 分维度、每档含义和短文本证据字段。模型响应必须过 schema 和数值范围校验；缺项、额外格式无法解析、拒答、timeout、provider error 分别留状态，结果不能化为零分。
5. 汇总只按 `valid` judge scores 计算每维中位数 / min / max / N；任何未完成候选均带状态，页面不得把未完成矩阵排除后仍称全批次完成。单个有效 judge 只显示其分数和 `1 位评审`，不显示“评审一致”。
6. 审批边界：浏览器先取得 payload manifest/hash 与可读完整预览；批准 API 绑定 run id、judge preset 集与 manifest hash，服务端重新校验当前待审 payload hash 后才向 provider 发出请求。过期 hash 返回冲突并要求重新查看。
7. 临时 payload、worktree 生命周期与 run state 一致。评分完成、拒绝或取消后删除暂存 payload；持久层不存 raw prompt、完整 patch、token、环境变量或密钥。评审摘要限制长度且写入本地账本；run 删除级联清除其结果和缓存文件。
8. 自动评分默认为 false；评测开始 API 不能同时触发模型评审。只能通过单独的授权动作推进 `awaiting-judge-consent -> judging`；无网络调用结果是该默认值的可验证条件。

### 7.2 Change Impact Tree

以下路径是实现起点，不是完整文件清单；开工前搜索 `create_chat_model`、`AgentModelPreset`、`ConsoleStore`、`stats`、`PRAGMA user_version` 和模型评测页面入口的现有引用，结合同一分支的并行 PRD 更新此树。

```text
.
├── tasks/evaluation-suites/README.md [新增]
│   【总结】定义版本化评测集的目录、样本字段和受限 validation recipe 维护方法
├── tasks/evaluation-suites/example.yaml [新增]
│   【总结】提供不含私有 Issue 内容、可在隔离仓库中运行的 suite 样例
├── config.toml [修改]
│   【总结】增加可选的多个默认评审预设名称及 model-id 引用示例，不写密钥
├── src/backend/core/shared/models/model_evaluation.py [新增]
│   【总结】定义 suite、run、case、rubric、judge 结果与阶段状态的领域值对象
├── src/backend/core/shared/interfaces/evaluation_store.py [新增]
│   【总结】定义 run/result/score 的查询、保存、授权状态 CAS 与级联删除端口
├── src/backend/core/use_cases/model_evaluation.py [新增]
│   【总结】校验评测矩阵、隔离执行编排、显式评分授权、逐评审解析与维度汇总
├── src/backend/engines/model_evaluation/ [新增目录]
│   【总结】用现有 Agent Runner 执行候选，用 create_chat_model 发送无工具结构化 judge 请求
├── src/backend/infrastructure/config/ [修改]
│   【总结】解析评审模型 preset 引用并复用现有模型/provider 凭据加载，不持久化密钥值
├── src/backend/infrastructure/persistence/console_store.py [修改]
│   【总结】为 run、候选结果、逐评审评分增添独立 SQLite 表并安全推进 PRAGMA user_version
├── src/backend/api/routes/agent_runner_console.py [修改]
│   【总结】提供 suite/run/result/judge-preview/explicit-consent/delete API DTO 和错误状态
├── frontend-public/app/(app)/app/evaluations/page.tsx [新增]
│   【总结】提供从样本集/基线/执行候选/多个评审模型到结果对比的 Console 页面
├── frontend-public/components/evaluations/ [新增目录]
│   【总结】呈现运行矩阵、外发 payload 确认、逐评审理由、确定性结果及差异视图
├── frontend-public/lib/api/console.ts + frontend-public/lib/api/types.ts [修改]
│   【总结】为评测 suite/run/score 接口增加共享类型与请求客户端
├── frontend-public/components/layout/ [修改]
│   【总结】将模型评测页面加入 Console 导航并从导航 registry 生成真实入口
├── tests/backend/test_model_evaluation.py [新增]
│   【总结】覆盖隔离矩阵、rubric 解析、状态迁移、分歧汇总及取消语义
├── tests/backend/test_console_evaluation_store.py [新增]
│   【总结】覆盖 schema 版本升级、fresh SQLite 读取、run 删除级联和临时 payload 清理
├── tests/backend/test_console_evaluation_api.py [新增]
│   【总结】覆盖真实路由、授权前零 provider 调用和过期 payload hash 拒绝
├── tests/playwright-e2e/tests/console/model-evaluations.spec.ts [新增]
│   【总结】从生产 Console 路由验证评测创建、payload 预览、拒绝与逐评审结果查看
├── docs/dev/evaluation.md [修改]
│   【总结】记录 suite schema、可复验对照口径、judge授权、结果解释和限制
├── docs/api/references.md [修改]
│   【总结】记录评测 APIs、状态机、版本与外发确认契约
└── mkdocs.yml [修改]
    【总结】把新增评测集维护指南纳入文档导航（若现有导航未覆盖）
```

### 7.3 Risk Classification Register

| Change point | Tier | Decisive reason / intervention | Oracle / gate |
|---|---|---|---|
| 同基线执行矩阵、逐评审分数与“只作比较不作验收”的语义 | R2 | core 评价语义是固定区；需要人确认样本公平性与指标展示 | rv-1，真实 Console + fresh SQLite 同样本执行矩阵、逐模型结果与汇总对照 |
| 外部 judge payload、网络调用、成本及本地暂存/删除 | R3 | 用户代码跨越本机 provider 边界且不可撤回；须人确认并用拒绝授权负控 | rv-2，预览 hash 与实际请求一致；拒绝时 provider 调用数为零 |
| suite 校验、模型响应解析、失败状态和页面过滤 | R1 | 受限于新功能路径，有明确可注入 provider/store 边界 | rv-3，超时/拒答/坏 schema/取消用例与生产页面空态 |
| Console SQLite 新实体与用户删除 | R2 | 持久状态与级联删除可能遗留数据；由人审数据图和 fresh DB 证据 | rv-1/rv-2；迁移、重启读取及 run 删除验证 |

### 7.4 Executor Drift Guard

实现前用以下搜索重定位同层能力并查隐藏使用方；若由 concurrent PR 已创建同名模块或更新路由，先合并其当前边界再继续。任何新增文件、存储字段、公共 API 或 preset 注册路径须回写此 PRD 与 Change Impact Tree。

```bash
rg -n "create_chat_model|AgentModelPreset|PRAGMA user_version|class .*ConsoleStore|stats/agent-performance" src/backend
rg -n "agent_runner\.presets|model_loader|api_key_env|ChatModelSettings" src/backend/infrastructure config.toml
rg -n "app/stats|/app/dashboard|sidebar|navItems|ConsoleNav" frontend-public
rg -n "agent-preset-performance|evaluation-suites|model-evaluation" docs tasks
```

### 7.5 Flow Diagram

```mermaid
flowchart TD
    A["Console 使用者配置 suite、基线和多个执行预设"] --> B["创建 run 快照：suite/rubric/preset/base hashes"]
    B --> C["每个 case × execution preset 创建独立 worktree"]
    C --> D["Agent 执行并运行受限确定性 checks"]
    D --> E["Console SQLite 保存候选状态、检查、时延和 usage"]
    E --> F["生成 judge payload 并显示 provider、model 与确切预览"]
    F -->|"拒绝或取消"| G["保留本地检查结果，judge 状态为未授权/已取消"]
    F -->|"显式确认且 hash 仍匹配"| H["无工具 model adapter 分别调用每个 judge preset"]
    H --> I["校验结构化逐维分数、简短理由和错误状态"]
    I --> J["逐样本逐 judge 保存；从明细计算中位数与分歧范围"]
    J --> K["Console 对比视图：检查与 LLM 评分分开展示"]
```

#### 本地评测数据关系

评测集是 Git 管理的版本化 YAML 文件，不复制成另一套长期数据表。Console SQLite 只保存 run 与本次执行/评分快照；精确 judge payload 只在审批与评分期间位于受限临时目录，数据库保留其 hash。

```mermaid
erDiagram
    EVALUATION_RUN ||--o{ EVALUATION_RESULT : "contains candidates"
    EVALUATION_RESULT ||--o{ EVALUATION_SCORE : "reviewed by"
    EVALUATION_RUN {
        string run_id PK
        string suite_id
        string suite_version
        string suite_hash
        string rubric_version
        string base_commit
        string execution_preset_snapshots
        string judge_preset_snapshots
        string status
        datetime created_at
    }
    EVALUATION_RESULT {
        string result_id PK
        string run_id FK
        string case_id
        string execution_preset_id
        string execution_model_snapshot
        string base_tree_hash
        string check_status
        string result_status
        float duration_seconds
        string usage_summary
        string diff_hash
    }
    EVALUATION_SCORE {
        string score_id PK
        string result_id FK
        string judge_preset_id
        string judge_model_snapshot
        string rubric_version
        string score_status
        string dimension_scores
        string rationale_summary
        string response_hash
        float duration_seconds
        string usage_summary
    }
```

### 7.6 Realistic Validation Plan

```yaml
- id: rv-1
  behavior: "在同一版本 suite / rubric / base commit 下比较多个执行预设；真实 Console 页面展示逐候选检查结果、多个 judge 的逐维分数和可从明细重算的中位数/分歧。"
  reviewer: human
  real_entry: "启动 `uv run kc console`，打开命令输出的 loopback origin 下 `/app/evaluations/`，以隔离的 sanitized fixture suite 启动一轮至少两个 execution preset × 两个 judge preset 对照。"
  expected: "开始前页面准确显示每个 execution preset 的 provider/model 与可得费用估算（未知即明确显示未知）；用户确认运行后，每个 case/preset 均从同一 base tree 独立 worktree 开始；run 与结果在真实 Console SQLite 中 fresh connection 可读；相同 fixture judge 结果按逐维数值算出的中位数、范围、有效数与页面一致；确定性 checks、耗时、LLM 分数分别列示；拒答/坏结果不计为 0 分。"
  mock_boundary: "Console route/page、SQLite、状态迁移、suite/base/worktree 隔离、矩阵和 rubric 汇总都必须真实；运行 Agent/provider 与 chat model 可在 engines/provider 接口替身稳定返回，不连接真实模型；suite 和 repo 使用小型本地隔离 fixture。真实模型调用另经 opt-in sandbox，不把 fixture 结果称 live。"
  tier: R2
  test_layer: e2e
  required_for_acceptance: true
  presentation: "目标对比页与实际结果截图：tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/rv-1-evaluation-results.png；10 秒检查：两个候选、同一 suite/base、两个独立 judge、确定性状态与每维分歧同时可见。"
  critical_value_source: "suite version/content hash、base commit/tree hash、preset snapshots、`evaluation_result` 检查状态、`evaluation_score` per-judge 维度和状态，以及 fresh SQLite 查询。"
  must_cross: "Console 真实导航 → `/app/evaluations/` → suite/run API → 干净 worktree 与 Agent adapter → 命名 checks → SQLite schema/保存 → fresh process 读取 → 两个 judge adapter → SQLite score → UI 汇总。"
  forbidden_bypasses: "从不同 base/sample 拼装伪对比；共享 worktree；仅用前端 fixture 截图；只 mock store / 页面组合；以平均单总分替代 per-judge/per-dimension rows；从 SQLite 缓存而非 fresh read 证明持久化。"
  fresh_state_probe: "关闭并重开 Console，以新的 SQLite connection 和浏览器加载同一 run；核对 suite/rubric/model snapshots、case/judge 明细、聚合值和失败状态保持一致。"
  final_tree_evidence: "保存 run JSON、SQLite fresh-read、worktree base tree 清单、页面截图和 `git rev-parse HEAD` / working tree tree stamp；任何矩阵、schema、聚合、API 或页面变化后重采 rv-1。"
  negative_control: "使用隔离 fixture 的两个 base commit 或人为改变一位 judge 的单维分数后重跑 rv-1；将其中一个候选 worktree 改成共享/污染基线仅在 test boundary 验证时注入。"
  expected_fail: "错误候选基线必须在 run 创建前被拒绝，或明细中检测出 tree hash 不一致；改变一个 judge 分数必须改变对应明细与中位数/范围。若 UI 汇总不变，rv-1 应失败。"
- id: rv-2
  behavior: "每次外部评审只发送用户当前预览过并明确批准的 payload；拒绝、取消、payload 过期或 provider 未配置时不得联系 judge。"
  reviewer: human
  real_entry: "在真实 Console `/app/evaluations/` 完成一个 sanitized local run，打开 judge payload preview，选择一个已配置远端 judge preset 后分别执行拒绝与批准流程；live provider 仅 opt-in。"
  expected: "拒绝时 provider spy/request count 为零；批准要求当前 run 和 manifest hash 匹配，外发模型/提供方与预览一致；真实 opt-in 请求返回后留下评分与 usage（如 provider 给出），拒绝/取消后临时 payload 清除。"
  mock_boundary: "真实 UI、授权 API、payload manifest/hash 校验、SQLite、临时文件生命周期、配置解析与批准状态 CAS 必须真实；网络 provider 在 no-credential 验证中由 test boundary spy 替代；live sandbox 用真实已配置模型且发送 sanitized payload，不作为无凭据 fallback 的替代。"
  tier: R3
  test_layer: e2e
  required_for_acceptance: true
  presentation: "授权确认截图、拒绝路径 provider-call-count 报告与可选 live 请求摘要：tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/rv-2-payload-consent.png / rv-2-consent-negative-control.txt；10 秒检查：预览中可读见目标模型、任务、Diff 和 checks；拒绝后调用数为 0。"
  critical_value_source: "浏览器收到的 payload manifest/hash、Console 授权请求中的 run id/judge preset/hash、provider adapter 收到的 exact serialized content hash 与最终请求模型。"
  must_cross: "Console 页 payload preview → 用户确认/拒绝 → 授权 API → 当前 payload hash fresh compare → provider adapter → 同一 run 的 judge score 保存/拒绝状态 → fresh UI/API 读取。"
  forbidden_bypasses: "run start 时隐式打分；浏览器直接调用 provider；用预览摘要替代实际发送 payload；不校验过期 manifest；向 provider 发送 API key/raw environment/tool transcript；拒绝后仍异步排队评分。"
  fresh_state_probe: "先在新浏览器 session 用与页面同源真实 API 拒绝一次并查看 spy count；再创建另一 run 批准一次，换 fresh Console process 查询对应 score/状态与 payload 文件清理状态。"
  final_tree_evidence: "保存预览截图、request hash 对照、负控调用数、临时目录前后清单、相关配置名（不含密钥）和最终 working tree stamp；授权、序列化、provider 路由或清理变化后必须复跑。"
  negative_control: "经真实页面点击拒绝后检查 test-boundary provider spy 请求数；另在 test boundary 让 payload 在预览后变化再批准。"
  expected_fail: "拒绝流程任何 judge 请求数大于 0 即判失败；payload 改变后批准必须返回 conflict 且 provider 请求数仍为 0。"
- id: rv-3
  behavior: "评测被取消、超时、suite 不可用或模型输出不符合 rubric schema 时保留可解释的独立状态，不污染正式 Agent Runner 数据或产生零分。"
  reviewer: verifier
  real_entry: "`uv run kc console` → `/app/evaluations/`；通过真实 run API 启动隔离 fixture suite，并在 engines 边界注入 timeout、refusal、malformed JSON、cancel 与缺 provider。"
  expected: "对应 case/judge 按实际状态结束；其他候选和 judge 仍可完成；非 score 状态不参与分布统计；正式 Issue、PR、生命周期日志和主工作区均不变；Console restart 后未完成 run 标记 interrupted，不自动重新调用 Agent。"
  mock_boundary: "真实 suite parser、状态机、SQLite、HTTP route、临时 worktree cleanup 和 Console 页面必须真实；provider/Agent 响应只在其专属 adapter boundary 可模拟。"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  presentation: "结构化失败状态报告：tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/rv-3-degraded-states.json"
```

失败分诊：rv-1 先核对 suite/base hash 与 worktree 创建/清理，再看 Console store user_version 和 SQLite fresh connection，最后对照 summary reducer 与页面类型；rv-2 检查 preview manifest 生成、hash 复核位置、授权状态迁移与 provider adapter 的首次调用点；rv-3 对照具体 run/case/judge 状态和进程退出路径，确保只替换 provider adapter、不绕过真实路由/状态持久化。

### 7.7 Low-Fidelity Prototype

本 PRD 的高保真交互目标已由既有 Console 原型承载：[`docs/prototypes/prd-lifecycle-observability.html#evaluations`](../../docs/prototypes/prd-lifecycle-observability.html#evaluations)。目标状态包括多执行预设对照、评审模型设置、多位评审者分维度结果与分歧、checks 与评分分离。该原型只使用静态 fixture，不连接 provider、API 或 SQLite；交付实现截图必须与同状态原型图配对，不能用它证明 provider 授权或真实数据持久化。

### 7.8 Interactive Prototype Change Log

| File Path | Change Type | Before | After | Why |
|---|---|---|---|---|
| `docs/prototypes/prd-lifecycle-observability.html` | Modify | 无执行预设横向对比和 judge 明细视图 | 加入评测设置、多 judge 分项评分/理由/分歧及明细弹窗 | 在实现前评审多个执行模型和多个评分模型的比较关系 |
| `docs/prototypes/assets/runtime-board.css` / `runtime-board-prototype.js` | Add/Modify | 无评测结果与配置交互 | 展示静态 suite、多个预设、独立指标和评分理由 | 让评审可点击走查目标态 |
| `docs/prototypes/assets/prototype-hub.js` | Modify | 原型未登记本次评测入口 | registry 提供评测状态链接、目标图和来源 sidecar | 保持 Hub 是唯一原型目录 |
| `docs/prototypes/assets/runtime-evaluation-preview.png` / `.source.md` | Add | 无目标截图来源 | 保存浏览器目标图及 viewport/state/provenance | 与最终 Console 用户流截图配对 |
| `docs/prototypes/assets/runtime-consent-preview.png` / `.source.md` | Add | 无评审前授权目标图 | 保存完整 payload 预览、接收模型和默认未授权状态 | 评审代码外发与逐次授权边界 |

### 7.9 External Validation

| Topic | Source | Checked On | Relevant Finding | Impact On Recommendation |
|---|---|---|---|---|
| 同样本的多版本比较 | [Langfuse: Compare experiments](https://langfuse.com/docs/evaluation/experiments/compare-experiments) | 2026-10-12 | 实验比较以同一 dataset runs 对齐，并可查看逐样本差异、分数、成本和延迟；只看聚合可能遮住单项退化。 | 固定 suite/version，保留逐 case 明细并单列指标，不只放一个总分。 |
| 多个独立模型评分与 reducer | [Inspect: Multiple scorers](https://inspect.aisi.org.uk/multiple-scorers.html) | 2026-10-12 | 一个样本可保留多个独立 scorer 输出，再用显式 reducer 派生汇总。 | 保留每个 judge 的原始结构化结果；中位数/范围从明细派生而不是覆盖票数。 |
| 模型评分异常 | [Inspect: Model graded evaluations](https://inspect.aisi.org.uk/model-graded.html) | 2026-10-12 | model grader 是可配置评分者；不能解释的答案可以标 abstain/unscored。 | 拒答、解析失败和超时单独留状态，不把它们压成 0 分。 |
| Agent 基准样本与任务执行 | [SWE-bench](https://github.com/SWE-bench/SWE-bench), [OpenHands Benchmarks](https://github.com/OpenHands/benchmarks) | 2026-10-12 | 开源 Agent benchmark 以固定 issue/task 样本和执行环境评估 Agent 解题，而不是仅比较裸模型名称。 | suite 必须固定任务和 base/environment；项目先提供本地、可维护的小型 YAML 版本，不照搬大型托管 benchmark。 |

上述开源实践只支持“同数据对照、保留单项评分和执行证据”的设计选择；将中位数、授权预览、临时 payload 保留期与不设总分用于本项目，是结合本机 Console 单用户/本地 SQLite/用户代码外发边界作出的推断，不是这些项目的原样要求。

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 与运行时看板 PRD 的导航文件软重叠，无 API、数据或构建顺序依赖；评测功能无需等待独立桌面窗口交付。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 要看什么 | 呈递物（实现后填真实截图；当前图是 design intent） | 约 10 秒自检 |
|---|---|---|
| 同 suite/base 下多个执行预设、多个 judge 的分样本结果与分歧 | Prototype: `docs/prototypes/assets/runtime-evaluation-preview.png`；production capture: `tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/rv-1-evaluation-results.png` | 核对 suite/base 一致，两个 execution preset 与两个 judge 可展开；checks 与评分不同列，逐维分数、理由及范围可见。 |
| 外部评审授权前的准确数据、提供方和拒绝结果 | Prototype: `docs/prototypes/assets/runtime-consent-preview.png`；production capture/负控: `tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/rv-2-payload-consent.png` 与 `rv-2-consent-negative-control.txt` | 核对预览包含实际发送 Diff 与 provider/model；拒绝后调用数为零、暂存 payload 已删除。 |

**当前目标图（component preview / design intent；不是生产入口证明）**

![多执行模型、多评审者的结果对照目标态](../../docs/prototypes/assets/runtime-evaluation-preview.png)

> 验证层级：**component preview / design intent**；候选结果、评分和统计均为静态 fixture。
> 本地打开：`open docs/prototypes/assets/runtime-evaluation-preview.png`

![评审 payload 外发授权目标态](../../docs/prototypes/assets/runtime-consent-preview.png)

> 验证层级：**component preview / design intent**；授权/拒绝仅切换页面状态，不连接模型 provider。
> 本地打开：`open docs/prototypes/assets/runtime-consent-preview.png`

交付时每行须提供同状态生产截图/报告，目标图为设计意图，不能替代 run API、SQLite 或实际 payload 授权证据。从真实 Console `/app/evaluations/` 进入；验收材料可用 `just prd review tasks/archive/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md` 查看（人审清单见 `tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/human-review-checklist.html`）。

`reviewer: verifier` 的 rubric/schema/failure 单测、状态机与 interrupted 重启断言、并发限流、模型密钥不落库、文档构建和 suite parser 负例属于自动验证，故不列入人读呈递区。

### 9.2 Acceptance Evidence Package

#### Human-Confirmed

- [ ] 确认分数语义按 §2 决策一：不同 judge 的独立判断和 deterministic checks 分开展示，不计算默认加权总分；任何分数不成为 CI、合并、发布或自动路由门禁。（对照 `rv-1` 与 prototype/production 配对。）
- [ ] 确认外发和本地数据范围按 §2 决策二：模型调用前逐次查看实际 payload 并确认；评测结果本地保存，拒绝/完成后临时 payload 清除；API key 和完整 transcript 不落库。（对照 `rv-2` 预览、真实请求 hash、拒绝负控和 fresh deletion 证据。）

#### Behavior Acceptance

- [x] 同一 run 固定 suite version/hash、rubric version/hash、base commit/tree hash 和执行/评审模型快照；执行矩阵的每个组合都从干净 worktree 开始，checks 用受限 recipe 执行。（`rv-1` tree manifest 与 run JSON。） — 证据：`rv-1-tree-stamps.txt`（SUITE_HASH_MATCHES_FILE / BASE_COMMIT_MATCHES_REPO / BASE_TREE_UNIFORM 全 True；badref 负控创建 400）、`rv-1-run-detail.json`（矩阵 4 候选 checks 分列 passed/failed）。
- [x] 每个 judge 在每个 candidate/case 单独留分、维度依据、model/prompt snapshot、耗时、usage/成本可用性和结果状态；修改逐评审值能改变重算摘要。（`rv-1` fresh DB 与 aggregation 重算。） — 证据：`rv-1-sqlite-fresh-read.txt`（8 条逐评审行、user_version 15）、API 中位数与独立 `statistics.median` 重算一致（MEDIAN_RECOMPUTE_MATCH）、单值篡改即改变中位数/范围（MEDIAN_SENSITIVE_TO_DETAIL→EXPECTED_FAIL，还原后 RESTORED_OK）。
- [x] deterministic check、执行结果、稳定性、latency/cost 与模型评分分区呈现；没有 usage/价格事实时标注未知，不填补估算。（页面截图与 DTO。） — 证据：`rv-1-evaluation-results.png`（checks 徽标与评分分列）、`rv-1-run-detail.json` DTO（duration/usage 与 score 分列；cost 统一 unknown，未造数）。
- [x] timeout、拒答、无效/缺字段 JSON、取消、provider error、suite 错误、未授权和 interrupted 各自可解释；未评分既不进入评分中位数也不成为 0 分；其他 case/judge 不因单一失败丢失。（`rv-3`。） — 证据：`rv-3-degraded-states.json` 状态桶 {valid:2,refusal:2,invalid:2,timeout:2,provider_error:2} 无任何 0 值、伪 0 分注入被 oracle 检出后还原（COERCION_DETECTED/RESTORED_OK）、无效 suite 创建 404 / 缺 key 评审预设创建 400、取消 200+重复 409、kill -9 后 interrupted 且 Agent 调用数 2→2。
- [x] 评测 run 不触发正式 Issue / PR /生命周期状态变更，Agent 不接触主工作区；执行 worktree、缓存 payload 和 database run 均能按结束/删除策略清理。（`rv-2`/`rv-3` 清单。） — 证据：`rv-3-degraded-states.json` formal_ledger_untouched（逐表不变且全 0；monitor/backlog 快照表为 Console lifespan 后台调度器心跳，已注明归因）、`rv-1`/`rv-2` STAGING/WORKTREES_PURGED、删除 run 200→404 级联清理。

#### Frontend Acceptance

- [x] 从真实 Console 导航进入 `/app/evaluations/` 可选 suite、基线、多个执行预设、多个 judge preset，显示矩阵状态与逐项结果；浏览器刷新后能从 fresh API 读取持久 run。（Playwright user flow + `rv-1-evaluation-results.png`。） — 证据：rv-1 headless Chromium 真实页面全流程（PAGE_* 标记）+ 重启 Console 后 fresh read 一致（FRESH_RESTART_MATCH）；Playwright spec `tests/playwright-e2e/tests/workflows/console-evaluations.no-auth.spec.ts`（2026-10-13 最终树 `just e2e` 复跑 3 passed，捕获 `rv-1-playwright-spec-run.txt`；spec 曾有三处断言写错目标，修订见 Change Log）。
- [x] Payload preview 具备 provider/model、任务、summary、Diff 与 check 结果的可读展开、敏感字段提示、费用/usage 可得性和单独确认/拒绝；过期内容需重新确认；拒绝在生产 composition 里可验。（`rv-2`，不以直接渲染 modal 代替页面入口。） — 证据：`rv-2-payload-consent.png`（真实 Dialog 展开 diff/模型/manifest hash）、`rv-2-after-page-deny.png`（页面拒绝路径）、篡改后 409 必须重新预览（STALE_APPROVE_CONFLICT + 漂移 hash 亦 409）。
- [x] 结果页可以定位 case、execution preset、judge、缺失/失败项；小屏 390×844 可滚动，无横向溢出且不会把表格裁成不可操作。（Playwright viewport 断言与截图。） — 证据：`rv-1-narrow-390x844.png` + NARROW_SCROLL_VS_CLIENT 390/390、NARROW_TABLES_IN_SCROLLER true；负控注入 900px 溢出块探针变红（NARROW_OVERFLOW_NEGCONTROL true）。

#### Architecture Acceptance

- [x] 依赖方向维持 `api -> core -> engines -> infrastructure`；suite 规则、rubric 验证、分数 reducer 属 core；React 页面不自行计算评分口径。 — 证据：`src/backend/core/shared/models/model_evaluation.py`（parse/validate/rubric 常量）与 `core/use_cases/model_evaluation.py`（聚合 reducer）；engines 仅做 IO 编排；`frontend-public/components/evaluations/*` 只渲染 API summary 字段，无中位数计算。
- [x] Judge adapter 使用 `create_chat_model` 或经审查等价的无工具 model call；评审请求不复用可运行 shell/仓库工具的 Agent task profile。credential、provider、model 在服务端解析，不由客户端提交密钥。 — 证据：`engines/model_evaluation/judge_client.py` 经 `create_chat_model`（llm extras，需 `uv sync --extra llm`）；rv-1/rv-2 mock HTTP 计数（8 请求/模型分布 4+4）证明真实调用链；请求体不含 key；预设可用性探针只读本地凭据链。
- [x] suite recipe 不拼 shell 字符串，不继承任意用户环境、不在主工作区执行；Agent/模型调用数量受预算和既有并发上限限制；run 创建、授权和删除有幂等/并发状态约束。 — 证据：`engines/model_evaluation/checks.py` argv 白名单（pytest/unittest/node-test）、`execution.py` ThreadPoolExecutor ceiling=min(max_concurrency, cells)；rv-1 AGENT_INVOCATIONS=4/MOCK=8 与矩阵严格一致；授权三 hash 复核 409、重复取消 409（rv-2/rv-3）。
- [x] SQLite schema 迁移遵守 ConsoleStore `PRAGMA user_version` 路径，升级后现有 Console 日志、Stats、Invocation 查询保持原样；run 删除级联清除相关行和临时文件。 — 证据：rv-1 SQLITE_USER_VERSION=15 fresh read；`just test` 4280 passed 覆盖既有 store 测试；rv-3 删除 run → 404 + worktrees/staging 清理，级联见 `console_store_evaluation.py`。

#### Documentation Acceptance

- [x] `docs/dev/evaluation.md` 记录 suite YAML 字段、版本/hash、授权流程、rubric v1、逐评审解读、删除/保留和无凭据验证入口；`mkdocs.yml` 若新增长期页面则同步导航。 — 证据：重写后的 `docs/dev/evaluation.md`（2026-10-13）+ `tasks/evaluation-suites/README.md`；`uv run mkdocs build --strict` exit 0（`dev/evaluation.md` 已在导航 `mkdocs.yml:93`）。
- [x] `docs/api/references.md` 记录 suite/run/result/preview/approve/delete APIs，状态枚举、Conflict/error 语义、默认不开启 judge 和 secret/payload 边界。 — 证据：`docs/api/references.md`「模型评测 Console 端点」节（10 端点 + 枚举 + 404/409/400 映射）；`uv run mkdocs build --strict` exit 0。
- [x] 如新增 config key，`config.toml`、settings loader 和配置参考文档同步；密钥仍仅由现有 env 注入、`.env.example` 只保留未注释空密钥项，不把预设模型值写为秘密。 — 证据：`config.toml` 注释示例段 + `EvaluationJudgePresetSettings`/`AgentRunnerEvaluationSettings`（只存 env 名，不存值）+ `docs/guides/configuration.md`「Agent Runner 模型评测配置」节；`.env.example` 未改动（无新密钥变量）。
- [x] Prototype Hub registry、原型返回入口、截图 sidecar、说明页和窄屏行为一致；现有 `/app/stats` 统计仍为单独的历史执行数据入口。 — 证据：`docs/prototypes/assets/prototype-hub.js:21-24` 已登记评测流程；`runtime-evaluation-preview.png`/`.source.md` 与 `runtime-consent-preview.png`/`.source.md` sidecar 成对存在；Stats 页未改动，评测入口为独立 `/app/evaluations/`。

#### Validation Acceptance

- [x] `rv-1` 通过真实 `kc console` 与页面/API、真实 SQLite store/schema、隔离 fixture repo/worktree 采集同批矩阵、fresh process 查询、逐评审记录和摘要重算；provider 在自动测试边界替身可用。 — 证据：`evidence.json` item 1（RV1_GREEN + 全部 stdout_assertions 命中，脚本 `scripts/rv1.py`/`scripts/rv_narrow.py`）。
- [x] `rv-2` 做拒绝/批准两条真实页面路径；负控用 provider spy 核实拒绝后请求数为零、payload 改变后 approval 失败；live provider run 使用 sanitized suite 与用户 opt-in 凭据，保存 provider/model 与 usage，不提交任何秘密。 — 证据：`evidence.json` item 2（REQUEST_COUNT_AFTER_PAGE_DENY: 0、STALE_APPROVE_CONFLICT: True、批准后 mock 恰 8）；**live provider 未执行**（本机无凭据，按 PRD 属用户 opt-in，已在 `rv-2-consent-negative-control.txt` LIVE_PROVIDER_NOTE 披露，mock 不称 live）。
- [x] `rv-3` 用 suite/provider boundary 注入 timeout/refusal/schema 错误/cancel/restart，在真实 store/API 中证明未评分为非 score 状态、未自动重试、其余结果仍可读取。 — 证据：`evidence.json` item 3 + `rv-3-degraded-states.json`（状态桶、interrupted 调用数 2→2、账本 unchanged）。
- [x] 配套 targeted Python 与 Playwright 用户流程、`uv run mkdocs build --strict`、前端 typecheck/build 按 `docs/ai-standards/testing.md` 分类记录；若无法访问真实模型服务，明确披露 live provider 未覆盖，不把 mock 称为 live。 — 证据：`uv run pytest tests/test_model_evaluation_domain.py tests/test_console_evaluation_store.py tests/test_console_evaluation_api.py -o addopts=""`（本地核心档）与 `just test` 4280 passed / 1 skipped（2026-10-13 重验）；`uv run mkdocs build --strict` exit 0（文档修正后重跑）；前端 typecheck/build 见任务 #6；live 披露同 item 2。2026-10-13 最终树 `e94f24b4` 复验：定向 44 项 pytest passed、`just e2e` 评测 spec 3 passed、`uv run mkdocs build --strict` exit 0、`pnpm typecheck` clean，RV 剧本重跑记录见 Change Log「最终树证据重采集」。
- [~] 独立 verifier 对 R2/R3 证据、同基线矩阵和外发授权负控复核 PASS — runner-owned gate。
- [~] PR 证据发布、主线归档与最终 tree 等价复核 — runner-owned gate。

#### Delivery Readiness

- [x] §9.1 每行都由相同状态的原型/生产配对及可读产物支撑；completion message 内嵌目标图与实现图，并携带表格内容。 — 生产截图 `rv-1-evaluation-results.png` / `rv-2-payload-consent.png` / `rv-2-after-page-deny.png` / `rv-1-narrow-390x844.png` 已与同状态原型配对；completion message 携带 §9.1 表格内容与图片引用。
- [x] 所有非 Human-Confirmed 项勾选或标 `[~]` runner gate，证据对应具体 run/hash/final tree；§13 Final Reconciliation 完成且 banner 与 §9 状态一致。 — run 标识见 `rv-1-tree-stamps.txt`（PAGE run id、manifest hash、base commit/tree、suite hash）与 `rv-3-degraded-states.json`（各场景 run_id）；`evidence.json` 逐 item 绑定 stdout 断言与证据文件；两条 runner-owned gate 保持 `[~]` 待 PR/verifier 环节。

## 10. Functional Requirements

- **FR-1**：用户可从 Console 导航启动一轮手动评测；选择一个版本化 suite、固定 base ref/rubric、两个或更多 execution preset；suite version、内容 hash、base commit/tree 和解析 preset snapshot 在 run 创建时固定。
- **FR-2**：run 为每个 execution preset × case 建立独立干净 worktree，运行 Agent 和 suite 声明的命名 deterministic check recipe；结果包含状态、exit/check outcome、wall time 与真实可得 usage/cost，不改正式 Issue/PR/main worktree。
- **FR-3**：可配置多个有名 judge presets 并为单个 run 选择一个或多个；judge 使用无工具的文本模型调用，按 rubric v1 独立评审每个 candidate/case，逐 judge 保存模型版本、维度评分、短依据、耗时、usage、hash 与错误态。
- **FR-4**：结果页将确定性 check、Agent 运行指标与 LLM 评分分开展示；同一维度从有效 judge 明细动态计算中位数、min/max 分歧范围和 N；无效/失败/未授权结果永不变为零分，页面不生成默认 weighted total 或 pass gate。
- **FR-5**：Agent 运行结束默认停在 `awaiting-judge-consent`；预览显示每个目标 provider/model 及准确 payload。用户批准后服务端检查 preview hash 一致才调用 judge；拒绝、取消、过期 hash 不发请求。运行开始、CI、CLI、daemon 不触发自动评分。
- **FR-6**：Console SQLite 持久保存 run、case result 和逐 judge score 快照，支持 fresh read、按条件过滤与删除；暂存 payload/worktree 有限生命周期，密钥和原始工具 transcript 不落库。Console 重启后未完成任务标记 interrupted，不自动执行重复的 Agent/provider 请求。

## 11. Non-Goals

- 不改已有历史 Stats 统计口径、现有 Agent Runner 生命周期、Verifier 判定或 PR merge gate。
- 不新增 `kc` CLI 子命令/旗标、CI 自动评分、自动选择模型、主观总分权重或质量门槛。
- 不导入任意外部 benchmark 服务、不自动采集 Issues、不建设 suite Web CRUD 编辑器/多租户共享/排行榜。
- 不在 judge prompt 或本地持久层放原始 API key、完整环境、全部 Agent tool transcript 或 entire repository archive。
- 不支持评审模型 tool calling / function calling，不让 judge 在 candidate worktree 内执行；不因评分重跑已完成的 Agent。

## 12. Risks And Follow-Ups

- **同样本仍可能有环境差异**：固定 base tree、依赖安装规则、超时和 worktree；在 run 详情显示运行环境快照，不能把一次随机波动描述为稳定优劣。
- **LLM judge 自身偏差**：每位评审者独立展示；每维中位数与范围不能代替原始判断。用户可增加评审者或人工复核，不做统计显著性承诺。
- **代码外发和提示注入**：payload 视为不可信代码文本，仅由无工具 model call 读取；无自动发送，展示精确内容与接收 provider，每次 run 重新授权；用户批准前显示内容可能含机密的提示。
- **并发/成本增长**：候选数量 × case 数 × judge 数构成调用上界；创建 run 前显示请求数、并发限制及能获取到的成本信息，单 run 有最大矩阵大小/超时，不以未知价推断金额。
- **Console SQLite 成长**：默认只保留短评分理由和结构化指标；run 删除级联清理，提供列表中本地数据大小或记录数量，以触发清理策略后续评估。
- **历史重评与 rubric 改版**：v1 不用新 rubric 悄悄覆盖旧分数；如要比较历史产物需独立“重新评审”用户动作和新的 consent/payload hash，留待实际用户需要后再做。
- **模型 registry 一致性**：judge alias 引用 provider 已知 model id；缺凭据或 model/provider 无法解析时显示不可用，不用执行 Agent preset 回退。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 执行效果比较单位 | 同一 suite version × base tree 的隔离 case/preset 矩阵 | 不同时间的历史 attempt 汇总 | 控制任务难度与代码基线，结果更可解释并可复验。 |
| D-02 | 评审模型配置 | 多个命名 judge preset 引用现有 provider/model registry 与 env 凭据 | 复用有 shell / repo tool 的执行 Agent 评审 | judge 只需文本推理；独立模型调用避免把任务工具权限交给评审者。 |
| D-03 | 分数聚合 | 每位评审者原始结果 + 每维中位数/范围/N | 默认加权总分或 pass/fail | 让用户看到分歧，避免把尚未校准的主观值当作真值。 |
| D-04 | 外发时机 | 单次 run 的 payload 预览后显式授权 | 自动后台评分 | 代码外发不可撤回且可能产生费用，需要明确逐次确认。 |
| D-05 | 结果用途 | 辅助比较，人工结合确定性证据解释 | 自动进入 CI/合并/路由决策 | 项目当前没有经校准的评分阈值，自动门禁会放大 judge 偏差。 |

### Final Reconciliation

- Interpretation: 已确认 — §1 解读回显五行全部有真实入口证据：同 suite/base 多预设对照与逐维分歧（rv-1）、授权前逐字节预览与显式批准（rv-2 页面批准/拒绝双路径）、失败态独立不折算 0 分（rv-3 状态桶 + 伪 0 分负控）、拒绝/取消零外发（rv-2 REQUEST_COUNT 0 / rv-3 mock delta 0）。
- Public behavior and contracts: 已确认 — `/app/evaluations/` 页面、run 状态机（created→…→awaiting_judge_consent→judging→completed/cancelled/interrupted）、payload manifest/consent 三 hash 语义与结果 DTO 与 `docs/api/references.md` 一致；400/404/409 映射经 rv-1/rv-2/rv-3 实测。
- Related PRD status: 已复核 — 运行时 Dashboard（P1-FEAT-20261012-103222）导航与 Stats 历史数据入口未改动；评测为独立 `/app/evaluations/` 路径，judge 分数不参与任何生命周期门禁。
- Requirements and risks: 已确认 — 数据保留（摘要落库、raw 不落库）、外发边界（授权前零 provider 调用）、judge 错误态分列、并发上限与调用数一致、评分非门禁语义均有证据；残余风险见 §12（live provider 未覆盖、rubric v2、成本事实源）。
- Reconciled differences:
  - 执行期实现偏差与缺陷修复已记录于 Change Log（probe_base 400 化、worktree 父目录清理、rubric 维度文档纠错、llm extras 前置、settings 下限），均不改需求语义；rubric 维度文档名错误为开工前既有文案问题，已修正并与 core 常量对齐。

## Change Log

### 交付门禁 flake 修复：kc preview 僵尸误判（2026-10-13，executor）
- 类型: test
- 原文: runner 门禁 `just test all` 在 `tests/test_kc_preview.py::test_stop_terminates_the_whole_owned_group` 变红（`assert not _pid_alive(grandchild_pid)`，4280 项其余全绿）。该测试与评测功能无关且与 main 一致：`_pid_alive` 仅在 Linux 分支排除僵尸，macOS 分支用 `os.kill(pid, 0)` 把「已随组退出、launchd 尚未回收」的孙进程僵尸表项误报为存活；产品侧 `_terminate_process_group` 本就在宽限期内轮询到进程组排空才返回。全量并行负载放大回收窗口导致偶发假阳。
- 变更后: `_pid_alive` 增加 darwin 分支，用 `ps -o state=` 把 `Z` 状态判为已退出；新增 `_wait_pid_gone`（≤5s 轮询）替换两处即时断言——真实存活的孤儿进程（sleep 300）依旧会变红。自证：live→alive、未回收→`_wait_pid_gone` 返回 False、SIGKILL+wait→gone、SIGKILL 不 wait（Z 态）→判死。`tests/test_kc_preview.py` 22 passed，`just test all` 复跑通过。
- 原因: 门禁必须全绿才可交付；按测试时序缺陷根因修复断言的存活判定，不 skip、不弱化检查。
- 影响: 仅测试文件；无产品代码与 §10 FR 语义变化。
- 审核: 执行侧自证（复现→根因→负控→修复→全量复跑）。

### e2e spec 交付缺口修复（2026-10-13，executor）
- 类型: test / bug-fix（交付物缺陷）
- 原文: `tests/playwright-e2e/tests/workflows/console-evaluations.no-auth.spec.ts` 随 `ca184bc0` 提交并在 §9 Frontend Acceptance 中被引用为证据，但从未在全栈环境跑通过：①mock `default_judge_presets: ["judge-1"]` 时页面会预选 judge-1，spec 再点击反而取消勾选，创建请求带 `judge_presets: []`；②断言文案「预计请求数： 2」与实际渲染「预计请求数：2」不符；③授权 409 的错误经 sonner toast 呈报，spec 却在对话框容器内找「授权提交失败…」。CI 未接入 Playwright e2e，红 spec 未被门禁发现。
- 变更后: spec 对齐真实 UI 契约——judge-1 断言预选 `toBeChecked`（不再点击）、文案去掉多余空格、409 错误断言改到 `[data-sonner-toaster]` 并补「对话框保持打开 + run 仍待授权」断言。`just e2e tests/workflows/console-evaluations.no-auth.spec.ts` 在最终树 `e94f24b4` 复跑 **3 passed**（捕获：`rv-1-playwright-spec-run.txt`）。
- 原因: §9 第 43 项把该 spec 列为 Frontend Acceptance 证据，证据文件本身必须真实可跑绿；三处偏差均为 spec 写错断言目标/文案，产品行为（预选、toast 报错、hash 冲突重新预览）与 PRD 授权红线一致，不改产品代码。
- 影响: 仅测试文件；不改 §10 FR 语义与 rv-1/rv-2/rv-3 剧本断言。补强「创建/启动绝不触发评审端点」「deny 留 unauthorized 行」「过期 hash 409 保留预览」三条红线的浏览器级回归网。
- 审核: 执行侧自证（红→修复→绿全记录）；`Human-Confirmed` 不受影响。

### 最终树证据重采集（2026-10-13，executor）
- 类型: evidence
- 原文: Pre-PR 评审修复条目声明 rv-1/rv-2/rv-3 证据采集于修复前树 `ca184bc0`，修复后行为由单元测试代证。
- 变更后: 在最终树 `e94f24b4` 完整重跑三个 RV 剧本（含各自负控先红后绿）与 `rv_narrow.py`，全部转绿；新增 stdout 捕获 `rv-1-final-tree-run.txt` / `rv-2-final-tree-run.txt` / `rv-3-final-tree-run.txt`，截图、run JSON、fresh SQLite 读取与退化状态报告同批再生成；`evidence.json` 登记 `recollected_at_final_tree` 并把三份新捕获并入各 item 的 `evidence_files`。另在最终树重验 44 项评测定向测试（passed）、`uv run mkdocs build --strict`（exit 0）、前端 `pnpm typecheck`（clean）。
- 原因: 证据完整性要求关键 RV 证据绑定最终实现树；修复触碰了 consent/预览/状态迁移路径，重采集消除对修复前树的依赖。
- 影响: 不改任何需求语义与代码；rv-1/rv-2/rv-3 验收断言的通过状态由最终树自身证据支撑。
- 审核: 执行侧自证；banner 与 §9 状态不变（仍 `🧍 待人工验收`，Human-Confirmed 两项保持未勾）。

### Pre-PR 评审修复（2026-10-13，reviewer）
- 类型: bug-fix / documentation
- 原文: 首轮 pre-PR 评审发现三处交付缺口——①候选执行与 judge 调用按**实时配置**解析预设，run 创建后配置漂移会让「所批即所发」失真；②payload 预览上限 40k 字符与文档「逐字节一致的完整预览」不符；③consent/cancel 的状态迁移冲突（`EvaluationStoreConflictError`）会以 500 逃逸而非 409。
- 变更后: 新增 `preset_snapshot_mismatch` 领域函数并在 `_execute_cell` / `_execute_judge` 调用前核对冻结快照（agent/model/reasoning_effort 与 model/temperature/api_key_env/base_url），漂移即停并落可解释失败态、零外发；judge 快照补冻结 `base_url`；预览上限提到 250k 字符（覆盖 diff 字节上限的最坏尺寸），超出显式置 `payload_truncated=true` 且 hash 仍绑定全文；consent/cancel/start 迁移冲突统一映射 409，deny 收尾路径保持写 `unauthorized` 评分行并清除暂存 payload；`docs/api/references.md` 与 `docs/dev/evaluation.md` 同步措辞。
- 原因: 三处均为评审确认的实现缺口而非需求语义变化——PRD 解读回显承诺的「冻结快照」「所批即所发」「错误可解释」需要在配置漂移与并发竞争下仍然成立。
- 影响: 不改 §10 FR 语义与对外 API 形状（预览响应仅新增/明确既有 `payload_truncated` 字段语义）。rv-1/rv-2/rv-3 证据包采集于修复前树 `ca184bc0`，其断言路径（干净 worktree、hash 三比对、deny 零外发、失败态分列）在修复后行为不变；漂移守卫与 409 映射由修复新增的单元测试与 API/领域测试（`tests/test_model_evaluation_domain.py` 等 44 项）在最终树验证。
- 审核: reviewer 修复随 pre-PR 门禁提交；`just test all`、pre-commit 与 `uv run mkdocs build --strict` 在最终树通过。

### Pre-PR 复审修复（2026-10-13，reviewer 第二轮）
- 类型: bug-fix / code-quality / validation / documentation
- 原文: 第二轮 pre-PR 复审在 `e94f24b4`/`ef1f253c` 之后发现五处交付缺口——①core 残留未被调用的 `apply_consent_outcome` / `ConsentDecisionOutcome` / `manifest_hash_for_entries`，其中 `apply_consent_outcome` 用 `run.manifest_hash` 自己和自己比，一旦被接线授权红线即失效；②在 `awaiting_judge_consent` 上取消只落候选状态，不写任何评审留痕，页面无法区分「评审被取消」与「没选评审者」，`SCORE_STATUS_META.cancelled` 成为不可达文案；③`evaluation.max_concurrency` 与 runner capacity 的取小是 **per-run** 的，外层池允许 4 个 run 并行，主机上在飞候选实际可叠成 N × ceiling，与 `config.toml`/文档承诺的「尊重与 runner 相同的主机 capacity」不符；④页面把 `default_judge_presets` 全量预选，凭据不可解析的默认项对应禁用的勾选框，形成既不能提交也不能取消的死锁；⑤`_execute_cell` / `_execute_judge` 的冻结快照漂移守卫与授权后 payload hash 复校没有任何 engines 边界测试（HTTP 层桩掉了整条执行链）。
- 变更后: 删除三处死代码（授权语义唯一入口回到 `service.submit_consent` 的三 hash 复校）；`build_unauthorized_scores` 泛化为 `build_unreviewed_scores(run, results, *, score_status, rationale_summary)` 并显式拒绝 `valid`，`cancel_run` 在授权前取消路径复用该 helper 为未评组合补 `cancelled` 行（已评组合跳过，不覆盖 deny/评审结果）；`execution.py` 新增进程级 `_host_gate`（`BoundedSemaphore(min(max_concurrency, runner.max_concurrent_issues))`），候选的 worktree + Agent + check + 清理全程持闸，并在取得槽位后重新确认 run 仍为 `running`，排队期间被取消的单元落 `cancelled` 且零 Agent 调用；`evaluations/page.tsx` 默认评审预设只预选目录在场且 `available` 的项；新增 `tests/test_model_evaluation_execution.py`（候选/评审配置漂移零外发、授权后 payload hash 不符拒发、闸门排队期间取消不补发、跨 run 在飞候选数 == ceiling）与 API 层授权前取消留痕用例；`docs/api/references.md`（cancel 留痕 + 评分状态枚举补 `cancelled`）、`docs/dev/evaluation.md`（冻结快照即执行前守卫、进程级并发口径、deny/cancel 留痕）、`config.toml`（默认预选需可用、并发为全进程口径）同步；§9 证据标注更正为「无效 suite 创建 404 / 缺 key 评审预设创建 400」（与 `rv-3-degraded-states.json` 实测一致）。
- 原因: ①②属评审确认的实现缺口与状态语义不完整（不改 FR，但 §10「未授权/取消各自可解释」的留痕只实现了 deny 一半）；③是配置注释与文档已承诺却未落实的行为，属需求与实现不一致；④是真实可用性缺陷；⑤是关键红线只有单元测试代证、缺接口/边界级证据。
- 影响: 对外 API 形状不变（`scores` 里可能多出 `cancelled` 行，前端文案与聚合口径已就位；`valid` 聚合与非 0 折算红线不受影响）。rv-1/rv-2/rv-3 已捕获证据仍绑定其原树语义：rv-3 取消场景发生在 `running`（不产生新留痕行），deny 文案与 hash 语义未改；闸门与漂移守卫的新证据由本轮新增测试提供，live provider 仍未覆盖。
- 审核: reviewer 修复随 pre-PR 门禁提交；最终树复跑评测定向 pytest、`just test`、pre-commit、`uv run mkdocs build --strict`、`pnpm typecheck` 与评测 Playwright spec（页面本轮有改动）。banner 与 §9 状态不变（仍 `🧍 待人工验收`，Human-Confirmed 两项保持未勾）。

### Pre-PR 复审修复（2026-10-13，reviewer 第三轮）
- 类型: bug-fix / validation / documentation
- 原文: 第三轮 pre-PR 复审在 `35332e28` 之后发现两处交付缺口——①页面为活跃 run 显式提供「强制删除」，但 `delete_evaluation_run` 删行后，仍在执行的候选/judge 线程持有内存记录并回写：`evaluation_result` / `evaluation_score` 以 `INSERT OR REPLACE` 重建已被删除的行（`PRAGMA foreign_keys` 未开启），孤儿行永久残留，违背「删除评测 run 后其 SQLite 结果与暂存 payload 一并删除」；且 judging 期间删除后，预先排好的 `_execute_judge` future 仍会继续向 provider 派发调用。②`docs/api/references.md`、`docs/dev/evaluation.md` 与两处 docstring 把 interrupted 自愈描述为「等待用户显式恢复或删除」，但状态机不提供任何 interrupted→可执行 的恢复路径，措辞承诺了不存在的能力。
- 变更后: `console_store_evaluation.py` 的 `save_evaluation_result` / `save_evaluation_score` 改为 `INSERT OR REPLACE … SELECT … WHERE EXISTS (SELECT 1 FROM evaluation_run WHERE run_id = ?)`，写入与 run 存在性原子绑定，删除后的残余回写被静默丢弃；`_execute_judge` 在每次 provider 调用前自检 run 仍存在且处于 `judging`，被删除的 run 不再追加派发（在飞 HTTP 无法撤回，但不产生新调用与残行）；`docs/dev/evaluation.md` / `docs/api/references.md` 增补强制删除的收敛语义，「显式恢复」措辞统一更正为「新建 run 重跑或删除」（含 `recover_interrupted_runs` docstring 与 `cli_typer_console.py` 自愈注释、`mark_interrupted` 用户可见错误文案）。新增回归测试：`tests/test_console_evaluation_store.py::test_writes_after_run_delete_are_dropped`（删除后回写不留孤儿行，含裸 SQL 计数断言）与 `tests/test_model_evaluation_execution.py::test_judge_dispatch_stops_after_force_delete`（删除后 provider 调用数为 0、零评分行）。
- 原因: ①是删除生命周期承诺在并发线程模型下的真实破口，属缺陷而非需求变化；②是文档承诺了实现中不存在的恢复能力，属文档与状态机不符。
- 影响: 对外 API 形状不变；rv-1/rv-2/rv-3 既有证据的删除场景针对终态 run，行为不受本轮修复影响（本轮以引擎/存储边界测试补强活跃 run 强制删除路径）；授权红线、hash 语义、评分聚合口径零变化。
- 审核: reviewer 修复随 pre-PR 门禁提交；评测定向 pytest 84 passed（`--no-testmon` 强制全跑）、`tests/test_console*` 145 passed、`SKIP=check-test-flag uv run pre-commit run --all-files` exit 0、`uv run mkdocs build --strict` exit 0；banner 与 §9 状态不变（仍 `🧍 待人工验收`，Human-Confirmed 两项保持未勾）。

### Pre-PR 复审修复（2026-10-13，reviewer 第四轮）
- 类型: bug-fix / validation / documentation / code-quality
- 原文: 第四轮 pre-PR 复审在 `03cd1a50` 之后发现四处交付缺口——①暂存 payload（候选代码 diff）在两条收敛路径上不会被清除：Console 重启自愈只改 DB 状态、不删 `runs/<run_id>/` 暂存目录，矩阵收尾若在「读取状态」与 running→awaiting_judge_consent 这次 CAS 之间被并发 cancel/force delete 抢先，则 manifest 已落盘而 run 永远停在 `cancelled`，两者都留下无人负责的代码差异，与 §10「评分完成、拒绝、取消或删除时清除暂存 payload」的保留红线不符；②`tasks/evaluation-suites/README.md` 与 `example.yaml` 承诺「同一目录内 suite `id` 重复会让两个文件一起校验失败」，但 `list_suites()` 只做逐文件解析，重复 id 仍各自返回 `valid=true`，页面下拉同时出现两个同名可选项而 `load_suite` 会在创建 run 时才报 400；③页面删除当前选中 run 后，`runAction` 闭包里的 `selectedRunId` 仍是删除前的值，收尾会对已消失的 run 再发一次详情请求，成功删除反而弹「无法加载 run 详情」的错误 toast，详情面板也继续显示一条不存在的 run；④端口模块 `evaluation_store.py` 定义了从未被抛出或导入的 `EvaluationStoreError` / `EvaluationStateConflictError`，`transition_run` 与端口的 docstring 又以 `Raises`/`抛 :class:` 承诺这个不存在的异常类。
- 变更后: `service.recover_interrupted()` 先按 `ACTIVE_RUN_STATUSES` 收集活跃 run，标记 interrupted 后逐个 `purge_run_artifacts()`（worktree 清理仍留在 `delete_run`，不把 git 压在启动路径）；`run_run_execution()` 收尾把状态迁移收敛为单次 CAS 并捕获 `EvaluationStoreConflictError`，冲突即清除刚写入的 manifest/payload 后返回，不再让异常逃进 `_safe_entry` 记一次崩溃；`service.list_suites()` 统计跨文件 `suite_id` 出现次数，重复条目经 `_mark_duplicate_suite_entry()` 降级为 `valid=false` + 「在 registry 目录内重复定义」，与 `load_suite` 报错口径一致；`evaluations/page.tsx` 的 `runAction` 增加显式 `detailRunId` 参数（默认 `selectedRunId`），`removeRun` 删除当前选中项时传 `null` 并同时清空详情面板；删除端口内两个死异常类，`transition_run` docstring 改为 Google 式并如实声明 `Raises: RuntimeError`（存储层冲突由 engines 转 409）。新增回归测试：`tests/test_model_evaluation_execution.py::test_finalize_cas_conflict_with_concurrent_cancel_purges_staged_payload`（manifest 确实写过、暂存目录收尾不存在、run 停 `cancelled` 且零评审调用）、`tests/test_console_evaluation_api.py::test_recover_interrupted_clears_leftover_staged_payloads`（自愈只清活跃 run、终态 run 不误删）与 `::test_list_suites_flags_duplicate_suite_ids_across_files`（重复 id 两个文件一起 invalid）；`docs/dev/evaluation.md`（保留与删除、重启自愈、suite id 唯一性）与 `docs/api/references.md`（suites 端点重复 id 语义）同步。
- 原因: ①②③均为评审确认的实现缺口而非需求变化——①是「不残留用户代码」的隐私/保留红线在崩溃恢复与并发竞争两条冷门路径上未闭合，②是 registry 文档承诺而自查入口未落实（属需求与文档不符），③是删除动作产生误导性错误反馈的可用性缺陷，④是零引用死代码与不实 `Raises` 文档，属可维护性问题。
- 影响: 对外 API 形状不变（`suites` 条目在重复 id 场景由 `valid=true` 改为带原因的 `valid=false`，前端清单/校验区已按此渲染）；rv-1/rv-2/rv-3 既有证据的语义零变化——冻结快照、三 hash 复比对、deny/cancel 留痕、非 `valid` 不折算 0 分与零外发红线均不受本轮清理/校验修复影响；本轮清理只在 run 已进入不可继续状态时删除本就该删除的本地产物。
- 审核: reviewer 修复随 pre-PR 门禁提交；评测定向 pytest 与最终树 `SKIP=check-test-flag uv run pre-commit run --all-files`、`uv run mkdocs build --strict`、`pnpm typecheck`、`just test all` 复跑结果见提交说明；banner 与 §9 状态不变（仍 `🧍 待人工验收`，Human-Confirmed 两项保持未勾）。

### 执行期实现偏差与文档同步（2026-10-13，executor）
- 类型: implementation-deviation / documentation
- 原文: Change Impact Tree 建议测试放 `tests/backend/`、Playwright spec 放 `tests/console/model-evaluations.spec.ts`、评测端点直接改 `agent_runner_console.py`；`docs/dev/evaluation.md` 与 `tasks/evaluation-suites/README.md` 把 rubric 维度写成 `code_quality` / `verification`；文档未记录 judge 运行时依赖与评测 settings 下限。
- 变更后: 三份 Python 测试按仓库既有平铺约定落在 `tests/`（`test_model_evaluation_domain.py`、`test_console_evaluation_store.py`、`test_console_evaluation_api.py`）；Playwright 用户流程 spec 为 `tests/playwright-e2e/tests/workflows/console-evaluations.no-auth.spec.ts`（该包按 smoke/workflows 目录与 `.no-auth` project 组织）；端点放入按域拆分的新模块 `src/backend/api/routes/agent_runner_console_evaluation.py` 并挂载到 app；rubric 维度文档统一更正为 `implementation_quality` / `verification_evidence`（与 core `EVALUATION_RUBRIC_DIMENSIONS` 及前端 `DIMENSION_LABELS` 对齐）；`docs/dev/evaluation.md` 补充 judge 经 `create_chat_model` 无条件 import langchain SDK、新环境需 `uv sync --extra llm` 的前置说明；同文件与 RV harness 记录 `[agent_runner.evaluation]` 下限 `agent_timeout_seconds ≥ 10`、`judge_timeout_seconds ≥ 5`（rv-3 env1 取 10s）；`docs/guides/configuration.md` 增补 `[agent_runner.evaluation]` 配置段说明；`tasks/evaluation-suites/README.md` 作为维护指南留在 registry 目录、不进 mkdocs 导航。
- 原因: 本仓库不存在 `tests/backend/`，全部既有测试平铺于 `tests/`；playwright-e2e 包按既有目录/project 约定组织 console 页面 specs；路由按域拆分是 Console 既有模式；文档维度名与代码常量不符属开工前既有文案错误；`llm` extras 与 settings 下限是实现后确认的真实运行约束。
- 影响: 不改 §10 FR 语义与对外 API；测试、路由与文档位置更贴合仓库事实约定；文档与代码常量一致，新环境可据此准备依赖；超时场景构造受 settings 下限约束。
- 审核: executor 自查完成；`just test`、`uv run mkdocs build --strict` 与 rv-1/rv-2/rv-3 绿灯佐证；无需求语义变化，不需要人工裁决。

### 执行期缺陷修复（2026-10-13，executor）
- 类型: bug-fix
- 原文: engines 层 `cancel_run` 向 core `transition_run` 传状态字符串（core 期望枚举）；`workspace.probe_base` 对不可达 `base_ref` 直接抛 `EvaluationWorkspaceError`，run 创建返回 500；逐单元 worktree 移除后 `worktrees/<run_id>/` 空父目录残留；随包 `kedacode-operator` skill 模板写有 `kc daemon --all`，与 CLI help 不符（开工前既有守卫测试 `test_packaged_skill_command_examples_match_cli_help` 失败）。
- 变更后: `cancel_run` 改传枚举；`probe_base` 不可达基线包成 `ValueError` 走既有校验链返回 400（rv-1 负控 `rv284-badref-suite` 实测 400 且错误消息含「基线」）；新增 `remove_directory_if_empty` 并在 cell pool 收尾调用（rv-1 复验 WORKTREES_PURGED: True）；skill 模板措辞更正为 `kc daemon run --all`。
- 原因: 前两项为执行期实现缺陷，分别由新增 API 测试与 rv-1 负控捕获；空父目录清理是 run 结束清理策略的应有效果；skill 措辞修复是让 `just test` 门禁通过的既有错误更正，与评测功能无关。
- 影响: 非法基线路径从 500 收敛为可解释 400；取消路径类型正确；临时工作区目录不再残留；错误语义与 §7.6 校验计划一致，不改变已定义状态机。
- 审核: executor 修复经 targeted pytest、`just test` 与 rv-1 重跑绿灯验证；属缺陷收敛，无需人工裁决。

### 初始 PRD
- Type: scope / architecture / acceptance
- Before: 无受控 Agent/模型效果对照评测 PRD；`docs/dev/evaluation.md` 仅有建议与格式/入口 TODO。
- After: 定义版本化本地样本集、同基线多执行预设、多评审模型、逐样本可追溯评分、外发前用户授权及本地结果生命周期。
- Reason: 用户提出不仅要评价“预测模型”，还要评价不同 Agent 实现效果，并指出评分模型应可配置多个。
- Impact: 建立与运行时 Dashboard 松耦合的评测闭环；历史 Stats 继续服务日常运行观测，judge score 不影响发布/合并/路由。
- Review: 初稿完成；评分非门禁和逐次外发授权待 PRD 人工评审。
