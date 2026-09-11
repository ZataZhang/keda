# Evidence Report: agent CLI 适配层（统一命令构造器 + 声明式注册表）

对应 PRD：`tasks/archive/P1-FEAT-20260911-010513-agent-cli-adapter-layer.md`
基线 commit：`c6faff87441dc4d626be4a56f78080a76c62e1de`（分支点）；本 PRD 全部改动为该基线上的未提交改动，随本证据包同一 commit 提交。

## rv-1 · 黄金命令行快照

- `export_golden_argv.py` 在改造前旧树（基线 `c6faff8`）直采 12 条命令行 → `golden-argv-before.json`。
- `iar agent doctor claude codex kimi --all-profiles --json` 在最终树重采 → `golden-argv-after.json`。
- `diff` 输出为空；两份 JSON、导出脚本与 SHA 归档于本目录（来源详见 `golden-argv-provenance.md`）。

## rv-3 · 字面量 / 沙箱告警 / 坏输入

- `rv3-shell-expansion-and-sandbox.md`：`$(whoami)` / `a|b` / `*.py` 在 doctor `--json`（`rv3-doctor-output.json`）与假 agent 子进程 `sys.argv`（`rv3-fake-agent-sysargv.txt`）同时以字面量出现；`{cwd}` 占位符按闭集展开；无沙箱参数的可写 profile 触发显式 WARN。
- `red-negative-controls.md`：未注册 agent 名（exit=1）、bin 不在 PATH（exit=1）、未注册协议（exit=1 并列出全部已注册协议）、未注册展开器（exit=1 并列出全部已命名展开器）、`--agent` 未注册名（exit=2 并列出全部注册名）——全部真实入口红色输出。

## rv-5 · list / doctor 绿色路径

- `rv5-agent-list-doctor.md`：`iar agent list` 列出 codex/claude/kimi/pi × 4 用途；doctor 对已注册名退出码 0 并打印完整命令行与投递方式。

## rv-6 · 搜索断言

- `rv6-rv7-validation.md`：旧构造点符号（`_build_*_command` / `_AGENT_COMMAND_BUILDERS` / `should_filter_claude_stream`）在 `src/` 零残留；`"(claude|codex|kimi)"` 命中 24 → 18 个文件，逐一归类（注册表定义/内置默认、auto 兜底、docstring/文案、模型名检测）。

## rv-7 · 全量回归与门禁

- `just test all`：1921 passed。
- `just lint --full`：全绿（含架构分层、max-file-lines、重复检测、quality-flag）。
- `uv run mkdocs build --strict`：通过。
- `run-agent-once-line-count.md`：`run_agent_once.py` 1046 → 977 行（净减少）。

## 豁免项（manual / opt-in，遗留人工确认）

- rv-2：pi 只读真实运行 + 负控（需本机 pi 真实进程）。
- rv-4：`iar deliberate --agents pi` 真实辩论轮（同上）。
- 旧 `[agent_runner.labels]` 配置的 `iar run` 真实轮询轮（需 fixture 仓库 + fake gh）；单测层等价证据：`test_agent_spec_config.py` 旧键兼容三例。
- 安装真·import 即抛异常的协议插件包；单测层等价证据：`test_output_protocol_registry.py::test_import_failure_does_not_fall_back_to_plain`。

## 独立审查

独立 verifier 结论 **PASS-with-notes**（Note 1 timeout 丢弃已修复并复验）；详情见同目录 `P1-FEAT-20260911-010513-agent-cli-adapter-layer.verifier-report.md`。
