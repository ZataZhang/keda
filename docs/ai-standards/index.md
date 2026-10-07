# AI Standards Hub

`docs/ai-standards/` 是本仓库面向 AI 编码代理的**统一规范源目录**，也是 AI 相关规则的 `source of truth`。

目标很简单：

- 把通用规范按主题拆分，而不是按工具拆分
- 让 GitHub Copilot、Claude、Cursor、Codex 等入口共享同一套规则
- 避免 `AGENTS.md`、`CLAUDE.md`、`.github/copilot-instructions.md` 之类入口文件各自膨胀成独立规范正文

## Source Of Truth

本目录是 AI 规范的主入口，但不是唯一的详细技术文档来源。

权威关系如下：

- `docs/ai-standards/`：AI 通用规范主入口
- `docs/architecture/system-design.md`：后端四层架构的详细权威文档
- `tests/playwright-e2e/README.md`：Playwright 包的详细适配说明

工具入口文件只做适配：

- `AGENTS.md`
- `CLAUDE.md`
- `.cursor/commands/cursor.md`
- `.github/copilot-instructions.md`
- `.github/instructions/*.instructions.md`

## Read Order

开始任务时，建议按这个顺序读取：

1. 本页
2. 与任务最相关的标准页
3. 若涉及后端新功能，再读 `docs/architecture/system-design.md`
4. 若涉及 Playwright，再读 `tests/playwright-e2e/README.md`

## Standards Map

- [Architecture](architecture.md)
- [Alembic Migrations](alembic.md)
- [Code Reuse](code-reuse.md)
- [Naming](naming.md)
- [Comments And Docstrings](comments-docstrings.md)
- [Documentation](documentation.md)
- [Testing](testing.md)
- [Tooling](tooling.md)

## 讨论与规划的 ROI 原则

每次需求、方案与任务规划讨论默认评估 ROI（投入产出比），优先推荐满足当前需求的最小有效方案。

- 说明当前收益、发生频率，以及实施、验证、维护和使用复杂度带来的成本；考虑现有配置、复用或手工操作等更低成本的替代方案。
- 有可靠依据时估算节省的时间、成本或回本周期；收益不确定时明确标注假设并做定性判断，不强行量化或编造精确数字。
- 必要的正确性、安全和架构约束应计入收益，不以短期省事为由省略。
- 不为未提出的未来需求默认增加抽象、扩展点或基础设施；可选改进说明触发条件，不默认纳入本次实施范围。
- 根据讨论规模简短给出「现在做、缩小范围做、暂缓」的判断及理由；无需为每次回答增加固定表格、评分或程序门禁。

## Agent 指令与程序边界

本项目以 AI Agent 理解并执行指令为核心，不以通过程序强制 Agent 100% 遵守全部规范为目标。不要把每一条 skill、模板或交付要求都转成校验器、解析器、守卫测试或硬门禁，使 Agent 工作流逐渐变成由程序逐项控制的软件工程流程。

发现交付偏差时，先区分执行方没有遵守清晰指令，还是指令、上下文或流程设计存在歧义。前者应修正交付并改善执行，后者应简化和统一规范；不要默认归因为「缺少程序验证」，也不要默认提出新增校验器。

验证用于证明产品行为、真实入口和必要的安全边界，而不是替代 Agent 对规范的理解与判断。新增程序约束必须有独立的产品正确性或安全需求依据，不能仅以「防止 Agent 再次漏掉一条规范」为理由。既有验证按其现有约定执行，本原则不要求删除已有测试或门禁。

## When To Update This Hub

以下情况应同步更新本目录：

- 架构边界或依赖方向发生变化
- 命名、Docstring、编码或注释规范发生变化
- 常用命令、工具链或验证流程发生变化
- 新增一类长期维护的技术子树，例如新的前端约束或新的测试栈

## Maintenance Rules

- 优先修改本目录中的主题页，再同步各工具入口
- 入口文件应保持简短，只保留最关键的高信号摘要
- 不要把一整份长文复制到多个入口文件中
- 若新增主题页，同时更新 `mkdocs.yml` 导航和相关入口文件引用
