# Verifier Report：Roadmap 功能正名为 Backlog（P1-REFACTOR-20261005-144335）

- Verifier：独立 verifier（verifier-rename-backlog），与实现者无关，只读复核 + 自跑命令
- 复核日期：2026-10-05
- 复核树：`HEAD = 7d9d9c2dce31c866096c360a714c15bbacadacf6`（main，remote `zata`），以当前 HEAD 为最终交付树
- **Verdict：PASS**（附 1 项 major 披露 + 3 项 minor，均不构成对交付判据的否定，见 §结论）

## 冻结凭证

| 时点 | `git diff HEAD -- src tests` 的 sha256 |
|---|---|
| 复核开始 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空 diff） |
| 复核结束 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（一致） |

复核全程未修改 src/tests；工作区另有 `config.toml`、`tasks/inbox/*` 为其他会话的既有改动，非本复核产生，未触碰。

## 交付树定位（对任务简报的一处校正）

简报称"交付主体在 main 的提交 54ec13f"。实测 **54ec13f 只是 PRD 发布提交**（`docs: position iar CLI for agents…`，仅 5 个文档文件，0 rename）。**真正的改名交付提交是 cc5dd64（PR #199）**，其相对父提交 ab55374 的 `git diff -M` 实测 **94 个文件 / 38 条 rename**，与 PRD §14「verifier 复核校正改动集计数」的口径完全一致。

PRD §14 与 verification-plan 所写命令 `git diff -M 2a1dd6a..HEAD` 的 base 表述有误：2a1dd6a 确是改名前树（worktree base，正确），但最终交付树的 94/38 对应的是 `cc5dd64^..cc5dd64`，而非 `2a1dd6a..HEAD`（后者当前实测为 158 文件 / 40 rename，因含 #198/#200/#201/#202 的后续改动）。数字 94/38 本身正确，命令式标注错误（minor）。

## 逐项复核动作与结果

### 1. 改动集计数与 git mv 历史

```
git diff -M --name-only  cc5dd64^..cc5dd64  -> 94 文件
git diff -M --name-status cc5dd64^..cc5dd64 | grep -c '^R' -> 38 条 rename
```

结果：与 §14 校正后口径（94/38）一致。9 条 rename 均为同一批符号替换（core 模型/用例、api 路由、cli typer/parsed、前端路由目录/组件/api client、playwright specs），无并行实现、无 alias 层。✅

### 2. rv-1 等效 oracle：CLI 与 argparse（真实入口，当前 HEAD）

- `env -u IAR_CONFIG uv run iar backlog advance --dry-run` → 退出 0，输出调度计划（`backlog advance (dry-run) repo=keda / max_parallel=2 …`）。✅
- `env -u IAR_CONFIG uv run iar roadmap advance --dry-run` → 退出 2，`Error: No such command 'roadmap'.`（旧命令真移除，非隐藏别名）。✅
- `iar --help` 命令表含 `backlog`、无 `roadmap`。✅
- `backend.api.cli_parser.build_parser()`：`['roadmap','advance']` → `SystemExit(2)`（invalid choice），`['backlog','advance']` → `command='backlog advance'`。Typer 与 argparse 一致。✅

### 3. rv-1 等效 oracle：HTTP API（真实 FastAPI app 路由表）

- `backend.api.app.app` 路由表：含 `backlog` 的路由 14 条（prds/settings/content/autopilot/lifecycle/evidence/start/start-global/stop-global 等），含 `roadmap` 的路由 **0 条**。✅
- TestClient：`GET /api/v1/agent-runner/backlog/prds?repo_id=keda` → **200**；`GET /api/v1/agent-runner/roadmap/prds` → **404**。（首次不带 repo 参数得 422，恰证明路由存在而参数校验生效。）
- agent-overrides 子路由同步：`agent_runner_lifecycle_agents.py:247,289` 均为 `/agent-runner/backlog/prds/{encoded_path}/agent-overrides`。✅

### 4. rv-2 等效 oracle：SQLite v6→v7 迁移无损（真实 sqlite3 + 真实 SqliteConsoleStore）

手工构造 v6 库（`roadmap_queue` 1 行 + `roadmap_settings` 1 行，`PRAGMA user_version=6`），交给真实 `SqliteConsoleStore` 打开：

- `user_version = 7` ✅
- `sqlite_master` 只剩 `backlog_queue` / `backlog_settings`，旧表名不存 ✅
- 行与列值逐列读回一致（含 `max_parallel=3`）✅
- 定向测试：`uv run pytest -o addopts="" tests/test_console_store.py -k "backlog_migration or migration"` → 3 passed；`tests/test_backlog_actions.py + tests/test_console_store.py` → 25 passed ✅
- 白名单口径成立：`console_store.py:199,203,204` 是迁移读取旧表名的必需字面量/注释（`_BACKLOG_TABLE_RENAMES`），与 §7 实现期澄清一致 ✅

### 5. rv-4 等效 oracle：功能面零命中 + 门禁

- `rg -n -i roadmap src/backend frontend-public` 过滤白名单：**在交付树 cc5dd64 上零命中**（用 `git archive cc5dd64` 独立复核）。frontend-public 当前为 0 命中。✅
- 在**当前 HEAD** 上发现 1 处白名单外残留：`src/backend/api/cli_helpers.py:161` docstring 含 `` roadmap advance ``——由后续 PR #202（7d9d9c2）引入，非本 PRD 交付树的问题（见 §发现 minor-1）。
- `uv run mkdocs build --strict` → exit 0（2 条 INFO 级 anchor 提示，不阻塞）。✅
- 文档面残留均在白名单内：`mkdocs.yml:78-79`（`prototypes/roadmap-*.md` 历史原型路径，nav 文案已改 Backlog）、`docs/ai-standards/tooling.md:57`（`ROADMAP.md` 战略文档名引用）。✅
- 随包 skill：`iar-operator/SKILL.md` 内 `roadmap` 0 命中，含 "Open the Backlog page"。✅
- `ROADMAP.md:3-6` 顶部消歧 blockquote 存在，正文里程碑未改。✅
- 前端结构：`app/(app)/app/backlog/`、`components/backlog/`、`lib/api/backlog.ts` 存在；`types.ts` 内 `Backlog*` 22 处、`Roadmap` 0 处。✅
- 未复跑全量 `just test all`（~2900 项，任务允许定向重跑）；已定向重跑迁移与 backlog 相关 25 项 + mkdocs strict + 零命中扫描。PRD 引用的全量结果（2888 passed）无法在本机低成本复核，依赖其证据叙述。

### 6. 证据包完整性（major 发现所在）

PRD 横幅与 §9.2 引用的原始证据文件（`.iar/evidence/rv-1-cli-real.txt`、`rv-2-*.txt`、`rv-3-console-page-real.txt`、`rv-3-backlog-page.png`、`evidence.json` 等）**已全部不存在**：

- 主仓 `.iar/evidence/` 已被后续 PRD（closeout 系列）的 evidence 覆盖；
- 镜像目录 `tasks/evidence/P1-REFACTOR-20261005-144335…/` 只剩 3 份进提交的 `.md`（原始 `*.txt`/`*.png`/`evidence.json` 按既定口径不进 Git，且本机副本随 `.iar-worktrees/issue-196` worktree 删除而丢失；`git ls-remote` 无 issue-196 evidence 分支，全盘 find 无幸存 PNG）；
- worktree `/Users/zata/code/keda/.iar-worktrees/issue-196` 仅剩空目录残留（不在 `git worktree list`）。

即：执行侧 4 份 rv 证据与 6 份负控如今只剩 `.evidence-report.md` 里**逐字摘录的原文**，原始文件不可查验。rv-3 的 PNG（§9.1 唯一人读呈递物）已无法向人工验收者出示。缓解：本复核在当前 HEAD 上独立重跑了 rv-1/rv-2/rv-4 的全部等效 oracle 并全部通过，交付树本身的声明成立；但 rv-3 的"真实浏览器渲染"只能依赖报告摘录文字，无法独立重现（真实入口已被后续静态产物覆盖）。

## 对抗检查

构造的反例与结果：

1. **零命中反例（命中）**：`cli_helpers.py:161` 在 HEAD 上白名单外含 `roadmap advance`。溯源为 #202 引入的 docstring。判 minor：不影响行为（该命令确不存在），但说明"功能面零 roadmap"是不变量，后续 PR 已在 HEAD 上破坏它，宜顺手清理。
2. **旧入口复可用反例（未命中）**：尝试旧 CLI/argparse/旧 API 路径三种注入面，均为硬失败（exit 2 / SystemExit 2 / 404），无 alias/redirect。
3. **迁移丢行反例（未命中）**：构造含数据 v6 库，行数与列值无损。
4. **计数反例（命中，minor）**：§14 所写命令 `2a1dd6a..HEAD` 当前实测 158/40，≠ 94/38；正确命令应为 `cc5dd64^..cc5dd64`。

## 结论

### 发现清单

| 级别 | 发现 |
|---|---|
| major | 本 PRD 的原始证据文件（rv-*.txt / rv-3 PNG / evidence.json）已随 worktree 删除与 `.iar/evidence` 覆盖而永久丢失，仅剩 3 份 .md 报告中的逐字摘录；§9.1 人读呈递物（PNG）已无法向人工验收者出示。后续 4 项 Human-Confirmed 的人工验收缺少可出示的呈递物，建议人工验收前用当前 HEAD 补采一张 `/app/backlog` 截图或接受报告摘录为准 |
| minor-1 | HEAD（#202 引入）`src/backend/api/cli_helpers.py:161` docstring 残留 `roadmap advance`，破坏"功能面零 roadmap"不变量（交付树 cc5dd64 上无此问题） |
| minor-2 | §14/verification-plan 写的计数命令 `git diff -M 2a1dd6a..HEAD` 与实际测得 94/38 的口径不符（正确为 `cc5dd64^..cc5dd64`）；数字本身正确 |
| minor-3 | 简报所称交付提交 54ec13f 实为 PRD 发布提交，真正的改名交付提交是 cc5dd64（PR #199），PRD/报告未标注交付提交号，后续追溯需靠本报告 |

### Verdict：PASS

理由：交付树 cc5dd64 的全部对外契约判据（CLI 硬改名、API 硬改名、argparse 同步、agent-overrides 子路由、SQLite v7 迁移无损、功能面零命中、文档/skill/ROADMAP 消歧、mkdocs strict）在当前 HEAD 上经等效 oracle 独立复核全部成立；未发现 alias/并行实现/丢数据等 blocker。major 项属证据载体丢失而非判据不成立，且已由本复核的独立重跑实质性补偿；minor 项均为文档/后续 PR 层面的小瑕疵。

### Verified tree

- `git rev-parse HEAD` = `7d9d9c2dce31c866096c360a714c15bbacadacf6`
- record-path 排除口径：PRD 交付提交为 cc5dd64，其树不含 `tasks/archive/<stem>.md`（该文件由后续 runner 提交归档）。按 evidence-report 的口径对 HEAD 树排除三条 record 路径（archive PRD、evidence 目录、pending PRD）后，改动面与 cc5dd64 交付树在 src/tests/docs/frontend 上等价（本复核零命中扫描与 oracle 均直接作用于 HEAD 树，未发现 record 路径之外的判据偏移）。未单独计算排除后 tree hash（涉及临时 index 写操作，超出本复核只读约束的舒适边界），以 HEAD + 上述等价性说明代替。
