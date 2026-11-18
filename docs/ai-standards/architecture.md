# Architecture Standards

本页是 AI 代理的架构速查版。详细权威说明仍在 [`docs/architecture/system-design.md`](../architecture/system-design.md)。

## Mandatory Rule

在开始任何新的后端功能开发前，必须先阅读 `docs/architecture/system-design.md`。

## Backend Layers

| Layer | Path | Responsibility |
|---|---|---|
| 接入层 | `src/backend/api/` | HTTP/CLI/WebSocket 入口、参数校验、DTO 转换 |
| 核心编排层 | `src/backend/core/` | 用例、编排、领域契约、纯业务规则 |
| 平台能力层 | `src/backend/engines/` | Skills、RAG、可插拔能力，实现 core 定义的接口 |
| 基础设施层 | `src/backend/infrastructure/` | LLM 客户端、数据库、HTTP、日志、配置等具体实现 |

## Persistence Layer Annotations

`src/backend/infrastructure/persistence/models/` 下所有 SQLAlchemy ORM 类必须同时具备**表注释**与**列注释**，由 `hooks/shared/check_sqlalchemy_model_comments.py` 在 pre-commit 阶段强制执行：

- **表注释**：含 `__tablename__` 的类必须通过 `__table_args__` 提供非空 `comment` 字符串。支持两种写法：

  ```text
  __table_args__ = {"comment": "..."}
  __table_args__ = ({"comment": "..."}, Index(...))
  ```

- **列注释**：每个 `Column(...)` / `mapped_column(...)` 调用都必须显式带 `comment="..."` 关键字参数。

新增模型必须从一开始即满足上述两条规则；该约束同时作为 Alembic 生成的 DDL 在运维排障时的字段说明来源。

## Agent-First Capabilities

新增面向业务的具体能力时，应优先作为 Agent 可编排能力接入，通常落在 `src/backend/engines/`，并通过 skill、tool 或 capability adapter 供 `src/backend/core/` 编排层调用。

这里的具体业务能力包括但不限于：单证识别、OCR、信息抽取、审核、检索、爬虫等。

除非详细架构文档明确批准新的服务边界，默认禁止仅为单一业务能力新增独立 HTTP 服务、独立端口或旁路的用户级 API。外部请求应统一经由 `src/backend/api/` 入口进入，再由 `src/backend/core/` 的用例或 Agent 编排层通过抽象契约与注册机制调用具体能力实现。

## Dependency Direction

```text
src/<module>/api/ -> src/<module>/core/ -> src/<module>/engines/ -> src/<module>/infrastructure/ -> third-party packages
```

禁止违反以下规则：

- `src/<module>/infrastructure/` 不得导入 `src/<module>/core/`、`src/<module>/engines/`、`src/<module>/api/`
- `src/<module>/core/` 不得导入 `src/<module>/api/`；可以静态依赖 `src/<module>/engines/` 与 `src/<module>/infrastructure/`
- `src/<module>/api/` 不得直接导入 `src/<module>/infrastructure/` 或 `src/<module>/engines/`
- 行为契约优先通过 `src/<module>/core/shared/interfaces/` 中的抽象接口表达；`core/` 的装配 facade 可以直接依赖下层实现，但不得承载新业务规则

新增模块若采用相同的四层结构，同样受 `hooks/shared/check_architecture.py` 约束。

## Placement Checklist

新增代码前先判断：

1. 这是入口适配、业务编排、平台能力，还是基础设施实现
2. import 方向是否仍然向内
3. 是否在已有模块职责内扩展，而不是偷塞到最近的文件
4. 是否需要先定义接口，再做跨层实现

## Layer Assembly

- 本仓库**没有** `src/backend/composition/`：FastAPI 应用仍由
  `src/backend/api/app.py` 汇总，`src/backend/main.py` 是进程启动薄入口，
  根目录 `main.py` 是兼容包装器
- `api/` 只依赖 `core/`；`core/` 里以 `*_factory.py`、`*_composition.py` 这类
  **装配 facade** 静态导入 `engines/` / `infrastructure/` 的实现，再转给 `api/`
- 这些 facade 只做对象创建、配置转换与依赖连接，不承载新业务规则；依赖是静态的，
  因此检查器、IDE 与类型检查都能看到
- `src/<module>/composition/` 仍是检查器预留的 composition root 形态（允许依赖全部
  四层、四层不得反向导入）；将来若把装配集中过去，不需要改检查器规则

## Frontend Boundary

仓库包含两个独立前端，都是后端四层之外的 Web 客户端，通过 HTTP 或 WebSocket 调用 `src/backend/api/`：

| App | Path | Stack | 定位 |
|---|---|---|---|
| 管理平台前端 | `frontend-admin/` | Vite + React + TypeScript (pnpm) | 内部管理后台 |
| 管理终端前端 | `frontend-public/` | Next.js + TypeScript (pnpm) | Agent Runner 管理终端（静态导出随 wheel 分发） |

它们不受后端四层依赖规则约束，遵循各自目录下的前端约定（见各自 `README.md`）。

**"边界外" 指架构层次不同，不等于规划时可以忽略。** 任何带用户可见界面的功能，PRD 与实现都必须把对应前端 app 当作一等公民规划：组件、路由/页面、状态、调用 `src/backend/api/` 的客户端代码与类型同步。纯后端任务需显式声明 `No frontend impact`，而不是默默省略前端。
