# Verification Plan: P1-BUG-20260914-171901-console-e2e-and-archive-view-defects

> 由 executor 在实现期生成；对应 PRD §7 Realistic Validation Plan 的四条 oracle。
> 证据按 `rv-<id>` 命名保存在本目录；原始产物（日志、HTML 报告、截图）留在本地磁盘，不入库。

## rv-1 — 就绪探针默认值可通（不设 env 覆盖）

- **行为**：不设 `PLAYWRIGHT_HEALTH_URL` 时 `just e2e` 的就绪探针在 30s 内通过。
- **真实入口**：worktree 内 `just e2e tests/smoke/pages.spec.ts`（不导出任何 `PLAYWRIGHT_*` 覆盖）。
- **必须穿过**：`just e2e` → `run-with-just-stack.sh` 导出默认 URL → `stack-control.mjs` 轮询 → 真实 HTTP GET `/api/v1/agent-runner/health` → 200 → 测试开始执行。
- **禁止旁路**：命令行传 `PLAYWRIGHT_HEALTH_URL` 让探针通过。
- **负向对照**：把 `run-with-just-stack.sh` 默认值临时改回 `/health`，用 `PLAYWRIGHT_STACK_TIMEOUT_MS=15000` 重跑同命令，应出现 `Timed out waiting for Playwright stack readiness` 且 URL 为 `.../health`；随后恢复。
- **证据**：`rv-1.e2e-run.log`（正向）、`rv-1-negative.e2e-run.log`（负向）。

## rv-2 — 控制台路由全部带 `/app/` 前缀

- **行为**：缺陷 B 的 7 个文件不再命中不存在路由。
- **真实入口**：worktree 内 `just e2e smoke` 与 `just e2e no-auth`（真实栈）。
- **静态断言**：`rg -n "goto\('/(?!(app/|'))" tests/playwright-e2e/tests/ tests/playwright-e2e/page-objects/ --pcre2` 无命中（PRD 原断言 `goto\('/(?!app)` 会误命中合法的根路径 `goto('/')`，本 PR 在证据中按此修正口径并已在 PRD 记录）。
- **负向对照**：修复前任一 spec 打开 `/roadmap` 即页面 404（修复前状态已在 PRD §1 实测记录：`/roadmap` → 404）。
- **证据**：`rv-2.rg-residual.log`、`rv-2.e2e-smoke.log`、`rv-2.e2e-no-auth.log`。

## rv-3 — 归档视图 200 且单条脏数据不打挂端点

- **行为**：`include_archived=true` 返回 200 且含 archived 条目；注入非法 `Gate type` 的临时 PRD 后仍 200，该条进 `skipped`。
- **真实入口**：worktree 内 `PORT=<port> IAR_CONFIG=<临时注册表> uv run python -m backend.main` 起服后，独立 curl 进程请求 `GET /api/v1/agent-runner/roadmap/prds?repo_id=keda-main&include_archived=true`。
- **mock 边界**：后端与文件系统全部真实；解析逻辑不 mock；`IAR_CONFIG` 仅用于把 `keda-main.path` 指向 worktree（注册表本身支持该覆盖，用于测试），提交文件零改动。
- **关键值来源**：archived 条目来自真实 `tasks/archive/` 目录；脏数据 fixture 显式命名 `ZZ-RV-FIXTURE-invalid-gate.md`，用完即删。
- **fresh-state 探针**：每步都用独立 curl 进程重取；删除 fixture 后该条从 `skipped` 消失。
- **负向对照**：`git stash push -- src/backend/core/use_cases/roadmap_prd_scanner.py` 去掉容错后，同一条脏数据使端点回到 500；随后 `git stash pop` 恢复。
- **步骤**：
  1. 起服 → curl（不带 include_archived）→ 200，结构一致（新增 `skipped` 字段为 PRD 授权的结构性新增）。
  2. curl `include_archived=true` → 200，`prds` 含 `status=="archived"`，且 `P1-FEAT-20260626-093939` 的 `gate_type=="hard"`；`skipped==[]`。
  3. 注入 fixture → 重启服 → curl → 200，fixture 不在 `prds` 而在 `skipped`（reason 含 `Gate type`）。
  4. 负向：stash 容错 → 重启 → 同请求 500；恢复。
  5. 删除 fixture → 重启 → `skipped==[]`。
- **证据**：`rv-3.curl-steps.log`、`rv-3-negative.curl-500.log`。

## rv-4 — 共享改动不被 `just sync-template` 回滚

- **行为**：本次改动涉及的共享文件不在覆盖清单中。
- **真实入口**：worktree 内 `./scripts/sync_template.sh --list`。
- **决策二结论（已人工确认：模板登记所有权）**：
  - 实测否决了 PRD 原方案二（`project_skip_paths` 对 upstream-owned 文件无效，默认模式照样列出）。
  - 实测否决了方案一（模板自身的 `/health` 与模板自带后端一致，上游改默认值会破坏模板）。
  - 实施为：在 `scripts/shared/template/sync_template.sh` 的 `_is_upstream_owned` 为 4 个文件（`scripts/shared/e2e/run-with-just-stack.sh`、`tests/playwright-e2e/scripts/stack-control.mjs`、`tests/playwright-e2e/.env.e2e.example`、`tests/playwright-e2e/README.md`）登记项目所有权，沿用 `pytest.ini`/`ruff.toml` 判例；同一改动已提交到模板仓库本地 clone（`~/code/zata_code_template` @ 289ac16），**待人工 push 上游合入**。
  - `--list` 预期残留 2 条：`hooks/shared/check_architecture.py`（本 PR 之前的既有漂移）与 `scripts/shared/template/sync_template.sh`（本次所有权登记本身，待上游合入后消失）。
  - `tests/playwright-e2e/support/env.ts` 与 `tests/playwright-e2e/page-objects/AgentRunnerMonitorPage.ts` 不在同步面内（前者本就不在 `_is_upstream_owned` 清单，后者为本仓独有文件），无需登记。
- **证据**：`rv-4.sync-list.log`。

## 门禁

- `just lint --full`、`just test`、`uv run mkdocs build --strict` 全绿（worktree 内）。
- 临时 fixture 删除后 `rg -l "ZZ-RV-FIXTURE" tasks/` 无命中。
- `git diff --stat frontend-admin/` 为空。
