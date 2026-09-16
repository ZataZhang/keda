# Verification Plan: P1-REFACTOR-20260703-184226-api-engines-layer-migration

> 本 PRD 的实现已随 `fea15af`（PR #141，2026-09-16）合入 main。本计划由执行者在**当前 main（`d953a49`）**
> 上补采集归档所需证据，对应 PRD §7.6 Realistic Validation Plan 的 rv-1 ~ rv-5。
> 证据按 `rv-<id>` 命名保存在本目录；原始产物（日志、diff）留在本地磁盘，不入库（`.gitignore: tasks/evidence/**`）。

## 基线与当前代码树

| 项 | 位置 | 说明 |
|---|---|---|
| 迁移前基线 | `ec621a4`（= `b7e3a67^`，PR #141 的分支基点） | 经 `git worktree add /tmp/keda-baseline-api-engines ec621a4` 取出 |
| 迁移后当前 | main `d953a49` | 含 `fea15af`（迁移）与后续 `5ae82c6`/`7e228da` 等 |
| 基线代码的加载方式 | `PYTHONPATH=/tmp/keda-baseline-api-engines/src uv run --no-sync iar ...` | editable 安装为 `.pth` 追加 `/Users/zata/code/keda/src`，`PYTHONPATH` 先于 site-packages 生效；已用 `rv-3.provenance.txt` 证明两次运行加载的是两份不同代码 |

## rv-1 — 架构检查严格态通过，且 api → engines 直连清零

- **行为**：`rg -n "from backend\.engines" src/backend/api/` 返回 0 行；`check_architecture.py` 以
  `FORBIDDEN_IMPORTS["api"] = ["infrastructure", "engines"]` 通过。
- **真实入口**：`rg -n "from backend\.engines" src/backend/api/`；`uv run --no-sync python hooks/shared/check_architecture.py`；
  `just lint --full`（含 Check architecture layer dependencies 步骤）。
- **必须穿过**：真实扫描 `src/backend/api/` 全部 `.py` → 真实 pre-commit 架构 hook → 退出码。
- **禁止旁路**：不临时放宽 `FORBIDDEN_IMPORTS`、不把违规文件加进白名单。
- **负向对照**：在 `src/backend/api/cli.py` 末尾追加一行
  `from backend.engines.agent_runner.factory import get_agent_runner_settings  # NEGCTRL` 后重跑检查器，
  应报 `[backend/api] → [engines]` 且 exit 1；随后还原。
- **证据**：`rv-1.negative.txt`（负向）；正向结果见证据报告（架构检查输出与 `just lint --full` 摘录）。

## rv-2 — core facade 覆盖全部 engines 能力且无重复职责

- **行为**：原 api 直连的 engines 能力都有 `core/use_cases/` 入口；无一对一冗余透传壳（适配/工厂类本就薄）。
- **真实入口**：`rg -n "from backend\.engines" src/backend/api/`（0 命中）、
  `rg -n -o 'import_module\("backend\.engines[^"]*"\)' src/backend/core/use_cases/`、
  `rg -o "from backend\.core\.use_cases\.agent_runner_[a-z_]*" src/backend/api/`。
- **说明（实现期口径修正）**：`FORBIDDEN_IMPORTS["core"]` 同样禁止 core → engines 的**静态** import
  （`hooks/shared/check_architecture.py:37`），因此 facade 采用 `backend.core.agent.memory._composition`
  既有先例：模块级 `importlib.import_module("backend.engines.agent_runner.*")` 绑定名字。
  故静态扫描口径由"`from backend.engines`"修正为"`import_module("backend.engines...")`"；
  `tests/test_agent_runner_container.py` 为该约束的守门测试。
- **证据**：`rv-2.core-facade-coverage.txt`。

## rv-3 — iar CLI 行为零变化（输出 diff 为空）

- **行为**：`iar --help` / `iar run --help` / `iar daemon --help` 与迁移前逐字节一致，退出码 0。
- **真实入口**：`uv run --no-sync iar --help`（及两个子命令），基线与当前各跑一次后 `diff`。
- **关键值来源**：基线输出取自基线 worktree 真实二进制入口（`iar` console script），不是复制粘贴或重构等价值。
- **必须穿过**：console script → typer app → 命令注册表（含迁移后的 core facade）。
- **禁止旁路**：不比较"只看命令名单"这类等价改写；必须逐字节 diff 完整 help 文本。
- **负向对照**：把 `src/backend/api/cli_typer_runner.py:86` 的 docstring 加 `NEGCTRL` 后重跑
  `iar run --help`，diff 应非空；随后还原。
- **fresh-state 探针**：`rv-3.provenance.txt` 证明基线与当前加载的是两份不同代码路径，排除"两次都跑当前树"的恒真。
- **证据**：`rv-3.help-*.{before,after,diff}.txt`、`rv-3.negative.diff.txt`、`rv-3.provenance.txt`。

## rv-4 — HTTP 路由契约零变化

- **行为**：agent runner 路由测试全绿；路由签名 / 响应 schema 与迁移前一致。
- **真实入口**：`uv run pytest tests/ -q -k "agent_runner" -o addopts=""`。
- **mock 边界**：路由 handler 真实；GitHub/LLM 在测试边界 mock。
- **负向对照**：改任一路由响应字段名即会红（由既有路由断言覆盖，本次不额外破坏代码）。
- **证据**：`rv-4.pytest-agent-runner.log`。

## rv-5 — 迁移不改变 daemon 并发调用顺序

- **行为**：daemon / loop / 并发相关测试全绿。
- **真实入口**：`uv run pytest tests/ -q -k "daemon or loop or concurrent" -o addopts=""`。
- **mock 边界**：GitHub/LLM/process 在边界 mock；worktree/标签流转在测试内按既有夹具执行。
- **证据**：`rv-5.pytest-daemon-loop.log`。

## 门禁

- `just lint --full`：全绿（含 Check architecture layer dependencies、Check PRD acceptance checklist）。
- 全量 pytest：`uv run pytest tests/ -q -o addopts=""`（`just test` 因 testmon 标记命中会跳过，故直接跑 pytest）。
- **前端视觉证据**：本 PRD 声明 `No frontend impact`，且 `git diff HEAD -- frontend-admin frontend-public` 为空
  （`scripts/shared/just/check_prd_evidence.sh` 的 Priority 0 规则），无需截图/录屏。
