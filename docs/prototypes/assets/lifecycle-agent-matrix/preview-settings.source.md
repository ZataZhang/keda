# preview-settings.png 来源

- 类型：Chrome 浏览器截图，非 AI 生成。
- 代码入口：`docs/prototypes/lifecycle-agent-matrix.html` 的 `settings` screen。
- 状态准备：运行 `python3 -m http.server 18888 --bind 127.0.0.1 --directory docs/prototypes`，打开 `?screen=settings&capture=1`；页面显示 Agent 标签和通往统一生命周期与执行器设置页的入口。
- 截图方式：Google Chrome headless；视口 `1440×1200`，device scale factor `1`。
- 画布尺寸：1440×1200。
- 采集日期：2026-10-09。
- 用途：Settings 主视图预览，确认旧 Agent-only 生命周期矩阵与单独回退卡片已移除，统一设置入口清楚可见。
- 验证层级：interactive prototype。底图来自真实 Settings 截图；本次内容由 HTML 覆盖层绘制，不代表生产实现或后端读写验证。
- 本图不展示模型预设或执行器回退候选；对应状态见 `preview-fallback-settings.png`。
- 失效条件：Settings 页面结构、Agent 标签表、覆盖层布局或统一设置入口文案变化时重截。
