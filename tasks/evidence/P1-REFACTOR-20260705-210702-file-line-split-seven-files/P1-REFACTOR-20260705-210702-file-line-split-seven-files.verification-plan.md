# Verification Plan: 拆分 7 个超 1000 行后端 Python 文件至 ≤800 行

> 对应 PRD：`tasks/archive/P1-REFACTOR-20260705-210702-file-line-split-seven-files.md`（归档后路径；采集期间在 `tasks/pending/`，2026-09-15 归档）
> 权威 oracle 定义在 PRD §7.6；本文件是执行摘要，记录**实际跑的命令、基线与产物命名**。
> 执行者：executor（与 verifier 分离）。采集时点：2026-09-15。

## 本轮补做范围

本 PRD 的 7 个目标文件分多批交付。此前各批已落地 5 个文件（`cli.py` / `cli_typer.py` /
`agent_runner_validation.py` / `factory.py` / `github_client.py`）；2026-09-15 的
对账发现三项欠账，本轮补齐：

| 文件 | 对账时 | 本轮目标 | 依据 |
|---|---|---|---|
| `core/use_cases/run_agent_once.py` | 889 | ≤ 800 | FR-1 |
| `core/use_cases/agent_runner_orchestrate.py` | 899 | ≤ 800 | FR-1 |
| `infrastructure/config/settings.py` | 1078 | ≤ 1000 并移出白名单 | FR-2 等价约束；`f2125d0` 明确归属本 PRD |

## 工作树绑定

全部证据采集于同一棵最终代码树，指纹见 `rv-0.tree-fingerprint.txt`。

**本文档是第 2 轮版本**（外部复审整改后重采）。第 1 轮的 `526c208b…` 指纹与全部正向证据已作废。

**约束性指纹用代码面口径**：`git diff HEAD -- src tests hooks docs | sha256` → `ae0e2ade…`
（`HEAD=7e71ef48`）。不用全树口径（`git diff HEAD`）——它把 `tasks/` 下的 PRD 与证据文件
也算进去，而这些文件在取得证据后仍会被编辑（补 §9 勾选、§13 Final Reconciliation），
同一份代码会算出不同指纹。首版 rv-0 用的正是全树口径，因此不可复现，由独立 verifier
复核时发现并更正（该整改已记入 verifier-report）。

## Oracle 清单

| id | 真实入口 | 基线 | 产物 |
|---|---|---|---|
| rv-1 | `uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend --allow-list-file hooks/max_file_lines.allowlist.txt` | 不适用（绝对门禁） | `rv-1.max-file-lines.txt`；负控 `rv-1-negative.max-file-lines.txt` |
| rv-2 | 逐文件非空行统计 + `check_max_file_lines.py --max-lines 800 <7 个文件>` | 不适用 | `rv-2.line-counts.txt` |
| rv-3 | 新模块顶层 `def`/`class` 清单 + 被拆文件符号数变化 | 不适用 | `rv-3.module-outline.txt` |
| rv-4 | `iar --help` 与 24 个子命令 `--help`（25 段，432 行）逐字节 diff | `git worktree add --detach /tmp/keda-rv4-baseline HEAD`（= 拆分前） | `rv-4.help-before.txt` / `rv-4.help-after.txt` / `rv-4.help-diff.txt`；负控 `rv-4-negative.help-diff.txt` |
| rv-5 | `uv run pytest -o addopts='' -q tests/`（全量，非 testmon 增量） | 同命令在 `/tmp/keda-rv4-baseline`（拆分前树）上跑一遍 | `rv-5.just-test.log` + `rv-5.just-test-BASELINE.log`；负控 `rv-5-negative.just-test.log` |
| rv-6（第 2 轮新增） | 探针驱动真实入口 `blocked_continue_issue`，在处理器调用点读回被补丁的协作者 | 不适用（红侧 = 临时移除刷新调用） | `rv-6.injection-hub-probe.txt` + `rv-6.injection-hub-probe.py`；红/绿/还原三态同文件 |

## 基线的取法（rv-4）

本轮改动全部停留在工作区、未提交，因此 `HEAD` 就是"拆分前"状态，无需翻历史 commit。
基线用独立 worktree 检出，并用 `PYTHONPATH` 指向基线 `src/` 复用同一 venv：

```bash
git worktree add --detach /tmp/keda-rv4-baseline HEAD
COLUMNS=200 PYTHONPATH=/tmp/keda-rv4-baseline/src uv run --no-sync python /tmp/rv4-capture.py
COLUMNS=200 uv run --no-sync python /tmp/rv4-capture.py
```

`COLUMNS=200` 固定终端宽度，避免 rich/typer 按真实 tty 宽度换行导致伪差异。

> **为什么不拿更早的 commit 当基线**：本 PRD 的 7 个文件跨数月分批拆分，期间
> `iar roadmap advance`（持续调度 PRD）、`iar agent list/doctor`（agent CLI 适配层 PRD）
> 等**其它 PRD** 也改了 CLI 表面。以月前 commit 为基线，diff 非空无法归因到本 PRD。
> 只有 HEAD 基线能隔离"本次拆分"这一变量。

## 新增文件的 lint 覆盖（第 2 轮方法修正）

`pre-commit run --all-files` 的作用域是 `git ls-files`，**未跟踪的新文件不在其中**
（对 5 个新模块命中 0 个）。因此第 1 轮"`just lint --full` 全绿"没覆盖新模块。
第 2 轮起，新增/触及的文件一律显式过一遍：

```bash
uv run --no-sync pre-commit run ruff --files <paths>
uv run --no-sync pre-commit run ruff-format --files <paths>
```

（`ruff` lint 与 `ruff-format` 是两个独立钩子，pin 的 ruff 为 0.7.4。）

## 负控（必须是红→绿闭环）

| rv | 注入 | 期望红 | 期望绿（还原后） |
|---|---|---|---|
| rv-1 | 在 `src/backend/api/` 造一个 1101 非空行的临时 `.py` | `[ERROR] … 超过上限 1000 行`，exit 1 | 删掉临时文件后无输出、exit 0 |
| rv-4 | 把 `cli_typer_runner.py` 中 `run` 命令 help 末尾句号改成感叹号 | 25 段 help 的 diff 非空 | 还原后 diff 为 0 字节 |
| rv-5 | 删掉 `run_issue_with_agent_fallback` 的"合并 attempt 履历"分支 | 3 条用例失败 | 还原后全量绿（并与绿色运行的被测文件字节一致） |
| rv-6 | 临时移除 `blocked_continue_issue` 里的 `refresh_runtime_dependencies()` 调用 | 探针报 False、exit 1 | 还原后探针报 True、exit 0 |

**python 字节码缓存陷阱（本轮踩到并已修正）**：rv-4 的负控把 `.` 改成 `!` —— 两者
都是 1 字节，源文件**大小不变**；若改写与还原落在同一秒，`.pyc` 头部记录的
(mtime, size) 与还原后的源文件相符，Python 会继续使用**含 `!` 的旧字节码**。
表现为：源文件已还原、`git diff` 干净，CLI 却仍打印 `!`。因此负控与正式采集
前后都显式清理 `src/**/__pycache__`，rv-4 的正向证据也因此重采过一次
（首版 `help-after` 被污染，已作废）。

## 非阻塞 / 未采集项

- 真实 `uv run iar daemon` 连 GitHub / LLM 的端到端（需凭据 + 远端副作用）：
  按 PRD §7.6 标 `opt-in / post-merge`；用 `--help` 全量烟测（rv-4）与全量测试集（rv-5）作替代。
- daemon 进程重启以载入新代码：由人工在合入后执行，不在本证据范围。
