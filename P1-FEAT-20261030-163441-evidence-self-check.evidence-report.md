# Evidence Report: Agent 证据自检入口（kc evidence check）

## 人审导航 / Human Review Navigation

本 PRD 的交付物是命令行工具，§9.1 没有 `reviewer: human` 的 oracle；人审只剩 PRD §2 的两个决策。
verifier 分组（rv-1～rv-5、既有测试回归、文档与 skill 同步检查）不在此呈递。

| 人审事项 | 呈递位置 | 打开方式 | 期望值 | 执行侧自检 |
|---|---|---|---|---|
| 决策一：允许 `--timeout` 调小且不得大于配置上限 | PRD §2 决策一；`human-review-checklist.md` 第 1 节 | `open tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/human-review-checklist.md` | 回复「同意」或选择另一方案 | `rv-3-timeout-isolation.txt` 显示 `--timeout 1` 标注 `delivery gate limit is 300s`，`--timeout 301` 退出码 2；负控去掉参数后通过 |
| 决策二：门禁聚合全部未通过项 | PRD §2 决策二；`human-review-checklist.md` 第 2 节 | 同上 | 回复「同意」或保持现状 | `rv-1-self-check-vs-gate.txt` 的门禁消息一次列出全部失败；`test_ensure_validation_commands_pass_aggregates_assertion_and_timeout` 覆盖一条断言不匹配 + 一条超时 |

没有静态图片，因此本报告不含内联图片；原始证据是可复制的终端输出 `.txt`。

## 结论摘要

- `kc evidence check` 已落地，与交付门禁共用 `load_evidence_manifest`、`bash -lc` 复跑和
  `_validate_stdout_assertions`；仓库内断言实现仍只有一处。
- rv-1～rv-5 全部通过真实 CLI 入口取得证据，并通过 `ensure_validation_evidence_ready` 与
  `ensure_validation_commands_pass` 的再次复跑。
- 门禁失败信息改为聚合全部未通过项；通过判据未变。
- 旧 manifest（无 `stdout_assertions`）行为不变：只校验退出码。
- 未覆盖：真实外部服务差异；自检通过不等于交付通过。

## 环境与入口

- 仓库/worktree：`ZataZhang/keda` / `.kc-worktrees/issue-341`。
- CLI：`uv run kc evidence check --evidence <dir> [--json] [--timeout <s>]`。
- 门禁：`ensure_validation_evidence_ready` + `ensure_validation_commands_pass`（真实 `SubprocessRunner`）。
- 证据目录：`tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/`。
- 所有 RV 脚本位于同目录 `scripts/`，未进入 `git status`（`.gitignore` 白名单只放行 `.md`）。

## 逐项证据

### rv-1 自检与门禁判定同源

- 命令：`uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-1-capture.py`
- 证据：`rv-1-self-check-vs-gate.txt`
- 关键输出：
  - 自检：`self_check_item=1`、`self_check_pattern=LEGACY GREEN: v1 example-derived suite`、
    `self_check_closest_line='LEGACY GREEN: v1 suite'`。
  - 新进程门禁：`{"gate_failed": true, "gate_item": 1, "gate_pattern": "LEGACY GREEN: v1 example-derived suite"}`。
  - 一致性：`consistent_verdict=item-number-and-pattern-identical`。
  - 负控：`{"negative_control_passed": true, "negative_control_status": "passed"}`，
    `negative_control_verdict=exit-0-command-passes-without-assertion-judgment`。
- 说明：fixture 复刻 Issue #334 的「断言多一个词」形态；自检与门禁分别在新旧进程中加载同一
  manifest，报告相同 item 序号与 pattern，证明判定同源。负控只关闭断言判定，同一 exit 0 命令
  立即变绿，证明断言本身有判别力。

### rv-2 一次列全断言错误

- 命令：`uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-2-capture.py`
- 证据：`rv-2-collect-all-assertions.txt`
- 关键输出：`failed_item_count=3`、三个 item 各自 `pattern:EXPECTED-*` 与 `closest:'ACTUAL-* value'`、
  `collect_all_verdict=three-failures-in-one-run`；改回正确断言后 `green_failed_item_count=0`、
  `green_exit=0`、`{'total': 3, 'passed': 3, 'failed': 0, 'timed_out': 0, 'skipped': 0}`。
- 说明：三个错误断言在一次运行中全部返回，未短路；红→绿两侧同时留痕。

### rv-3 单题超时不中断其余题目

- 命令：`uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-3-capture.py`
- 证据：`rv-3-timeout-isolation.txt`
- 关键输出：`TIMEOUT item 1`、`PASSED item 2`、`delivery gate limit is 300s`、
  `Result: 1/2 passed, 1 timed out`、`local_timeout_exit=1`；`--timeout 301` 输出
  `usage_error: --timeout must be between 1 and 300 seconds`、`above_limit_exit=2`；
  负控 `default_timeout_exit=0`、`default_timeout_summary={'total': 2, 'passed': 2, ...}`。
- 说明：慢题超时只影响自身，另一题继续复跑并给出结论；去掉本地超时后同一 fixture 通过，
  证明超时来自 `--timeout` 而非证据或实现。

### rv-4 manifest 格式错误不执行命令

- 命令：`uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-4-capture.py`
- 证据：`rv-4-format-error-no-execution.txt`
- 关键输出：`Item 1: missing or empty required field risks`、
  `no evidence command was executed`、`broken_exit=1`、`probe_executed=False`；
  负控补齐 `risks` 后 `fixed_exit=0`、`probe_executed_after_fix=True`。
- 说明：探针命令会创建文件，格式错误时该文件不存在，客观证明没有执行任何证据命令；
  修复字段后正常进入逐项判定。

### rv-5 真实证据包通过自检且 JSON 可解析

- 命令：`uv run python tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/scripts/rv-5-capture.py`
- 证据：`rv-5-real-package-json.txt`
- 关键输出：`real_exit=0`、`json_parse=ok`、`real_passed=True`、
  `real_summary={'total': 5, 'passed': 5, 'failed': 0, 'timed_out': 0, 'skipped': 0}`；
  副本改错一条断言后 `corrupted_exit=1`、`corrupted_failed_item_numbers=[5]`、
  `corrupted_assertion_pattern=RV-5 IMPOSSIBLE PATTERN`；phase 3 在唯一候选的临时 worktree 中
  不传 `--evidence`，解析到 `tasks/evidence/only-task` 并通过。
- 说明：真实入口对真实证据目录运行，JSON 由 `cli_output` 唯一出口序列化并被 `json.loads`
  解析；负控证明退出码与 JSON 状态会随断言错误改变。item 5 的 manifest command 是限界探针
  `scripts/rv-5-command.py`（避免递归），同时覆盖 `--json` 解析、失败转红与旧 manifest
  `legacy_manifest_status=passed` / `legacy_manifest_assertion_failures=0`。

## 负控与判别力

| 项 | 负控 | 预期失败形态 | 实际 |
|---|---|---|---|
| rv-1 | 关闭编排里的断言判定 | 同一 exit-0 fixture 仍为 failed | 变绿（`negative_control_status=passed`），证明断言是判别点 |
| rv-2 | 改回正确断言 | 仍有失败 | `green_failed_item_count=0` |
| rv-3 | 去掉 `--timeout` | 仍报超时 | `default_timeout_exit=0`，证明超时来自本地参数 |
| rv-4 | 补齐 `risks` | 仍停在格式错误 | `fixed_exit=0` 且探针被执行 |
| rv-5 | 副本改错一条断言 | 仍返回 0 | `corrupted_exit=1` 且 item 5 failed |

## 门禁复跑与回归

- `ensure_validation_evidence_ready`（真实证据目录）：通过（manifest 覆盖 5 项、必填字段、
  证据文件存在性与 `rv-<n>-*` 命名、negative_control）。
- `ensure_validation_commands_pass`（真实证据目录）：通过，5 个 item 实际复跑耗时
  0.7s / 0.8s / 7.2s / 0.8s / 2.1s。
- `just test`：通过（50 passed, 125 deselected；local 档增量选择）。
- `just test all`：见下方「最终全量结果」。
- `just lint --full`：通过；`just lint --reuse`：jscpd / pylint duplicate-code / 架构 / 规范一致性
  / 文件行数全部通过。
- `uv run mkdocs build --strict`：通过。
- `uv run python hooks/shared/check_architecture.py`：通过（扫描 477 个文件，无违规）。

## 架构与文档同步

- 新增 `src/backend/api/cli_typer_evidence.py`（入口、参数、渲染、退出码）、
  `src/backend/core/use_cases/agent_runner_evidence_check.py`（编排、结果模型、聚合渲染）与
  `src/backend/core/use_cases/agent_runner_evidence_assertions.py`（唯一断言实现）。
- 断言实现仍只有 `src/backend/core/use_cases/agent_runner_evidence_assertions.py` 一处：
  `rg -n "_validate_stdout_assertions\|_is_stdout_assertion_satisfied" src/backend` 仅命中
  2 个定义与 2 个调用点（门禁编排与本模块的断言收集）。
- `kc schema --json` 含 `evidence check`，参数为 `--evidence` / `--worktree` / `--timeout` /
  `--output` / `--json`。
- 文档：`docs/guides/agent-runner.md` 增加自检命令、collect-all、超时与「自检通过不等于交付通过」；
  `docs/guides/prd-standard.md` 指向自检；随包 skill `SKILL.md` 与 `references/run-once.md` 同步。

## 限制与未覆盖

- fixture 命令为 `printf` / `sleep` / `touch`，不依赖网络或外部服务；本证据不证明真实外部服务
  环境下的超时与断言行为。
- 自检只读，不会修改证据、代码或断言；环境差异（网络、端口占用、外部服务）仍可能让门禁失败，
  因此「自检通过」不等于「交付通过」。
- 本仓库 `tasks/evidence/` 下已有多个历史 `evidence.json`（其中部分为早于 `.gitignore` 白名单的
  跟踪文件）。按 PRD 的「找到多个就报错列候选、不猜」约定，此时缺省解析会退出 2 并列出候选；
  执行 prompt 与本文示例都显式传 `--evidence <证据目录>`。新 worktree 只有一个 manifest 时，
  缺省 `kc evidence check` 直接通过（`rv-5-real-package-json.txt` phase 3：
  `default_evidence_dir=tasks/evidence/only-task`；CLI 单测 `test_check_resolves_unique_task_directory_by_default` 同口径）。
- 本地 `just test` 使用 pytest-testmon 增量选择，证据以通过数非零与定向 `--no-testmon` 套件
  （`tests/test_agent_runner_validation.py`、`tests/test_evidence_check_cli.py`、
  `tests/test_cli_schema.py`、`tests/test_kedacode_operator_skill.py`）共同支撑；最终全量见下。

## 可复制用法

```bash
# 在 worktree 内，提交前自检（缺省寻找 tasks/evidence/ 下唯一含 evidence.json 的目录）
kc evidence check

# 显式指定证据目录并输出机器可读结果
kc evidence check --evidence tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check --json

# 本地快速迭代（不得大于门禁上限）
kc evidence check --timeout 30
```

## 最终全量结果

- `just test all`：**5204 passed, 6 skipped in 370.00s**（exit 0；最终代码树）。
- 定向套件：`tests/test_agent_runner_validation.py` + `tests/test_evidence_check_cli.py` +
  `tests/test_cli_schema.py` + `tests/test_kedacode_operator_skill.py` **188 passed**。
- `just lint --full`：通过（含 ruff / ruff-format / 架构 / 文件行数 / 守卫检查）。
- `just lint --reuse`：jscpd 与 pylint duplicate-code 均通过。
- `uv run mkdocs build --strict`：通过。
- 门禁复跑（`ensure_validation_evidence_ready` + `ensure_validation_commands_pass`）：
  5/5 通过，退出码 0。
