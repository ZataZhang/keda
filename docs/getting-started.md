# 快速开始

本文档说明如何在本地初始化并运行该模板项目。

## 环境要求

- Python 版本：`>=3.14`
- 包管理器：`uv`
- 推荐任务工具：`just`

## 安装依赖

安装主依赖和开发依赖：

```bash
just sync
```

首次启动开发环境（含 pre-commit hook 安装）：

```bash
just dev
```

## 运行项目

```bash
just run
```

默认会同时启动后端和前端：

- 后端默认执行 `uv run python -m backend.main`
- 前端默认进入 `frontend/` 目录执行 `npm run dev`

如果只想启动其中一部分，可以这样运行：

```bash
just run backend
just run frontend
```

如果项目实际目录或命令不同，可以覆盖默认参数：

```bash
just run all frontend_dir=web frontend_cmd="pnpm dev"
```

### 前端 dev server 的堆上限

`frontend-public/package.json` 的 `dev` 脚本带了 `NODE_OPTIONS=--max-old-space-size=3072`，不要删。

Next 的 `next dev` 在未显式指定 `max-old-space-size` 时，会把 V8 老生代上限设成**物理内存的一半**
（见 `next/dist/cli/next-dev.js`，32G 机器上就是 16 GiB），而 dev server 的堆会随会话单调增长。
实测在本机跑满 45 分钟后堆到 8.6 GB、峰值 12.6 GB，整机进入 swap thrash，
Next 自带的熔断要到约 14 GB 才触发重启，那时早已拖垮其他进程。

`next-dev.js` 里这个默认值只在 `NODE_OPTIONS` 未声明该参数时才生效，所以显式写上即可接管；
需要更大堆时改这里的数值，临时放开整段逻辑可用 `NEXT_DISABLE_MEM_OVERRIDE=1`。

排查时注意 `ps` 的 RSS 会骗人（堆被压缩换出后只报 1 GB 上下且剧烈抖动），
真实值在 `frontend-public/.next/dev/trace` 的 `memory-usage` 事件里（`memory.heapUsed`）。

## 测试

运行默认本地测试集：

```bash
just test
```

运行完整测试集：

```bash
just test all
```

## 文档预览

本项目已集成 MkDocs：

```bash
just docs-serve
```

构建静态文档：

```bash
uv run mkdocs build --strict
```

## Git Worktree

创建新的 worktree：

```bash
just worktree feature-branch
```

默认会同步 base branch 的远程 tracking ref 并作为起点创建 worktree，确保新分支基于最新远程提交。需要从其他分支创建时，传入 `--base`：

```bash
just worktree feature-branch --base develop
```

### 基于远程分支创建 worktree

当本地不存在同名分支、且某个 remote 下唯一存在同名分支时，`just worktree <branch>` 会**自动进入 checkout 模式**，创建一个跟踪该远程分支的本地分支，无需手动 `git fetch` + `git checkout`：

```bash
# 远程已有 origin/feature-login，本地尚无 feature-login
just worktree feature-login
# → 自动检测到远程分支并 checkout 成本地 worktree
```

也可以用 `--checkout` 显式指定来源分支，来源支持纯分支名或 `<remote>/<branch>` 形式：

```bash
just worktree issue-15 --checkout zata/issue-15   # 指定远程来源
just worktree issue-15 --checkout                 # 来源默认等于分支名
```

若希望忽略同名远程分支、强制新建本地分支，使用 `--new`（与 `--checkout` 互斥）：

```bash
just worktree feature-x --new
```

可通过环境变量 `KEDA_WORKTREE_SYNC_BASE=false` 关闭远程同步，`KEDA_WORKTREE_BASE_REMOTE` 覆盖默认 remote 名。

`just worktree`（底层实现位于 `scripts/shared/worktree/create.sh`）在创建 worktree 后会自动执行两类依赖准备：

- Python：如果仓库根目录存在 `pyproject.toml`，则运行 `uv sync --all-extras`。
- Frontend：扫描 worktree 根目录及其子目录中的 `package.json`，并在每个前端项目目录内按锁文件选择对应安装命令，例如 `npm ci`、`pnpm install`、`yarn install` 或 `bun install`。

这意味着像 `demo-frontend/`、`admin-frontend/` 这类把 `package.json` 放在子目录里的前端项目，也会在新 worktree 中自动完成依赖安装。

## 目录说明

- `src/backend/infrastructure/config/`：应用配置与环境变量解析。
- `src/backend/infrastructure/logging/`：日志器配置。
- `src/backend/infrastructure/helpers.py`：无状态通用辅助函数。
- `src/backend/infrastructure/models/`：模型配置加载与 LLM 客户端装配。
- `src/backend/engines/`：平台能力扩展点（项目按需挂载具体能力）。
- `src/backend/infrastructure/persistence/`：数据库接入与通用持久化工具。
- `tests/`：单元测试与集成测试。
- `docs/`：项目文档源目录。
