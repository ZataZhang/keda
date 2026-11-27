# Agent 按需浏览器操作

`kc run` 与 daemon 的可写 Agent 调用共用 `kc browser`，适用于 Codex、Claude Code、Pi 等具备 shell 工具的执行器。Agent 可以根据页面快照决定下一步点击、输入或截图。

## 安装运行时

在运行 runner 的机器安装 Node.js 和官方 Playwright CLI，并安装 Chromium。

```bash
npm install -g @playwright/cli@0.1.20
playwright-cli install-browser chromium
```

当前适配以 `@playwright/cli` 0.1.20 为基线。会话参数、JSON 配置与空闲超时依据该版本 `playwright-core/lib/tools/cli-client/registry.js` 及 `coreBundle.js` 的 CLI 实现。上游文档见 [Playwright CLI](https://github.com/microsoft/playwright-cli)。

本功能面向 macOS 和 Windows 本地 runner，两端使用 Python 标准库回环 HTTP 通道和子进程 API，Linux 可使用同一入口。跨平台验证范围以交付结果为准。

## 操作当前调用的浏览器

执行任务需要浏览器时，runner 会在 prompt 中提供下面的入口说明。

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

`snapshot` 返回页面结构和元素引用，后续动作使用本轮快照中的引用。`screenshot` 支持可选的元素引用、`--filename` 和 `--full-page`，输出路径须位于当前工作树内。未指定路径时保存到工作树的 `.kedacode/browser/`。成功输出包含截图的绝对路径，`--json` 模式另提供 `screenshot_path` 字段，方便执行器直接读取图片。截图产物由 Agent 按验收要求检查，再写入证据报告。

其他动作包括 `goto`、`find`、`dblclick`、`type`、`hover`、`check`、`uncheck`、`select`、`resize`、`reload`、`go-back`、`go-forward`、`tab-list`、`tab-new`、`tab-select`、`tab-close`、`dialog-accept` 与 `dialog-dismiss`。完整入口使用 `kc browser --help` 查询。动作结果沿用 Playwright CLI 文本，动作失败返回非零退出码。

## 生命周期与隔离

runner 在每次可写 Agent 调用期间创建一个仅监听 `127.0.0.1` 随机端口的内部通道，通过 `KC_BROWSER_URL`、`KC_BROWSER_TOKEN` 向该调用传递连接。第一次 `open` 才在宿主进程启动 Chromium，`status` 只查询通道状态。

本机进程执行器通过 `IProcessRunner.supports_browser_host` 声明通道能力，协议路由适配器和 Issue 日志分流包装器均转发该声明。远端执行器和未声明此能力的自定义适配器沿用原有 prompt 与调用行为。

浏览器命令由 runner 宿主执行，Chromium 在 Agent shell 沙箱外创建。该通道是调用期内部能力，每次调用使用独立的随机会话身份和内存浏览器上下文，并行任务、恢复调用及独立 verifier 分别建立会话。

调用结束或抛错时关闭会话。headless 会话空闲五分钟后由 Playwright CLI 自动关闭，runner 意外退出时同样适用。命令墙钟上限为 75 秒，页面导航上限为 30 秒。宿主临时配置位于 `~/.kedacode/browser-host/`，正常收尾会清理。

通道仅支持文档列出的浏览器动作，宿主持有 CLI 配置、会话名与浏览器启动参数。`eval`、`run-code`、自定义配置、全局会话管理和输出到其他工作树的截图会被拒绝。浏览器子进程沿用 E2E 环境过滤，runner 的模型和 GitHub 凭据不会传入。

只读用途不授予浏览器通道，也会剔除继承的连接参数。直接在普通终端或未由 runner 托管的原生会话中调用，会返回缺少宿主通道的明确错误。

## 失败排查

缺少运行时时，按安装步骤补齐 CLI 与 Chromium，再发起新的任务调用。通道已关闭时回到当前 runner 调用核对进程状态。页面断言或导航失败时，读取动作输出并检查目标工作树的服务和账号。登录态来自当前隔离会话，需按目标仓库的测试环境准备。
