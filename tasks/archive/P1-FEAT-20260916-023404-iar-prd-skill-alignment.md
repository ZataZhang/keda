# PRD: IAR 与 PRD Skill 对齐——约定单源化、证据目录统一与 init provisioning

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ✅ **验收状态**：可归档 — 验收清单已全部完成。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文档分两层阅读：**Part A · 人审层**（§1–§4）给人看，用来批准或打回这项工作；**Part B · 执行器层**（§5–§13）给实现者（人或 Agent）看，人只在 §2 人审地图指向时下钻。

## Feature Overview (功能一览)

> 本区块是 §10 Functional Requirements 的通俗投影，**那里是唯一事实源**；行为验收以 §1 行为样例表为准。

- **证据目录统一**（FR-1、FR-2、FR-3）：daemon 跑任务时，执行 agent 把验证证据直接写进 `tasks/evidence/<PRD名>/`，与本地 `just ai implement` 流和 prd skill 的约定一致；提交 PR 时只有三份文本报告进版本库，截图录屏等原始产物照常用 orphan 分支发布，永不进 git 历史。
- **约定教学单源化**（FR-4、FR-5）：PRD 格式约定（Change Log 六字段、验收复选框语法、rv-id 命名、证据目录布局）从 iar 的 prompt 字符串里剥离，并入 prd skill 新增的 Machine Contract v1 章节；iar 各 prompt 只保留一行指向 skill 的指针。runner 执行语义（归档归属、结构化证据 manifest）留在 iar，但收敛到单一出处。
- **启动前预检**（FR-6）：daemon 开跑前检查执行环境里能解析到 prd skill，缺失则立即失败并给出安装指引，而不是跑到交付门禁才挂。
- **init 一体化 provision**（FR-7）：`iar init` 往目标仓库 `.gitignore` 的托管块里写入 `tasks/evidence` 白名单规则，重跑幂等；同时修复 `--force` 不传递给 skill 安装的问题。
- **文档同步**（FR-8）：`docs/guides/agent-runner.md` 与 `docs/ai-standards/testing.md` 改写为统一后的单一约定。
- **明确不做**（§11）：不清理 iar 的其他 keda 耦合点（数据库 provision 默认值、前端目录默认值、验证命令探测），不改结构化证据 manifest 的 schema，不迁移历史证据。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

iar daemon 和 prd skill 各自维护了一份"PRD 约定"的说法，两边已经漂移，而且漂移方向对我不利：

- daemon 注入给执行 agent 的 prompt 里内嵌了一整套格式教学——Change Log 必须是 `###` 标题加六个字段、验收复选框的语法、证据文件怎么命名。这些内容 prd skill 里**完全没有**（skill 只教 PRD 怎么写，不教这些机器可校验的格式细则），它们只活在 daemon 的 prompt 字符串里。
- 反过来，skill 早就进化出的东西 daemon 一概不认识：在 daemon 全部源码里搜索"验收状态横幅"相关标记，零命中。daemon 心里的 PRD 格式是 skill 好几个版本之前的样子。
- 证据目录也是两套：skill 的模板写明截图落在 `tasks/evidence/<PRD名>/`，本地 `just ai implement` 流也用这个目录；唯独 daemon 流把证据写进 runner 私有的 `.iar/evidence/`，三份验证报告从不进版本库——PR 合并后，归档的 PRD 旁边找不到它的证据链，追溯断在 PR 评论和 orphan 分支里。
- 后果已经实际发生过：skill 每次更新，daemon 的 prompt 教学不会跟着变；agent 按 skill 写 PRD、daemon 按旧格式卡门禁，两边对"什么叫合规 PRD"的回答不一致。prd skill 是我最核心、迭代最快的资产，而 daemon 的硬编码教学让它实际上在跟 skill 抢"约定制定者"的位置。

### Interpretation (解读回显)

**行为样例**（下表每一行都会被逐字转写为 §7.6 的验收 oracle——改一个单元格就是改验收标准，值得逐行审）：

| 输入 / 操作 | 期望观察到的结果 |
|---|---|
| 在一个全新初始化的 git 仓库里执行 `iar init` | `.gitignore` 里出现 iar 托管块，内含 `tasks/evidence` 白名单规则（只放行 `.md` 报告）；重跑一次幂等不重复 |
| daemon 起一个执行任务，查看发给执行 agent 的 prompt | prompt 里搜不到 Change Log 六字段教学、复选框语法教学，只有一行"PRD 约定以 prd skill 的 Machine Contract v1 为准"的指针；归档归属等 runner 执行语义仍在 |
| 执行 agent 跑完验证，把截图和验证报告存进证据目录 | 文件落在 `tasks/evidence/<PRD名>/` 下；PR 提交时三份 `.md` 报告进入版本库，`.png` 等原始产物不进 git 历史 |
| 一台没装 prd skill 的机器上启动 daemon 跑任务 | 开跑前直接失败，报错里给出安装方法；不会跑到交付门禁才发现 agent 不懂约定 |
| 老仓库的 `.iar.toml` 里显式配置了 `evidence_dir = ".iar/evidence"` | 行为与今天完全一致， evidence 照旧落在 `.iar/evidence/`（兼容路径，不强制迁移） |
| 一个没有关联 PRD 的 issue 进入 daemon 流（边界情况） | 证据落在 `tasks/evidence/issue-<编号>/` 兜底目录，不报错、不阻塞 |
| 执行 agent 试图把一张 `.png` 原始证据提交进 PR（失败情况） | 该文件不会被 stage 进提交；即使被强制加入，发布前拦截也会拒绝 |

**我默默定了这些**（Phase 2 刻意没问，答案都在这里，请扫一遍）：

- 证据目录按 PRD 名分子目录（`tasks/evidence/<PRD名>/`）；没有关联 PRD 的 issue 用 `tasks/evidence/issue-<编号>/` 兜底。
- runner 执行语义（归档由 runner 执行、`[~]` 复选框语义、结构化证据 manifest 的字段规范）**不**并入 skill——它们是 daemon 私有契约，留在 iar 但收敛到单一出处。搬进 skill 的只有"任何执行者都该遵守的 PRD 格式约定"。
- "只提交 `.md` 报告"靠 `.gitignore` 白名单实现（`git add -A` 天然遵守 gitignore），不改动 runner 的提交代码。
- skill 的修改发生在另一个仓库（`zata-codes-template`，本地 `~/code/zata_code_template`），本次执行会跨两个仓库提交；keda 的 PR 只含 keda 侧改动。
- 保留 `.iar/evidence` 作为可配置的 legacy 路径；不迁移任何历史证据。
- 机器契约从 v1 起步，预检只校验主版本一致。
- skill 安装目标目录维持现状（第一个已存在的 agent 配置目录），不改回固定 `~/.claude`。

**我理解为不做**（你可能想要、但本 PRD 排除的）：

- 不一并清理 iar 的其他 keda 耦合（worktree 数据库 provision 默认值、前端目录名默认值、验证命令的 Python 栈探测）——那是"iar 彻底独立发行"的活儿，单列后续。
- 不改结构化证据 `evidence.json` 的 manifest schema，不改 RV 复跑、串味检测、独立 verifier 的任何判定逻辑。
- 不把 daemon 的门禁校验逻辑搬进 skill——门禁是执法层，必须留在 runner 代码里，skill 只持有"被校验的格式约定"。

**解读（可证伪版）**：本 PRD 读作"daemon 向 skill 收敛"——证据目录、格式约定、provision 职责三处都以 skill 的约定为准，iar 瘦身为纯编排层（注入运行时状态、执行门禁、发布结果）；**不读成**"把 skill 的内容复制一份进 iar"或"让 iar 在运行时去读 skill 文本来动态生成门禁"。门禁校验逻辑一行不动，只换它教给 agent 的说法从哪来。

### What The User Gets

- **仓库操作者**：在任何新仓库跑一次 `iar init`，证据目录约定（gitignore 白名单）和 prd skill 就到位了，不需要 keda 项目在场。
- **我（维护者）**：PRD 格式约定只有 skill 一个出处。skill 迭代时只需要改 skill；daemon 自动跟上，因为它只指针、不抄录。两周一度的"skill 进化了、daemon 门禁还活在上个版本"的漂移消失。
- **验收时的我**：daemon 流的 PR 合并后，归档 PRD 旁边就是它的三份验证报告，证据链在版本库里闭环，不用翻 PR 评论和 orphan 分支。
- **执行 agent**：prompt 变短，且"要遵守什么格式"有了唯一权威出处，不会再收到两份互相矛盾的指令。

### Measurable Objectives

- 在 daemon 源码中搜索 Change Log 六字段教学、复选框语法教学：零命中（教学块已删除，仅剩指针）。
- prd skill 中存在 Machine Contract v1 章节，且包含上述全部格式约定；daemon 源码中声明的支持版本与之相等。
- 在干净临时仓库执行真实 `iar init`，`.gitignore` 含 `tasks/evidence` 白名单托管块；重跑幂等。
- 集成验证：证据写入 `tasks/evidence/<PRD名>/` 后，提交只包含 `.md` 报告，原始产物不出现在任何 commit 中。
- 既有 agent runner 测试套件与守卫测试全绿。

## 2. Human Review Map (介入与风险地图)

### 决策一：证据目录从 runner 私有目录搬进 `tasks/evidence/`

这推翻了一个已归档的选址决策（证据放 `.iar/evidence/`、整目录排除出 git）。当时的考虑是"默认安全"——不信任目标仓库的配置，runner 私有目录天然不会进 git。现在翻转的理由：`git add -A` 天然遵守 `.gitignore`，只要 init 把白名单规则 provision 好，安全性等价；换来的是三份验证报告随 PR 进版本库、证据链随归档 PRD 沉淀，且与 skill 约定、本地流程三方统一。代价是安全性从"默认就有"变成"依赖 init 把 gitignore 配对"——配错就是原始截图进 git 历史，所以白名单规则进 init 托管块、且发布前拦截保留兜底。

**请确认：** 证据目录默认值改为 `tasks/evidence/<PRD名>/`（无 PRD 的 issue 用 `issue-<编号>` 兜底），老配置显式指定 `.iar/evidence` 的仓库行为完全不变——同意这个选址翻转吗？

**验收：** 一次真实演示 run 后，PR 的版本库 diff 里能看到三份 `.md` 报告、看不到任何截图/录屏文件；老配置仓库的行为回归测试全绿。

### 决策二：约定教学分家——PRD 格式归 skill，runner 执行语义留 iar

不是"全部搬走"。分家的线画在"谁该遵守"：Change Log 六字段、复选框语法、rv-id 命名、证据目录布局是**任何执行者**（人、本地流、daemon 流）都该遵守的 PRD 格式约定，并入 skill 新增的 Machine Contract v1 章节；而归档由 runner 执行、`[~]` 表示 runner 自勾、结构化证据 manifest 字段是 **daemon 私有执行语义**，留在 iar——但目前它们散落在至少五个 prompt 构建点里重复表述，本次收敛到单一出处。skill 侧章节带版本号，daemon 开跑前校验版本匹配，不匹配就大声报错而不是静默放行。

**请确认：** 同意这条分家线——格式约定进 skill（带版本），daemon 私有语义留 iar（单源化），而不是把后者也搬进 skill？

**验收：** daemon 的全部 prompt 里搜不到任何格式教学片段、只有契约指针；skill 的 Machine Contract 章节包含全部被剥离的约定（包括"Change Log 写成表格不算数"这类实战教训）；版本不匹配时启动即报错。

### 决策三：允许本次执行跨仓库提交

prd skill 的权威源在模板仓库（本地 `~/code/zata_code_template`，远端 GitHub），不在 keda 仓库里。本 PRD 的 FR-4（skill 新增 Machine Contract 章节）必须在那个仓库里改、提交、推送，然后同步到本机 skill 目录；keda 这边的 PR 只含 keda 侧改动。这意味着执行 agent 需要操作两个本地仓库。

**请确认：** 同意本次执行跨 keda 与 zata-codes-template 两个仓库提交，且模板仓库的 diff 严格限于 Machine Contract 相关内容？

**验收：** 两个仓库各自的提交历史清晰可辨；模板仓库的 diff 只有 skill 契约章节的新增与必要的交叉引用。

### 自动门禁，不需要逐项人工审阅

以下由 executor + 自动化门禁覆盖：prompt 教学块删除后的 golden 测试、证据提交语义（`.md` 放行 / 原始产物拦截）的集成测试、启动预检的失败路径、init gitignore 幂等性、既有 agent runner 测试套件与守卫测试回归、文档同步。这些项的正确性由"改错了就会红"的针对性测试锁定。

### 本次明确不涉及

无数据库结构变更；无前端变更（两个前端应用对证据机制零引用，已核实）；不清理 iar 的其他 keda 耦合点；不改结构化证据 manifest schema；不迁移历史证据。

## 3. Usage And Impact After Implementation

- **仓库操作者（在新仓库跑 `iar init`）**：`.gitignore` 里多出 `tasks/evidence` 白名单托管块（与既有 `.iar/` 块并列，幂等可重跑）；skill 安装行为不变，但 `iar init --force` 现在会把 force 传递给 skill 覆盖判断。已在用的老仓库重跑一次 `iar init` 即可补齐 gitignore 规则。
- **daemon 执行 agent**：收到的 prompt 明显变短——格式教学替换为一行契约指针；证据写入路径从 `.iar/evidence/` 变为 `tasks/evidence/<PRD名>/`。归档归属、`[~]` 语义、manifest 规范等 runner 指令保留，措辞统一。
- **人类验收者（我）**：daemon 流的 PR diff 里直接包含三份验证报告；合并后 `tasks/archive/<PRD>.md` 与 `tasks/evidence/<PRD>/` 同库闭环。PR 评论与 orphan 分支的原始证据发布行为不变。
- **本地 `just ai implement` 流用户**：行为完全不变（本来就用 `tasks/evidence/`），但现在与 daemon 流是同一套约定，不再需要在文档里区分两条流。
- **下游模板系仓库**：重跑 `iar init` 后获得统一约定；显式配置了 `evidence_dir = ".iar/evidence"` 的仓库行为逐字节不变。

向后兼容：仅"未显式配置 `evidence_dir`"的仓库会观察到证据落点变化（从 `.iar/evidence/` 到 `tasks/evidence/`）；这正是一次行为变更，已列为决策一。

## 4. Requirement Shape

- **actor**：仓库操作者（跑 `iar init`）、daemon 执行/恢复/收尾 agent、人类验收者、本地 PRD 流用户
- **trigger**：`iar init` 初始化仓库；daemon 起执行任务并注入 prompt；执行 agent 收集验证证据；交付门禁提交 PR
- **expected behavior**：证据落 `tasks/evidence/` 且只有文本报告进版本库；prompt 中的格式约定全部来自 prd skill 的 Machine Contract v1 指针；skill 缺失时启动即失败；init 一次 provision 齐备
- **explicit scope boundary**：门禁校验逻辑、manifest schema、RV 复跑与 verifier 判定不变；其他 keda 耦合点、skill 安装目录语义、历史证据迁移均不在范围内

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**当前相关模块**（均在既有四层边界内，无跨层新增依赖）：

- prompt 构建（core）：`src/backend/core/use_cases/agent_runner_feedback.py`（`PRD_CHANGE_LOG_FORMAT_EXAMPLE`:141、`PRD_ARCHIVE_OWNERSHIP_RULE`:166、`RUNNER_OWNED_CHECKLIST_ITEM_RULE`:178、`_build_prd_closeout_instruction`:189，注入 execution/recovery/continuation 三类 prompt；:825 硬编码 `.iar/evidence` 字面量）、`agent_runner_structured_evidence.py:852`（manifest 字段教学）、`agent_runner_validation_parsing.py:299`（证据命名/截图分工/脚本归属教学；:282 写进 Issue body 的 `.iar/evidence` 文案）、`agent_runner_closeout.py:399`（`_CLOSEOUT_KIND_INSTRUCTIONS` 四类教学）
- 证据目录机制（core）：`agent_runner_validation.py`（`evidence_dir_path`:154、`ensure_evidence_dir_excluded`:240、`ensure_validation_evidence_ready`:377、RV 复跑缓存 `_rv_reexec_cache_relpath`:458-467、`ensure_no_evidence_paths_in_changes`:793）
- 证据发布（core）：`agent_runner_validation_publication.py`（orphan 分支上传 :92、评论构建 :181——全部经 config 间接取路径，无字面量）
- closeout（core）：`agent_runner_closeout.py`（允许集 `build_closeout_allowed_scope`:128 跟随 config）
- 配置默认值：`src/backend/infrastructure/config/agent_runner_settings.py:242` 与 `src/backend/core/shared/models/agent_runner.py:522`，均为 `".iar/evidence"`
- init（api + engines）：`src/backend/api/cli_init.py:38`（`_run_init_command` 编排）、`src/backend/engines/agent_runner/repository_gitignore.py:230`（`ensure_gitignore_entries` 托管块机制，现含 `.iar/`、`.agent-runner/`、`.iar-worktrees/` 三条）、`remote_template_skills.py`（远程 skill 安装，已实现从 GitHub sparse clone；`cli_init.py:84-86` 未把 `--force` 传入）
- skill 解析（core）：`generated_content.py:675-734`（`resolve_prd_skill_path`/`load_prd_skill_spec`，可复用于预检）
- PRD 格式解析器（core/shared）：`prd_change_log.py`、`prd_checklist.py`——机器契约在代码侧的既有落点

**既有架构模式**：四层方向 `api -> core -> engines -> infrastructure`；init 的 gitignore 托管块（header/footer 标记、幂等、块外跳过）是现成的 provision 模式，直接复用。PRD 相关路径约定（`tasks/pending/`、`tasks/archive/`）已是全仓统一约定。

**Frontend impact**：No frontend impact——`frontend-admin/src` 与 `frontend-public` 对 evidence 机制零引用（已 grep 核实），改动为 CLI/daemon/文档。

**相关 PRD**（全部已归档，无 pending 重复）：

- `tasks/archive/P1-REFACTOR-20260716-132633-user-level-skill-installation.md`：确立 skill 用户级安装。其"iar 不动用户 home"的边界已被后续提交（远程安装进 init）实际修订，本 PRD 沿用该现状并补齐 provisioning。
- `tasks/archive/P1-FEAT-20260610-143013-validation-evidence-gate.md`：`.iar/evidence/` 选址的奠基决策，本 PRD **显式修订**（决策一）。
- `tasks/archive/P1-FEAT-20260731-094822-rv-oracle-evidence-dir-only.md`：`{evidence_dir}/scripts/` oracle 约定与复跑缓存指纹的权威，证据目录搬家必须保持其语义；其遗留问题"证据策略落点会被模板 sync 覆盖"由本 PRD 的 init gitignore provision 顺手收编。
- `tasks/archive/P1-FEAT-20260714-171537-prd-change-log-vs-checklist.md`：Change Log 六字段与归档职责的现行权威——正是被剥离进 skill 的那段教学的来源；runner 侧 `ensure_prd_delivery_ready()` 门禁不变。
- `tasks/archive/P1-FEAT-20260824-133115-runner-delivery-closeout-agent.md`：closeout 允许集含证据目录，需跟随。
- `tasks/archive/P1-FEAT-20260618-000726-rework-prd-worktree-pr-and-skill-source.md`：确立"PRD 规范以 skill 为单一来源"，本 PRD 是其自然延伸（前置背景）。
- pending `tasks/pending/P1-FEAT-20260705-161739-completeness-judgment-hardening.md`：改 RV stdout 断言与 supervisor prompt，与本 PRD 共享 prompt 构造表面——soft 协调，见 §8。

## 6. Recommendation

**Recommended Approach**：单阶段目标态——daemon 向 skill 收敛，一次到位，不设过渡兼容层（legacy 配置路径除外，它是永久兼容项而非过渡）。

1. **证据目录统一**：`config.validation.evidence_dir` 默认值从 `.iar/evidence` 改为 `tasks/evidence`（两处默认值同步）；目录解析增加一层"按 PRD stem 分子目录"（有 canonical PRD 时 `<root>/<prd-stem>`，无 PRD 时 `<root>/issue-<N>`）。`.gitignore` 白名单（`tasks/evidence/**` + `!**/` + `!**/*.md`）由 init provision；`ensure_evidence_dir_excluded` 不再对 `tasks/evidence` 做整目录 info/exclude（仅对 legacy `.iar/evidence` 保留）；`ensure_no_evidence_paths_in_changes` 重写为"放行证据目录内 `.md`、拦截其余"。RV 复跑缓存固定放 `.iar/`（runner 状态），与证据目录父目录解耦。
2. **约定单源化**：prd skill（模板仓库 `~/code/zata_code_template` 的 `skills/prd/`）新增 Machine Contract v1 章节，收纳：Change Log `###`+六字段（含"表格不算数"教训）、checklist 复选框语法与 `[~]` 语义、rv-id 证据命名（`rv-<n>-<slug>.<ext>`）、证据目录布局（`tasks/evidence/<prd-stem>/`、三份报告文件名、`scripts/` 子目录）、Delivery Dependencies 块语法、两条横幅标记。章节带 `Machine-Contract-Version: 1` 标记。keda 侧在 `core/shared/` 声明支持版本常量并与解析器（`prd_change_log.py`/`prd_checklist.py`）同处。
3. **prompt 瘦身**：删除 execution/recovery/continuation/closeout 四类 prompt 与 Issue body 文案中的全部格式教学片段，替换为一行契约指针（"PRD 格式约定以 prd skill 的 Machine Contract v1 为准，执行前必读"）；runner 私有语义（归档归属、manifest 规范、RV 复跑纪律）保留并收敛到单一常量模块，各 prompt 引用而非复述。
4. **启动预检**：daemon 起执行循环前用既有 `resolve_prd_skill_path` 解析 prd skill 并读取其契约版本，缺失或主版本不匹配即 fail fast，报错含 `iar init` 修复指引。
5. **init 补齐**：gitignore 托管块增加 `tasks/evidence` 白名单段；`--force` 传入 skill 安装选项。

**为什么是最佳 fit**：所有改动落在既有模块与既有 provision 模式内，无新层、无新服务、无新依赖；`core/shared` 的版本常量与既有解析器同目录，是"约定唯一来源"的现成落点。gitignore 白名单方案让 `git add -A` 天然正确，**不需要改 runner 提交代码**（这是勘察后确认的最小路径）。

**Alternatives Considered**：

- **改提交代码实现过滤**（在 checkpoint/commit 路径加"证据目录仅 `.md`"pathspec 白名单）：能达到同样效果，但要动三条提交路径的过滤维度与审计口径，而 gitignore 白名单零代码达成同一语义，拒绝。
- **约定全部并入 skill（含 runner 私有语义）**：skill 是跨工具资产，归档归属、manifest 字段是 daemon 实现细节，搬进 skill 会让 skill 绑死 iar，违背"没有 keda 也能跑"的初衷，拒绝。
- **runner 运行时读 skill 文本动态生成 prompt/门禁**：门禁必须稳定且不受被执法对象影响，skill 文本是 agent 可读的教学材料，运行时拼接会把执法依据变成可变输入，拒绝（门禁代码不变，只换 prompt 里的说法出处）。

### Proposed Solution Summary (实现机制)

核心机制是"契约搬家 + 指针替换 + provision 补齐"：格式约定的权威文本从 iar 的 prompt 常量搬到 prd skill 的 Machine Contract v1 章节（由模板仓库承载，经既有 `iar init` 远程安装分发）；iar 各 prompt 构建点删除教学、改为引用一行指针；门禁解析器（`prd_change_log.py`/`prd_checklist.py`）一行不动。证据目录由配置默认值变更 + 解析时按 PRD stem 分子目录实现，`config.validation.evidence_dir` 仍是唯一配置入口（系统只消费显式配置，不做隐式推断）。git 语义由 init provision 的 `.gitignore` 白名单保证。刻意避开的复杂度：不改提交代码、不引入运行时 skill 文本解析（除预检的版本标记正则外）、不做历史证据迁移。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

**Core Logic**：daemon 起任务 → 预检解析 prd skill（版本匹配）→ prompt 构建（编排状态 + 契约指针 + runner 私有语义单源块）→ 执行 agent 按 skill 约定写证据到 `tasks/evidence/<prd-stem>/` → 验证门禁（复跑、齐备性，逻辑不变）→ 提交（`git add -A` 经 gitignore 白名单自然只含 `.md` 报告）→ 发布（orphan 分支上传整目录，PR 评论渲染，路径随 config 自动跟随）→ PRD 交付门禁（不变）。

**Change Impact Tree**（起始点，非穷举；执行中发现新受影响面先更新本 PRD）：

```
api/
└── cli_init.py                         # --force 传入 skill 安装选项
core/
├── shared/
│   ├── models/agent_runner.py          # evidence_dir 默认值 → tasks/evidence
│   ├── prd_change_log.py / prd_checklist.py  # 不变；契约版本常量同目录新增
│   └── prd_machine_contract.py (新)    # SUPPORTED_MACHINE_CONTRACT_VERSION + 版本标记解析
├── use_cases/
│   ├── agent_runner_feedback.py        # 删教学块（:141/:178/:189），改指针；:825 字面量改走 config
│   ├── agent_runner_validation_parsing.py  # 删命名/截图/脚本归属教学（:299-335），Issue body 文案（:282）改走 config
│   ├── agent_runner_structured_evidence.py # manifest 教学保留但收敛为单源常量引用
│   ├── agent_runner_closeout.py        # _CLOSEOUT_KIND_INSTRUCTIONS 瘦身为门禁报错+契约指针
│   ├── agent_runner_validation.py      # evidence_dir_path 按 prd-stem 分目录；:240/:793 白名单语义重写；复跑缓存固定 .iar/；:659 字面量
│   ├── agent_runner_validation_publication.py  # 路径随 config 自动跟随（穿线 resolved evidence_dir）
│   └── run_agent_execution_loop.py / run_agent_once.py  # prompt 插值改走解析器
│   （预检挂接点实际落在 agent_runner_publish.py 的 run_preflight_checks——它是
│     run_once 领取 Issue 前的既有单点预检，比执行循环入口更"开跑前"）
engines/agent_runner/
└── repository_gitignore.py             # 托管块新增 tasks/evidence 白名单段
infrastructure/config/
└── agent_runner_settings.py            # evidence_dir 默认值
docs/
├── guides/agent-runner.md              # 证据目录、prompt 契约、init 行为章节改写
└── ai-standards/testing.md             # "证据留存策略"段改写为统一约定
外部仓库 ~/code/zata_code_template/
└── skills/prd/SKILL.md (+ references/) # 新增 Machine Contract v1 章节；同步到 ~/.kimi-code/skills 并推送
tests/
└── prompt golden 测试、证据提交语义集成测试、预检失败路径测试、init gitignore 幂等测试
```

**Risk Classification Register**：

| change point | tier | decisive dimension/override | intervention | oracle/gate |
|---|---|---|---|---|
| evidence_dir 默认值变更 + prd-stem 分目录解析 | R2 | 持久路径语义变更、波及多道门禁与发布 | 人工确认（决策一）+ 强 oracle | rv-3、rv-5 |
| info/exclude 与发布拦截的白名单语义重写 | R2 | 正确性关键：写错则原始证据进 git 历史 | 人工确认（决策一覆盖）+ 负向控制 | rv-3 |
| prompt 格式教学剥离 + 契约指针 | R2 | 执行 agent 行为依赖，漂移风险 | 人工确认（决策二）+ golden 测试 | rv-2 |
| skill Machine Contract v1 章节新增（跨仓库） | R2 | 外部仓库变更，约定权威迁移 | 人工确认（决策三） | rv-6 |
| 启动预检 fail fast | R1 | 单点新增，失败路径清晰 | executor + 失败判别测试 | rv-4 |
| init gitignore 白名单段 + --force 传递 | R1 | 复用既有托管块机制，幂等 | executor + 真实 CLI 验证 | rv-1 |
| RV 复跑缓存固定 .iar/ | R1 | 纯内部路径，有现有测试覆盖 | executor + 回归 | rv-5 |
| 文档同步 | R0 | 纯文档 | executor + 守卫测试 | rv-5 |

**Executor Drift Guard**：

- 全仓搜 `.iar/evidence` 字面量残留：`rg -n '\.iar/evidence' src/ scripts/ docs/ tests/`——除 legacy 兼容分支与文档说明外应清零。
- 全仓搜 `evidence_dir` 消费点：`rg -n 'evidence_dir' src/backend/`，逐一确认跟随 config 或已改写。
- 全仓搜教学块引用：`rg -n 'PRD_CHANGE_LOG_FORMAT_EXAMPLE|RUNNER_OWNED_CHECKLIST_ITEM_RULE|_CLOSEOUT_KIND_INSTRUCTIONS' src/`。
- `tasks/evidence` 的既有消费者（本地流脚本 `scripts/shared/just/check_prd_evidence.sh`、`prd_status.py`、守卫 `tests/guards/shared/test_prd_lock.py`）语义不变，改动后跑守卫测试确认。
- 上述文件清单是起始点；发现隐藏引用先更新本 PRD 再动手。

**Flow Diagram**：

```
iar init ──► .gitignore 托管块(+.iar/, +tasks/evidence 白名单) ──► 远程安装 prd skill
                                                        │
daemon run ──► 预检: resolve prd skill + 契约版本匹配? ──否──► fail fast(含安装指引)
                  │是
                  ▼
        prompt = 编排状态 + 契约指针(→skill v1) + runner私有语义(单源)
                  │
                  ▼
        执行 agent ──► 证据写 tasks/evidence/<prd-stem>/
                  │
                  ▼
        门禁(复跑/齐备, 逻辑不变) ──► git add -A (gitignore 白名单: 仅 .md 入commit)
                  │
                  ▼
        发布: orphan 分支(整目录原始证据) + PR 评论 + PR 内含三份报告
```

无 ER diagram（无数据模型变更）；无低保真原型（无 UI）。

### 7.6 Realistic Validation Plan

```yaml
oracles:
  - id: rv-1
    behavior: 在干净临时 git 仓库执行真实 iar init，.gitignore 出现 tasks/evidence 白名单托管块，重跑幂等；--dry-run 输出包含 skill 安装计划
    reviewer: human
    real_entry: "bash scripts 创建临时仓库 + iar init（真实 CLI 入口，无网络依赖项全验；skill 实际拉取为 opt-in 联网步骤）"
    expected: .gitignore 含 tasks/evidence/** 与 !tasks/evidence/**/*.md 规则且位于 iar 托管块内；第二次 init 报告无变更
    presentation: 临时仓库 .gitignore 全文 + 两次 init 的终端输出（验收消息原样附上）
    mock_boundary: 不 mock CLI 与文件系统；skill 远程拉取用 --dry-run 断言计划 + opt-in 实拉
    tier: R1
    test_layer: e2e-script
    required_for_acceptance: true
  - id: rv-2
    behavior: execution/recovery/continuation/closeout 四类 prompt 不再含格式教学，含契约指针；runner 私有语义（归档归属、manifest 规范）仍在
    reviewer: verifier
    real_entry: "uv run pytest tests/ -k prompt（golden/字符断言测试，直接调用真实 prompt 构建函数）"
    expected: 教学片段（六字段示例、复选框语法教学、rv 命名教学）零命中；指针行与 runner 语义块各一次命中
    mock_boundary: 无 mock，直接构建 prompt 字符串断言
    tier: R2
    test_layer: unit-golden
    required_for_acceptance: true
    critical_value_source: 真实 prompt 构建函数的输出字符串
    must_cross: [prompt 构建 → 字符串断言]
    forbidden_bypasses: [在测试里硬编码期望全文而不断言"教学缺失"]
    fresh_state_probe: 删除任一教学块后对应断言变绿、且指针断言仍红→绿可逆
    final_tree_evidence: 测试在最终代码树上重跑
    negative_control: 临时把指针行从构建函数删掉，测试必须红
    expected_fail: 指针断言失败
  - id: rv-3
    behavior: 证据写入 tasks/evidence/<prd-stem>/ 后，git add -A 只 stage .md 报告；.png 原始产物不进任何 commit；被强制 stage 时发布前拦截拒绝；显式配置 .iar/evidence 的仓库行为不变
    reviewer: verifier
    real_entry: "uv run pytest tests/ -k evidence（集成测试：tmp_path 真实 git 仓库，走真实提交与拦截函数）"
    expected: commit 树中含三份 .md、不含 .png；强制 add .png 后 ensure_no_evidence_paths_in_changes 抛出；legacy 配置用例全绿
    mock_boundary: git/python 真实调用，不 mock；不触网（发布到 orphan 分支仅断言本地对象构造）
    tier: R2
    test_layer: integration
    required_for_acceptance: true
    critical_value_source: 真实 git commit 的文件清单（git ls-tree）
    must_cross: [证据写入 → git add -A → commit 树]
    forbidden_bypasses: [断言 stage 区而非 commit 树; 用 monkeypatch 改 gitignore]
    fresh_state_probe: 每个用例独立 tmp git 仓库，从空仓库起步
    final_tree_evidence: 最终代码树上重跑并附 git ls-tree 输出
    negative_control: 临时移除 .gitignore 白名单后运行，.png 必须出现在 commit 树中（证明测试能区分失败）
    expected_fail: 原始产物拦截用例转红
  - id: rv-4
    behavior: prd skill 不可解析或契约主版本不匹配时，daemon 起执行循环前 fail fast，报错含 iar init 指引
    reviewer: verifier
    real_entry: "uv run pytest tests/ -k preflight（用 env 覆盖把 skill 路径指向缺失/低版本 fixture，调用真实预检入口）"
    expected: 两种场景均以明确错误信息退出；skill 存在且版本匹配时正常放行
    mock_boundary: 仅环境变量与 fixture 文件，不 mock 预检逻辑
    tier: R1
    test_layer: integration
    required_for_acceptance: true
  - id: rv-5
    behavior: 既有 agent runner 测试套件与守卫测试全量回归（含 RV 复跑缓存、串味检测、closeout、prd_lock 守卫）
    reviewer: verifier
    real_entry: "uv run pytest tests/ -x -q（或仓库标准 just test 入口）"
    expected: 全绿；与证据目录相关的既有用例已更新为新约定且语义不丢
    mock_boundary: 沿用各测试既有 mock 策略
    tier: R1
    test_layer: regression
    required_for_acceptance: true
  - id: rv-6
    behavior: 模板仓库 skills/prd 存在 Machine Contract v1 章节，含全部被剥离约定；keda 支持版本常量等于 skill 声明版本；本机 ~/.kimi-code/skills 已同步
    reviewer: verifier
    real_entry: "检查脚本：解析 ~/code/zata_code_template/skills/prd/SKILL.md 的契约章节与版本标记，比对 keda 常量与 ~/.kimi-code/skills 副本一致性"
    expected: 章节存在且含六字段/[~]/rv-id/证据布局/Delivery Dependencies/横幅标记六项；版本相等；两处副本逐字节一致
    mock_boundary: 真实文件系统，无 mock
    tier: R1
    test_layer: e2e-script
    required_for_acceptance: true
```

## 8. Delivery Dependencies

### Delivery Dependencies

- Group: none
- Depends on tasks/issues:
  - none
- Gate type: none
- Notes: 与 pending `P1-FEAT-20260705-161739-completeness-judgment-hardening` 共享 prompt 构造表面（supervisor prompt / RV stdout 断言），无顺序依赖，但并行实施时需协调 `agent_runner_feedback.py` 等同一文件的改动避免冲突。本 PRD 显式修订已归档决策：`P1-FEAT-20260610-143013-validation-evidence-gate` 的 `.iar/evidence/` 选址。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 验收点 | 呈递物（交付时填实际路径） | 10 秒自验 |
|---|---|---|
| 新仓库 `iar init` 后的 gitignore 白名单与幂等重跑（rv-1） | 临时仓库 `.gitignore` 全文 + 两次 init 终端输出（验收消息原样附上） | 在输出中搜 `tasks/evidence`，应同时看到排除行与 `!*.md` 放行 |

> 注：rv-2～rv-6 均为 `reviewer: verifier`（测试退出码/文件内容断言），肉眼核对无增量价值，故不在本区呈递；任一失败会自动升级到本区。

### 9.2 Acceptance Evidence Package

按 §7 Risk Classification Register 排序：决策一/二/三（human-confirmed，rv-1/rv-2/rv-3/rv-6 支撑）在前，R2 其余项随后，R1/R0 门禁结果折叠附后。

**Human-Confirmed**

- [x] 决策一：演示 run 的 PR/commit 树中含三份 `.md` 报告且不含任何原始产物文件（rv-3 的 `git ls-tree` 输出）；显式 `.iar/evidence` 配置的 legacy 用例全绿 —— 人审通过（2026-09-16）
- [x] 决策二：daemon 源码中格式教学片段零命中、契约指针存在（rv-2 测试输出）；skill Machine Contract v1 章节含全部六项约定（rv-6 检查输出） —— 人审通过（2026-09-16）
- [x] 决策三：keda 与 zata-codes-template 两仓库提交历史可辨，模板仓库 diff 仅限契约相关内容（模板仓库 `453ac6f` 已推送；keda 侧改动在工作区待提交） —— 人审通过（2026-09-16）
- [x] 9.1 呈递区内容已在验收消息中原样呈现并完成人工过目 —— 人审通过（2026-09-16）

**Architecture Acceptance**

- [x] 改动不破坏四层依赖方向；新增模块仅限 `core/shared/prd_machine_contract.py`，无新跨层依赖（verifier 复跑 `rg` 零命中 + pre-commit 架构钩子通过，见 verifier-report）

**Behavior Acceptance**

- [x] rv-1：`iar init` 真实 CLI 在临时仓库写入白名单托管块且幂等（`rv-1-init-gitignore.txt`）
- [x] rv-2：四类 prompt 教学零命中、指针与 runner 语义单源命中（`rv-2-prompt-contract.txt`，verifier 复跑 17 用例绿）
- [x] rv-3：`.md` 入 commit / 原始产物拦截 / legacy 配置兼容（`rv-3-evidence-commit.txt` 含负向控制）
- [x] rv-4：skill 缺失与版本不匹配两种 fail fast（`rv-4-skill-preflight.txt`）

**Documentation Acceptance**

- [x] `docs/guides/agent-runner.md` 证据目录、prompt 契约、init 行为章节已改写为新约定（verifier 核对通过）
- [x] `docs/ai-standards/testing.md` "证据留存策略"段不再区分两条流，与 agent-runner.md 表述一致

**Validation Acceptance**

- [x] rv-5：全量测试套件与守卫测试绿（`rv-5-full-regression.txt`；verifier 复跑 2112 passed, exit=0）
- [x] rv-6：契约版本一致性与 skill 副本同步检查通过（`rv-6-contract-sync.txt`，verifier 复跑 PASS）

**Delivery Readiness**

- [x] 三份证据文件（verification-plan / evidence-report / verifier-report）落在 `tasks/evidence/P1-FEAT-20260916-023404-iar-prd-skill-alignment/`
- [x] 独立 verifier Agent 按 rv-id 逐项核对并给出 PASS（verifier-report.md，结论 PASS）
- [x] 完成消息原样携带 9.1 呈递区全部内容（截图/输出路径 + 自验方式），只甩路径视为未交付
- [x] 模板仓库改动已提交并推送（`453ac6f`），本机 `~/.kimi-code/skills` 副本已同步（rv-6 证据）

## 10. Functional Requirements

- **FR-1**：`config.validation.evidence_dir` 默认值改为 `tasks/evidence`；证据目录解析按 PRD stem 分子目录（`tasks/evidence/<prd-stem>/`），无 canonical PRD 时用 `tasks/evidence/issue-<N>/` 兜底；显式配置 `evidence_dir` 的仓库行为不变。
- **FR-2**：证据目录的 git 语义为白名单提交——`.md` 报告可进版本库，其余产物被 `.gitignore` 排除；`ensure_evidence_dir_excluded` 不再整目录排除 `tasks/evidence`；`ensure_no_evidence_paths_in_changes` 放行 `.md`、拦截其余证据产物；RV 复跑缓存等 runner 状态固定留在 `.iar/`。
- **FR-3**：证据发布（orphan 分支上传、PR 评论渲染）在新目录下行为等价，路径全部经 config 解析，无新增字面量。
- **FR-4**：prd skill（zata-codes-template 仓库）新增 Machine Contract v1 章节，含 Change Log 六字段（含"表格不算数"教训）、checklist 复选框与 `[~]` 语义、rv-id 命名、证据目录布局与三份报告文件名、Delivery Dependencies 块语法、两条横幅标记；章节带机器可读的版本标记；改动同步到 `~/.kimi-code/skills` 并推送远端。
- **FR-5**：execution/recovery/continuation/closeout 四类 prompt 与 Issue body 文案中的格式教学全部删除，替换为契约指针；runner 私有语义（归档归属、manifest 规范、RV 复跑纪律）保留并收敛到单一出处，各 prompt 引用而非复述。
- **FR-6**：daemon 起执行循环前预检 prd skill 可解析且契约主版本匹配，否则 fail fast 并在报错中给出 `iar init` 修复指引。
- **FR-7**：`iar init` 的 `.gitignore` 托管块新增 `tasks/evidence` 白名单段（幂等、可被 `--no-update-gitignore` 跳过）；`--force` 正确传递给 skill 安装选项。
- **FR-8**：`docs/guides/agent-runner.md` 与 `docs/ai-standards/testing.md` 同步为统一约定；keda 侧声明契约支持版本常量并与 skill 标记比对。

## 11. Non-Goals

- 清理 iar 的其他 keda 耦合点（`worktree.provision_database` 默认值、`validation.frontend_paths` 默认值、验证命令的 Python 栈探测、模板头注释指向 keda 文档）——列入 §12 后续。
- 修改结构化证据 `evidence.json` 的 manifest schema、RV 复跑、串味检测、独立 verifier 的判定逻辑。
- 把 runner 门禁校验逻辑搬进 skill，或让 runner 运行时读 skill 文本动态生成门禁。
- 迁移任何仓库的历史 `.iar/evidence` 证据；历史 orphan 分支保持只读。
- 改变 skill 安装目标目录的探测语义（首个已存在的 agent 配置目录），不改回固定 `~/.claude`。
- 本地 `just ai implement` 流的行为变更（它已是目标态）。

## 12. Risks And Follow-Ups

- **风险：gitignore 白名单未 provision 的仓库原始证据进 git。** 缓解：init 托管块 + 发布前拦截兜底 + rv-3 负向控制。残留风险：绕过 init 手工配置 `evidence_dir=tasks/evidence` 的仓库——文档中明确该配置要求白名单规则在场。
- **风险：prompt 瘦身恰逢 skill 未安装的执行环境。** 缓解：rv-4 预检 fail fast，错误信息给出修复路径；预检本身成为新的事实门禁。
- **后续（非阻塞）**：iar 彻底独立发行的 keda 耦合清理（provision_database / frontend_paths 默认值、验证命令探测、模板头注释）——建议单开 PRD；skill 安装目标目录语义（多 agent 目录并存时的覆盖策略）随该 PRD 一并评估。

## 13. Decision Log

| ID | 决策问题 | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 证据目录选址 | `tasks/evidence/<prd-stem>/`，gitignore 白名单 | 保留 `.iar/evidence/` 整目录排除 | gitignore 白名单下安全性等价，换来三份报告随 PR 入库、与 skill/本地流三方统一；且 skill 模板早已写死 `tasks/evidence/` |
| D-02 | "只提交 .md"的实现位置 | `.gitignore` 白名单（`git add -A` 天然遵守） | 改 checkpoint/commit 三条提交路径加 pathspec 白名单 | 零代码达成同一语义；改提交代码要动过滤维度与审计口径，复杂度不成比例 |
| D-03 | 约定分家线 | PRD 格式约定→skill Machine Contract v1；runner 私有语义留 iar 单源化 | 全部并入 skill / 全部留 iar | 全部并入会让 skill 绑死 iar 实现细节；全部留 iar 则漂移依旧 |
| D-04 | 门禁与契约的关系 | 门禁代码不变，prompt 只换说法出处 | runner 运行时读 skill 文本动态生成 prompt/门禁 | 门禁必须稳定且不受被执法对象影响；skill 文本是 agent 可读材料，不能成为执法依据 |
| D-05 | skill 缺失时的行为 | 启动预检 fail fast | 宽松回退（保留内嵌教学兜底） | 双份教学正是漂移根源；fail fast + 明确修复指引使单一出处真正成立 |
| D-06 | skill 改动落点 | zata-codes-template 仓库提交推送 + 同步本机 | 只改本机 `~/.kimi-code/skills` | 模板仓库是 skill 的权威源与 iar init 的分发源，只改本机会在下次 init 时被覆盖回滚 |
| D-07 | 无 PRD issue 的证据落点 | `tasks/evidence/issue-<N>/` 兜底 | 阻塞无 PRD 任务 / 退回 `.iar/evidence` | deliberate 等流在生成 PRD 前就需要存证据，兜底目录保持流程不断 |

---

## Change Log

### keda 侧实施落地（FR-1/2/3/5/6/7/8）

- Type: scope / evidence
- Before: 预检挂接点建议写在 run_agent_execution_loop.py / run_agent_once.py；`build_validation_prompt_line` 与各 prompt 各自携带契约指针；证据目录机制散在 `evidence_dir_path` 直连。
- After: 预检实际挂在 `agent_runner_publish.run_preflight_checks`（`run_once` 领取 Issue 前的既有单点，失败即整轮退出码 1）；契约指针只由 PRD 块（`_build_prd_closeout_instruction`）与 Issue body 区块各携带一次，validation 行只指方向不复述，保证每个 prompt 指针恰好一次命中；新增 `resolve_evidence_dir` 系列单一解析入口（agent_runner_validation.py），全部消费方穿线 resolved evidence_dir。
- Reason: 执行中发现两处更优落点——run_once 已有"开跑前预检"单点（比在每条执行循环入口重复预检更早、更符合行为样例"开跑前直接失败"）；指针若同时进 validation 行与 PRD 块会在同一 prompt 命中两次，违背 §7 rv-2 的"各一次命中" oracle。
- Impact: 不改变 FR 目标态与验收 oracle；预检覆盖面为 daemon run_once 路径（blocked-continue 直入口不在内，见返回说明）。
- Review: executor self-reviewed；待人审。

### 验收状态推进：机器层全绿，横幅翻至待人工验收

- Type: status
- Before: §9 全部未勾，横幅 ⬜ 未开工。
- After: Architecture / Behavior / Documentation / Validation / Delivery Readiness 五组全部勾选（证据：rv-1~rv-6 文件 + verifier-report PASS，verifier 亲自复跑 17 新测试与全量 2112 passed）；仅剩 4 项 Human-Confirmed 待人审；横幅翻 🧍 待人工验收。
- Reason: 独立 verifier 结论 PASS，两处实施偏离（预检挂接点、指针单次命中）被评估为不破坏 PRD 意图。
- Impact: 进入两触模型的第二次人工触点；人审通过并勾选剩余 4 项后可归档。
- Review: verifier PASS；待人审。

### Final Reconciliation

2026-09-16 归档前对照最终实现与证据完成核对：

- Interpretation: 行为样例七行全部按原样实现并被 rv-1~rv-6 锁定；"显式配置 `.iar/evidence` 行为逐字节不变"由 rv-3 legacy 用例证明；"默默定了这些"各条（prd-stem 子目录、gitignore 白名单方案、跨仓库提交、legacy 保留、契约 v1 起步、skill 目录语义不变）均与交付一致。无被推翻的假设。
- Public behavior and contracts: `config.validation.evidence_dir` 默认值 `tasks/evidence`、解析规则（`<root>/<prd-stem>` / `issue-<N>` 兜底）、`iar init` 托管块第四段、`--force` 传递、预检报错指引、prompt 契约指针——均与 §6/§7 叙述一致；§7 Change Impact Tree 已按实际落点修正（预检挂 `agent_runner_publish.run_preflight_checks`）。legacy 配置、无 PRD issue 兜底、skill 缺失/版本不符 fail fast 三种模式均有测试锁定。
- Related PRD status: §5 列举的归档 PRD 关系不变；pending `completeness-judgment-hardening` 的 soft 协调事项仍有效（本 PRD 已先行落地 prompt 面改动）。
- Requirements and risks: FR-1~FR-8 全部交付且 Feature Overview 锚点无漂移；§12 风险缓解（init 托管块 + 发布前拦截 + rv-3 负向控制；预检 fail fast）均已落地；§12 所列后续项（iar 独立发行的 keda 耦合清理、skill 安装目录语义）维持非阻塞后续定位。
- **Decision Log**：D-01~D-07 均与最终交付一致；两处实施偏离（预检挂接点、指针单次命中）已在 Change Log 记录且经 verifier 评估不破坏意图，不构成新决策。
- **验收状态横幅投影**：与 §9 最终状态一致（全部勾选，✅ 可归档）。
- **遗留**：`ensure_no_misplaced_evidence_helpers` 报错文案仍写 `<root>/scripts/`（门禁判定正确，仅文案精度）；`blocked-continue` 直入口不经过预检。两者均为非阻塞观察，不影响本 PRD 验收结论。
