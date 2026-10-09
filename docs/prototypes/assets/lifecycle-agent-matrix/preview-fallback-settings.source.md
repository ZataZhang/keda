# preview-fallback-settings.png 来源

- 类型：Chrome 浏览器截图，非 AI 生成。
- 代码入口：`docs/prototypes/lifecycle-agent-matrix.html` 的 `lifecycle-settings` screen。
- 状态准备：运行 `python3 -m http.server 18888 --bind 127.0.0.1 --directory docs/prototypes`，打开 `?screen=lifecycle-settings&capture=1&focus=fallback&demo=fallback-preset-saved`；评审截图聚焦统一设置页内的执行器回退卡片，Claude / Claude / Kimi / Codex 依次绑定 `fallback-claude`、`fallback-claude-high`、`fallback-kimi`、`fallback-codex`，并展示数组表保存预览。此聚焦只影响截图视图，完整交互仍可从原型链接打开。
- 截图方式：Google Chrome headless；视口 `1600×1200`，device scale factor `1`。
- 画布尺寸：1600×1200。
- 采集日期：2026-10-09。
- 用途：展示执行器回退候选预设绑定和保存预览，并与统一设置页入口关联。
- 演示数据：同一 Claude 执行器分别使用 `sonnet-5.5 / max` 和 `sonnet-5.5 / high`；Kimi、Codex 分别使用 `kimi-k2.6 / high`、`gpt-5.4 / xhigh`。这些是具体展示组合，不代表当前仓库已配置对应的 Agent 参数模板或账号可用性。
- 验证层级：interactive prototype；底图来自真实 Settings 截图，覆盖层和写入预览是交互原型，不代表生产实现或后端读写验证。
- 失效条件：统一设置页结构、执行器回退候选、数组表格式或保存预览变化时重截。
