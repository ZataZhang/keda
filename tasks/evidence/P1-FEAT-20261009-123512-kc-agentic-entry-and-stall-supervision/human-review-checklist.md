# 人工审查清单 · KedaCode Agent 原生终端与停滞监督（Issue #256）

## 当前结论

建议暂缓本 PRD 的人工验收：rv-1 已观察到真实 `kc` TTY 启动 Codex，但 provider 在首次输入前因宿主权限错误退出；真实多轮对话、对话触发 preview、终端回传 URL 和运行截图都没有产生。概念图只表示设计意图。这里记录 §9.2 的三项人工决定，不代表已接受任何未完成行为。

请逐项回复“确认”或“指出差异”。若确认当前缺失证据不可接受，请回复“暂缓验收，补齐 rv-1”；这不会修改 PRD 的验收范围。只有观察到真实 provider 对话与 preview 后，才可对相应行为作验收结论。

## 1. 执行器沿用原生权限设置

**决定：** 确认原生 TTY 使用所选执行器的正常权限、sandbox 与确认配置；不附加无人值守 skip-confirm 参数。若权限提示被跳过，执行器可能在没有交互确认的情况下写入仓库。

> 确认原生 TTY 使用所选执行器的正常权限、sandbox 与确认配置；不附加无人值守 skip-confirm 参数。（§2 决定三；rv-1）

**证据与限制：** [rv-1-automated-tests.txt](rv-1-automated-tests.txt) 中的 argv 测试确认没有加入无人值守权限参数；[rv-1-kc-native-session.txt](rv-1-kc-native-session.txt) 记录真实 TTY 启动 Codex 后，在首次输入前收到 `Operation not permitted`。没有观察到原生权限提示，因此当前**不足以确认完整 TTY 行为**。

**回复：** 确认设计要求并暂缓验收，或指出权限/沙箱方面需要纠正的差异。

可选复跑：`UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest tests/test_cli_agent_session_entry.py -k interactive_argv_never_carries_unattended_permission_flags --no-testmon -q --no-header`

## 2. 停滞监督默认关闭且配置可覆盖

**决定：** 确认监督默认关闭；启用后默认每 30 分钟巡检，周期、停滞阈值和执行器均可通过配置覆盖。若默认开启或配置无法覆盖，可能产生未预期模型调用或中断任务。

> 确认监督默认关闭；启用后默认每 30 分钟巡检，周期、停滞阈值和执行器均可通过配置覆盖。（§2 决定一；rv-4）

**证据：** [rv-4-config-supervision.txt](rv-4-config-supervision.txt) 记录隔离配置加载结果 `SUPERVISOR_LAYERING_OK {"enabled": false, "check_interval_seconds": 29, "stalled_after_seconds": 47, "agent": "repo-supervisor"}`。另有默认关闭零调用与 interval override 测试。

**回复：** 确认以上默认值与覆盖规则，或指出期望调整的配置行为。

可选复跑：`UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest tests/test_agent_runner_stall_supervision.py -k 'disabled_supervision or supervision_off_by_default or invalid_supervisor_intervals' --no-testmon -q --no-header`

## 3. 原生 TTY 入口与按需项目预览

**决定：** 确认裸 `kc` 默认启动配置的 provider TUI；只用 `--agent` 覆盖执行器。项目预览只在对话请求时启动并返回 URL；已查看终端/preview 概念原型。若裸启动就运行项目或自动打开浏览器，会增加不必要的副作用；若 URL 不是 loopback，会扩大本地服务暴露范围。

> 确认裸 `kc` 默认启动配置的 provider TUI；只用 `--agent` 覆盖执行器。项目预览只在对话请求时启动并返回 URL；已查看终端/preview 概念原型。（§2 决定四；rv-1）

![终端执行器与按需项目预览概念图（设计参考，不是运行截图）](../../../docs/prototypes/assets/kc-terminal-agent-preview.png)

**证据与限制：** [rv-1-kc-native-session.txt](rv-1-kc-native-session.txt) 记录裸 `kc` 已进入真实 Codex TTY，provider 随后在对话开始前退出；同一日志确认裸启动没有启动 preview。隔离 repo 的真实 `kc preview` 启停与 loopback URL 见 [rv-4-config-supervision.txt](rv-4-config-supervision.txt)，但它由验证脚本直接调用 CLI，**不能证明 agent 在用户明确请求后才触发，也不能证明 URL 已由 agent 回到 provider 对话**。PRD 9.1 要求的 `rv-1-kc-terminal-preview.png` 尚未生成。

**回复：** 确认该行为边界及设计参考，或指出差异；若接受标准仍要求真实对话证据，请回复“暂缓验收，补齐 rv-1”。

可选复跑：`just prd review P1-FEAT-20261009-123512-kc-agentic-entry-and-stall-supervision.md`
