# 验证计划：扩展内置 agent 覆盖（codebuddy / qoder / opencode）

对应 PRD：`tasks/archive/P1-FEAT-20260920-225333-builtin-agent-coverage-codebuddy-qoder-opencode.md`（归档前位于 `tasks/pending/`）
冻结凭证：`git diff HEAD -- src tests | shasum -a 256` = `2665f1d2ef8ba27c07ba1d7aeef7d0a3a129e5b31f86d780e627daadc0c66f51` @ HEAD `452bfaae`

## 验证目标

证明三件事：①三个新 agent 真的进入内置注册表且既有四个 agent 的行为逐字节不变；②用途集合不全的 agent（`opencode`）能写进 `config.toml` 且显式空段仍报错；③console 两层下拉在真实入口里真的渲染出这三个新 agent，且写回真的落盘。

## 风险分级与对应策略

本 PRD 无 R2/R3 改动点（无跨组件、跨进程、外部契约的不确定改动），全部按 R0/R1 深度取证：始终字段 + 一条能真正判别本次失败的断言。

| 改动点 | tier | 取证方式 |
|---|---|---|
| 三个 agent 的纯数据注册 | R1 | 出厂块守卫 + 命令行黄金快照（既有四条零 diff） |
| 合并判断修正 | R1 | 双向回归测试：可加载 + 显式空段仍报错 |
| `codebuddy` 反例改名 | R1 | 反例改名后两个测试文件绿 + 全局搜索无残留 |
| 容器认证派生名 | R1 | 派生结果断言 |
| 文档与原型枚举 | R0 | 文本断言 + 独立搜索复核 |
| console 可见性 | R1 | 真实入口截图 + 候选值集合断言 + 写回落盘 |

## oracle（与 PRD §7.6 同源，此处只列执行方式）

| id | 层 | 命令 / 入口 | 判定 |
|---|---|---|---|
| rv-1 | unit | `uv run pytest -o addopts='' tests/test_agent_spec_config.py -q` | 出厂块与内置 spec 逐字段一致 |
| rv-2 | unit | `uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q` | 三条新形态正确 + 既有四条零 diff |
| rv-3 | smoke（真实 CLI） | `uv run iar agent list` / `iar agent doctor <name> --all-profiles` | 三个新名字在列；doctor 打印 argv；`opencode` 的 `generate` 报错并退出非零 |
| rv-4 | unit | `uv run pytest ... -k opencode_has_no_generate_profile` | 未声明用途 fail-fast 且指名已声明用途 |
| rv-5 | integration | `rg -n 'codebuddy' tests/ src/` + 两个生命周期测试文件 | 无"未注册"用法残留 |
| rv-6 | integration | `container_auth.SUPPORTED_AGENT_SPECS` 派生断言 | 派生目录名正确（含 opencode 两段路径） |
| rv-7 | manual（真实 console） | 起隔离 console 实例 + standalone Playwright | 两层下拉各含三个新名字；写回落盘 |
| rv-8 | unit | 文本断言 + 独立 `rg` 复核 | 文档/原型枚举无遗漏 |
| rv-9 | unit | 合并修正的两条回归测试 | 可加载 + 空段仍报错 |
| rv-10 | e2e | `CI=true just test all` + `just lint --full` | 全量测试与 lint 通过 |

## 负控与基线

- **rv-9 的负控**：在未修改的基线代码上运行同一测试 —— 集成测试实际失败（`agents.opencode.profiles.generate is declared empty and has no built-in default`），证明该断言能判别修复前后。基线可用 `git show 452bfaae:src/...` + 临时 `PYTHONPATH` 构造，或直接读两个版本的函数逻辑对照。
- **环境的负控**：`tests/test_cli_console.py` 的端口用例在本机与主仓库 HEAD 上表现一致，用于区分"环境失败"与"本次回归"。

## 高保真入口

- rv-3 用真实 `iar` CLI（非 pytest 替身）。
- rv-7 用真实 console 实例：后端为被测 worktree 代码、`HOME`/`IAR_CONFIG` 指向隔离 fixture；前端沿用主仓库已构建的静态产物（本 PRD 未改前端代码）；仅 mock `/api/auth/me` 登录态，`lifecycle-agents` 与写回接口走真实后端。**不 mock 那个 agents 数组**——mock 它等于自己写答案。

## 明确不验证（并说明原因）

- 三个新 agent 的真实任务执行（会消耗模型额度、产生外部副作用）：`codebuddy` 与 `claude` 同构性来自 `--help`；`qoder` 的流式协议复用来自参数校验探针 + 安装包内 schema 读取；`opencode` 走 `plain` 无需协议推断。这一点在 PRD §12 已显式披露为"未做真实流式运行"。
- 容器镜像路径（PRD §11 非目标）。
- `dsh`（本轮出局）。
