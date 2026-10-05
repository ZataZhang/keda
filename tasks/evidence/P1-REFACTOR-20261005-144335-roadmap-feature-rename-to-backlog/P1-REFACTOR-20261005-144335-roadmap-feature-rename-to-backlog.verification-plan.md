# 验证计划：Roadmap 功能正名为 Backlog——端到端硬改名与控制台 SQLite v7 迁移

> 本文件是 `tasks/pending/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md`
> §7「Realistic Validation Plan」的执行副本。**判据以 PRD §7 为唯一事实源**，本文件只记录
> "怎么跑、跑在哪棵树上、结果落在哪个文件"。
>
> 同目录的 `….evidence-report.md` 是人读入口（记录原文与负控）。

## 复现环境

| 项 | 值 |
|---|---|
| worktree | `/Users/zata/code/keda/.iar-worktrees/issue-196` |
| 分支 | `issue-196` |
| base commit | `2a1dd6aff786f191e53a81d99c02f4db750f4584`（改名前的树，同时是负控 rv-4「改名前零命中扫描」的对照组） |
| 被测代码树 | base + 本 PRD 的 90 条未提交改动（其中 37 条为 rename，保留 `git mv` 历史） |
| Python | 本 worktree `.venv`（CPython 3.13.13，pytest 9.0.2） |
| 环境注入 | `IAR_CONFIG=/Users/zata/code/keda/config.toml`（IAR agent 注入，指向**另一个** worktree 的配置）。rv-4 与本轮门禁**不清洗**该变量，见下文「与 runner 门禁同源」 |
| 控制台库 | rv-2/rv-3 在隔离 `HOME` 的 `tmp` 目录里造 v6 库并启动真实 `iar console`，不触碰用户 `~/.iar/console.db` |

## 与 runner 门禁同源

提交前门禁命令是 `bash -lc 'just test all'`（内部 = 全量 lint `SKIP=check-test-flag just lint --full` + `pytest --no-testmon` 全量套件）。rv-4 的段 [4] 跑的是**同一条命令、同一个未清洗环境**，不再用 `env -u IAR_CONFIG` 包壳，也不再手工拼 `CI=1 just test` + `uv run pytest --no-testmon` 这对近似命令。理由与返修过程见 PRD §14「返修复核」条目。

`just test`/`just lint` 有 warm-path 缓存（`$(git rev-parse --git-dir)/.last_{tested,linted}_commit`）。rv-4 在跑之前显式删除这两个标记，确保证据里是真跑而不是 flag 复用。

## Oracle 与执行入口

原始日志放在 `.iar/evidence/`（worktree 本地，`.gitignore` 排除，不进提交），并镜像一份到本目录同名文件。下表「证据文件」列的是 basename。

| id | 判据（摘要） | 实际执行（真实入口 / mock 边界） | 证据文件 |
|---|---|---|---|
| rv-1 | 对外入口硬改名：`iar backlog` 可用且 `iar roadmap` 移除；`/api/v1/agent-runner/backlog/*` 可用且旧 `/roadmap/*` 404 | `iar` 真实 Typer 命令树（子进程 `iar backlog advance --dry-run` / `iar roadmap advance --dry-run`）+ 真实 `backend.api.cli_parser.build_parser()` argparse 分支 + 真实 `uvicorn`（`app=backend.api.app:app`）上的真实 FastAPI 路由表。命令名与路径前缀直接取自命令树/路由表，不用常量转述；无 mock | `rv-1-cli-real.txt`、`rv-1-api-real.txt` |
| rv-2 | 控制台 SQLite v6→v7 迁移把 `roadmap_queue`/`roadmap_settings` 改名为 `backlog_*` 且数据无损；空库/缺表安全 | 真实 `sqlite3` 按 v6 时代的建表语句造库并塞行 → 真实 `SqliteConsoleStore` 打开触发就地迁移 → 直接查 `PRAGMA user_version` / `sqlite_master` / 逐列 `SELECT`；另有真实 `iar console` 进程在隔离 HOME 上打开同一个库后经 HTTP 读回。不 mock 数据库 | `rv-2-store-migration.txt`、`rv-2-console-migration-real.txt` |
| rv-3 | 控制台路由改名：真实浏览器打开 `/app/backlog` 渲染 PRD 待办工作台、标题与导航为 Backlog；`/app/roadmap` 不再提供 | 隔离 HOME 启动真实 `iar console` 进程（真实 Next.js 静态导出产物 + 真实 FastAPI 后端），真实 Chromium 打开页面断言 h2/侧边导航/工作台 PRD 计数并截图；同一浏览器探测旧路由。未注入组件状态、未用 TestClient | `rv-3-console-page-real.txt`、`rv-3-backlog-page.png` |
| rv-4 | 功能面 `roadmap` 零残留 + 架构守卫/测试/前端构建/文档 strict 全链路通过 | `rg` 直接扫最终代码树与构建产物（`src/backend`、`frontend-public`）；门禁按仓库既有命令执行：`SKIP=check-test-flag just lint --full`、`just test all`、`pnpm --dir frontend-public typecheck`、`just console-sync`、`uv run mkdocs build --strict`。无行为 mock | `rv-4-zero-hit-and-gates.txt` |

## 负控（每项都必须先证明会红）

不改生产代码来让 oracle 能红；负控一律在临时副本或临时注入里做，跑完即删。

| 对应 | 负控做法 | 期望红点 | 证据文件 |
|---|---|---|---|
| rv-1 CLI | `negative_cli.py` 把 `roadmap` 别名重新注册进真实 Typer 树 | 旧命令退出码回到 0 → `ASSERT-FAIL: 旧命令 iar roadmap 仍然可用`，`RESULT: FAIL (failures=1, mode=inject-negative)` | `rv-1-cli-negative-control.txt` |
| rv-1 API | `negative_app.py` 把 `/agent-runner/roadmap` 前缀重写回真实 backlog 处理器 | 旧路径返回 200 → `ASSERT-FAIL: 旧路径 /agent-runner/roadmap/* 仍可调用`，`RESULT: FAIL` | `rv-1-api-negative-control.txt` |
| rv-2 迁移 | 造好同一个 v6 库但**不让新代码打开它**，直接对未迁移库跑同一批断言 | 6 条 `ASSERT-FAIL`（`user_version=6`、缺 `backlog_queue`/`backlog_settings`、旧表仍存在、`no such table: backlog_queue`），`RESULT: FAIL (failures=6)` | `rv-2-migration-negative-control.txt` |
| rv-3 路由 | 复制当前静态产物并把旧路由目录 `app/roadmap` 恢复回来，真实静态服务器只跑路由判定 | `/app/roadmap/` 返回 200 → `ASSERT-FAIL: 旧路由 /app/roadmap 仍然提供` | `rv-3-console-route-negative-control.txt` |
| rv-4 零命中 | 同一套扫描跑在改名前的 git 树（`git archive 2a1dd6a`） | 扫出 `636` 行命中、涉及 50 个文件 → `RESULT: FAIL (failures=1, mode=inject-negative)` | `rv-4-zero-hit-and-gates-negative-control.txt` |
| rv-4 门禁环境 | 把 3 个测试文件还原成修复前（HEAD）版本、同 depth 放入 `tests/`，在真实 agent 环境（`IAR_CONFIG` 仍注入）跑同一批用例 | `6 failed, 4 passed`——正是上一轮 runner 门禁判红的那 6 项 | `rv-4-gate-env-negative-control.txt` |

## 复跑命令

```bash
cd /Users/zata/code/keda/.iar-worktrees/issue-196

# 一次跑完 rv-1..rv-4：每项先跑负控确认变红，再跑正向确认变绿；末尾逐文件核对
# RESULT: PASS / RESULT: FAIL 是否齐备（缺则打印 MISSING-PASS 并以 exit=1 结束）。
# 证据落在 .iar/evidence/，需再镜像到本目录。
bash .iar/evidence/scripts/run-all.sh

# 单项
bash .iar/evidence/scripts/rv-1-cli.sh && bash .iar/evidence/scripts/rv-1-api.sh
bash .iar/evidence/scripts/rv-2-migration.sh && bash .iar/evidence/scripts/rv-2-real-console.sh
bash .iar/evidence/scripts/rv-3-console-route.sh
bash .iar/evidence/scripts/rv-4-gates.sh

# 负控（任一 --inject-negative）
bash .iar/evidence/scripts/rv-1-cli.sh --inject-negative
bash .iar/evidence/scripts/rv-2-migration.sh --inject-negative

# runner 门禁本身
just test all

# 证据镜像到本目录（.iar/ 与 tasks/evidence/** 的非 .md 文件都被 .gitignore 排除）
cp .iar/evidence/evidence.json .iar/evidence/rv-*.txt .iar/evidence/rv-*.png \
   tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/
```

「rv-4 门禁环境」那条负控是**一次性**的：它要把 3 个测试文件还原成 HEAD 版本、以同目录同 depth 的
临时文件名放进 `tests/` 再跑，跑完立即删除，因此没有编进 `run-all.sh`（`run-all.sh` 会真的把这
批文件带进套件）。复现方式见 `.iar/evidence/rv-4-gate-env-negative-control.txt` 头部记录的注入说明。

## 口径与已披露限制

- **零命中的判定口径**：`rg -n -i roadmap src/backend frontend-public` 过滤白名单后零命中。白名单只有 `console_store.py` 第 199/203/204 行——v6→v7 迁移必须读取的旧表名字面量与注释。文档面允许 `ROADMAP.md`（战略文档名）与 `docs/prototypes/**`（历史原型资产）。口径来源见 PRD §7 实现期澄清与 §14。
- **rv-3 地址栏**：无头浏览器不渲染地址栏控件，"地址栏为 `/app/backlog`"由证据里的 `page.url()` 行与 `GET /app/roadmap/ -> 404` 行承担（PRD §9.1 同步披露）。
- **lint 跳过的唯一 hook**：`check-test-flag`。它只校验"当前 staged 树是否已被上一次 `just test` 覆盖"，而本 worktree 的改动按 runner 规则刻意保持未 staged，该 hook 会因 staged 树为空而报工作流状态错误。其余 hook（含架构守卫）照常执行并通过。
- **未执行**：`tests/playwright-e2e/` 改名后的 smoke/workflow 规格需要常驻已登录控制台，本批证据未执行；路由引用已静态核对，真实浏览器路径由 rv-3 覆盖。已记入 PRD §12。
