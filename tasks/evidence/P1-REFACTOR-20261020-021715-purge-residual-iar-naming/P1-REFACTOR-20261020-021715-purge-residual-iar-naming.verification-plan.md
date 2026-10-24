# Verification Plan

PRD: `tasks/pending/P1-REFACTOR-20261020-021715-purge-residual-iar-naming.md`

验证按风险从真实入口开始。所有 RV 原始输出分别保存在 `tasks/evidence/P1-REFACTOR-20261020-021715-purge-residual-iar-naming/rv-*.txt`；脚本只放在同目录的 `scripts/`，不进入代码 diff。

| Item | 入口与边界 | 负控 | 绿测 / 预期 |
|---|---|---|---|
| rv-1 | opt-in 测试 GitHub 仓库；`kc issue create --depends-on 292` 后用新进程读回新 Issue 正文 | 改名前版本执行同一入口，正文应出现旧依赖标记 | 新正文含 `<!-- kc:depends-on #292 -->`，不含新写入的旧前缀。当前缺少 `GH_TOKEN` 与 `KC_RV_SANDBOX_REPO`，留给 runner post-merge，禁止以单测替代。 |
| rv-2 | 安装版 `kc config migrate` / `kc run --dry-run`，仅 GitHub API 边界由持久化文件型 `gh` shim 替代；先看未迁移行为，再预演、apply、二次预演与进程拒绝 | 跳过 apply，断言旧认领仍阻止重复执行；该断言应失败 | 预演不写，apply 只替换 marker 前缀，fresh process 判为 active claim，第二次迁移 0 处，active runner 时无写入。当前进程枚举受 sandbox 的 sysctl PermissionError 阻断，apply fail-closed；作为 runner-owned gate 保留。 |
| rv-3 | 新建临时 Git 仓库，通过真实 `kc worktree create`、`ls -la`、`git worktree list` | 临时注入旧目录默认值 | 工作树只落在 `.kc-worktrees` 且 Git 元数据可见。 |
| rv-4 | 临时 Git 仓库预置 `.iar-worktrees`，通过真实 CLI 创建工作树 | 临时跳过旧目录解析 | 新工作树继续写入旧目录，无平行新目录。 |
| rv-5 | FastAPI `TestClient` 调用生产 Idea Inbox 路由，使用真实 HMAC 算法 | 改错新头 alias，参数化新头接受测试应失败 | 新旧头单独签名均返回 202；缺头返回 401。 |
| rv-6 | 运行仓库 guard | 临时向 docs 注入一个白名单外旧标记 | guard 对探针失败；删除探针后通过。 |

补充命令：`UV_CACHE_DIR=.uv-cache just test`、`PRE_COMMIT_HOME=.uv-cache/pre-commit UV_CACHE_DIR=.uv-cache just lint`、`UV_CACHE_DIR=.uv-cache just lint --reuse`、`UV_CACHE_DIR=.uv-cache uv run --offline mkdocs build`。测试标记必须在最终 evidence manifest 写完后重跑，以覆盖当前测试树。
