# Evidence Report: P1-REFACTOR-20260703-184226-api-engines-layer-migration

> 实现已随 `fea15af`（PR #141）合入 main。本报告的证据于 2026-09-16 在 main `d953a49` 上**重新采集**，
> 基线与当前分别取自 `ec621a4`（PR 分支基点）与当前主树；原始产物留本地不入库，判定所需摘录见下。

## 结论速览

| oracle | 判定 | 核心证据 |
|---|---|---|
| rv-1 架构严格态 + api→engines 清零 | PASS | `rg` 0 命中；`check_architecture.py` 扫描 241 文件 0 违规；`just lint --full` 的 Check architecture 步骤 Passed；负向对照报 1 处违规 exit 1 |
| rv-2 core facade 覆盖 | PASS | api 侧 40 处 import 全部落到 `core.use_cases.agent_runner_*`；AST 全量扫描 12 个 importlib 绑定点覆盖 12 个 engines 模块，15/15 能力对账齐全 |
| rv-3 CLI 行为零变化 | PASS | `--help` / `run --help` / `daemon --help` 与基线逐字节相同（79/27/38 行）；verifier 独立扩到全部 24 个子命令 help diff，24/24 空；负向对照 diff 非空 |
| rv-4 路由契约零变化 | PASS | `pytest -k agent_runner` → 906 passed |
| rv-5 daemon 并发调用顺序不变 | PASS | `pytest -k "daemon or loop or concurrent"` → 146 passed |
| 门禁 | PASS | `just lint --full` 全绿；全量 `pytest` 2091 passed |

## rv-1 — 架构检查严格态通过，api → engines 直连清零

**正向**：

```text
$ rg -n "from backend\.engines" src/backend/api/          # 无输出，exit 1

$ uv run --no-sync python hooks/shared/check_architecture.py
架构依赖检查 — 共扫描 241 个文件
✅ 架构依赖方向全部合法，无违规。

$ rg -n -A 6 '^FORBIDDEN_IMPORTS' hooks/shared/check_architecture.py
FORBIDDEN_IMPORTS: dict[str, list[str]] = {
    "infrastructure": ["core", "engines", "api", "composition"],
    "core": ["engines", "infrastructure", "api", "composition"],
    "api": ["infrastructure", "engines", "composition"],
    "engines": ["api", "composition"],
}
# 完整摘录见 rv-1.forbidden-imports.txt；api 的禁止列表含 engines，严格态成立
```

`just lint --full` 的 `Check architecture layer dependencies................................Passed`
（`hodJbw` 运行记录），说明该结论在真实 pre-commit 门禁下同样成立。

**负向对照**（临时在 `src/backend/api/cli.py:295` 追加一行 engines import 后重跑，随后还原）：

```text
❌ 发现 1 处违规：
  [backend/api] → [engines]  src/backend/api/cli.py:295
    from backend.engines.agent_runner.factory import get_agent_runner_settings  # NEGCTRL
exit=1
```

→ 证明"0 违规"不是因为检查器没在跑。

## rv-2 — core facade 覆盖且无冗余

**口径修正（2026-09-16，verifier 复核后）**：初版证据用单行正则只扫到 5 处 `import_module(...)`
绑定，漏掉了跨行书写的调用；现改为对 `src/backend/core/**/*.py` 做 **AST 全量扫描**，
共 **12 个绑定点 / 12 个 engines 模块**，完整清单见 `rv-2.core-facade-coverage.txt`。

api 侧 import 落点（迁移后，全部指向 `core.use_cases.agent_runner_*`）：

```text
20 from backend.core.use_cases.agent_runner_factory            # 原 engines.factory（20 处，含 5 处纯 logger）
 9 from backend.core.use_cases.agent_runner_repository_local   # 原 engines.repository_local（9 处）
 2 from backend.core.use_cases.agent_runner_takeover
 2 from backend.core.use_cases.agent_runner_init_assets        # 原 remote_template_skills / workflow_install
 1 from backend.core.use_cases.agent_runner_worktree_cli
 1 from backend.core.use_cases.agent_runner_output_protocols
 1 from backend.core.use_cases.agent_runner_monitor            # 既有监控用例，与呈现模块无关
 1 from backend.core.use_cases.agent_runner_loop_state         # 原 persistence.loop_state_json
 1 from backend.core.use_cases.agent_runner_failure_resolver
 1 from backend.core.use_cases.agent_runner_container          # 原 container_auth / container_ops
```

15 个 engines 能力的落点对账（对照 PRD §5 能力归类表，**15/15 全覆盖**）：

| 能力（迁移前被 api 直连） | 迁移后落点 |
|---|---|
| factory（20 处，含 5 处纯 logger） | `agent_runner_factory.py:24`（importlib → `engines...factory`） |
| factories（`build_app_config` 等） | 同上；engines 侧 `factory.py:52` 由 `factories` 再导出后经 facade 绑定 |
| repository_local（9 处） | `agent_runner_repository_local.py:13` |
| takeover / takeover_interactive | `agent_runner_takeover.py:14` / `:15` |
| failure_resolver | `agent_runner_failure_resolver.py:13` |
| persistence/loop_state_json | `agent_runner_loop_state.py:13` |
| worktree_cli | `agent_runner_worktree_cli.py:13` |
| workflow_install | `agent_runner_init_assets.py:22` |
| remote_template_skills | `agent_runner_init_assets.py:19` |
| output_protocols | `agent_runner_output_protocols.py:13` |
| container_auth / container_ops | `agent_runner_container.py:245` / `:255` |
| **live_terminal / runner_live_view（+ 隐藏共用依赖 live_panels）** | **迁入 `api/agent_runner_views/`**（`fea15af` 的 R097/R100 rename 可溯） |

> 勘误：初版本节曾把 `agent_runner_monitor` 写成 `live_terminal` / `runner_live_view` 的落点——
> 这是错的。`agent_runner_monitor` 是既有监控用例，呈现模块的真实落点是 `src/backend/api/agent_runner_views/`。

**实现期口径修正（已写回 PRD §13 D-05）**：`FORBIDDEN_IMPORTS["core"]` 同样禁止 core → engines 的静态 import
（`hooks/shared/check_architecture.py:37`），故 facade 沿用 `backend.core.agent.memory._composition` 先例，
用模块级 `importlib.import_module(...)` 绑定 engines 名字。绑定时机仍在模块 import 期，
调用方 `from ... import X` 与 `patch("backend.api.<module>.X")` 语义不变；
`tests/test_agent_runner_container.py` 为该约束的守门测试。

## rv-3 — CLI 行为零变化

基线代码来源（`rv-3.provenance.txt`，排除"两次都跑当前树"）：

```text
--- current ---
/Users/zata/code/keda/src/backend/api/cli_typer_runner.py
--- baseline (PYTHONPATH=/tmp/keda-baseline-api-engines/src) ---
/tmp/keda-baseline-api-engines/src/backend/api/cli_typer_runner.py
```

三条命令 diff 结果（`rv-3.help-*.diff.txt` 均为空）：

| 命令 | 输出行数 | diff |
|---|---|---|
| `iar --help` | 79 | 空 |
| `iar run --help` | 27 | 空 |
| `iar daemon --help` | 38 | 空 |

**负向对照**（把 `cli_typer_runner.py:86` 的 docstring 加 `NEGCTRL` 后重跑，随后还原）：

```text
4c4
<  Run one agent-runner polling cycle.
---
>  Run one agent-runner polling cycle. NEGCTRL
```

→ 证明该 diff 判据能判负，空 diff 不是恒真。

## rv-4 / rv-5 — 路由契约与 daemon 并发

```text
$ uv run pytest tests/ -q -k "agent_runner" -o addopts=""
906 passed, 1185 deselected in 28.46s

$ uv run pytest tests/ -q -k "daemon or loop or concurrent" -o addopts=""
146 passed, 1945 deselected in 3.23s
```

## 门禁

- `just lint --full`：全绿（ruff、ruff-format、Check PRD acceptance checklist、Check guidelines consistency、
  Check architecture layer dependencies、Check max file lines 等），标记写入 `.last_linted_commit`。
- 全量测试：`uv run pytest tests/ -q -o addopts=""` → **2091 passed in 73.87s**（独立 verifier 复跑同数）。
  注：实现期在 worktree 内用 `just test all` 得到 2084 passed，与最终代码树的 2091 差异来自测试集本身
  在两次运行之间新增了用例；PRD §9 以最终代码树的 2091 为准。
  （`just test` 因 testmon 标记命中会直接跳过，故直接跑 pytest 取证。）
- 前端视觉证据：本 PRD 声明 `No frontend impact`，且 `git diff HEAD -- frontend-admin frontend-public` 为空 → 无需截图/录屏。

## 交付后状态

- 违规计数从撰写时的 26、2026-07-31 的 40、2026-09-16 基线重测的 44，归零为 **0**（严格态下）。
- `hooks/shared/check_architecture.py` 已随 `d953a49` 同步为上游版本（api 禁止列表为
  `["infrastructure", "engines", "composition"]`，比本 PRD FR-2 的下限更严），模板同步的 hold back 已解除。

## AI 编码自检清单（docs/ai-standards/code-reuse.md）

- [x] 未复制粘贴已有代码后微调（facade 复用 engines 实现，未重写）
- [x] 复用既有先例（`backend.core.agent.memory._composition` 的 importlib 绑定模式）
- [x] 无新增抽象层（只为层次边界补入口）
- [x] import 方向符合四层架构（静态扫描 + 架构 hook 双重证明）
- [x] `just lint --full` 与全量 pytest 在归档前全绿
- [x] 文档同步（`CLAUDE.md` 依赖规则已改；`docs/architecture/system-design.md` 表述核对一致）
