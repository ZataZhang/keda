# dsh 内置 agent 接入 · 证据报告

## 人审导航 / Human Review Navigation

- **先看人工审查清单**：[`human-review-checklist.md`](human-review-checklist.md)（交互版：[`human-review-checklist.html`](human-review-checklist.html)；或 `just prd review tasks/pending/P1-FEAT-20261118-113234-dsh-builtin-agent.md`）
- **PRD**：[`tasks/pending/P1-FEAT-20261118-113234-dsh-builtin-agent.md`](../../../tasks/pending/P1-FEAT-20261118-113234-dsh-builtin-agent.md)
- **PR / CI**：无（本地交付，未创建 PR、未跑远端 CI）
- **本轮结论**：实现与执行侧证据已完成，独立 verifier 最终 **PASS**（[`verifier-report.md`](verifier-report.md)）。人审已于 2026-11-18 12:39 确认：决策一 A（环境继承）、决策二 B（乐观档：强制只读 + 声明续传）、呈递面确认。

### 先看这两张图

![全局层 Agent 下拉包含 dsh](rv-4-console-agents.png)

*图 1：真实 console（工作树代码起服）的「模型预设 → Agent」下拉，`dsh` 在列表末尾。*

![保存后生命周期矩阵的生效 AGENT 为 dsh](rv-4-console-saved.png)

*图 2：真实保存后的生命周期矩阵——实现/修复/收尾的生效 AGENT = dsh，toast「生命周期设置已保存」；`config.toml` 写回后已用备份恢复并校验 SHA-256 一致。*

## 环境与版本

- 仓库：`<HOME>/code/keda`，分支 `main`，交付树标识记录于下（含其他会话未提交改动，均不属本次交付）。
- dsh：`0.2.2-rc.1`，可执行文件由 fnm 提供的 PATH 解析（`dsh`）。
- kc：editable 安装，源码指向本工作树；console 以 `uv run kc console --no-browser --port 54667` 起服。
- 模型凭据：本执行环境**没有** `DEEPSEEK_API_KEY`，因此 rv-10 记 skipped（不冒充通过）。

## 交付树身份（本交付文件哈希）

工作区同时有其他会话的未提交改动，因此用**本交付文件**的 SHA-256 标识被验证的代码，而不是整棵混合树：

| 文件 | SHA-256 |
|---|---|
| `src/backend/core/shared/models/agent_spec.py` | `64e42dce…a52221a` |
| `src/backend/core/shared/interfaces/agent_output_protocol.py` | `a9b817b2…e9221` |
| `src/backend/engines/agent_runner/output_protocols/dsh_json_lines.py` | `698d06f2…64b0c3b8` |
| `src/backend/engines/agent_runner/output_protocols/__init__.py` | `68cd93f4…1b86d` |
| `src/backend/infrastructure/agent_stream_usage.py` | `cf1b94c6…707dc0` |
| `config.toml` | `8976862d…f1863a` |
| `pyproject.toml` | `2117bef7…ed681` |
| `tests/test_dsh_output_protocol.py` | `a05bd6ce…f14fd` |

独立 verifier 复核的正是这些文件（最终结论 PASS；`verifier-report.md`）；其他会话改动不属于本交付。

## 证据清单

| rv | 结论 | 证据（同目录） | 入口 |
|---|---|---|---|
| rv-1 | ✅ | `rv-1-config-parity.txt` | `uv run pytest tests/test_agent_spec_config.py`（18 passed） |
| rv-2 | ✅ | `rv-2-invocation-golden.txt` | `uv run pytest tests/test_agent_invocation_golden.py`（45 passed，含续传形态） |
| rv-3 | ✅ | `rv-3-dsh-protocol.txt` + `scripts/probe_dsh_protocol.py` | 真实 dsh 子进程；9 项断言全 PASS（session id 同源、退出码自洽、error 事件渲染、fresh state） |
| rv-4 | ✅ | `rv-4-console-agents.txt`、`rv-4-console-agents.png`、`rv-4-console-draft.png`、`rv-4-console-matrix-bound.png`、`rv-4-console-saved.png`、`rv-4-console-repo-scope.png`、`scripts/capture_console_*.cjs` | 真实 console：两次 PATCH 200；`config.toml` 写回 diff + 恢复 SHA-256；仓库层下拉只读复核 |
| rv-5 | ✅ | `rv-5-doctor.txt` | `kc agent doctor dsh --all-profiles` exit 3 指名 judge；`kc agent doctor dsh` exit 0；真实非法权限档发 `error` 事件 |
| rv-6 | ✅ | `rv-6-usage-parsing.txt` | `uv run pytest tests/test_agent_stream_usage.py`（65 passed） |
| rv-7 | ✅ | `rv-7-protocol-render.txt` | `uv run pytest tests/test_dsh_output_protocol.py`（19 passed） |
| rv-8 | ✅ | `rv-8-enumeration-sync.txt` + `scripts/check_enumeration_sync.py` | 35 条逐处断言全 PASS（文档/原型/随包 skill/同步适配器/配置说明） |
| rv-9 | ✅ | `rv-9-full-test-lint.txt`、`rv-9-mkdocs.txt` | 隔离 worktree（仅本交付 23 文件 + HEAD）`just test all`：5400 passed / 1 skipped + `just lint --full`；`mkdocs build --strict` exit 0 |
| rv-10 | ⏭️ skipped | （无） | 需 `KEDA_DSH_REAL_RUN=1` + 模型凭据；本环境无凭据，补验条件见 PRD §12 |
| rv-11 | ✅ | `rv-11-dsh-resume.txt` | 对既有会话 `--session-id` 续传：sessionId 不变、turn_start.turn=2 |
| rv-12 | ✅ | `rv-12-dsh-readonly.txt` | 真实运行 + 会话日志：permission/preset=read-only、sandbox/mode=read-only、approval=never |

## 可信度与反作弊说明

- **同源对照**：rv-3 先真实跑一次 dsh 并采集 stdout，把**同一份**输出交给观测器（`StreamUsageCollector`）与渲染器（`_render_line`）；随后用真实协议类 `DshJsonLinesOutputProtocol.relay` 另起一个真实 dsh 进程，验证退出码一致与 session id 非空且与首次不同（fresh state）。不是合成事件。
- **真实写回**：rv-4 的保存不是前端模拟——`PATCH /v1/agent-runner/lifecycle-settings` 返回 200，且 `~/.kedacode/config.toml` 出现对应 diff；随后恢复备份并校验 SHA-256。
- **无凭据不冒充**：rv-10 明确 skipped；rv-3 在无凭据下走真实失败路径（`MISSING_CREDENTIAL`，退出码 1），没有用替身宣称成功。
- **已知偏差（不阻断）**：
  - 人类已于 2026-11-18 12:39 聊天确认两项决策（1A / 2B）与呈递面；按 B 改为乐观档，见 rv-11/rv-12。
  - read-only 的「实际写文件被拒」仍缺模型凭据无法真机验证；已有证据是沙箱模式被真实应用（rv-12），补验条件见 PRD §12。
  - `kc agent doctor` 对 dsh 仍打印“writable but declares no sandbox/approval flag”——检测器只认 `--sandbox`/`--ask-for-approval`，不认识 `--permission-mode`（已在文档注明）。
  - 工作区含其他会话的未提交改动（`kedacode-operator/SKILL.md`、`references/run-once.md`、`repository_gitignore.py`、`test_agent_runner_init.py`），不属本次交付。

## 复跑命令

```bash
# 注册表 / argv / 协议 / 观测
uv run pytest -o addopts='' tests/test_agent_spec_config.py tests/test_agent_invocation_golden.py \
  tests/test_dsh_output_protocol.py tests/test_agent_stream_usage.py \
  tests/test_output_protocol_registry.py tests/test_container_auth.py \
  tests/test_agent_config_consistency.py -q

# 真实 dsh 进程 ↔ kc 协议
uv run python tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/probe_dsh_protocol.py

# CLI 自检
uv run kc agent list
uv run kc agent doctor dsh
uv run kc agent doctor dsh --all-profiles   # 期望 exit 3，指名 judge

# console（真实页面，rv-4：只读下拉 + 真实保存并恢复）
uv run kc console --no-browser --port 54667
NODE_PATH=tests/playwright-e2e/node_modules node \
  tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/capture_console_agents.cjs
# 保存流程会写 ~/.kedacode/config.toml，务必先备份：
cp -p ~/.kedacode/config.toml /tmp/kedacode-config.backup.toml
NODE_PATH=tests/playwright-e2e/node_modules node \
  tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/capture_console_save.cjs
cp -p /tmp/kedacode-config.backup.toml ~/.kedacode/config.toml   # 恢复并核对 SHA-256
NODE_PATH=tests/playwright-e2e/node_modules node \
  tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/capture_console_repo_scope.cjs

# 枚举同步（35 条逐处断言）
uv run python tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/check_enumeration_sync.py

# 全量测试与文档
just test
uv run mkdocs build --strict
```
