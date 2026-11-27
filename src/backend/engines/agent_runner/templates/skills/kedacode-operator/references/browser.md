# 浏览器操作

可写的 runner Agent 调用共用按需浏览器入口。首次打开页面时，runner 宿主启动独立 Chromium 会话。

```bash
kc browser status
kc browser open http://127.0.0.1:3000
kc browser snapshot
kc browser click e5
kc browser fill e8 '测试组织'
kc browser press Enter
kc browser screenshot --filename tasks/evidence/issue-137/workspace.png
kc browser close
```

使用当前页面快照里的元素引用。其他支持动作通过 `kc browser --help` 查询。
截图支持 `--filename` 与 `--full-page`，路径限定当前工作树。动作输出是 Playwright CLI 文本，失败返回非零。
用 `kc browser status` 查询通道不会启动浏览器。

运行时缺失时，在 runner 机器安装 `@playwright/cli` 0.1.20 并执行其 `install-browser chromium`。
缺少宿主通道时，先核对自己是否在可写 runner 调用中；普通终端、原生会话与只读用途无该通道。
页面或登录失败需检查被测服务和测试账号。截图完成后应检查实际界面并按任务要求保存证据。

每次调用的会话隔离，调用结束后自动回收，空闲五分钟后浏览器自动关闭。
宿主通道限定浏览器动作，拒绝脚本执行和自定义配置、跨工作树截图及全局会话管理。
