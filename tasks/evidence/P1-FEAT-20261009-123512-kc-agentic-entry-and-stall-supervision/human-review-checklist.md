# 人工审查清单 · KedaCode Agent 原生终端与停滞监督（Issue #256）

## 当前结论

rv-1 至 rv-4 的执行器侧证据已齐备，**可以进入人工验收**。rv-1 的真实 TTY 现场已在交付树同一 HEAD 的隔离 clone 中采集完成：裸 `uv run kc` 把终端交给 Codex CLI v0.162.0 原生 TUI，provider 报告仓库 cwd 与可发现的 `kedacode-operator` skill，用户在对话中明确要求后才启动 `kc preview start` 并回传 loopback URL，随后由同一会话停止；画面见 [rv-1-kc-terminal-preview.png](rv-1-kc-terminal-preview.png)，逐行记录见 [rv-1-kc-terminal-preview.txt](rv-1-kc-terminal-preview.txt)。

请逐项回复"确认"或"指出差异"。三项决定都不因执行器已自检而自动通过：需要人工判断的是**权限边界、配置默认值与入口行为是否符合期望**。采集环境与交付工作树的差异（宿主沙箱导致改用 clone、clone 内关闭用户级 skill 检查、终端画面为真实 PTY 画面的转译）在下方各项与证据报告「验证限制」中逐条披露，请一并审阅是否可接受。

## 1. 执行器沿用原生权限设置

**决定：** 确认原生 TTY 使用所选执行器的正常权限、sandbox 与确认配置；不附加无人值守 skip-confirm 参数。若权限提示被跳过，执行器可能在没有交互确认的情况下写入仓库。

> 确认原生 TTY 使用所选执行器的正常权限、sandbox 与确认配置；不附加无人值守 skip-confirm 参数。（§2 决定三；rv-1）

**证据与限制：** [rv-1-automated-tests.txt](rv-1-automated-tests.txt) 的 argv 测试确认 interactive profile 不含无人值守权限参数（仅 `--cd {cwd}`，`prompt_delivery = none`）。[rv-1-kc-terminal-preview.txt](rv-1-kc-terminal-preview.txt) 与 [rv-1-kc-terminal-preview.png](rv-1-kc-terminal-preview.png) 记录真实 TUI 自行显示其权限模式（该机器上为 provider 既有的 `permissions: YOLO mode`），KedaCode 侧未注入任何权限或审批参数；只读轮里 provider 主动遵守"不改动文件、不启动服务"。需要人工判断的点：终端显示的权限档位来自 provider 自身配置，KedaCode 不代为收紧——若验收期望由 KC 强制更严格档位，属于范围变更，请指出。

**回复：** 确认该边界（KC 不附加权限、provider 策略照旧），或指出需要 KC 介入权限的方向。

可选复跑：`UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest tests/test_cli_agent_session_entry.py -k interactive_argv_never_carries_unattended_permission_flags --no-testmon -q --no-header`

## 2. 停滞监督默认关闭且配置可覆盖

**决定：** 确认监督默认关闭；启用后默认每 30 分钟巡检，周期、停滞阈值和执行器均可通过配置覆盖。若默认开启或配置无法覆盖，可能产生未预期模型调用或中断任务。

> 确认监督默认关闭；启用后默认每 30 分钟巡检，周期、停滞阈值和执行器均可通过配置覆盖。（§2 决定一；rv-4）

**证据：** [rv-4-config-supervision.txt](rv-4-config-supervision.txt) 记录隔离配置加载结果 `SUPERVISOR_LAYERING_OK {"enabled": false, "check_interval_seconds": 29, "stalled_after_seconds": 47, "agent": "repo-supervisor"}`，含代码树复跑块（脚本 SCRIPT_EXIT=0、`tests/test_preview_settings.py` 5 passed）。本地状态落点修复已作为提交 `95e341a6`（tree `701b1cc2…`）落地，交付树由此推进，门禁在提交树上对该命令复跑。另有默认关闭零调用与 interval override 测试。

**回复：** 确认以上默认值与覆盖规则，或指出期望调整的配置行为。

可选复跑：`UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest tests/test_agent_runner_stall_supervision.py -k 'disabled_supervision or supervision_off_by_default or invalid_supervisor_intervals' --no-testmon -q --no-header`

## 3. 原生 TTY 入口与按需项目预览

**决定：** 确认裸 `kc` 默认启动配置的 provider TUI；只用 `--agent` 覆盖执行器。项目预览只在对话请求时启动并返回 URL；已查看终端/preview 概念原型。若裸启动就运行项目或自动打开浏览器，会增加不必要的副作用；若 URL 不是 loopback，会扩大本地服务暴露范围。

> 确认裸 `kc` 默认启动配置的 provider TUI；只用 `--agent` 覆盖执行器。项目预览只在对话请求时启动并返回 URL；已查看终端/preview 概念原型。（§2 决定四；rv-1）

![rv-1 真实 PTY 终端画面（裸启动 → 只读核对 skill → 明确请求后 kc preview start 回传 loopback URL → kc preview stop 终止精确进程组 → --agent 覆盖入口）](rv-1-kc-terminal-preview.png)

![终端执行器与按需项目预览概念图（设计参考，不是运行截图）](../../../docs/prototypes/assets/kc-terminal-agent-preview.png)

**证据与限制：** [rv-1-kc-terminal-preview.txt](rv-1-kc-terminal-preview.txt) 逐项对应本决定的期望：裸启动与只读轮 `listening = False` / `DEV_SERVER_PIDS: none` / Chrome 主进程 PID 与 preflight 完全一致（未自动打开浏览器）；明确要求预览后 provider 自行调用 `uv run kc preview start` → 端口监听、`HTTP_PROBE_OF_REPLIED_URL: 200`、dev PID 73320/73327，终端回复 `http://127.0.0.1:31789`；停止轮 `kc preview stop` 输出 `Stopped preview process group 73296`，随后 `HTTP_REPROBE_AFTER_STOP: 000`、registry 无登记；`uv run kc --agent codex` 覆盖入口同样进入原生 TUI 且未启动项目服务。"没有唯一命令时先询问"由 [rv-1-preview-gate-negative-control.txt](rv-1-preview-gate-negative-control.txt) 在同一真实 CLI 入口上证明（0 候选 / 2 候选 / 缺 `--confirm` / `--confirm` 不相等四组全部 exit 2 且探针为空）；预览生命周期正控与三组内置负控的可复跑链记录在 [rv-1-reproducible-oracle.txt](rv-1-reproducible-oracle.txt)（每次门禁复跑由 `rv1_prepare_clone.py` 把 clone 快进到当时 HEAD）。需要人工判断的两点：(1) 采集在快进到交付 HEAD `95e341a6` 的**隔离 clone** 完成（交付工作树所在目录被宿主沙箱拒绝 provider 写入，历史与定位见 [rv-1-kc-native-session.txt](rv-1-kc-native-session.txt) 末尾），(2) 终端画面是 `capture-pane -e` 真实画面的 ANSI→HTML→无头 Chrome 转译，非屏幕实拍。另注意 PATH 上的旧发行 `kc` 尚无 `preview` 子命令（provider 先试它失败后改用仓库内 `uv run kc`），属发布节奏而非本 Feature 缺陷。

**回复：** 确认该行为边界与上述两点披露可接受，或指出差异（例如要求在交付工作树目录内复现、或要求屏幕实拍）。

可选复跑：`PYTHONPATH=src python3 tasks/evidence/P1-FEAT-20261009-123512-kc-agentic-entry-and-stall-supervision/scripts/rv1_native_tty_session.py`（需真实 PTY 与已配置的 provider）
