# AI Agent Entry Guide

本文件是仓库的**跨工具 AI 入口适配层**，不是规范正文。
统一规范源在 `docs/ai-standards/`，详细后端架构权威文档仍在 `docs/architecture/system-design.md`。

## Read Order

任何任务都先读：

1. `docs/ai-standards/index.md` — 规范地图与权威关系

然后根据任务类型按需补读下表对应的页面，**不要无差别地一次性读完所有标准页**。

## When To Read Which Standard

| 标准页 | 何时必须读 | 何时可以跳过 |
|---|---|---|
| `docs/ai-standards/index.md` | 任何任务开始时 | 永远不跳过 |
| `docs/ai-standards/architecture.md` | 新增/修改 `src/backend/` 下的模块、调整跨层依赖、新增 engine 或 infrastructure 实现、新增对外 HTTP/CLI 入口 | 只改 `frontend-admin/`、文档、测试、构建脚本时 |
| `docs/architecture/system-design.md` | 开始**任何**新的后端功能、新增四层之间的契约、改动 composition root、新增独立服务边界 | 已经只是小范围 bugfix 或参数调整，不动层间契约时 |
| `docs/ai-standards/code-reuse.md` | **任何**新增或修改代码前；考虑提取 helper、判断是否复制粘贴、函数参数 ≥4 个、文件接近 500 行、运行 `just lint --reuse` 之前；交付前必须完成其中的 **AI 编码自检清单** | 纯文档排版或纯配置改动且无任何代码逻辑变更 |
| `docs/ai-standards/naming.md` | 新增 Python 变量、函数、类、模块；命名风格不确定时 | `tests/playwright-e2e/` 内的 TypeScript 改动 |
| `docs/ai-standards/comments-docstrings.md` | 新增/修改公共 Python API；写模块/类/函数 docstring；做文件 I/O 涉及编码问题 | 仅改私有实现细节且不涉及公共 docstring 时 |
| `docs/ai-standards/documentation.md` | 改动公共函数签名、配置项、业务流程；新增长期文档页；更新 `mkdocs.yml` 导航 | 纯内部重构且无对外行为或文档变化 |
| `docs/ai-standards/testing.md` | **任何代码变更开始前**；制定验证策略、收集证据或声称完成前必须重新核对；写 PRD 的 Realistic Validation Plan；**遇到测试失败或打算修改 `tests/guards/` 守卫测试时** | 仅改注释、纯文档排版或无可执行行为变化的纯配置 |
| `docs/ai-standards/tooling.md` | 选择运行命令；改 `justfile` / `pre-commit` / `mkdocs` / Docker 配置；处理 PRD 归档流程；处理 lint flag 或重复检测 hooks；**改动 `kc` CLI 表面（子命令 / 旗标 / 退出码 / 机器输出）或随包 `kedacode-operator` skill**；撰写提交信息 | 在已熟悉常用 `just` 命令、且本次不动工具链配置与 CLI 表面时 |
| `docs/ai-standards/alembic.md` | 新增、重命名或修改 Alembic migration；检查迁移链或 `down_revision` | 不涉及 `alembic/versions/` 时 |

`tests/playwright-e2e/` 是独立 TypeScript/Node 包，遵循该目录自己的 `README.md`，不强制套用 Python 规范。

## Critical Summary

- 项目以 AI Agent 理解并执行指令为核心；不为追求 100% 规范遵守率而逐条增加程序校验或硬门禁。指令执行偏差先修正执行结果、指令与上下文，不默认转成校验器开发；详见 `docs/ai-standards/index.md` 的「Agent 指令与程序边界」。
- 后端必须遵守四层依赖方向：
  `src/backend/api/ -> src/backend/core/ -> src/backend/engines/ -> src/backend/infrastructure/`
- Python 项目优先使用 `uv` 和 `just`
- 新增 Alembic migration **必须**通过 `just new-migration <slug>`（或其底层 `scripts/shared/alembic/new_migration.sh`）生成；禁止手工创建、命名或伪造秒级时间戳。生成后再仅用补丁填写 `upgrade()` / `downgrade()`，并执行 `uv run alembic heads` 确认唯一 head。
- 公共 Python API 使用 Google Style Docstrings；后端启用 Ruff `D100`–`D107`/`D419` 强制检查
- 内部注释、docstring、JSDoc/TSDoc 统一使用中文（专有名词除外）
- 前端启用 `eslint-plugin-jsdoc` 强制公共函数/类/方法写 JSDoc/TSDoc（当前 warn，逐步升级 error）
- Python 文本文件 I/O 必须显式写 `encoding="utf-8"`
- 变量命名必须具有来源、类型或状态语义，避免 `data`、`item`、`res`
- 新增或修改代码前先搜索现有实现；禁止复制粘贴后微调，参数超过 4 个时收敛到对象
- 除非用户明确要求，否则不要自动执行 `git add`、`git commit`、`git push` 等 Git 变更操作
- 提交信息统一用英文并遵循 Conventional Commits（`type(scope): subject`，标题 ≤ 72 字符，正文解释 why），详见 `docs/ai-standards/tooling.md` 的 Commit Messages 小节
- 单代码文件非空行不超过 1000 行；`just lint` 会对此发出警告
- 随手想法先落 `tasks/inbox/`：只在**想法边界**捕获（用户明确要求，或话题收敛到可开 PRD 的状态），条目 = 用户原话逐字**引用** + 标注为 `AI 派生` 的背景块，**只追加**到 `tasks/inbox/ideas.md`（禁止改写已有条目）；AI 维护 `tasks/inbox/summary.md` 做总结；想法成熟后用 PRD 流程升级到 `tasks/pending/`。详见 `docs/guides/idea-inbox.md`
- PRD 对应任务全部完成后：生成验证计划、收集证据；独立 verifier `PASS` 后，executor 必须勾选证据充分支持的非人工 Acceptance Checklist 项并标注证据、完成 Final Reconciliation、让验收状态横幅与 §9 对齐，随交付改动一并将 PRD 从 `tasks/pending/` 归档到 `tasks/archive/`。**归档只代表执行侧交付完成**，不等人工验收：`Human-Confirmed` 空框带着 `🧍 待人工验收` 归档，人确认后（对话/审查清单，或一个明确声明“合并即验收”、唯一关联该 PRD 且已发布证据的 PR merge——含 squash，须核对最终 Git tree）只回填验收记录（勾选 `Human-Confirmed`、横幅改 `✅ 已验收`、追加 Change Log）；人工验收若发现 PRD 自身 oracle/范围未达成，把 PRD 移回 `tasks/pending/` 重开，需求本身变了则新开关联 PRD
- PRD 必须包含 Realistic Validation Plan，验收清单需覆盖最高可行保真度的真实入口验证，或说明无可执行行为变更
- 前端截图必须标注验证层级；临时预览页、直接渲染组件或手工注入状态仅属于 `component preview`，不得称为真实验证。涉及 Dialog、Portal、父级布局或用户流程时，验收证据必须保留对应生产边界；无法走真实入口时必须披露限制
- 变更代码时同步更新 `docs/` 与 `mkdocs.yml`
- 改动 `kc` CLI 表面（新增/改名子命令、旗标、退出码或机器可读输出）时，必须同步随包 `kedacode-operator` skill（`src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md`）与 `docs/`，避免 agent 侧知识漂移；详见 `docs/ai-standards/tooling.md` 的 CLI Surface And Packaged Skill Sync
- 守卫测试（`tests/guards/`，文件头标注"守卫测试（guard test）"）失败时，修复触发它的源代码或配置，不要修改守卫测试本身让测试通过；仅当约定本身变更时才改守卫测试，并同步更新对应文档。修改 `tests/guards/**` 需 `GUARD_UPDATE_ACK=1 git commit`
- 新增给密钥类的配置变量（如 `api_key_env`、密钥别名、provider 注册信息）应放到 `config.toml`，实际密钥值仍由 `.env/.env.local` 注入；默认 env 未填写时，配置加载仍须正常完成
- `.env.example` 中非密钥类变量应保持 `# KEY=默认值` 的注释状态，仅作为示例；密钥类变量（含 API key、密码、token，以及可能携带凭据的连接字符串如 `DATABASE_URL`、`REDIS_URL`）保留未注释的空值（如 `OPENAI_API_KEY=`），确保变量名可见且不会误用默认值
- `tests/playwright-e2e/` 是独立 TypeScript/Node 包，使用 `npm`，不强制套用 Python SSA 命名规范

## Maintenance Rule

- 共享规范优先写入 `docs/ai-standards/`，不要把长篇规则重新复制回本文件
- 新增标准页时，同时在上方 **When To Read Which Standard** 表中加一行触发条件，保持入口可路由
