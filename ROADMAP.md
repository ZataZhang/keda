# Roadmap

> 本文件是项目的**战略路线图**（方向、里程碑、边界）。管理终端里的 **Backlog**
> 页面（`/app/backlog`、`kc backlog`、`/api/v1/agent-runner/backlog/*`）是执行侧的
> PRD 待办队列工作台，二者不是同一事物；下文里程碑名中的 "Roadmap" 均指该功能在
> 正名为 Backlog 之前的历史名称。

## Vision

构建一个面向人工调度的 AI 工程交互终端。项目本身不自动决定哪些 issue 开始、哪些 issue 暂停，而是在人工选择任务后，辅助完成需求理解、代码修改、验证、code review、提交 pull request，并在主分支、CI、评论或 PR 状态变化后维护已提交 PR 的监督与 rebase 状态。在这条链路里，`kc` CLI 是核心执行面：既供人操作，也是外部 agent（Claude / Codex / Kimi 等）经 shell 驱动这套基于 issue 的编排的一等机读接口。

**下一阶段的重心**（自 2026-10-16 起）：任务流转这一圈已基本闭环，接下来把投入放在**让 Agent 本身可度量、可改进**上——先建立可复现的评测与回归基线（M13），再在评测的约束下做经人批准的自进化（M14）。流程类剩余项转入维护带。

## Product Boundary

- 当前项目提供交互终端和本地 runner 能力，不提供全自动 issue 调度系统。
- 是否开始处理某个代码任务由人工控制：**守护进程的自主挑选**以 `agent/ready` label 为唯一准入（并可结合 `agent/claude`、`agent/codex` 或 `agent/kimi` 指定执行终端）；**人的显式定向**（`kc run --issue N` / 给 PRD 路径）不受该 label 约束——不打标记即直接执行，因而也不会与任何守护进程竞争。系统不提供「指派给某台机器」的机制：打标记即先到先得，不打标记则自行显式执行。
- 用户可以直接提交没有 PRD 的 Issue。**PRD 不是执行前置**：这类 Issue 只要被人工打上 `agent/ready`，就可以直接被 runner 当作可执行代码任务领取。需求澄清、多 agent 合议与 PRD 审批是**按需能力**（需求复杂、涉及多方或表述含糊时使用），不是每次都必经的强制链。
- 用户提交 Issue 时有义务尽量把需求、问题背景、复现信息、期望结果和已知约束描述清楚。**没有 PRD 时，Issue 正文就是唯一的需求来源**，因此描述质量直接决定交付质量；澄清是可选的增强手段，不是执行前的强制闸门。
- 用户可以在 Issue 中上传图片作为需求上下文，例如界面截图、错误截图、流程图或设计稿；AI 在澄清、合议和 PRD 草稿生成时应把这些图片视为 Issue 上下文的一部分。
- 终端可以展示 issue 信息、辅助分析任务和执行工程动作，但不会主动处理未标记的 issue。
- `kc` CLI 是本项目的核心执行面，并把外部 agent（Claude / Codex / Kimi 等）视为一等消费者：命令输出、退出码与运行时能力自省应提供可机读契约，而不只是给人看的表格（由 `P1-FEAT-20260930-141135` 承接）。
- 改动 `kc` CLI 表面（新增/改名子命令、旗标、退出码或机器可读输出）必须同步随包 `kedacode-operator` skill 与 `docs/`，保证 agent 侧知识与真实命令树不漂移；约定见 `docs/ai-standards/tooling.md` 的 CLI Surface And Packaged Skill Sync。
- 一旦 Issue 被标记为 `agent/ready`（无论是否 PRD-backed），后续执行链路（修改代码、验证、review、提交 PR、维护 PR 分支）应尽量自动完成；遇到需求不明确、安全门禁、验证失败、发布失败、冲突或高风险 review 发现时安全停止并报告。
- **验证策略由仓库用户定义，KedaCode 负责生命周期**：检查哪些内容、使用哪些命令或 CI checks、哪些结果属于必需门禁，应由仓库配置决定。KedaCode 负责运行或观察这些门禁，并编排 pending、修复、等待、阻塞与通过等状态；不把某种 provider 的 SHA、tree、diff 指纹或证据格式固化为通用有效性规则。Supervisor 自己能够完成的状态对账、结果发布和 PR repair/rebase 留在 `agent/supervising`；需要代码修改时可调用配置的 repair agent，但不把 Issue 交回 `agent/running` builder 队列。
- 当前实现优先支持 GitHub Issues / Labels / Pull Requests；其他代码托管平台暂不在当前交付边界内。

## Current Status

截至 2026-09-17，仓库已经形成"CLI/daemon + 多 agent runner + post-PR supervisor + independent verifier"的稳定闭环；进展集中在三个方向——一是把已经闭环的能力进一步钉死（Agent Runner 记忆持久化的跨 worktree 锚点修复、`api→engines` 直连迁移回 `core` 编排层、`pre-commit` 验证命令与独立 verifier gate 落地）；二是为产品仓引入的 autopilot fast-lane 能力族已交付两项——**合并队列**（`P1-FEAT-20260703-105322`）与 **roadmap 持续调度**（`P1-FEAT-20260703-105330`），连同独立 verifier gate 构成"打开开关即无人值守"的最小闭环；三是原计划的第三项"执行前 re-grounding 与触碰面避让"经评估**整体收缩**为默认 `execution` 提示模板内的"PRD map check"核验指令（`P1-FEAT-20260703-105340`）——不引入独立流水线阶段、不预测触碰面、不做并行撞车避让。当前状态依据来自 `src/backend/` 实现、`docs/guides/agent-runner.md`、`tasks/archive/` 已归档 PRD 与 `tasks/pending/` 待完成 PRD。

### Completed

- **CLI 基础能力已落地**：`kc init`、`kc labels sync`、`kc issue-from-prd`、`kc run-once`、`kc daemon`、`kc review-once`、`kc review-daemon`、`kc recover-publish`、`kc deliberate` 和 `kc worktree` 已接入项目脚本，并通过 `.kedacode.toml`、`config.toml` 与环境变量配置。
- **仓库本地配置已落地**：`kc init` 能在目标仓库生成 `.kedacode.toml`，全局 `config.toml` 主要保留默认值、环境级设置和 legacy repository registry。
- **人工准入队列已落地**：runner 只处理带有 `agent/ready` label 的代码任务，不会主动挑选未标记任务；支持 `agent/codex`、`agent/claude`、`agent/kimi` 等 agent 路由 label。
- **标准 label 流转已落地**：支持同步和使用 `agent/ready`、`agent/running`、`agent/supervising`、`agent/automated-review-passed`、`agent/failed`、`agent/blocked` 等队列状态；`human/awaiting-review` 独立标记仍需人工审阅或验收的 Issue。
- **PRD 到 Issue 的发布链路已落地**：`issue-from-prd` 能从 PRD 创建 GitHub Issue、回写 Issue URL，并支持 `--publish-prd` 在 ready 前提交和推送目标 PRD 文件；不带 `--publish-prd` 的 `--ready` 交互路径会延迟添加 ready label，避免 PRD 尚未发布时被 runner 领取。
- **多仓库目标解析已落地**：当前目录、`--repo`、`--repo-id` 和 `--all` 均可解析目标仓库；多仓库 registry 继续兼容，但未指定目标时默认处理当前 Git 仓库，不再隐式轮询所有 enabled registry entries。
- **隔离 worktree 执行链路已落地**：`run-once` 能领取 ready issue、创建或复用 worktree、选择 agent、运行 agent、执行验证、推送分支并创建 Draft PR。
- **内置 worktree 管理已落地**：`kc worktree create/path/remove` 统一管理 `.kc-worktrees/<branch>`，新 `kc init` 默认使用内置命令，`create_or_reuse_worktree` 会在返回前校验路径存在并输出三段命令诊断。
- **受限提交代理已落地**：agent 不直接 `git add` / `git commit`，而是写入 `.agent-runner/commit-request.json`，由 runner 在 host 侧完成受控提交。
- **本地验证和失败恢复已部分落地**：runner 支持配置化验证命令、失败输出摘要、有限 recovery loop、Claude stream-json 前台过滤和 recovery retry delay；commit request、验证失败、agent CLI 异常和 pre-commit 失败等可恢复错误会进入修复循环。
- **pre-commit 验证命令已落地**：runner 在 commit 阶段新增 `runner.pre_commit_verification_command`，与既有 `runner.verification_commands` 解耦；记忆/技能晋升与 verifier 阶段不经过该命令。
- **pre-push AI review 已落地**：实现 agent 提交后、push 前会执行独立 review session；reviewer 修改必须通过同一 commit proxy 和验证命令；空 commit request 会按 reviewer verdict 收敛或软失败，不再被误判为硬失败。
- **独立 verifier gate 已落地**：Realistic Validation sign-off 由独立 verifier agent 评估——verifier 默认换 agent/model、在干净 worktree 中对抗性验证，通过后打 `validation/verifier-passed` 标签；head 漂移时会自动清除该标签，防止 autopilot 误判。
- **post-PR supervisor 已落地**：Draft PR 创建后 Issue 进入 `agent/supervising`，supervisor 可批准进入 `agent/automated-review-passed`、在 supervising 内执行 repair/rebase/resolve-conflict、转人工 blocked 或标记 failed。
- **review daemon 宽上下文检测已落地**：`review-once` / `review-daemon` 能扫描 `agent/supervising` 和 `agent/automated-review-passed` Issue，并基于 head/base、CI checks、mergeability、Issue comments 和 PR comments 变化重新运行 supervisor cycle；supervisor 自写评论不会触发无限自循环，中断的 supervising rework marker 可在后续 review pass 恢复。
- **rebase 冲突 agent 解决基础能力已落地**：post-PR supervisor 的 rebase 路径遇到冲突时可调用 agent 处理冲突、运行验证并用 `--force-with-lease` 推送。
- **AI 生成 Issue / PR 内容已落地**：`[agent_runner.generated_content]` 支持 template 和只读 agent 两种模式，并对 Issue 的 PRD anchor 与 PR 的 `Closes #...` anchor 做 fallback 校验。
- **只读多 agent 合议基础能力已落地**：`kc deliberate` 能运行 architect / skeptic / implementer 等 profile，输出 event stream、transcript、result、session metadata 和隔离 workspace 原始输出。
- **Issue 依赖门禁已落地**：PRD `Delivery Dependencies` 小节在 `kc issue create` 时被物化为 `kc:depends-on` marker；runner 领取 `agent/ready` Issue 前实时判定依赖满足状态，未满足时叠加 `agent/waiting` label 并写去重 comment；仅支持 Issue 编号依赖，上游 failed/blocked 点名提示。
- **Agent Runner 记忆持久化已落地**：runner 启动时检索长期记忆 / 草稿 / 已晋升 skill，短期记忆写入 worktree 局部目录、问题关闭后清理；skill 草稿按 usage_count 自动晋升阈值（默认 3）。
- **Agent Runner 记忆锚点稳定化已落地**：所有记忆目录在 `factory` 构建 `RepositoryRunContext` 时被一次性绝对化到目标仓库主检出根，跨 worktree / 跨 Issue 持久化真实生效；共享目录写入采用 tmp + `os.replace` 原子落盘，并发场景 last-write-wins 不产生半写文件；原 PRD 验收降级为"同目录内部函数调用"的失真场景已被纠正，证据脚本强制 `git worktree add` 创建两个真实副本。
- **`api → engines` 直连已迁移回 `core` 编排层**：`src/backend/api/` 对 11 个 `engines/agent_runner/` 能力的直连 import 全部清零，业务类能力在 `core/use_cases/` 补薄 facade 用例或复用既有用例；`live_terminal` / `runner_live_view` 等呈现模块从 engines 迁入 `api/`；`hooks/shared/check_architecture.py` 的 `FORBIDDEN_IMPORTS["api"]` 恢复严格态 `["infrastructure", "engines"]` 并稳定通过 `just lint --full`；`CLAUDE.md` 与 `docs/ai-standards/architecture.md`、`docs/architecture/system-design.md` 关于 `api/` 依赖方向的表述三处一致。
- **原生执行器入口与按需项目预览已落地**：TTY 下裸 `kc` 即 `kc session`——KC 只解析默认执行器、核对随包 operator skill、投递 bootstrap 后把终端交出去，并**原样转发 provider 退出码**（非 TTY 回落帮助且退出 1，管道/CI 不会把失败读成成功）；`kc preview start|status|stop` 承载按需预览，argv 必须是精确参数数组（不经 `sh -c`），ready 地址主机限定回环闭集且非回环是**加载期错误**，只终止 KC 自己登记的进程组。隧道、外部预览平台、以及"用模型猜用户是否想要预览"仍是明确非目标（PRD `P1-FEAT-20261009-123512`）。
- **停滞监督已落地（默认开启）**：`[agent_runner.stall_supervisor]` 为活跃 attempt 起一个只读 observer，按现场指纹（HEAD/branch/status/diff/调用终态数/最近事件时间，**刻意不含 stdout**）判定疑似停滞，请模型在 `progress / blocked / stalled / uncertain` 闭集里下一次结论；只有 `stalled` 且摘要与证据齐全、且处置前现场与进程归属复核通过，才终止该进程组并把 `AgentStallCancelledError` 交回既有失败分类与恢复阶梯——不新增恢复路径、不改标签。结论解析容忍中英键名、markdown/表格/JSON/换行续写与 provider 追加字段（两个线上误报：追加字段解析失败、三字段齐全仍判 uncertain），容错只到"读法"为止，取值仍落闭集（PRD `P1-FEAT-20261009-123512`，含 P2 复盘）。
- **文档与测试基础已落地**：已有 Agent Runner 使用指南、配置说明、架构规范、归档 PRD、pytest 覆盖和 `just test` 验证入口。
- **包管理器分发链路已落地**：PyPI 分发包与 CLI 命令定名 `kedacode` / `kc`，发布走"tag 构建 draft release → 人工正式发布 → OIDC Trusted Publishing 上传 PyPI → 自动更新 Homebrew tap"两步走；用户侧三条等价入口 `brew install ZataZhang/tap/kedacode`、`uv tool install kedacode` / `pipx install kedacode`、`curl -fsSL .../install.sh | bash`（`--source auto|pypi|tarball`，pypi 来源失败不静默回退）。公开分发仍不提供原生安装包：未签名/公证的 `.app` 不适合公开分发；本机自用的菜单栏 App 已由 PRD `P1-FEAT-20260913-204531` 单独实现，不改变 PyPI 与 Homebrew tap 链路。

### Partially Completed

- **交互终端能力**：裸 `kc` 原生执行器入口、`kc repl`、`kc preview` 与少量交互式提示已具备，前端仍是基础结构；但还没有完整的 issue 浏览、任务选择、状态追踪和人工介入终端体验。
- **自动恢复能力**：执行、验证、提交请求、已有本地 commit 发布恢复、repair/rebase 已有基础闭环；supervisor-owned rework marker 可留在 supervising 并续跑；发布恢复后重新进入 supervisor 的安全闭环、旧 runner-owned CI rework 状态恢复、blocked/forbidden resolution、rebase detached HEAD guard 仍待补齐。
- **可观测性**：终端前台输出、Issue comment、`kc:event` marker、合议日志、review-daemon cursor 和健康端点已具备；还缺少面向 operator 的统一监控面板、异常聚合和可审计时间线视图。
- **review 能力**：pre-push review、post-PR supervisor、宽上下文 review-daemon、独立 verifier gate 已有闭环；高风险 finding 的稳定阻断规则、PR 正文 schema 校验和部分 supervisor 安全边界仍需补齐。
- **PR 分支维护能力**：supervisor 可在 `agent/supervising` 内请求并执行现有 PR branch repair/rebase/resolve-conflict，review-daemon 可感知 base、checks、comment 和 mergeability 变化；detached HEAD rebase 中间态识别、发布恢复后的 supervisor 闭环和旧 runner-owned CI rework 状态恢复仍不完整。
- **前端能力**：已有基础前端结构和页面骨架；面向 agent runner 的可用操作台尚未完成。
- **Issue-first 能力**：PRD -> Issue 已完成；**任意 Issue（含无 PRD）可执行**的运行侧路径已具备，并已由 `P1-FEAT-20261006-122336` 完成首次真实端到端实证（Issue #216 → Draft PR #217，待人工验收）；Issue -> PRD / PRD rewrite / PRD review deliberation 作为**按需**能力仍在 pending PRD 中（不再是执行前置）。
- **Autopilot fast-lane（产品仓"打开开关即无人值守"能力族）**：原三件中已交付两件——**合并队列快速档**（`P1-FEAT-20260703-105322`，已归档：`[autopilot]` + `safety.auto_merge` 双开关门控，verifier 绿灯 → 自动签核 → rebase 最新 base → 全量验证重跑 → 禁改路径终扫 → squash 合并，按 Issue 号 FIFO 串行）与 **roadmap 持续调度**（`P1-FEAT-20260703-105330`，已归档：daemon 内对账 + 补位 + 发现式入队，`kc roadmap advance [--dry-run]` 一次性入口）。第三件"执行前 re-grounding 与触碰面避让"已**取消独立阶段、取消 agent 侧触碰面预测、取消并行撞车避让**，收缩为默认 `execution` 提示模板内置的 `PRD map check` 核验指令并交付（`P1-FEAT-20260703-105340`；形态见 M2）。仍未完成的只剩 fast-lane 的发布说明（`auto_merge` 语义已从死开关激活，需显式提示升级影响）。

### Not Completed

- 无 PRD Issue 的**按需**澄清链路：合议候选识别、需求澄清、PRD 审批与落盘。澄清与 PRD 生成不是执行前置，仅在需求复杂或表述含糊时使用。
- `agent/rework-prd` 驱动的 Issue -> PRD 自动生成、已有 PRD 重写和多 agent PRD review。
- 将只读多 agent 合议接入 GitHub Issue intake、PRD 草稿生成和 review 流程。
- 面向 operator 的 Agent Runner 监控面板、异常检测和 Issue 时间线 API。
- 发布恢复后先进入 `agent/supervising` 并运行 post-PR supervisor 的完整安全闭环。
- rebase conflict 阶段 detached HEAD / active rebase target 的安全识别。
- forbidden path blocked resolution、CI rework state recovery 和 process runner 错误可诊断性增强。
- 高风险 review 发现阻止 PR 发布或转人工的完整稳定规则。
- 非 GitHub 平台适配层。
- autopilot fast-lane 的发布说明：`safety.auto_merge` 语义已从死开关激活（双开关同时为真才生效），需要显式提示升级影响，发布渠道（README / release notes / daemon 启动日志）待定。

## 路线更新（2026-09-28 复核增补，2026-10-06 更新产品边界与 pending 清单，2026-10-16 路线重心转向 Agent 能力）

> 上方正文是 2026-09-17 快照，历史条目未逐条重写；本节记录自该快照以来的交付与当前 pending PRD 顺序。各 PRD 的结构化交付依赖以对应 PRD §8 Delivery Dependencies 为唯一事实源，本节是阅读视图。

### 2026-10-06 产品边界调整：无 PRD 的 Issue 可直接执行

- **撤销**「没有 PRD 的 Issue 只能进入需求澄清、合议和 PRD 审批流程；在管理员确认前不得被 runner 当作可执行代码任务领取」这条边界。**PRD 不再是执行前置**：无 PRD 的 Issue 只要人工打上 `agent/ready` 即可被领取并端到端执行，Issue 正文即需求来源。同步更新的章节：Product Boundary、Target Workflow 第 3–9 步与第 21 步、Partially/Not Completed、M1、M8、Acceptance Checklist、Near-Term Delivery Order、Open Questions。
- **澄清、合议与 PRD 生成改为按需能力**：需求复杂、涉及多方决策或表述含糊时使用；简单需求不经过这些环节。M8 由「Issue-First PRD Gate」改名为「Issue-First Clarification And PRD (On Demand)」，能力**不删除**，只是不再作为执行前闸门。
- **配套后果（已披露并接受）**：这类 Issue 没有自动化质量锚——证据门禁整体关闭（`validation_required` 读 Issue body 的验收小节，默认不生成则该门禁连同前端视觉证据要求一并跳过）；原「描述不清时 AI 必须先反问用户」的规则失去强制力，澄清成为可选。
- **事实依据**：代码中从未实现这条强制链——全仓检索 `intake` / `needs-prd` / `requires_prd` 零命中；所有 PRD 相关门禁（交付门、PR body 契约、证据门禁、最终复核）都是「有 PRD 锚点才生效，没有即提前返回」；独立 verifier 的意图来源本就是 Issue body。**撤销边界是让文档与实现对齐，不是放宽一个真实存在的门禁。** 撤销前的状态是「文档承诺了一个代码里不存在的保护」，比明确放开更危险。
- **未验证项（已在 PRD 中登记）**：无 PRD 的 Issue 能跑，此前只有单元测试与代码阅读支撑，从未在真实生产 run 上验证过（账本中 29 条空 `prd_path` 记录全部来自测试夹具）。首次真实端到端验证是承接 PRD 的首要交付。
- **新增 `--direct-pr` 档位（同一 PRD）**：实测确认 `--fast-merge` **不等于**「agent 干完就出 PR」——它之后仍挂着一个默认开启的 `pre_pr_review`（第二个 agent，最多 2 轮、单轮 1800s，且能改代码再 push）与两处会打回 agent 的验证命令。`--direct-pr` 让执行 agent 结束后**只剩机械步骤**（runner 受控提交 → push → 建 Draft PR），把门禁从 runner 侧转移到 PR 上的 CI。**代价已显式接受**：runner 侧不再有任何质量检查（可推出连测试都不过的 PR），唯一门禁是该仓库的 CI。该档位刻意限定为只对**无 PRD 锚点**的 Issue 有效，且与 `--fast-merge` 互斥；`--fast-merge` 的既有行为逐字不变。
- **准入规则调整（同一 PRD）**：`agent/ready` 从「唯一准入」收窄为「**守护进程自主挑选的准入**」——人的显式定向（`kc run --issue N` / PRD 路径）不再要求该标记。这疏通了一条此前不存在的能力：「**我想让我这台机器跑，别被别的机器抢走**」——不打标记、直接显式执行，零竞争（此前只能「打标记 + 抢」，而守护进程默认 120 秒轮询，人抢不过）。**随之必须补一处并发正确性**：显式定向不再要求标记后，领取时的原子性成为唯一防双跑机制，而首次领取当前是裸的 read-modify-write（`agent_runner_workflow.py` 无回读校验），两个并发领取者可双跑同一 Issue —— 本次补真 CAS（`R3`，需可执行负控）。同时显式定向到不可领取状态（被他人持有 / blocked 无解除标记 / 不存在）改为报明确错误而非静默成功。
- **daemon 互斥收窄（同一 PRD 同一条决策）**：`kc run` 与正在运行的本机守护进程**不再互斥**——现状是本机有守护进程在跑时任何 `kc run` 都被拒绝（CONFLICT，要求 `--takeover` 先停掉守护进程），这让上一条的「零竞争路径」实际被堵死。互斥收窄为**只挡队列轮询**（`--all-ready` / 无显式 target）：显式单目标与守护进程共存，各跑各的 Issue（工作树按 Issue 隔离；同仓多 Issue 并行本就是守护进程的既有能力，线程池）。**同目标的排他因此完全落在首次领取 CAS 上**，故该收窄必须与 CAS 同批或更晚落地。`--takeover` 语义不变（仍用于显式停守护进程）。
- 承接 PRD：`P1-FEAT-20261006-122336`（任意 Issue 可执行；含首次真实验证、`kc issue create --from-prompt` 入口、`--direct-pr` 档位、以及 daemon 互斥收窄）。

### 2026-10-14 验证门禁归用户配置，KedaCode 提供生命周期

- **产品边界**：仓库用户决定要运行/观察哪些检查、哪些检查是合并或验收所必需，以及如何解释检查结果。现有配置化验证命令和 GitHub checks 是这条边界的基础；平台负责统一生命周期与状态编排。
- **不把 provider 规则变成平台规则**：KedaCode 不以内部计算的 commit SHA、tree SHA、PR diff 指纹或固定证据格式作为通用验证有效性 oracle。某个检查若需要 revision 绑定，应由该检查/provider 自己定义并报告，生命周期只消费其结果与状态。
- **Supervisor 的职责**：监督器应在 `agent/supervising` 内完成它拥有的生命周期收尾（例如对账、发布、重新关联检查结果、repair/rebase PR branch）。需要代码修改时使用配置的 repair agent 在当前监督周期执行；不能因 `repair_pr_branch` 把 Issue 交回 `agent/running`，从而让生命周期倒退并重复认领。
- **路线调整**：新增 M12「User-Owned Validation Gates And Lifecycle」，作为验证、PR review 与自动合并之间的边界整理。现有 Realistic Validation / verifier 流程作为兼容迁移对象，不再扩大其平台内置 oracle。
- **当前状态**：用户定义验证门禁的配置契约仍待实施；Supervisor repair/rebase 留在 supervising 的生命周期边界已在本次改动中落地。此前本地尝试的内部 diff 指纹方案已撤回，不构成交付行为。

### 2026-10-16 路线重心调整：从流程闭环转向 Agent 能力

- **判断**：从人工准入、验证与 review、PR 监督、合并队列，到 Backlog 持续调度、loop 定时任务、统一管理终端和发布流程，任务"流转"这一圈已经基本闭环；继续在流程上加码的边际收益在下降。接下来投入的重心转向**让 Agent 本身可度量、可改进**。这不是放弃流程侧：流程类缺口转入维护带（见 Near-Term Delivery Order）。
- **已有的积累（这个方向不是从零开始）**：
  - 度量：Token 用量统计（CLI、Stats 页、按 PRD 汇总）；按 Agent 与预设的 attempt 表现统计（成功率、P50/P90 耗时、失败分类，来自执行时留存的快照，`P2-FEAT-20261009-171037`）；生命周期时间线里的调用明细。
  - 评测：多评审模型的 Agent 与模型效果评测（`P1-FEAT-20261012-103909`，#286）——同一评测集与基线、多个评审器各自评分、确定性检查与 LLM 评分分开展示、评审前先检查外发内容。执行侧已交付并合并，人工验收状态以该 PRD 的验收横幅为准。
  - 学习素材：Agent Runner 记忆持久化（长期记忆、skill 草稿与晋升，`P1-FEAT-20260626-093933`）、停滞监督、失败分类与回退链、独立 verifier 的判定与证据。
- **缺口（按代码现状核对过）**：
  1. 没有**版本化的黄金评测集与可复现的 harness**——评测目前靠每次人工选择样本。
  2. 没有**变更前后的回归基线**：改 prompt、skill、预设或模型之后，无法回答"变好还是变坏"。
  3. **度量与行为之间没有闭环**：统计与评测结果目前不参与 Agent / 模型的选择，选择仍靠人工配置的预设与回退链。
  4. **skill / 记忆的晋升没有评测验证**：`should_auto_promote` 只看自身的使用次数与成功率（观察值），既没有拿评测集验证过质量，也没有版本化与回滚。
- **推荐边界（AI 于 2026-10-16 提出，待人工确认）**：沿用「人工调度」，Agent 的改进**不绕过人**。
  1. 自进化产出的是**提案**（文件或配置补丁），经评测验证、由人批准后才生效；首期**逐条批准**，只有在积累足够的"批准且未退化"记录后，才评估对窄类提案放宽为批量批准。
  2. 可进化的对象限于 skill、prompt/模板、记忆与路由偏好；每次生效都可版本化、可回滚。
  3. **不得修改**：禁改路径、验证门禁策略、自动合并开关、凭据与 `safety.*` 配置，以及——**评测本身**（评测集、评审器配置、阈值）。被评测的一方不能改写评分规则，否则评测失去意义。
- **核心指标（推荐，待确认）**：用 3 个结果指标判断这个方向有没有用，而不是只看评测分数。
  1. **一次通过合并率**：PRD / Issue 的 Draft PR 不经返工（无 repair / 重新认领 / 人工改代码）直接合并的比例。
  2. **每个合并 PR 的人工介入时长**：从生命周期台账里的人工事件（`human/awaiting-review`、`agent/blocked` 的停留时长、人工评论与接管）派生。
  3. **每个合并 PR 的成本**：Token 用量折算，含返工与评审消耗。
  - **数据可得性（2026-10-16 对本机台账 `console.db` 实测，纠正此前"数据都已存在"的说法）**：
    - **一次通过合并率——台账里算不出来。** 生命周期事件有 `MERGED` / `ARCHIVED` 类型，但库里 `merged` 事件为 0 条，91 次运行中 72 次 `outcome` 为空；合并事实在 GitHub 上。现在只能用 attempt 级的代理指标：自 2026-10-05 起 keda 仓库 37 个 Issue 中，首次 attempt 即成功的有 8 个（22%）。
    - **人工介入时长——台账里没有这类数据。** `LifecycleEventType` 全部是 runner 侧事件，没有任何人工事件；`human/awaiting-review` 等标签只在 GitHub 上。只有 `blocked` / `unblocked` 能近似一部分。需要新增信号源（GitHub 标签时间线或审计记录）。
    - **成本——基本算不出来。** 633 次调用结束事件里只有 19 次（约 3%）带了 Token 用量，而主力执行器 qoder（384 次调用）**一次都没有上报**，所以按 Token 折算的成本对绝大多数运行是空的。
  - **结论**：在做黄金评测集之前，M13 要先补**度量数据的完整性**（见 M13「前置」），否则核心指标没有可靠的基线。**先测出基线再定目标值**，在有基线之前不设数字目标。
- **承接**：M13（Agent 评测）、M14（自进化 Agent）。暂无对应 PRD；第一步是把 M13 的首个里程碑拆成 PRD。

### 自 2026-09-17 快照以来已交付

- **Roadmap 单 PRD 控制、归档证据与仓库级 Autopilot 开关**（#148，`P1-FEAT-20260916-122645` 已归档）：统一右侧详情容器（`prd-detail.tsx` 的 `additionalTabs`）与受限 `.kedacode.toml` writer 落地。
- **PRD 生命周期台账与可观测 UI**（#153）：console SQLite schema v5（`prd_lifecycle_runs` / `prd_lifecycle_events`）+ `frontend-public` 执行过程 tab 与 Stats 生命周期口径。
- **Operator Skill 与可预期队列**（`P1-FEAT-20260924-020856` 已归档）。
- **Post-PR CI 决策契约（Agent-led）**（`P1-BUG-20260924-100212` 已归档）：`checks_state`/`checks_summary` 作为 Agent 观察事实输入，不再按聚合状态强制改写 Supervisor 动作，仅保留 `mergeable=false` 等非 CI 确定性安全门。
- **Roadmap CI/CD 监控与可选自动修复**（`P1-FEAT-20260916-134008` 已归档）：前置契约已满足后交付。
- **KedaCode CLI 的 Agent 机读契约**（`P1-FEAT-20260930-141135` 已归档）：`--output json` / 语义退出码 / `kc schema` 运行时自省。
- **浏览器 E2E 验证工具链**（`P1-FEAT-20260930-225500` 已归档）。
- **Daemon 崩溃对账与 Agent 会话续传**（`P1-FEAT-20260930-225000`，PR #210 已合并 `c4792fba`）：Phase -1 对账 + 三出口处置 + 声明式会话续传。
- **`kc run --fast-merge` 快速通道**（`P1-FEAT-20261005-215933`，PR #208 已合并 `2b7cae4c`）：per-run 旗标，builder 提交后旁路 Phase 4.5 的 rv_reexec 与独立 verifier。
- **行数红线清零**（PR #212 已合并 `866ddabc`）：4 个超 1000 非空行的源文件拆分完毕，`main` 的 CI 恢复绿色。
- **macOS 菜单栏状态面板**（`P1-FEAT-20260913-204531` 已归档，2026-10-28）：菜单栏图标 + 锚定 popover + 固定悬浮窗；真实入口验收通过，sidecar 并存与退出回收（含负向对照）、Finder 最小 PATH 定位均已实跑。

### 当前 pending / hold

**pending（待执行）**

1. `P1-FEAT-20261006-122336`（**任意 Issue 可执行**）：执行侧已随本 PR 交付——首次真实端到端实证（Issue #216 → Draft PR #217）、文档边界撤销、`kc issue create --from-prompt` 与 `--direct-pr` 档位、daemon 互斥收窄与首次领取 CAS 全部落地；待人工验收与合并后从本清单移除。归入 M1 与 M8。
2. `P1-FEAT-20261006-013227`（Stats 页 Token 用量补按 PRD 维度汇总）：执行侧已交付为 Draft PR #211，待人工验收与合并后从本清单移除。

**hold（已成形但暂缓，不参与排期）**

- `P1-FEAT-20261006-024434`（`kc run --quick` 轻量档）：2026-10-06 移入。方向被「任意 Issue 可执行」取代——它优化「出了 PRD 之后跑得慢」，而后者消除「必须出 PRD」这个前提；其识别出的行数债已由 PR #212 承接。

### 已知漂移（下次大更新时校准）

- 上方快照的若干"未完成"条目已被后续归档 PRD 交付覆盖（如 rebase detached HEAD branch guard、发布恢复后的 supervisor 安全闭环等，见 `tasks/archive/` 2026-05 至 2026-07 条目），正文未逐条回写。

## Target Workflow

1. 用户可以直接创建 GitHub Issue，也可以先写 PRD 后通过 `kc issue-from-prd` 发布 Issue；直接创建 Issue 时，用户应尽量写清需求、问题、复现路径、期望结果和约束，并可上传图片附件补充说明。
2. `kc` 读取 Issue 正文、评论和图片附件等上下文，判断任务是否已有足够信息进入后续流程。
3. 如果 Issue 描述不清楚，AI 可以在 Issue 中反问用户并等待补充。澄清是**可选增强**，不是执行前置——是否在信息不完整时开工，由打 `agent/ready` 的人决定，AI 不代劳。
4. 如果 Issue 需求已足够明确（无论是否关联 PRD），管理员通过添加 `agent/ready` label（可结合 `agent/claude`、`agent/codex` 或 `agent/kimi` 指定终端）标记 AI 介入。
5. 如果 Issue 没有关联 PRD，`kc` 可以（但不强制）把它视为待澄清候选：需求足够清楚时，人工直接打 `agent/ready` 即可进入执行；需求含糊或涉及多方决策时，才走需求澄清与合议流程。**没有 PRD 不构成执行障碍**。
6. （按需）`kc` 调用多 agent 合议能力，对 Issue 背景、目标、风险、实现边界、验收标准和图片上下文进行讨论；公开 transcript、synthesis 和建议动作写回 Issue comment。
7. 管理员在 Issue 中决定是否需要创建 PRD。**选择「不需要 PRD」是正常路径**（简单的需求直接打 `agent/ready` 即可执行），不是例外处理。
8. 管理员决定创建 PRD 后，`kc` 根据 Issue 与合议结果生成 PRD 草稿，并把草稿内容或草稿链接写回 Issue，等待管理员确认。
9. 若决定创建 PRD，管理员确认后 `kc` 把 PRD 真实写入仓库 `tasks/pending/`，并在 PRD 与 Issue 之间建立双向链接。若决定不创建，则直接进入下一步。
10. runner 基于仓库现有架构与规范创建隔离 worktree，并把 issue、PRD 和执行规则传给 agent。
11. 执行 agent 在动代码之前先做一次 PRD 引用核验（由默认 `execution` 提示模板内置的 `PRD map check` 规则提供；所有仓库通用，非快速档专属，**不是**独立流水线阶段）：核对 PRD 点名的文件路径 / 符号 / 配置键是否仍存在于当前 worktree；已变更则以当前代码为准调整路线、不重建已不存在的旧结构；PRD 计划新增的按计划实施；收尾总结固定带一行 `PRD map check:` 结论（无过期引用写 `none`）。该指令是**增益而非门禁**——agent 未执行或核不出结论时不阻塞交付。**不做** agent 侧触碰面预测、**不做**并行撞车避让（原设计的该半边整体取消，理由与决策记录见 `tasks/archive/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance.md`）。
12. agent 修改代码、测试和必要文档；runner 通过受限 commit proxy 完成本地提交。
13. runner 执行配置化本地验证（含 pre-commit 验证命令与 `runner.verification_commands`），失败时把日志摘要交回 agent 做有限次数恢复。
14. runner 做发布前安全检查和 pre-push AI review；reviewer 如需修改，仍通过受限 commit proxy 和配置化验证命令完成。
15. review 通过后，runner 推送任务分支、创建 Draft PR，并把 Issue 移入 `agent/supervising`。
16. post-PR supervisor 检查 PR context、Issue/PR comments、diff、仓库配置的验证门禁结果、checks 与 mergeability；门禁通过后进入 `agent/automated-review-passed`。Supervisor 的对账、发布与 repair/rebase 全程留在 `agent/supervising`，需要代码修改时调用配置的 repair agent；按生命周期等待、请求人工决策或失败收敛。独立 verifier 是当前实现，按 M12 迁移。
17. **快速档仓库**在 `agent/automated-review-passed` 阶段额外挂合并队列：只消费仓库声明为必需的验证门禁与分支保护结果，再按仓库策略推进签收、rebase/验证和 squash 合并并写 `kc:event`（auto-merged）评论；任一步失败走既有失败/修复路径，不阻塞其余 PR。当前固定 verifier、自动签收与验证重跑行为是待 M12 整理的实现，不能成为所有仓库必须采用的验证规则。严格档仓库保持原样停在人工 Review。
18. `review-once` / `review-daemon` 持续观察已进入 `agent/supervising` 或 `agent/automated-review-passed` 的 Issue；当 PR head/base、CI/check、Issue/PR comment 或 mergeability 变化时重新运行 supervisor cycle。
19. **快速档仓库**的 `kc daemon run` 在每轮 pass 头部执行 roadmap 持续调度：对账 running 队列条目（merged/archived → completed、failed → failed 泊车不重试）、按 `max_parallel` 补位晋升 queued 与 `tasks/pending/` 中新发现的合格 PRD，幂等建 Issue / 打 ready 标签交 Phase 2 消费；`kc roadmap advance [--dry-run]` 作为一次性入口。
20. 发布阶段失败但本地 commit 已存在时，operator 可用 `kc recover-publish` 完成发布收尾；目标状态应与普通 Draft PR 发布一致，进入 supervisor 闭环后再交给人工 review。
21. 在需求不明确、rebase 冲突无法安全确认、发布失败、验证失败或高风险 review 发现时安全停止，并输出明确的人工处理建议。

## Milestones

### M0: CLI And Queue Foundation

Status: Completed.

- 注册 `kc` CLI。
- 支持仓库本地 `.kedacode.toml` 初始化。
- 支持标准 label 同步。
- 使用 GitHub Issues / Labels 作为队列状态源。
- 通过 `.kedacode.toml`、`config.toml`、环境变量管理 runner、labels、git、worktree、safety、prompts、review、supervisor 和 generated content 配置。

### M1: Human-Gated Task Intake

Status: Partially completed.

- 已完成：通过 `agent/ready` label 和 agent 路由 label 控制 issue 是否进入 runner。
- 已完成：从 PRD 创建 Issue，并在 Issue body 中保留 canonical PRD 路径和验收摘要。
- 已完成：`issue-from-prd --ready` 在 PRD 未发布前不会提前添加 ready label。
- 未完成（本次登记）：**`kc run` 对任意 Issue 通用**——人手写的、无 PRD 锚点的、正文自由文本的 Issue 只要带 `agent/ready` 就能被领取并端到端执行。运行侧代码路径已具备（各门禁对无 PRD 条件生效），但**从未被真实验证**；由 `P1-FEAT-20261006-122336` 交付，含首次端到端实证与文档边界对齐。
- 未完成（本次登记）：`kc issue create --from-prompt "<一句需求>"` —— 直接从自然语言建出不含 PRD 指针的 Issue，并可选择**立刻进队列**（`--ready`：打上就绪标记，任何为该仓库轮询的守护进程先到先得，**与哪台机器无关**）或**先不进队列**（默认：谁的守护进程都不领，由人显式 `kc run --issue N` 执行）。系统不提供「指派给某台机器」的机制。由 `P1-FEAT-20261006-122336` 交付。
- 未完成（本次登记）：**显式定向不受就绪标记约束 + `kc run` 与守护进程共存** —— `agent/ready` 从「唯一准入」收窄为「**守护进程自主挑选的准入**」，人的显式 `kc run --issue N` / PRD 路径不再要求该标记；daemon mutex 收窄为只挡队列轮询，本机守护进程在跑时也能手动跑**另一个** Issue（对守护进程正持有的 Issue 仍拒绝，依据认领状态）。同目标的排他改由首次领取的真 CAS 承担，故两项须同批落地。由 `P1-FEAT-20261006-122336` 交付。
- 未完成：完整交互终端中浏览 issue、选择任务、追问需求和展示状态。
- 未完成（**按需能力，非执行前置**）：无 PRD 的 Issue 进入澄清候选池、经管理员决策转换为 PRD 草稿；以及 PRD 草稿在管理员确认前只写回 Issue、不落盘。这些能力对复杂需求仍有价值，但不再是执行前的强制链。
- 未完成（**按需能力**）：在 Issue 描述不清楚时自动生成面向用户的澄清问题并等待补充。
- 未完成：读取和引用 Issue 图片附件，将截图、设计稿或流程图纳入澄清、合议和 PRD 草稿上下文。

### M2: Code Change Agent

Status: Completed for the basic code-change path; 完整的结构化规划（任务前的形式化影响面识别与验收标准校验）仍是 future work。执行前的 PRD 引用核验已落地，但形态是"默认 `execution` 模板内置的 `PRD map check` 规则"，**不是**原设计的独立 re-grounding 阶段，也不含触碰面预测。

- 已完成：runner 能创建或复用 issue worktree，并启动 Codex、Claude 或 Kimi。
- 已完成：内置 `kc worktree` 管理 `.kc-worktrees/<branch>`，并在 worktree 路径漂移时 fail fast。
- 已完成：prompt 会要求 agent 读取 `AGENTS.md`、遵守仓库规范、修改代码、测试和必要文档。
- 已完成：runner 通过 commit request 文件完成受限提交。
- 已完成：prompt template / phase 配置化，避免每次调整 execution prompt 都改 Python 代码。
- 已完成：Agent Runner 记忆（长期/短期/skill 草稿/已晋升 skill）锚定在目标仓库主检出根，跨 worktree / 跨 Issue 真实持久化；并发写入采用 tmp + `os.replace` 原子落盘。
- 已完成：`runner.pre_commit_verification_command` 在 commit 阶段独立执行，与 `runner.verification_commands` 解耦。
- 已完成：执行前的 PRD 引用核验——默认 `execution` 提示模板内置 `PRD map check` 规则，agent 开工前核对 PRD 引用的路径 / 符号 / 配置是否仍有效，收尾总结带 `PRD map check:` 声明行；非门禁、失败静默退回现状。
- 未完成：任务前的结构化规划、影响面识别和验收标准校验仍主要依赖 agent 自身执行，尚未形式化到 M3 / M8 之前；触碰面预测与并行撞车避让已明确不做（见 Open Questions 与 `P1-FEAT-20260703-105340` 决策记录）。

### M3: Verification And Review

Status: Partially completed.

- 已完成：自动运行配置化验证命令（含 pre-commit 与 runner.verification_commands 两套）。
- 已完成：验证失败、agent CLI 异常、commit request 错误、pre-commit 失败和零提交场景已有有限 recovery loop。
- 已完成：失败时把 issue 标记为 `agent/failed` 并写入失败 comment。
- 已完成：pre-push AI review gate 会在 push 前独立检查最终 diff；未批准时不会发布 PR。
- 已完成：空 commit request 在 pre-push review 中按 reviewer verdict 收敛或软失败，不再误报为 hard fail。
- 已完成：post-PR supervisor 会在 Draft PR 创建后至少运行一次，并支持 approve、repair、rebase、resolve-conflict、human-input-needed 和 failed 结果。
- 已完成：review-daemon 能检测 head/base、checks、comments 和 mergeability 变化，并避免 supervisor 自写评论触发自循环。
- 已完成：独立 verifier gate 落地——Realistic Validation sign-off 由独立 verifier agent 评估，verifier 默认换 agent/model、干净 worktree、对抗性验证，通过后打 `validation/verifier-passed` 标签，head 漂移自动清除。
- 路线调整（M12）：上述 verifier 与 Realistic Validation 是当前实现，不定义平台必须采用的通用验证 oracle；后续迁移到仓库用户配置的检查门禁与平台生命周期契约。
- 已完成：关键阶段写入 `kc:event` marker，形成基础审计 cursor。
- 未完成：把高风险 finding count 转换为稳定阻断规则或转人工规则。
- 未完成：PR 正文实现摘要、验证详情和残余风险仍缺少强 schema 化校验。
- 未完成：更细粒度的失败分类、恢复策略和面向 operator 的审计时间线视图。跨进程崩溃分类（`stale_attempt`）与三出口恢复策略已随 `P1-FEAT-20260930-225000` 落地；时间线视图与 finding 计数规则化仍是缺口。
- 未完成：UI 类 Issue 的浏览器 E2E 验证通道（真实启动应用 + 浏览器执行 + 产物证据）——由 `P1-FEAT-20260930-225500`（浏览器 E2E 验证工具链）承接，2026-10-05 登记本里程碑。
- 已完成：跨进程边界的恢复——daemon / host 崩溃后的僵尸 attempt 对账与 agent 会话续传——随 `P1-FEAT-20260930-225000`（Daemon 崩溃对账与 Agent 会话续传）交付：daemon 每轮 tick 头部的 Phase -1 对账（证据源限定为 claim 存活 / label / comment 历史 / worktree 现场四件现成事实），三出口处置（`resume-session` / `re-enqueue` / `mark-failed`）均留四要素对账 comment，内容 hash 去重保证幂等；agent 侧按 `supports_resume` 声明式能力续传会话，无记录时降级为全新会话。`kc daemon` 侧新增 `[agent_runner.daemon] reconcile_stale_attempts`（默认开）与 `reclaim_ttl_seconds`。详见 `docs/guides/agent-runner.md` 的「崩溃对账与 Agent 会话续传」。

### M4: Pull Request Automation

Status: Partially completed.

- 已完成：自动推送任务分支并创建 Draft PR。
- 已完成：PR body 包含 `Closes #<issue-number>`，Issue comment 记录分支、PR URL 和验证结果。
- 已完成：Draft PR 创建后 Issue 从 `agent/running` 流转到 `agent/supervising`，supervisor 通过后再进入 `agent/automated-review-passed`。
- 已完成：可通过 `[agent_runner.generated_content]` 生成更完整的 Issue / PR Markdown，并在锚点缺失时 fallback。
- 已完成：PRD-backed Issue 发布前会强制检查 Acceptance Checklist，并在完成时归档 PRD。
- 已完成：发布前会校验 remote、branch 和 forbidden paths，降低错误发布风险。
- 已完成：`kc recover-publish` 支持发布阶段失败后的显式恢复命令，并可复用已有 open Draft PR。
- 已完成（新增）：快速档合并队列（verifier 绿灯 → 自动签核 → rebase → 全量验证 → 禁改终扫 → squash 合并）作为 `agent/automated-review-passed` 阶段的延伸，已随 `P1-FEAT-20260703-105322` 交付归档（实现：`src/backend/core/use_cases/agent_runner_merge_queue.py`；测试：`tests/test_agent_runner_merge_queue.py`；门控：`config.toml` 的 `[agent_runner.autopilot]` + `[agent_runner.safety].auto_merge`）。
- 未完成（本次登记）：`--direct-pr` per-run 档位——执行 agent 结束后只保留机械步骤（runner 受控提交 → push → 建 Draft PR），跳过 pre-PR review 与 runner 验证命令，把门禁转移到 PR 上的 CI。仅限无 PRD 锚点的 Issue，与 `--fast-merge` 互斥。由 `P1-FEAT-20261006-122336` 交付。
- 未完成：发布恢复成功后仍需与普通 Draft PR 发布保持同样的 post-PR supervisor 安全闭环。
- 未完成：默认分支 token 匹配、发布失败阶段分类和只读 supervisor dirty guard 仍在 pending PRD 中。

### M5: Multi-Repository Runner

Status: Completed.

- 已完成：在 `config.toml` 中通过 `[agent_runner.repositories]` 声明多个目标仓库，用于 legacy registry 和 `--repo-id` / `--all`。
- 已完成：`labels sync`、`run-once`、`review-once` 和 `daemon` 支持当前仓库、`--repo`、`--repo-id` 和 `--all` 目标解析。
- 已完成：`issue-from-prd` 在多仓库配置存在时通过当前仓库、`--repo` 或 `--repo-id` 解析到唯一目标仓库。
- 已完成：API 状态端点返回多仓库配置摘要和基础健康信息。

### M6: Main Branch Rebase Automation

Status: Partially completed.

- 已完成：post-PR supervisor 可请求 `rebase_pr_branch`，runner 会校验当前 branch 和 HEAD、fetch base、rebase、重新运行验证，并用 `--force-with-lease` 推送 PR branch。
- 已完成：rebase 冲突时可调用 agent 辅助解决，解决后继续 verification 和 push。
- 已完成：`review-once` / `review-daemon` 已按 Issue label 轮询，并能检测 base、checks、comments 和 mergeability 变化。
- 未完成：rebase 冲突中 Git 进入 detached HEAD 时，runner 还不能可靠识别 active rebase target 并保持分支安全门禁。
- 已补齐：Supervisor-owned pending rework marker 在 `agent/supervising` 下可续跑；Issue 不再退回 `agent/running`。
- 未完成：旧 runner-owned CI rework state recovery 和其他恢复后的 supervisor 状态机仍需补齐。
- 未完成：冲突失败分类、blocked/forbidden resolution 和 operator 操作建议仍需收敛。

### M7: Deliberation And Review Intelligence

Status: Partially completed.

- 已完成：支持 `kc deliberate` 多 agent 合议会话，用于需求澄清、方案争议和复杂设计评估。
- 已完成：支持只读 transcript、最终 synthesis、事件流、session metadata 和隔离 workspace 输出。
- 已完成：合议能力与 issue runner 解耦，不修改代码、不创建 branch、不创建 PR。
- 已完成：独立 verifier gate 把"对抗性只读 agent 评估"复用为 Realistic Validation sign-off 的唯一绿灯来源；与 deliberate 共享 `IAgentTranscriptRunner` 端口，不新建第二个评估抽象。
- 未完成：把面向 Issue 的合议 transcript、关键分歧、推荐结论和后续动作写回 Issue comment。
- 未完成：把合议结果接入 task intake、PRD 草稿生成、PRD review 或 code review，但不暴露隐藏思维链。

### M8: Issue-First Clarification And PRD (On Demand)

Status: Not completed. **本里程碑是「按需增强」而非「执行前置」**（2026-10-06 定位调整）：无 PRD 的 Issue 可以直接执行，本节的澄清与 PRD 生成能力服务于需求复杂、涉及多方决策或表述含糊的场景；简单需求不需要经过本里程碑任一环节。

- 支持用户直接提交没有 PRD 的 GitHub Issue；这类 Issue **可直接被执行**（人工打 `agent/ready` 即可），也可按需进入澄清与 PRD 流程。
- 需求含糊或涉及多方决策时，支持先写入澄清问题并等待用户回复；澄清是可选增强，不构成执行前闸门。
- 支持读取 Issue 中的图片附件，并在合议与 PRD 草稿中引用这些图片所表达的界面状态、错误信息或设计约束。
- （按需）对无 PRD Issue 启动多 agent 合议，讨论需求清晰度、实现边界、验收标准、风险和是否需要 PRD。
- （按需）将公开讨论 transcript、最终 synthesis、PRD 建议和待管理员决策项写入 Issue comment，形成可审计记录。
- 管理员在 Issue 中决定是否创建 PRD。**选择不创建是正常路径**：简单需求直接打 `agent/ready` 执行，不必先补 PRD。
- 管理员决定创建 PRD 后，`kc` 先生成 PRD 草稿并写回 Issue，等待管理员确认草稿内容。
- 管理员确认后，`kc` 把 PRD 落盘到 `tasks/pending/`，在 PRD 中记录关联 Issue，在 Issue 中记录 PRD 路径。
- runner 在 Issue 进入 `agent/ready` 后开始执行代码任务，**不要求 PRD 已存在**。

### M9: Operations Console

Status: Completed. 监控面板与时间线 API 已随 `#39` / `#81` / `#89` 落地，随后升级为统一管理终端（写操作白名单 + 审计），并在 `P1-FEAT-20260910-111901-iar-console-bundled-web-terminal` 中补齐分发能力。

- 提供监控面板，展示队列统计、仓库健康、Issue 状态、PR context、worktree 状态和最新事件。
- 提供 Issue 时间线 API，复用 `kc:event` marker、Issue comments、PR comments 和本地 worktree 状态。
- 检测 label 与 PR/worktree 状态不一致、failed/blocked、dirty worktree、stale PR、checks failed 和缺失 supervisor event 等异常。
- 运维写操作收敛在管理终端 API（白名单动作 + 审计）：启停 daemon / runner 进程、重试 failed、roadmap 队列与 idea inbox 管理；直接写 GitHub label/comment/PR 仍走 CLI。
- 统计接口线程池并发构建并带 TTL 缓存，多仓库下仪表盘首屏不串行等待。
- 分发能力：`frontend-public` 静态导出产物随 wheel 打包，`kc console` 一条命令启动 API + 内置面板并自动打开浏览器；无需源码检出、Node / pnpm / just。

### M10: Autopilot Fast-Lane (Product Repos)

Status: Completed. 随 `P1-FEAT-20260703-105322` 交付归档；实现 `src/backend/core/use_cases/agent_runner_merge_queue.py`，测试 `tests/test_agent_runner_merge_queue.py`，门控 `config.toml` 的 `[agent_runner.autopilot]`（与 `[agent_runner.safety].auto_merge` 双开关同时为真才生效）。

- 快速档开关：在 `.kedacode.toml` 写入 `[autopilot] enabled = true` 与 `[safety] auto_merge = true`，双开关同时为真才激活；默认全关，严格档仓库零行为变化。
- 合并队列：review pass 末尾按 Issue 号 FIFO 串行处理 supervisor 已 approve 的 PR，链式门禁（verifier 绿灯 → 自动签核 → rebase 最新 base → worktree 全量验证重跑 → 禁改路径终扫 → checks 全绿 → squash 合并），任一步失败转既有修复路径、不阻塞队列中其余 PR。
- 自动签核：合并前自动勾选 PR body 的 Realistic Validation sign-off 清单并发 `kc:auto-sign-off` marker 评论；勾选与评论均幂等，崩溃重入不重复动作。
- 队列状态：复用 `agent/automated-review-passed` 标签与 PR 状态表达进度，无新存储；仍有 PRD 人工验收项时添加 `human/awaiting-review`，待办完成或合并成功后清理；合并成功后发 `kc:event`（auto-merged）评论并摘除自动审查通过标签。
- 显式逃生：`safety.auto_merge` 维持为死开关等价物直到 `autopilot.enabled` 同时为真；`recover-publish` / 现有 review daemon 行为不被动；`auto_merge` 语义激活需要发布说明显式提示。
- 跨仓库边界：每仓独立串行合并，不做跨仓库统一编排；merge 方式仅 squash。

### M11: Roadmap Continuous Scheduling (Fast-Lane)

Status: Completed. 随 `P1-FEAT-20260703-105330` 交付归档；实现 `src/backend/core/use_cases/roadmap_actions.py` 的 `advance_roadmap_queue`（对账 / 槽位核算 / 发现式晋升），CLI `kc roadmap advance [--dry-run]`，daemon 在 `autopilot.enabled` 时于 Phase 2 之前插入该阶段。

- daemon 内每仓 pass 头部新增持续调度阶段（`autopilot.enabled` 门控）：对账 running 队列条目（merged/archived → completed、failed → failed 泊车不重试）→ 重算依赖 → 按 `max_parallel` 补位晋升 queued 与 `tasks/pending/` 中新发现的合格 PRD。
- 晋升动作 = 幂等建 Issue（复用既有"同名 Issue 已存在则复用"判定）+ 打 `agent/ready` + 队列条目置 running；**不** spawn 独立 runner 进程——Phase 2 既是 ready 标签的消费者也是执行入口，由 daemon `--concurrency` 统一约束并发。
- 槽位口径：沿用 `start_global_roadmap` 的 RUNNING-only（supervising/review/blocked 不占槽、不晋升）；blocked 保留 running 记录但不计槽位。
- 新 CLI：`kc roadmap advance [--dry-run]` 作为一次性入口；dry-run 零副作用，可用于观察下一轮调度计划与人工对账。
- 幂等与竞态：与 console 手动 `start_prd` 并发最坏结果是同 PRD 出现手动+自动两条队列记录，但 Issue / ready 标签唯一，不会双开执行。
- 失败处理：失败 PRD 泊车（failed + error_detail），不自动重试、不阻塞其余 PRD；状态解析依赖 GitHub 可达性，gh 失败时保守返回，下一轮 pass 自愈。
- M11 自身是 `M10` 之后的工作流闭环；非快速档仓库零变化，console 手动路径完全保留。

### M12: User-Owned Validation Gates And Lifecycle

Status: Partially implemented (2026-10-15：Supervisor repair/rebase 留在 supervising 并可恢复中断的 pending marker；用户定义验证门禁契约仍待实施)。

- 仓库用户配置要运行或观察的验证门禁及其必需性；优先复用现有 `runner.verification_commands`、pre-commit 命令和 GitHub checks，不另造固定的 KedaCode 验证清单。
- KedaCode 提供统一生命周期：启动/观察检查、记录可审计结果、等待外部 CI、把真实失败交给 Supervisor/执行器修复，并在结果变化后继续监督。
- 验证判定由配置的命令、workflow 或 provider 负责。生命周期不自行推断 SHA/tree/diff 指纹等平台内部规则来宣告证据过期；检查需要 revision 绑定时，由对应检查契约表达。
- Supervisor 在 supervising 阶段完成对账、结果发布、repair/rebase 和中断恢复；需要代码修改时调用配置的 repair agent，不通过 `agent/running` 重新认领 builder。
- 迁移现有 Realistic Validation、独立 verifier、自动签收与 merge queue 的验证依赖，定义兼容/弃用边界，避免现有仓库无配置时静默改变门禁语义。
- 验收至少覆盖：不同仓库可配置不同验证政策；失败、未运行、等待和通过能走正确生命周期；监督器收尾不会触发无意义的 builder 重跑；自动合并只消费用户声明的必需门禁结果。

### M13: Agent Evaluation And Regression Baseline

Status: Partially implemented（度量与单次评测已交付；评测集、harness 与回归基线未开始）。这是 2026-10-16 起的主线。

- **已有**：Token 与按 Agent/预设的 attempt 表现统计；多评审模型评测（`P1-FEAT-20261012-103909`）。
- **前置：度量数据的完整性**（2026-10-16 实测发现的缺口，先于评测集）：
  1. 合并结果入账：让合并 / 归档真正写入 `merged` / `archived` 事件，使"是否合并、是否一次通过"可从台账直接读出，不必每次回 GitHub 拉取。
  2. 人工事件入账：把人工审阅等待、人工接管、人工评论等记为台账事件（或可由 GitHub 标签时间线可靠派生），否则"人工介入时长"无从计量。
  3. Token 上报覆盖：主力执行器 qoder 当前 Token 全为 0，覆盖率约 3%；需要执行器侧上报、或对无上报的执行器给出估算口径并显式标注"估算"。
- **版本化的黄金评测集**：一组带固定输入与基线的任务（含 PRD 与无 PRD 的 Issue），评测集本身有版本号，样本不随被测 Agent 变化；样本来源与"避免被测 Agent 见过答案"的隔离规则需先定（见 Open Questions）。
- **可复现的 harness**：在干净的目标仓库副本上完成评测任务（沿用 2026-06-26 收录的想法：预设若干 PRD、复制模板仓库、完成后对比），记录成功率、耗时、成本、**人工介入时长**，并可对比"用 / 不用 KedaCode"。
- **确定性检查与 LLM 评分分开**：沿用多评审模型评测的口径——确定性测试通过率、LLM 评审分数、评审分歧各自展示，不默认折算成单一总分；评审前检查外发内容。
- **回归基线与门禁**：对 prompt 模板、skill、预设、模型、回退链等变更，能在评测集上给出"前后对比"，并把显著退化作为可选的合并前信号（不做强制门禁，先观察）。
- **评审器校准**：用一小批人工标注样本检验评审模型的一致性与偏差，评审结论必须可追溯到原始评分。
- 非目标：不在此里程碑内自动修改任何 Agent 配置；评测结果只进入报告与对比视图。

### M14: Self-Evolving Agent（评测约束下的、经人批准的自进化）

Status: Not started；记忆持久化与 skill 草稿晋升是已有的起点，但判据只有使用次数与成功率。

- **闭环**：信号 → 提案 → 评测验证 → 人工批准 → 生效（版本化）→ 监测 → 可回滚。
  - 信号：失败分类、review finding、verifier 判定、人工修正、停滞事件、评测与统计结果。
  - 提案：skill 草稿、prompt/模板微调、记忆条目、按任务类型的 Agent/模型路由与回退建议。提案是文件或配置补丁，**不是直接生效的修改**。
  - 验证：提案必须在 M13 的评测集上证明不退化（或在目标指标上改善）后才进入人工批准。
- **版本化与回滚**：每个生效的 skill / 模板 / 路由策略都有版本与来源提案；能一键回到上一版本，并保留"为何生效、依据哪次评测"的可审计记录。
- **评测驱动的路由**：在有足够样本的前提下，让 Agent/模型选择参考实测表现（按任务类型的成功率、耗时、成本），仍由人配置上限与允许集合；样本不足时保持现有的人工预设行为。
- **边界**：不得自动修改禁改路径、验证门禁策略、自动合并相关开关，也不得扩大自身权限；所有生效动作留人工批准点。
- 依赖：M13 的评测集与回归基线是前置——没有它，自进化无法区分"改进"和"过拟合/退化"。
- **go / no-go 检查点（M13 首个里程碑的试点结束后评估一次，再决定是否投入 M14）**：
  1. harness 的方差是否小到能分辨差异（同一 Agent 同一任务重复运行的结果波动）？不能则先改 harness 与判分，不进入 M14。
  2. 仅靠 M13 的评测结果加人工调整（选模型、改 prompt 与预设）已经拿到多少收益？如果大部分收益已经到手，M14 只保留范围最窄的一项（给 skill 晋升加评测验证），其余暂缓。
  3. 样本量是否足够：本仓库的任务数量与可判分程度能否支撑统计上的"改进"结论？
  4. 评测成本是否在预算内。
  - 检查点的结论与依据写回本文件；任一项不满足时，M14 缩小范围或暂缓，不硬推。

## Near-Term Delivery Order

> 2026-10-16：下列 1–3 是新的主线；原先的流程类条目（4 起）转入**维护带**——只在它们阻塞评测或自进化、或出现真实故障时优先处理，不再作为投入重心。
>
> **维护带里优先级最高的是运行可靠性**，因为评测要消耗大量 API 额度，harness 或底层运行不稳，评测结论就不可信：
>
> - **GitHub API 额度**：跨进程共享的额度账本与暂停闸门已交付（`kc` 的用量按进程来源归因）。待账本积累一天以上的数据后，用数据判断 Monitor 的逐 Issue 扫描等是否仍是大头，再决定是否做合并查询、条件请求（ETag）或为 daemon 单独配置身份。
> - **常驻 daemon 的版本可见性**：`kc daemon status` 目前只给出 `pid` 与 `started_at`，看不出它加载的是哪一版代码，也不会提示"比已安装版本旧"。2026-10-15 的持续限流，根因正是一直没重启、仍跑旧代码的 daemon。需要在状态输出里暴露所加载的版本，并在落后于已安装版本时提示重启。

1. **Agent 评测基线（M13 首个里程碑）**：先定评测集的格式、版本与隔离规则，再做可复现的 harness（含人工介入时长），拆成 PRD。
2. **回归基线与对比视图（M13）**：在评测集上对比 prompt / skill / 预设 / 模型变更前后的表现，并接入现有 Stats 页的度量口径。
3. **自进化的最小闭环（M14，须先通过 M13 试点后的 go / no-go 检查点）**：从 skill 草稿晋升切入——把晋升判据从"使用次数与成功率"升级为"经评测验证 + 人工批准 + 可回滚"，再扩展到模板与路由提案。

4. **用户定义验证门禁与 KedaCode 生命周期（M12）**：先明确配置契约、门禁结果模型与现有 verifier / Realistic Validation / merge queue 的兼容迁移；Supervisor repair/rebase 已按线性生命周期留在 supervising。
5. **autopilot fast-lane 的发布说明（本组唯一剩项）**：合并队列（`P1-FEAT-20260703-105322`）与 roadmap 持续调度（`P1-FEAT-20260703-105330`）**已交付归档**；第三件"执行前 re-grounding 与触碰面避让"已收缩为默认 `execution` 模板内的 `PRD map check` 核验指令并交付（`P1-FEAT-20260703-105340`）。剩余只有 `safety.auto_merge` 语义激活的升级提示与渠道（README / release notes / daemon 启动日志）待定。
6. **Agent Runner 记忆锚点稳定化落地与防回归**（**已交付归档**）：`P1-BUG-20260704-153640-agent-runner-memory-stable-anchoring` 把所有记忆目录绝对化到目标仓库主检出根、共享写入原子化、证据脚本强制 `git worktree add` 双副本；整套记忆系统"跨 Issue 复用"已生效。
7. **层迁移善后**（**已交付归档**）：`P1-REFACTOR-20260703-184226-api-engines-layer-migration` 把 `api/` 对 `engines/agent_runner/` 的直连 import 全部清零，架构检查恢复严格态稳定通过；`CLAUDE.md` / `docs/ai-standards/architecture.md` / `docs/architecture/system-design.md` 三处表述一致。
8. **发布恢复后的 supervisor 安全闭环**：恢复成功后先进入 `agent/supervising`，supervisor approve 后再进入 `agent/automated-review-passed`，并修正分支 token 匹配与发布失败阶段分类。
9. **rebase detached HEAD branch guard**：确保 active rebase target 可确认时允许继续，无法确认时安全停止并输出可诊断错误。
10. **CI rework state recovery、blocked/forbidden resolution 与 process runner 错误可诊断性增强**：从 supervisor 修复回路单点补齐。
11. **Agent Runner operator 监控面板、异常检测和 Issue 时间线 API**（M9）：已交付，并升级为统一管理终端 + `kc console` 随 wheel 分发一键启动。
12. **基于现有 generated content 和合议能力补齐 Issue -> PRD / PRD rewrite（按需）**：`agent/rework-prd`、管理员 PRD gate、确认后落盘 PRD；M8 全闭环。**注意本项已从「执行前置」改为「按需增强」**——无 PRD 的 Issue 可直接执行，本项不再阻塞任何交付。
13. **把多 agent deliberation 接入 PRD review**：生成结构化 verdict、finding、risk 和后续动作 comment，与 M8 协同。
14. **高风险 review finding 的稳定阻断规则**：定义哪些风险必须转人工，哪些可以自动重试或自动合并。
15. **PR 正文 schema 校验**：强制包含实现摘要、验证结果和残余风险；与 M3 验证门禁协同。
16. **在 CLI、监控能力和 Issue-first clarification / PRD（按需）稳定后**，再完善更完整的交互终端体验（M1 剩余项）。

## Acceptance Checklist

- [x] 不会自主决定开始处理哪些 issue。
- [x] 能够通过 GitHub label 接收人工选择的 issue。
- [x] 能够从 PRD 创建可追踪的 GitHub Issue。
- [x] 能够在 PRD 发布前延迟 ready label，避免 runner 读取过期 PRD。
- [x] 能够从人工选定的 issue 中提取 PRD 路径和验收摘要。
- [x] 能够在隔离 worktree 中启动指定 agent。
- [x] 能够用内置 `kc worktree` 管理默认 worktree 路径，并在路径漂移时 fail fast。
- [x] 能够自动运行配置化验证命令（含 pre-commit 与 runner.verification_commands 两套）。
- [x] 能够自动提交受控变更、推送任务分支并创建 Draft PR。
- [x] 能够在验证失败、agent 执行失败、commit request 错误或 pre-commit 失败时做有限恢复。
- [x] 能够在失败时停止并报告原因到 Issue comment。
- [x] 能够处理多个目标仓库，并支持当前仓库、`--repo`、`--repo-id` 和 `--all` 解析。
- [x] 能够通过配置化 prompt template 管理不同执行阶段。
- [x] 能够在提交 PR 前完成独立 pre-push code review。
- [x] 能够在 pre-push review 空 commit request 时按 reviewer verdict 收敛或软失败。
- [x] 能够在 Draft PR 创建后运行 post-PR supervisor，并在通过后进入人工 review。
- [x] 能够通过 review-daemon 感知 head/base、checks、comments 和 mergeability 变化。
- [x] 能够避免 supervisor 自写 comment 导致 review-daemon 自循环。
- [x] 能够在 PRD-backed Issue 完成时检查 Acceptance Checklist 并归档 PRD。
- [x] 能够运行只读多 agent 合议并输出 transcript、synthesis 和事件记录。
- [x] 能够生成包含 `Closes #<issue-number>` 锚点的 PR body，并支持 template / agent 生成模式。
- [x] 能够在发布失败后通过显式 `kc recover-publish` 继续已有本地成果。
- [x] 能够在 rebase 冲突时调用 agent 尝试解决，失败或验证失败时安全停止并报告原因。
- [x] Agent Runner 记忆（长期 / 短期 / skill 草稿 / 已晋升 skill）锚定在目标仓库主检出根，跨 worktree / 跨 Issue 真实持久化；共享目录写入原子化不产生半写。
- [x] Realistic Validation sign-off 由独立 verifier agent 评估，verifier 绿灯以 `validation/verifier-passed` 标签物化。
- [ ] 验证门禁由仓库用户配置；KedaCode 只提供运行、观察、记录结果及监督修复的生命周期，不把内部 SHA/tree/diff 规则当作通用验证 oracle；Supervisor 自有收尾不重新派发给 builder（M12）。
- [x] `api → engines` 直连 import 已清零，`hooks/shared/check_architecture.py` 严格态稳定通过；`CLAUDE.md` 与权威架构文档三处表述一致。
- [ ] 能够在发布恢复后进入与普通 Draft PR 相同的 supervisor 安全闭环。
- [ ] 能够在 rebase detached HEAD 中间态正确识别 active rebase target。
- [ ] 能够通过完整交互终端浏览、选择 issue 或输入明确任务。
- [ ] 能够在需求不明确时主动追问（**按需增强**，非执行前置：信息不完整时是否开工由打标记的人决定，AI 不代劳）。
- [ ] 能够接收没有 PRD 的用户 Issue，并**直接执行**它（人工打 `agent/ready` 即可），也可按需将其纳入澄清 / 合议 / 审批流程。
- [ ] 能够在用户 Issue 缺少关键需求、问题背景或复现信息时，在 Issue 中提出澄清问题并等待用户补充。
- [ ] 能够读取用户上传到 Issue 的图片附件，并把图片上下文纳入澄清、合议和 PRD 草稿生成。
- [ ] 能够把无 PRD Issue 的合议 transcript、synthesis 和建议动作写回 Issue comment。
- [ ] 能够在管理员确认前只生成 PRD 草稿，不把 PRD 落盘到仓库。
- [ ] 能够在管理员确认后把 PRD 写入 `tasks/pending/` 并双向链接 Issue（PRD 是可选产物；无 PRD 的 Issue 也能直接执行）。
- [ ] 能够对任意 Issue（含人手写的、无 PRD 锚点的、正文自由文本的）在打上 `agent/ready` 后领取并端到端执行出 Draft PR，且已通过真实端到端验证（`P1-FEAT-20261006-122336`）。
- [ ] 能够从一句自然语言创建不含 PRD 指针的 Issue，并可选择立刻进队列（任何守护进程先到先得）或先不进队列、留给人显式执行（`P1-FEAT-20261006-122336`）。
- [ ] 能够显式定向执行任意可领取的 Issue 而不必先打就绪标记；`kc run` 与本机守护进程共存（互斥只挡队列轮询），同目标的排他由首次领取的真 CAS 承担（`P1-FEAT-20261006-122336`）。
- [ ] 能够对无 PRD 锚点的 Issue 用 `--direct-pr` 直接出 Draft PR：执行 agent 之后只剩机械步骤，runner 侧不再有第二个 agent 与验证命令，门禁转移到 PR 上的 CI（`P1-FEAT-20261006-122336`）。
- [ ] 能够从 Issue 生成或重写 PRD，并在多 agent PRD review 后等待人类确认。
- [ ] 能够用稳定规则阻止带有高风险问题的 PR 自动发布或自动转人工。
- [ ] 能够强校验 PR 正文包含完整实现摘要、验证结果和残余风险。
- [ ] 能够提供只读 operator 监控面板和 Issue 时间线 API。
- [x] 能够在快速档仓库中按 Issue 号 FIFO 串行 squash 合并 supervisor 已 approve 的 PR，且自动签核、rebase、全量验证、禁改终扫四道门禁全部生效（`P1-FEAT-20260703-105322` 已交付归档；实现 `src/backend/core/use_cases/agent_runner_merge_queue.py`）。
- [x] 能够在快速档 daemon 每轮 pass 自动对账 running 队列条目、按 `max_parallel` 补位晋升 queued 与 `tasks/pending/` 中新发现的合格 PRD，且 `kc roadmap advance [--dry-run]` 一次性入口可用（`P1-FEAT-20260703-105330` 已交付归档）。
- [x] 能够在执行 agent 启动前核验 PRD 引用的路径 / 符号 / 配置是否仍有效，并在收尾总结给出 `PRD map check:` 声明行；核验不通过不阻塞交付（`P1-FEAT-20260703-105340` 已交付归档；形态为默认 `execution` 模板内置规则，非独立阶段）。
- ~~能够在执行 agent 启动前注入 re-grounding 勘误附录并物化触碰面评论；触碰面相交时自动转 `agent/waiting` 让路、冲突消除后由调度循环自动 re-ready，且 defer 次数有上限防止活锁。~~ **已取消，不再作为验收项**：默认串行时"在途 running 集合"恒空永不触发，并发下触碰面发布竞态使其在最需要的场景失效；决策记录见 `tasks/archive/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance.md` §13 D-03。

- [ ] 合并结果、人工事件与 Token 用量都能可靠入账（执行器 Token 上报覆盖率有数字且标注估算口径），在此基础上给出三项核心指标（一次通过合并率、每个合并 PR 的人工介入时长、每个合并 PR 的成本）的当前基线值，并在 Stats 中随时间可见（M13）。
- [ ] 能够在 `kc daemon status` 里看出常驻 daemon 加载的代码版本，并在其落后于已安装版本时给出重启提示（维护带·运行可靠性）。
- [ ] 能够用一份**版本化的黄金评测集**在干净的仓库副本上复现评测，并记录成功率、耗时、成本与人工介入时长（M13）。
- [ ] 能够对 prompt / skill / 预设 / 模型的变更给出评测集上的前后对比，并让显著退化在合并前可见（M13）。
- [ ] 能够把确定性检查、LLM 评分与评审分歧分开展示，并用人工标注样本校验评审器的一致性（M13）。
- [ ] 能够让一项 Agent 改进以**提案**形式产生，经评测验证和人工批准后才生效，且可版本化、可一键回滚（M14）。
- [ ] 能够确认自进化流程不会修改禁改路径、验证门禁策略与自动合并开关（M14）。

## Open Questions

- M12 的仓库级配置如何表达门禁命令、GitHub check 名称、required/optional 与缺失配置语义，同时兼容现有 `runner.verification_commands`、独立 verifier 和快速合并队列？
- 是否只支持 GitHub，还是需要预留 GitLab 等平台适配层。
- 除了 issue label 准入控制外，是否还需要在某些高风险操作（如 rebase、force-push）前增加二次确认。
- PR 自动 rebase 后需要运行哪些最小验证集合。
- 何种风险级别必须转人工 review。
- 多仓库 daemon 是否始终顺序轮询，还是允许受限并发。
- 交互终端和前端 Dashboard 的边界如何划分。
- 无 PRD Issue 在**选择走按需澄清**时，其中间状态使用哪些 label 表达，例如 `agent/needs-triage`、`agent/needs-prd`、`agent/rework-prd` 或仅依赖 comment command。（注意：无 PRD 本身不再是需要标注的状态——它可以直接执行。）
- 管理员确认 PRD 草稿的交互方式是 Issue comment command、label、CLI 选择，还是三者都支持。
- 合议 transcript 写入 Issue 时的长度限制、摘要策略和敏感信息过滤规则。
- Issue 图片附件需要支持哪些格式、大小限制、下载缓存策略和隐私处理规则。
- AI 澄清问题的等待状态如何表达，例如 label、comment command、check run 或独立 intake state。
- 发布恢复命令是否应默认运行 post-PR supervisor，还是提供 operator 显式 `--no-supervisor` 逃生选项。
- review-daemon 对 comment 数量的 cursor 是否足够，还是需要后续记录评论 ID / 更新时间以区分编辑和删除。
- 快速档默认是否应在产品仓中开启，还是维持纯 opt-in；`safety.auto_merge` 激活后的升级说明以哪种渠道发放（README / release notes / daemon 启动日志）。
- 快速档是否允许只开"自动签核+rebase"但禁掉 squash 合并，或只禁合并保留自动签核；当前 PRD 锁 squash 是默认选择。
- 持续调度的 `max_parallel` 与 daemon `--concurrency` 同时启用时，是否需要在启动时显式校验二者口径并提示用户。
- 触碰面预测与并行撞车避让已在 2026-09-16 评估后**整体取消**（默认串行恒不触发 + 并发下发布竞态失效；触碰面本可从 PRD 自带的 `### Change Impact Tree` 静态解析，无需 agent 预测）。原"两个 Issue 几乎同时开工时互相看不见对方 touch-map 的漏网冲突"问题随之不再存在；若将来重启该方向，须先解决上述两个前提。冲突兜底仍由合并队列 rebase + 全量验证承担。
- **评测怎么定义"完成"与"变好"**。**推荐**：以确定性检查为主（固定任务上测试 / CI 通过），人工介入时长与成本为辅，LLM 评分只补充确定性测不了的部分、不单独作为依据；各项分别展示、不折算单一总分。"变好"取**配对比较**：同一批任务上成功率不低于基线（在噪声范围内），且耗时或成本改善。人工介入时长不用秒表计量，而是从事件派生——但**目前台账里没有人工事件**（2026-10-16 实测），需先补信号源（见 M13「前置」），再用 `human/awaiting-review` 与 `agent/blocked` 的停留时长、人工评论与接管来计算。
- **黄金评测集的来源与隔离**。**推荐**：先用自有仓库**已合并的历史任务**——冻结到修复之前的提交，用原 PR 带的测试做确定性判分，没有可判分测试的任务不入集；另配一个很小的模板仓库集，只用于验证 harness。评测集拆成**开发集**（迭代与提案可见）和**留出集**（只在最终门禁时使用，不进入任何 Agent 的记忆 / skill 来源）；评测运行锁定 skill 与记忆的版本，工作树里不含修复之后的历史。
- **评测成本预算**。**推荐**：每次评测配置**硬性预算上限**，触顶即停；日常用小样本，完整评测只在提案要生效前或按周执行；外发代码的隐私边界沿用多评审模型评测的"评审前先检查外发内容"。
- **评审模型的偏差与提示注入**。**推荐**：多评审器的原始评分分开保留（已有）；评审模型尽量与被测模型不同族以减轻自偏好；用 20–30 条人工标注样本校准评审一致性；评审结论视为信号而非真值，不得单独触发自动生效。
- **自进化的批准粒度**。**推荐**：首期逐条批准；放宽为批量批准要以数据为依据（有足够多"批准且未退化"的历史），并且只限不触碰安全边界的窄类提案。
- **自进化的回滚与审计**。**推荐**：版本粒度取"单个产物"（一个 skill、一份模板、一条路由规则），另保留"Agent 配置快照"记录整套版本，使每次评测运行都能标明它跑的是哪一套；产物放进 git 以自带历史。**回滚不需要重新评测**（回到已验证过的版本），**前滚才需要**。
- **自进化的数据来源**。**推荐**：首期只使用本仓库自己的失败、review 与 verifier 信号，**不跨仓库共享**；以后若要跨仓库，必须显式授权并先做脱敏。
- **路由依赖实测表现的门槛**。**推荐**：放在 M13 稳定、skill 晋升闭环验证过之后再做；只在人允许的集合内选择；样本不足一律保持现有人工预设行为；初始门槛建议每个（任务类型, Agent）至少 20 组配对样本，**该数字只是起点，待首轮试点测出方差后再定**。
- **M13 首个里程碑的规模**。**推荐**：只做 10–15 个任务，目标是**证明 harness 可靠**而不是比较模型——同一 Agent 对同一任务重复运行 3 次，度量结果方差；方差大到分不出差异时，先改进 harness 与判分，不扩大评测集。
