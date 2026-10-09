# PRD: `kc skill install`——独立的随包/远程模板 Skill 安装刷新入口

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> 🧍 **验收状态**：待人工验收 — 执行侧已完成，仅剩 1 项 Human-Confirmed 未确认，证据包见 §9。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文分两层：Part A（§1–§4）面向人审，只讲行为与决策；Part B（§5–§13）面向执行器，含机制、改动树与验证命令。

## Feature Overview (功能一览)

本块是 §10 Functional Requirements 的通俗投影，行为验收以 §1 行为样例表为准，**那里是唯一事实源**。

- **独立 Skill 安装入口**（FR-1）：新增 `kc skill install [--force] [--dry-run]`，不读写 `.kedacode.toml`、不注册仓库、不同步 labels/gitignore，仓库已 init 过也能直接重装/刷新全部用户级 Skill。
- **init 冲突时指路**（FR-2）：`kc init` 被已存在且内容不同的 `.kedacode.toml` 挡住时，退出码仍为 1，但终端提示明确指向 `kc skill install`；`--force` 仍保留给「确实要重建配置」的场景。
- **单一安装实现**（FR-3）：`kc init` 与 `kc skill install` 共用同一份同步编排，远程模板 `prd`/`code-reviewer` 与随包 `kedacode-operator` 的内容来源、安装根解析、幂等跳过、旧名副本清理与 fail-closed 判定完全一致。
- **旧名副本处理**（FR-4）：安装新名 skill 的同时清理各安装根里未被用户改动的旧名 `iar-operator` 副本；改动过的保留并回显路径，`--force` 才删除。
- **远程模板 fail-closed**（FR-5）：本地 `prd`/`code-reviewer` 的 `SKILL.md` 与远程模板不同且未传 `--force` 时，报错退出且不覆盖，行为不回退。
- **机器可发现**（FR-6）：`kc schema --json` 自动收录 `skill install`；随包 `kedacode-operator` skill 与 `docs/guides/agent-runner.md` 同步说明新入口。
- **明确不做**（§11）：不改 runner 运行期的 worktree 内 skill 装载；不改旧名 digest 清理与 fail-closed 语义本身；`kc init` 遇到已存在配置仍然失败（不静默跳过配置继续装 skill）；不提供 `kc skill list`。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

随发行包发行的 operator skill 已随产品改名（`iar-operator` → `kedacode-operator`），安装逻辑本身完整（幂等、fail-closed、带旧名 digest 清理），但**唯一触发入口是 `kc init`**，而 init 的第一步「生成 `.kedacode.toml`」遇已存在的配置文件会直接抛错退出，skill 安装步骤根本执行不到。升级 kedacode 后，已 init 过的仓库没有任何受支持路径安装新名 skill：旧名 `iar-operator` 残留在各用户级 skills 根，`prd`/`code-reviewer` 远程模板 skill 的刷新也被同一耦合挡住；`kc init --force` 会把深度定制的 `.kedacode.toml` 整文件重写，不可接受；`kc schema --json` 全命令树无任何 skill 子命令（以上均为 Issue #245 复现步骤逐条观察到的当前状态）。

### Interpretation (解读回显)

下表的每一行都会成为验收 oracle——**改动表里的行为单元格，就是在改动验收标准**，请按这个心态审阅。

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 🤖 自动验证 | 在已有 `.kedacode.toml`（内容与生成结果不同）的仓库里运行 `kc skill install` | 退出码 0；全部用户级安装根都装上 `kedacode-operator/SKILL.md`；该 `.kedacode.toml` 字节不变 |
| 🤖 自动验证 | 某安装根里有未改动过的旧名 `iar-operator` 副本（与历史随包版本逐字节一致）时运行 `kc skill install` | 旧名副本被自动删除，输出回显 "Removed the legacy operator skill copy at …"；另一安装根里改动过的副本被保留并回显 "Kept the modified legacy operator skill copy at …" |
| 🤖 自动验证 | 本地 `prd` 的 `SKILL.md` 被用户改过时运行 `kc skill install`（失败用例） | 非零退出，报 "Refusing to overwrite user-owned skill 'prd'"，用户改动内容仍在盘上；加 `--force` 后以随包/远程内容覆盖并退出 0 |
| 🤖 自动验证 | `kc skill install --dry-run`（边界用例） | 只打印各安装根的安装计划（"Would install …"），不创建任何目录/文件，退出码 0 |
| 🤖 自动验证 | 在已有 `.kedacode.toml` 的仓库里运行 `kc init` | 退出码仍为 1（既有语义不变），终端提示把用户导向 `kc skill install`，并说明 `kc init --force` 只用于重建配置 |
| 🤖 自动验证 | `kc schema --json` 与 `kc skill install --repo <path>` | schema 的 `commands` 里存在 `["skill","install"]`；`--repo`/`--repo-id`/`--config` 以用法错误拒绝（退出码 2）——本命令面向用户级目录、与目标仓库无关 |

#### 我默默定了这些

- 采用「更完整」方案（新增 `kc skill install` 一等命令），不是「最小改」（让 init 在配置已存在时跳过配置写入继续装 skill）——后者会让 `kc init` 的「失败」语义变得含糊（部分成功算成功还是失败？），且独立入口对「只想刷新 skill」是更干净的长期形态。
- 不提供 `kc skill list`（建议方案里标注「可顺带」，非必需）。
- `kc init` 遇已存在且不同的配置**仍然失败**（退出码 1 不变），只新增指向 `kc skill install` 的提示；`kc takeover` 内部走 init，行为随之自然继承。
- `kc skill install` 拒绝 `--repo`/`--repo-id`/`--config`（用法错误，退出码 2），与 `kc container auth import` 等既有「与仓库无关」命令的守卫形态一致。
- 远程模板 skill 冲突（本地 `SKILL.md` 与远程不同）映射为退出码 1（GENERAL）而非 conflict 专属码——该异常类型同时覆盖「远程不可用」与「fail-closed 拒绝覆盖」，命令树无法只认后者，与 `kc init` 对同一失败保持同一退出码。
- 安装根仍完全由既有解析逻辑决定（`KEDACODE_SKILLS_DIR` 优先，其后产品自有目录与各 agent 用户级目录），新命令不引入新的目标根来源。

#### 我理解为不做

- 不改 runner 运行期 worktree 内的 skill 装载行为（那是执行时路径，不是安装路径）。
- 不改旧名 digest 清理与 fail-closed 的语义本身（哪些字节算「未改动的历史副本」的判定不动）。
- 不做 skill 的卸载/禁用管理，也不做 `kc skill list` 状态展示。

「只想重装/刷新用户级 skill」从此有一条不触碰仓库配置的一等命令（read as：新增独立入口并让 init 指路），而不是「让 init 变得更宽容、在配置冲突时继续跑下去」（not that）；`kc init` 的失败语义、`--force` 语义、安装实现的行为语义全部不变。

### What The User Gets

升级 kedacode（或任何时候想重装/刷新 skill）的用户，在已 init 过的仓库里直接运行 `kc skill install` 即可：新名 `kedacode-operator` 装进全部用户级安装根，未改动过的旧名 `iar-operator` 副本自动清理，改动过的保留并明确告知；深度定制的 `.kedacode.toml` 全程不被触碰。当用户习惯性再跑 `kc init` 而被已有配置挡住时，终端提示会直接告诉他这条新命令。

### Measurable Objectives

- 在已有 `.kedacode.toml` 的仓库中，`kc skill install` 退出码 0 且每个安装根出现 `kedacode-operator/SKILL.md`（真实 CLI 入口，隔离 HOME 下执行）。
- `kc skill install` 前后该 `.kedacode.toml` 的 sha256 逐字节不变。
- 原样旧名副本被删除、改动过的旧名副本保留，且两条回显都出现在输出中。
- 本地改动过的远程模板 `prd` skill 在无 `--force` 时使命令以非零退出且不覆盖；`--force` 后内容被替换。
- `kc schema --json` 的 `commands` 包含 `["skill","install"]`；`--repo` 传参以退出码 2 拒绝。
- `kc init` 在配置冲突时退出码仍为 1，且输出包含 `kc skill install` 指引。

## 2. Human Review Map (介入与风险地图)

### 决策：`kc skill install` 把「远程模板同步失败」与「远程模板被本地改动（fail-closed 拒绝覆盖）」映射为同一个退出码 1

随包 operator skill 的冲突是「保留并提示」（退出码 0），而远程模板 skill 的冲突是「报错退出」（fail-closed，退出码非零）——这套语义是既有安装实现决定的，本 PRD 不改。新命令需要决定的是退出码取值：安装实现用同一个异常类型表达「远程拉取失败」和「拒绝覆盖用户改动」，命令树无法区分两者，因此统一映射为通用失败码 1，而不是 conflict 语义码 4；这样 `kc skill install` 与 `kc init` 对同一失败给出同一退出码，不新增错误语义。风险很低：该命令不在 `--json` 机器输出契约内，退出码 1 vs 4 只影响人工排障习惯。

**请确认：** `kc skill install` 对「远程模板不可用」和「远程模板 fail-closed 拒绝覆盖」统一返回退出码 1（与 `kc init` 一致），可以接受吗？

**验收：** 本地改动 `prd` 的 `SKILL.md` 后运行 `kc skill install` 得到非零退出且与 `kc init` 同失败同码；远程模板不可用时两命令同样同码（均为 1）。

### 自动门禁，不需要逐项人工审阅

其余改动均为执行器 + 自动门禁覆盖：新命令的 argparse/Typer 双注册与 dispatch 接线（守卫测试锁定命令树与 flag 集合）、安装编排抽取为共享函数（既有 init 测试重打桩后全绿）、`RepositoryLocalConfigExistsError` 子类化（仍是 `ValueError`，既有捕获不受影响）、文档与随包 skill 同步（CLI Surface 同步规则的例行执行）。

### 本次明确不涉及

不改 runner 运行期 skill 装载；不改旧名清理与 fail-closed 语义；无数据库结构变化；无前端变化。

## 3. Usage And Impact After Implementation

### 直接使用者：升级 kedacode 后想重装/刷新 skill 的人

```bash
kc skill install --dry-run   # 预览全部安装根的安装计划，不写任何文件
kc skill install             # 安装/刷新全部用户级 Skill（远程模板 + 随包 operator）
```

在已有 `.kedacode.toml` 的仓库里也可直接运行——命令不读仓库配置；传 `--repo`/`--repo-id`/`--config` 会被用法错误拒绝（退出码 2）。远程模板下载需要网络与 GitHub 访问（与 `kc init` 相同的前提）。

### 直接使用者：跑 `kc init` 被已有配置挡住的人

行为不变地失败（退出码 1），但终端提示从「Use --force to overwrite it」单一指向，变为明确的两分指引：刷新 skill 用 `kc skill install`，重建配置才用 `kc init --force`。

### 审查者 / 管理员：阅读随包 kedacode-operator skill 的人

随包 skill 的路由表与 `setup-and-config` 说明新增「重装/刷新 skill」场景，指向 `kc skill install`，并明确「不要用 `kc init --force` 修安装问题」。

### 运维 / 集成方：消费机器可读输出的调用方

`kc schema --json` 自动收录 `["skill","install"]` 及其 flag 集合；`kc skill install` 本身不在 `--json` 机器输出契约内（该命令无 `--output json` 模式，与 `kc init` 一致）。

### 开发者：修改 CLI 表面并需要同步随包资产的维护者

本 PRD 本身就是一次「CLI Surface And Packaged Skill Sync」的完整示例：新子命令、随包 skill 模板、`docs/guides/agent-runner.md`、守卫测试白名单四处同步落地。

### 向后兼容影响

`kc init` 的成功路径输出文本逐字不变（技能同步部分只是从 init 内联搬到共享函数）；`kc init` 配置冲突失败路径的退出码不变（1），stderr 新增一段双语提示；安装实现的任何行为语义（幂等、冲突、旧名清理、fail-closed）均未修改。`kc takeover` 内部走 init，无独立影响。

## 4. Requirement Shape

- **actor**：升级 kedacode 后需要重装/刷新用户级 skill 的用户（及其代为操作的 agent）。
- **trigger**：在任意目录（含已 init 过的仓库）运行 `kc skill install [--force] [--dry-run]`。
- **expected behavior**：把远程模板 `prd`/`code-reviewer` 与随包 `kedacode-operator` 同步到全部用户级安装根；幂等跳过一致副本；清理未改动的旧名副本、保留改动过的并提示；远程模板本地改动 fail-closed；全程不触碰任何 `.kedacode.toml`；`kc init` 配置冲突时把用户导向本命令。
- **explicit scope boundary**：只覆盖安装/刷新入口；不改安装语义本身，不改 runner 运行期装载，不做卸载/list。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

### 5.1 Existing Path

- `src/backend/api/cli_init.py`：`kc init` 的实现，原内联技能同步编排（远程模板 + 逐根随包安装）。
- `src/backend/engines/agent_runner/remote_template_skills.py`：`install_remote_template_skills` / `install_packaged_operator_skill` / `resolve_user_skill_install_roots`——安装语义的唯一权威（幂等、fail-closed、旧名 digest 清理）。
- `src/backend/core/use_cases/agent_runner_init_assets.py`：core 层对上述 engines 符号的再导出（api 层只允许导入 core）。
- `src/backend/engines/agent_runner/repository_local.py`：`initialize_repository_local_config`——init 的第一步，配置已存在且不同时抛错。

### 5.2 Reuse Candidates

- `sync_user_skills` 的全部构件均已存在：两个 install 函数、安装根解析、结果打印文案（从 cli_init 原样搬迁）。
- 双 CLI 注册的既有模式：`workflow install` 等命令的 argparse facade（`cli_parser_ops_commands.py` + dispatch 表 + `cli_parsed_commands/*.py` handler）与 Typer 子应用（`cli_typer_app.py` + `cli_typer_*.py`）。
- 「与仓库无关命令拒绝 selector」的既有守卫形态：`container auth import`、`workflow install` 的同款三条件检查。

### 5.3 Architecture Constraints

- 四层依赖方向 `api -> core -> engines -> infrastructure`：新 api 模块只能经 `agent_runner_init_assets` 再导出拿安装实现，不得直接 import engines。
- `RepositoryLocalConfigExistsError` 必须保持 `ValueError` 子类：既有 `except Exception` / 退出码翻译不受影响。
- CLI 表面变更必须三处同步：随包 `kedacode-operator` skill、`docs/guides/agent-runner.md`、守卫测试 `tests/test_kedacode_operator_skill.py` 的 flag 白名单。

### 5.4 Frontend Impact

No frontend impact：纯 CLI/后端变更，`frontend-admin` 与 console 静态导出均不涉及。

### 5.5 Existing PRD Relationship

- 直接前序：`tasks/archive/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.md`（产品改名，operator skill 改名 `kedacode-operator` 并引入旧名 digest 清理）——本 PRD 的「旧名残留」问题正是那次改名的后续。
- 相关：`tasks/archive/P1-FEAT-20261007-013031-iar-operator-skill-subcommand-hub.md`（随包 skill 结构与守卫测试形态）。
- `tasks/pending/` 中无与本 PRD 重复或互为前后置的工作；可独立交付。

### 5.6 Potential Redundancy Risks

- 最大的冗余风险是把安装编排在 init 与新命令里各写一份——已通过抽取 `sync_user_skills` 单一实现消除。
- 结果打印文案若在新旧两处各存一份会漂移——已随编排一并搬入共享模块。
- 异常类型若新增第二种「配置已存在」表达会分裂捕获面——已用 `ValueError` 子类避免。

## 6. Recommendation

### Recommended Approach

新增 `kc skill install` 一等命令（argparse facade + Typer 双注册，复用既有 `workflow install` 的接线模式），把 init 内联的技能同步编排原样抽取为 `sync_user_skills`（`src/backend/api/cli_skill.py`），init 与新命令都调它；同时把 init 配置冲突的裸 `ValueError` 换成 `RepositoryLocalConfigExistsError`（`ValueError` 子类），init 的 CLI 层识别该类型后在既有失败路径上追加指向 `kc skill install` 的双语提示。

### Design Challenge

- **为什么不选「最小改」（init 跳过配置写入继续装 skill）**：那会让 `kc init` 的成功语义分裂（有时写了配置、有时没写），退出码无法表达「部分完成」，且「我跑 init 到底干了什么」变得不可预测。独立命令把「装 skill」从 init 的多副作用组合里解耦出来，是更干净的长期形态；PRD 的「更完整」建议也是这个方向。
- **`kc skill install` 该不该在远程模板不可用时仍装随包 skill（离线降级）**：随包安装不需要网络，而远程拉取失败会让整条命令退出非零、随包步骤不执行。维持「远程先行、失败即停」与 init 语义一致（同一实现、同一失败面），离线场景用户可用 `KEDACODE_SKILLS_DIR` 在无远程副本的环境下规避；真正的离线解耦若被需要，应另开 PRD 评估，不在本范围静默引入第二种失败语义。

### Proposed Solution Summary (实现机制)

核心机制是「单一同步编排 + 两个入口」：`sync_user_skills`（api 层）依次调用 `install_remote_template_skills`（其返回的 `target_skills_roots` 即安装根解析结果）与逐根 `install_packaged_operator_skill`——内容来源、目标根、冲突判定全部由既有 engines 实现决定，调用方不传路径，因此 `kc init` 与 `kc skill install` 永远写同一批目录、用同一套冲突判定。新命令经既有双注册接线进入命令树（dispatch key `"skill install"`），handler 拒绝仓库 selector（该命令与仓库无关），并把安装实现的异常映射为与 init 一致的退出码。init 侧新增的 `RepositoryLocalConfigExistsError`（`ValueError` 子类）只改变「CLI 能否认出这一种失败」这一件事：认出后在原失败输出上追加指引，退出码不变。刻意避免的复杂度：没有第二种安装实现、没有新的安装根来源、没有 `skill list` 子命令、没有 init 成功语义的变化、没有新增退出码。

### Alternatives Considered

- **最小改：init 在配置已存在时跳过配置写入、继续执行 skill 同步**——被否，理由见 Design Challenge 第一条；`kc init` 的失败语义保持单一。
- **把编排下放到 core 层（`agent_runner_init_assets` 新增组合函数）**：可行但当前只有一个编排消费面（api 层的两个 CLI 入口），core 层既有模块是「再导出」形态而非编排形态；把打印文案也拖进 core 反而污染层职责。维持 api 层编排。

### Scope Cohesion

本 PRD 只有一个可独立批准的决策（新命令的语义与退出码），init 提示与异常子类是它的可发现性配套，文档/守卫同步是 CLI 表面变更的强制配套——三部分构成一个不可分割的目标态，拆分会留下「命令存在但 init 不指路」或「指路但命令不存在」的中间态。

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### 7.1 Core Logic

```
kc skill install                          kc init
    │                                        │
    ▼                                        ▼
argparse facade / Typer 双注册           （既有）初始化本地配置
    │                                        │  ├─ 已存在且不同 → RepositoryLocalConfigExistsError
    ▼                                        │  │   → CLI 识别 → 原失败输出 + 指向 kc skill install 的提示（exit 1）
run_skill_install_command                  ▼
    │  └─ 拒绝 --repo/--repo-id/--config   （既有）gitignore 同步
    │     （usage error, exit 2）            ▼
    ▼                                  _sync_user_skills_or_report_error ──┐
    └──────────────►  sync_user_skills  ◄─────────────────────────────────┘
                          │（单一实现，src/backend/api/cli_skill.py）
                          ├─ install_remote_template_skills（远程模板 prd/code-reviewer；
                          │   返回 target_skills_roots；本地改动 fail-closed → 异常 → exit 1）
                          └─ 逐根 install_packaged_operator_skill（随包 kedacode-operator；
                              幂等 skip / 冲突保留提示 / 旧名 digest 清理）
```

### 7.2 Change Impact Tree

- `src/backend/engines/agent_runner/repository_local.py`：新增 `RepositoryLocalConfigExistsError(ValueError)`；`initialize_repository_local_config` 的「已存在且不同」分支改抛该类型（消息文本不变）。
- `src/backend/core/use_cases/agent_runner_repository_local.py` / `agent_runner_init_assets.py`：再导出新异常与安装结果类型。
- `src/backend/api/cli_skill.py`（新）：`sync_user_skills` + 两个结果打印函数（自 `cli_init.py` 原样搬迁）。
- `src/backend/api/cli_init.py`：内联编排删除，改调共享实现；新增 `except RepositoryLocalConfigExistsError` 分支（提示 + exit 1）。
- `src/backend/api/cli_parsed_commands/skill.py`（新）+ `__init__.py` dispatch 表 + `cli_parser_ops_commands.py`：argparse facade 接线。
- `src/backend/api/cli_typer_app.py`（`skill_app` 注册）+ `cli_typer_skill.py`（新）：Typer 接线。
- `src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md` 与 `references/setup-and-config.md`：新入口的路由与流程说明。
- `docs/guides/agent-runner.md`：新命令章节 + 速查表 + init 提示语更新。
- `tests/test_kedacode_operator_skill.py`：flag 白名单新增 `("skill","install")`；`tests/test_agent_runner_init.py`：打桩目标改到 `cli_skill`；`tests/test_cli_skill_install.py`（新）：CLI 层验收测试。

### 7.3 Risk Classification Register

| 改动点 | 层级 | tier | 决定性维度/覆盖 | 干预 | oracle/门禁 |
|---|---|---|---|---|---|
| `sync_user_skills` 抽取（编排搬迁，语义零变化） | api | R1 | 单组件内聚改动，回滚即还原 | 执行器 + 失败可判别测试 | rv-1、既有 init 测试重打桩全绿 |
| `kc skill install` 命令接线（argparse/Typer/dispatch） | api | R1 | 新增只读表面，无既有路径变更 | 执行器 + 守卫测试 | rv-4、`test_kedacode_operator_skill` flag 白名单 |
| selector 拒绝守卫（usage error exit 2） | api | R1 | 与 sibling 命令同款 | 执行器 + 测试 | rv-4（`--repo` 拒绝） |
| `RepositoryLocalConfigExistsError` 子类化 + init 提示 | engines/api | R1 | `ValueError` 子类，捕获面不变；失败路径文案新增 | 执行器 + 失败可判别测试 | rv-1（init 冲突提示） |
| init 改调共享编排 | api | R1 | 输出文本逐字不变 | 执行器 + 测试 | 既有 init 测试 + rv-1 |
| 文档/随包 skill 同步 | docs/templates | R0 | 纯呈现 | 执行器 + 守卫 | `test_kedacode_operator_skill` 全绿、mkdocs 构建 |
| 退出码统一为 1（远程失败/fail-closed） | api | R2 | 错误语义约定（跨命令一致性） | **人工确认**（§2 唯一决策） | rv-3 + `test_skill_install_remote_template_failure_exits_one_like_init` |

### 7.4 Executor Drift Guard

- 打桩目标已随编排搬家：`tests/test_agent_runner_init.py` 里所有 `backend.api.cli_init.install_*` 的 monkeypatch 必须改指 `backend.api.cli_skill.install_*`——若测试报「attribute does not exist」，先查打桩路径。
- 新命令的 argparse facade 用 `add_common_options`（`--repo`/`--repo-id`/`--config` 以 `argparse.SUPPRESS` 注册），handler 里读 `config` 必须用 `getattr(ctx.parsed, "config", None)`，直接属性访问会在 facade 路径上 `AttributeError`。
- `cli_typer_app.py` 底部的 `cli_typer_*` 导入顺序决定 `kc --help` 顺序：`cli_typer_skill` 插在 `cli_typer_tokens` 与 `cli_typer_schema` 之间（`schema` 永远最后）。
- 远程模板 URL 无 env 覆盖；验证远程失败用 `GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` 的 `url.insteadOf` 重定向，不要改生产代码。

### 7.5 Flow / Architecture Diagram

见 §7.1 的调用流图（文本图即为本 PRD 的架构图：两个入口共享一个编排，编排只调用既有 engines 实现）。

### 7.6 Realistic Validation Plan

以下 YAML 为结构化验收 oracle。全部使用真实 `kc` CLI 入口（`uv run kc`，真实 Typer 命令树），在 `mktemp` 沙箱（隔离 HOME 与隔离 git 仓库）中执行；远程模板 skill 需要网络（git clone 模板仓库），无网络时 rv-1/rv-3 如实失败——这是命令的既有前提，不是环境缺陷。

```yaml
- id: rv-1
  behavior: "在已有 .kedacode.toml 的仓库中，kc skill install 经真实 CLI 入口把 kedacode-operator 装进全部用户级安装根（含 init 被同一配置挡死的前提、幂等复跑、配置文件字节不变）。"
  reviewer: verifier
  real_entry: "隔离 HOME（真实探测出多个安装根）+ 已有 .kedacode.toml 的隔离 git 仓库：先跑 kc init（确认被挡且输出指向 kc skill install），再跑 kc skill install 两次。"
  expected: "kc init 非零退出且输出含 kc skill install 指引；第一次 install 退出码 0、每个安装根出现 kedacode-operator/SKILL.md、.kedacode.toml sha256 不变；第二次报 already up to date。"
  mock_boundary: "隔离 HOME 与隔离仓库；kc 的 Typer 入口、安装根真实解析、安装编排、冲突判定、旧名清理均不替换。"
  tier: R1
  test_layer: real_entry
  required_for_acceptance: true

- id: rv-2
  behavior: "未被用户改动的旧名 iar-operator 副本（与历史随包版本逐字节一致）被自动清理并回显；改动过的副本被保留并回显路径。."
  reviewer: verifier
  real_entry: "在两个真实探测出的安装根分别预置原样旧名副本（取 git 历史中最后一版 iar-operator/SKILL.md）与改动过副本，随 rv-1 的 kc skill install 一并执行。"
  expected: "原样副本被删除且输出含 'Removed the legacy operator skill copy at <路径>'；改动过副本仍在盘上且输出含 'Kept the modified legacy operator skill copy at <路径>'。"
  mock_boundary: "旧名原样副本取自真实 git 历史（b77e74d8 的 iar-operator/SKILL.md）；digest 判定走生产实现。"
  tier: R1
  test_layer: real_entry
  required_for_acceptance: true

- id: rv-3
  behavior: "本地 SKILL.md 与远程模板不同的 prd skill 在无 --force 时保持 fail-closed（非零退出、不覆盖、指名路径），--force 后覆盖；kc skill install 与 kc init 对同一失败同码。"
  reviewer: verifier
  real_entry: "kc skill install 实装后把某安装根的 prd/SKILL.md 改写为用户内容，分别跑 kc skill install（应失败）、kc skill install --force（应成功）；另在测试层断言 init 对同一异常同码。"
  expected: "失败时非零退出、输出含 'Refusing to overwrite user-owned skill' 与该 prd 路径、盘上用户内容不变；--force 后退出码 0 且内容被远程模板替换、改动过的旧名副本同时被删除。"
  mock_boundary: "真实远程模板克隆（需网络）；fail-closed 判定与 --force 覆盖均走生产实现。"
  tier: R2
  test_layer: real_entry
  required_for_acceptance: true
  critical_value_source: "安装根内 prd/SKILL.md 的实际字节（用户改写内容与远程模板内容）。"
  must_cross: "真实 kc CLI 入口 → 远程模板克隆 → fail-closed 比对 → 盘上文件保留/替换的实际结果。"
  forbidden_bypasses: "不得直调 install_remote_template_skills 替代 CLI 入口；不得只断言异常类型而不断言盘上字节。"
  fresh_state_probe: "冲突断言从盘上重新读取 SKILL.md 字节，而非复用进程内状态。"
  final_tree_evidence: "记录最终 git tree、三次命令的完整终端输出（rv-3-conflict.txt / rv-3-force.txt）与冲突前后的文件内容 grep。"
  negative_control: "测试层负控：注入远程模板异常后断言 kc skill install 与 kc init 同以退出码 1 失败（test_skill_install_remote_template_failure_exits_one_like_init）。"
  expected_fail: "若 fail-closed 退化为默认覆盖，rv-3 的『用户内容仍在盘上』断言失败；若两命令退出码分叉，负控测试失败。"

- id: rv-4
  behavior: "机器可读输出与命令边界：kc schema --json 收录 skill install；--dry-run 只打印计划零写入；--repo 以用法错误（退出码 2）拒绝且不写任何文件。"
  reviewer: verifier
  real_entry: "kc schema --json 解析断言 commands 含 ['skill','install']；kc skill install --dry-run 前后对安装根做 sha256 树比对；kc skill install --repo <仓库> 断言退出码 2。"
  expected: "schema 含该命令；dry-run 输出含 'Would install' 且安装根文件树字节不变；--repo 退出码 2 且输出含 'takes no repository'。"
  mock_boundary: "真实命令树派生的 schema；dry-run 走真实编排（安装实现内部短路写入）。"
  tier: R1
  test_layer: real_entry
  required_for_acceptance: true
```

### 7.7 Low-Fidelity Prototype

无用户可见界面变化（纯 CLI 文本输出），免原型；§9.1 以真实终端输出捕获件为呈递物。

### 7.8 External Validation

未使用外部检索；全部事实来自本仓库代码、测试与真实命令执行。

## 8. Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: 改名 PRD（`P1-REFACTOR-20261007-013512`）与本 PRD 的直接前序已归档落地（见 §5.5）；`tasks/pending/` 无前后置工作。

## 9. Acceptance Checklist

本节分两层读者：**9.1 是给人看的**；**9.2 是给 verifier 与未来回溯用的机器证据**。每项必须带证据，不是裸勾。

### 9.1 人读呈递区（Human Review Surface）

| Oracle | 你要看什么 | 呈递物 | 想自己复核？ |
|---|---|---|---|
| rv-3（§2 决策的物证） | 本地改动过的 `prd` skill 在无 `--force` 时不被覆盖 | 终端输出捕获：`tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-conflict.txt`（`open "tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-conflict.txt"`）；全量走查：`just prd review tasks/pending/P2-BUG-20261008-145100-kc-skill-reinstall-entry.md` | 确认输出含 "Refusing to overwrite user-owned skill 'prd'" 与具体路径、退出码非零；同目录 `rv-3-force.txt` 确认 `--force` 后退出码 0 且用户内容被替换 |
| rv-1（新入口端到端） | 已有 `.kedacode.toml` 的仓库里新命令装齐全部安装根 | `tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-1-install.txt` 与 `rv-1-init-failure.txt` | `rv-1-install.txt` 应列出 4 个安装根各一行 "Installed KedaCode operator skill"；`rv-1-init-failure.txt` 应含 init 的报错与指向 `kc skill install` 的提示 |

`reviewer: verifier` 的 rv-2、rv-4 不进入人工呈递区（命令输出断言，人眼看不出增量）。

### 9.2 Acceptance Evidence Package

#### Human-Confirmed

- [ ] **退出码统一为 1**（§2 决策）：确认 `kc skill install` 对「远程模板不可用」与「远程模板 fail-closed 拒绝覆盖」统一返回退出码 1（与 `kc init` 一致）可接受。证据见 rv-3 与负控测试 `test_skill_install_remote_template_failure_exits_one_like_init`。

#### Behavior Acceptance

- [x] 已存在 `.kedacode.toml` 的仓库中 `kc skill install` 把 `kedacode-operator` 装进全部用户级安装根、退出码 0、配置字节不变、复跑幂等（rv-1）。 — 证据：`tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-1-install.txt`（4 根各一行 Installed + 两次 legacy 回显）、`rv-1-idempotent.txt`（already up to date）、`rv-1-init-failure.txt`（init 被挡且指向新命令）；`scripts/rv-skill-install.sh` 阶段 1–5 全 PASS。
- [x] 原样旧名 `iar-operator` 副本自动清理、改动过的保留并回显路径（rv-2）。 — 证据：`rv-1-install.txt` 中 "Removed the legacy operator skill copy at …/.kedacode/skills/iar-operator" 与 "Kept the modified legacy operator skill copy at …/.codex/skills/iar-operator"；脚本阶段 2/4 的 PASS 行。
- [x] 本地改动过的远程模板 `prd` 无 `--force` 时报错不覆盖、`--force` 覆盖；`kc skill install` 与 `kc init` 同失败同码（rv-3）。 — 证据：`rv-3-conflict.txt`（Refusing to overwrite + 退出非零 + 用户内容仍在）、`rv-3-force.txt`（退出 0 + 内容替换 + 改动旧名副本删除）；`tests/test_cli_skill_install.py::test_skill_install_remote_template_failure_exits_one_like_init`。
- [x] `kc schema --json` 收录 `["skill","install"]`；`--dry-run` 零写入；`--repo` 退出码 2（rv-4）。 — 证据：`rv-4-schema.json`、`rv-4-dryrun.txt`、`rv-4-repo-selector.txt`；`tests/test_cli_skill_install.py::test_skill_install_is_visible_in_machine_readable_schema`、`test_skill_install_rejects_repository_selectors`。

#### Documentation Acceptance

- [x] `docs/guides/agent-runner.md` 新增 `kc skill install` 章节（用途、冲突语义、selector 拒绝）并更新 init 提示语与速查表。 — 证据：本分支 `docs/guides/agent-runner.md` diff；`uv run mkdocs build --strict` 通过（见 evidence-report.md）。
- [x] 随包 `kedacode-operator` skill（SKILL.md 路由表 + `references/setup-and-config.md`）同步新入口与「不要用 `kc init --force` 修安装」的指引。 — 证据：本分支两个模板文件 diff；`tests/test_kedacode_operator_skill.py` 全绿（含新增 `("skill","install")` flag 白名单）。

#### Validation Acceptance

- [x] 全部 rv oracle 在真实 `kc` CLI 入口（隔离 HOME、真实安装根解析、真实远程克隆）下执行并 PASS。 — 证据：`scripts/rv-skill-install.sh` 全阶段 PASS；捕获件 rv-1/rv-3/rv-4 系列。
- [x] CLI 层测试锁定四条验收口径与退出码一致性。 — 证据：`uv run pytest -o addopts="" tests/test_cli_skill_install.py tests/test_agent_runner_init.py tests/test_kedacode_operator_skill.py` → 77 passed；另 `tests/test_remote_template_skills.py tests/test_cli_schema.py` → 23 passed。
- [x] 仓库自带门禁通过。 — 证据：`CI=true JUST_FULL_TEST_FLAGS="--no-testmon" just test all`（3667 passed, 1 skipped, 196.56s）；`SKIP=check-test-flag uv run pre-commit run --all-files` 全绿（ruff / ruff-format / 架构层依赖 / 文件行数 / PRD checklist 等）；`uv run mkdocs build --strict` 通过。

#### Delivery Readiness

- [x] 完成消息（交付摘要）逐字携带 §9.1 两行呈递与打开方式。 — 证据：本次交付的最终摘要包含呈递表；`just prd review tasks/pending/P2-BUG-20261008-145100-kc-skill-reinstall-entry.md` 可打开本检查单。
- [~] 独立 verifier PASS 后归档（runner-owned gate: independent verifier + archive）。

## 10. Functional Requirements

- **FR-1**：新增 `kc skill install [--force] [--dry-run]`：同步远程模板 `prd`/`code-reviewer` 与随包 `kedacode-operator` 到全部用户级安装根；不读写 `.kedacode.toml`、不注册仓库、不同步 labels/gitignore；拒绝 `--repo`/`--repo-id`/`--config`（usage error，退出码 2）。
- **FR-2**：`kc init` 遇已存在且内容不同的 `.kedacode.toml` 时维持退出码 1，并在终端输出中明确指向 `kc skill install`；`--force` 语义（整文件重建）不变。
- **FR-3**：`kc init` 与 `kc skill install` 共用单一同步编排（`sync_user_skills`），内容来源、安装根解析、幂等、冲突判定、旧名清理完全一致；init 成功路径的输出文本逐字不变。
- **FR-4**：未改动的旧名 `iar-operator` 副本（与历史随包版本逐字节一致）在安装时自动删除并回显；改动过的保留并回显路径；`--force` 直接删除。
- **FR-5**：本地 `prd`/`code-reviewer` 的 `SKILL.md` 与远程模板不同且未传 `--force` 时，报错退出（与 `kc init` 同码），不覆盖；`--force` 覆盖。
- **FR-6**：`kc schema --json` 自动收录 `skill install`；随包 `kedacode-operator` skill 与 `docs/guides/agent-runner.md` 同步说明新入口。

## 11. Non-Goals

- 不改 runner 运行期的 worktree 内 skill 装载行为。
- 不改旧名 digest 清理与 fail-closed 语义本身（含「哪些字节算未改动历史副本」的判定）。
- 不让 `kc init` 在配置已存在时跳过配置写入继续执行（init 失败语义保持单一）。
- 不提供 `kc skill list` 或任何卸载/禁用管理。
- 不引入新的安装根来源、新的退出码、`--json` 机器输出模式。
- 不做远程模板失败时的离线降级（随包装、远程跳过）——如有需要另开 PRD。

## 12. Risks And Follow-Ups

- **离线场景**：远程模板克隆失败时整条命令（含不需要网络的随包安装）退出非零——与 `kc init` 一致的既有耦合；§6 Design Challenge 已记录，若真实用户撞到再评估解耦。
- **远程失败的消息形态**：远程克隆的原生 subprocess 失败（非 `RemoteTemplateSkillInstallError`）走顶层通用错误封套而非「Remote template skill installation failed」前缀行——失败仍非零、信息仍完整，属消息形态差异；如评审认为应统一，是一行 except 拓宽的后续微调。
- **后续迭代（不阻塞）**：`kc skill list` 状态展示、离线降级、远程模板 URL 可配置化，均以真实使用反馈为前提另行立项。

## 13. Decision Log

| ID | 决策 | 状态 | 说明 |
|---|---|---|---|
| D-01 | 新增 `kc skill install` 一等命令，init 组合调用同一实现 | Human-Confirmed（随 §2 决策一并接受） | 最小改会让 `kc init` 的成功/失败语义分裂（部分完成无法表达）；独立入口是「只想刷新 skill」的干净长期形态 |
| D-02 | `kc init` 配置冲突仍失败（exit 1），只新增指路提示 | Proposed | 保持 init 失败语义单一；提示经 `RepositoryLocalConfigExistsError`（`ValueError` 子类）识别，既有捕获与退出码不受影响 |
| D-03 | 远程模板失败与 fail-closed 统一退出码 1，不引入 conflict 码 | Human-Confirmed | 同一异常类型覆盖两种失败，命令树无法区分；与 `kc init` 同失败同码，不新增错误语义 |
| D-04 | 拒绝仓库 selector（exit 2），不提供 `kc skill list` | Proposed | 命令面向用户级目录、与仓库无关，与 `container auth import` 等 sibling 守卫形态一致；list 非必需 |
| D-05 | 编排留在 api 层共享模块，不下放 core | Proposed | core 层既有模块是再导出形态；编排含终端打印，属 api 层职责 |

### Final Reconciliation

- Interpretation:与最终实现一致 —— §1 行为样例表六行全部按样落地：已有 `.kedacode.toml` 的仓库里 `kc skill install` 全根安装且配置字节不变（rv-1）；原样旧名副本 Removed / 改动副本 Kept 回显（rv-2）；远程模板 fail-closed 与 `--force` 覆盖（rv-3）；dry-run 零写入、init 冲突指路、schema 收录与 selector 拒绝（rv-4）。「我默默定了这些」各条（不做最小改、不做 list、init 仍失败、selector 拒绝、退出码 1、安装根来源不变）均与实现逐项核对无误；§5 引用的路径与符号已对照当前 worktree 核对（`cli_skill.py` / `cli_typer_skill.py` / `cli_parsed_commands/skill.py` / `RepositoryLocalConfigExistsError` 均在位）。
- Public behavior and contracts:CLI 表面新增 `kc skill install` 命令（旗标 `--force` 与 `--dry-run`；usage error 退出码 2 拒绝仓库 selector；远程失败/ fail-closed 退出码 1；其余成功 0）；`kc init` 配置冲突失败路径退出码不变（1）、stderr 新增双语指引，成功路径输出逐字不变；`kc schema --json`、随包 `kedacode-operator` skill 与 `docs/guides/agent-runner.md` 已同步；功能一览六个 bullet 与 §10 FR-1–FR-6 锚点一一对应且仍全部成立。
- Outcome:`kc skill install` 落地（argparse + Typer 双注册、dispatch 接线、selector 拒绝、schema 收录）；`kc init` 改调共享编排并在配置冲突时指向新命令；四条验收口径全部在真实 CLI 入口取证通过。
- Related PRD status:`tasks/pending/` 中无与本 PRD 重复或互为前后置的待办；前序改名 PRD（`P1-REFACTOR-20261007-013512`）与随包 skill 结构 PRD（`P1-FEAT-20261007-013031`）均已归档落地（§5.5）。
- Design:与 §6 一致——单一编排 `sync_user_skills`（api 层）、`RepositoryLocalConfigExistsError`（`ValueError` 子类）、退出码统一 1；未新增安装实现、安装根来源或子命令。
- Requirements and risks:§10 全部 FR 已逐条验证并留证（FR-1/FR-4/FR-5 由 rv-1–rv-3 真实入口取证，FR-2 由 rv-1 阶段 1 与 `test_init_config_conflict_points_at_skill_install` 锁定，FR-3 由既有 init 测试重打桩全绿与输出比对锁定，FR-6 由 rv-4 与守卫测试锁定）；§12 披露的残余风险（离线耦合、远程原生失败消息形态）已如实记录并给出后续方向。
- Dependency:无前后置（§8 none）；改名前序 PRD 已归档。
- Evidence:`scripts/rv-skill-install.sh` 全阶段 PASS（rv-1/rv-2/rv-3/rv-4 捕获件齐备）；`uv run pytest -o addopts="" tests/test_cli_skill_install.py tests/test_agent_runner_init.py tests/test_kedacode_operator_skill.py` → 77 passed、`tests/test_remote_template_skills.py tests/test_cli_schema.py` → 23 passed；`CI=true JUST_FULL_TEST_FLAGS="--no-testmon" just test all` → 3667 passed, 1 skipped；`SKIP=check-test-flag uv run pre-commit run --all-files` 全绿；`uv run mkdocs build --strict` 通过。详见 evidence-report.md。
- Deviation:代码评审发现两类低严重度遗留并如实记录（§12）：离线场景随包安装被远程失败阻断（与 init 一致的既有耦合）、远程原生失败的消息形态走通用封套；均未静默按通过处理，均不触碰验收口径。代码评审建议的 4 处打磨（Facade `__all__` 补 `skill_app`、随包 skill Recovery 补 fail-closed 说明、selector 守卫与测试 helper 抽取、`cli_typer_skill` 换用 `_run_typer_repository_command`）经评估为既有模式一致性取舍，不做静默扩大改动面，记录备查。
- Delivery:PR 创建、独立 verifier 与归档为 runner 自有门禁（§9.2 Delivery Readiness 的 `[~]` 项）；Human-Confirmed 一项保持空框待人工验收。

## Change Log

### Change 1 — 按 Machine Contract v5 重构为两层结构并补齐交付证据

- Type: structure + evidence
- Before: PRD 为简式结构（背景/复现/影响/建议方案/验收口径/非目标），无 Acceptance Checklist 章节、无横幅、无 rv-id oracle，runner 交付检查因「Acceptance Checklist section missing」失败。
- After: 全文重构为 v5 两层结构（交付前置横幅 ✅、验收状态横幅 🧍、功能一览、Part A §1–§4、Part B §5–§13、Change Log）；四条验收口径转为 rv-1–rv-4 结构化 oracle 并绑定已采集证据；§9 执行侧条目全部勾选并点名证据；§12 如实记录代码评审发现的两类低严重度遗留。
- Reason: 实现与证据已在前序尝试完成（代码、测试、rv 捕获件齐备），但 PRD 从未升级到 Machine Contract 结构，交付门禁无法解析验收清单。
- Impact: 只改 PRD 文档与新增证据文本报告（verification-plan / evidence-report），不改任何已交付代码、测试或 oracle 语义；验收口径四条原样保留为 rv-1–rv-4 与 §9 勾选项；新增 §2 唯一人工决策（退出码统一为 1）对应 Human-Confirmed 空框一项。
- Review: 执行侧自检（结构对齐 archived v5 范例 P1-FEAT-20261007-013031；`check_prd_acceptance_checklist.py --check-provided --archive-ready` 通过；横幅与 §9 一致）。
