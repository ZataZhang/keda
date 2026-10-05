# 人工验收清单 · Roadmap 功能正名为 Backlog——端到端硬改名与控制台 SQLite v7 迁移

- PRD：`tasks/archive/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md`（交付时由 runner 从 `tasks/pending/` 归档），横幅为 🧍 待人工验收。
- Issue：ZataZhang/keda#196。代码 PR 由 runner 创建；PR 正文带"合并即验收"声明，**合并就等于下面 4 项都同意**，不必再到对话里逐条回。
- 状态：**执行侧已交付（rv-1..rv-4 全绿、各自负控全红），等你对 3 个决策和 1 项呈递物过目表态。** 这 4 项就是 PRD §9.2 `Human-Confirmed` 的 4 个空框。独立 verifier review 与归档仍是 runner-owned `[~]`，不由本清单代答。
- 证据报告：`open "/Users/zata/code/keda/.iar-worktrees/issue-196/tasks/evidence/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.evidence-report.md"`
- 也可以让工具打开交互版：`cd /Users/zata/code/keda/.iar-worktrees/issue-196 && just prd review tasks/archive/P1-REFACTOR-20261005-144335-roadmap-feature-rename-to-backlog.md`

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时一句"四项都同意"就够了。

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 功能整体正名为 Backlog，战略文档 `ROADMAP.md` 只加一句消歧、内容与里程碑命名不变 | §2 决策一 → §9.2 Human-Confirmed 第 1 项 | 如果战略文档也该改名，那 `ROADMAP.md` 里的里程碑命名会与功能命名继续撞车；反之若你其实想让功能继续叫 Roadmap，这批改名要整体回退 |
| 2 | 硬改名、旧 CLI 命令 / API 路径 / 控制台路由直接移除，不留兼容别名（破坏性对外变更） | §2 决策二 → §9.2 Human-Confirmed 第 2 项 | 已发布到 PyPI 的 `kedacode` 里，任何外部脚本、书签、API 调用会在升级后立刻失败。若你希望平滑过渡，需要另立一个短期兼容 PRD（本 PRD 明确不预留） |
| 3 | 控制台 SQLite 新增 v7 迁移，把 `roadmap_queue`/`roadmap_settings` 改名为 `backlog_*` 并保留数据 | §2 决策三 → §9.2 Human-Confirmed 第 3 项 | 迁移写在用户存量库里，跑错就是丢待办队列与 Autopilot 设置；若你更倾向只改代码标识符、表名留在 `roadmap_*`，就会长期保留"代码 backlog / 库表 roadmap"的分裂命名 |
| 4 | §9.1 的人读呈递物你已亲眼看过（`/app/backlog` 的真实控制台截图） | §9.1 → §9.2 Human-Confirmed 第 4 项 | 没看就确认，前三项的"验收"就只剩机器背书——上面三项都是关于命名的决策，命名对不对最直接的证据就是页面本身 |

**术语先解释一句**

- *功能面*：`src/backend` 与 `frontend-public`（含控制台构建产物）。"零残留"是对功能面判定，不是字面全仓零命中。
- *硬改名*：旧入口一律移除，没有 hidden alias、没有 redirect、没有并行实现。
- *v6→v7 迁移*：控制台库用 `PRAGMA user_version` 做就地迁移；本轮在既有迁移链上新增一步 `ALTER TABLE ... RENAME TO`。
- *验收状态横幅*：PRD 标题下的 `验收状态` 那一行，⬜ 未开工 / 🧍 待人工验收 / ✅ 已验收。
- *`[~]`*：清单里"等 runner 门禁"的标记，不是"已完成"。
- *oracle*：PRD §7 事先写好、能逐条判真假的验收判据，即 rv-1…rv-4。
- *负控*：把被验证的东西还原成"错误状态"再跑同一批断言，确认它会红；绿才有意义。

---

## 决策一 · 功能正名为 Backlog，战略文档 `ROADMAP.md` 不动

**你在拍板的事**：这个功能实为"以 PRD 文件为事实源的待办/队列工作台"，叫 Roadmap 会让人以为它承载战略方向，并与根目录 `ROADMAP.md` 撞名。接受把它在代码、UI、CLI、API、数据库与文档中的身份统一正名为 `Backlog`；`ROADMAP.md` 只在顶部加一句消歧说明。

**PRD 原话（§2 决策一）**：

> **请确认：** 接受把该功能整体正名为 Backlog，且 `ROADMAP.md` 战略文档只加消歧说明、不做内容改名？
>
> **验收：** 控制台 `/app/backlog`、CLI `iar backlog`、API `/agent-runner/backlog/*` 全部体现 Backlog；`ROADMAP.md` 仍存在且内容与里程碑命名不变。

**证据怎么说**：rv-3 截图里标题与侧边导航都是 Backlog，`rg -n -i roadmap src/backend frontend-public` 白名单外零命中；`ROADMAP.md` 的命中全部属于允许例外（战略文档本身 + `docs/prototypes/**` 历史原型资产）。见证据报告「rv-3」「rv-4」两节。

---

## 决策二 · 硬改名，不留兼容别名（破坏性对外变更）

**你在拍板的事**：`iar roadmap`、`/api/v1/agent-runner/roadmap/*`、`/app/roadmap` 三个对外入口一律直接移除，不保留 hidden alias 或重定向。

**PRD 原话（§2 决策二）**：

> **请确认：** 接受硬改名、旧入口直接移除（这是破坏性变更）？
>
> **验收：** `iar roadmap` 报未知命令；`/api/v1/agent-runner/roadmap/*` 与 `/app/roadmap` 均不再提供；新入口全部可用。

**代价与收益（PRD 原文的取舍）**：风险是老脚本 / 书签 / 外部 API 调用会中断；收益是命名彻底一致、无第二事实源。§3 明确列出四类调用方各受什么影响——其中影响最大的是"脚本里的 `iar roadmap advance` 必须改为 `iar backlog advance`"。若日后确需兼容，另立短期兼容 PRD，本 PRD 不预留。

**证据怎么说**：`iar roadmap advance --dry-run` 退出码 2 且 `Error: No such command 'roadmap'.`；真实 argparse 分支的 choices 里已经没有 `roadmap`；真实 uvicorn 上旧前缀 404、新前缀 200；真实浏览器上 `/app/roadmap/` 404。负控把 `roadmap` 别名重新注册回真实命令树、把旧路径重写回真实处理器之后，同一批断言立刻变红——证明"旧入口不存在"这条判定有判别力。

---

## 决策三 · 控制台 SQLite v7 迁移重命名两张表并保留数据

**你在拍板的事**：存量控制台库里 `roadmap_queue`、`roadmap_settings` 有真实数据。在既有 `PRAGMA user_version` 就地迁移链上新增 v7 一步，用 `ALTER TABLE ... RENAME TO` 改名，数据原样保留；同步把 `IRoadmapStore` 及其方法改名。

**PRD 原话（§2 决策三）**：

> **请确认：** 接受新增 v7 迁移重命名这两张表（保留数据），而不是只改代码标识符、把表名留在 `roadmap_*`？
>
> **验收：** 用含数据的 v6 库启动 console 后，表名为 `backlog_*`，行数与关键列值不变；对空库亦可迁移。

**已披露的边界**：真实 console 验证跑在隔离 `HOME` 的临时库上，**没有**直接操作你的 `~/.iar/console.db`——避免把迁移副作用打进真实账本。三种路径都演练过（有数据的 v6 库、全新空库、缺这两张表的 v6 库）。你本机库的迁移会在升级后第一次启动 `iar console` 时发生；要提前演练，可以先备份该文件再启动。

**证据怎么说**：真实 `sqlite3` 造的 v6 库经真实 `SqliteConsoleStore` 打开后 `user_version=7`，表名只剩 `backlog_queue`/`backlog_settings`，迁移前塞入的 2 行队列与 1 行设置逐列读回一致；同一个库由真实 `iar console` 进程打开后，`GET /api/v1/agent-runner/backlog/settings` 返回 `"max_parallel":3`——那个 3 正是迁移前写在 `roadmap_settings` 里的值。

---

## 第 4 项 · §9.1 人读呈递物过目

**你在看的东西**：真实 `iar console` 进程 + 真实 Chromium 打开 `/app/backlog` 的截图（验证层级：**real UI**）。已就地嵌在证据报告「rv-3」一节：

![真实控制台 /app/backlog：标题与导航为 Backlog，工作台渲染 7 个真实 PRD](rv-3-backlog-page.png)

10 秒自检：页面标题与侧边导航显示 Backlog，不再有"路线图/Roadmap"；工作台正常列出待办 PRD。

**披露**：无头浏览器不渲染地址栏控件，"地址栏为 `/app/backlog`"这项自检由同批日志里的 `page.url()` 行与 `GET /app/roadmap/ -> 404` 行承担（PRD §9.1 同步披露）。截图含本仓库真实 PRD 标题与 Issue 链接，均为仓库内公开内容，无密钥。
