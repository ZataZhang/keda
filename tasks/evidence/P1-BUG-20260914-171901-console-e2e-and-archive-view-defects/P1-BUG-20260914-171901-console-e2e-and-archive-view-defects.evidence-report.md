# Evidence Report: P1-BUG-20260914-171901-console-e2e-and-archive-view-defects

> Executor 于 2026-09-14 在 worktree `/Users/zata/code/keda-worktrees/fix/console-e2e-archive-view`（分支 `fix/console-e2e-archive-view`）采集。
> 所有证据均在最终代码树上重新收集；原始产物（完整日志、JSON、截图、视频）留本地，本文只保留判定所需摘录。

## 结论速览

| oracle | 判定 | 核心证据 |
|---|---|---|
| rv-1 就绪探针默认值 | PASS | `rv-1.e2e-run.log`：探针 30s 内 200，`2 passed (6.3s)`；负向对照复现 240s 型超时 |
| rv-2 控制台路由 /app/ 前缀 | PASS | `rv-2.rg-residual.log` 无命中；修复树 smoke/no-auth 无任何页面 404；基线（原始树）连 global-setup 都过不去 |
| rv-3 归档视图 + 容错 | PASS | `rv-3.curl-steps.log`：200/110 archived/skipped 留痕/负向 500/fixture 清理 |
| rv-4 模板同步边界 | PASS（按决策二实施） | `rv-4.sync-list.log`：4 个文件已移出覆盖清单 |
| 门禁 lint/test/mkdocs | PASS | `just test`、`just lint --full`、`just lint --reuse`、`mkdocs build --strict` 全绿（详见门禁小节） |

## rv-1 — 就绪探针默认值可通

**正向**（`rv-1.e2e-run.log`，命令 `just e2e tests/smoke/pages.spec.ts`，未设任何 `PLAYWRIGHT_*` 覆盖）：

```text
Services ready on ports 8233/5416/3757.
INFO: ... "GET /api/v1/agent-runner/health HTTP/1.1" 200 OK
  2 passed (6.3s)          # setup + chromium dashboard 测试
```

后端访问日志直接证明探针打到的是新默认路径并得到 200。整轮 36s（含栈启动）。

**负向对照**（`rv-1-negative.e2e-run.log`，把默认值临时改回 `/health`，`PLAYWRIGHT_STACK_TIMEOUT_MS=15000` 缩短等待）：

```text
INFO: ... "GET /health HTTP/1.1" 404 Not Found   （连续多次）
Error: Timed out waiting for Playwright stack readiness: http://127.0.0.1:8233/health, ...
```

默认值是决定性因素：改回 `/health` 即复现 PRD 描述的超时。随后已恢复修复，`git diff` 仅含 1 行修复。

## rv-2 — 控制台路由全部带 `/app/` 前缀

**静态断言**（`rv-2.rg-residual.log`）：PRD 原口径 `rg "goto\('/(?!app)"` 会误命中 FR-2 明确豁免的根路径 `goto('/')`，故采用精确口径 `goto\('/(?!(app/|'))`，在 `tests/playwright-e2e/tests/` 与 `page-objects/` 上无命中。

**真实栈运行**（修复树）：

- `rv-2.e2e-smoke.log`：`/app/dashboard/` 200、`/app/roadmap/` 200、`/app/ideas/` 200；`pages.spec`、`idea-inbox`、`roadmap-prd-content` 全过。
- `rv-2.e2e-roadmap-realistic.log`：`4 passed (44.7s)`。
- `rv-2.e2e-no-auth.log`：`console-served-static`（直接刷新 /app/roadmap 不 404）等通过。
- 首轮 smoke 中 monitor / roadmap-realistic 的失败见下文"实现期发现"，根因为清单外既有缺陷（api-client 认证路径），修复后 roadmap-realistic 全绿；monitor 剩余失败与本 PR 三项缺陷无关（见下）。

**基线对照**（`rv-2-baseline.e2e-console-pages.log`，stash 全部改动后在原始树上跑同一 spec）：

```text
Error: Timed out waiting for Playwright stack readiness: ...   （global-setup 阶段）
```

原始树连就绪探针都过不去（240s 型超时），套件一个测试都执行不到——证实 e2e 全套在本 PR 之前处于不可运行状态，修复后的剩余失败不是本 PR 引入的回归。

## rv-3 — 归档视图 200 且单条脏数据不打挂端点

详见 `rv-3.curl-steps.log`。要点：

1. 不带 `include_archived`：200，8 条 pending，`skipped=[]`，响应结构仅新增授权的 `skipped` 字段。
2. `include_archived=true`：200，118 条（archived=110），`skipped=[]`；`P1-FEAT-20260626-093939` 回到 prds（修复后 Gate type 合法）。
3. 注入 `ZZ-RV-FIXTURE-invalid-gate.md`：200，该条不在 prds 而在 skipped，reason 为 `Invalid 'Gate type' ... Expected one of: none, soft, hard.`，后端日志同记录 WARNING。
4. **负向对照**：`git stash push -- roadmap_prd_scanner.py` 去掉容错 → 同请求 **500** → 恢复后 200。
5. 删除 fixture → `skipped=[]`。fixture 文件已删；`rg -l "ZZ-RV-FIXTURE" tasks/` 剩余命中仅为 PRD 与本证据报告中对该名称的文字记录（PRD §7 自己规定了该命名），非数据残留。

单元层：`tests/test_roadmap_prd_scanner.py` 新增 `test_invalid_gate_type_prd_is_skipped_not_fatal`，`uv run pytest tests/test_roadmap_api.py tests/test_roadmap_prd_scanner.py --no-testmon` → 13 passed。

## rv-4 — 模板同步边界（决策二：登记所有权）

详见 `rv-4.sync-list.log`。用户确认的过程与依据：

- 实测否决 PRD 原方案二：`SYNC_TEMPLATE_PROJECT_SKIP_PATHS` 覆盖实验显示 `project_skip_paths` 对 upstream-owned 文件无效（默认模式照样列出 4 个文件）。
- 实测否决方案一：模板自带后端有 `/health`，模板默认值对其自身是对的；把 keda 路径写进模板会破坏模板与其它派生项目。
- 实施：`_is_upstream_owned` 为 4 个文件登记项目所有权（同 `pytest.ini`/`ruff.toml` 判例），守卫测试 `tests/guards/shared/test_sync_template.py` 19 项全过（守卫用通用 fixture，不锁这 4 个具体路径）。
- 结果：`--list` 从 5 条降到 2 条（既有漂移 check_architecture.py + 所有权登记脚本本身）；同一改动已提交模板仓库本地 clone `~/code/zata_code_template`（commit `289ac16`，模板 `just test` 199 项通过），**待人工 push 上游合入后彻底闭环**。
- `support/env.ts`、`page-objects/AgentRunnerMonitorPage.ts` 不在同步面内（前者不在 `_is_upstream_owned` 清单，后者为本仓独有文件），无需登记。

## 实现期发现（清单外，已记录于 PRD §7 Change Impact Tree）

1. **api-client 认证路径 404**（已修复）：`support/api-client.ts` login 走 `/auth/login`，后端真实路径为 `/api/auth/login`（router 挂载在 `/api` 前缀）。该文件自带 TODO "Adapt login() to match your application's auth endpoint"。此缺陷使依赖 `api` fixture 的 roadmap-realistic / agent-runner-monitor 全组失败，此前被缺陷 B 的 404 掩盖。一行修复后 roadmap-realistic 4/4 通过。
2. **agent-runner-monitor 标题断言失效**（未修，超出本 PR 范围）：spec 断言的 `Agent Runner Monitor` 标题在 `frontend-public` 中不存在（rg 无命中），对应旧版 UI 文案。该 spec 不在缺陷 B 清单内；其 goto 路径已按清单修复（/app/dashboard 200），但断言目标 UI 已不存在，需要独立 PRD 处理。
3. **环境性噪声**：本机 `gh` 对部分仓库报 GraphQL EOF / repository disabled，后端监控循环被拖慢，前端代理出现 socket hang up，导致 console-pages 的 dashboard mock 测试与 no-auth-example 文件上传测试超时。与三项缺陷及本次改动无关（基线树上这些测试根本无法运行）。

## 门禁

- `just test`：全绿并刷新 test 标记（pytest-testmon 增量；roadmap 相关 13 项用 `--no-testmon` 强制全跑验证）。
- `just lint --full`：全绿（ruff、ruff-format、PRD 验收清单、guidelines、架构分层、max-file-lines 等）。
- `just lint --reuse`：全绿（jscpd / pylint duplicate-code 无改动触碰的重复）。
- `uv run mkdocs build --strict`：通过（2 条既有 INFO 锚点提示，非本次引入）。
- `git diff --stat frontend-admin/`：空。

## AI 编码自检清单（docs/ai-standards/code-reuse.md）

- [x] 未复制粘贴已有代码后微调（扫描器容错复用现有 `parse_delivery_dependencies`，仅加边界防护）
- [x] 复用的是业务规则函数而非数据加载操作
- [x] 函数参数未增加；新返回结构收敛为 `RoadmapScanResult` 值对象
- [x] 无文件超限（roadmap_prd_scanner.py 改后 ~330 非空行）
- [x] import 方向符合四层架构（容错在 core 用例内；api 层仅透传 skipped 字段，无新增 try/except）
- [x] 变量名有来源/状态语义（`skipped_prds`、`scan_result`、`delivery_decl`）
- [x] `just lint --reuse` 与 `just lint --full` 在测试之前全绿
- [x] 文档同步（e2e README 适配清单 + 控制台路由说明；`docs/guides/agent-runner.md` 无需改动，本就使用全路径；mkdocs 无导航变化）
- [x] 未创建只被引用一次的小 helper
