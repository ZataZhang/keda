# Verifier Report: agent CLI 适配层（统一命令构造器 + 声明式注册表）

对应 PRD：`tasks/archive/P1-FEAT-20260911-010513-agent-cli-adapter-layer.md`
审查人：独立 verifier Agent（与执行者分离，只读审查）；审查时点：2026-09-11，最终树（含审查后修复）。

## 结论

**PASS-with-notes** → Note 1 已修复并复验；交付通过。

## 复跑验证（verifier 亲自执行）

1. `iar agent doctor claude codex kimi --all-profiles --json` 与 `golden-argv-before.json` diff 为空——FR-3 逐字节一致成立。
2. 旧构造符号（`_build_*_command` / `_AGENT_COMMAND_BUILDERS` / `should_filter_claude_stream`）在 `src/` 零命中。
3. `git diff HEAD --stat`：65 files / +4254 / −720，与 PRD 大型重构叙述相称。

## FR 抽查

- FR-1 ✓ `agent_invocation.py` 仅 import dataclasses/pathlib/core 模型，无 engines/infrastructure。
- FR-4 ✓ `agent_spec.py` 覆盖全部声明字段，4 agent × 4 profile 齐全。
- FR-6 ✓ argv 字面量拼接、无 `shell=True`、`_EXPANDERS` 闭集仅 `git_writable_roots`。
- FR-7 ✓ 三协议经 `pyproject.toml` entry points（group `iar.agent_output_protocols`）注册；加载失败抛 `OutputProtocolLoadError`，无 plain 回落。
- FR-8 ✓ planner 只读门禁保留（`content_generators.py` 未声明 `read_only` 拒绝启动）。
- FR-10 ✓ `cli_parser.py` choices 与 `container_auth.py::SUPPORTED_AGENT_SPECS` 均由注册表派生。

## Notes 与处置

1. **timeout 丢弃（已修复）**：`content_generators.py` 两处非 plain 协议分支未把 `timeout` 传入 `OutputRelayRequest`，`generate(timeout=...)` 在流式协议路径静默失效（当前内置 generate/repl 全为 plain 未触发，配置指向流式协议即命中）。→ 已补 `timeout=timeout` 透传并重跑全量回归；`transcript_runner.py` 无需同修（`IAgentTranscriptRunner.run` 接口本就不含 timeout，辩论超时由上层处理）。
2. **新增 api→engines import（跟踪项）**：`cli_parser.py:25`、`cli_parsed_commands/agent.py:31` 为本次新增；`check_architecture.py` 对 api→engines 属过渡期放宽、不算违规，但与最终目标方向相悖，建议后续 PRD 跟踪收敛。
3. **FR-1 纯度字面冲突（可接受）**：`agent_invocation.py` 读 `.git` 指针文件属文件 I/O，严格不满足"无 I/O"；docstring 已显式声明为唯一环境读取，且为 FR-6 展开器语义所需。

## 验收清单诚实性

`[x]` 条目抽查的证据文件（golden-argv-*、rv3-*、rv5-*、rv6-rv7-validation.md、red-negative-controls.md）真实存在且与叙述一致；三项 `[~]` 豁免均附替代证据，所引单测真实存在，未见用豁免掩盖代码缺陷。
