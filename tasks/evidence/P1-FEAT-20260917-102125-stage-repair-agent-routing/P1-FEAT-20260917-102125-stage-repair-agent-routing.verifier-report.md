# Verifier Report · 审核与修复分工可配置（repair_agent 阶段路由）

- PRD：`tasks/pending/P1-FEAT-20260917-102125-stage-repair-agent-routing.md`
- worktree：`/Users/zata/code/keda-worktrees/stage-repair-agent-routing`（分支 `stage-repair-agent-routing`，HEAD `96b4669`）
- 复核时间：2026-09-18 11:38–12:00（执行器在工作区被反复并发修改，见 F1；本报告以**最终代码修订**为准）
- 复核者：独立 verifier（对抗式，全部自行重跑，不采信执行器自述）

**修订口径（重要）**：`tasks/evidence/**/*.md` 被 git 跟踪，因此「整树 `git diff HEAD` 哈希」会随证据文档一起变，不适合作冻结凭证。本报告以**代码哈希**为准：

```bash
git diff HEAD -- src tests | shasum -a 256
→ 341440c90a7872d3f7a3a3e7dc2b20a7c6d1bca286c26cbe50f2da6f324a8c77   # 最终代码（自 11:54 起未变）
git diff HEAD | shasum -a 256
→ 3ba67953b03da9b9bccd64a28738389ead26fae4d173fbdfe314a4b6dfa12316   # 含证据文档，会再变
```

复核过程中代码至少变更 4 次（11:41 / 11:44 / 11:51 / 11:54），我在 3 个代码修订上完整重跑过；下表为最终修订 `341440c9…` 的结果。

## 0. 结论

**PASS**（实现与最终证据均成立；剩下的问题都是流程/证据卫生与低危可用性，不是功能缺陷）。

- 四个正向 RV 与三个负控制在最终代码修订上全部按预期（正向 exit 0、负控 exit 1）。
- 我自设计的三个对抗探针全部通过（含 lead 要求但前提不成立的 #2，已换成可构造的等价探针）。
- 全量测试在最终代码修订上收集 2267 条；本机 2266 passed + 1 条已披露的固定端口环境 flake（`test_cli_console.py`，单独复跑 17 passed），无功能回归。
- 执行器在复核中途处理了我的 F2/F3/F4/F6（见 §6），最终修订上已修复/补齐。
- 唯一仍未闭合的是 **F2 的 PRD 文本引用**（§9.2 仍写「rv-1 中回落来源的日志行」，该行不存在）与 **F1 冻结流程**、**F11 harness 并发互相清目录**。

## 1. 最终修订 `341440c9…` 上的结果（我自己跑）

隔离并发：我用自己的 `RV_WORK_ROOT=/tmp/iar-rv-verifier` 运行，避免与执行器并发重跑互相清目录（见 F11）。

### 1.1 正向 oracle（`bash $S/rv-*.sh`）

| Oracle | exit | 关键观察（我从进程日志/假 gh 捕获读出） |
|---|---|---|
| `rv-1-pre-pr-split.sh` | 0 | 顺序 `rv-implementer → rv-reviewer → rv-repairer`；审核者 argv `--mode deliberate --sandbox read-only`；修复者 `--mode run`；分支 2 提交，无 `reviewer illegal patch`；评论含 `- Reviewer: rv-reviewer`、`- Repairer: rv-repairer`、`discarded (no commit was made from it)` |
| `rv-2-post-pr-repair.sh` | 0 | 顺序 `rv-implementer → rv-supervisor → rv-implementer → rv-supervisor`；修复提示词含 `RV-SUPERVISOR-FINDING: repair the PR branch`；2 提交 |
| `rv-3-routing-fixes.sh` | 0 | (a) `['rv-codex','rv-claude']`；(b1) `['rv-supervisor']`；(b2) `['rv-alt']`；(c) `['rv-implementer','rv-reviewer']` 且无 `not registered` |
| `rv-4-default-unchanged.sh` | 0 | agent 序列 / 标签编辑序列 / `commit_count=3`+提交标题 三条逐行一致 |

### 1.2 负控制

| 负控制 | exit | 失败点 |
|---|---|---|
| `rv-1 … self` | 1 | 调用顺序 `['rv-implementer','rv-reviewer']` |
| `rv-2 … self` | 1 | 修复者=`rv-supervisor` 而非实现者 |
| `rv-3 … baseline` | 1 | 3/3 段全败：(a) 审核者=`codex`；(b1)(b2) 无 agent 被拉起；(c) `Agent 'rv-reviewer' is not registered` |

### 1.3 全量测试

```bash
uv run pytest -o addopts="" tests/ -q
# → 1 failed, 2266 passed（CODE_H0 == CODE_H1 == 341440c9…，清洁绑定）
# 唯一失败：tests/test_cli_console.py::…::test_callback_always_binds_loopback
#   AssertionError: assert [('127.0.0.1', 58328)] == [('127.0.0.1', 58327)]
uv run pytest -o addopts="" tests/test_cli_console.py -q   # → 17 passed
```

- 该失败为已披露的固定端口环境 flake（本机有常驻 `iar daemon --repo-id keda-main` 在写 `~/.iar`），单独复跑即绿，与本 PRD 改动无关。
- 一处更早的整树运行（跨 11:54 写入）为 `2267 passed`；最终代码修订的失败条数由上述 flake 解释。基线（`/tmp/iar-rv-baseline @ 96b4669`）= `2252 passed`。

### 1.4 其它

| 检查 | 结果 |
|---|---|
| 架构依赖 `python hooks/shared/check_architecture.py` | 247 文件，全部合法 |
| 行数 `check_max_file_lines.py agent_review.py` | 拆出 `agent_review_comment.py` 后已回到 1000 行内（无 ERROR） |
| FR-8 调用点 | 13 个真实调用点全部传 `config=`；`run_agent_once.py` 内部一处 `**` 透传 |
| AST 守卫变异 | 真实树 GREEN（14 调用点，非空断言成立）；删 `agent_runner_closeout.py` 的 `config=` → RED 且指名 `:522` |
| 三层映射 | pydantic×2 / dataclass×2 / factory×2 / `.iar.toml` 键表×2 / `config.toml`×2 / 文档，齐全 |
| 残留 `"codex"` 回落 | 已无（仅无关模块的字面量） |

## 2. Harness 审计

- **真假边界成立**：只假 `gh` 与 agent CLI；`iar` CLI、git、worktree、commit proxy、验证重跑、push 全真跑。断言读 `logs/agent.log` 的 argv、`git log`/`rev-list`、假 gh 落盘的评论正文。
- **基线机制有效**：编辑安装用普通 `.pth`，`PYTHONPATH=<baseline>/src` 确实前置。我用 `python -c "import backend…; print(__file__)"` 验证过切换生效，rv-4 不是自比。
- **无恒真断言**；两处强度偏弱（非造假）：rv-1/rv-2 的 finding 标题在 harness 脚本与假 agent 里各写一份常量；rv-3(b1) 基线失败根因是「漏传 config 导致注册表只剩内置四表」，未把「ignoring configured supervisor」这条缺陷孤立证伪。

## 3. 对抗探针（最终修订，全部通过）

| 探针 | 设计 | exit | 观察 |
|---|---|---|---|
| A | `pre_pr_review.repair_agent="rv-alt"`（仅配置内注册）+ 审核者越权写提交请求 | 0 | 修复者切到 `rv-alt`、审核者仍 deliberate/read-only、2 提交、评论 `Repairer: rv-alt`、越权补丁未提交 |
| B | `resolve_repair_agent("executor", 省略 executor_agent)` + 捕获日志 | 0 | 回落 `claude`，日志 `falling back to Issue-label routing -> 'claude'` |
| C | `pre_pr_review.repair_agent="rv-ghost"`（未注册）跑 `iar run` | 0（断言成功，`CLI_EXIT=1`） | 只拉起实现者、无审核/修复者、错误逐字指名 `rv-ghost`，不回落 |

- lead 指定的探针 #2（`iar review` 路径验证 executor 回落）**前提不成立**：`iar review` 只跑 `run_post_pr_supervisor_cycle`，从不调用 `execute_repair`，该入口不解析 `repair_agent`。真正的回落点在 rework 路径与 `recover_publish.py`（后者已在最终修订显式传 `executor_agent=None`）。

## 4. §9 Acceptance Checklist 核对（最终修订）

| 条目 | 判定 |
|---|---|
| 决策一/二/三已答复 | 成立（但 §9.2 决策一的证据引用不实，见 F2） |
| rv-1..rv-4 通过 + 三个负控变红 | 成立（我独立重跑） |
| 未注册 `repair_agent` fail-fast | 成立（e2e 探针 C + 新单测） |
| 五处映射齐全 / AST 守卫可复现变红 / 四层依赖 | 成立 |
| 文档 / `config.toml` / `.iar.toml` 键表同步 | 成立 |
| 最高保真入口 / 关键值可追溯 / 新鲜状态复验 | 成立 |
| RV 脚本不在 diff | 成立（`git ls-files` 不含 `scripts/`，`.gitignore:81` 覆盖） |
| 全量测试 | 成立（2267 收集；2266 pass + 1 环境 flake，fl​ake 单跑绿） |
| `[~]` verifier / PR | runner-owned gate |

## 5. 最终修订上仍未闭合的项

### F2（MEDIUM）— PRD §9.2 决策一的证据引用仍不实
PRD 第 558 行仍写「证据：rv-4 的默认行为逐行一致记录 + **rv-1 中回落来源的日志行**」。rv-1 用具名修复者 `rv-repairer`，走具体名分支，不产生回落日志。
- 复现：`grep -n "falling back\|Issue-label routing" /tmp/vfy7-rv1.log` → 无；`grep -rl "falling back to Issue-label routing" /tmp/iar-rv-verifier/*/out/*.txt` → 无。
- 已缓解：执行器新增了单测 `tests/test_agent_runner_failure.py::test_resolve_repair_agent_executor_fallback_logs_its_source`（断言 caplog 含该日志），该行为现已有测试级证据。
- 待办：把 §9.2 的证据改成引用该单测（或本报告探针 B），否则仍是「清单声称有证据、实际引用不存在」。

### F1（流程 / MEDIUM）— 复核期间代码被反复并发修改
最终代码修订之外，代码在 11:41 / 11:44 / 11:51 / 11:54 被改；一次探针撞上半应用状态得到 `name 'has_changes' is not defined`（import 未落地。非交付缺陷，但说明中间态会污染结论）。
- 复现：`git diff HEAD -- src tests | shasum -a 256` 的多次取值 + 各文件 mtime。
- 建议：冻结以**代码哈希**（`-- src tests`）为准；冻结窗口内不写 `src/`、`tests/`、`config.toml`。

### F11（LOW，harness）— 并发重跑互相清目录
`rv-4` 用固定 `/tmp/iar-rv/rv-4-<label>`；当执行器与 verifier 同时跑同一 oracle 时，`rv_setup_harness` 的 `rm -rf` 会清掉对方目录，我因此偶发 `rv4=1`（`patched.agent-sequence.txt: No such file`），单独重跑即 0。
- 复现：并发跑两次 `rv-4-default-unchanged.sh`；或看我 11:53 的 `/tmp/vfy6-rv4.log` 与单独重跑的 `/tmp/vfy6-rv4b.log`。
- 建议：脚本支持/文档化 `RV_WORK_ROOT` 隔离（我复核时即用 `/tmp/iar-rv-verifier`）。

### F5（LOW，守卫强度）
AST 守卫只匹配裸名调用（漏 `module.foo(...)` / 别名）；对「定义入口的模块」内任何 `**kwargs` 转发调用豁免；只查 `config` 关键字存在、不校验取值。变异测试证明它**不空转**、能抓普通回退，结论仍成立。

### F7（INFO，harness）— `rv_run_iar` 覆盖调用方 `set +e`
函数内部结尾 `set -e`，使 `set +e; rv_run_iar …; code=$?` 这一惯用法失效（`rv-3(c)` 只因打补丁后 exit 0 才没暴雷）。改为 `rv_run_iar … || code=$?` 即可。

### F9（INFO，类型安全）
`run_agent_with_prompt_resilient(**agent_call_options)` 丢失静态签名校验（为消除 jscpd 重复的取舍，已在证据报告披露）。

### F12（INFO）— 本机无法独立运行 `ruff`
venv 内无 `ruff` console script 且未联网安装，lint 结论仅来自 pytest + 两个 hook（架构 / 行数）。

## 6. 执行器已修复的本轮发现

| 编号 | 原问题 | 最终修订状态 |
|---|---|---|
| F3 | 非 self 模式日志把修复提交写成 reviewer | **已修**：现为 `repairer 'rv-repairer' wrote commit request / pushing repairer 'rv-repairer' patch / repairer '…' changes committed`（`/tmp/vfy7-rv1.log`） |
| F4 | 用例数陈旧、`agent_review.py` 1012 行、守卫负控行号 80 | **用例数已更新为 2263→现 2267；行数已拆模块回到 1000 内**；证据报告 §2 仍写 `agent_runner_closeout.py:80`（实际唯一调用点 `:522`），属文档笔误 |
| F6 | `recover_publish.py` 漏传 executor_agent | **已修**：显式 `executor_agent=None` + 注明回落语义 |
| F2 | 回落来源日志无测试 | **已有单测**；仅剩 PRD §9.2 文本引用未改 |

## 7. 最薄弱的一环

**F2 的 PRD 文本**：行为与测试都已到位，但 §9.2 仍把「rv-1 的日志行」列为证据，而该行不存在。这是本次唯一一处「验收清单引用了不存在的产物」，属于证据诚信问题的残留，必须在归档前改掉。

## 8. 复核范围与限制

- 未修改任何被 git 跟踪的文件；仅写入本 `.verifier-report.md` 与 gitignored 的 `/tmp/**`、`tasks/evidence/**/*.txt`。
- 未运行 `just lint` / `pre-commit run --all-files`（会写工作区，且复核期工作区被并发修改）。
- 本机存在常驻 `iar daemon --repo-id keda-main` 在写 `~/.iar`（环境既有）；RV 断言不依赖它，全量测试仅出现一次已披露端口 flake。
