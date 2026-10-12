## 人审导航 / Human Review Navigation

- PRD：`tasks/pending/P1-FEAT-20261012-103222-unified-runtime-dashboard.md`
- PR / CI：尚未创建；本次交付是待办 PRD 与交互原型，不包含生产实现。
- 人审入口：启动 `uv run mkdocs serve -a 127.0.0.1:8000`，然后打开 [运行时看板原型](http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html#dashboard)。
- 当前验证边界：以下两张图均为 component preview / design intent。桌面图使用静态 fixture，不证明原生窗口、当前 worktree 触达值或实时 Console 数据。

### 1. 默认 Dashboard、运行状态、实时日志与折叠调用

![运行时 Dashboard 目标态](../../../docs/prototypes/assets/runtime-board-preview.png)

> 验证层级：**component preview / design intent**；任务、日志和调用来自固定 fixture，尚无生产路由截图。
> 本地图已纳入仓库版本控制，可从仓库根目录运行：`open docs/prototypes/assets/runtime-board-preview.png`

逐项期望值：Dashboard 首屏以生命周期任务卡片为主；颜色之外有状态文本；选择任务后能看到日志区域，结构化工具调用在初始状态折叠。生产入口、实时日志 offset 与调用账本一致性仍待实现后验证。

### 2. 紧凑桌面窗与改动树触达进度

![紧凑桌面窗口与改动树触达条目标态](../../../docs/prototypes/assets/runtime-widget-preview.png)

> 验证层级：**component preview / design intent**；静态目标示例，不是系统桌面窗口或当前代码树测量结果。
> 本地图已纳入仓库版本控制，可从仓库根目录运行：`open docs/prototypes/assets/runtime-widget-preview.png`

逐项期望值：有限窗口空间内最多显示四项任务；比例条显示现有改动树读数 `7/12`，旁边显示 `?2` 与“改动树触达 / 弱信号”；不可比较项不显示假 0%。该条是已触达节点比例，不代表任务完成率、质量或验收结果。原生置顶、关闭、断连行为与后端真实进度 API 尚待实现后验证。

### 执行者已替你核对过什么

- Prototype Hub 已登记 Dashboard、桌面窗和评测原型入口；Hub → 原型 → 返回 Hub 的真实原型路径已在桌面和 390px 窄屏浏览器验证，采集时 `pageErrors=0`。
- 原型进度条绑定现有改动树触达语义，并明确披露不可判定节点；静态 `7/12` 只验证信息呈现方式，不声称实时检测已经接入。
- 没有运行生产 App、原生窗口或后端/API 测试；当前待办 PRD 未实现功能，因此没有生产截图、PR 或 CI 结论。
