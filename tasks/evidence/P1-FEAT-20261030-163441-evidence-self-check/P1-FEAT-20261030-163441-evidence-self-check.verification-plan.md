# Verification Plan: Agent 证据自检入口（kc evidence check）

本计划对应 PRD `tasks/pending/P1-FEAT-20261030-163441-evidence-self-check.md` 的 §7.6 Realistic Validation Plan。
所有 oracle 都通过真实 CLI 入口 `kc evidence check` 执行；fixture 命令经真实 `bash -lc` 复跑，
manifest 解析、超时和断言判定复用交付门禁的同一批函数。证据文件均为捕获的终端输出（`.txt`）。

## 环境与入口

- 仓库：`ZataZhang/keda`，worktree `.kc-worktrees/issue-341`。
- CLI 入口：`uv run kc evidence check --evidence <dir> [--json] [--timeout <s>]`。
- 门禁入口：`ensure_validation_evidence_ready` + `ensure_validation_commands_pass`（core 用例，见证据报告）。
- 证据目录：`tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/`。
- 所有 RV 脚本位于 `tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/`，不进入代码 diff。

## Oracle 对照

| ID | 命令 | 证据文件 | 预期 | 负控 |
|---|---|---|---|---|
| rv-1 | `uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-1-capture.py` | `rv-1-self-check-vs-gate.txt` | 自检与新进程门禁对同一 fixture 报出相同 item 与 pattern；负控关闭断言判定后同一 fixture 变为 passed | 只把编排调用的断言判定替换为空操作，同一 fixture 应转绿 |
| rv-2 | `uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-2-capture.py` | `rv-2-collect-all-assertions.txt` | 三个题目各写错一条断言时一次列出三条，退出码 1；改正后 0 条失败、退出码 0 | 改回正确断言后应 0 失败 |
| rv-3 | `uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-3-capture.py` | `rv-3-timeout-isolation.txt` | `--timeout 1` 时慢题标为 TIMEOUT 并写明门禁上限 300s，另一题仍给出 PASSED；`--timeout 301` 退出码 2 | 去掉 `--timeout` 后同一 fixture 应通过 |
| rv-4 | `uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-4-capture.py` | `rv-4-format-error-no-execution.txt` | 缺 `risks` 时逐条报格式错误、不执行命令、探针无痕迹；补齐后进入正常流程 | 补齐 `risks` 后探针应被执行 |
| rv-5 | `uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-5-capture.py` | `rv-5-real-package-json.txt` | 真实证据目录 `--json` 退出码 0、JSON 可解析、5/5 passed；副本改错一条断言后退出码 1 且 item 5 failed | 改错一条断言后应转红 |

`evidence.json` 中 item 5 的 `command` 是限界探针 `scripts/rv-5-command.py`（避免真实目录自检递归），
同时覆盖 `--json` 解析、断言失败转红与旧 manifest（无 `stdout_assertions`）只校验退出码。

## 门禁与回归检查

1. `ensure_validation_evidence_ready`：真实证据目录的 manifest 覆盖 5 个 checklist item、必填字段、
   证据文件存在性与 `rv-<n>-*` 命名、`negative_control` 齐全。
2. `ensure_validation_commands_pass`：真实复跑 5 个 item 的 manifest command，全部通过。
3. `tests/test_evidence_check_cli.py`：CLI 参数、退出码、`--json`、collect-all、超时、格式错误、旧 manifest。
4. `tests/test_agent_runner_validation.py`：门禁聚合全部失败、超时不中断、断言聚合、缓存语义。
5. `tests/test_cli_schema.py`、`tests/test_kedacode_operator_skill.py`：命令树自省与随包 skill 同步。
6. `just test`、`just test all`、`just lint --full`、`uv run mkdocs build --strict`、
   `uv run python hooks/shared/check_architecture.py`。

## 失败排查顺序

1. 证据目录解析到的具体路径（`Evidence directory:` 行）。
2. manifest 是否被解析成预期的 5 个 item。
3. 单条命令的退出码与耗时。
4. 断言期望串与实际输出最接近行（`closest ... line`）。
