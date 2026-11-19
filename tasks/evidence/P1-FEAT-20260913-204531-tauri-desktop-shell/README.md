# P1-FEAT-20260913-204531 验收证据（2026-10-28 实跑）

采集环境：macOS 27.0.1，真实构建产物 `~/Applications/KedaCode.app`（`just desktop-install`），
Finder 启动，未使用 mock 进程或 stub HTTP。代码树基线：main @ c0126337，`desktop/src-tauri/src/main.rs` 无本地改动。

## 逐项结论

| oracle | 结论 | 证据 |
|---|---|---|
| rv-1 构建安装 | 通过 | `just desktop-install` 产出 13.10 MiB `KedaCode.app` + dmg，装入 `~/Applications` |
| rv-1 Finder 启动 + sidecar | 通过 | 壳日志 `已启动 kc console，端口 60514，程序 /Users/zata/.local/bin/kc` |
| rv-1 同源数据 | 通过 | 面板显示 AI Assistant#97 / 运行中 / 25m，与 `rv1-b-tasks-api.json` 中 `issue_title`=`[Bug] Main CI 日检与修复 — 2026-10-28`、`status`=running、`duration_seconds`≈1489 一致 |
| rv-1 点击图标 / 固定悬浮 / 取消固定 / 失焦收起 | **用户对话确认通过（2026-10-28），无自动截图** | 本机是 AweSun 远程会话，合成鼠标事件不进入菜单栏（见 `diagnostics/`），因此交互序列由用户手动完成并在对话中确认；面板在我截图前已因失焦自动收起，佐证收起行为生效 |
| rv-2 并存不抢端口 | 通过 | 独立 console 占 8313，App sidecar 用 60514，两者 `/console/version` 均 200（`rv2-4-coexist.txt`） |
| rv-2 退出回收 | 通过 | 退出后 sidecar pid 消失、独立 console 仍 200（`rv2-2`、`rv2-3`） |
| rv-2 负向对照 | 通过 | `stop_console` 临时改为 no-op 后重建，退出 App 残留 sidecar 82072（`rv2-5-negative-control.txt`）；随后 `git checkout` 还原并重建 |
| rv-3 仅 Homebrew 路径定位 | 通过 | 临时移走 `~/.local/bin/kc`、仅留 `/opt/homebrew/bin/kc`，Finder 启动后日志 `程序 /opt/homebrew/bin/kc`，sidecar 200（`rv3-1-homebrew-only.txt`） |
| rv-3 全路径无 kc | 分支已触发，界面未取证 | 日志 `启动失败：没有找到 kc 命令。请先安装 KedaCode CLI…`（`rv3-2-no-kc.txt`）；引导页视觉需点击图标后才能截图，本轮未取得 |
| sidecar 启动失败"后端未运行"错误页 | 未验证 | 未构造可执行但启动失败的 kc 场景 |

## 未执行

- `just lint` / 后端测试：采集时工作区存在其他会话的未提交改动（`justfile.shared`、`scripts/shared/`、M13 PRD 等），全量 lint/测试结果不能归因于本 PRD。
- `uv run mkdocs build --strict`：通过（4.14s，无 warning 级失败）。

## 环境副作用与已还原项

- 临时创建 `/opt/homebrew/bin/kc` 符号链接，已删除。
- 临时移走 `~/.local/bin/kc`，已还原（`kc 0.5.0`）。
- 负向对照改动 `main.rs` 已 `git checkout` 还原，工作区无本 PRD 的代码改动。
- 测试期间启动的独立 `kc console`（8313）已停止。

## diagnostics/

远程会话下合成点击不生效的排查产物（菜单栏截图、AweSun 光标、AXPress 记录）。
保留用于说明 rv-1 交互为何必须由人手动完成；**不是验收证据**。
