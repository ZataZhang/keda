# PRD 生命周期观测与执行分析原型

本页归档 `tasks/pending/P1-FEAT-20260921-161621-prd-lifecycle-observability.md` 的交互原型，用于在实施前确认三件事：Roadmap 单 PRD「执行过程」的信息层级、端到端耗时的拆分口径怎么读、以及失败/阻塞/观测缺口如何在不覆盖历史的前提下呈现。这是一个 **interactive prototype**（静态 fixture 驱动），不代表生产功能已实现或已接入真实 API。

## 打开方式

- 文档站入口：[Prototype Hub](hub.html)，从列表选择「PRD 生命周期观测与执行分析」
- 直接入口：[打开交互原型](prd-lifecycle-observability.html)
- 本地预览：`uv run mkdocs serve`，然后打开 `http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html`

## 状态模型

```text
Hub
└── Roadmap · PRD 已选中 · 「执行过程」标签
    ├── 顶部：当前阶段 / Agent 尝试 / 完整性
    ├── 四类耗时：端到端（主）= 有效执行 + 等待 + 阻塞（互斥）
    ├── 生命周期时间线（按发生时间升序）
    │   └── 点击任一事件 → 事件详情抽屉（时间 / 执行者 / 原因 / 阶段 / run id）→ 关闭
    ├── 场景切换：正常轨迹 ⇄ 失败与重试 ⇄ 观测写入失败
    └── 「查看统计」→ PRD 执行统计
        └── 点击 PRD 名称 → 返回 Roadmap 生命周期详情
```

## 关键点击语义

- 侧栏 **Roadmap / 统计** 与详情页右上角 **查看统计**：在 Roadmap 详情与仓库级统计之间切换。
- 时间线任一事件（点击或 `Enter` / `Space`）：打开事件详情抽屉；`×` 或 `Esc` 关闭。
- **正常轨迹 / 失败与重试 / 观测写入失败**：切换三套稳定场景，同时刷新时间线、当前阶段与四类耗时。
- Stats 表格第一行 PRD 名称：返回其生命周期详情；其余归档 / 失败行为静态展示。
- Stats 顶部 `未关联 PRD` 提示与表格末行：演示旧记录降级，不提供生命周期链接。
- 底部原型 Dock（评审工具层，非产品 UI）：**可点击区域开关**（默认开启）、**↺ 重置**（回到初始场景与 Roadmap 视图）、**返回 Hub**、**原型说明**。

## 三套演示场景

| 场景 | current_phase | 演示的产品语义 |
|---|---|---|
| 正常轨迹 | `reviewing` | 首次验证失败被保留，随后自动恢复并验证通过；末尾等待审查显示「进行中」，端到端计算到当前时刻。 |
| 失败与重试 | `blocked` | 连续验证失败 + 人工重试，历史不被覆盖；随后进入阻塞，阻塞时间单独累计、不计入有效执行。 |
| 观测写入失败 | `validating` | 一次事件写入失败，页面顶部显示**数据不完整**告警、完整性标为「数据不完整」；runner 主流程仍完成。 |

## 口径与降级（原型要评审的核心结论）

- **端到端是主指标**：从首次进入执行队列到归档的完整历时，而不是一次 runner 调用的 `duration_seconds`。
- **执行 / 等待 / 阻塞互斥**：详情页固定展示三项拆分，并在下方用一行等式复算「端到端 = 有效执行 + 等待 + 阻塞」，强调前端只格式化、不重算。
- **进行中不伪造结束时间**：进行中的 run 端到端「计算到当前时刻」，状态显示「进行中」；阻塞中显示「阻塞中」与当前阶段。
- **失败不被覆盖**：失败事件与各次 attempt 耗时全部保留在时间线上，后续成功只追加、不重写历史。
- **旧记录降级**：无法可靠归属 PRD 的记录标为「未关联 PRD」，在最近运行列表可见，但被排除出完成分位数统计。
- **旁路降级**：观测写入失败不阻断 runner，但页面必须明确显示数据不完整，而不是静默假装完整。

## 模拟范围

- 模拟 `RoadmapPrd`、生命周期 run/event、attempt、四类耗时与仓库统计聚合后的**目标展示形态**。
- 所有数字、run id、事件 ID 与文案均为**稳定 fixture**，不含随机数与定时器，也不暗示已存在生产 API。
- 未实现真实日志流、数据刷新、分页、搜索过滤、权限、错误请求与跨仓库切换。

## 设计依据

- 产品 shell、色彩与排版参考 `frontend-public/app/(app)/`、`frontend-public/app/globals.css` 与现有 Roadmap / Stats 页面；原型完全由 HTML/CSS/JavaScript 构建。
- 生命周期详情沿用统一 PRD 详情的「PRD 原文 / 验收证据」标签结构（`frontend-public/components/roadmap/prd-detail.tsx`），新增「执行过程」。
- Stats 入口沿用 `frontend-public/app/(app)/app/stats/page.tsx`，把统计口径从单次 runner 记录改为完整 PRD 生命周期。
- 字段命名对齐后端只读契约：明细 `GET /v1/agent-runner/roadmap/prds/{encoded}/lifecycle`，统计 `GET /v1/agent-runner/console/stats/prd-lifecycle?repo_id=&days=`；`current_phase` 与 `event_type` 取值来自 PRD §7 的闭集。
- 本原型不使用 AI 生成图片，因此不需要 `.prompt.md`；代码与本说明即来源记录。

## 生产实现仍需确认

- 生命周期事件的持久化 schema、旧运行记录迁移与数据保留周期。
- 「执行时间 / 等待时间 / 阻塞时间」的精确定义与跨时区聚合，以及进行中 run 的「当前时刻」基准。
- Issue 与 PRD 路径无法关联时的降级标记规则，以及分位数排除口径。
- 观测写入失败的可诊断错误上下文（仅结构化摘要，不落敏感原始输出）。
