# KC 终端执行器与按需项目预览

> **验证层级：概念原型（ImageGen 静态图片）**。图片用于评审终端入口与预览反馈，不是实际 Codex/Claude 截图，也不证明功能已经实现。

![kc 启动配置的 Codex 执行器，并在用户请求后返回项目预览 URL](assets/kc-terminal-agent-preview.png)

- [打开原始图片](assets/kc-terminal-agent-preview.png)
- 关联 PRD：`tasks/pending/P1-FEAT-20261009-123512-kc-agentic-entry-and-stall-supervision.md`
- [查看完整生成提示词与图片来源](assets/kc-terminal-agent-preview.prompt.md)
- [返回 Prototype Hub](hub.html)

流程概念：裸 `kc` 按设置直接启动所选执行器的原生终端界面；只有用户在对话中要求打开项目页面时，才启动已配置或经确认的本地开发命令，并在执行器回复中提供 loopback URL。用户自行访问该地址。KC 不创建网页聊天、不自动启动项目预览，也不自动打开浏览器；具体行为以关联 PRD 为准。
