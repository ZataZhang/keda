# 验证计划 · 审核与修复分工可配置（repair_agent 阶段路由）

PRD：`tasks/pending/P1-FEAT-20260917-102125-stage-repair-agent-routing.md`
分支：`stage-repair-agent-routing`（worktree `/Users/zata/code/keda-worktrees/stage-repair-agent-routing`）
基线代码树：`96b4669`（`git merge-base HEAD main`，即本 PRD 的开工基点）
执行时间：2026-09-18

## 验收项 → 可执行验证映射

| 验收项 | 验证方式 | 命令 / 入口 | 证据文件 |
|---|---|---|---|
| rv-1 非 self 模式下审核者只出 findings、修复者接手、审核者补丁被丢弃并点名（FR-3/FR-9，决策二） | e2e：真 `iar run` 子进程 + 真 git/worktree/commit proxy + 假 gh + 两个不同的假 agent 可执行文件 | `bash tasks/evidence/P1-FEAT-20260917-102125-stage-repair-agent-routing/scripts/rv-1-pre-pr-split.sh` | `rv-1-pre-pr-split.txt`（假 gh 捕获的 Issue 评论正文） |
| rv-1 negative control | 同一脚本把 `repair_agent` 改回 `self` 重跑 | `bash .../scripts/rv-1-pre-pr-split.sh self` | 见下方"负控设计"，退出码 1 |
| rv-2 PR 后修复执行者路由 + 修复提示词含 findings（FR-4/FR-5，决策一） | e2e：真 `iar run` → supervisor 循环 → `execute_repair` → 真子进程 → commit proxy | `bash .../scripts/rv-2-post-pr-repair.sh` | `rv-2-post-pr-repair.txt` |
| rv-2 negative control | 同一脚本把 `post_pr_supervisor.repair_agent` 改回 `self` | `bash .../scripts/rv-2-post-pr-repair.sh self` | 退出码 1 |
| rv-3 三处既有配置不再静默失效（FR-6/FR-7/FR-8，决策三） | e2e：真 `iar run` / `iar review`；注册表派生、CLI 参数解析、进程拉起全部真跑 | `bash .../scripts/rv-3-routing-fixes.sh` | `rv-3-routing-fixes.txt` |
| rv-3 negative control | 同一脚本在打补丁前的代码树上运行（`PYTHONPATH` 前置基线 src，同一 venv） | `bash .../scripts/rv-3-routing-fixes.sh baseline` | `rv-3-routing-fixes.baseline.txt`，三段全败 |
| rv-4 默认配置行为不变（FR-10，决策一） | e2e：同一 harness 在补丁前后各跑一次完整流程，逐行 diff agent 调用序列 / 标签编辑序列 / 提交记录 | `bash .../scripts/rv-4-default-unchanged.sh` | `rv-4-default-unchanged.txt` |
| rv-5 全量测试不回归 + AST 守卫（门禁） | 补丁前后各跑一次全量测试；AST 守卫在故意删掉任一 `config=` 时必须变红 | `uv run pytest -o addopts="" tests/`；`uv run pytest tests/test_agent_config_consistency.py -k call_site_passes_config` | `rv-5-tests.txt` |
| 两个新字段的三层映射（架构验收） | integration：非默认值穿 settings → factory → domain + 键说明表 + config.toml 登记 | `uv run pytest tests/test_agent_config_consistency.py -v` | `rv-5-tests.txt` |
| 文档同步（文档验收） | `docs/guides/agent-runner.md` 两段配置参考 + 审-修分工小节 + 三处修正；`config.toml` 新键与三种取值 | `just lint --repo`（含 mkdocs build） | `rv-5-tests.txt` |

## Mock 边界

- 所有 oracle 共用同一套无凭据 harness（`scripts/lib/harness.sh`）：
  - **真跑**：临时 git 仓库 + bare 远端、`git worktree`、`iar` CLI（真进程）、agent 子进程（真 argv、真写文件）、commit proxy、验证重跑、push、标签编辑。
  - **假 **`gh`**（`scripts/lib/fake_gh.py`）**：按 `src/backend/infrastructure/github_*.py` 实际调用的 argv 实现（`repo view` / `auth status` / `issue list|view|comment|edit` / `pr list|view|create` / `api`），每次调用以 JSON Lines 追加到 `logs/gh.log`；PR 上下文里的 `headRefOid` 用 `@issue-1` 占位符由假 gh 现场用真 git 解析，保证 `expected_head` 与实际 HEAD 一致。
  - **假 agent（`scripts/lib/fake_agent.py`）**：`rv-implementer` / `rv-reviewer` / `rv-repairer` / `rv-supervisor` / `rv-alt` 以及同名兜底 `codex` / `claude` / `kimi` / `pi`，全部放在 `PATH` 最前。同名兜底是必需的：被验证的正是"漏传 config 时回落到内置注册表"这一缺陷，没有它就会真的拉起本机 agent CLI。
  - 角色由**收到的提示词**判定（`Pre-PR Review for Issue` / `Read-only review mode` / `Repair the code for GitHub Issue` / `Post-PR Supervisor Review for Issue`），因此同一个假二进制在实现 / 审核 / 修复 / 监督四种提示词下都表现正确角色。
  - 每个假 agent 把自己的 argv[0] 与完整 argv 追加到 `logs/agent.log`——"谁真的被拉起来了"只以这份进程自报记录为准。
- **被测的路由与循环逻辑不 mock**：`resolve_repair_agent` / `resolve_reviewer_agent` / `resolve_supervisor_agent` / `run_pre_pr_review` / `_run_supervisor_with_repair_loop` / `execute_repair` 全部按真实调用链执行。
- 负控用的"打补丁前的代码树"是本仓库的一个 git worktree（`/tmp/iar-rv-baseline`，detached `96b4669`），通过 `PYTHONPATH=<baseline>/src` 在同一 venv 里加载旧模块，从而复用同一套 harness 与假可执行文件。

## 负控设计

| Oracle | 负控 | 期望差异 |
|---|---|---|
| rv-1 | `repair_agent = "self"` 重跑同一脚本 | 正向：`[rv-implementer, rv-reviewer, rv-repairer]`、仅一次修复提交、评论含 Reviewer/Repairer 两行与"已忽略审核者补丁"；负控：日志只有 `[rv-implementer, rv-reviewer]`，断言在"调用顺序"处失败退出 1 |
| rv-2 | `post_pr_supervisor.repair_agent = "self"` | 正向：supervisor 之后的修复子进程是 `rv-implementer`；负控：修复子进程是 `rv-supervisor`，断言失败退出 1 |
| rv-3 | 在 `96b4669` 的代码树上跑同一脚本 | 正向：三段断言全过；负控：三段全败 —— (a) 审核者仍是 `codex`（硬编码回落）、(b) `iar review` 未拉起配置里的 supervisor（漏传 config 导致 `rv-*` agent 解析失败）、(c) 只在配置里注册的 agent 无法在审核阶段被拉起 |
| rv-4 | 无（自身即为"补丁前 vs 补丁后"的对照） | 两边都不写新键：agent 调用序列、标签编辑序列、提交记录必须逐行一致 |
| rv-5 | 故意删掉任一 `run_agent_with_prompt*` 调用点的 `config=` 再跑 AST 守卫 | 守卫必须变红并指名漏传位置 |

## 对抗自检（实施期执行）

- rv-1：断言的是"进程日志里的可执行文件顺序 + 提交标题归属 + 假 gh 捕获的评论正文"，而不是函数返回值；修复提交的作者归属取自修复者写出的 `commit-request.json` 与进程 argv。
- rv-1：审核者的越权提交请求由假 agent 在**只读模式下主动写出**触发（`RV_REVIEWER_VIOLATES=1`），因此"被丢弃"是一个真实发生过的行为，而不是构造出来的空断言。
- rv-2：finding 标题取自假 supervisor 实际输出的 JSON，再在修复者收到的提示词文件里原样查找，不使用脚本另写的常量作为两侧来源。
- rv-3：每段用独立临时目录与独立 `iar` 进程，互不共享缓存；三段失败不互相掩盖（逐段报告后统一汇总）。
- rv-4：比较的是"可执行文件序列 / 标签编辑 / 提交标题"这些外部可观察量，刻意不比较提示词文本——共享修复提示词带上 findings 是本 PRD 有意引入的唯一默认差异（§1 解码表）。
- rv-5：全量测试在补丁前后的两棵树上各跑一次，用例数按"基线 + 新增"核对。
