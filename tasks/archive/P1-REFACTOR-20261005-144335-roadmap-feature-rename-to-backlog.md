# PRD: Roadmap 功能正名为 Backlog

- GitHub Issue: https://github.com/ZataZhang/keda/issues/196

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> 🧍 **验收状态**：待人工验收 — §9 除 4 项 `Human-Confirmed`（决策一/二/三 + §9.1 呈递物过目）与 2 项 runner-owned `[~]` 门禁（verifier review、归档）外全部完成并附证据；rv-1..rv-4 均 `RESULT: PASS` 且各含负控变红，证据包见 `.iar/evidence/evidence.json`。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文档分两个高度：**Part A（§1–§4）** 给人确认行为与风险，**Part B（§5–§13）** 给执行器实现和验证。

## Feature Overview (功能一览)

> 本块是 §10 Functional Requirements 的通俗投影，不是第二事实源；行为验收以 §1 行为样例表为准。

- **功能词义归位**（FR-1）：把"以 PRD 文件为事实源的待办/队列工作台"从 `roadmap` 正名为 `backlog`，覆盖后端模型、用例、端口、工厂、持久化与 re-export。
- **CLI 硬改名**（FR-2）：`iar roadmap` → `iar backlog`（含 `advance`），旧命令移除，不留别名。
- **HTTP API 与控制台路由硬改名**（FR-3、FR-4）：`/api/v1/agent-runner/roadmap/*` → `/backlog/*`，控制台 `/app/roadmap` → `/app/backlog`，旧路径不再提供。
- **前端命名同步**（FR-5）：`frontend-public` 的路由目录、`components/roadmap/*`、`lib/api/roadmap.ts` 与 `Roadmap*` 类型全部改为 `backlog`。
- **存量数据无损迁移**（FR-6）：控制台 SQLite `user_version` v6→v7，`ALTER TABLE ... RENAME` 把 `roadmap_queue`/`roadmap_settings` 改为 `backlog_*`，保留数据。
- **文档、导航与守卫同步**（FR-7、FR-8）：`docs/`、`README.md`、`mkdocs.yml`、测试与架构守卫全量对齐；功能面不得残留 `roadmap` 语义。
- **战略文档不动并消歧**（FR-9）：`ROADMAP.md` 与里程碑命名保持原样，只在顶部加一句消歧说明，明确它是战略文档、与 Backlog 工作台不同。
- **本次明确不涉及**（§11）：不做行为变更，不重命名历史原型文件与归档 PRD，不引入兼容别名。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

keda 里 "Roadmap" 一词同时指两样不同种类的东西：仓库根目录的 `ROADMAP.md`（战略/里程碑文档）与控制台功能 `/app/roadmap` + `iar roadmap` + `/agent-runner/roadmap/*`。后者实际是"以 PRD 文件为事实源的待办/队列工作台"——`roadmap_prd_scanner.py` 的 docstring 原文即 *"Scan PRD Markdown files … reads `tasks/pending/` and `tasks/archive/`"*，`roadmap_actions.py` 的动词是 `start` / `advance_roadmap_queue` / `max_parallel`，`docs/guides/agent-runner.md` 也写作"路线图（Roadmap）… 展示 `tasks/pending/` 与 `tasks/archive/` 中的任务全景"。

问题有二：**其一，词义偏了**——"Roadmap" 承诺的是战略方向，而这个功能是执行侧的 PRD 队列/看板，承载的是"手头这些 PRD 排到哪、下一个跑谁"，不是"我们要走到哪"。**其二，撞名**——同一仓库里 "Roadmap" 既是功能又是根目录文档，`rg roadmap` 同时命中两个语义域，对话与文档里说"更新 roadmap"必须靠上下文猜。

当前可观测事实：功能名 `roadmap` 已嵌入 `src/backend` 29 个文件、`frontend-public` 36 个、`docs` 27 个、`tests` 26 个、`tasks` 68 个；控制台 SQLite 有两张以 `roadmap` 命名的表（`roadmap_queue`、`roadmap_settings`）；CLI 有 `iar roadmap advance`。因此这不是换一个 UI 文案的问题，而是一次需要端到端对齐、且带一次数据迁移的重命名。

### Interpretation (解读回显)

**行为样例**（下表每一行都会逐字变成验收标准，修改单元格即修改对应验收条件）：

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 🤖 自动验证 | `iar backlog advance --dry-run`（真实 CLI） | 正常输出调度计划；退出 0 |
| 🤖 自动验证 | `iar roadmap advance --dry-run`（旧命令） | 用法错误/未知命令，旧名不再可用（硬改名） |
| 🤖 自动验证 | `GET /api/v1/agent-runner/backlog/prds` | 返回与旧 `roadmap/prds` 同构的数据；`GET /api/v1/agent-runner/roadmap/prds` 返回 404 |
| 👀 人审 + 自动验证 | 真实控制台打开 `/app/backlog` | 渲染 PRD 待办工作台，标题/导航显示 Backlog（不再出现"路线图/Roadmap"）；`/app/roadmap` 不再提供 |
| 🤖 自动验证 | 对既有 v6 `console.db`（含 `roadmap_*` 表与数据）启动 console | 迁移到 v7 后表名为 `backlog_queue`/`backlog_settings`，行数与内容保留 |
| 🤖 自动验证 | `rg -n "roadmap" src/backend frontend-public` | 功能面零命中（仅剩战略文档 `ROADMAP.md` 与历史原型/归档等白名单） |
| 🤖 自动验证 | 全量门禁 | `just lint --full`、`just test`、`pnpm typecheck && pnpm build`、`uv run mkdocs build --strict` 全绿，无断链 |

修改上表任一单元格即修改对应验收条件；命门在两处：旧名必须真的不可用（不是隐藏别名），以及 v6 数据必须无损迁移。

**我默默定了这些**：

- 战略文档 `ROADMAP.md` 与里程碑（M0–M11）命名不改——用户已确认；本功能改名不牵连战略文档。
- 硬改名、不留兼容别名——用户已确认；旧 CLI 命令、旧 API 路径、旧控制台路由一律移除。
- 持久化表随功能一起改名，走控制台 SQLite `PRAGMA user_version` v6→v7 迁移——用户已确认。
- 领域词统一用 `Backlog`；中文语境可用"待办队列"。控制台页面主标题用 `Backlog`，可带中文副标题。
- 历史原型资产文件名（`docs/prototypes/roadmap-*`）与 `tasks/archive/` 内的历史 PRD 文本不改——它们是历史记录，改了反而破坏可追溯性与既有链接。
- 控制台静态产物（`src/backend/api/static/console/...`、`frontend-public/out/...`）由构建重新生成，不手工改。
- 本 PRD 只做重命名，不改变任何功能行为（不新增/删除能力、不调整调度语义）。

**我理解为不做**：

- 不修改 `ROADMAP.md` 的战略内容（除顶部一句消歧说明）。
- 不重命名历史原型文件与归档 PRD 中的文本引用。
- 不为兼容只改用户可见名而保留内部 `roadmap` 标识符（那正是要消除的语义残留）。

**可证伪的读法**：本 PRD 读作一次**行为保持**的功能身份重命名——把功能在代码、UI、CLI、HTTP API、数据库与文档中的身份从 `roadmap` 统一改为 `backlog`，且旧对外入口真的消失；**不**读作功能变更，**不**读作战略文档 `ROADMAP.md` 的改名，**不**读作局部/仅文案的重命名。

### What The User Gets

- 控制台使用者：在 `/app/backlog` 看到同一个 PRD 待办工作台，命名与实际用途一致，不再与应用名相近的战略文档混淆。
- Agent（经 shell 调用 CLI）：用 `iar backlog advance` 驱动调度，语义清晰。
- 仓库维护者：搜索 `roadmap` 只指向战略文档 `ROADMAP.md`，代码/界面里的 `backlog` 专指功能。
- 战略文档读者：`ROADMAP.md` 仍是唯一的战略路线图，顶部一句话点明与控制台 Backlog 工作台的区别。

### Measurable Objectives

- CLI：`iar backlog advance` 可用、`iar roadmap` 不再可用；真实命令退出码符合约定。
- HTTP API：`/api/v1/agent-runner/backlog/*` 全量可用，旧 `/roadmap/*` 路径返回 404。
- 控制台：`/app/backlog` 渲染工作台，`/app/roadmap` 不再提供。
- 数据：对含数据的 v6 库迁移后，两张表名与全部行/列值保持，无丢行。
- 静态：`rg -n "roadmap"` 在功能面（`src/backend`、`frontend-public`）零命中；白名单外不得残留。
- 全量门禁：`just lint --full`、`just test`、`pnpm typecheck && pnpm build`、`uv run mkdocs build --strict` 全部通过。

## 2. Human Review Map (介入与风险地图)

### 决策一：把功能正名为 Backlog，战略文档 ROADMAP.md 保持不动

该功能实为"以 PRD 文件为事实源的待办/队列工作台"，"Roadmap" 一词会让人以为它承载战略方向，并与根目录 `ROADMAP.md` 撞名。推荐把功能在代码、UI、CLI、API、数据库与文档中的身份统一正名为 `Backlog`；战略文档 `ROADMAP.md` 与其里程碑命名保持原样，只在顶部加一句消歧说明，明确它是战略文档、与 Backlog 工作台不同。

**请确认：** 接受把该功能整体正名为 Backlog，且 `ROADMAP.md` 战略文档只加消歧说明、不做内容改名？

**验收：** 控制台 `/app/backlog`、CLI `iar backlog`、API `/agent-runner/backlog/*` 全部体现 Backlog；`ROADMAP.md` 仍存在且内容与里程碑命名不变。

### 决策二：硬改名，不留兼容别名（破坏性对外变更）

`iar roadmap`、`/agent-runner/roadmap/*`、`/app/roadmap` 都是对外入口，`kedacode` 已发布到 PyPI。为避免长期背负两套命名，推荐**全面硬改名**：旧 CLI 命令、旧 API 路径、旧控制台路由一律移除，不保留 hidden alias 或重定向。风险是老脚本/书签/外部 API 调用会中断；收益是命名彻底一致、无第二事实源。若日后确需兼容，可另立一个短期兼容 PRD，而不是在本 PRD 里预留。

**请确认：** 接受硬改名、旧入口直接移除（这是破坏性变更）？

**验收：** `iar roadmap` 报未知命令；`/api/v1/agent-runner/roadmap/*` 与 `/app/roadmap` 均不再提供；新入口全部可用。

### 决策三：控制台 SQLite 表随功能迁移重命名（v6→v7）

控制台库有两张以 `roadmap` 命名的表（`roadmap_queue`、`roadmap_settings`）且含真实数据。推荐在既有 `PRAGMA user_version` 就地迁移里新增 v7 步，用 `ALTER TABLE ... RENAME TO` 把两张表改为 `backlog_queue`/`backlog_settings`，数据原样保留；同步把 `IRoadmapStore` 及其方法（`enqueue_roadmap`、`list_roadmap_queue` 等）改名。风险是迁移写错导致丢数据；缓解是迁移前后对行数/关键列做断言，并提供 round-trip 验证。

**请确认：** 接受新增 v7 迁移重命名这两张表（保留数据），而不是只改代码标识符、把表名留在 `roadmap_*`？

**验收：** 用含数据的 v6 库启动 console 后，表名为 `backlog_*`，行数与关键列值不变；对空库亦可迁移。

### 自动门禁，不需要逐项人工审阅

除上述三项，其余为机械重命名，由自动化门禁覆盖：架构守卫（`just lint --full`，含分层依赖与命名）、全量测试（`just test`）、前端类型与构建（`pnpm typecheck && pnpm build`）、文档断链（`uv run mkdocs build --strict`），以及"功能面 `roadmap` 零命中"的仓库级搜索断言。这些不需要人工逐项目视。

**本次明确不涉及**：无功能行为变更；不改 `ROADMAP.md` 战略内容；不重命名历史原型文件与归档 PRD 文本；不引入兼容别名；无新增第三方依赖。

三项决策同属一个不可分割的目标态：功能身份必须端到端一致，任何一项单独落地都会留下"代码叫 backlog、旧入口还在"或"入口改了、表还叫 roadmap"的不一致，反而比现状更乱；因此不拆分 PRD。

## 3. Usage And Impact After Implementation

**控制台操作者**：书签 `/app/roadmap` 失效，改用 `/app/backlog`；页面标题与导航显示 Backlog。其余操作（选择仓库/PRD、开始 PRD、Autopilot、证据、生命周期）不变。

**Agent（经 shell 的 CLI 调用方）**：脚本中的 `iar roadmap advance` 必须改为 `iar backlog advance`；旧命令会失败。这是本 PRD 最主要的破坏性影响。

**API 调用方**：`/api/v1/agent-runner/roadmap/*` 全部迁移到 `/backlog/*`，旧路径 404。仓库自带的控制台前端已同步，无内部破坏。

**daemon 运维者**：`iar daemon`/`review-daemon` 等命令名不变；仅内部调用与日志文案里的 roadmap 语义改为 backlog。行为不变。

**维护者/开发者**：代码、测试、文档、`mkdocs.yml` 导航、架构守卫里的命名统一为 backlog；`ROADMAP.md` 顶部新增一句消歧。`rg roadmap` 只剩战略文档与历史资产。

## 4. Requirement Shape

- **actor**：控制台操作者、Agent（CLI 调用方）、HTTP API 调用方、daemon 运维者、仓库维护者。
- **trigger**：合并本 PRD 的实现后，所有 actor 通过新命名（`backlog`）访问同一功能；升级方在既有控制台库上首次启动时触发 v6→v7 迁移。
- **expected behavior**：功能行为完全不变，仅身份命名从 `roadmap` 变为 `backlog`；旧对外入口移除；存量数据无损迁移；全量门禁通过。
- **explicit scope boundary**：只做重命名与一次表迁移；不改功能逻辑、不改 `ROADMAP.md` 内容、不改历史原型/归档资产、不留兼容别名。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**当前相关路径**：

- 后端 core 模型与用例：`src/backend/core/shared/models/roadmap.py`；`src/backend/core/use_cases/roadmap_state_resolver.py`、`roadmap_dependencies.py`、`roadmap_prd_scanner.py`、`roadmap_actions.py`、`roadmap_prd_evidence.py`、`roadmap_autopilot_settings.py`。
- core 端口：`src/backend/core/shared/interfaces/runner_console.py` 的 `IRoadmapStore`、`RoadmapQueueEntry`、`RoadmapSettingsEntry` 与 `enqueue_roadmap` / `list_roadmap_queue` / `update_roadmap_queue_status` / `clear_roadmap_queue` / `get_roadmap_settings` / `save_roadmap_settings`。
- 持久化：`src/backend/infrastructure/persistence/console_store.py` 的表 `roadmap_queue`、`roadmap_settings` 与 `PRAGMA user_version` 迁移（当前 `_SCHEMA_VERSION = 6`）。
- 工厂：`src/backend/engines/agent_runner/factories/__init__.py::create_roadmap_store`，以及 `factory.py`、`cli.py`、`cli_reexports.py` 的 re-export。
- API：`src/backend/api/routes/agent_runner_roadmap.py`（`/agent-runner/roadmap/*`）；`agent_runner_lifecycle_agents.py` 另有 `/agent-runner/roadmap/prds/{encoded_path}/agent-overrides`；`app.py` 的 `include_router`。
- CLI：`src/backend/api/cli_typer_app.py`（`roadmap_app`、`add_typer(..., name="roadmap")`）、`cli_typer_roadmap.py`、`cli_parsed_commands/roadmap.py`、argparse 路径 `cli_parser.py`（`add_parser("roadmap")`、`command="roadmap advance"`）。
- 前端：`frontend-public/app/(app)/app/roadmap/`（路由）、`frontend-public/components/roadmap/*`、`frontend-public/lib/api/roadmap.ts`、`frontend-public/lib/api/types.ts` 的 `Roadmap*` 类型、`frontend-public/app/layout.tsx` 的描述文案。
- 文档/导航：`docs/guides/agent-runner.md` 的"路线图（Roadmap）"小节、`docs/api/references.md`、`docs/architecture/system-design.md`、`README.md`、`mkdocs.yml` 的 nav 文案。
- 战略文档：根目录 `ROADMAP.md`（保持内容，仅加消歧说明）。

**架构约束**：遵守四层依赖方向（`api → core → engines → infrastructure`）；重命名不得引入新层或新依赖；`core` 不得 import `infrastructure`/FastAPI/tomlkit；架构守卫 `just lint --full` 必须通过。持久化迁移必须复用既有 `console_store` 的 `PRAGMA user_version` 机制，不引入 Alembic。

**Frontend Impact**：**Full-stack**，只改 `frontend-public`（路由目录、组件目录、API client、类型、文案）；`frontend-admin` 无影响。运行命令 `just run frontend-public`；真实入口验证用 `just e2e`（或手工打开 `/app/backlog`）。本次为纯命名变更，无布局/交互变化，原型改动见 §7 的 waiver 说明。

**Existing PRD Relationship**：本 PRD 是对现有功能的重命名，**不**与任何 pending PRD 重复。与 `P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair` 存在硬依赖关系：该 PRD 已按改名后的目标态书写（CLI `iar backlog ci`、backlog 路径/符号），并把本改名 PRD 记为 `hard` 前置；本改名 PRD 自身不依赖任何 PRD，但**应先建 Issue** 以便 134008 物化依赖。其余 pending PRD 与本 PRD 独立。

**Potential Redundancy Risks**：不得为兼容另建"roadmap 别名层"或第二套路由；不得复制一份 `backlog_*` 而保留 `roadmap_*`。重命名应是同一批符号的替换（含 `git mv`），而非新增并行实现。

## 6. Recommendation

### Recommended Approach

做一次**行为保持的仓库级重命名**，把功能身份从 `roadmap` 统一改为 `backlog`，覆盖后端标识符、CLI、HTTP API、控制台路由、前端组件与类型、控制台 SQLite 表名与文档：

1. 后端：`git mv` 把 `models/roadmap.py`、`use_cases/roadmap_*.py`、`api/routes/agent_runner_roadmap.py`、`api/cli_typer_roadmap.py`、`api/cli_parsed_commands/roadmap.py` 改名；把 `Roadmap*` 类/函数/变量（`RoadmapPrd`、`RoadmapQueueEntry`、`IRoadmapStore`、`create_roadmap_store`、`advance_roadmap_queue` 等）改为 `Backlog*`/`backlog`；同步 `runner_console.py` 端口、`factories/__init__.py`、`factory.py`、`cli.py`、`cli_reexports.py`、`app.py`。
2. CLI：`roadmap_app` → `backlog_app`，`add_typer(..., name="backlog")`；`cli_parser.py` 的 argparse 分支由 `roadmap` 改为 `backlog`；**移除** `roadmap` 命令，不留别名。
3. HTTP API：路由前缀 `/agent-runner/roadmap/*` → `/agent-runner/backlog/*`，含 `agent_runner_lifecycle_agents.py` 里的 agent-overrides 子路由；旧路径不保留。
4. 前端：`git mv` 路由目录 `app/(app)/app/roadmap` → `backlog`、`components/roadmap` → `backlog`、`lib/api/roadmap.ts` → `backlog.ts`；`Roadmap*` 类型与调用点改为 `Backlog*`；更新 `layout.tsx` 文案与页面标题。控制台静态产物由构建重生成。
5. 数据：在 `console_store.py` 的 `_migrate` 新增 v7 步，`ALTER TABLE roadmap_queue RENAME TO backlog_queue`、`ALTER TABLE roadmap_settings RENAME TO backlog_settings`，并把所有 SQL 语句与方法名中的 `roadmap` 改为 `backlog`；`_SCHEMA_VERSION` 升为 7。
6. 文档与守卫：更新 `docs/`、`README.md`、`mkdocs.yml` nav 文案；同步 `tests/`（26 个文件）与守卫期望；在 `ROADMAP.md` 顶部加消歧说明。
7. 收尾：`rg -n "roadmap"` 在功能面应为零命中（白名单：`ROADMAP.md`、`docs/prototypes/roadmap-*` 历史原型、`tasks/archive/` 历史 PRD）。

### Proposed Solution Summary (实现机制)

机制是**符号与路径的机械替换 + 一次幂等表迁移**，不新增抽象、不改状态机、不改依赖方向。作用域由 §5 的点名文件与全仓 `rg roadmap` 界定；执行顺序以保证任意时刻可编译/可测为准（先 core 端口与模型，再 infrastructure 与 factories，再 api/cli，再前端，再文档与测试），每步后跑对应门禁。数据库迁移复用既有 `PRAGMA user_version` 就地迁移，先建新表名（RENAME 保留数据），使升级用户首次启动即完成；对全新库则在建表时直接用新表名。旧 CLI 命令、旧 API 路径、旧控制台路由直接移除，不引入兼容层——这是用户确认的硬改名取舍。避免的复杂度：不引入 alias/redirect/双写、不新增存储、不改功能语义。

### Alternatives Considered

- **只改用户可见名（UI 文案/文档），保留内部 `roadmap` 标识符与 API 路径**：拒绝；这正是要消除的语义残留，且 `rg roadmap` 仍混淆两个语义域。
- **改内部名但保留旧 CLI/API/路由兼容别名**：拒绝（用户选择硬改名）；额外兼容层会成为长期维护负担与第二事实源。
- **不迁移表、只改代码标识符**：拒绝（用户选择迁移重命名表）；会留下"代码叫 backlog、schema 叫 roadmap"的命名分裂。
- **连 `ROADMAP.md` 战略文档一起改名**：拒绝（用户确认保留）；战略文档正是 "roadmap" 一词的本来正确用法。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

重命名本身不改变数据/控制流。需要保证的两条不变量：(1) **编译/加载可恢复**——任意中途状态都能 `just lint`/`just test` 定位遗漏的引用；(2) **迁移幂等且无损**——`_migrate` 依据 `PRAGMA user_version` 只在 v6→v7 执行一次 RENAME，且对空表/缺表安全。控制流保持：`console`/CLI → core 用例（`backlog_actions` 等）→ 端口 `IBacklogStore` → `console_store` 的 SQLite 表。命令分发：Typer `backlog_app` 与 argparse `backlog` 分支产出同一 `command="backlog advance"` 字符串。

### Change Impact Tree

```text
.
├── src/backend/core/shared/models/roadmap.py → backlog.py
│   [重命名/修改] RoadmapPrd/RoadmapDependency/RoadmapPrdState/RoadmapActionResult → Backlog*
├── src/backend/core/use_cases/roadmap_state_resolver.py → backlog_state_resolver.py
├── src/backend/core/use_cases/roadmap_dependencies.py → backlog_dependencies.py
├── src/backend/core/use_cases/roadmap_prd_scanner.py → backlog_prd_scanner.py
├── src/backend/core/use_cases/roadmap_actions.py → backlog_actions.py
│   [修改] start_prd / start_global_roadmap → start_global_backlog；advance_roadmap_queue → advance_backlog_queue
├── src/backend/core/use_cases/roadmap_prd_evidence.py → backlog_prd_evidence.py
├── src/backend/core/use_cases/roadmap_autopilot_settings.py → backlog_autopilot_settings.py
├── src/backend/core/shared/interfaces/runner_console.py
│   [修改] IRoadmapStore → IBacklogStore；RoadmapQueueEntry/RoadmapSettingsEntry → Backlog*；枚举与方法名去 roadmap
├── src/backend/infrastructure/persistence/console_store.py
│   [修改] 表 roadmap_queue/roadmap_settings → backlog_queue/backlog_settings；_SCHEMA_VERSION 6→7 新增 RENAME 迁移；SQL 与方法名对齐
├── src/backend/engines/agent_runner/factories/__init__.py
│   [修改] create_roadmap_store → create_backlog_store
├── src/backend/engines/agent_runner/factory.py
│   [修改] re-export 改名
├── src/backend/api/routes/agent_runner_roadmap.py → agent_runner_backlog.py
│   [修改] 路由前缀 /agent-runner/backlog/*；全部端点去 roadmap
├── src/backend/api/routes/agent_runner_lifecycle_agents.py
│   [修改] /agent-runner/roadmap/prds/{encoded_path}/agent-overrides → backlog
├── src/backend/api/app.py
│   [修改] import/include_router 指向 agent_runner_backlog
├── src/backend/api/cli_typer_app.py
│   [修改] roadmap_app → backlog_app；add_typer(name="backlog")
├── src/backend/api/cli_typer_roadmap.py → cli_typer_backlog.py
│   [修改] @backlog_app.command("advance")；command="backlog advance"
├── src/backend/api/cli_parsed_commands/roadmap.py → backlog.py
│   [修改] run_roadmap_advance_command → run_backlog_advance_command
├── src/backend/api/cli_parser.py
│   [修改] argparse 分支 "roadmap" → "backlog"（移除旧分支）
├── src/backend/api/cli.py, src/backend/api/cli_reexports.py
│   [修改] re-export 改名
├── src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md
│   [修改] 随包 operator skill 中的 roadmap 引用（如 "Roadmap page"）改为 "Backlog page"；命令表中如有 roadmap 命令同步为 backlog
│   └── 该文件位于 src/backend 内，是 `rg roadmap` 零命中断言的覆盖范围，必须同步
├── frontend-public/app/(app)/app/roadmap/ → backlog/
│   [重命名/修改] page.tsx 路由与文案
├── frontend-public/components/roadmap/* → frontend-public/components/backlog/*
│   [重命名/修改] prd-detail.tsx / prd-card.tsx / roadmap-*.tsx 等组件及内部符号
├── frontend-public/lib/api/roadmap.ts → backlog.ts
│   [重命名/修改] API client 路径与函数名
├── frontend-public/lib/api/types.ts
│   [修改] RoadmapPrd/RoadmapAutopilotState 等类型 → Backlog*
├── frontend-public/app/layout.tsx
│   [修改] 描述文案去 roadmap
├── tests/
│   [修改] 26 个含 roadmap 的测试与 fixture 对齐新名（含迁移测试新增）
├── docs/（guides/agent-runner.md、api/references.md、architecture/system-design.md 等）
│   [修改] 术语与"路线图（Roadmap）"小节改为 Backlog/待办队列
├── README.md
│   [修改] 术语对齐
├── mkdocs.yml
│   [修改] nav 文案去"Roadmap"（原型文件名保留）
├── ROADMAP.md
│   [修改] 顶部新增一句消歧说明：本文件是战略文档，控制台 Backlog 工作台是 PRD 待办/队列，二者不同
└── src/backend/api/static/console/**, frontend-public/out/**
    [重建] 由构建重新生成，不手工改
```

> 上述文件是起点而非穷尽清单；以 `rg -n "roadmap\|Roadmap" src/backend frontend-public tests docs README.md mkdocs.yml` 的实时结果为准。

### Risk Classification Register

| 变更点 | tier | 决定性维度/覆盖 | 干预 | oracle/gate |
|---|---|---|---|---|
| 仓库级标识符重命名（core/engines/api/前端） | R1 | 机械、限定语义，但有跨组件面 | executor + 架构守卫与全量测试 | rv-4、`just lint --full`、`just test` |
| CLI/API/控制台路由硬改名（移除旧入口） | R2 | 对外契约 / 破坏性变更（外部契约固定区） | human confirm + 契约 oracle | rv-1、决策二 |
| 控制台 SQLite 表迁移 v6→v7 | R2 | 持久化状态 / 迁移（数据库结构固定区） | human confirm + round-trip 迁移 oracle | rv-2、决策三 |
| 控制台路由与页面命名（用户可见） | R1 | 局部、可回滚；用户可见路径变化 | executor + 真实入口 E2E | rv-3 |
| 文档/导航/战略文档消歧 | R0 | 呈现层、无行为 | executor + mkdocs strict | rv-4 |

### Executor Drift Guard

- 实施前运行 `rg -n "roadmap\|Roadmap" src/backend frontend-public tests docs README.md mkdocs.yml config.toml .iar.toml`，把结果作为完整清单的起点。
- 白名单（允许保留 `roadmap`）：`ROADMAP.md`、`docs/prototypes/roadmap-*`（历史原型资产文件）、`tasks/archive/**`（历史 PRD）、`CHANGELOG`/历史公告；其余命中一律改。
- 结束后运行 `rg -n "roadmap\|Roadmap" src/backend frontend-public` 必须零命中；`rg -n "roadmap" docs mkdocs.yml README.md` 只允许白名单命中。
- **实现期澄清（2026-10-05，见 §14）**：功能面零命中的唯一例外是 `console_store.py` 里 v7 迁移读取既有用户库所必需的旧表名字面量（`_BACKLOG_TABLE_RENAMES` 元组与其紧邻注释，共 3 行）。把它们改写成拼接或编码会让迁移更难审计，且不改变"功能面不再有 roadmap 语义"这一意图；文档面的白名单同样包含对战略文档 `ROADMAP.md` 的文件名引用与 `docs/prototypes/roadmap-*` 原型资产路径（`mkdocs.yml` nav 文案已改为 Backlog，仅文件路径保留）。
- 迁移相关的 SQL/方法/表名必须同名同步，避免出现"代码 backlog、SQL 查询 roadmap"的分裂；定向测试覆盖空库与有数据 v6 库两条路径。
- 运行相关命令时注意构建上下文：前端构建在 `frontend-public/`（pnpm workspace 根）；控制台静态产物在 `src/backend/api/static/console/`，由构建生成，勿手工编辑。

### Flow / Architecture Diagram

```text
                        (重命名前)                         (重命名后)
  CLI  iar roadmap advance        ──────►   CLI  iar backlog advance
  API  GET /agent-runner/roadmap/prds  ──►  GET /agent-runner/backlog/prds
  UI   /app/roadmap               ──────►   /app/backlog
        │                                          │
        ▼                                          ▼
  api/routes/agent_runner_roadmap ────►   api/routes/agent_runner_backlog
        │                                          │
        ▼                                          ▼
  core/use_cases/roadmap_*        ────►   core/use_cases/backlog_*
        │  (IBacklogStore)                         │
        ▼                                          ▼
  infrastructure/persistence/console_store
    roadmap_queue / roadmap_settings ──► (v7 RENAME) ──► backlog_queue / backlog_settings

  ROADMAP.md（战略文档）── 不变，仅顶部加消歧说明
```

### ER Diagram

数据模型不变，仅两张表改名（迁移 v7）：

```text
backlog_queue (原 roadmap_queue)
  id INTEGER PK
  repo_id TEXT
  prd_path TEXT
  status TEXT
  trigger TEXT
  started_at, finished_at TEXT
  error_detail TEXT

backlog_settings (原 roadmap_settings)
  repo_id TEXT PK
  max_parallel INTEGER
  default_view TEXT      # timeline | list
  updated_at TEXT
```

### Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 对外入口硬改名——CLI iar backlog 可用且 iar roadmap 移除；HTTP API /agent-runner/backlog/* 可用且旧 /roadmap/* 404
  reviewer: verifier
  real_entry: "真实 CLI：`iar backlog advance --dry-run --repo-id <repo>` 与 `iar roadmap advance --dry-run`；真实 API：`GET /api/v1/agent-runner/backlog/prds` 与 `GET /api/v1/agent-runner/roadmap/prds`"
  expected: "新 CLI 退出 0 并输出计划；旧 CLI 报未知命令/用法错误；新 API 返回数据，旧 API 404"
  mock_boundary: "真实 Typer/argparse CLI 与真实 FastAPI app；GitHub 用记录副作用的 fake，命令行与路由解析不 mock"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "命令名/路由前缀直接来自 CLI 与 FastAPI 路由表；不来自常量转述"
  must_cross: "shell -> Typer/argparse 命令解析；HTTP -> FastAPI router -> core use case"
  forbidden_bypasses: "不得保留 hidden alias/redirect 冒充硬改名；不得只改文档而命令/路径仍用 roadmap"
  fresh_state_probe: "从新进程运行 CLI、从新 app 实例请求 API，确认旧入口真的不存在"
  final_tree_evidence: "定向 CLI/API 测试与真实命令在最终 CLI/路由树运行；命令树或路由变更后重跑"
  negative_control: "运行旧命令 `iar roadmap advance` 与旧路径 `GET /agent-runner/roadmap/prds`"
  expected_fail: "若旧命令仍成功或旧路径仍 200，负控失败"
- id: rv-2
  behavior: 控制台 SQLite v6→v7 迁移把 roadmap_queue/roadmap_settings 重命名为 backlog_* 且数据无损
  reviewer: verifier
  real_entry: "`uv run pytest -o addopts=\"\" tests/ -k 'backlog_migration or console_migration'`；并用手工构造的 v6 库（含 roadmap_* 表与若干行）启动 console 验证"
  expected: "迁移后表名为 backlog_queue/backlog_settings，行数与原值保留；PRAGMA user_version=7；空库与缺表场景安全"
  mock_boundary: "真实 sqlite3 与真实 console_store；不 mock 数据库"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "迁移前后直接查询 sqlite_master 与 SELECT COUNT(*)/关键列值"
  must_cross: "v6 库 -> console_store._migrate -> ALTER TABLE RENAME -> 查询新表名与数据"
  forbidden_bypasses: "不得只改建表语句而丢历史数据；不得静默丢表；不得依赖手工迁移"
  fresh_state_probe: "在新进程/新连接打开迁移后的库查询"
  final_tree_evidence: "迁移测试在最终 console_store 树运行；SCHEMA_VERSION 或迁移逻辑变更后重跑"
  negative_control: "构造一个含 1 行 roadmap_queue 的 v6 库，断言迁移后 backlog_queue 仍有该行"
  expected_fail: "若行丢失或 user_version 未到 7，负控失败"
- id: rv-3
  behavior: 控制台路由改名——/app/backlog 渲染待办工作台且不再出现"路线图/Roadmap"；/app/roadmap 不再提供
  reviewer: human
  real_entry: "真实 console：`just run frontend-public` 或 `just e2e`，浏览器打开 `/app/backlog`"
  expected: "页面渲染 PRD 待办工作台，标题/导航为 Backlog；旧 `/app/roadmap` 不再提供"
  mock_boundary: "真实 Next.js 页面与后端；GitHub 由 API 边界 fake 提供确定性数据"
  tier: R1
  test_layer: e2e
  required_for_acceptance: true
  presentation: "tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/rv-3-backlog-page.png（标注 real UI / fake GitHub boundary）；自检：地址栏为 /app/backlog 且标题显示 Backlog"
- id: rv-4
  behavior: 功能面零 roadmap 残留，且架构、测试、前端构建与文档全链路通过
  reviewer: verifier
  real_entry: "`rg -n \"roadmap|Roadmap\" src/backend frontend-public`（须零命中）&& `just lint --full && just test && cd frontend-public && pnpm typecheck && pnpm build && cd .. && uv run mkdocs build --strict`"
  expected: "功能面零命中；全部命令退出 0；架构守卫与 mkdocs strict 通过，无断链"
  mock_boundary: "各质量门禁既有边界；无行为 mock"
  tier: R0
  test_layer: smoke
  required_for_acceptance: true
```

失败排查：旧入口仍在 → 检查是否误留 alias/redirect 或静态产物未重建；迁移丢数据 → 检查 v7 RENAME 顺序与 `_SCHEMA_VERSION`；前端 404 → 检查 `app/` 路由目录是否 `git mv` 且构建产物重生成；文档断链 → 检查 `mkdocs.yml` nav 文案与原型的相对路径。

### Low-Fidelity Prototype

**Prototype waiver**：本次为纯命名变更，页面布局、组件结构、交互与信息层级均不改变，唯一用户可见差异是路由与文案。按规范，纯前端 plumbing 且无视觉/交互变化可明确豁免新原型；不新建原型文件，`rv-3` 以真实实现截图承担用户可见证据。

### Interactive Prototype Change Log

无（未新增或修改原型文件；`docs/prototypes/roadmap-*` 历史原型文件名按决策保留）。

### External Validation

无（不涉及外部事实；不需要联网研究）。

## 8. Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Notes: 与 `tasks/pending/P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair.md` 存在硬依赖关系：该 PRD 已按改名后的目标态书写（CLI `iar backlog ci`、backlog 路径/符号），其 §8 已把本改名 PRD 记为 `hard` 前置。本改名 PRD 自身不依赖任何 PRD，故 `Depends on tasks/issues: none`、`Gate type: none`。**本改名 PRD 应先执行 `iar issue-from-prd` 建 Issue（写入 `- GitHub Issue:` 链接）**，否则 134008 创建 Issue 时无法物化该依赖。相关 pending PRD `P1-FEAT-20260930-141135`（Agent 机读契约）与 `P1-FEAT-20260930-225000`/`225500` 与本 PRD 独立。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 要看的结果 | 呈递物 | 10 秒自检 |
|---|---|---|
| 控制台 `/app/backlog` 渲染待办工作台且标题为 Backlog | `tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/rv-3-backlog-page.png` | 地址栏为 `/app/backlog`，页面标题/导航显示 Backlog，不再有"路线图/Roadmap" |

注：rv-1、rv-2、rv-4 属于 `reviewer: verifier`，不在人读呈递区逐项展示，仅失败时上报。

注（证据采集限制披露）：截图由无头 Chromium 在真实 `iar console` 进程上采集，页面本体、导航与地址栏 URL 均真实；但无头浏览器不渲染地址栏控件，因此"地址栏为 `/app/backlog`"这一自检由同批证据里的 `page.url()` 与 HTTP 状态行承担（见 `.iar/evidence/rv-3-console-page-real.txt` 的 `浏览器地址栏 URL=` 与 `GET /app/roadmap/ -> 404` 两行）。

### 9.2 Acceptance Evidence Package

1. **Human-Confirmed / R2**：rv-1 CLI/API 硬改名契约、rv-2 SQLite v7 迁移无损；决策一/二/三确认。
2. **R1 human**：rv-3 真实控制台入口的命名呈现。
3. **R0 verifier**：rv-4 功能面零残留 + 架构/测试/构建/文档全链路。

#### Architecture Acceptance

- [x] 重命名为同一批符号替换，未新增 alias/redirect/并行实现；`git mv` 保留文件历史 — 证据：`git status` 中 37 条 rename 条目（`git diff -M --name-only HEAD` 共 90 个文件）+ rv-4 白名单外零命中扫描（无别名层/无并行实现）→ `.iar/evidence/rv-4-zero-hit-and-gates.txt`
- [x] core 未 import `backend.infrastructure`/FastAPI/tomlkit；`just lint --full` 架构守卫通过 — 证据：rv-4 段 [3] `SKIP=check-test-flag just lint --full` 中 `Check architecture layer dependencies....Passed`（跳过 hook 的理由见 §14）→ `.iar/evidence/rv-4-zero-hit-and-gates.txt`
- [x] 四层依赖方向未被破坏；未引入新层、新依赖或新抽象 — 证据：同一次架构守卫通过；改动仅为符号与路径替换，未新增模块或依赖 → `.iar/evidence/rv-4-zero-hit-and-gates.txt`

#### Data Acceptance

- [x] 控制台 SQLite `_SCHEMA_VERSION` 升为 7，新增 v6→v7 `ALTER TABLE ... RENAME` 迁移；空库、缺表、有数据 v6 库三种路径均安全 — 证据：rv-2 段 [2] 真实 sqlite3 造的 v6 库交给真实 `SqliteConsoleStore` 后 `user_version=7`；全新空库与"缺这两张表"的 v6 库补建空表且不产生数据行；定向测试 `test_backlog_migration_creates_missing_legacy_tables` → `.iar/evidence/rv-2-store-migration.txt`
- [x] 迁移后表名为 `backlog_queue`/`backlog_settings`，原有行数与关键列值保留 — 证据：rv-2 `sqlite_master` 只剩新表名，逐列读回值与迁移前一致，`list_backlog_queue('keda')` 读到 2 条、`get_backlog_settings('keda')` 读到 max_parallel=3 → `.iar/evidence/rv-2-store-migration.txt`
- [x] 所有 SQL 语句、方法名、表名同步去 roadmap，无"代码 backlog / SQL roadmap"分裂 — 证据：rv-4 `rg -n -i roadmap src/backend frontend-public` 白名单外零命中，唯一残留是迁移必须读取的 3 行旧表名字面量/注释（口径澄清见 §7 实现期澄清与 §14）→ `.iar/evidence/rv-4-zero-hit-and-gates.txt`

#### API / CLI Acceptance

- [x] `iar backlog advance` 可用，`iar roadmap` 报未知命令（无 alias/redirect）— 证据：rv-1 真实 CLI `iar backlog advance --dry-run` 退出 0 并打印调度计划，`iar roadmap advance` 退出 2 且 `Error: No such command 'roadmap'.` → `.iar/evidence/rv-1-cli-real.txt`
- [x] `/api/v1/agent-runner/backlog/*` 全部端点可用，旧 `/roadmap/*` 返回 404；`agent-overrides` 子路由同步 — 证据：rv-1 真实 uvicorn 上 `GET /api/v1/agent-runner/backlog/prds` 返回 200 含真实 PRD、`/roadmap/prds` 返回 404；rv-2 真实 `iar console` 进程上 `/backlog/settings` 200、`/roadmap/settings` 404；agent-overrides 见 `src/backend/api/routes/agent_runner_lifecycle_agents.py:247,289` → `.iar/evidence/rv-1-api-real.txt`、`.iar/evidence/rv-2-console-migration-real.txt`
- [x] `cli_parser.py` 的 argparse 分支同步改名，Typer 与 argparse 命令名一致 — 证据：rv-1 走真实 `build_parser()`：`roadmap advance` 抛 SystemExit(2)（invalid choice），`backlog advance` 产出 `command='backlog advance'` → `.iar/evidence/rv-1-cli-real.txt`

#### Frontend Acceptance

- [x] `frontend-public` 路由目录、`components/*`、`lib/api/*`、`Roadmap*` 类型全部改为 backlog；`just run frontend-public` 构建通过 — 证据：`git mv` 后为 `app/(app)/app/backlog/`、`components/backlog/*`、`lib/api/backlog.ts`、`Backlog*` 类型；`pnpm --dir frontend-public typecheck`（tsc --noEmit）与 `just console-sync`（`next build` + 静态产物同步）通过 → `.iar/evidence/rv-4-zero-hit-and-gates.txt`
- [x] `/app/backlog` 渲染待办工作台，页面标题/导航为 Backlog；`/app/roadmap` 不再提供 — 证据：rv-3 隔离 HOME 启动真实 `iar console` 进程 + 真实 Chromium 打开 `/app/backlog/` 返回 200，h2 仅 `["Backlog"]`，导航 `Backlog => /app/backlog/`，工作台渲染 7 个真实 PRD；同一浏览器 `/app/roadmap/` 返回 404 → `.iar/evidence/rv-3-console-page-real.txt`、`.iar/evidence/rv-3-backlog-page.png`
- [x] `app/layout.tsx` 描述文案去 roadmap — 证据：rv-4 前端零命中扫描覆盖该文件 → `.iar/evidence/rv-4-zero-hit-and-gates.txt`

#### Documentation Acceptance

- [x] `docs/`（guides/api/architecture）、`README.md`、`mkdocs.yml` nav 文案对齐 Backlog，无断链 — 证据：`uv run mkdocs build --strict` 通过；文档面残留仅历史原型资产路径与战略文档 `ROADMAP.md` 文件名引用（白名单口径见 §14）→ `.iar/evidence/rv-4-zero-hit-and-gates.txt`
- [x] 随包 `iar-operator` skill（`src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md`）中的 roadmap 引用（如 "Roadmap page"）改为 backlog；`rg -n "roadmap|Roadmap"` 在该文件零命中 — 证据：该文件现为 `Open the Backlog page`，全目录 `roadmap` 零命中 → `.iar/evidence/rv-4-zero-hit-and-gates.txt`
- [x] `ROADMAP.md` 顶部新增一句消歧说明，明确其战略文档身份与 Backlog 工作台的区别；正文与里程碑命名不变 — 证据：`ROADMAP.md:3-7` 新增消歧 blockquote，正文里程碑名未改 → `ROADMAP.md`
- [x] `uv run mkdocs build --strict` 通过 — 证据：rv-4 段 [6] `(gate exit=0)`，无断链 → `.iar/evidence/rv-4-zero-hit-and-gates.txt`

#### Validation Acceptance

- [x] rv-1 至 rv-4 全部通过，证据按 `rv-<n>-<slug>.<ext>` 归入本 PRD evidence 目录 — 证据：四个 rv 脚本均 `RESULT: PASS (failures=0, mode=real-entry)`，清单为 `.iar/evidence/evidence.json`，副本归入 `tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/`
- [x] rv-1/rv-2 含负控且按预期变红；`rg -n "roadmap|Roadmap" src/backend frontend-public` 零命中 — 证据：rv-1/rv-2/rv-3/rv-4 各有以 `RESULT: FAIL` 结束的 `*-negative-control.txt`（旧入口重新可用、未迁移库、恢复旧路由目录、改名前 636 行命中/50 文件、门禁环境未隔离时 6 failed）；功能面白名单外零命中见 rv-4 → `.iar/evidence/rv-1-cli-negative-control.txt`、`.iar/evidence/rv-2-migration-negative-control.txt`、`.iar/evidence/rv-3-console-route-negative-control.txt`、`.iar/evidence/rv-4-zero-hit-and-gates-negative-control.txt`、`.iar/evidence/rv-4-gate-env-negative-control.txt`
- [x] 功能行为与重命名前逐项等价（定向测试或快照对比），确认无行为漂移 — 证据：全量套件按 runner 门禁命令 `just test all` 在其真实环境执行，`2888 passed, 1 skipped`；真实入口上逐项核对改名前后同一行为（CLI 调度计划、迁移前后逐列等值、工作台 7 个 PRD 渲染与 Autopilot 状态）→ `.iar/evidence/rv-4-zero-hit-and-gates.txt`（段 [4]）、`.iar/evidence/rv-1-cli-real.txt`、`.iar/evidence/rv-2-console-migration-real.txt`、`.iar/evidence/rv-3-console-page-real.txt`
- [x] 门禁证据与 runner 的验证命令同源，且测试对 agent 注入的配置环境自洽（不再靠清洗环境才绿）— 证据：rv-4 段 [4] 直接跑 `just test all`，未剥离 `IAR_CONFIG`，`2888 passed, 1 skipped`；把 3 个测试文件还原为修复前（HEAD）版本后，同一批用例在真实环境下 `6 failed, 4 passed`，即上一轮 runner 门禁判红的那 6 项 → `.iar/evidence/rv-4-zero-hit-and-gates.txt`、`.iar/evidence/rv-4-gate-env-negative-control.txt`

#### Delivery Readiness

- [x] 推荐目标态全量实现，无"先改一半、旧名留兼容"的拆分或隐藏兼容层 — 证据：旧 CLI 命令、旧 API 前缀、旧控制台路由均直接移除（rv-1/rv-3 探测为未知命令 / 404），迁移为一次性 `RENAME` 而非双写或并行表 → `.iar/evidence/rv-1-api-real.txt`、`.iar/evidence/rv-3-console-page-real.txt`
- [x] 完成消息逐字携带 §9.1 人读呈递区的全部内容与实际呈递物 — 证据：交付说明逐字复述 §9.1 表格与 `tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/rv-3-backlog-page.png`；runner 的 commit proxy 把 `commit_message` 压成单行并截断至 200 字符（`sanitize_commit_message`），故 §9.1 全文载于交付说明与 `tasks/evidence/` 证据副本，commit 标题携带呈递物文件名 `rv-3-backlog-page.png` 与 `/app/backlog` 自检结论
- [~] 独立 verifier Agent 审查通过 — runner-owned gate: verifier review
- [~] PRD 归档至 tasks/archive/ — runner-owned gate: archive

#### Human-Confirmed

- [ ] 决策一确认：功能整体正名为 Backlog，`ROADMAP.md` 战略文档仅加消歧说明、内容与里程碑命名不变
- [ ] 决策二确认：硬改名、旧 CLI 命令/API 路径/控制台路由直接移除（破坏性对外变更），不留兼容别名
- [ ] 决策三确认：控制台 SQLite 新增 v7 迁移重命名 `roadmap_queue`/`roadmap_settings` 为 `backlog_*` 且保留数据
- [ ] §9.1 人读呈递物均已查看并接受

## 10. Functional Requirements

- **FR-1**：后端 core/engines 的 roadmap 标识符必须全部改为 backlog——模型（`RoadmapPrd` 等）、用例模块与函数（`roadmap_actions`、`advance_roadmap_queue`、`start_global_roadmap` 等）、端口（`IRoadmapStore`、`RoadmapQueueEntry`、`RoadmapSettingsEntry`）与工厂（`create_roadmap_store`）。
- **FR-2**：CLI 必须硬改名为 `iar backlog`（含 `advance`）；`iar roadmap` 不再可用，不留 hidden alias 或 redirect。
- **FR-3**：HTTP API 路径必须由 `/api/v1/agent-runner/roadmap/*` 改为 `/api/v1/agent-runner/backlog/*`（含 agent-overrides 子路由），旧路径不再提供。
- **FR-4**：控制台路由必须由 `/app/roadmap` 改为 `/app/backlog`，旧路由不再提供。
- **FR-5**：`frontend-public` 的路由目录、`components/roadmap/*`、`lib/api/roadmap.ts` 与 `Roadmap*` 类型必须改为 backlog，调用点同步。
- **FR-6**：控制台 SQLite 必须新增 v7 就地迁移，用 `ALTER TABLE ... RENAME TO` 把 `roadmap_queue`/`roadmap_settings` 改为 `backlog_queue`/`backlog_settings` 并保留数据；`_SCHEMA_VERSION` 升为 7；所有 SQL 与方法名同步。
- **FR-7**：`docs/`、`README.md`、`mkdocs.yml` 的术语必须对齐 Backlog；`uv run mkdocs build --strict` 通过。
- **FR-8**：功能面（`src/backend`、`frontend-public`）在 `rg -n "roadmap"` 下必须零命中；测试与架构守卫同步更新并通过 `just lint --full` 与 `just test`。
- **FR-9**：`ROADMAP.md` 战略文档内容与里程碑命名保持不变，仅在顶部新增一句消歧说明，指明其与控制台 Backlog 工作台的不同。

## 11. Non-Goals

- 不改变任何功能行为（重命名不得引入或删除能力、不得调整调度/队列语义）。
- 不修改 `ROADMAP.md` 的战略内容与里程碑命名（仅加一句消歧）。
- 不重命名历史原型资产文件（`docs/prototypes/roadmap-*`）与 `tasks/archive/` 中历史 PRD 的文本引用。
- 不保留 CLI/API/控制台路由的兼容别名、重定向或双写。
- 不引入 Alembic 或其他新迁移框架；复用既有 `PRAGMA user_version`。
- 不改 `frontend-admin/`；不新增第三方依赖。

## 12. Risks And Follow-Ups

- **破坏性对外变更**：`iar roadmap`、旧 API 路径、旧控制台书签在合并后立即失效，非 keda 仓库内脚本的调用方需自行迁移；本 PRD 已按用户决策接受该风险，不提供兼容层。若后续出现外部依赖，另立兼容 PRD。
- **迁移正确性**：表 RENAME 若在异常中断或重复执行时处理不当可能丢数据；缓解是 v7 步幂等（依 `user_version` 只跑一次）、对空/缺表安全，并有 rv-2 的负控与 round-trip 覆盖。
- **全仓遗漏**：`roadmap` 命中面广（约 186 文件，含 `docs/prototypes/` 历史原型与 `tasks/` 归档 PRD；改名前功能面 `src/backend` + `frontend-public` 实测 636 行 / 50 文件，见 rv-4 负控），可能漏改某处引用；缓解是"先 `rg` 列清单、后 `rg` 零命中收口"，并靠 `just lint --full`/`just test`/`pnpm build` 暴露编译期遗漏。
- **与 134008 的命名协调**：该 PRD 已按改名后的目标态书写，并把本 PRD 记为 `hard` 前置（见 §8 Notes）；若协调失败，可能出现 `iar roadmap ci` 与 `iar backlog` 并存，需在任一方落地时统一。
- **Playwright e2e 规格未执行**：本次改名同步了 `tests/playwright-e2e/` 的 5 个 smoke 规格文件名与其中 4 个 workflow 规格的路由引用，但这批规格需要已登录且常驻的控制台进程才能跑，交付期未执行；替代覆盖是 rv-3 在真实 `iar console` 进程上用真实 Chromium 验证 `/app/backlog`（含 404 探测）与 rv-4 的前端类型检查/构建，规格内引用的路由已静态核对为存在。后续在常驻控制台环境跑一次 `npm run test:smoke`（`tests/playwright-e2e`）即可补齐。
- **agent 注入的 `IAR_CONFIG` 与"按 cwd 发现配置"的用例长期冲突**（返修轮发现，非本 PRD 引入）：IAR agent 会把父进程生效的 `config.toml` 以 `IAR_CONFIG` 注入子进程，在 worktree 里执行时它指向**主检出**目录，而该变量在配置解析链上的优先级高于仓库根发现，于是任何假设"没有 `IAR_CONFIG`"的用例都会拿到别的检出目录的配置。本轮只对受影响的 3 个文件做隔离（与仓库既有 `monkeypatch.delenv("IAR_CONFIG")` 约定一致），不做全局 conftest 清洗（那会改变全部用例的共同前提）。真正的收口应在 `iar` 侧：要么让 runner 在执行 `verification_commands` 前不注入该变量，要么把它指向当前 worktree 自己的 `config.toml`。属独立 issue，不在本改名 PRD 范围内。

## 13. Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 功能命名 | 正名为 `Backlog` | 维持 `Roadmap` | 该功能是以 PRD 文件为事实源的待办/队列工作台，`Roadmap` 承诺战略方向且与 `ROADMAP.md` 撞名。 |
| D-02 | 战略文档处置 | `ROADMAP.md` 保留，仅加消歧说明 | 一并改名战略文档 | "roadmap" 一词的本来正确用法是战略文档，问题在功能名而非文档名。 |
| D-03 | 对外兼容策略 | 硬改名、移除旧入口 | 保留 hidden alias/redirect | 用户确认；避免长期背负两套命名与第二事实源。 |
| D-04 | 持久化表名 | 新增 v7 迁移重命名表 | 只改代码标识符、表名保留 | 用户确认；命名必须端到端一致，否则 schema 与代码分裂。 |
| D-05 | 重命名方式 | 同一批符号 `git mv`/替换 | 新增 `backlog_*` 与 `roadmap_*` 并存 | 避免并行实现与重复逻辑；保证引用唯一。 |
| D-06 | 历史资产处置 | 原型文件与归档 PRD 文本不改 | 全量改历史引用 | 历史记录改了会破坏可追溯性与既有链接。 |

## 14. Change Log

### 初始创建：Roadmap 功能正名为 Backlog

- Type: feature-scope / refactor
- Before: 仓库没有本 PRD；`roadmap` 同时指根目录 `ROADMAP.md` 战略文档与控制台 `/app/roadmap` + `iar roadmap` + `/agent-runner/roadmap/*` 功能，`rg roadmap` 混淆两个语义域。
- After: 新增本 PRD，定义一次行为保持的端到端重命名（后端标识符、CLI、HTTP API、控制台路由、前端组件/类型、SQLite 表 v6→v7 迁移、文档），并确认 `ROADMAP.md` 战略文档保留、仅加消歧说明。
- Reason: 用户指出"Roadmap"命名有词义问题并同意正名为 Backlog；`ROADMAP.md` 不动。
- Impact: 引入破坏性对外变更（旧 CLI/API/路由移除）与一次数据库迁移；不改变功能行为。
- Review: 用户已就两项关键范围（硬改名、迁移重命名表）作出选择；本 PRD 待人工确认三项决策后开工。

### 实现期修订（2026-10-05）：零命中 oracle 的范围澄清与迁移实现细节

- Type: validation-scope / implementation-detail
- Before: §7 Drift Guard 与 §9.2 把收口条件写成"`rg -n "roadmap\|Roadmap" src/backend frontend-public` 必须零命中"，未说明 v7 迁移自身必须知道旧表名；§5/§7 也未点名原型截图资产与新增的定向迁移测试。
- After: 三点落地并记录，功能行为与 §10/§11 范围不变：
  1. **零命中 oracle 加一条必要例外**（见 §7 Drift Guard 的"实现期澄清"）：`console_store.py` 的 `_BACKLOG_TABLE_RENAMES` 元组与其紧邻注释共 3 行保留 `roadmap_queue`/`roadmap_settings` 字面量——这是就地重命名既有用户库的输入，去掉它迁移就无法读取 v6 库。功能面其余部分（含 `frontend-public` 与控制台构建产物）字面零命中。
  2. **文档面白名单细化**：`mkdocs.yml` nav 文案已改为 Backlog，但条目路径仍是历史原型文件 `prototypes/roadmap-*.md`；`docs/ai-standards/tooling.md` 等对战略文档 `ROADMAP.md` 的文件名引用按 D-02/D-06 保留。
  3. **迁移实现与测试**：v7 步用 `sqlite_master` 探测做幂等（旧表存在且新表缺席才 `RENAME`），并补建缺失的空表以覆盖"旧库从未建过这两张表"的路径；新增定向测试 `test_backlog_migration_renames_roadmap_tables_and_keeps_rows`、`test_backlog_migration_creates_missing_legacy_tables`，同时让 v3 兼容 fixture 刻意保留旧表名，否则迁移路径不会被真正演练。`tests/playwright-e2e/tests/workflows/prototype-screenshots.spec.ts` 继续输出 `roadmap-real.png`，因为 `docs/prototypes/lifecycle-agent-matrix.html` 直接引用该历史原型资产名（D-06）。
- Reason: 收口断言若按字面执行会要求把迁移输入也改成不可读的形式；原型资产与历史引用改名会破坏可追溯性。二者都属于 PRD 原意的"功能命名清零"，不是行为或范围变更。
- Impact: 只影响 rv-4 的判定口径与测试清单；对外契约（CLI/API/路由/表名）与 §10 FR-1..FR-9 不变。
- Review: **待人工确认**（oracle 范围澄清，未削弱任何用户可见、安全或范围要求；负控与正向证据均按澄清后的口径采集，见 `.iar/evidence/`）。

### 实现期修订（2026-10-05）：rv-4 门禁执行方式的三项披露

- Type: validation-detail
- Before: §7/§9.2 只写"架构守卫 `just lint --full` 通过、`just test` 全量通过"，未说明本 worktree 里这两条命令的实际执行条件。
- After: rv-4 证据（`.iar/evidence/rv-4-zero-hit-and-gates.txt` 第 [3]/[4]/[4b] 段）按以下方式采集，功能范围与判定口径不变：
  1. **`just lint --full` 跳过 `check-test-flag` 一个 hook**（`SKIP=check-test-flag`）：该 hook 只校验"当前 staged 树是否已被上一次 `just test` 覆盖"，而 runner 明令禁止 agent 执行 `git add`，本 worktree 改动全程保持未 staged，hook 会因 staged 树为空而报工作流状态错误（其自身输出亦确认内容未变化、已被同脚本的 `just test` 覆盖）。其余 hook（ruff/ruff-format/架构分层守卫/复用/PRD checklist/guard test 等）全部照常执行并通过。
  2. **删除本地质量门禁缓存标记后跑门禁**：脚本先 `rm` 掉 `$(git rev-parse --git-dir)/.last_tested_commit` 与 `.last_linted_commit` 并置 `CI=1`，否则 `just test`/`just lint --full` 会命中 warm-path 直接输出"flag 有效，跳过"，证据将不成立。
  3. **补跑一次关掉 testmon 的全量套件**：`pytest` 配置里的 `--testmon` 会把增量运行收窄（本次 `CI=1 just test` 内 collected 0 items），因此 rv-4 额外执行 `uv run pytest tests/ -q --no-testmon`，结果为 `2888 passed, 1 skipped`，用它承担"全量测试通过"的判定。
  另外所有证据命令统一 `env -u IAR_CONFIG`：本机 shell 导出的 `IAR_CONFIG` 指向主检出目录，不剥掉会让 `iar` 解析到 worktree 之外的仓库。（**该做法已被 §14「返修复核」条目作废**：清洗环境让 rv-4 与 runner 门禁不再是同一个环境，掩盖了 6 个用例的真实判红；现在门禁按真实环境跑，隔离改由测试自身完成。）
- Reason: 这三项都是 runner 约束与仓库既有门禁缓存机制的交互结果，不披露会让"门禁通过"的证据看起来比实际更强。
- Impact: 只影响 rv-4 的执行细节与可复现方式；未跳过任何实质检查，未削弱 §9.2 的收口条件。
- Review: **待人工确认**（执行方式披露；若认为 `check-test-flag` 不可跳过，可要求以 runner 的 `verification_commands` 复核一次）。

### 实现期修订（2026-10-05）：§5 未点名的额外受影响文件

- Type: scope-disclosure
- Before: §5「当前相关路径」点名了 core/infrastructure/engines/api/CLI/前端/文档的主干文件，未覆盖实现中实际同样引用旧符号或旧路径的其余文件（下列各条）。
- After: 一并改名，仍属同一批符号替换，不改变行为与 §10/§11 范围：
  - 后端引用点：`core/use_cases/agent_runner_factory.py`、`agent_runner_lifecycle.py`、`agent_runner_orchestration_runtime.py`、`agent_runner_token_stats.py`、`prd_content_reader.py`、`run_agent_daemon.py`、`api/cli_parsed_commands/__init__.py`、`api/cli_parsed_commands/runner.py`（re-export 与命令分发的下游引用）。
  - 前端与配置：`components/layout/app-sidebar.tsx`、`components/agent-runner/repository-agent-matrix-sheet.tsx`、`app/(app)/app/settings/page.tsx`、`app/(app)/app/stats/page.tsx`、`lib/api/client.ts`、`lib/api/lifecycleAgents.ts`、`next.config.ts`（导航项、API 前缀与 rewrite 规则）。
  - CI：`.github/workflows/ci.yml`、`.github/workflows/cd.yml`（引用改名后的测试文件路径与 CLI 命令）。
  - 测试：`tests/conftest.py`、`tests/playwright-e2e/README.md` 与 `tests/playwright-e2e/tests/smoke/` 下 5 个 `backlog*.spec.ts`（原 `roadmap*.spec.ts` 随功能改名）、`tests/workflows/` 下 4 个 spec 的路由引用；Python 侧 `tests/test_backlog_*.py` 9 个文件随被测模块改名。
- Reason: 这些文件都是旧符号/旧路径的直接引用点，留下任何一处都会让 §9.2 的"零残留"与"无代码 backlog / SQL roadmap 分裂"不成立。
- Impact: 仅扩大同一次替换的文件集合；未新增功能、未改变对外契约之外的行为。
- Review: 自记（§7 living guide 允许的发现型扩展，范围与 §10 FR 一致）。

### Final Reconciliation（2026-10-05）：执行侧交付完成，转人工验收

- Type: reconciliation
- Before: 验收清单全部为 `- [ ]`，banner 为「⬜ 未开工」；rv-4 尚未跑完最终门禁。
- After: rv-1/rv-2/rv-3/rv-4 四份脚本在真实入口上均 `RESULT: PASS (failures=0, mode=real-entry)`，各自负控以 `RESULT: FAIL` 变红；清单中 21 项执行侧项逐条勾选并标注证据文件，剩余仅 4 项 `Human-Confirmed`（决策一/二/三 + §9.1 呈递物过目）与 2 项 runner-owned `[~]`（verifier review、归档）；banner 相应改为 🧍 待人工验收。证据清单 `.iar/evidence/evidence.json`（4 项，含 stdout 断言）与全部 `rv-*` 证据已镜像到 `tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/`。最终门禁实测（返修轮复测口径，见下一条 §14 条目）：`SKIP=check-test-flag just lint --full` 全 hook Passed、runner 门禁命令 `just test all` 在其真实环境 `2888 passed, 1 skipped`、`pnpm --dir frontend-public typecheck` + `just console-sync` + `uv run mkdocs build --strict` 均 exit 0。改动集为 `git diff -M --name-only HEAD` 的 90 个文件（其中 37 条 rename）；证据目录新增三份进提交的 `.md` 文本报告（验证计划、证据报告、人工验收清单），其余原始日志与图片按 `.gitignore` 只留本机。
- Reason: 归档只代表执行侧交付完成，人工验收由人确认；本轮不自行 `git mv` 到 `tasks/archive/`。
- Impact: 文档状态与验收口径对齐；未削弱任何用户可见、安全、范围或真实验证要求（`check-test-flag` 单 hook 跳过与 testmon 补跑已在上一条目披露，e2e 规格未执行已记入 §12）。
- Review: **待人工确认**（4 项 `Human-Confirmed` 未勾选，§14 两条实现期修订亦标记待确认）。
- Interpretation: §1 行为样例 7 行与最终交付逐项一致，无需回改：新 CLI 可用/旧 CLI 报未知命令、新 API 200/旧 API 404、`/app/backlog` 渲染 Backlog 工作台且 `/app/roadmap` 不再提供、v6 含数据库无损迁移到 v7、功能面 `roadmap` 零命中（白名单见 §7 实现期澄清）、全量门禁绿。返修轮只动测试的环境隔离与证据采集方式，未新增行为样例行。
- Public behavior and contracts: 对外契约最终态为 CLI `iar backlog`（子命令 `advance` 等）、`/api/v1/agent-runner/backlog/*`（含 `agent-overrides` 子路由）、控制台 `/app/backlog`，旧入口一律移除且无 alias/redirect；控制台 SQLite `user_version` 7、表名 `backlog_queue`/`backlog_settings`；随包 `iar-operator` skill 与 `docs/` 同步该命名。返修轮未改变上述任何一项。
- Related PRD status: `P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair` 仍为 pending 且以本 PRD 为 `hard` 前置（§8），本 PRD 交付后它才获得 `iar backlog ci` 与 backlog 路径的目标态基线；其余 pending PRD 与本 PRD 独立。
- Requirements and risks: §10 FR-1..FR-9 全部落地并有证据；§11 非目标（无行为变更、不改历史资产、不留兼容别名）未被破坏；§12 风险项中"破坏性对外变更""迁移正确性""全仓遗漏""与 134008 命名协调"均已由 rv-1..rv-4 覆盖，唯"Playwright e2e 规格未执行"作为已披露的后续项保留。返修新增风险见下一条 §14 条目（agent 注入 `IAR_CONFIG` 与"按 cwd 发现配置"用例的长期冲突）。

### 返修复核（2026-10-05）：runner 门禁判红的根因是环境注入，rv-4 证据改为与门禁同源

- Type: test / evidence / validation-fidelity
- Before: runner 的提交前门禁 `just test all` 判红（6 failed / 2882 passed），而上一轮 rv-4 证据却记录"全量套件 2888 passed"。原因是rv-4 脚本把所有门禁命令都包在 `env -u IAR_CONFIG` 里跑（§14「rv-4 门禁执行方式的三项披露」第 4 条），清洗掉了 IAR agent 注入给测试进程的环境变量；而 runner 自己的门禁不清洗，于是证据环境与门禁环境不是同一个环境。该变量指向**主检出目录**的 `config.toml`，它在配置解析链上的优先级高于仓库根发现，因此 4 个 `tests/test_preview_env_script.py` 用例（写入 `tmp_path/config.toml` 的 `[preview]` 被外部文件整份顶掉，报 `registry_namespace 为空`）、`test_agent_runner_reads_root_config_toml`（断言命中的是主检出的 `config.toml`）与 `test_iar_init_does_not_pollute_target_repo_config_toml`（前提"没有 IAR_CONFIG"被破坏，registry 落进外部配置而非 fake home）共 6 项在真实门禁下必然判红。
- After: 两件事分别修：
  1. **测试自身对注入环境自洽**（与仓库既有约定一致——同类用例本来就用 `monkeypatch.delenv("IAR_CONFIG", raising=False)` 自我保护）：`tests/test_agent_config_consistency.py::test_agent_runner_reads_root_config_toml` 与 `tests/test_agent_runner_init.py::test_iar_init_does_not_pollute_target_repo_config_toml` 各加一条 `delenv`；`tests/test_preview_env_script.py::_run_script` 从继承环境里 `pop("IAR_CONFIG")`，与同一 helper 里已有的 `GITHUB_ENV` 消毒同构（注释同处）。未新增/删除/跳过任何用例，也未改动被测源码行为。
  2. **rv-4 证据改为与门禁同源**：`.iar/evidence/scripts/rv-4-gates.sh` 去掉 `env -u IAR_CONFIG` 清洗，并把"全量测试通过"的判定改由 runner 实际使用的命令 `just test all` 承担（它内部即"全量 lint + `--no-testmon` 全量 pytest"），不再用 `CI=1 just test` + 手工 `uv run pytest tests/ -q --no-testmon` 这对被清洗过的近似命令；段号因此为 [3] lint、[4] `just test all`、[5] 前端、[6] mkdocs。
  重跑结果：`just test all` 在真实 agent 环境（`IAR_CONFIG` 仍指向主检出）`2888 passed, 1 skipped`，rv-4 全脚本 `RESULT: PASS (failures=0, mode=real-entry)`；负控 `.iar/evidence/rv-4-gate-env-negative-control.txt` 用还原为修复前（HEAD）的同批测试文件复现 `6 failed, 4 passed`，证明该项确实会因缺少隔离而变红。证据清单 `.iar/evidence/evidence.json` 同步修正：`stdout_assertions[].source` 原先填的是证据文件名（契约只接受 `stdout`/`stderr`，会让清单解析失败），现改为 `source: stdout` 并把断言收敛到各项 `command` 的真实标准输出，负控期望移入 `negative_control`/`expected_fail` 叙述；`severity` 归一为 `high`；rv-3 补 `expected_artifacts`（PNG 尺寸下限与 key_claim）。清单已用仓库自身的 `load_evidence_manifest()` 复验通过（4 项，文件齐全）。
- Reason: "清洗环境才绿"的证据不等于门禁通过：门禁命令的真实环境是 agent 注入后的环境，证据必须与之同源，否则每轮自动执行都会在同一批用例上反复失败。修测试的注入自洽性而不是修门禁环境，是因为被测行为（按 cwd 发现仓库根配置）本身没变，变的是执行者所在环境。
- Impact: 不改变任何用户可见行为、对外契约、§10 FR 与 §11 范围；§9.2 两条 Validation Acceptance 证据改指 `just test all` 与新负控文件，Architecture Acceptance 的 lint 段号改为 [3]；§14「rv-4 门禁执行方式的三项披露」第 4 条（统一 `env -u IAR_CONFIG`）自本条目起作废。风险登记：IAR 侧把 `IAR_CONFIG` 注入 agent 的测试进程仍是一个会持续给"按 cwd 发现配置"的用例出难题的执行环境特性，本轮只在受影响的 3 个文件上做隔离，未做全局 conftest 清洗（全局清洗会改变 2882 个用例的共同前提，收益不抵风险）。
- Review: 自记（返修性质：修复门禁判红与清单格式，未削弱任何用户可见、安全、范围或真实验证要求；门禁与 rv-4 均按真实环境重跑，可复现命令见 §9.2 与 `.iar/evidence/scripts/`）。

### 返修复核（2026-10-05）：补齐进提交的证据文本报告，并校正改动集计数

- Type: evidence / documentation
- Before: `tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/` 只有 worktree 本地的原始日志、截图与 `evidence.json` 镜像，没有 Machine Contract §4 要求进提交的那三份 `.md` 文本报告；人审在 PR 上读不到证据叙述（`*.txt`/`*.png` 被 `.gitignore` 排除，只有 `*.md` 会进历史）。同时 §9.2 与 Final Reconciliation 记的改动集计数（"33 条 rename / 87 个文件"）是返修前的测量，返修轮把 3 个测试文件纳入改动后已不等于真实值。
- After: 新增三份报告并让仓库自身门禁认可——`….verification-plan.md`（复现环境、oracle→真实执行入口表、6 条负控做法、可复跑命令、口径与已披露限制）、`….evidence-report.md`（以「人审导航」开头，就地嵌入 `![…](rv-3-backlog-page.png)`，逐 rv 原样摘出记录原文，含负控表与「绑定最终代码树」一节）、`human-review-checklist.md`（4 项 `Human-Confirmed` 各自的确认内容、PRD 原话、判错代价与证据指向）。计数校正为 `git diff -M --name-only HEAD` 的 90 个文件、其中 37 条 rename；§9.2 Architecture Acceptance 首条与本条同步。核对通过：`bash scripts/shared/just/check_prd_evidence.sh <prd> <worktree>` 输出 `✅ 证据报告已就地嵌入全部 1 张证据图片` + `✅ Frontend changes detected and visual evidence found`；`check_prd_acceptance_checklist.py --check-provided --archive-ready` 输出 `PASS`。
- Reason: 呈递物没有落到评审者真能读到的载体上，等于证据只在 executor 本机存在；PRD §9.1 的 10 秒自检要求人能在不打开本地目录的情况下看完命名结论。
- Impact: 只新增 `tasks/evidence/` 下的文本报告与两处计数校正，不改变任何用户可见行为、对外契约、§10 FR 与 §11 范围，也不改动 rv-1..rv-4 的判据本身。`check_prd_evidence.sh` 的嵌图检查自本报告存在起才真正生效（此前因报告缺失而直接返回），后续新增静态图必须继续嵌图。
- Review: 自记（呈递载体补齐；4 项 `Human-Confirmed` 仍保持空框，等 reviewer 回答，见 `human-review-checklist.md`）。

### 返修复核（2026-10-05）：daemon 用例被本机在跑的 keda daemon 单实例锁判红

- Type: test
- Before: `tests/test_agent_runner_cli.py::test_main_daemon_with_repo_id_does_not_default_to_all` 直接跑 `main(["daemon", "--repo-id", "keda"])`，单实例锁落在真实 `~/.iar/daemon-locks/keda.lock`；本机只要有该仓库的 daemon 在跑（本轮即 runner 自己的 daemon，PID 存活），锁获取就被拒、`main` 返回 1，runner 门禁 `just test all` 判红（1 failed / 2887 passed）。这是上一轮"隔离改由测试自身完成"口径的同类漏网：受影响的恰好只有这一条用真实 repo_id `keda` 的用例。
- After: 该用例沿用同文件既有先例（`test_main_daemon_cwd_matches_enabled_single_repo` 的做法），patch `backend.api.cli.acquire_daemon_locks` / `release_daemon_locks` 并注释说明理由——本用例断言的是 `--repo-id` 路由（不默认 `--all`），锁行为本身由 `tests/test_daemon_single_instance.py` 专项覆盖。未改动任何被测源码行为、未增删用例。修复后 runner 门禁命令 `just test all` 在本 worktree 真实环境复跑通过：`2888 passed, 1 skipped`。
- Reason: 路由断言不应依赖"本机恰好没有在跑的 daemon"这一机器状态；否则每次自动执行轮到有 daemon 在跑就必红。
- Impact: 仅测试隔离；对外契约、§10 FR、rv-1..rv-4 判据均不变。
- Review: 自记（返修性质：修复门禁判红，未削弱任何用户可见、安全、范围或真实验证要求）。
