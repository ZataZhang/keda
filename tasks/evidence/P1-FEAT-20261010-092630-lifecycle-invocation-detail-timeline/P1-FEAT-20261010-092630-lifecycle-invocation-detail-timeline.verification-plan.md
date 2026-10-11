# 验证计划：生命周期时间线接入调用明细与展示去噪

> 本文件是 `tasks/archive/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md` §7.6
> Realistic Validation Plan 的执行副本。**判据以 PRD §7.6 为唯一事实源**；本文件只记录
> 「怎么跑、跑在哪棵树上、结果落在哪个文件」。

## 复现环境

| 项 | 值 |
|---|---|
| worktree | 仓库内 worktree 目录 `.iar-worktrees/issue-273`（分支 `issue-273`；本文件所有命令都相对此 worktree 根执行） |
| base commit | `44a33d6b`（= 本轮开工时的 `main`）；WIP 快照 `71f697ae` 已包含上一轮实现 |
| 本轮代码路径改动 | `docs/guides/agent-runner.md`、`tests/test_cli_schema.py`、`tests/test_kedacode_operator_skill.py`、`tests/playwright-e2e/tests/smoke/backlog-prd-lifecycle-invocation.spec.ts`（相对快照的全部代码路径改动） |
| 证据绑定 | `rv-1-final-tree-binding.txt`（`code_paths_diff_sha256` / `full_diff_sha256` / 逐文件 sha256）；harness 自述见 `rv-1-harness-provenance.txt`；结构化 manifest 为 `evidence.json`（version 1 / zh-CN，按 rv-1..rv-4 分组，含可复现命令、负控与 stdout 断言；本目录被 gitignore，只留本机） |
| Python | 工作树 `.venv`（uv 管理，CPython 3.13） |
| 前端 | `frontend-public`（pnpm 11 / Next 16，静态导出到 `src/backend/api/static/console/`，构建产物 gitignore） |
| 浏览器 | 真实 Chromium（Playwright，`PLAYWRIGHT_BROWSERS_PATH=~/Library/Caches/ms-playwright`） |
| e2e 凭据 | 本机单操作者认证：`PLAYWRIGHT_IDENTIFIER` / `PLAYWRIGHT_PASSWORD` 任意非空值即可（`/api/auth/login` 恒返回本机 operator 会话） |

## Oracle 与执行入口

| id | 判据（摘要） | 实际执行（真实入口 / mock 边界） | 证据文件（本目录，不进提交） |
|---|---|---|---|
| rv-1 | 真实 Console + 真实 SQLite：会话分组、尝试展开、页面调用行与账本逐条一致 | `SKIP_CONSOLE_SYNC=1 bash scripts/capture_real_console.sh`：隔离 HOME + `IAR_CONFIG` → 真实 uvicorn（真实 FastAPI + 真实静态 console + 真实 SQLite）→ 真实 Chromium 三档视口（1440 / 420 / 1100）→ 与独立 fresh read 的账本逐条比对。外部执行器由确定性计划 fixture 代替（PRD 允许的边界），不跑完整 runner 调度（避免建分支/推 PR 这类共享状态变更） | `rv-1-page-vs-ledger.txt`、`rv-1-lifecycle-invocation.png`、`rv-1-lifecycle-invocation-narrow-window.png`、`rv-1-page-rows*.json`、`rv-1-ledger-rows.json`、`rv-1-lifecycle-detail.json`、`rv-1-harness-provenance.txt`、`rv-1-harness-run.txt` |
| rv-2 | 端点新增字段与账本逐条一致（会话标识、尝试编号、结果、耗时、模型三态、重试链）；旧事件无标识不虚构分组 | `uv run pytest -q tests/test_prd_lifecycle.py tests/test_prd_invocation_detail.py -k 'invocation or degrade'`（真实临时 SQLite + 真实 HTTP 客户端） | `rv-2-pytest.txt`；红跑对照 `rv-2-red-before-implementation.txt`、`rv-2-red-injection-before-implementation.txt`；反例 `rv-2-negative-control.txt` |
| rv-3 | 三类降级（不可用/无归属/截断）显式披露且不阻断页面 | `uv run pytest -q tests/test_prd_invocation_detail.py -k 'unavailable or truncated'` + 补跑 `-k 'truncates'`（PRD 词干匹配不到 truncates 用例，见负控脚本注释）+ `just e2e tests/smoke/backlog-prd-lifecycle-invocation.spec.ts --grep rv-3` + 旧 spec（读端点 fixture 顶替，页面与浏览器真实） | `rv-3-pytest.txt`、`rv-3-e2e.txt`、`rv-3-legacy-spec.txt`、`rv-3-*.png`、`rv-3-legacy-detail.json`；反例 `rv-3-negative-control.txt` |
| rv-4 | 展示去噪 DOM 断言；事件抽屉与原始 detail 未变 | `just e2e tests/smoke/backlog-prd-lifecycle-invocation.spec.ts`（8 用例） | `rv-4-e2e.txt`、`rv-4-timeline-sessions-folded.png`、`rv-4-invocation-rows-expanded.png`、`rv-4-lifecycle-detail.json`；反例 `rv-4-negative-control.txt` |
| 原型 | Hub → 原型 → 五套场景 → 展开交互 → 返回 Hub（桌面 + 窄屏，零 pageerror） | `python3 -m http.server 8001 --directory docs` + `node scripts/capture_prototype.cjs --base-url http://127.0.0.1:8001`（与 mkdocs 站点同 URL 路径；真实 Chromium） | `prototype-roundtrip.json`、`prototype-roundtrip-desktop.png`、`prototype-roundtrip-narrow.png`；反例 `prototype-roundtrip-negative-control.txt` |
| 人审清单 | 交互 HTML：零 pageerror、首屏卡片可见、四张内嵌图加载、能翻页生成 Markdown | `node scripts/validate_human_review_checklist.cjs --file <html>`（真实 Chromium 打开 file://） | `human-review-checklist-validation.txt` |

定向命令统一注意：仓库默认 `addopts = --testmon` 会按改动增量选择，取证时用 `-p no:randomly` 并显式传目标文件；e2e 走 `just e2e`（自动启停 just-run stack）。

## 反例（negative control，两层，都不改生产代码的前置状态）

1. **rv-1**：把独立 fresh read 导出的第 2 条 `invocation_finished` 终态从 `failed` 改成 `ok`，重跑同一比较脚本 → 必须红（`rv-1-negative-control.txt`，退出码 1）。
2. **rv-2 / rv-3**：对 `prd_invocation_detail.py` 注入单点故障（丢 `retry_of` / 截断判定写死 `False`）→ 对应用例必须红（`rv-2-negative-control.txt`、`rv-3-negative-control.txt`）。脚本用备份还原，不用 git 写操作。
3. **rv-4**：把前端 `SAME_SECOND_MERGE_GROUPS` 置空 → e2e 必须红（`rv-4-negative-control.txt`，2 failed）。
4. **原型**：分别置空折叠组与观测事件类型集合 → 往返脚本必须红（`prototype-roundtrip-negative-control.txt`，2 / 4 项失败）。
5. **实现前红跑**：`rv-2-red-before-implementation.txt` 记录改动前同批用例的红跑输出。

## fresh-state 探针

- rv-1：harness 在写入后**新起进程**重新打开同一 SQLite 文件导出原始行（`export_real_ledgers.py`），再经真实 HTTP 端点读同一数据，最后与浏览器 DOM 比对；任何不一致都让脚本非 0 退出。
- rv-2：pytest 用例先写库、再新起 HTTP 客户端读端点，与 DB 原始行比对（不用组装函数冒充实战契约）。
- rv-1 采集完成后没有触碰任何代码路径：`rv-1-final-tree-binding.txt` 的 `code_paths_diff_sha256` 与 harness 记录运行同刻的整树 diff 哈希一致（`full_diff_sha256`），此后到交付只写交付记录（PRD 与证据 `.md`）。

## 门禁清单（本地复跑顺序）

```bash
just test all                     # 全量 pytest（CI 口径）
just lint --reuse                 # 重复/复用/架构/行数/规范一致性
just lint --full                  # pre-commit 全量（含 check-test-flag）
pnpm --dir frontend-public typecheck && just console-sync   # 前端类型与构建 + 静态同步
uv run mkdocs build --strict
PLAYWRIGHT_IDENTIFIER=local-operator PLAYWRIGHT_PASSWORD=local-operator \
  just e2e tests/smoke/backlog-prd-lifecycle-invocation.spec.ts
PLAYWRIGHT_IDENTIFIER=local-operator PLAYWRIGHT_PASSWORD=local-operator \
  just e2e tests/smoke/backlog-prd-lifecycle.spec.ts
```
