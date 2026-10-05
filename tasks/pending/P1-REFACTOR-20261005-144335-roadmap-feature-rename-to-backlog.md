# PRD: Roadmap 功能正名为 Backlog

- GitHub Issue: https://github.com/ZataZhang/keda/issues/196

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ⬜ **验收状态**：未开工。
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

### 9.2 Acceptance Evidence Package

1. **Human-Confirmed / R2**：rv-1 CLI/API 硬改名契约、rv-2 SQLite v7 迁移无损；决策一/二/三确认。
2. **R1 human**：rv-3 真实控制台入口的命名呈现。
3. **R0 verifier**：rv-4 功能面零残留 + 架构/测试/构建/文档全链路。

#### Architecture Acceptance

- [ ] 重命名为同一批符号替换，未新增 alias/redirect/并行实现；`git mv` 保留文件历史
- [ ] core 未 import `backend.infrastructure`/FastAPI/tomlkit；`just lint --full` 架构守卫通过
- [ ] 四层依赖方向未被破坏；未引入新层、新依赖或新抽象

#### Data Acceptance

- [ ] 控制台 SQLite `_SCHEMA_VERSION` 升为 7，新增 v6→v7 `ALTER TABLE ... RENAME` 迁移；空库、缺表、有数据 v6 库三种路径均安全
- [ ] 迁移后表名为 `backlog_queue`/`backlog_settings`，原有行数与关键列值保留
- [ ] 所有 SQL 语句、方法名、表名同步去 roadmap，无"代码 backlog / SQL roadmap"分裂

#### API / CLI Acceptance

- [ ] `iar backlog advance` 可用，`iar roadmap` 报未知命令（无 alias/redirect）
- [ ] `/api/v1/agent-runner/backlog/*` 全部端点可用，旧 `/roadmap/*` 返回 404；`agent-overrides` 子路由同步
- [ ] `cli_parser.py` 的 argparse 分支同步改名，Typer 与 argparse 命令名一致

#### Frontend Acceptance

- [ ] `frontend-public` 路由目录、`components/*`、`lib/api/*`、`Roadmap*` 类型全部改为 backlog；`just run frontend-public` 构建通过
- [ ] `/app/backlog` 渲染待办工作台，页面标题/导航为 Backlog；`/app/roadmap` 不再提供
- [ ] `app/layout.tsx` 描述文案去 roadmap

#### Documentation Acceptance

- [ ] `docs/`（guides/api/architecture）、`README.md`、`mkdocs.yml` nav 文案对齐 Backlog，无断链
- [ ] 随包 `iar-operator` skill（`src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md`）中的 roadmap 引用（如 "Roadmap page"）改为 backlog；`rg -n "roadmap|Roadmap"` 在该文件零命中
- [ ] `ROADMAP.md` 顶部新增一句消歧说明，明确其战略文档身份与 Backlog 工作台的区别；正文与里程碑命名不变
- [ ] `uv run mkdocs build --strict` 通过

#### Validation Acceptance

- [ ] rv-1 至 rv-4 全部通过，证据按 `rv-<n>-<slug>.<ext>` 归入本 PRD evidence 目录
- [ ] rv-1/rv-2 含负控且按预期变红；`rg -n "roadmap|Roadmap" src/backend frontend-public` 零命中
- [ ] 功能行为与重命名前逐项等价（定向测试或快照对比），确认无行为漂移

#### Delivery Readiness

- [ ] 推荐目标态全量实现，无"先改一半、旧名留兼容"的拆分或隐藏兼容层
- [ ] 完成消息逐字携带 §9.1 人读呈递区的全部内容与实际呈递物
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
- **全仓遗漏**：`roadmap` 命中面广（约 186 文件），可能漏改某处引用；缓解是"先 `rg` 列清单、后 `rg` 零命中收口"，并靠 `just lint --full`/`just test`/`pnpm build` 暴露编译期遗漏。
- **与 134008 的命名协调**：见 §8 `soft` 依赖；若协调失败，可能出现 `iar roadmap ci` 与 `iar backlog` 并存，需在任一方落地时统一。

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
