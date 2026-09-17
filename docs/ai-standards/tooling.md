# Tooling Standards

## Preferred Tools

### Python

- 包管理：`uv`
- 任务入口：`just`
- 文档：`mkdocs`

优先顺序：

- 用 `uv` 代替 `pip` / `conda`
- 用 `just` 代替手工记忆零散命令

## Common Commands

| Command | Purpose |
|---|---|
| `just sync` | 同步开发依赖 |
| `just run` | 运行主应用（后端 + 管理平台前端 + 管理终端前端）；若前端 `node_modules` 缺失会自动运行 `pnpm install` |
| `just run backend_port=8010 frontend_admin_port=13173 frontend_public_port=3001` | 使用指定端口运行主应用，并保存为当前 Git worktree 的默认端口 |
| `just run frontend-public` | 只启动管理终端前端（Next.js，端口读取当前 run-state） |
| `just frontend-public dev` | 委托 `just run frontend-public`，读取当前 run-state 端口 |
| `just down` | 按当前 Git worktree 保存的端口停止本地开发服务 |
| `just copy <new-dir>` | 派生新项目；随机分配三个互不重叠的端口避免多副本端口冲突，并根据新项目名自动生成独立 PostgreSQL 数据库 |
| `just worktree <branch>` | 仅能从 Git primary worktree 创建；自动分配端口、创建专用 PostgreSQL 空库并执行迁移，开发与 E2E 共用该 Worktree 的数据库 |
| `just worktree -o <worktree-name>` | 打开已有 worktree；名称接受分支全名、分支最后一段、PRD slug、PRD 文件名（可带 `.md`）与 `tasks/pending/....md` 路径，歧义时报错列候选、未命中时列可用 worktree |
| `just test` | 运行本地测试 |
| `just bench-test` | 验证 warm / after-edit 场景的 `just test` 是否满足 30 秒预算 |
| `uv run mkdocs build` | 验证文档站点 |
| `just docs-serve` | 本地预览文档 |
| `just ai check <file> [claude\|kimi]` | 用 AI 审查单个文件；使用 kimi 时会自动恢复当前工作目录的上一个会话，方便追问 |
| `just ai fix [claude\|kimi]` | 用 AI 解决当前 git 冲突（rebase/merge/cherry-pick 等） |
| `just ai commit [claude\|kimi]` | 先跑 `just test`，再用 AI 生成提交信息 |
| `just ai implement <prd-file> [claude\|kimi]` | 按 PRD 实现功能 |
| `just prd status [all\|pending\|archive]` | PRD 状态看板：pending 逐条列出、archive 按月折叠，展示验收清单勾选进度、影响树触达进度（FILES 列）、证据包状态与运行态（ACTIVITY 列） |
| `just prd start <prd-file> [--tool <名称>] [--branch <名称>]` | 领取 PRD 执行锁：他人新鲜锁拒绝开工（退出 1）并输出持锁者信息；过期锁自动接管并留档；同归属重复领锁幂等刷新 |
| `just prd heartbeat <prd-file>` | 续期当前会话持有的执行锁；锁丢失或归属不符时警告并非零退出 |
| `just prd release <prd-file> [--force]` | 释放执行锁；归属不符需显式 `--force` |
| `just prd review <prd-file> [--print]` | 打开该 PRD 的人工审查清单（证据目录分支副本优先，交互 HTML 优先于 Markdown）：未完成 Human-Confirmed 项、9.1 人读呈递区与人工待决事项的集中页；无清单时回退打开证据报告，两者皆无时列出证据目录现状 |

## Justfile Layering

仓库根目录下的 `just` 入口被拆分为两层，分别由模板上游和派生项目自己拥有：

- `justfile.shared`：模板上游维护的共享 recipe 集合，由 `just sync-template` 同步。所有项目无关的脚手架命令（`sync`、`lint`、`test`、`docs-serve`、`clean`、`release`、`check`、`codex-notify`、`staged_changes`、`worktree`、`implement`、`sync-template`、`e2e`、`e2e-install`、`export-env-encrypted` 以及内部 `_check-completion`）都在这里。**不要手改这个文件**——改了下次 `just sync-template` 会提示覆盖。
- `justfile`：项目私有入口，第一行启用 `set allow-duplicate-recipes` 后通过 `import 'justfile.shared'` 引入共享层，之后只保留与项目结构耦合、仅模板维护者使用，或会触达本机 AI 工具目录的 recipe：`default`、`run`、`down`、`frontend`、`ops`、`sync-local-skills`、`copy`。派生项目可以自由增删此文件中的 recipe，`just sync-template` 默认会跳过它。

行为约定：

- `import` 是 just 1.19+ 原生命名空间合并；私有 `justfile` 使用 `set allow-duplicate-recipes` 允许本地同名 recipe 覆盖共享版本。需要本地化某条共享命令时，直接在 `justfile` 中重写同名 recipe 即可，不要去改 `justfile.shared`。
- 裸 `just` 默认运行 `default` recipe；根 `justfile` 需要保留本地 `default`，并委托执行 `just --list`。
- `just sync-template` 的默认跳过名单（`scripts/shared/template/sync_template.sh` 的 `_is_skipped_by_default`）已包含 `justfile`；`justfile.shared` 仍按常规规则进入 NEW/CHANGED 候选清单。
- `just copy <dir>` 通过定位 `justfile` 中的 `# Copy template to a new directory` 段落标记，把 `copy` recipe 及其之后的内容从 destination 的 `justfile` 中 trim 掉；destination 仍保留 `set allow-duplicate-recipes`、`import 'justfile.shared'` 与 `default`/`run`/`down`/`frontend` 起始版本。该 marker 是 contract，调整 `copy` 上方注释时需保持 marker 文本不变。
- 从单文件旧版升级时，建议手工把本地 `justfile` 重写为最小私有版（`import 'justfile.shared'` + 项目特有 recipe），避免与 import 进来的共享 recipe 重复定义。

## Run Port State

`just run` 会把运行状态写入项目根目录的 `.env.run-state`。该文件不进入版本控制（被 `.env*` 规则忽略）；每个 worktree 拥有自己独立的运行状态文件，天然避免多 worktree 端口冲突。

- 未传端口且状态文件不存在时，默认使用后端 `8000`、管理平台前端 `5173`、管理终端前端 `3000`。
- 传入 `backend_port`、`frontend_admin_port` 或 `frontend_public_port` 时，会保存本次端口配置。
- 后续 `just run` 和 `just down` 会复用保存的端口。
- 前端 Vite 使用 `strictPort`，端口被占用时直接失败，避免自动漂移后 `just down` 停错端口。
- `just copy <name>` 派生新项目时，会从三个互不重叠的区间随机分配端口（后端 `8000-8999`、管理平台前端 `5180-5999`、管理终端前端 `3010-3999`），并且只写入 destination 的 `.env.run-state`；不会改写 justfile、前端配置或文档中的 fallback。首次 `just run` 会读取随机端口，所选端口也会打印到 stdout。
- `just frontend dev` 与 `just frontend-public dev` 会委托给对应的 `just run` target，因此同样读取 `.env.run-state`，不会绕过已保存端口。
- `just run docker` 会把 `.env.run-state` 作为最后一层 Compose 插值环境，动态设置三个主机端口；容器内部端口仍固定为 backend `8000`、admin `80`、public `3000`，不属于需要随机化的主机端口。

## Run Process Guard

会启动 dev server 或触发前端构建的 `just` 入口（`run`，以及项目私有 justfile 中直接调用 `pnpm` 的前端 recipe）在执行前会 source `scripts/shared/just/process_guard.sh`，为整棵进程树设置 `RLIMIT_NPROC` 上限，兜住构建工具 fork 失控的场景。

- 触发背景：`next dev`(turbopack) 在陈旧 `.next` 产物下会无上限 spawn postcss worker，每个 worker 自带 `tailwindcss-oxide` 的 rayon 线程池（50–78MB / ~9 线程）且任务完成后不回收。实测一个页面请求即 spawn 159 个，数分钟可堆到数千进程、耗尽内存把机器压进 swap。撞上上限的代价只是该次构建失败，远小于整机失去响应。
- 上限按 **当前用户进程数 + 余量** 动态计算：`RLIMIT_NPROC` 统计的是整个 uid 的进程数而非本 shell 的子进程，写死一个小值会连启动命令自身都 fork 不出来。
- 余量默认 400；用 `RUN_PROCESS_HEADROOM=<n>` 调整，`RUN_PROCESS_HEADROOM=0` 关闭保护。
- 脚本缺失、余量非法或 hard limit 低于目标值时，打印告警并放行，不阻断启动。

## Project Database Isolation

`just copy <name>` 派生新项目时，会读取目标目录 `.env.local` 中的 `DATABASE_URL`。如果该 URL 使用 PostgreSQL，脚本会基于新项目名称派生一个唯一的数据库名（小写、下划线连接、不超过 63 字节），替换 URL 中的数据库名部分，并尝试连接 `postgres` 维护数据库自动创建该数据库；数据库创建后会立即执行 `uv run alembic upgrade head`。这样多个派生项目不会共享同一个数据库，且复制完成后已有完整迁移结构，避免迁移版本冲突或数据串扰。

- 自动创建依赖当前环境已安装的 `psycopg2`；若不可用或连接失败，会打印手动建库命令。
- 若 `.env.local` 不存在、未配置 `DATABASE_URL` 或使用非 PostgreSQL 数据库，则跳过 PostgreSQL 建库步骤，但复制流程仍会执行 Alembic 迁移。
- 仅修改目标目录的 `.env.local`；模板源文件中的 `.env.example` 保持占位符不变。

## Worktree Database Isolation

`just worktree <branch>` 只能从 Git primary worktree 发起。创建时会复制必要环境配置，但会为目标 Worktree 重新派生唯一的 PostgreSQL 数据库名（包含分支标识和短哈希），改写**目标 Worktree** 的 `.env.local` 中 `DATABASE_URL`，然后执行 `uv run alembic upgrade head`。因此每个 Worktree 有独立的 `alembic_version` 和数据，开发服务与该 Worktree 的 E2E 测试共用这一个可丢弃的数据库。

- 不复制主开发库或生产库的数据；初始状态是空库加当前迁移及应用 bootstrap seed。
- `.env.local` 缺少 PostgreSQL `DATABASE_URL`，或创建数据库失败时，Worktree 创建会失败，避免静默回退到共享库。
- Worktree 删除时数据库默认保留，方便排查；需要清理时手动删除对应数据库。
- 数据库隔离不解决 Alembic 源码迁移链并发：多个分支各自新增 migration 后，合并前仍必须 rebase 并运行 `uv run alembic heads`，确保仅有一个 head。

## Automatic Frontend Dependency Install

`just run`（以及 `just run frontend`、`just run frontend-public`）在启动前端服务前会检查对应目录是否存在 `node_modules/`。若缺失且系统已安装 `pnpm`，则自动运行 `pnpm install` 安装依赖；若 `pnpm` 未安装，会给出明确提示并退出。这样新克隆仓库或清理依赖后首次运行无需手动执行安装步骤。

## Docker Local Run

`just run docker` **强制要求当前目录存在 `.env.local`**（缺失时直接报错退出），并按 `settings.py` 的方式分层加载环境：**先 `.env`、后 `.env.local` 覆盖**。

- 实际命令是 `docker compose --env-file .env --env-file .env.local up --build`（`.env` 不存在时自动省略）。多个 `--env-file` 后者优先，所以 `.env.local` 覆盖 `.env` 中的同名键，并让 compose 的 `${VAR}` 替换读到最终值。
- `docker-compose.yml` 中各服务的 `env_file` 同样按 `[.env, .env.local]` 顺序列出（均 `required: false`），把两份文件合并注入容器，`.env.local` 优先。这与 `settings.py` 的 `env_file=(.env, .env.local)` 完全一致。
- 不做任何地址改写——你连哪个数据库 / 存储，由你在 `.env.local` 中自行决定。
- `docker-compose.dokploy.yml` 不使用 `env_file`，部署环境变量仍由 Dokploy 平台注入。

## PRD Workflow Hooks

本仓库通过 `pre-commit` 调用项目本地 `hooks/shared/check_prd_acceptance_checklist.py` 维护 PRD 交付状态；PRD skill 另带 `scripts/check_prd_acceptance_checklist.py`，供 agent 按 skill 相对路径运行，或由其他仓库自行接入：

- `tasks/pending/` 下的 PRD 可以保留未完成验收项
- `tasks/` 根目录下的旧 active PRD 必须完成 `Acceptance Checklist`
- 新增、复制或重命名进入 `tasks/archive/` 的 PRD 也必须完成验收清单
- 已存在的历史 archive PRD 不会因为普通修改被重新套用新规则
- 验收清单标题支持英文 `Acceptance Checklist`、中文 `验收清单` 和双语标题
- skill 内 checker 还会拒绝缺失关键值来源、必经边界、禁止旁路、fresh-state probe 或最终代码树证据的可执行 oracle

这条规则的目标是让“归档”代表交付完成，同时避免历史归档文档被新标准批量翻旧账。

当 agent 已加载 PRD skill 时，也可以从 skill 目录运行：

```bash
python scripts/check_prd_acceptance_checklist.py --repo-root "$PWD" --all
```

### PRD 状态看板

`just prd status` 汇总上述状态，输出 `tasks/pending` 的逐条明细与 `tasks/archive` 的月份折叠概览（`all` 展开每条）：

- 明细列：优先级与类型（取自文件名前缀）、创建日期、验收清单 `已勾/总数`、影响树触达进度（FILES 列）、证据包 `plan` / `report` / `verifier` 三个槽位。
- archive 概览：每月 PRD 条数、清单全完成的条数、有证据包的条数与 verifier `REJECT` 条数；月份下出现 `⚠ 有未勾完的清单` 时，会列出具体是哪几条。

**进度与证据取分支副本**：执行发生在 worktree 里，主仓库的 `tasks/pending` 副本与证据目录要等合并回主线才更新。因此存在分支名匹配的 worktree 时，清单进度取该 worktree 内 `tasks/archive` → `tasks/pending` 的 PRD 副本，证据包按 `plan` / `report` / `verifier` 每个槽位单独「分支目录先查、主仓库目录后查」；没有匹配 worktree（例如直接在主仓库开工）时读主仓库副本。只认 slug 匹配到的那个 worktree——每个 worktree 都带一份未改动的同名 pending 副本。

**FILES 列 = 影响树触达进度（弱信号）**：验收清单要到收尾才勾，从开工到验收之间看板原本没有任何进度粒度。FILES 列解析 PRD 的 `Change Impact Tree`，把其中的文件节点与**分支上实际改动过的文件集**求交，形如 `~7/12?2`。

- 判据是「本次分支碰过没有」，取 `git diff <与主线的分叉点>` 加未 `git add` 的新文件，覆盖已提交、已改未提交与新建三种状态。**不是**「文件在磁盘上存在」——模板历史 PRD 的影响树里 57% 的节点是 `[修改]`，那些文件开工前就在磁盘上，用存在性判定会让一条尚未动工的 PRD 直接显示过半完成。
- 树里点到**目录**（`src/backend/core/fcl_sync [修改]`）时，目录下任一文件被碰过即算触达；只做文件级精确比较会让这类节点永远判不出进度。
- 可判定性只认 **git 报出的真实路径**（`git ls-files`），不做文件系统探测：`Path.is_dir()` 在 macOS/Windows 上大小写不敏感，层级标签 `Docs` 会命中真实目录 `docs/`，让 `Docs/mkdocs.yml` 混进分母却永远匹配不上；被 gitignore 的目录（如运行时 `logs/`）同样会让跨仓库路径看起来讲得通。分母不该随操作系统或本地残留文件变。
- `?n` 后缀披露本地无法判定、因而**未计入分母**的节点数：跨仓库路径、`{a,b}.py` 花括号展开、通配符、`<由 … 生成>` 占位文件名。宁可少算也不用一个假分母换好看的百分比。
- 一行写多个文件时 ` / `、` + `、`、` 都是分隔符，后续文件继承第一个文件的目录（`locales/zh.json、en.json` 会拆成两个节点）。
- 只在存在分支名匹配的 worktree 时测量；没有分支可比对、PRD 没写影响树或树内没有可判定节点时显示 `-`。
- **永不转绿。** 它与 ACTIVITY 列的 mtime 启发式同级：影响树自己声明「以上为起点而非穷尽清单」，且「文件被碰过」不等于「改对了」——executor 换一条更合理的实现路径，触达率反而会掉。看板的强信号链仍是 CHECKLIST → EVIDENCE → verifier，FILES 不参与其中。

**看板是启发式视图，不是门禁**：清单进度按 `Acceptance Checklist` / `验收清单` 小节内的 `- [ ]` 与 `- [x]` 计数，`- [~]`（runner-owned gate）不计入总数；verifier 结论取报告中最后一次出现的显式结论行（`verdict:`、`结论：` 或整行独立的 `PASS`/`REJECT`），只能从散文推断时会加 `?` 标记。真正的把关仍由 `check_prd_acceptance_checklist.py` 与独立 verifier 完成，看板只用于快速定位。

### PRD 执行锁

执行 `tasks/pending/` 下的 PRD 前必须先领锁：`just prd start <prd-file>`（知道工具名就 `--tool` 自报）。锁语义：

- 锁是主仓库 `tasks/evidence/<prd-stem>/active.lock` 的本机 JSON 文件，被 gitignore 覆盖、不进版本库；在任意 linked worktree 内运行时经 `git rev-parse --git-common-dir` 反推主仓库根，各 worktree 共享同一份锁视图。
- 领锁原子化（排他创建）；他人**新鲜锁**硬拒绝并输出持锁者工具 / 分支 / worktree / 开始时间 / 最后心跳，想接手只能显式 `just prd release` 后重试；**过期锁**（心跳超 30 分钟）自动接管并把旧锁留档为 `active.lock.<时间戳>.stale`；同归属（worktree 相对路径一致）重复领锁幂等刷新。过期判定看心跳 + worktree 活性、不做 pid 探测——锁脚本与 agent 工具调用的会话都是短命的，pid 死亡不代表持锁会话已死；反过来，心跳过期但归属 worktree 在过期窗口内仍有文件改动时视为存活会话、拒绝接管，心跳只是无活性佐证时的兜底信号。
- 三个机械入口自动领锁：`just implement` 在校验 PRD 后、创建 worktree 前领锁（透传 `--tool` / `--branch`）；`just worktree`（`create.sh`）在分支名匹配 pending PRD 时先领锁、冲突即拒绝创建，创建成功后锁归属自动移交到新 worktree；`just worktree -o`（`open.sh`）打开已有 worktree 时同样尝试领锁（冲突仅提示持锁者、不阻塞打开），领锁记的是解析出的真实分支名。分支名 ↔ PRD 匹配规则的唯一事实源在 `scripts/shared/worktree/prd_branch_match.sh`（slug 与分支全名或分支最后一段相等即命中），与看板的文件名解析保持一致。`just worktree -o <worktree-name>` 接受看板与 PRD 流程里出现的各种名称——分支全名、分支最后一段、PRD slug、PRD 文件名（可带 `.md`）与 `tasks/pending/....md` 路径；精确分支名优先于 slug 等价匹配，命中多个 worktree 时报错并列出候选，未命中时列出当前可打开的 worktree（不猜旧约定路径 `$repo_parent/<名称>`）。
- 执行过程中每个主要步骤后运行 `just prd heartbeat <prd-file>` 续期；锁丢失或归属不符时 heartbeat 非零退出，便于 executor 发现锁已被接管。锁归属 worktree 有持续文件改动时活性探测会阻止误接管，心跳是兜底；主仓库持有的锁没有活性佐证，仍完全依赖心跳。
- 宽松兜底：提交时 `check_prd_lock_conflict` 钩子发现 staged 变更触及他人新鲜锁 PRD 的 `tasks/pending` / `tasks/evidence` 路径会输出警告，但永不阻断提交。
- 看板 ACTIVITY 列：**分支归档优先于一切锁信号**——存在分支名匹配的 worktree 且其中 `tasks/archive` 已有该 PRD 时，无论锁是新鲜还是过期（含带活性佐证的 RUNNING）一律显示绿色 `✔ branch-archived @<branch> · awaiting merge`（收尾已在分支完成，只差合并回主线；清单进度见 CHECKLIST 列，该列同样读分支副本，ACTIVITY 不再重复携带）；其余按锁状态渲染：新鲜锁显示 `RUNNING <tool> <时长> @<位置>`，位置按实际状态解析——锁归属主仓库（`worktree` 为空，例如 `just implement` 领锁后 worktree 尚未建出的窗口）显示 `@主仓库`，归属 worktree 仍存在时显示其当前实际检出的分支，目录已消失且锁里的分支也无处检出时显示归属标签本身（不照抄锁里的 `branch` 快照，否则看板会给出一个用 `just worktree -o` 打不开的名字）；过期锁但归属 worktree 仍有近期改动同样显示 `RUNNING`（活性佐证优先于心跳）；过期且无活性佐证显示 `STALE <最后心跳>`；无锁但存在分支名匹配的 worktree 且其中未归档该 PRD 显示黄色 `⚠ unlocked @<branch>`（互斥未生效，需进 worktree 补领锁）；无锁但 PRD 文件或证据目录 15 分钟内有改动显示暗色 `⚡ active <n>m ago`；其余 `-`。

### PRD 人工审查清单

当 PRD 只剩 `Human-Confirmed` 项、验收横幅翻成 `🧍 待人工验收` 时，执行方在证据目录 `tasks/evidence/<prd-stem>/` 生成 `human-review-checklist.md`：按审查顺序集中全部未完成项，每项为人类阅读而写——**决策先行**（开头点明要拍板什么、判断错了的后果）、**自足可读**（原样引用 PRD 表述并白话展开，引用只用于核验而非理解）、**稳定锚点**（按章节引用如 `§9.2 Human-Confirmed 第 N 条`，禁止行号——PRD 一编辑行号即漂移）、**渐进披露**（一句话结论 → 引文 → 证据 → 复跑命令放最后）、**证据走人类入口**（视觉呈递物用相对路径内嵌渲染，关键报告行原文摘录，打开命令只作可选深挖）、**回复格式明确**（每项写清回"同意"还是选选项/列差异）、**术语即用即解**（内部黑话首现处给一行白话定义）。涉及拍板的分歧（接受披露 / 要求补测等）给出可勾选的选项。清单携带截图或选项项时，同目录再生成自包含交互版 `human-review-checklist.html`（无外部依赖）：逐步向导、每项点按钮作答、进度存浏览器 localStorage、末页生成可复制的审查结果——Markdown 为静态底稿，两者内容必须一致。清单是审查会话的呈现面，**不是第二事实源**：确认结果回填 PRD §9 与证据包，清单本身留作人工审查的留痕。

`just prd review <prd-file>` 打开该清单（清单槽位内交互 HTML 优先于静态 Markdown）。证据目录解析与看板共用同一套规则（worktree 按 slug 匹配、分支副本优先）——执行发生在 worktree 里，未合并前证据不在主仓库，直接按主仓库路径找会扑空。没有清单时回退打开证据报告（其「人审导航」节是呈递物入口）；两者都没有时列出证据目录现状并退出 1，绝不静默成功。`--print` 只打印解析到的路径、不调用系统打开器，供测试与脚本消费。

## Platform Notes

- Windows 下优先使用 PowerShell 语法
- 文本文件读写显式指定 UTF-8

## Playwright Exception

`tests/playwright-e2e/` 是 Node/TypeScript 包：

- 使用 `npm`
- 不使用 `uv`
- 运行方式遵循该目录自己的 `README.md`

## Lint Modes

`just lint` 是默认开发反馈命令，等价于：

```bash
uv run pre-commit run --show-diff-on-failure
```

它使用同一份 `.pre-commit-config.yaml`，并按 pre-commit 默认 staged-files 语义运行；不会默认传入 `--all-files`，不会运行 manual 重复检测 hooks，也不会写入 `.last_linted_commit`。

完整模式：

- `just lint --full`：运行 `uv run pre-commit run --all-files --show-diff-on-failure`，通过后写入 `.last_linted_commit`；manual 重复检测 hooks 不属于该模式。
- `just lint --reuse`：显式运行 manual 重复检测 hooks：`jscpd`、`pylint-duplicate-code`，并补跑 `check-architecture`、`check-guidelines-consistency`、`check-max-file-lines`。
- `just lint --repo`：本地交付前总入口，串行执行 `just lint --full`、`just lint --reuse`、`just test`、`uv run mkdocs build --strict`，并在最后重新确认 full lint/test 标记。

推荐顺序：

1. 本地迭代和提交前频繁运行 `just lint`。
2. 修改复用边界、架构规则、AI 规范入口或疑似重复逻辑时运行 `just lint --reuse`。
3. 交付、PRD 归档或合并前运行 `just lint --repo`；如果时间受限，至少运行 `just lint --full` 和相关测试。

## AI Adapter Sync

`just sync-template` 默认模式只同步上游维护的基础设施（`justfile.shared`、`scripts/shared/*`、`scripts/build/*`、工具配置、`hooks/shared/*`、E2E 基础设施等），**不同步 AI 适配层文件**：

- AI 规范源：`docs/ai-standards/*`
- Copilot 入口：`.github/copilot-instructions.md`、`.github/instructions/*.md`
- Cursor 入口：`.cursor/commands/cursor.md`、`.cursor/rules/*`
- 跨工具入口摘要：`AGENTS.md`、`CLAUDE.md`

这些文件派生项目常会自定义（改入口指向、调整规范），默认覆盖会破坏项目定制，因此只在 `just sync-template --all` 模式才作为同步候选出现。实现见 `scripts/shared/template/sync_template.sh` 的 `_is_ai_adapter_file`。

模板包含 Skill 更新时，`just sync-template` 与 `just sync-local-skills` 遵守以下稳定契约：

- 显式配置的目标与当前检测到的工具适配器可以同时生效；同一批选中的 Skill 会同步到全部目标。
- `AI_SKILLS_DIRS` 接受以冒号分隔的任意 skills 目录，供新增、临时或未内置适配的 Agent 使用；`CC_SWITCH_SKILLS_DIR` 继续作为兼容入口。
- 非交互的 `scripts/sync_template.sh --skill <name>` 只更新目标中**已存在**的同名 Skill，不会创建首次安装目录；没有可更新安装时明确失败。
- 首次安装仍走交互式 `just sync-local-skills`；未检测到目标时才展示当前内置适配器供选择。

内置适配器是随工具生命周期增删的便利清单，目前包含 Codex、Claude、Pi、Qoder、Kimi Code 与 CodeBuddy；它们不是永久支持承诺。交互安装与非交互更新必须共用 `scripts/shared/template/sync_template.sh` 中的同一份适配器注册表，守卫测试保护上述工具无关契约，不为单个适配器建立永久性断言。

## Pre-commit Configuration Sync

`.pre-commit-config.yaml` 由模板上游维护，已通过 `scripts/shared/template/sync_template.sh` 的 `_is_upstream_owned()` 纳入同步清单。派生项目**不要直接手改**该文件——本地修改会在下次 `sync_template` 时被覆盖。

同步方式：

```bash
./scripts/shared/template/sync_template.sh
```

在 TUI 中选中 `.pre-commit-config.yaml` 的更新项并应用即可。应用后建议验证：

```bash
uv run pre-commit validate-config
uv run pre-commit run --all-files
```

### 项目差异如何处理

如果某个 hook 在不同派生项目需要不同行为（例如 Alembic 迁移文件名使用 `-` 还是 `_` 作为分隔符），**不要把差异写进 `.pre-commit-config.yaml`**，而应让 hook 脚本自身支持自动探测或通用参数：

- 优先在 `hooks/shared/<hook>.py` 中增加自动探测逻辑
- 次选通过环境变量或命令行参数让脚本自适应
- 避免在 YAML 里硬编码项目特定值

这样 `.pre-commit-config.yaml` 在所有项目中保持一致，模板改动可以一键同步到所有下游仓库。

### 项目私有 hook 放哪里

如果某个项目确实需要独有的 pre-commit hook，不要塞进 `.pre-commit-config.yaml`，建议：

- 把检查逻辑做成独立脚本，放到项目私有的 `hooks/`（非 `hooks/shared/`）目录
- 在 CI 或 `justfile` 中调用，而不是 pre-commit 统一配置
- 若该 hook 具有通用价值，优先提交到上游模板，让所有派生项目受益

## Quality Check Flags

`just lint --full` 和 `just test` 会把通过状态写入 Git 目录下的本地标记文件：

- `.last_linted_commit`：绑定当前分支、HEAD 和 lint 有效 tree；未变更时，后续 `just lint --full` 走快速路径。
- `.last_tested_commit`：绑定当前分支、HEAD 和 test 有效 tree；提交前由 `check-test-flag` 校验，并在 `just test` 入口用于判断是否需要重新跑测试。
- 对于刚 `git init`、尚无首个 commit 的仓库，flag 会绑定当前分支、`no-commit` 和对应有效 tree，因此模板仓库复制后可在首次提交前正常运行 `just test` / `just lint --full` / `git commit` 流程。

`just test` 在入口处先读 `.last_tested_commit`：若分支、HEAD 和 test 有效 tree 均未变化，则直接打印提示并退出，避免重复 pytest。如果 test 标记不命中，则进一步读 `.last_linted_commit`：命中时跳过 lint 前置直接进入 pytest，未命中时才执行 `SKIP=check-test-flag just lint --full`。这种"两级快路径"是 warm tree 上 `just test` 能稳定低于 30s 的关键。CI 环境（`CI` 非空）下会禁用这两条快路径，强制走完整 lint + pytest，避免跨 job/host 的 flag 文件泄漏掩盖回归。测试成功后会同时刷新 test 标记和 full lint 标记，避免刚跑完 `just test` 后再次执行完整 full lint。本地 pytest 默认使用 `-q`，CI 下回退到 `-v`。

`just lint --full` 的快速路径仍会执行轻量的 `check-test-flag`，除非调用方显式设置 `SKIP=check-test-flag`。如果 `SKIP` 跳过了除 `check-test-flag` 以外的 hook，本次 full lint 不会写入 `.last_linted_commit`。

当 Git index 中存在新增、复制或重命名进入 `tasks/archive/` 的 PRD 时，`just lint --full` 不使用快速路径，而是强制运行完整 `pre-commit`。这是因为 archive PRD 验收检查依赖 staged 状态，需要和提交阶段保持一致。

## Duplicate Detection Hooks

重复检测 hooks 被设置为 `manual` stage，不会在默认 `git commit`、`just lint` 或 `just lint --full` 中运行；需要通过 `just lint --reuse`、`just lint --repo` 或 CI/CD 的显式 reuse diagnostics 步骤运行：

- `jscpd`：跨 Python / TypeScript / JavaScript 的复制粘贴检测，版本由 `.pre-commit-config.yaml` 的 `additional_dependencies` 固定
- `pylint duplicate-code`：Python 结构级重复检测，版本由 `pyproject.toml` 与 `uv.lock` 固定

重复检测区分"候选文件"和"比较语料"：候选文件来自当前变更；`jscpd` 比较 `src/backend/`、`frontend/` 与 `frontend-public/`，`pylint duplicate-code` 比较 `src/backend/`。这样可以阻断新增重复，同时避免历史重复让干净工作区的 `just lint` 永久失败。

执行与性能约束：

- 两个重复检测 hook 均声明 `require_serial: true`。wrapper 每次调用都做一次全量语料扫描，若允许 pre-commit 按 CPU 分片并行传参，`--all-files` 会同时启动 N 份完全相同的全量扫描，内存与耗时放大 N 倍。
- `jscpd` 默认没有任何忽略规则、也不读取 `.gitignore`，wrapper 通过 `--ignore` 显式排除 `node_modules`、`dist`、`build`、`.next`、`coverage` 等依赖与构建产物目录，避免扫描产物导致单次扫描耗时数十分钟、内存数 GB。

常用调试命令：

| Command | Purpose |
|---|---|
| `just lint --reuse` | 运行所有复用、架构和规范一致性诊断 |
| `uv run pre-commit run jscpd --hook-stage manual --all-files` | 验证 jscpd hook 可执行 |
| `uv run pre-commit run pylint-duplicate-code --hook-stage manual --all-files` | 验证 pylint duplicate-code hook 可执行 |

若需要对干净工作区中的指定历史文件强制运行重复扫描，可临时设置 `DUPLICATION_CHECK_FORCE=1`。

误报处理优先级：

1. 复用已有函数或提取公共规则
2. 如果是合法相似 DTO / schema，缩小候选变更或在代码审查中说明
3. 不为绕过检测而复制逻辑或降低全局阈值

## PRD Workflow Hooks

本仓库通过 `pre-commit` 调用项目本地 `hooks/shared/check_prd_acceptance_checklist.py` 维护 PRD 交付状态；PRD skill 另带 `scripts/check_prd_acceptance_checklist.py`，供 agent 按 skill 相对路径运行，或由其他仓库自行接入：

- `tasks/pending/` 下的 PRD 可以保留未完成验收项
- `tasks/` 根目录下的旧 active PRD 必须完成 `Acceptance Checklist`
- 新增、复制或重命名进入 `tasks/archive/` 的 PRD 也必须完成验收清单
- 已存在的历史 archive PRD 不会因为普通修改被重新套用新规则
- 验收清单标题支持英文 `Acceptance Checklist`、中文 `验收清单` 和双语标题
- skill 内 checker 还会拒绝缺失关键值来源、必经边界、禁止旁路、fresh-state probe 或最终代码树证据的可执行 oracle

这条规则的目标是让"归档"代表交付完成，同时避免历史归档文档被新标准批量翻旧账。

当 agent 已加载 PRD skill 时，也可以从 skill 目录运行：

```bash
python scripts/check_prd_acceptance_checklist.py --repo-root "$PWD" --all
```

**PRD 归档动作**：实现完成后，将对应 PRD 从 `tasks/pending/` 移动到 `tasks/archive/`，并确保验收清单已全部完成。

## Codex macOS 通知

macOS 用户可以把 Codex CLI 的 `notify` 事件转发到系统快捷指令：

```bash
just codex-notify install codex通知
just codex-notify test codex通知
```

该能力会把顶层 `notify` 写入 `~/.codex/config.toml`，并在 Codex 完成 `agent-turn-complete` 事件时调用 macOS 快捷指令，快捷指令输入中包含当前任务仓库名和当前 Git 分支。详细步骤见 `docs/guides/codex-notifications.md`。
