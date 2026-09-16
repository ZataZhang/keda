# PRD: iar console dashboard 本地快照缓存与可配置后台定时同步

> ✅ **交付前置**：无，可立即开工。
> 结构化声明见 §8 Delivery Dependencies，**那里是唯一事实源**。

> ✅ **验收状态**：已实现并采集全部 rv-1–rv-6 证据（2026-09-17）；待独立 verifier 复核与归档。
> 本行是 §9 Acceptance Checklist 的投影，**那里是唯一事实源**。

本文档分两个高度：**Part A（§1–§4）** 给人看，用来确认"要不要做、做成什么样"，不含实现细节；**Part B（§5–§13）** 给执行者看，包含机制、改动树与验证命令。

## Feature Overview (功能一览)

> 本块是 §10 Functional Requirements 的通俗投影，不是第二事实源；行为验收以 §1 行为样例表为准。

- **本地快照秒开首屏**（FR-1、FR-2）：dashboard 数据持久化在本地数据库，打开页面直接读本地快照，不再每次现场扫描 GitHub。
- **后台定时同步**（FR-3）：console 服务内置一个后台循环，按设定间隔自动重新扫描并把结果写入本地快照，同步失败保留旧数据（FR-7）。
- **界面上的全局同步设置**（FR-4）：dashboard 上可直接开关自动同步、调整同步间隔，设置持久保存，重启后仍生效。
- **手动刷新不阻塞**（FR-5）：点"刷新全部"或单仓库刷新会触发后台重扫并写回快照，期间页面继续显示旧数据可正常浏览。
- **首次使用自动补数据**（FR-6）：全新环境没有任何快照时，页面显示"尚未同步"；后端幂等触发首次扫描，浏览器不重复发起第二个任务。
- **数据新鲜度可见**（FR-8）：页面显示"上次同步时间"，并轻量轮询本地快照，后台同步完成后界面自动反映新数据。
- **明确不做逐 issue 增量 diff**（§11）：每个同步周期仍是全量扫描；基于 `updatedAt` 的增量优化列为后续跟进。

# Part A · 人审层 (Review Layer)

## 1. Introduction & Goals

### Problem Statement

打开 `http://127.0.0.1:8313/app/dashboard/` 看 Agent Runner 各仓库的 issue 队列状态时，每次进入页面或点刷新都要等待几十秒：dashboard 的每一次数据展示都是对 GitHub 的**全量实时扫描**——11 个仓库、每仓库 6 个 label 的 issue 查询、每个 issue 再带 2–3 次网络调用和 4 个本地 git 子进程，全部通过 `gh` CLI 子进程串行/半并发完成。仓库归档的历史记录里，同类全量统计接口实测单次耗时 **71.7 秒**。系统里其实已有一个 30 秒的内存缓存，但 dashboard 实际走的异步任务路径完全绕过了它，等于没有缓存；前端也没有任何自动刷新机制。用户想要的效果类似 git：数据先落在本地，后台按节奏同步，需要时也可以手动刷新。

### Interpretation (解读回显)

**行为样例**（下表每一行都会逐字变成验收标准，改一个单元格就是改验收条件）：

| 输入 / 操作 | 期望观察到的结果 |
|---|---|
| 打开 dashboard（本地已有快照） | 页面 1–2 秒内渲染出各仓库队列与状态，数据来自本地快照，并显示"上次同步于 HH:MM:SS" |
| 点击"刷新全部" | 后台开始重新扫描，页面不卡死、继续显示旧数据；扫描完成后卡片更新，"上次同步"时间戳随之更新 |
| 在设置里把同步间隔改为 15 分钟 | 设置立即保存；之后后台每 15 分钟自动同步一次；重启 console 后该设置仍然保持 |
| 在设置里关闭自动同步（边界情况） | 不再有任何后台自动同步，只能手动刷新；页面照常显示已有快照与上次同步时间 |
| 全新环境（本地从未同步过）打开 dashboard（边界情况） | 页面显示"尚未同步/正在同步"空态；后端只触发一次首次扫描，浏览器不重复创建任务 |
| 后台同步期间 GitHub 网络失败（失败情况） | 页面继续显示上一份快照，数据不被清空；下个周期自动重试 |
| 仓库在 registry 中被禁用或删除（边界情况） | dashboard 不再展示该仓库；历史快照即使仍留在本地库中也不得重新出现在页面 |

**我默默定了这些**（没有逐一请示、直接定了的解读）：

- 快照和同步设置都存在**本地 SQLite 数据库**（console 现有的本地库），不回写全局配置文件。
- 默认同步间隔定为 **5 分钟**，界面上可在 1–60 分钟间调整，也可以整体关闭；设置是**全局**的，不分仓库。
- 每个同步周期仍是**全量扫描**；基于 `updatedAt` 的逐 issue 增量 diff 不做（它省的是 GitHub 配额，不影响你的等待时间）。
- 前端增加 15 秒一次的轻量轮询去读本地快照（纯本地读，成本约等于零），后台同步一完成界面就能跟上。
- 设置 UI 复用现有组件做**内联展开面板**，不新引入对话框组件库。
- 首次补扫由后端统一触发；前端只展示"尚未同步/正在同步"并轮询结果，不再额外创建第二个扫描任务。
- 自动同步与手动刷新通过按仓库协调器去重：不同仓库仍可并行，同一仓库同一时刻只允许一次扫描。

**我理解为不做**（你可能想要、但本 PRD 排除的）：

- 不做逐 issue 级别的增量更新（只重建有变化的 issue 详情）。
- 不做完成度统计接口的缓存改造（它有自己独立的 30 秒缓存，不在本次范围）。
- 不做多用户、权限或远程访问——console 本来就是绑定 127.0.0.1 的单机工具。

**文字版解读**：本需求读作"把 dashboard 的数据源从'每次实时扫描 GitHub'切换为'读本地持久化快照'，快照由一个节奏可配置的后台循环和用户手动刷新共同维护"，而**不是**"给现有实时扫描再加一层短时缓存"或"实现 git 式逐对象增量同步"。边界：快照必须进程重启后仍在；自动同步可全局关闭且关闭后不得有任何后台扫描；同步失败不得清除已有快照；现有手动刷新入口与数据内容结构保持不变。非目标见 §11。

### What The User Gets

打开 dashboard 立即看到最近一次同步的各仓库队列状态（秒级），页面上能看到数据是什么时候同步的；后台按自己设定的节奏自动保持数据新鲜，也可以随时点按钮立刻刷新且不影响当前浏览；这些偏好在界面上设置一次就长期生效。

### Measurable Objectives

- 本地已有快照时，dashboard 首屏数据就绪 ≤ 2 秒（快照读取接口本地响应 < 500ms），对比当前全量扫描的数十秒有数量级提升。
- 手动全量刷新期间页面持续可浏览旧数据，不出现整页阻塞。
- 在界面上修改同步间隔并重启 console 进程后，设置值保持不变（持久化生效）。
- 断网或 `gh` 不可用时，只要本地有快照，dashboard 仍正常渲染旧数据。
- 后台同步实际发生周期与界面设置值一致；修改间隔或关闭开关后调度器立即重新计算等待时间，不沿用旧周期继续等待。

## 2. Human Review Map (介入与风险地图)

本次需要人工确认的只有两项，其余全部由执行者 + 自动化门禁覆盖。

### 决策一：本地数据库新增两张表（监控快照 + 监控设置）

快照要进程重启后不丢，就需要落库。console 已有一个本地 SQLite 库（存运行历史、审计等），本次在其中新增两张表：一张按仓库存快照内容（JSON）和扫描时间，一张存全局同步设置（开关 + 间隔）。新增表、不动任何现有表，旧库文件打开时自动原地升级，已有数据不受影响。设置的默认值仍由静态配置声明，界面上改的是运行时覆盖值——这与现有 roadmap 设置的做法完全同构。

```mermaid
erDiagram
    monitoring_snapshots {
        TEXT repo_id PK
        TEXT payload_json
        TEXT scanned_at
    }
    monitor_settings {
        INTEGER id PK
        INTEGER sync_enabled
        INTEGER sync_interval_seconds
        TEXT updated_at
    }
```

**请确认：** 同意在 console 本地库中按上述结构新增 `monitoring_snapshots` 与 `monitor_settings` 两张表（不动现有表）？

**验收：** 用一个含历史数据的旧版库文件打开新代码，旧数据原样保留、新表自动建好；全新环境首次启动也能从零建表。

### 决策二：同步节奏的默认值与可调范围

后台自动同步默认开启、默认每 **5 分钟**一次；界面上可在 1–60 分钟之间调整，或整体关闭（关闭后只剩手动刷新，不得有任何后台扫描）。间隔越小数据越新鲜，但每个周期都是一次全量 GitHub 扫描（11 个仓库约一分钟的网络调用量），5 分钟是新鲜度与 GitHub API 配额消耗之间的平衡点。设置全局生效，不按仓库区分——队列监控场景下各仓库新鲜度需求一致，分仓库配置带来的界面复杂度不值当。

**请确认：** 默认 5 分钟、可调范围 1–60 分钟、可全局关闭、不分仓库——这个节奏方案是否接受？

**验收：** 在界面上把间隔改成 15 分钟并保存后，服务端日志随后按 15 分钟周期出现同步记录；改成"关闭"后日志中不再出现自动同步，手动刷新仍可用。

### 自动门禁，不需要逐项人工审阅

快照读写方法、由 FastAPI lifespan 托管的后台同步循环、按仓库扫描去重、手动刷新写回快照、两个新接口（读快照、读写同步设置）、前端页面改造与设置面板、旧文档字符串修正——这些由失败可辨识的单元测试、接口测试、端到端测试和 lint/构建门禁覆盖。后台循环不会在模块 import 时启动，服务关闭时会停止并回收。

**本次明确不涉及**：不修改任何现有数据库表结构以外的 schema；不改 agent runner daemon 处理 issue 队列的逻辑；不改完成度统计接口；不引入任何新的调度框架或第三方依赖；无多用户/权限设计。

## 3. Usage And Impact After Implementation

**console 终端用户（dashboard 直接使用者）**：入口仍是 `http://127.0.0.1:8313/app/dashboard/`。打开页面立即看到本地快照数据与"上次同步"时间；header 新增"设置"入口，展开后可开关自动同步、选择间隔（1/5/15/30/60 分钟）；"刷新全部"与各仓库刷新按钮外观不变，但点击后页面不再阻塞等待，后台扫完数据自动更新。首次使用（无快照）时会看到"尚未同步"提示并自动开始首次扫描。断网时页面仍显示最近一次快照。

**运维/安装者**：console 的启动方式（`iar console`）不变；本地库文件自动原地升级，无需手工迁移；后台同步随 FastAPI 应用生命周期启动和停止，不因测试导入、reload 或重复装载路由产生额外调度器；前端静态产物需随发布流程重新构建（发布流水线已有对应步骤）。

**API 调用方**：现有 overview 相关接口的地址、参数、返回结构全部保持不变；新增两个只读/设置接口属于纯增量，不影响任何既有调用。

**向后兼容**：现有配置文件中无需新增任何配置项即可运行（默认值内置）；本地库自动迁移；无废弃行为。

## 4. Requirement Shape

- **actor**：iar console 终端用户（通过 dashboard 页面）；间接涉及运维/安装者与 API 调用方（行为不变）。
- **trigger**：打开 dashboard 页面；到达后台同步周期；点击"刷新全部"或单仓库刷新；在设置面板修改同步偏好；console 进程启动。
- **expected behavior**：页面数据来自本地持久化快照；后台循环按全局设置的节奏全量重扫并写回快照；不同仓库可并行、同一仓库扫描去重；手动刷新不阻塞浏览；无快照时由后端幂等触发一次首扫并显示空态；同步或持久化失败保留旧快照并暴露失败状态；设置持久化且重启后保持。
- **explicit scope boundary**：不做逐 issue 增量 diff；不改统计接口缓存；不改 runner daemon 逻辑；不引入新依赖或调度框架；不做多用户与远程访问。

# Part B · 执行器层 (Build Layer)

## 5. Repository Context And Architecture Fit

**当前相关模块**：

- 后端四层：`api/`（路由接入）→ `core/`（用例与端口）→ `engines/` → `infrastructure/`（GitHub 客户端、SQLite 持久化、配置），依赖方向必须保持；当前 FastAPI 应用装配入口是 `src/backend/api/app.py`。
- 扫描逻辑：`src/backend/core/use_cases/agent_runner_monitor.py` 的 `build_repository_overview`（逐仓库全量扫描，经 `infrastructure/github_client.py` 的 `GitHubCliClient` 调 `gh` CLI）。
- 现状断点：`src/backend/api/routes/agent_runner.py` 的 `_run_overview_job` 直接调 `_build_overview_response`，**绕过**模块级 `_OVERVIEW_CACHE`（`TTLResponseCache`，30s），dashboard 主路径无缓存可用。
- 本地库：`src/backend/infrastructure/persistence/console_store.py` 的 `SqliteConsoleStore`（`~/.iar/console.db`，WAL，`PRAGMA user_version` 逐级迁移，当前 `_SCHEMA_VERSION = 3`，已有 `run_records`/`audit_logs`/`roadmap_queue`/`roadmap_settings`/`attempt_records` 五表；模块 docstring 的"当前版本 2"已过时）。
- 运行时可写设置先例：`roadmap_settings` 表 + `GET/PATCH /agent-runner/roadmap/settings`（`src/backend/api/routes/agent_runner_roadmap.py`）+ core 侧 `get_or_create_roadmap_settings`，是本 PRD 设置功能的同构模板。
- 线程先例：`agent_runner.py` 的 `_warm_overview_cache` 是一次性预热，`_start_overview_job` 是短生命周期任务；二者都不能直接充当长期周期调度器。周期同步必须挂入 `src/backend/api/app.py` 的 FastAPI lifespan，显式持有 stop/wake event 并在 shutdown 回收。
- 配置：`src/backend/infrastructure/config/agent_runner_settings.py` 的 `AgentRunnerConsoleSettings`（pydantic-settings，config.toml `[agent_runner.console]` 段）。
- 前端：`frontend-public`（Next.js 静态导出，`output: "export"`，产物由发布流水线拷入 `src/backend/api/static/console/`）；dashboard 页 `frontend-public/app/(app)/app/dashboard/page.tsx`；API client 按域分文件（`lib/api/agentRunner.ts`、`lib/api/console.ts`），类型集中在 `lib/api/types.ts`；无 dialog 组件，有 `sheet.tsx`/`form.tsx`/`input.tsx`；roadmap 页有 `POLL_INTERVAL_MS` 轮询先例。

**Reuse Candidates**：`build_repository_overview`（快照内容的生产者，原样复用）；`SqliteConsoleStore` 的迁移与 upsert 模式；`TTLResponseCache` 保留不动；现有 per-repo job 机制继续作为手动刷新入口；roadmap settings 端到端链路；FastAPI lifespan 作为长期后台任务的生命周期边界。

**架构约束**：路由层不直接写 SQL；core 不 import FastAPI 或 `SqliteConsoleStore`，而是依赖 `core/shared/interfaces/runner_console.py` 中新增的快照存储端口；infrastructure 的 `SqliteConsoleStore` 实现该端口；前端经 `lib/api/` 客户端访问，不直接 fetch 散落在组件里。

**Frontend Impact**：**Full-stack**。受影响前端为 `frontend-public`（仅此一个承载 console 界面）。改动页面 `app/(app)/app/dashboard/page.tsx`、API 客户端 `lib/api/agentRunner.ts` 与 `lib/api/console.ts`、类型 `lib/api/types.ts`，新增设置面板组件。

**Existing PRD Relationship**：2026-09-16 开工前复核时，除本 PRD 外 `tasks/pending/` 还有 3 个 PRD（prd-regrounding、completeness-judgment、tauri-desktop-shell），均与本任务无重复、无先后依赖；`iar-prd-skill-alignment` 已归档。归档 PRD `tasks/archive/P1-FEAT-20260910-111901-iar-console-bundled-web-terminal.md` 提供了 71.7 秒实测数据与 console 静态托管背景。本 PRD 可独立交付。

**Potential Redundancy Risks**：不要新建第二套缓存（内存 TTL 缓存保留现状即可）；不要为设置再写一套 TOML 回写（沿用 DB 设置表先例）；不要新引入调度框架。

## 6. Recommendation

### Recommended Approach

**最小改动路径**：给 dashboard 数据加一层"本地持久化快照"，读写两侧都挂在现有机制上——

1. `SqliteConsoleStore` 升 `_SCHEMA_VERSION` 到 4，新增 `monitoring_snapshots` 与 `monitor_settings` 两表及读写方法（照 `roadmap_settings` 的 upsert / get-or-create 模式）。
2. 在 `core/shared/interfaces/runner_console.py` 增加 `IMonitorSnapshotStore` 及快照/设置 DTO；core 新增用例模块负责持久化已构建结果、读取快照概览、读写同步设置与按仓库扫描协调，不依赖具体 SQLite 实现。
3. `agent_runner.py` 路由：`_run_overview_job` 复用本次已经生成的 payload 写回快照，绝不为持久化再次扫描；新增 `GET /v1/agent-runner/overview/snapshots`。`app.py` lifespan 启停唯一的周期调度器，PATCH 设置通过 wake event 使其立即重算等待时间。
4. `agent_runner_console.py` 路由：新增 `GET/PATCH /v1/agent-runner/console/monitor/settings`（照 roadmap settings 路由模式，PATCH 用 pydantic 模型校验）。
5. 前端 dashboard：首屏改读快照接口 + 15s 轮询；header 加"上次同步"时间与设置面板（内联展开，不引 dialog）；无快照时只显示后端返回的首扫状态并等待结果，不从浏览器重复创建扫描任务。

**为什么最贴合现架构**：设置表沿用 roadmap_settings、扫描沿用现有 job、持久化沿用 console store；只有长期周期任务改用应用生命周期管理，避免把一次性预热线程误扩成常驻调度器。不新增第三方依赖、不破坏四层边界、不改现有接口契约。

**拒绝冗余抽象的理由**：不引入 APScheduler/Celery（本地单机工具用 lifespan 托管的轻量 scheduler 足够）；不写 config.toml 回写（`TomlRegistryEditor` 只覆盖 registry 子树，且运行时可调偏好的先例是 DB 表）；不做第二份内存缓存层（快照库本身就是缓存）。

**范围凝聚性复核**：数据库迁移、scheduler、设置 API 和 dashboard 看似可分别回滚，但拆开会产生“有快照无生产者”“有后台同步却仍走实时首屏”或“设置已保存但调度不响应”的临时半成品契约，因此保持为一个 PRD。四个 R2 oracle 分别守住迁移、真实读取、设置持久化和并发生命周期，不能由相邻路径互相替代。

### Proposed Solution Summary (实现机制)

核心机制是**本地快照库 + 单一持久化入口 + 按仓库 in-flight 协调器**：GitHub 扫描仍由现有 overview 构建路径完成，构建出的 payload 只经 `persist_monitoring_result` 一次性落入 `monitoring_snapshots`，不得为写库重复扫描。周期同步与手动刷新进入同一按仓库协调器：不同仓库可并行，同一仓库已有任务时复用或报告其 in-flight 状态而不新开扫描。同步节奏由 `monitor_settings`（运行时覆盖，DB 持久）与 `AgentRunnerConsoleSettings` 的静态默认值提供；lifespan 调度器持有 stop/wake event，PATCH 后立即唤醒并重算下一次运行。仓库清单来自当前 registry，快照响应只展示当前启用仓库，已删除或禁用仓库的历史行不得泄漏回页面。状态变化：console.db 出现两张新表；用户可见变化为首屏秒开、"上次同步"时间、设置面板。刻意避免的复杂度：逐 issue 增量 diff、独立调度服务、第二套缓存、config.toml 回写编辑器扩展。

### Alternatives Considered

- **基于 `updatedAt` 的逐 issue 增量重建**：只重扫有变化的 issue。拒绝理由：用户等待已被本地读消除，增量省的只是 GitHub 配额；但要在快照中维护逐 issue 的 `updated_at` 索引并处理 issue 删除/换 label 的一致性，复杂度与出错面明显更高。列为 §12 跟进。
- **config.toml 回写设置**：拒绝理由见上（DB 设置表是同构先例，TOML 编辑器扩展影响面更大）。
- **引入 APScheduler**：拒绝理由：零新依赖原则；FastAPI lifespan + stop/wake event 已覆盖单进程本地工具的周期、唤醒和关闭需求。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

数据与控制流（目标态）：

1. **扫描协调路径**：周期调度与手动 job 都先向 `MonitorSyncCoordinator` 申请目标 `repo_id`。协调器维护按仓库 in-flight 集合：不同仓库可并行；同仓库已有扫描时不再启动第二次扫描，而是让调用方得到已有 job/in-flight 状态。扫描仍调用现有 overview 构建函数，不复制 GitHub 查询逻辑。
2. **持久化路径（唯一）**：扫描成功产生的现成 repository payload 交给 `persist_monitoring_result(store, payload)`，每个仓库一次 upsert；不得为了持久化重新调用 GitHub。写库异常不得在 store 内吞掉：手动 job 标记失败，周期批次记录该仓库失败并继续其他仓库，二者都保留旧快照。
3. **读路径**：`GET /overview/snapshots` → core 用例（依赖 `IMonitorSnapshotStore`）→ store。用例以当前启用 registry 为展示集合，返回 `{repositories: [{repo_id, scanned_at, overview}], missing_repo_ids, sync_status}`；库中已删除/禁用仓库的历史行不展示。dashboard 首屏与 15s 轮询都走这里。
4. **生命周期与首次补扫**：`src/backend/api/app.py` lifespan 创建唯一 coordinator + scheduler；启动后若当前启用仓库缺少快照，由后端幂等申请首扫。shutdown 设置 stop event 并有界 join。导入路由模块本身不得启动线程、访问 GitHub 或写库。
5. **设置路径**：`GET /console/monitor/settings` 返回 `{sync_enabled, sync_interval_seconds, updated_at}`；无 DB 记录时，间隔取 `AgentRunnerConsoleSettings.monitor_sync_interval_seconds`，不是 core 内硬编码 300。`PATCH` 校验 `[60, 3600]` 后写库，写失败返回错误；成功后设置 wake event，使 scheduler 立即重读设置并重算等待时间。`sync_enabled=false` 时不得启动新自动扫描，已在途手动扫描不取消。

### Change Impact Tree

```text
.
├── Database (~/.iar/console.db via SqliteConsoleStore)
│   └── src/backend/infrastructure/persistence/console_store.py
│       [修改]
│       【总结】schema 升 v4，新增监控快照与监控设置两表及读写方法，修正过时 docstring
│
│       ├── _SCHEMA_VERSION 3 → 4；_migrate 增加 v4 分支（照 v2/v3 逐级 if 模式）
│       ├── 新增 _CREATE_MONITORING_SNAPSHOTS（repo_id PK / payload_json / scanned_at）
│       ├── 新增 _CREATE_MONITOR_SETTINGS（id PK 单行 / sync_enabled / sync_interval_seconds / updated_at）
│       ├── 新增 upsert_monitor_snapshot / list_monitor_snapshots（照 save_roadmap_settings 的 upsert 模式；JSON 用 ensure_ascii=False）
│       ├── 新增 get_monitor_settings / save_monitor_settings（默认值由 core 用例注入，不在 store 硬编码）
│       └── 快照/设置作为 dashboard 的事实读取路径，写异常必须抛给调用方；不得照 append_run 的旁路审计语义静默吞错
├── Core
│   ├── src/backend/core/shared/interfaces/runner_console.py
│   │   [修改] 新增 MonitorSnapshotEntry / MonitorSettingsEntry DTO 与 IMonitorSnapshotStore 端口
│   └── src/backend/core/use_cases/monitor_snapshots.py
│       [新增]
│       【总结】已构建 payload 的单一持久化入口、按当前 registry 过滤的快照读取、设置读写与按仓库 in-flight 协调
│
│       ├── persist_monitoring_result(store, payload)：只持久化现成结果，不再次扫描
│       ├── get_snapshot_overview(store, enabled_repo_ids, sync_status)：过滤禁用/删除仓库并标记缺失项
│       ├── get/update_monitor_settings(store, static_default)：默认值回落 + 校验；写失败向上传播
│       └── MonitorSyncCoordinator：不同 repo 可并行、同 repo 去重，供周期与手动入口共用
├── Infrastructure
│   └── src/backend/infrastructure/config/agent_runner_settings.py
│       [修改]
│       【总结】console 设置新增静态默认同步间隔字段
│
│       └── AgentRunnerConsoleSettings 增加 monitor_sync_interval_seconds: int = 300（config.toml [agent_runner.console] 可覆盖默认值）
├── API
│   ├── src/backend/api/routes/agent_runner.py
│   │   [修改]
│   │   【总结】job 经共享 coordinator 扫描并写回快照、新增快照读取端点；本模块 import 不启动长期线程
│   │
│   │   ├── _run_overview_job：先经 coordinator 申请 repo；完成后把现成 payload 交给 core 持久化，写失败则 job 失败
│   │   ├── 新增 GET /v1/agent-runner/overview/snapshots → core.get_snapshot_overview()，并透传 registry 解析失败仓库（unreachable_repositories）
│   │   └── 现有 /overview/per-repo、/overview/jobs/{id} 端点契约不变
│   ├── src/backend/api/routes/agent_runner_console.py
│       [修改]
│       【总结】新增监控同步设置的 GET/PATCH 端点
│
│       ├── GET /v1/agent-runner/console/monitor/settings
│       └── PATCH 同路径，UpdateMonitorSettingsRequest（pydantic，两字段必填）校验间隔 [60,3600] 与开关布尔；保存成功后唤醒 scheduler
│   ├── src/backend/api/monitor_sync.py
│       [新增]
│       【总结】调度器与唤醒入口的生命周期边界：模块 import 零副作用，start/stop 只由 app.py lifespan 调用
│   └── src/backend/api/app.py
│       [修改]
│       【总结】FastAPI lifespan 启动唯一 monitor scheduler，缺快照时申请首扫；shutdown 通过 stop event + 有界 join 回收
├── Frontend (frontend-public)
│   ├── lib/api/types.ts
│   │   [修改]
│   │   【总结】新增 MonitorSnapshotsResponse 与 MonitorSettings 类型
│   ├── lib/api/agentRunner.ts
│   │   [修改]
│   │   【总结】新增 fetchOverviewSnapshots()（GET /overview/snapshots）
│   ├── lib/api/console.ts
│   │   [修改]
│   │   【总结】新增 fetchMonitorSettings() / updateMonitorSettings()
│   ├── app/(app)/app/dashboard/page.tsx
│   │   [修改]
│   │   【总结】首屏与轮询改读快照，header 加上次同步时间与设置入口，新增无快照空态；恢复"注册路径失效"警示条（改由快照响应的 unreachable_repositories 驱动）
│   │
│   │   ├── 首屏 fetchOverviewSnapshots() 直接渲染；保留 job 轮询仅用于手动刷新进度
│   │   ├── 15s setInterval 轮询快照接口（照 roadmap POLL_INTERVAL_MS 先例）
│   │   ├── header 显示"上次同步于 HH:MM:SS"（取快照 scanned_at 最大值）+ 设置按钮
│   │   ├── 全部仓库无快照时显示"尚未同步/正在同步"空态；首扫由后端负责，前端不重复触发 job
│   │   └── unreachable_repositories 非空时显示琥珀色警示条（保持 registry 路径失效的既有可见性）
│   └── components/agent-runner/monitor-settings-panel.tsx
│       [新增]
│       【总结】内联展开的同步设置面板（开关 + 间隔选择），保存即 PATCH
├── Tests
│   ├── tests/test_console_store.py
│   │   [修改] v3→v4 迁移保留旧数据、快照 upsert/读取、设置默认与往返
│   ├── tests/test_monitor_snapshots.py
│   │   [新增] core 用例：现成 payload 写回、写失败传播、registry 过滤、设置校验、同 repo 去重/跨 repo 并行
│   ├── tests/test_monitor_scheduler.py
│   │   [新增] import 零副作用（子进程探针）、lifespan 单实例与回收、wake 重算等待窗口、关闭后零扫描、读取失败不杀循环
│   ├── tests/test_monitor_api.py
│   │   [新增] 快照端点契约与 registry 过滤、设置 GET/PATCH 往返与校验、PATCH 唤醒、写失败 500、手动 job 写回快照与失败传播
│   └── tests/playwright-e2e/
│       ├── tests/smoke/agent-runner-monitor.spec.ts
│       │   [修改] stub 快照端点驱动首屏、空态不建 job、设置面板 PATCH canonical path
│       ├── page-objects/AgentRunnerMonitorPage.ts
│       │   [修改] mockSnapshots / mockMonitorSettings / trackScanRequests 与相关断言助手
│       └── tests/workflows/console-pages.no-auth.spec.ts
│           [修改] dashboard 用例的 stub 从 /overview 迁移到 /overview/snapshots（同一 fixture 包装）
└── Docs
    └── docs/guides/agent-runner.md
        [修改] Dashboard 一节补"本地快照与后台定时同步"说明；console 配置段补 monitor_sync_interval_seconds；修正"不新增数据库"的非目标表述
```

以上文件清单是起点而非穷举；见 Executor Drift Guard。

### Risk Classification Register

| 变更点 | 层级 | tier | 决定性维度/覆盖 | 干预 | oracle/gate |
|---|---|---|---|---|---|
| console.db 新增两表 + user_version 3→4 迁移 | infrastructure | R2 | schema 固定区 + 持久状态 | 人工确认（决策一）+ rv-1 | rv-1 |
| 快照/设置 store 读写方法 | infrastructure | R1 | 单组件、有测试先例 | 执行者 + 失败可辨识单测 | rv-4 |
| core monitor_snapshots 用例与存储端口 | core | R1 | 纯编排复用现有扫描，无新外部依赖 | 执行者 + rv-4 | rv-4 |
| lifespan scheduler + 按仓库 in-flight 协调器 | api/core | R2 | 长期并发触发器；必须防 import/reload 重复、同仓库重入与 shutdown 残留 | 执行者 + 强 oracle | rv-4 |
| `_run_overview_job` 写回快照 | api | R1 | 单路由模块行为补充 | 执行者 + rv-2 覆盖 | rv-2 |
| GET /overview/snapshots | api | R1 | 新只读端点，纯增量 | 执行者 + API 测试 | rv-4 |
| GET/PATCH monitor settings | api | R2 | 持久状态写 + 新 API 契约（新增非破坏） | 人工确认（决策二）+ rv-3 | rv-3 |
| dashboard 首屏/轮询/空态改造 | frontend | R1 | 用户可见、单页面 | 执行者 + e2e + 人读录屏 | rv-2、rv-5 |
| 设置面板 UI | frontend | R1 | 用户可见、复用现有组件 | 执行者 + e2e + 人读截图/观察记录 | rv-3、rv-5 |
| console_store docstring 修正 | infrastructure | R0 | 纯文档注释 | 执行者 + lint | rv-6 |

### Executor Drift Guard

- 以上文件路径基于当前仓库分析；若文件被移动/改名，用语义锚点定位：`SqliteConsoleStore._migrate`、`build_repository_overview`、`_run_overview_job`、`_warm_overview_cache`、`get_or_create_roadmap_settings`、`AgentRunnerConsoleSettings`。
- 隐藏的引用检查（实现前后各跑一次）：
  - `rg -n "per-repo|overview/jobs" frontend-public/lib src/backend/api` — 确认现有 overview 端点的全部调用方，保证契约不变。
  - `rg -n "_SCHEMA_VERSION|user_version" src/backend tests` — 找出所有依赖 schema 版本的测试。
  - `rg -n "create_console_store" src/backend` — 快照读写应复用同一 store 工厂，注意路由层每次请求新建实例的惯例。
  - `rg -n "lifespan|FastAPI\(" src/backend/api` — scheduler 只能从应用生命周期边界启停，路由模块 import 不得产生线程或 I/O 副作用。
  - `rg -n "_start_overview_job|fetchOverviewJobsByRepo|fetchMonitoringOverview" src/backend frontend-public` — 所有手动扫描入口必须经过同一按仓库 coordinator，不能只给周期入口加锁。
  - `rg -n "POLL_INTERVAL_MS" frontend-public` — 前端轮询先例。
- 前端组件目录确认：`rg --files frontend-public/components | head -30`。

### Flow Diagram

```mermaid
flowchart LR
    subgraph 读路径
        UI["dashboard 页面<br/>(首屏 + 15s 轮询)"] --> SNAP["GET /overview/snapshots"]
        SNAP --> DB[("console.db<br/>monitoring_snapshots")]
    end
    subgraph 写路径
        LIFE["FastAPI lifespan<br/>start / shutdown"] --> TIMER["scheduler<br/>(stop + wake event)"]
        TIMER --> COORD["MonitorSyncCoordinator<br/>按 repo 去重"]
        MANUAL["手动刷新 job<br/>(_run_overview_job)"] --> COORD
        COORD --> GH["现有 overview 构建<br/>(gh CLI 全量扫描)"]
        GH --> PERSIST["persist_monitoring_result<br/>(不重复扫描)"]
        PERSIST --> DB
    end
    subgraph 设置路径
        PANEL["设置面板"] --> PATCHS["GET/PATCH /console/monitor/settings"]
        PATCHS --> SDB[("console.db<br/>monitor_settings")]
        SDB -.每周期读取.-> TIMER
        PATCHS -.保存成功后 wake.-> TIMER
    end
```

### ER Diagram

```mermaid
erDiagram
    monitoring_snapshots {
        TEXT repo_id PK
        TEXT payload_json
        TEXT scanned_at
    }
    monitor_settings {
        INTEGER id PK
        INTEGER sync_enabled
        INTEGER sync_interval_seconds
        TEXT updated_at
    }
```

两张新表相互独立，与现有五表无外键关系；迁移只新增表，不修改现有表。

### Realistic Validation Plan

```yaml
oracles:
  - id: rv-1
    behavior: 旧版 console.db（含历史数据）打开新代码后自动升级到 v4，旧数据完整保留，两张新表建成；全新环境从零建表
    reviewer: verifier
    real_entry: "uv run pytest tests/test_console_store.py -k 'monitor or migrate' -v"
    expected: "v3 种子库（先以旧 CREATE 语句建表并插入 run_records 数据、置 user_version=3）经 SqliteConsoleStore 打开后：run_records 数据逐条仍在、monitoring_snapshots 与 monitor_settings 存在、user_version=4"
    mock_boundary: "SQLite 用 tmp_path 真实文件库，不 mock；不涉及 GitHub"
    tier: R2
    test_layer: integration
    required_for_acceptance: true
    critical_value_source: "测试内以 v3 时代的 CREATE 语句手工播种的库文件（模拟用户真实旧库）"
    must_cross: "种子库写入 -> 新代码 SqliteConsoleStore 构造（_migrate）-> 新连接查询"
    forbidden_bypasses: "不得直接以 v4 代码建库后声称迁移成功；不得只验证新表存在而漏掉旧数据行数断言"
    fresh_state_probe: "迁移后用独立 sqlite3 连接重新打开库文件查询，不复用迁移连接的内存状态"
    final_tree_evidence: "迁移测试在最终实现树上运行；任何 _migrate 或建表语句变更后必须重跑"
    negative_control: "在实现前的代码（_SCHEMA_VERSION=3）上运行同一测试"
    expected_fail: "查询 monitoring_snapshots 报错 no such table，测试红色"
  - id: rv-2
    behavior: 真实 console 服务下 dashboard 首屏从本地快照秒开、显示上次同步时间，手动刷新经后台扫描后数据与时间戳更新，期间页面可继续浏览
    reviewer: human
    real_entry: "构建前端静态产物后运行 console（uv run iar console），浏览器打开 http://127.0.0.1:8313/app/dashboard/"
    expected: "已有快照时页面 2 秒内渲染数据并显示上次同步时间；点刷新全部后页面不阻塞，扫描完成后卡片与时间戳更新；快照接口本地 curl 响应 < 500ms"
    mock_boundary: "真实 console 进程、真实 SQLite、真实浏览器；GitHub 侧用本机已认证的 gh（凭证依赖，post-merge 人工验证时真实执行；CI 不可用时以 fake gh 环境的同路径运行作为降级证据并披露）"
    tier: R2
    test_layer: manual
    required_for_acceptance: true
    presentation: "tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-2-dashboard-refresh.webm（真实入口录屏，覆盖刷新前/刷新中仍可浏览/刷新后时间更新，标注 real-entry）；另附快照接口计时文本"
    critical_value_source: "页面上显示的上次同步时间取自 GET /overview/snapshots 响应中的 scanned_at 字段原值"
    must_cross: "浏览器 -> FastAPI 路由 -> core 用例 -> SqliteConsoleStore -> SQLite 文件"
    forbidden_bypasses: "不得用组件级预览页或手工注入状态充当 dashboard 证据；不得直接调用 core 函数代替 HTTP 路径"
    fresh_state_probe: "手动刷新完成后开新浏览器标签（新会话）重新打开 dashboard，确认新数据与新时间戳"
    final_tree_evidence: "录屏与接口计时在最终实现树上采集；路由、前端页面或 store 变更后重采"
  - id: rv-3
    behavior: 在 dashboard 设置面板修改同步间隔/开关后立即保存生效，重启 console 进程后设置保持，关闭后后台不再自动同步
    reviewer: human
    real_entry: "dashboard header 设置面板（真实入口同 rv-2）"
    expected: "改为测试用短间隔后 GET 设置接口返回新值且 scheduler 被立即唤醒；重启进程后值保持；在两个应触发周期内出现对应自动同步记录；关闭后再观察两个同长度周期，无新自动同步，手动刷新仍成功"
    mock_boundary: "真实进程与真实库；GitHub 网络可用 fake gh 替代（设置持久化不依赖 GitHub 内容）"
    tier: R2
    test_layer: manual
    required_for_acceptance: true
    presentation: "tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-3-settings-panel.png（设置面板展开态真实截图）+ rv-3-scheduler-observation.md（启用/关闭各两个有界周期的时间线）；自检：重新启动 console 后 GET 设置接口对比界面显示值"
    critical_value_source: "界面上显示的间隔值来自 GET /console/monitor/settings 响应原值；修改操作经 PATCH 写库"
    must_cross: "浏览器 PATCH -> FastAPI 路由校验 -> core -> store 写库 -> 进程重启 -> 新请求 GET 读回"
    forbidden_bypasses: "不得直接写 SQLite 或改 config.toml 冒充界面设置；不得只验证 PATCH 200 而不做重启后读回"
    fresh_state_probe: "重启 console 进程后用全新 HTTP 请求 GET 设置接口断言持久值"
    final_tree_evidence: "证据在最终实现树上采集；设置路由、校验模型或 store 方法变更后重采"
    negative_control: "在实现前的代码上执行 curl PATCH /api/v1/agent-runner/console/monitor/settings"
    expected_fail: "404 Not Found，验证该端点此前不存在"
  - id: rv-4
    behavior: scheduler 只随 FastAPI lifespan 启动一次并在 shutdown 回收；周期与手动入口对同一仓库去重、不同仓库可并行；持久化失败不得伪装成刷新成功
    reviewer: verifier
    real_entry: "uv run pytest tests/test_monitor_scheduler.py tests/test_monitor_snapshots.py tests/test_console_store.py -v"
    expected: "仅 import 路由模块时零线程/零扫描；TestClient lifespan 内恰好一个 scheduler；同 repo 周期+手动并发只执行一次扫描、两个不同 repo 可并行；写库注入异常后 job/批次报告失败且旧 scanned_at 不变；PATCH 后 wake 使等待时间立即重算；退出 lifespan 后线程终止"
    mock_boundary: "GitHub/gh 边界用记录调用次数与并发窗口的 fake；FastAPI lifespan、真实线程、事件与 tmp_path SQLite 均不 mock"
    tier: R2
    test_layer: integration
    required_for_acceptance: true
    critical_value_source: "周期触发与手动 API 产生的真实 repo_id，以及 PATCH 后从 monitor_settings 重新读取的间隔值"
    must_cross: "FastAPI lifespan start -> scheduler -> shared MonitorSyncCoordinator -> overview producer -> persist_monitoring_result -> SQLite commit；shutdown -> stop event -> thread join"
    forbidden_bypasses: "不得直接调用持久化 helper冒充调度；不得为测试添加生产开关；不得只测独立锁而绕过周期/手动两个入口；不得以 daemon 进程退出代替 shutdown join 断言"
    fresh_state_probe: "并发动作完成后用新 SQLite 连接读取快照，并在退出 TestClient 后枚举/探测 scheduler thread 已终止"
    final_tree_evidence: "在最终实现树执行；app.py lifespan、coordinator、job 入口、store 或设置 PATCH 任一变更后重跑"
  - id: rv-5
    behavior: dashboard 前端在 stub 快照/设置接口下正确渲染首屏、上次同步时间、空态与设置面板交互
    reviewer: verifier
    real_entry: "just e2e tests/smoke/agent-runner-monitor.spec.ts"
    expected: "agent-runner-monitor.spec.ts 更新后全绿：stub 快照响应驱动首屏渲染；无快照 stub 显示后端 sync_status 且浏览器不发起 overview job；设置面板选择间隔后只向 canonical path /api/v1/agent-runner/console/monitor/settings 发出 PATCH"
    mock_boundary: "后端接口经 Playwright 路由 stub（前端为被测对象）；页面真实渲染于浏览器"
    tier: R1
    test_layer: e2e
    required_for_acceptance: true
  - id: rv-6
    behavior: 全仓质量门禁保持绿：后端 lint/测试、前端 typecheck/lint/build
    reviewer: verifier
    real_entry: "just lint --repo；just frontend-public typecheck；just frontend-public build"
    expected: "完整 lint/reuse/test/docs 门禁通过；前端类型检查通过且 out/ 成功导出"
    mock_boundary: "无 mock"
    tier: R0
    test_layer: smoke
    required_for_acceptance: true
```

失败排查提示：rv-2/rv-3 若 dashboard 404，先确认前端静态产物已构建并拷入 `src/backend/api/static/console/`（参照 `.github/workflows/release.yml` 的 build+copy 两步）；若快照为空，先查 scheduler/coordinator 日志与 job 失败状态；Playwright 失败先看 stub 路径是否带 `/api` 前缀。`tests/playwright-e2e/` 是独立 npm 包，日常优先走仓库 `just e2e` 入口，不在 PRD 中直接使用 pnpm 子命令。

### Low-Fidelity Prototype

设置面板改动小、单步交互，一处 ASCII 线框足够：

```text
┌ Agent Runner 管理终端    上次同步 12:03:21   [同步设置 ▾] [刷新全部] ┐
│ （点"同步设置"后 header 下方内联展开，页面内容不跳转）                  │
│ ┌─ 后台自动同步 ─────────────────────────────────────────────┐ │
│ │  (●) 开启   ( ) 关闭                                       │ │
│ │  同步间隔: [ 5 分钟 ▾ ]   可选: 1 / 5 / 15 / 30 / 60 分钟  │ │
│ │  上次同步: 2026-09-16 12:03:21   状态: 已保存 ✓            │ │
│ └────────────────────────────────────────────────────────────┘ │
│ ┌ 仓库卡片 … ┐  ┌ 仓库卡片 … ┐                                │
└──────────────────────────────────────────────────────────────┘

无快照空态：
│ ⏳ 尚未同步 — 正在后台执行首次扫描，完成后自动显示数据 [立即刷新] │
```

### Interactive Prototype Change Log

No interactive prototype file changes in this PRD.

### External Validation

No external validation required; repository evidence was sufficient.

## 8. Delivery Dependencies

### Delivery Dependencies

- Group: none
- Depends on tasks/issues:
  - none
- Gate type: none
- Notes: 2026-09-16 开工前复核已检查其余 3 个 pending PRD；与本任务无重复或先后依赖，可独立交付。`iar-prd-skill-alignment` 已归档。

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 要看的结果 | 呈递物 | 10 秒自检 |
|---|---|---|
| dashboard 秒开并显示上次同步时间，手动刷新期间旧数据仍可浏览、完成后数据与时间更新 | `tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/rv-2-dashboard-refresh.webm`（交付时填实际录屏） | 打开 http://127.0.0.1:8313/app/dashboard/ 看 header 有无"上次同步"时间；快照接口本地响应应远小于 1s |
| 设置面板改间隔后调度立即重算、重启后保持、关闭后连续两个周期无自动扫描 | `rv-3-settings-panel.png` + `rv-3-scheduler-observation.md`（交付时填实际路径） | 重启 console 后 GET 设置接口应与界面值一致；观察记录应同时包含开启与关闭窗口 |

注：数据库迁移、core/store 单测、Playwright stub 测试、lint/构建属 `reviewer: verifier` 组（rv-1、rv-4、rv-5、rv-6），按设计不在人读呈递区展示，仅失败时上报。

### 9.2 Acceptance Evidence Package

按 §7 Risk Classification Register 排序：

1. **人工确认 + R2（优先审查）**：rv-1（迁移往返，含 negative_control 红跑记录）、rv-3（设置持久化，含 negative_control 404 记录）、rv-2（真实入口 dashboard）、rv-4（scheduler 生命周期与并发协调）。
2. **R1**：rv-5（Playwright smoke 输出）及 rv-4 内附带的 store/core 单组件断言。
3. **R0**：rv-6（lint/typecheck/build 输出折叠呈现）。

#### Architecture Acceptance

- [x] core 不依赖 Web 框架或具体 SQLite 实现：`rg -n "^\s*(from|import)\s+(fastapi|backend\.infrastructure\.persistence\.console_store)" src/backend/core/use_cases/monitor_snapshots.py src/backend/core/shared/interfaces/runner_console.py` 无命中（同 pattern 去掉行首锚点只命中接口 docstring 里的路径说明文字）
- [x] 路由层无直接 SQL：`rg -n "CREATE TABLE|INSERT INTO|sqlite3" src/backend/api/routes/` 无命中（快照/设置 SQL 全部在 `console_store.py`）
- [x] 路由模块 import 零副作用：`tests/test_monitor_scheduler.py::test_importing_route_modules_starts_nothing` 用子进程探针断言 import 后无额外线程、无 scheduler、无 coordinator；scheduler 只由 `backend.api.app` lifespan 启停
- [x] 未引入新第三方依赖：`git diff --cached --stat -- pyproject.toml uv.lock frontend-public/package.json` 为空

#### Behavior Acceptance

- [x] rv-1 通过：v3 种子库迁移后旧 `run_records` 行数不变（3 行）、两新表存在、`PRAGMA user_version` 为 4；实现前红跑记录见 `rv-1-negative-control.txt`（`no such table: monitoring_snapshots`）
- [x] rv-4 通过：`uv run pytest tests/test_monitor_scheduler.py tests/test_monitor_snapshots.py tests/test_console_store.py -v` 47 passed（含生命周期、同 repo 去重/跨 repo 并行、写失败传播、registry 过滤与间隔边界）；HTTP 契约另有 `tests/test_monitor_api.py` 12 passed
- [x] 快照或设置持久化失败不会返回成功假象：手动 job/PATCH 显式失败（`test_overview_job_fails_when_snapshot_write_fails`、`test_monitor_settings_patch_reports_persist_failure`），旧快照仍可从新连接读取
- [x] 现有 overview 端点契约未变：`tests/` 中原有 agent-runner 相关 Python 测试（`test_agent_runner_monitor.py`、`test_agent_runner_console_api.py` 等）未修改即通过；Python 侧唯一改动的既有测试文件是 `tests/test_console_store.py`（新增用例，属本 PRD 要求）；另有两个既有 Playwright spec 被修改（`agent-runner-monitor.spec.ts`、`console-pages.no-auth.spec.ts`，原因见 Change Log 与 Frontend Acceptance）

#### Frontend Acceptance

- [x] rv-5 通过：`just e2e tests/smoke/agent-runner-monitor.spec.ts` 4 passed（含空态、后端首扫状态不建 job 与设置面板 PATCH canonical path 断言）；`tests/workflows/console-pages.no-auth.spec.ts` 5 passed 覆盖 dashboard 回归
- [x] rv-2 真实入口证据采集：录屏来自 `uv run iar console` 真实服务（`http://127.0.0.1:8313/app/dashboard/`），覆盖空态/首屏/刷新中可浏览/刷新后更新四段，非组件预览；标注 `real-entry`，原始文件为 local-only（见证据报告人审导航）
- [x] 前端 build 成功：`just frontend-public typecheck` 与 `just frontend-public build` 通过并产出 `out/`（13 页静态导出）

#### Documentation Acceptance

- [x] console 相关文档页补充快照/定时同步/设置项说明：`docs/guides/agent-runner.md` 新增"本地快照与后台定时同步"一节 + console 配置段 `monitor_sync_interval_seconds` + 修正"不新增数据库"的非目标表述；未新增页面，`mkdocs.yml` 无需变更（`just lint --repo` 内 `mkdocs build --strict` 通过）
- [x] `console_store.py` 模块 docstring 的 schema 版本描述与实际 `_SCHEMA_VERSION = 4` 一致

#### Validation Acceptance

- [x] rv-1 至 rv-6 全部执行通过，证据文件按 `rv-<n>-<slug>.<ext>` 命名存于 `tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/`
- [x] rv-2/rv-3 证据链完整：关键值取自接口响应原值（`scanned_at` / `sync_interval_seconds`）、迁移与崩溃边界均用独立连接或进程重启复核、证据采集于最终实现树（`0d368777` + 本分支改动）
- [x] 所有 RV 脚本位于证据目录 `scripts/` 下，未进入代码 diff（`git diff --cached --name-only | grep scripts/` 为空；证据 md 之外的文件被 `.gitignore` 白名单排除）

#### Delivery Readiness

- [x] 推荐方案全量实现，无遗留 regression 或发布阻塞（全量 `just test all` 2185 passed，其中本分支新增 64 项 monitor 相关用例；`just lint --repo` 0 error）
- [x] 完成消息逐字携带 9.1 人读呈递区的全部内容（含录屏、截图与观察记录），仅归档证据目录而未展示视为未交付
- [~] 独立 verifier Agent 审查通过 — runner-owned gate: verifier review
- [~] PRD 归档至 tasks/archive/ — runner-owned gate: archive

#### Human-Confirmed

- [x] 决策一确认：console.db 新增 `monitoring_snapshots`/`monitor_settings` 两表方案（对照 rv-1：v3→v4 迁移保留历史 + 全新环境从零建表）
- [x] 决策二确认：默认 5 分钟、1–60 分钟可调、可全局关闭、不分仓库（对照 rv-3：界面改 1 分钟即时生效、周期一致、关闭后零自动同步、重启后保持）
- [~] 9.1 人读呈递区全部呈递物已查看 — runner-owned gate: human review（呈递物已在本机生成，路径见证据报告人审导航）

## 10. Functional Requirements

- **FR-1**：系统将每个受监控仓库的队列概览快照（内容与现有 per-repo overview 相同结构）持久化到本地数据库，记录扫描完成时间，进程重启后保留。
- **FR-2**：dashboard 首屏数据读取本地快照而非实时扫描；无快照的当前启用仓库以空态标记返回；已禁用或删除仓库即使仍有历史行也不得展示。
- **FR-3**：console 服务运行期间，由 FastAPI lifespan 管理唯一后台 scheduler，按全局设置自动扫描并写回；不同仓库可并行，同一仓库的周期与手动扫描必须去重；设置变化立即唤醒 scheduler 重算等待时间；shutdown 后不得残留线程。
- **FR-4**：用户可在 dashboard 界面查看并修改全局同步设置（开关 + 间隔，间隔合法范围 60–3600 秒），修改立即持久化并在进程重启后保持；非法值被拒绝，持久化失败不得返回成功。
- **FR-5**：手动刷新（全部或单仓库）复用现有扫描产物，完成后通过单一持久化入口写回快照，不得为写库再次扫描；刷新期间页面继续显示旧数据。
- **FR-6**：本地存在缺失快照的启用仓库时，由后端 scheduler 幂等申请首扫；dashboard 只展示"尚未同步/正在同步"并轮询结果，不从前端重复创建扫描任务。
- **FR-7**：任一仓库扫描或快照持久化失败时保留该仓库旧快照，其余仓库正常更新；失败进入 job/批次状态并记录日志，不得伪装为刷新成功。
- **FR-8**：dashboard 显示"上次同步时间"（取各仓库快照扫描时间的最大值），并以约 15 秒周期轮询本地快照使后台同步结果自动反映到界面。

## 11. Non-Goals

- 不做基于 `updatedAt` 的逐 issue 增量 diff 重建（列为 §12 跟进）。
- 不改完成度统计接口（`/console/stats/overview`）的现有 30 秒缓存策略。
- 不改 agent runner daemon 处理 issue 队列的逻辑与节奏。
- 不引入 APScheduler/Celery/Redis 等任何新依赖或独立调度服务。
- 不做多仓库分别设置同步间隔；不做多用户、权限、远程访问。
- 不修改现有五张数据库表的结构。

## 12. Risks And Follow-Ups

- **GitHub API 配额消耗**：定时全量扫描（默认 5 分钟 × 11 仓库）会持续消耗 `gh` 配额；对绝大多数个人使用场景富余，但值得作为后续优化。跟进项：基于 `updatedAt` 的逐 issue 增量重建（需快照内维护逐 issue 时间戳索引并处理 issue 删除一致性）。
- **并发与生命周期**：周期和手动入口共享按仓库 coordinator；不同仓库可并行、同仓库去重。scheduler 只由 FastAPI lifespan 启停，避免 import/reload/test 产生幽灵线程。剩余风险为批次期间各仓库快照时间不同（逐仓库 commit 的设计取舍，可接受且由每仓库 `scanned_at` 可见）。
- **历史行清理**：registry 删除/禁用仓库后，旧快照行先保留以便恢复，但读取接口按当前启用 registry 过滤；物理清理不属于本次范围。
- **发布依赖**：前端改动需经发布流水线重建静态产物并拷入包数据；本地开发缺产物时 console 以 API-only 模式启动（现有行为），验证 rv-2/rv-3 前须先构建。
- **设置面板竞态无自动化回归护栏**：面板挂载 GET 与用户首击的竞态已用 `userEditedRef` 守卫修掉（独立 verifier 复核通过），但现有 e2e 的瞬时 stub 无法复现该窗口，新增的"延迟 GET"用例经对抗自检证明不具判别力、已删除；后续若要补，需要在 frontend-public 侧引入组件级测试或可控延迟的 e2e 夹具。

## 13. Decision Log

| ID | 决策问题 | Chosen | Rejected | Rationale |
|---|---|---|---|---|
| D-01 | 快照存哪 | console.db 新增快照表 | 扩容内存 TTL 缓存；独立 JSON 文件 | 进程重启不丢且与 run_records 等同库，内存缓存重启即丢，JSON 文件要自建并发控制而 SQLite WAL 现成 |
| D-02 | 同步设置存哪 | console.db `monitor_settings` 表 | config.toml 回写（扩 TomlRegistryEditor） | 运行时可调偏好已有 roadmap_settings 同构端到端先例；TOML 编辑器仅覆盖 registry 子树，扩展写错影响全局共享配置 |
| D-03 | 后台同步怎么调度 | FastAPI lifespan 托管的 scheduler（stop/wake event） | 路由模块 import 时启动 daemon；引入 APScheduler/Celery | 一次性 cache warm 不是长期任务先例；lifespan 可保证单实例边界、即时唤醒与 shutdown 回收，同时无需新依赖 |
| D-04 | 每周期扫描策略 | 全量扫描 | 基于 `updatedAt` 的逐 issue 增量 | 用户等待已被本地读消除，增量只省配额却引入逐 issue 索引与删除一致性复杂度 |
| D-05 | 设置 UI 形态 | header 内联展开面板 | 新增 shadcn Dialog 组件 | `components/ui/` 无 dialog，roadmap 设置先例就是内联式，避免新增组件依赖 |
| D-06 | 周期扫描与手动刷新如何并发 | 共享按 repo_id 的 in-flight coordinator，不同仓库并行、同仓库去重 | 全局非重入锁；两套独立锁 | 全局锁会破坏现有 per-repo 并行，两套锁又无法阻止周期与手动重复扫描 |
| D-07 | 快照写失败如何呈现 | store 抛错，job/批次记录失败并保留旧快照 | 仅 warning 后仍返回成功 | dashboard 以 SQLite 为事实读取路径，吞错会产生“刷新成功但数据未更新”的假成功 |
| D-08 | 首次无快照由谁触发 | 后端 scheduler 幂等申请首扫，前端仅展示状态 | 后端与前端各自触发一次 | 单一所有者可避免启动竞态导致重复 GitHub 扫描 |

### Final Reconciliation

- **对照最终实现与新鲜证据（2026-09-17 完成）**：
  - **实现与 §7 计划一致**：store v4 迁移 + 两新表、core `monitor_snapshots` 用例与端口、lifespan scheduler、按仓库 coordinator、`GET /overview/snapshots`、`GET|PATCH /console/monitor/settings`、dashboard 快照首屏 + 15s 轮询 + 设置面板，全部落地；实现期偏差（快照端点透传 unreachable、PATCH 两字段必填、写库失败错误顺序、移除 import 期预热线程、测试文件落位）逐条记入 §14 Change Log。
  - **oracle 全部执行**：rv-1（迁移往返 + 实现前红跑）、rv-2（真实入口录屏 + 接口计时）、rv-3（设置即时生效/周期一致/关闭零同步/重启保持 + 实现前 404 负控）、rv-4（调度生命周期/并发/写失败 + HTTP 契约）、rv-5（Playwright 两个 spec）、rv-6（`just lint --repo`、`just test all` 2185 passed、前端 typecheck/build）全部通过，原始输出见 `tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/`。
  - **风险地图对账**：R2 项（迁移、scheduler 生命周期与并发、设置 API）均以强 oracle 覆盖；§12 遗留风险（GitHub 配额、批次内各仓库时间戳不同、历史行物理清理、发布需重建静态产物）保持原样，无新增未登记风险。
  - **与 Part A 的行为样例逐条对账**：秒开首屏 ✅（重采证据 109ms）、刷新不阻塞 ✅、间隔可改且重启保持 ✅、关闭后零自动同步 ✅、全新环境空态 + 后端首扫 ✅（前端不重复建任务由 e2e 断言）、同步失败保留旧数据 ✅（注入用例）、禁用/删除仓库不回流 ✅（registry 过滤用例 + e2e）。

## 14. Change Log

### 恢复 registry 路径失效的 dashboard 警示条，并由快照端点透传 unreachable_repositories
- Type: scope
- Before: §7 变更树里 `snapshot_overview_to_payload` 的 `unreachable_repositories` 恒为 `[]`，前端改造未提及该警示条
- After: route 层新增 `_resolve_enabled_repositories()` 解析 registry 失败项并透传；core 的 `snapshot_overview_to_payload(result, *, unreachable_repositories=())` 接收该列表；dashboard 在非空时显示琥珀色警示条；`console-pages.no-auth.spec.ts` 的 stub 同步迁移到快照端点
- Reason: 实施期发现该警示条已在更早的重构提交中被移除（既有 e2e 断言"个已注册仓库无法访问"在 main 上已红），而 `docs/guides/agent-runner.md` 明确承诺"某个已注册路径失效时……总览页给出醒目警示"；本次改造不应静默丢掉该可见性
- Impact: 新增一个 route 层私有解析函数与一个 core 函数可选参数；前端 `LoadState.ready` 增加 `unreachable` 字段；不改变任何既有端点契约
- Review: 执行者自审；待独立 verifier 复核

### 测试落位：新增 test_monitor_scheduler.py 与 test_monitor_api.py
- Type: test
- Before: §7 计划为 `tests/test_monitor_scheduler.py`（新增）与 `tests/test_agent_runner_console_api.py 或新文件`（修改/新增）
- After: 实际新增 `tests/test_monitor_scheduler.py`（import 零副作用子进程探针、lifespan 单实例与回收、wake 重算、关闭零扫描、读取失败不杀循环）与 `tests/test_monitor_api.py`（快照端点、设置往返与校验、PATCH 唤醒、写失败 500、手动 job 写回与失败传播）
- Reason: 生命周期/线程用例需要独立的进程级隔离，与已有常驻 `TestClient(app)` 的 console API 测试模块混放会互相干扰；HTTP 契约测试独立成文件更易定位
- Impact: 未修改 `tests/test_agent_runner_console_api.py`，其原有断言无需变更即通过
- Review: 执行者自审；待独立 verifier 复核

### 设置 PATCH 请求体改为两个字段必填
- Type: api
- Before: `UpdateMonitorSettingsRequest` 的 `sync_enabled` / `sync_interval_seconds` 均带默认值（True / 300）
- After: 两字段必填，缺一即 422
- Reason: 设置是整体覆盖语义；带默认值会让只传开关的请求把用户已保存的间隔静默改写成 300
- Impact: 前端设置面板始终发送两个字段，行为不变；新增一条"缺字段被拒"的契约测试
- Review: 执行者自审

### 写库失败优先报告持久化原因
- Type: code
- Before: `_scan_repository_and_persist` 先判断"没有写入任何仓库"，导致唯一仓库写库失败时抛出 `produced no overview payload`，掩盖真实原因
- After: 先报告 `failed_repo_ids` 明细（含 store 抛出的原始错误），再兜底判断空 payload
- Reason: D-07 要求失败可辨识；job 的错误信息必须指向真实失败原因
- Impact: 仅影响错误文案与 job.error 内容
- Review: 执行者自审

### 移除 import 期 overview 缓存预热线程
- Type: scope
- Before: `agent_runner.py` 模块底部在 import 时启动 `_warm_overview_cache()` 后台线程做实时扫描预热
- After: 移除该调用与函数；`_OVERVIEW_CACHE`（30s TTL）与 `/overview` 端点保留不变
- Reason: 本次验收要求"仅 import 路由模块不启动 scheduler、不访问 GitHub、不写 SQLite"，import 期预热线程与该约束直接冲突；dashboard 首屏已改由快照提供，预热不再有收益
- Impact: `/overview` 首次请求不再享受预热数据（该端点不再是 dashboard 主路径）；新增子进程探针测试守住"import 零副作用"
- Review: 执行者自审；待独立 verifier 复核

### 文档更新：dashboard 快照与定时同步
- Type: doc
- Before: `docs/guides/agent-runner.md` 只描述 `GET /overview` 的实时扫描口径，并把"不新增数据库、后台任务队列"列为显式非目标；console 配置段无同步间隔项
- After: 新增"本地快照与后台定时同步"一节（快照表、调度器、首扫、失败保留、设置 API、`unreachable_repositories` 过滤），修正非目标表述，配置段补 `monitor_sync_interval_seconds`
- Reason: AGENTS 要求公共行为变更同步更新 `docs/`
- Impact: 无新增文档页，`mkdocs.yml` 导航无需变更
- Review: 执行者自审

### 独立 verifier 复核后的修正（2026-09-17）
- Type: code
- Before: verifier 报告指出五处非阻塞缺口：①设置面板挂载时的 GET 会覆盖用户在响应返回前的选择（`rv-3-settings-panel.png` 因此截到"已保存但高亮 5 分钟"的不一致画面）②`MonitorSyncScheduler.stop()` 在 join 超时后仍清空线程句柄，配合 `start()` 可起出第二个调度循环 ③`MonitorSyncCoordinator._threads` 只在 `wait_until_idle`（测试专用）里回收，长驻进程缓慢累积死线程对象 ④`get_snapshot_overview` 的 `in_flight_repo_ids` 参数在实现体内从未被使用，docstring 承诺名不副实 ⑤合法 JSON 但结构错误的快照会被当作 `ready` 下发（前端渲染期可能抛错）
- After: ①面板加 `userEditedRef` 守卫，用户动过之后晚到的初始 GET 不再回写界面状态 ②`stop()` 在超时窗口内线程未退出时保留句柄并 warning，`start()` 被 `is_alive()` 挡住 ③`_run_scan` 的 finally 里顺手回收已结束线程句柄 ④删除该死参数（route 不再为此取协调器锁），`sync_status` 语义由「是否有快照/是否部分缺失」承担 ⑤新增 `_is_renderable_overview` 最低结构校验（`repo_id` 对得上且 `issues` 是列表），不符者视同缺失；每条修正均有对应单测（`test_malformed_snapshot_is_treated_as_missing`、`test_stop_timeout_keeps_handle_so_no_second_loop_starts`、`test_coordinator_reclaims_finished_thread_handles`）
- Reason: verifier 独立复核发现的健壮性与证据口径问题；其中 ①⑤ 会影响用户可见行为与证据可信度
- Impact: rv-3 截图与观察记录、rv-4/rv-5/rv-6 证据均在修正后重采/重跑；无接口契约变化（`GET /overview/snapshots` 响应字段不变）
- Review: 独立 verifier 复核（见 `tasks/evidence/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync.verifier-report.md`）

### 手动刷新 job 改为从快照读回 payload（实现期偏差补记）
- Type: scope
- Before: §7 写的是"`_run_overview_job` 完成后把现成 payload 交给 core 持久化"
- After: job 通过共享协调器触发扫描（扫描内部完成写回），随后用 `_build_snapshot_payload` 从库读回结果组装 payload
- Reason: 扫描与写回已收敛到 `_scan_repository_and_persist` 单一生产者，job 再拼一份 payload 会产生两个事实源；读回保证 job payload 与页面展示完全一致
- Impact: 功能等价；`_build_snapshot_payload` 的 `unreachable_repositories` 仍为 `[]`（dashboard 不消费 job payload，警示条由快照端点提供）
- Review: 执行者自审 + 独立 verifier 复核（未判为阻塞）
