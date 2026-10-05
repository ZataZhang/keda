# 证据报告：Roadmap 功能正名为 Backlog——端到端硬改名与控制台 SQLite v7 迁移

> PRD：`tasks/pending/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md`，判据以其 §7 Realistic Validation Plan 与 §9 Acceptance Checklist 为准。
> 同目录的 `….verification-plan.md` 说明怎么跑、跑在哪棵树上。
> 原始日志（`rv-*.txt`、`rv-3-backlog-page.png`、`evidence.json`）在本 worktree 的 `.iar/evidence/` 下，并镜像到本目录；`.gitignore` 规定只有 `*.md` 进提交，其余只留在本机。**下文引用的记录都从这些日志原样摘出，不另开文件也能读完。**
>
> **独立 verifier 结论：待 runner 触发。** 本报告由 executor 撰写，不含 verdict。verifier review 与 PRD 归档是 §9.2 里两条 runner-owned `[~]` 门禁。
>
> **本轮为返修轮（recovery attempt 1）**：上一轮 runner 的提交前门禁 `just test all` 判红（6 failed），根因与修复见 PRD §14「返修复核」条目与下文「门禁环境负控」一节。

## 人审导航

按 PRD §9.1，**人只需要看一项**：rv-3 的真实控制台截图。rv-1 / rv-2 / rv-4 是 `reviewer: verifier`，记录原文已嵌在下文，只在变红时需要你介入。

要你拍板的 3 项决策 + 1 项呈递物过目（共 4 个 `Human-Confirmed` 空框）汇总在同目录 `human-review-checklist.md`，可以这样打开：

```bash
cd /Users/zata/code/keda/.iar-worktrees/issue-196 && just prd review tasks/pending/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md
```

| # | 看什么 | 打开方式 | 逐项期望值 | 状态 |
|---|---|---|---|---|
| 1 | rv-3：真实控制台 `/app/backlog` 渲染 PRD 待办工作台，标题与导航为 Backlog，`/app/roadmap` 不再提供 | 本节下方嵌入的 `rv-3-backlog-page.png`（**真实 UI**，本地图片不入 Git）；原始日志：`open "/Users/zata/code/keda/.iar-worktrees/issue-196/.iar/evidence/rv-3-console-page-real.txt"` | ① 日志里 `GET /app/backlog/ -> HTTP 200`<br>② `浏览器地址栏 URL=http://127.0.0.1:49413/app/backlog/`<br>③ `页面 h2 文本=["Backlog"]`<br>④ 侧边导航含 `Backlog => /app/backlog/`，且没有「路线图/Roadmap」项<br>⑤ `工作台 PRD 计数=7`<br>⑥ `GET /app/roadmap/ -> 404` | ✅ 6 项齐备 |
| 2 | rv-1：旧 CLI 命令与旧 API 路径确实不存在（不是被别名藏起来） | 本报告「rv-1」一节；原始日志：`open "/Users/zata/code/keda/.iar-worktrees/issue-196/.iar/evidence/rv-1-cli-real.txt"` 与 `…/rv-1-api-real.txt` | ① `iar roadmap advance --dry-run` 退出码 2 且 `Error: No such command 'roadmap'.`<br>② argparse 分支的 choices 里没有 `roadmap`、有 `backlog`<br>③ 新路径 200 / 旧路径 404 | ✅ |
| 3 | rv-2：存量控制台库迁移后数据逐列无损 | 本报告「rv-2」一节；原始日志：`open "/Users/zata/code/keda/.iar-worktrees/issue-196/.iar/evidence/rv-2-store-migration.txt"` | ① `user_version=7`<br>② `tables=['backlog_queue', 'backlog_settings', ...]`，旧表名不再存在<br>③ 迁移前塞入的 2 行队列 + 1 行设置逐列读回一致（`max_parallel=3`） | ✅ |
| 4 | rv-4：功能面零残留 + 全链路门禁 | 本报告「rv-4」一节；原始日志：`open "/Users/zata/code/keda/.iar-worktrees/issue-196/.iar/evidence/rv-4-zero-hit-and-gates.txt"` | ① `白名单外零命中`<br>② `Check architecture layer dependencies....Passed`<br>③ `just test all` → `2888 passed, 1 skipped`<br>④ typecheck / `just console-sync` / `mkdocs build --strict` 均 `(gate exit=0)` | ✅ |
| 5 | 负控：6 份红记录 | 本报告「负控」一节 | 每份以 `RESULT: FAIL` 结束并带 `ASSERT-FAIL` 点名的具体禁止状态 | ✅ 6/6 变红 |

**已替你核对过的内容**

- 四个检查点都走**真实入口**：真实 `iar` 命令树、真实 argparse 解析器、真实 `uvicorn` + FastAPI 路由表、真实 `sqlite3` + 真实 `SqliteConsoleStore`、真实 `iar console` 进程 + 真实 Chromium。没有对被验证入口打桩。
- **门禁证据与 runner 门禁同源**：rv-4 段 [4] 跑的就是 runner 的验证命令 `just test all`，且**没有**清洗 `IAR_CONFIG`。上一轮的证据是"清洗过环境才绿"，与门禁不同源——本轮已修（见 PRD §14）。
- **每个检查点都证明了自己会红**：6 份负控见下节。没有任何命令用 `|| true` 之类兜底刷绿。
- **改名是硬改名**：无 alias、无 redirect、无并行实现；`git mv` 保留文件历史（37 条 rename 记录）。
- **残留命中都在白名单内且已逐行列出**：迁移必须读的 3 行旧表名字面量、战略文档 `ROADMAP.md`、`docs/prototypes/**` 历史原型资产。

## rv-3（人读呈递物）：真实控制台页面

无头 Chromium 在真实 `iar console` 进程上采集。**验证层级：real UI**（真实 Next.js 静态导出页面 + 真实 FastAPI 后端 + 真实浏览器渲染，未注入组件状态、未使用 TestClient）。

![真实控制台 /app/backlog：标题与导航为 Backlog，工作台渲染 7 个真实 PRD](rv-3-backlog-page.png)

> 该 PNG 与本报告同目录，用任意本地 Markdown 预览打开即可渲染。图片被 `.gitignore` 排除，在 GitHub 上会显示为坏图——这是「本地图片不入 Git」的既定口径，不是不嵌图的理由。

`.iar/evidence/rv-3-console-page-real.txt` 原文（节选）：

```
== 启动真实 iar console（隔离 HOME=/var/folders/.../tmp.HwwOlG5Gls/home port=49413）==
console pid=4594
== 打开真实控制台页面 http://127.0.0.1:49413/app/backlog/ ==
GET /app/backlog/ -> HTTP 200
浏览器地址栏 URL=http://127.0.0.1:49413/app/backlog/
页面 h2 文本=["Backlog"]
侧边导航=["Dashboard => /app/dashboard/","Processes => /app/processes/","Repositories => /app/repositories/","Stats => /app/stats/","Backlog => /app/backlog/","Ideas => /app/ideas/","Settings => /app/settings/"]
工作台 PRD 计数=7
正文含 roadmap 字样的行数=1
  roadmap-line: Roadmap 功能正名为 Backlog
== 探测旧路由 /app/roadmap/ 是否仍然提供 ==
GET /app/roadmap/ -> 404
RESULT: PASS (failures=0, mode=real-entry)
```

**披露**：无头浏览器不渲染地址栏控件，所以"地址栏为 `/app/backlog`"这一自检由 `浏览器地址栏 URL=`（真实 `page.url()`）与 `GET /app/roadmap/ -> 404` 两行承担。正文里唯一一行 `roadmap` 字样是本 PRD 自己的标题（`Roadmap 功能正名为 Backlog`），属于待办数据内容，不是功能命名。

## rv-1：对外入口硬改名

`.iar/evidence/rv-1-cli-real.txt`（真实 Typer 命令树 + 真实 argparse）：

```
== [1] new entry: iar backlog advance --dry-run ==
backlog advance (dry-run) repo=keda
max_parallel=2 free_slots=0 running_after=2
queued ['tasks/pending/P1-FEAT-20260930-225000-daemon-crash-reconciliation-session-resume.md']
== [2] legacy entry: iar roadmap advance --dry-run ==
Usage: iar [OPTIONS] COMMAND [ARGS]...
Try 'iar -h' for help.

Error: No such command 'roadmap'.
legacy probe exit=2
== [3] argparse 路径：backend.api.cli_parser.build_parser ==
parse_args(['roadmap','advance']) -> SystemExit(2)
iar: error: argument command: invalid choice: 'roadmap' (choose from init, ..., backlog, config)
parse_args(['backlog','advance']) -> command='backlog advance'
RESULT: PASS (failures=0, mode=real-entry)
```

`.iar/evidence/rv-1-api-real.txt`（真实 uvicorn + 真实路由表）：

```
== 启动真实 uvicorn（app=backend.api.app:app port=65509）==
GET /api/v1/agent-runner/backlog/prds  -> 200
GET /api/v1/agent-runner/roadmap/prds -> 404
新路径响应前 400 字节：
{"prds":[{"prd_path":"tasks/pending/P1-FEAT-20260916-134008-roadmap-prd-cicd-monitor-auto-repair.md","title":"Backlog PRD 完成后 CI/CD 监控与可选自动修复", ...
RESULT: PASS (failures=0, mode=real-entry)
```

覆盖范围：`advance` 一个子命令与 `prds`/`settings` 两个端点直接探测；其余 backlog 端点由全量测试与 rv-4 零命中扫描间接覆盖。`agent-overrides` 子路由同步改名见 `src/backend/api/routes/agent_runner_lifecycle_agents.py`。

## rv-2：控制台 SQLite v6→v7 迁移

定向测试（真实 pytest，`--no-testmon` 语义下的 5 个迁移用例全绿，含本轮新增两条）：

```
tests/test_console_store.py::test_backlog_migration_renames_roadmap_tables_and_keeps_rows PASSED
tests/test_console_store.py::test_backlog_migration_creates_missing_legacy_tables PASSED
5 passed, 17 deselected in 0.04s
```

用真实 `sqlite3` 按 v6 时代的建表语句造库（`roadmap_queue` 2 行 + `roadmap_settings` 1 行），交给真实 `SqliteConsoleStore` 打开触发就地迁移：

```
已通过真实 SqliteConsoleStore 打开库（触发就地迁移）
user_version=7
tables=['backlog_queue', 'backlog_settings', 'sqlite_sequence']
backlog_queue rows=[(1, 'keda', 'tasks/pending/P1-REFACTOR-...roadmap-feature-rename-to-backlog.md', 'running', 'manual', '2026-10-05T06:00:00+00:00', None, None), (2, 'keda', 'tasks/pending/P1-FEAT-20260916-134008-....md', 'queued', 'autopilot', None, None, 'legacy row before rename')]
backlog_settings rows=[('keda', 3, 'list', '2026-10-05T06:00:00+00:00')]
store.list_backlog_queue(keda) -> 2 条
store.get_backlog_settings(keda) -> BacklogSettingsEntry(repo_id='keda', max_parallel=3, default_view='list', ...)
RESULT: PASS (failures=0)
```

真实入口（隔离 HOME 启动真实 `iar console`，首次打开同一个 v6 库）：

```
GET /api/v1/agent-runner/backlog/settings -> 200
响应体：{"repo_id":"keda","max_parallel":3,"default_view":"list","updated_at":"2026-10-05T06:00:00+00:00"}
GET /api/v1/agent-runner/roadmap/settings -> 404（旧前缀应 404）
```

`max_parallel: 3` 是迁移前塞进 `roadmap_settings` 的旧值——它经 HTTP 读回来，证明迁移不只是建表，而是把真实数据搬到了新表名上。

三条路径均已演练：有数据的 v6 库、全新空库、缺这两张表的 v6 库（后两条补建空表且不凭空产生数据行，`backlog_queue=0 行 / backlog_settings=0 行`）。

**披露**：真实 console 验证跑在隔离 HOME 的临时库上，未直接操作用户 `~/.iar/console.db`，避免把迁移副作用打进真实账本。

## rv-4：零残留与全链路门禁

`.iar/evidence/rv-4-zero-hit-and-gates.txt` 六段，逐段结果：

| 段 | 门禁 | 结果 |
|---|---|---|
| [1] | `rg -n -i roadmap src/backend frontend-public`（含控制台静态产物）过滤白名单 | `白名单外零命中`。白名单命中仅 `console_store.py` 第 199/203/204 行（v6→v7 迁移必需旧表名字面量与注释） |
| [2] | `rg -n -i roadmap docs README.md mkdocs.yml` | 命中全部属于白名单：战略文档 `ROADMAP.md` 引用、`docs/prototypes/**` 历史原型资产 |
| [3] | `SKIP=check-test-flag just lint --full` | 全部 hook `Passed`，含 `Check architecture layer dependencies....Passed`、`Check PRD acceptance checklist....Passed`、`Check guard test modification....Passed`；仅 `check-test-flag` Skipped（理由见下） |
| [4] | **`just test all`**（runner 实际使用的验证命令：内部全量 lint + `--no-testmon` 全量 pytest） | `2888 passed, 1 skipped in 130.40s`，`(gate exit=0)` |
| [5] | `pnpm --dir frontend-public typecheck`（`tsc --noEmit`）+ `just console-sync`（`next build` + 静态产物同步） | 两者 `(gate exit=0)` |
| [6] | `uv run mkdocs build --strict` | `(gate exit=0)`，无断链报错 |

脚本末尾 `RESULT: PASS (failures=0, mode=real-entry)`。

**两点执行披露**：

1. 跑之前删除 `$(git rev-parse --git-dir)/.last_{tested,linted}_commit` 缓存标记，确保证据是真跑而不是 warm-path flag 复用。
2. `check-test-flag` 是唯一被跳过的 pre-commit hook：它只校验"当前 staged 树是否已被上一次 `just test` 覆盖"，而本 worktree 的改动按 runner 规则刻意保持未 staged，该 hook 会因 staged 树为空而报工作流状态错误；它自身的输出也确认内容未变化、已被段 [4] 的 `just test all` 覆盖。其余 hook 照常执行。

## 负控：每个检查点都先证明会红

| 对应 | 注入方式（不改生产代码） | 红点 | 证据文件 |
|---|---|---|---|
| rv-1 CLI | `negative_cli.py` 把 `roadmap` 别名重新注册进真实 Typer 树 | `ASSERT-FAIL: 旧命令 iar roadmap 仍然可用` → `RESULT: FAIL (failures=1, mode=inject-negative)` | `rv-1-cli-negative-control.txt` |
| rv-1 API | `negative_app.py` 把 `/agent-runner/roadmap` 前缀重写回真实 backlog 处理器 | `ASSERT-FAIL: 旧路径 /agent-runner/roadmap/* 仍可调用` → `RESULT: FAIL` | `rv-1-api-negative-control.txt` |
| rv-2 | 造好同一个 v6 库但不让新代码打开它，直接对未迁移库跑同一批断言 | 6 条 `ASSERT-FAIL`：`user_version=6（期望 7）`、缺少 `backlog_queue`、缺少 `backlog_settings`、旧表 `roadmap_queue`/`roadmap_settings` 仍存在、`no such table: backlog_queue` → `RESULT: FAIL (failures=6)` | `rv-2-migration-negative-control.txt`、`rv-2-console-migration-negative-control.txt` |
| rv-3 | 复制当前静态产物并把旧路由目录 `app/roadmap` 恢复回来，真实静态服务器只跑路由判定 | `ASSERT-FAIL: 旧路由 /app/roadmap 仍然提供` | `rv-3-console-route-negative-control.txt` |
| rv-4 零命中 | 同一套扫描跑在改名前的 git 树（HEAD `2a1dd6a`） | `改名前的功能面仍有 636 处 roadmap 命中`（涉及 50 个文件）→ `RESULT: FAIL (failures=1, mode=inject-negative)` | `rv-4-zero-hit-and-gates-negative-control.txt` |
| rv-4 门禁环境 | 把 3 个测试文件还原成修复前（HEAD）版本、以同 depth 临时文件名放进 `tests/`，在真实 agent 环境（`IAR_CONFIG` 仍指向主检出）跑同一批用例 | `6 failed, 4 passed`：4 个 `test_preview_env_script.py` 用例 + `test_agent_runner_reads_root_config_toml` + `test_iar_init_does_not_pollute_target_repo_config_toml` → `ASSERT-FAIL: 修复前同一批用例在真实门禁环境下 6 failed` | `rv-4-gate-env-negative-control.txt` |

最后一行正是上一轮 runner 门禁判红的那 6 项，因此它同时是「返修有效性」的负控：缺了测试侧的 `IAR_CONFIG` 隔离，同一批用例必然变红；补上隔离后 `just test all` 变绿。修复只动测试的环境隔离，未改动被测源码行为，也未新增/删除/跳过任何用例。

## 结构化证据清单

`.iar/evidence/evidence.json`（副本在本目录）声明 `version: 1`、`language: "zh-CN"`、4 个 `item_number` 为整数的 evidence block，每项含 `command` / `evidence_files`（bare filename）/ `output_summary` / `explanation` / `risks` / `negative_control` / `expected_fail` / `stdout_assertions`，rv-3 另含 `expected_artifacts`（`rv-3-backlog-page.png`，`mime: image/png`，`min_size: 50000`）。清单已用仓库自身的 `load_evidence_manifest()` 复验通过（4 项，文件齐全，无解析错误）。

## 绑定最终代码树

- 证据采集树 = `HEAD 2a1dd6aff786f191e53a81d99c02f4db750f4584` + 本 worktree 的 90 条未提交改动（37 条 rename），即 `git status --porcelain` 的当前输出；证据采集之后 `src/`、`tests/`、`docs/`、`frontend-public/` 未再变更。
- 提交由 runner 完成（executor 不碰 index），因此 PR head 的 tree 尚未存在。**PR 建好后**按 record-excluded tree 核对（排除三条 delivery-record 路径）：

```bash
cd /Users/zata/code/keda/.iar-worktrees/issue-196
P=tasks/pending/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md
tmp_index=$(mktemp)
GIT_INDEX_FILE="$tmp_index" git read-tree <pr-head>
GIT_INDEX_FILE="$tmp_index" git rm -r -q --cached --ignore-unmatch -- \
  "$P" "tasks/archive/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md" \
  "tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog"
GIT_INDEX_FILE="$tmp_index" git write-tree; rm -f "$tmp_index"
```

改这些路径之外的任何文件都会让本批证据过期，需要重跑 rv-1..rv-4。

## 还没做完的（都要你来）

1. 回答 4 项 `Human-Confirmed`（3 项决策 + §9.1 呈递物过目），回复格式见同目录 `human-review-checklist.md`。
2. 独立 verifier review 与 PRD 归档到 `tasks/archive/` 由 runner 执行；归档只代表执行侧交付完成，不等人工验收。
3. 人工确认后只回填验收记录（勾选 `Human-Confirmed`、横幅改 ✅ 已验收、追加 Change Log）；若人工验收发现 PRD 自身的 oracle 或范围未达成，把 PRD 移回 `tasks/pending/` 重开，而不是就地打补丁。
