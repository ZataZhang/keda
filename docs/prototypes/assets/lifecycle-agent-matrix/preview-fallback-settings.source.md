# preview-fallback-settings.png 来源

- 类型：Chrome 浏览器截图，非 AI 生成。
- 代码入口：`docs/prototypes/lifecycle-agent-matrix.html` 的 `settings` screen。
- 状态准备：运行 `python3 -m http.server 18888 --bind 127.0.0.1 --directory docs/prototypes`，打开 `?screen=settings&capture=1&focus=fallback&demo=fallback-preset-saved`；评审截图聚焦回退卡片，Claude / Kimi / Codex 分别绑定 `fallback-claude` / `fallback-kimi` / `fallback-codex`，并展示保存后的 TOML 预览。此聚焦只影响截图视图，完整交互仍可从原型链接打开。
- 截图方式：Google Chrome headless；视口 `1440×1200`，device scale factor `1`。
- 画布尺寸：1440×1200。
- 采集日期：2026-10-09。
- 用途：展示回退 Agent 预设绑定和保存预览。
- 演示数据：`claude-sonnet-5-5 / max`、`kimi-k2.6 / high`、`gpt-5.4 / xhigh` 为具体展示组合，不代表当前仓库已配置对应的 Agent 参数模板或账号可用性。
- 验证层级：interactive prototype；底图来自真实 Settings 截图，覆盖层和写入预览是交互原型，不代表生产实现或后端读写验证。
- 失效条件：回退卡片结构、preset 映射格式或保存预览变化时重截。
