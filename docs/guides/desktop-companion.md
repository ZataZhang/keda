# 桌面进度小窗（Desktop Companion）

本指南说明 KedaCode 的**可选**桌面小窗：一个能留在其他桌面应用上方的紧凑窗口，
值守时不用一直盯着浏览器标签页。

窗口本身**不是新的数据来源**，它加载的就是 Console 自己的一个精简页面
`{console_origin}/app/companion/`，读的是与 Web 看板完全相同的那几个本机只读 API。
因此小窗能看到什么、不能看到什么，与看板一致；小窗挂掉也不影响看板。

## 它解决什么，不解决什么

解决：离开浏览器后仍有一个能置顶、能一眼扫过最多 4 个活动任务的小面板，
以及一个明确的关窗入口。

不解决：

- **不是任务完成度**。窗口里的横条是「改动树触达」，含义是「这次运行碰到了
  PRD 影响计划里的几个节点」，**触碰 ≠ 改对**。读不到、不可比、没有基准时，
  窗口按各自原因留白并写明原因，不画空条、不写 `0%`。
- **不是新的监控采集**。不打开 GitHub、不读日志文件、不算改动树，也不新增存储。
- **不是常驻服务**。没有窗口时不占资源；Console 退出后残留的窗口会自行退场。

## 前置条件

- 本机正在运行 `kc console`，且监听回环地址（`127.0.0.1` / `localhost` / `::1`）。
  绑定到非回环地址时，开启窗口的请求会被直接拒绝。
- 已安装可选依赖 `desktop`（见下）。未安装时网页看板照常可用，
  页面上的按钮旁会写明「桌面运行时未安装」。
- 平台支持系统窗口：macOS / Windows / Linux。其余平台状态为
  「当前操作系统不支持系统窗口」。

## 安装与卸载

桌面运行时是**可选** extra，不在默认依赖里：

```bash
# 安装（uv 环境下用 extra 形式）
uv sync --extra desktop

# 只想知道当前解释器有没有装好
uv run python -c "import importlib.util; print(importlib.util.find_spec('webview') is not None)"

# 卸载：回到不含该 extra 的同步即可
uv sync
```

升级就是重装：`uv sync --extra desktop --upgrade-package pywebview`。

`pywebview` 在不同平台拉起的原生后端不同（macOS 走 PyObjC/Cocoa，Windows 走
WebView2 或 WinForms，Linux 走 GTK/WebKit）。这类依赖是**运行时按 `import` 探测**的，
所以「Python 包能导入」不等于「已经做成可双击的桌面 App」——真正的打包发行
（macOS 用 py2app、Windows/Linux 用 PyInstaller）不在本次范围内，仍按 `kc console`
的源码运行方式使用。

## 使用

1. 打开 `http://127.0.0.1:<port>/app/dashboard/`，在「值守看板」视图里。
2. 点「开启桌面小窗」。第一次会新建窗口；再次点击**只把已有窗口带到前台**，
   不会开出第二个窗口。
3. 点窗口内的「关闭」请求关窗。后端只发一个关闭工单，由窗口自己退出，
   **不做进程强杀**，因此不会留下半个窗口。

## Console origin 从哪里来

窗口固定加载 `{console_origin}/app/companion/`，这个 origin **不是**前端或请求体给的：

- 后端取 ASGI `scope["server"]`，即 uvicorn 实际绑定的 host/port。这个值不来自
  `Host` 头，所以伪造 `Host` 改变不了判定结果。
- 状态变更类的 POST 还要求请求头的 `Origin` 与该绑定**完全一致**：scheme 必须是
  `http`、主机必须是回环别名、端口必须等于实际端口、且不带 path / query / fragment。
- 不一致或缺失 → `403`，并且**不会**启动任何进程。

`GET .../desktop-companion/status` 是只读探针，不要求 `Origin`（浏览器对同源 GET
可能根本不发这个头，原生窗口里的轮询也是普通 HTTP 客户端）。

## 排查

| 现象 | 先查什么 |
|---|---|
| 按钮旁写「桌面运行时未安装」 | `uv run python -c "import webview"`；按上文安装 `desktop` extra |
| 写「当前操作系统不支持系统窗口」 | 该平台没有可置顶窗口的常规路径，用网页看板 |
| 点了没出现窗口，状态是 `failed` | 看 `detail` 里的退出码；无 GUI 会话（SSH、cron、`launchd` 后台）是最常见原因 |
| Console 重启后旧窗口还留着 | 属预期内的短暂现象：窗口按 `console_session` 身份判定前任 Console 已不在，会自行退出 |
| 窗口显示「读不到 Console」 | Console 停止或端口变了。恢复后窗口自己重新加载；期间看板仍可直接用 |
| 反复点击开出多个窗口 | 不应发生。检查是否有多个 Console 进程共用同一 pidfile |

辅助文件与判据：pidfile 名为 `desktop-companion.pid`，并用命令行里的
`-m backend.engines.desktop_companion.helper` 标记校验，避免 pid 复用把别的进程
误认成窗口。

## 触达读数为什么是「弱信号」

窗口里的 `touched/judgeable` 来自现有
`GET /api/v1/agent-runner/backlog/prds/{encoded_path}/progress` 契约，
看板与小窗共用同一个来源，没有第二套检测逻辑。以下情况一律**不画条**并写明原因：

- `no prd`：该 Issue 没关联 PRD，没有可比对象；
- `no worktree` / `no_tree` / `no_nodes`：找不到执行工作区、没有比较树或树里没有节点；
- `no base`：没有有效 merge-base，此时显示 `-`，而不是 `~0/y`；
- `terminal_archive`：已归档终态，不参与分支比较；
- `ambiguous`：多个工作区候选，无法确定比较对象；
- `unavailable`：查询或 git 命令失败。

`judgeable = 0` 属于「无法判定」，同样不画条、不写 `0%`；`unresolvable` 节点以
`?n` 单独披露，不进分母。
