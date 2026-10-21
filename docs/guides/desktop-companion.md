# macOS 菜单栏状态面板与独立桌面窗口

macOS 上优先使用 KedaCode 菜单栏 App：顶部常驻图标，点击后展开任务状态；点「固定悬浮」  
可切换成独立置顶窗口。网页看板里的「开启独立窗口」仍保留 pywebview 路径，适合临时开窗，  
它本身不会创建菜单栏图标。

两种窗口都加载 Console 的精简页面 `{console_origin}/app/companion/`，读与 Web 看板完全相同的  
本机 API；不新增任务数据源，窗口退出也不影响浏览器看板。

## 菜单栏 App

应用图标采用黑色底与浅灰色的抽象钟表符号：圆角方形表盘在右上方开口，内部保留独立的折角指针。  
菜单栏使用同一符号的透明底单色模板，随 macOS 深浅主题适配；单独收紧留白并加粗笔画，确保小尺寸可辨认。  
矢量源位于 `desktop/src-tauri/icons/keda.svg` 和 `menubar.svg`；修改后使用 `pnpm --dir desktop exec tauri icon`  
在临时目录生成图标，仅将配置引用的 `32x32.png`、`128x128.png`、`128x128@2x.png`、`icon.png`、  
`icon.icns` 同步回源码目录。菜单栏 PNG 使用 36×36 像素；不保留当前未使用的 Android、iOS 或 Windows 图标。

同一份 `keda.svg` 也是项目图标的唯一矢量源：README 顶部 hero、Console favicon（`frontend-admin/public/images`）、  
文档站 logo（`docs/assets/brand/keda.svg`，由 `mkdocs.yml` 的 `theme.logo`/`theme.favicon` 引用）与  
Console 前端站点（`frontend-public/app/icon.svg`、`favicon.ico`）都从它派生。修改图标时需一并同步这些副本并重新生成 PNG/ICO。

### 构建与安装

用户可通过 `uv tool install kedacode` 安装 CLI；首次在 macOS 交互式终端运行 `kc` 时，可选择下载与 CLI 版本和 CPU 架构匹配的菜单栏 App。也可稍后运行：

```bash
kc desktop install
```

安装器校验同一 GitHub Release 的 `SHA256SUMS`，并将 App 安装或升级到 `~/Applications/KedaCode.app`。CLI 仍由原安装器（例如 `uv`）管理；菜单栏 App 不会另装一份 CLI。安装后需从 Finder 打开该 App，菜单栏图标才会出现。首次交互提示只出现一次；脚本和机器输出不会被打断。它与 shell 补全设置菜单互相独立；两者都未配置时，App 菜单先出现。

公开 Release 的 DMG 按 Apple Silicon（arm64）和 Intel（x64）分别构建。当前 CI 使用 ad-hoc 签名，未进行 Apple 公证；macOS 首次打开可能要求用户到「系统设置 → 隐私与安全性」允许，或在 Finder 中按住 Control 点击 App 并选择「打开」。

源码开发者也可以在 macOS 本机执行：

```bash
just desktop-install
```

这会构建 `KedaCode.app` 并复制到 `~/Applications`。构建需要 macOS 12+、Rust（rustup）、  
Xcode Command Line Tools 和 pnpm。本机源码构建不签名、不公证，也不用于公开分发。

双击 `~/Applications/KedaCode.app` 后，菜单栏出现 KedaCode 图标。点击图标展开状态面板；  
点击「固定悬浮」切换成带原生标题栏的置顶窗口；「取消固定」返回图标面板。popover 失焦后  
自动收起，窗口关闭按钮只会隐藏到菜单栏。右键菜单栏图标并选择「退出 KedaCode」才会退出  
App 并回收它启动的 `kc console`。

菜单栏面板与固定悬浮窗可显示在不同桌面及其他应用的全屏空间中；在全屏浏览器里点击  
KedaCode 菜单栏图标，也应在当前空间展开面板。  
多显示器定位使用 macOS 原生逻辑坐标，在当前鼠标所在屏幕的菜单栏下方展开，  
兼容不同缩放比例以及位于主屏上方、左侧的显示器。  
macOS 13 及以上还启用 `canJoinAllApplications`，允许浮动面板加入其他应用的全屏空间  
（[Apple 窗口行为说明](https://developer.apple.com/documentation/appkit/nswindow/collectionbehavior-swift.struct/canjoinallapplications)）。

App 查找 `kc` 的位置依次包括 `~/.local/bin/kc`、`/opt/homebrew/bin/kc`、  
`/usr/local/bin/kc` 和进程 `PATH`。Finder 启动时不会继承登录 shell 的 PATH，因此这些固定路径  
是主要查找来源。启动诊断日志在 `~/Library/Logs/KedaCode/desktop-console.log`。

### 数据与行为

任务卡片首行并列显示状态与中文阶段（例如「失败 · 阶段：PR 前审查」），标题下方直接说明该阶段在做什么；  
悬停阶段可查看原始标识和说明。未记录阶段时明确标注；未知阶段保留原文并说明尚未配置解释。  
阶段优先采用最近一次 attempt 记录的失败阶段；没有失败阶段记录时回退到 Issue 时间线的最后阶段事件。  
因此阶段说明解释处理环节，不一定就是失败原因；阻塞和失败卡片另显示当前状态来源，监控告警单独展示，  
不会冒充任务状态或失败原因。网页看板列表与详情沿用相同的阶段说明。

面板紧凑呈现前 4 项活动任务，其余可在窗口内滚动。横条表示「改动树触达」，即这次运行碰到  
PRD 影响计划中的节点，**触碰不等于改对**。不可读、不可比或没有基准时会说明原因，不画空条、  
不写 `0%`。App 不打开 GitHub、不读日志文件、不计算改动树，也不新增业务存储。

“阻塞”只对应当前监控快照的 `agent/blocked` 标签；`agent/failed` 单独显示为“失败”。历史运行失败本身  
不代表 Issue 目前仍然失败或阻塞。Issue 离开监控队列后，若缺少当前状态，历史失败归入看板的  
“未知”并保留上次运行结果，不进入小窗活动列表。快照明确记录已关闭时，关闭状态优先于残留标签。  
`agent/automated-review-passed` 归入等待；以上状态随后台监控同步更新。任务状态来源显示当前 Issue 标签或  
关闭状态；`event_label_mismatch`、`dirty_worktree_mismatch` 等监控告警独立显示，不作为状态判定依据。

## 独立桌面窗口

网页看板上的「开启独立窗口」使用默认依赖 `pywebview`，临时打开一个紧凑窗口，不创建菜单栏  
图标。源码环境执行 `uv sync` 即可同步默认依赖；只检查是否安装可运行：

```bash
uv run python -c "import importlib.util; print(importlib.util.find_spec('webview') is not None)"
```

升级运行时：`uv sync --upgrade-package pywebview`。这条路径在 macOS、Windows、Linux 上打开  
独立窗口；macOS 菜单栏交互请使用上面的 `KedaCode.app`。

## 前置条件

- 本机正在运行 `kc console`，且监听回环地址（`127.0.0.1` / `localhost` / `::1`）。  
  绑定到非回环地址时，开启窗口的请求会被直接拒绝。
- 独立窗口路径使用 KedaCode 默认依赖 `pywebview`。若当前环境缺失该依赖，网页看板照常可用，  
  页面上的按钮旁会提示如何恢复默认依赖。
- 平台支持系统窗口：macOS / Windows / Linux。其余平台状态为  
  「当前操作系统不支持系统窗口」。

`pywebview` 在不同平台拉起的原生后端不同（macOS 走 PyObjC/Cocoa，Windows 走  
WebView2 或 WinForms，Linux 走 GTK/WebKit）。这类依赖是**运行时按 `import` 探测**的，  
所以「Python 包能导入」不等于「已经做成可双击的桌面 App」——真正的打包发行  
（macOS 用 py2app、Windows/Linux 用 PyInstaller）不在本次范围内，仍按 `kc console`  
的源码运行方式使用。

## 使用独立窗口

1. 打开 `http://127.0.0.1:<port>/app/dashboard/`，在「值守看板」视图里。
2. 点「开启独立窗口」。第一次会新建窗口；再次点击**只把已有窗口带到前台**，  
   不会开出第二个窗口。
3. 点窗口内的「关闭」请求关窗。后端只发一个关闭工单，由窗口自己退出，  
   **不做进程强杀**，因此不会留下半个窗口。

窗口与看板用**同一个刷新节奏**（15 秒一轮任务摘要）；触达读数每轮最多读取窗口里  
前 4 项，滚动到的行写明「本轮未取数」，不会为剩下的行追加取数。

## Console origin 从哪里来

窗口固定加载 `{console_origin}/app/companion/`，这个 origin **不是**前端或请求体给的：

- 后端取 ASGI `scope["server"]`，即 uvicorn 实际绑定的 host/port。这个值不来自  
  `Host` 头，所以伪造 `Host` 改变不了判定结果。
- 状态变更类的 POST 还要求请求头的 `Origin` 与该绑定同源：scheme 必须是 `http`、  
  主机必须是回环别名（`127.0.0.1` / `localhost` / `[::1]` 指向同一个本机 Console，  
  都算同源）、端口必须等于实际端口、且不带 path / query / fragment。
- 不一致或缺失 → `403`，并且**不会**启动任何进程。
- 校验通过后，**窗口地址由绑定地址推出**（IPv6 字面量会补上方括号），不回显  
  请求头里的 host：`localhost` 与 `127.0.0.1` 都能开窗口，但 helper 的启动参数与  
  WebView 加载的 URL 一律写后端自己绑定的那个 origin。

`GET .../desktop-companion/status` 是只读探针，不要求 `Origin`（浏览器对同源 GET  
可能根本不发这个头，原生窗口里的轮询也是普通 HTTP 客户端）。

## 排查

| 现象                   | 先查什么                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------- |
| 按钮旁提示桌面运行时缺失         | `uv run python -c "import webview"`；源码环境执行 `uv sync` 后重启 Console                             |
| 写「当前操作系统不支持系统窗口」     | 该平台没有可置顶窗口的常规路径，用网页看板                                                                        |
| 点了没出现窗口，状态是 `failed` | 看 `detail` 里的退出码；无 GUI 会话（SSH、cron、`launchd` 后台）是最常见原因                                       |
| Console 重启后旧窗口还留着    | 新 Console 按 `console_session` 认不出前任的窗口，因此状态回到「未开启」；再点一次会开一个新窗口，旧窗口按会话身份判定前任 Console 已不在，自行退出 |
| 窗口显示「读不到 Console」    | Console 停止或端口变了。恢复后窗口自己重新加载；期间看板仍可直接用                                                        |
| 同一入口反复点击开出多个窗口       | 不应发生：已存在本入口的窗口时只递增聚焦工单。看到两个窗口通常意味着**有两个 Console 进程**，每个入口各管自己的窗口，互不接管                        |

辅助文件与判据：pidfile 名为 `desktop-companion.pid`，三行分别是 helper 的 pid、  
命令行标记 `-m backend.engines.desktop_companion.helper`、写入它的 Console 会话身份。  
命令行标记避免 pid 复用把别的进程误认成窗口，会话身份避免另一个 Console 进程把  
别人的窗口报成「已聚焦」或替对方关窗。

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
