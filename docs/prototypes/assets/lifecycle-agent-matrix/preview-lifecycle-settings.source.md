# preview-lifecycle-settings.png 来源

- 类型：浏览器截图，非 AI 生成。
- 代码入口：`docs/prototypes/lifecycle-agent-matrix.html` 的 `lifecycle-settings` screen。
- 状态准备：启动 `python3 -m http.server 18888 --directory docs/prototypes`，以 `?capture=1&screen=lifecycle-settings&focus=overview` 打开；初始范围为全局，预设 `plan` / `work` / `review` 展示样例绑定。截图聚焦四列生命周期矩阵与区块导航；同页下方的执行器回退卡片见 `preview-fallback-settings.png`。静态设置页底图的旧侧栏文字由原型覆盖层显示为当前名称 Backlog。
- 截图命令：使用安装的 Google Chrome headless 模式，视口 `1600×1200`、device scale factor `1`；捕获模式隐藏评审 Dock 与说明标题，保留产品外壳。
- 画布尺寸：1600×1200。
- 采集日期：2026-10-09。
- 用途：Prototype Hub 的统一设置页缩略图；截图展示矩阵分组、配置范围和区块导航，页面可滚动查看下方模型预设与执行器回退设置。
- 验证层级：interactive prototype；静态产品外壳来自 `settings-real.png`，覆盖层与交互（包括 Backlog 标签修正）是原型，不代表生产实现或后端读写证据。
- 失效条件：生命周期分组、统一设置页或执行器回退卡片布局、其使用的真实设置页底图发生变化时重截。
