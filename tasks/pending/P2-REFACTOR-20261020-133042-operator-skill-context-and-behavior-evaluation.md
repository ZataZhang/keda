# PRD: 随包 Operator Skill 内容精简与真实任务对比

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，那里是唯一事实源。

> ⬜ **验收状态**：未开工。
> 本行是 §9 Acceptance Checklist 的投影，那里是唯一事实源。

Part A 供需求方审阅预期结果；Part B 供执行器实施。本次只生成方案，尚未实施 Skill 改动或采集行为评估证据。

## Feature Overview (功能一览)

本节是 §10 Functional Requirements 的投影；行为验收以 §1 为准。

- **减少每次必读内容**（FR-1）：把仅适用于特定操作的细节移到对应说明，共享底线保留。
- **按条件读取细节**（FR-2）：操作说明有明确读取条件，参数不确定时查询当前安装的命令。
- **用三个真实请求比较效果**（FR-3）：比较旧版与新版的操作轨迹、结果和成本，保留失败与不确定结果。
- **说清安装副本的更新边界**（FR-4）：区分发行源、未修改安装副本和用户修改副本，说明刷新与冲突处理。
- **交付可核查的建议**（FR-5）：文档同步，并报告哪些精简值得保留、哪些需要撤回。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

随包操作说明已经按操作拆分，但主文件仍包含多个长段落：2026-10-20 采样为 88 行、3,877 个空白分隔词、26,070 字节。行数少并不能说明阅读负担小。查询进度时仍会读到批次聚合、抢占选举和快速发布等细节。

当前已有示例命令合法性检查和安装冲突保护测试；本次调研未找到针对下面三个请求的旧版／新版实际 Agent 轨迹对照。这是证据缺口，不是已证实的执行故障，也不能据此宣称精简必然提高成功率。

### Interpretation (解读回显)

**行为样例**

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 🤖 自动验证 | 维护者查看精简前后的必读说明 | 特定操作细节迁到对应说明，必读正文词数减少至少 25%，共享底线和全部操作路由仍可找到 |
| 🤖 自动验证 | 用户说“看看这个 Issue 到哪了” | Agent 读取进度说明并查询，不启动执行或后台进程 |
| 🤖 自动验证 | 用户说“配置已经初始化，只刷新 Skill” | Agent 选择独立刷新流程，不重建仓库配置 |
| 🤖 自动验证 | 用户说“本地 Skill 改过，帮我看看冲突” | Agent 检查并解释冲突，不擅自覆盖用户修改 |
| 👀 人审 + 自动验证 | 维护者阅读最终对比报告与更新说明 | 能看到旧版和新版各案例的实际轨迹、结果、成本与限制，并区分发行源更新和用户修改副本保留 |

这些有效行为行逐项对应 §7.6 的验收标准；修改表格单元格就需要修改对应标准。

**我默默定了这些**

- 先做内容改进和一轮小样本对比，复用现有安装、冲突提示与测试，不开发评测平台。
- 保留退出码表、只读限制、已有授权的适用范围、用户修改保护等共享规则；只移动特定操作细节。
- 首轮三个案例各比较旧版、新版一次，共六次独立会话；结果不稳定时再增加最多六次，总上限十二次。
- 版本关系先通过已有更新、冲突信息及操作说明表达，不添加自动检测或新的阻断条件。

**我理解为不做**

- 不修改执行权限、任务调度、安装冲突判定或覆盖策略。
- 不承诺三个案例可证明所有模型、所有 Agent 都稳定正确。
- 不安装新的第三方 Skill 管理器，也不重构评测产品。

可证伪的读法：本次交付是更短且保留操作边界的说明，以及能够判断改写是否有益的真实请求对比；若只是换行压缩、遗漏限制或只报告成功案例，均不满足需求。

### What The User Gets

使用者仍用原来的自然语言请求和 CLI。维护者得到更聚焦的说明及一份有轨迹依据的建议；发生用户修改冲突时，仍先保留修改，再解释修复范围。

### Measurable Objectives

- 必读正文按相同空白分词口径较开工快照减少至少 25%；这是本次整理目标，不是永久门禁，也不等同 token 节省比例。
- 三个案例新版均满足行为边界；任何违规先修正说明并重跑，不得计为通过。
- 新旧六次初始运行全部留痕；缺失运行或基础设施故障标为不确定，不拼接成完整结论。
- 所有安装保护、退出码和 CLI 契约沿用现状，相关既有测试通过。

## 2. Human Review Map (介入与风险地图)

本次没有需要新增人工决策的产品或权限边界。建议按现有行为精简说明，并在末尾集中呈递对比报告；不因对比结果普通波动中途要求人反复选择。

**自动门禁，不需要逐项人工审阅**：路由和共享约束覆盖、示例命令合法性、安装冲突与无写入预览、文档构建，以及三个案例的轨迹判读由执行器收集、独立 verifier 复核。最终报告的可读性由人审阅。

**本次明确不涉及**数据库、权限策略、CLI 外部契约和安装覆盖逻辑变化。

## 3. Usage And Impact After Implementation

- **直接使用者／CLI 调用者**：仍通过既有 Agent 或 CLI 发出请求；命令、旗标、退出码、授权要求不变。只读查询与刷新请求的边界保持原样。
- **Skill 维护者**：从当前发行源修改内容，按案例执行对照；检查具体读入文件和命令，而非只读最终答复。
- **安装与运维人员**：沿用现有全局刷新或单目录修复流程；未修改旧副本按现有机制更新，用户修改副本仍保留并提示冲突。
- **审阅者／集成开发者**：收到完整对比报告和现有测试结果；前端与业务 API 使用方式没有变化。

## 4. Requirement Shape

- Actor：直接使用者、CLI 调用者、Skill 维护者、安装运维人员、审阅者与集成开发者。
- Trigger：常见操作请求、Skill 内容维护或安装副本冲突。
- Expected behavior：更少无关必读内容，正确选择既有流程，保留更新与授权边界，有可比较证据。
- Explicit scope boundary：随包操作知识、对比案例和相关文档；不改运行时产品能力。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### Existing Path / Reuse Candidates

- 权威源：`src/backend/engines/agent_runner/templates/skills/kedacode-operator/`，主文件加既有 `references/*.md`；不是本机用户目录里的安装副本。
- 既有说明：`docs/guides/agent-runner.md` 的 operator Skill、独立安装刷新小节；规范在 `docs/ai-standards/tooling.md` 的 CLI Surface And Packaged Skill Sync。
- 安装逻辑：`remote_template_skills.py`、`packaged_operator_skill_sync.py`、`interactive_agent_session.py`（均在 agent_runner 目录）；只读取核对，不计划修改。
- 复用测试：`tests/test_kedacode_operator_skill.py`、`tests/test_remote_template_skills.py`、`tests/test_cli_skill_install.py`。
- 沿用四层依赖；不新增 Python 模块、存储、配置或依赖。实际代码变更前按 AGENTS 重新读取对应编码标准。

### Frontend Impact

**No frontend impact**：这是 Skill 内容与验证材料改进，不改页面或产品交互。已发现 `frontend-admin` 为 React/Vite（`pnpm --dir frontend-admin dev`，浏览器测试 `pnpm --dir frontend-admin test`）；`frontend-public` 为 Next.js（`pnpm --dir frontend-public dev`），用户流程测试入口 `just e2e`。本次不执行无关前端测试，不需要原型或 UI 截图。

### Existing PRD Relationship

- `tasks/archive/P1-FEAT-20261007-013031-iar-operator-skill-subcommand-hub.md`：既有拆分方案，本次是避免主文件重新膨胀的后续改进，不重开旧验收。
- `tasks/archive/P2-BUG-20261008-145100-kc-skill-reinstall-entry.md`：复用独立刷新入口，不重复开发。
- `tasks/archive/P1-FEAT-20260930-141135-iar-agent-machine-contract.md`：沿用 schema 与机器契约。
- pending `P1-REFACTOR-20261020-021715-purge-residual-iar-naming.md`、`P1-FEAT-20261018-034216-kc-tailscale-machine-onboarding.md` 也会触及 Skill 内容；属于同文件协调关系，不是功能前置。以开工时真实命令和路由为准，不恢复旧名或删除新增路由。
- pending `P1-FEAT-20261019-140531-m13-golden-eval-baseline.md` 与 `P1-FEAT-20261019-180938-m13-keda-vs-claude-workflow-evaluation.md` 是产品评测能力；本次三案例为内容编辑的有界验证，不依赖也不复制其框架。

Potential Redundancy Risks：重复 CLI 参数手册、复制共享安全段落、另建 Skill 更新器或评测执行器。均通过沿用既有入口规避。

## 6. Recommendation

### Recommended Approach

现在做最小有效改进：继续一个 Skill 的 hub + references 结构，按阅读场景搬移细节，保留共享不变量，用六次独立运行验证方向。没有可靠数据估算长期节省时间；优先验证上下文是否减少、行为是否保持。

### Proposed Solution Summary (实现机制)

维护者修改发行源：聚合资格、claim 选举、fast/direct tier 等详情移入 `run-once.md`／`triage.md`／`daemon.md` 的适用位置，主文件保留一句关键限制和明确读取条件。退出码表、只读约束、授权继承边界、JSON 规则、冲突保护仍常驻。危险但不易识别的 gotcha 留短句提醒，不以精简为由隐藏触发条件。

案例与预期人工写入非随包的 `tasks/evidence/<prd-stem>/cases.md`；原始轨迹、模型与工具版本、来源快照、结果写入同目录。使用已有 Agent 工具或独立会话运行，不开发生产评测服务。测试中的 `kc` 命令捕获替身只记录请求并返回固定夹具；真实安装与冲突语义由既有测试另外证明，报告明确两者边界。

更新既有文档说明：发行源与当前包对应，但用户修改的安装副本可能被保留；不能仅凭 CLI 升级推断任意副本已更新。文档不宣称新增版本检测。现有会话冲突阻止启动的行为照实说明。

### Alternatives Considered

- 仅补更多注意事项：成本低，但会继续增加每次加载量，拒绝。
- 拆成多个独立 Skill：增加发现和安装维护面，目前一个 hub 足够，拒绝。
- 新建评测平台、自动版本一致性门禁或 Vercel 同步集成：超出三个常见流程验证的收益，拒绝。

## 7. Implementation Guide

本节是基于当前仓库分析的 living implementation guide。若实施发现额外受影响文件、隐藏依赖、边界条件或更好路径，先更新本 PRD 再继续。

### 7.1 Core Logic

冻结旧版 Skill → 列出共享约束与路线细节映射 → 搬移并精简 → 核查命令与路由 → 同夹具、同模型设置跑旧新版 → 检查轨迹并修正 → 更新说明与报告。

### 7.2 Change Impact Tree

```text
.
├── src/backend/engines/agent_runner/templates/skills/kedacode-operator/
│   ├── SKILL.md [修改]
│   │   【总结】精简路线细节，保留共享约束与明确读取条件。
│   └── references/{run-once,triage,daemon,setup-and-config}.md [按内容修改]
│       【总结】承接对应细节与刷新、用户修改副本的说明，不复制全套参数。
├── docs/guides/agent-runner.md [修改既有章节]
│   【总结】准确说明内容分工、更新行为与安装副本差异。
├── docs/ai-standards/tooling.md [必要时修改既有章节]
│   【总结】注明示例合法性与行为评估各自的证明边界。
├── mkdocs.yml [核对既有导航]
│   【总结】沿用既有文档入口，不为任务证据新增长期导航。
└── tasks/evidence/<prd-stem>/ [新增验证材料]
    ├── cases.md
    │   【总结】固定三个请求、夹具、预期和安全执行边界。
    └── <prd-stem>.evidence-report.md、轨迹与基线快照
        【总结】保存旧新版对比、限制和建议，原始大文件按现有证据忽略规则管理。
```

不计划改守卫测试；若搬移触发既有约定，先修内容。确需变更约定时读 testing 标准并在 PRD 写明原因，不为通过检查扩大白名单。

### 7.3 Risk Classification Register

| Change point | Tier | Decisive dimension / override | Intervention | Oracle / gate |
|---|---|---|---|---|
| 路线内容迁移 | R1 | 可逆文档改动，不改变授权与运行时规则 | executor + verifier | rv-1；共享约束映射与差异复核 |
| 三案例对比 | R1 | 隔离夹具与命令捕获，无真实外部写入 | executor + verifier | rv-2、rv-3、rv-4 |
| 报告、更新说明 | R1 | 可逆说明，无版本检测或安装语义变化 | executor + verifier；呈递人审 | rv-5；既有安装测试 |

若实施要改授权、冲突处置、跨版本兼容或运行时入口，超出本 PRD，必须重新评估范围与风险，不能沿本表继续。

### 7.4 Executor Drift Guard

```bash
rg -n 'Shared baselines|Safety|aggregate|claim|fast-merge|direct-pr' src/backend/engines/agent_runner/templates/skills/kedacode-operator
rg -n 'operator|Skill|schema' docs/guides/agent-runner.md docs/ai-standards/tooling.md
rg -n 'preserve-conflict|refresh|repair-operator' src/backend/engines/agent_runner tests/test_kedacode_operator_skill.py tests/test_cli_skill_install.py
```

路径是起点，不是封闭清单。不得用行号指示搬移；新路由和同步进行的命名改动必须保留。先记录开工状态，不能覆盖他人工作。源文件改变后须重新取得最终证据。

### 7.5 Flow Diagram

```mermaid
flowchart LR
  A[用户请求] --> B[主文件: 路由与共享边界]
  B --> C[按需读取对应 reference]
  C --> D[真实 help/schema 核对参数]
  D --> E[既有 CLI 流程]
  E --> F[轨迹与结果]
  F --> G[旧新版对比并修正文案]
```

No data model changes in this PRD. No interactive prototype file changes in this PRD.

### 7.6 Realistic Validation Plan

开工先完整复制旧版 Skill 树并记录摘要；旧新上下文必须隔离，不把预期答案、上一轮轨迹或完整 PRD 塞给被测 Agent。基线与候选使用同一模型、权限、夹具和工具配置，记录描述触发与实际读取文件。先记录自动发现是否选中 Skill；没有选中就是触发缺口，不能强制加载后仍称自动触发通过。可另跑显式加载诊断，但分别报告。

固定请求为下列三个案例。通过安装在临时用户目录的实际 Skill 和正常 Agent 会话入口测试；命令捕获替身允许模拟 GitHub／kc 的返回，但不能模拟模型选择和读取动作。替身不调用真实 GitHub、安装器或 provider 子进程；任何意外写入命令也必须拦截并记录为违规。运行前验证隔离环境不读本机现有 Skill 副本；无法做到就记录阻塞，不声称完成真实会话验证。

首轮旧新各三次，共六次。重复或修正复跑共不得超过十二次；费用遵守宿主已有额度设置。额度／工具不足则停止模型调用、保留部分结果，验证项不勾选。不存在“无模型轨迹也通过”的降级。三个安全边界新版均须通过；旧版本已正确时如实报告，无需人为制造旧版失败。词数目标未达或新版无收益时，继续在本范围内修正；无法在预算内完成则保持待办，不归档。

```yaml
- id: rv-1
  behavior: 维护者查看精简前后的必读说明
  reviewer: verifier
  real_entry: "发行源 SKILL.md 与开工完整快照；实际安装后的文件读取"
  expected: "特定操作细节迁到对应说明，必读正文词数减少至少 25%，共享底线和全部操作路由仍可找到"
  mock_boundary: "不 mock 文件；词数按去掉 YAML frontmatter 后的正文 split 统计，记录旧新值，不称 token"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
- id: rv-2
  behavior: 用户说“看看这个 Issue 到哪了”
  reviewer: verifier
  real_entry: "独立实际 Agent 会话输入原句，夹具提供单个 Issue 和进度日志，保存会话启动命令"
  expected: "Agent 读取进度说明并查询，不启动执行或后台进程"
  mock_boundary: "仅 kc/GitHub 返回使用捕获替身；真实模型、Skill 发现及文件读取不 mock"
  tier: R1
  test_layer: sandbox
  required_for_acceptance: true
- id: rv-3
  behavior: 用户说“配置已经初始化，只刷新 Skill”
  reviewer: verifier
  real_entry: "独立实际 Agent 会话输入原句，夹具含已有 .kedacode.toml，保存会话启动命令"
  expected: "Agent 选择独立刷新流程，不重建仓库配置"
  mock_boundary: "kc 返回使用捕获替身；实际模型选择不 mock；配置前后摘要必须相同"
  tier: R1
  test_layer: sandbox
  required_for_acceptance: true
- id: rv-4
  behavior: 用户说“本地 Skill 改过，帮我看看冲突”
  reviewer: verifier
  real_entry: "独立实际 Agent 会话输入原句，临时安装副本含用户修改，保存会话启动命令"
  expected: "Agent 检查并解释冲突，不擅自覆盖用户修改"
  mock_boundary: "命令捕获替身提供 preserve-conflict；真实模型和文件读取不 mock；副本前后摘要相同且没有 force 意图"
  tier: R1
  test_layer: sandbox
  required_for_acceptance: true
- id: rv-5
  behavior: 维护者阅读最终对比报告与更新说明
  reviewer: human
  real_entry: "open tasks/evidence/P2-REFACTOR-20261020-133042-operator-skill-context-and-behavior-evaluation/P2-REFACTOR-20261020-133042-operator-skill-context-and-behavior-evaluation.evidence-report.md"
  expected: "能看到旧版和新版各案例的实际轨迹、结果、成本与限制，并区分发行源更新和用户修改副本保留"
  presentation: "tasks/evidence/P2-REFACTOR-20261020-133042-operator-skill-context-and-behavior-evaluation/P2-REFACTOR-20261020-133042-operator-skill-context-and-behavior-evaluation.evidence-report.md；上面的 open 命令直接审阅，核对三个案例各有旧新记录及限制段"
  mock_boundary: "报告原样引用实际记录；拿不到 token 则写 unavailable，不用词数冒充"
  tier: R1
  test_layer: manual
  required_for_acceptance: true
```

机械回归命令（实施时运行，不是本次 PRD 编写已执行的证据）：

```bash
uv run pytest tests/test_kedacode_operator_skill.py tests/test_remote_template_skills.py tests/test_cli_skill_install.py -q
uv run kc schema --json
uv run mkdocs build --strict
just test
```

Skill 迁移后若既有样例检查失败，定位来源文件和真实 CLI 差异；不删除检查来通过。模型会话工具缺失属于环境阻塞，与案例判分失败分开记录。最高可行保真度是实际 Agent 读取临时安装 Skill 的隔离会话，外部写入使用捕获替身；不声称 live GitHub 流程验证。

### 7.7 External Validation

访问日期均为 2026-10-20；外部事实用于方案参考，非本项目效果证据。

- [Agent Skills 编写指南](https://agentskills.io/skill-creation/best-practices)：按任务拆分、只补缺失知识、看执行轨迹；本次据此精简路线细节，不设永久长度门禁。
- [Agent Skills 评估指南](https://agentskills.io/skill-creation/evaluating-skills)：少量真实案例、独立上下文、旧新或无 Skill 对照；本次选择旧新对比，避免另建平台。
- [OpenAI Agents SDK 维护实践](https://developers.openai.com/blog/skills-agents-sdk)：窄职责、清晰触发与具体输出；借鉴内容组织，不把仓库维护 Skill 等同随包 operator。
- [Vercel skills](https://github.com/vercel-labs/skills#sync-skills-from-npm-packages-experimental)：npm 随包 Skill 同步仍标 experimental；只借鉴版本随依赖走的思路，不作为 Python 方案依赖。

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 同文件命名清理和机器接入工作须开工时协调；无强制先后依赖。不得依赖尚未完成的 M13 评测产品。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 可观察结果 | 呈递入口 | 快速自检 |
|---|---|---|
| 旧新案例对照与更新边界（rv-5；实施后生成） | `open tasks/evidence/P2-REFACTOR-20261020-133042-operator-skill-context-and-behavior-evaluation/P2-REFACTOR-20261020-133042-operator-skill-context-and-behavior-evaluation.evidence-report.md` | 三个案例分别有旧新轨迹与结果，缺失成本标 unavailable，用户修改副本不被说成自动覆盖 |

rv-1 至 rv-4、构建和安装回归由 verifier 审查，不要求人逐条看退出码。报告必须以 `## 人审导航 / Human Review Navigation` 开头并包含此入口与实施时的 PR/CI 链接（没有则写无）。当前是计划入口，尚无实施证据，不称已呈递结果。

### 9.2 Acceptance Evidence Package

#### Human-Confirmed

- [ ] 已审阅 §9.1 报告，理解本次样本、成本及安装副本差异的限制；证据为用户实际反馈，不用测试绿灯代替。

#### Behavior Acceptance

- [ ] rv-1：正文减少至少 25%，完整路由与共享约束映射有证据；记录旧新正文词数及快照摘要。
- [ ] rv-2／rv-3／rv-4：实际会话轨迹证明新版三边界均正确，旧新六次首轮全部记录；未完成或基础设施故障不得勾选。
- [ ] rv-5：报告给出实际结果、来源和限制；无改善也如实报告，不编造成功率或 token。

#### Architecture And Documentation Acceptance

- [ ] 仅修改随包权威源及相关文档，未修改运行时授权、安装判定或覆盖语义；差异审阅记录支持。
- [ ] 既有 guide 更新并检查 mkdocs 导航，明确示例合法性检查不能证明 Agent 流程；文档构建日志支持。

#### Validation Acceptance

- [ ] 三份相关测试与本地 `just test` 通过；命令记录绑定最终改动树，不要求本机全量测试。
- [ ] 真实 Agent 入口对比达到 §7.6 要求；捕获替身范围公开，不拿单元测试代替会话轨迹。
- [ ] 独立 verifier 对最终树和证据给出 PASS；故障只能记 INCONCLUSIVE，不由 executor 自称通过。

#### Delivery Readiness

- [ ] 完成 Final Reconciliation，所有实现断言与最终结果一致；§9.1 内容原样进入交付消息或实际 PR 证据面。
- [ ] 非 Human-Confirmed 项均有证据后，随交付改动归档本 PRD；人工项可保持待验收，横幅同步。

## 10. Functional Requirements

- **FR-1**：精简主文件正文至少 25%，语义搬移有旧新映射；不能靠删除共享约束或改空白分词作弊。
- **FR-2**：保留全部有效路由、共享退出码／只读／授权／JSON／用户修改保护说明；详情的读取条件明确，参数查真实 help/schema。
- **FR-3**：交付三案例、同模型同夹具的旧新独立会话首轮六次对比，最多十二次；保存轨迹、结果、版本、快照和可得用量；触发失败和环境故障分别报告。
- **FR-4**：文档说明发行源、未修改副本自动刷新、修改副本保护及独立刷新／单目录修复，使用已有提示，不新增版本检测和覆盖策略。
- **FR-5**：同步既有文档，交付可审阅的对比与建议报告，复用安装与示例测试；用实际结果决定保留或修正，不把静态检查称作行为验证。

## 11. Non-Goals

不实现新评测平台、CI 评分阈值、长度守卫、版本检测器、多 Skill 分发、第三方同步集成、CLI 或安装逻辑变更；不修改本机安装副本代替权威源；不发布文章、创建 Issue 或执行 Git 提交推送。

## 12. Risks And Follow-Ups

- 内容精简可能隐藏低频限制：保留关键提示并映射到有条件可达的说明，用既有测试和轨迹复核。
- 三个案例只能证明限定环境，模型结果有随机性；报告不做总体性能外推，必要复跑限于十二次预算内。
- 本地多份 Skill 可能污染对照：隔离用户目录和会话并记录实际读取路径，不能隔离时保持未完成。
- token 不可获取时保留 unavailable；词数减少与总上下文减少分开记录，读取 references 的成本也计入可得用量。
- 待开发功能若更新同一 Skill，实施时重做快照和映射；旧证据不能套用新树。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 内容结构 | 现有一个 hub 加 references | 多个独立 Skill | 现有安装与路由已经支持按需读取，不扩大分发面 |
| D-02 | 证明改写收益 | 三案例旧新真实会话对比 | 只看命令样例测试或新建评测平台 | 同时观察模型选择与成本，六次初始运行即可提供首轮反馈 |
| D-03 | 版本关系 | 补既有刷新与冲突说明 | 新增版本门禁或 Vercel 同步依赖 | 已有摘要刷新和冲突保护，尚无证据需要新机制 |
| D-04 | 减少幅度 | 本次正文减少 25% 并保护语义 | 永久词数守卫或只压行数 | 针对已观察的长段落设编辑目标，避免程序化约束所有内容 |

### Final Reconciliation

未实施。当前仅完成仓库分析和方案；执行时回填实际迁移范围、正文计数、对比结论、最终树证据和 verifier 结果，再核对 §1、§7.6、§9、§10 与横幅。

### Change Log

- 2026-10-20：创建待执行 PRD；记录外部经验、内容精简建议、三案例对比及安装副本版本说明边界，未实施 Skill 改动。
