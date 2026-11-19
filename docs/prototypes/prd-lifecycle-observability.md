# Console 运行时看板与模型评测基线原型

本页从 Prototype Hub 进入，包含多个互相连接的交互视图：Dashboard 运行时任务卡片、桌面悬浮窗预览、既有多评审评测，以及 M13 可复现基线评测；还包括 Backlog 生命周期详情与 Stats。它是 **interactive prototype**（静态 fixture 驱动），不连接后端、SQLite、模型服务或桌面原生运行时。

## 打开方式

- 从 [Prototype Hub](hub.html) 搜索「Console 运行时看板与模型评测」，点击名称或预览进入；选择行内其他区域可查看说明和关联原型。
- 直接入口：[打开交互原型](prd-lifecycle-observability.html#dashboard)
- M13 评测基线：[打开重复运行与校准视图](prd-lifecycle-observability.html#evaluation-baseline)
- 本地预览：在仓库根目录运行 `uv run mkdocs serve -a 127.0.0.1:8000`，然后打开 `http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html#dashboard`
- 可直接打开既有多评审页：`http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html#evaluations`
- 可直接打开 M13 基线页：`http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html#evaluation-baseline`

## 演示状态与交互

```text
Prototype Hub
└── Dashboard（默认态）
    ├── 按状态筛选 / 搜索任务 / 选择卡片
    │   └── 右侧详情：生命周期阶段、耗时、Agent 预设与日志
    │       ├── 暂停或继续日志跟随（仅切换演示状态）
    │       └── 展开「工具调用」查看结构化调用摘要；原始输出保留
    ├── 「开启桌面悬浮窗」→ 紧凑的多任务独立窗口 → 查看改动树触达条 → 关闭并返回
    ├── 模型评测
    │   ├── 同一评测集的多个 Agent / 模型预设对比，checks 与评审分数分开
    │   ├── 评审模型设置 → 多选命名模型预设 → 保存演示设置
    │   ├── 「开始对比」→ 固定样本/基线/预设配置 → 静态模拟候选完成
    │   ├── 外发预览 → 选择评审模型与样本，查看完整 prompt/payload → 授权或拒绝
    │   └── 样本详情 → 查看各评审者的分维评分、理由和分歧；不生成综合总分
    ├── 评测基线
    │   ├── 切换真实 Agent / @noop 与 @gold 校准模式；重复次数、留出集更新候选执行估算
    │   ├── 显示逐题通过次数、稳定性与独立的执行故障计数；不生成合成总分或阈值
    │   └── 原型固定样例不创建真实 run 或调用模型
    ├── Backlog → 单个 PRD 生命周期 / 调用时间线
    └── Stats → PRD 生命周期统计
```

### 关键点击语义

- 左侧 Dashboard、模型评测、评测基线、Backlog、Stats 都是同一画布内的可达视图；URL hash 跟随视图，可直接深链。
- Dashboard 卡片点击后在右侧显示相同任务的阶段、预设、尝试和日志；筛选会保留当前可见任务，否则自动选择第一张可见卡片。
- 工具调用区使用原生 `<details>`，默认折叠；展开只显示调用摘要，日志文字不会被删除或改写。
- 「开启桌面悬浮窗」打开带桌面背景的紧凑视觉预览，可见四张任务行和三个真实比例条；预览可通过关闭按钮或 `Esc` 关闭。它不能证明窗口能覆盖其他桌面应用；真实置顶行为必须由原生桌面运行时验证。
- 「评审模型设置」打开多选命名模型预设对话框；保存只更新本页按钮状态，不保存配置。
- 「开始对比」打开 suite/base/execution preset/judge preset 配置；点击开始后只模拟候选执行完成，然后进入逐模型外发预览。
- 外发预览可以切换评审模型和候选样本查看完整静态 payload；勾选授权后才可点模拟确认；拒绝显示零请求反馈。确认与拒绝都只切换原型文案，不调用模型。
- 「查看样本」打开两个评审器各自的四维分数与理由，并显示逐维分歧；无加权综合总分；对话框可用关闭按钮或 `Esc` 收起。
- 左侧「评测基线」进入 M13 重复运行页面；「真实 Agent / 校准候选」切换会同步预设区、留出选项、校准摘要和预计执行次数。创建按钮只显示固定原型反馈，不写账本、不启动候选或调用模型。
- 页面底部原型 Dock 的 Hub、说明、可点击区域开关和重置属于评审工具，不是产品 UI。重置回到 Dashboard 默认态。

## 评审要点

- Dashboard 是默认视图；卡片用色彩和状态文字共同区分执行中、审阅等待、阻塞、完成。
- Dashboard 卡片以生命周期阶段和运行时长呈现，不推断任务完成百分比。悬浮窗若关联了有有效工作区基准的 PRD，使用既有改动树读数 `touched/judgeable` 绘制比例条，标签写“改动树触达（弱信号）”并披露不可判定数量；该条描述已触达节点，不描述任务完成率、正确性或验收通过。没有 PRD、工作区、有效比较基准或读数服务失败时不显示假 0%，按原因显示不可比较。
- 运行日志与结构化调用记录分层：文字日志持续追加，工具调用摘要可以折叠；原始输出仍可见。
- 悬浮窗紧凑显示多个任务的标题、状态、阶段、耗时与改动树触达弱信号；不显示日志、任务正文、代码差异或凭据；用户打开后可关闭。
- 评测对比固定样本集与 rubric 版本；确定性检查、LLM 评分、时延和可得成本分开呈现。
- 每个评审模型的分维评分与理由分别保留；按维度显示中位数和分歧范围，不生成总分或把评审分数当作测试、verifier 或人工验收结果。
- 评审模型设置可以同时选择多个纯文本 judge preset；候选执行结束后逐次查看目标模型和确切 payload，再授权或拒绝外发。
- M13 评测基线按题和预设展示重复通过数、稳定/不稳定与执行故障；稳定性是对重复判分结论的描述，不是综合得分或合格阈值。

## 模拟范围与生产实现待验证项

- 所有 Issue、仓库、时间、阶段、日志、调用、基准数据、成本和评分均为**静态 fixture**，只用来评审信息结构和交互。
- Dashboard 卡片字段与现有 Console 的 Issue 快照、生命周期阶段、`agent_invocation_events` 调用账本和 Issue 输出端点的目标复用关系已映射；该原型不证明 API 已返回对应数据。
- 评测比较展示固定样本、执行预设、多个评审模型、逐维评分与分歧；M13 基线页的校准、重复结果和执行故障是静态 fixture。候选执行、schema 持久化和模型调用均未实现。
- 外发预览使用固定静态 payload；选择/授权/拒绝只切换页面状态，不访问 API/provider、不扣费、不写文件。
- 「桌面悬浮窗」是网页中的桌面场景示意，不会创建系统窗口，也不验证始终置顶、安装、升级、深链或本机 IPC；最多四行任务和进度值是固定 fixture，不是当前工作区的实时读数。
- 页面没有模型调用、网络请求、定时器或持久化设置；数据不会离开浏览器。
- 真实实施仍需验证桌面 helper 缺失、Console 离线、旧日志缺失、结构化调用账本不可用、评审模型超时/格式错误、多个评审器分歧和取消评测等状态。

## 原型来源

- HTML/CSS/JavaScript 交互原型，复用现有 Console 原型 shell 和 Prototype Hub。
- 视觉参考：`frontend-public/app/(app)/app/dashboard/page.tsx`、`frontend-public/components/agent-runner/` 与 `frontend-public/app/globals.css`。
- 生命周期与调用详情词汇参考 `docs/prototypes/prd-lifecycle-observability.html` 原有时间线示例；本次将 Dashboard 与评测视图整合进该原型入口。
- 本原型未使用 AI 生成图片；Hub 目标截图从交互原型真实页面入口截取，来源记录见 [`runtime-board-preview.source.md`](assets/runtime-board-preview.source.md)、[`runtime-widget-preview.source.md`](assets/runtime-widget-preview.source.md)、[`runtime-evaluation-preview.source.md`](assets/runtime-evaluation-preview.source.md)、[`runtime-evaluation-baseline-preview.source.md`](assets/runtime-evaluation-baseline-preview.source.md) 和 [`runtime-consent-preview.source.md`](assets/runtime-consent-preview.source.md)。
