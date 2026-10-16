## 人审导航 / Human Review Navigation

这份证据由真实 Hosted Compose Runner 生成，运行于 Docker Desktop 的 Linux VM（ARM64）。执行者已交叉核对视频时长、动作轨迹、截图状态、输入哨兵搜索和容器端口/挂载；它证明通用 X11 操作与录屏链路，不代表客户业务应用已在此环境部署。人工确认仍待 PR 评审。

| Oracle | 你要看的结果 | 呈递物与打开方式 | 预期内容 | 已交叉核对 |
|---|---|---|---|---|
| rv-2（真实桌面操作） | Agent 右键打开 Openbox 菜单并点击 `Reconfigure`，录下菜单关闭结果。 | WebM：[`rv-2-desktop-menu-selection.webm`](</private/tmp/keda-issue-265-rebase/tasks/evidence/P2-FEAT-20261016-152354-hosted-desktop-interaction/rv-2-desktop-menu-selection.webm>)；本地整页 bundle：[`human-review-checklist.html`](</private/tmp/keda-issue-265-rebase/tasks/evidence/P2-FEAT-20261016-152354-hosted-desktop-interaction/human-review-checklist.html>)，运行 `open "/private/tmp/keda-issue-265-rebase/tasks/evidence/P2-FEAT-20261016-152354-hosted-desktop-interaction/human-review-checklist.html"`。 | 5.6 秒 VP9、1440×900、10 fps；先有桌面菜单，实际菜单项点击后菜单关闭。 | `ffprobe` fresh process、`.actions.jsonl` 右键/左键事件、前后截图均核对。 |

![真实 Runner 屏幕：Openbox 菜单打开](rv-2-desktop-menu.png)

菜单打开状态（X11 `scrot` 实际截图）。运行 `open "/private/tmp/keda-issue-265-rebase/tasks/evidence/P2-FEAT-20261016-152354-hosted-desktop-interaction/rv-2-desktop-menu.png"` 查看原图。

![真实 Runner 屏幕：点击菜单项后菜单关闭](rv-2-desktop-after-action.png)

点击 `Reconfigure` 后的桌面状态（X11 `scrot` 实际截图）。运行 `open "/private/tmp/keda-issue-265-rebase/tasks/evidence/P2-FEAT-20261016-152354-hosted-desktop-interaction/rv-2-desktop-after-action.png"` 查看原图。

与本次桌面能力边界有关的人工确认见 PRD §9.2 Human-Confirmed 第 1 项：请在 [PR #269](https://github.com/ZataZhang/keda/pull/269) review evidence 后回复“接受”或列出需要修正的边界。

## Machine Evidence Index

### rv-1：默认关闭、关闭态 fail-closed

- `rv-1-hosted-desktop-config.txt` 是 Hosted Compose 插值后的 JSON 摘要：默认关闭时使用 `iar-runner:latest`、桌面 build arg/runtime 均为 `false`；启用时使用 `iar-runner:desktop`，两处均为 `true`；两种配置的 `ports=[]`，不挂 Docker socket。
- `rv-1-disabled-control.txt` 由另一个 Hosted Compose one-off Runner 生成。桌面 runtime 被关闭后，`kc-computer record start` 与 `kc-computer screenshot` 各自返回退出码 1，均提示 `Hosted desktop is disabled`。

### rv-2：Linux X11 实际操作与媒体产物

- `rv-2-desktop-menu-selection.webm`：执行者从 Runner 内通过 `kc-computer click` 右键菜单并左键选择 `Reconfigure`；视频时长 5.6 秒，fresh `ffprobe` 显示 `matroska,webm`、VP9、1440×900、10 fps。
- `rv-2-desktop-menu-selection.actions.jsonl`：5 行有效 JSONL，记录 `recording_started`、右键 (700, 440)、左键 (740, 540)、截图和 `recording_stopped`，含 UTC 时间。
- `rv-2-desktop-operation.webm` 与 `rv-2-desktop-operation.actions.jsonl`：额外录制的文本脱敏负控；输入 `trace-secret-sentinel` 后轨迹仅记 `character_count: 21`，哨兵原文不在 JSONL 中。
- `rv-2-desktop-menu.png` 与 `rv-2-desktop-after-action.png`：分别呈递打开菜单和点击后的关闭画面；画面是容器内 Xvfb 的真实输出，不是合成图。
- `rv-2-source-runtime-identity.txt`：对 Docker 镜像内脚本和挂载 checkout 脚本做 Python AST 比较，结果 `kc_computer_ast_equal=True`；镜像是在格式化 hook 重排空白前构建，规范化 AST 与最终工作树相同。
- 主视频 SHA-256：`9a9dacd2e4e66779375d42666a79a5a30ebdf0bab3f569a138124f25457549e1`。
- 主动作轨迹 SHA-256：`c9f22830e8d8396c7b30af6a2c4baf5656cdca92f42ca9b0385a78e0a21e2eaa`。
- 脱敏负控视频 SHA-256：`50fff6741bc6d16d77e43f680c63d51729b8ac6b6c2037fc8f13f2c8c1b25db8`；对应轨迹 SHA-256：`461562b11aef633e58b0f4a8d45f098df31726fce7236e2748656a310ffeb74f`。
- 两张截图 SHA-256：菜单打开 `25e3dd1de6203c06beadb31ace644f298709733f767d06678dae21a14ec908a2`；点击后 `26e314c88dc960b06e42599c6efcc3a6e009f9c9bc0c4f25bc3567fad5a14a22`。

### rv-3：容器隔离边界

- `rv-3-hosted-isolation.txt` 来自实际 Runner 的 `docker inspect` 与 `/proc`：`ports={}`、`port_bindings={}`，挂载列表无 `/var/run/docker.sock`。
- 实际 Xvfb 进程参数含 `-nolisten tcp`，并存在 `/tmp/.X11-unix/X99`。`/proc/net/tcp` 唯一 LISTEN 项是 `127.0.0.11:44907`，UID 0 且没有容器内进程持有该 inode；这是 Docker 网络 namespace 的内部 loopback 项，不是映射给宿主的应用端口。
- 构建镜像为 `sha256:15b88b1edfd7c2c8e2d0ca067cda66f26349856821c7408940b1a889e2cd9eaf`（Linux ARM64）。

## Validation Status

- `just lint --reuse`：通过。
- 首次 `just lint --full`：只因 `check-test-flag` 的测试运行标记过期而失败；未运行测试套件以刷新该标记。
- `SKIP=check-test-flag just lint --full`：全量静态 hooks 通过（ruff、ruff-format、架构、文档一致性、PRD checklist 等）；这不是测试通过。
- Hosted Compose `config --format json` default-off/opt-in：通过；桌面 Docker 镜像构建：通过；真实 Compose Runner 操作、截图、录屏和 fresh `ffprobe`：通过。
- `uv run mkdocs build --strict`：通过；构建输出只有已有导航项缺省与本地 mkdocstrings formatter 未安装提示，没有文档错误。
- `just prd status`：影响树 18/18 个可判定节点已触达；本地 ignored review bundle 和原始 evidence 不计入 Git 触达分母。
- 自动化测试套件：not run；VNC/TCP 配置变异负控：not run。关闭态 fail-closed 负控与类型文本脱敏负控已经按证据条目执行。
- 独立 verifier：NOT RUN；详情见同目录 `.verifier-report.md`。因此不登记 `verified_tree_sha`，PRD 继续留在 `tasks/pending/`，PR #269 保持 draft。

原始视频、截图、轨迹和命令捕获件遵循仓库 `.gitignore`，不进入代码 diff。此环境没有可用的浏览器自动化 surface（Chrome tab 不可用且原生 app 控制超时），所以不伪造 GitHub 附件链接；本地 review bundle 是视频与图片的实际播放入口。PR #269 的 WIP comment 会提供此 worktree 的直接 `open` 命令。机器证据 manifest 位于同目录 `evidence.json`。
