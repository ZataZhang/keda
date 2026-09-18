# 证据报告 · 审核与修复分工可配置（repair_agent 阶段路由）

PRD：`tasks/pending/P1-FEAT-20260917-102125-stage-repair-agent-routing.md`
分支：`stage-repair-agent-routing`（worktree `/Users/zata/code/keda-worktrees/stage-repair-agent-routing`）
基线代码树：`96b4669`（`git merge-base HEAD main`）
执行时间：2026-09-18
执行器：CodeBuddy Code（会话内自证），待独立 verifier 复核

> 本报告引用的 `.txt`（评论正文 / 进程日志 / 测试输出）与 `scripts/`（harness 与 RV 脚本）
> 按 `.gitignore`（`tasks/evidence/**` + `!tasks/evidence/**/*.md`）刻意不入库，只在本机
> worktree 中留存；报告内给出绝对路径，`open` 即可复核。

## 0. 一句话结论

六个 oracle 全部通过，三个负控制全部按预期变红：非 self 模式下审核者只出 findings、
修复者接手且审核者的补丁被丢弃（rv-1）；PR 后的修复执行者按配置解析且提示词带 findings
（rv-2）；三处"配置写了不生效"的缺陷被修掉（rv-3）；不写新键的仓库行为与改动前逐行一致
（rv-4）；全量测试 2252 → 2267 全绿且 AST 守卫可复现地抓住漏传 config（rv-5）。

## 1. 自动化 oracle 结果

| Oracle | 入口（真实执行） | 结果 | 退出码 | 证据 |
|---|---|---|---|---|
| rv-1 | `bash tasks/evidence/P1-FEAT-20260917-102125-stage-repair-agent-routing/scripts/rv-1-pre-pr-split.sh` | PASS | 0 | `rv-1-pre-pr-split.txt` |
| rv-1 负控 | `bash .../scripts/rv-1-pre-pr-split.sh self` | 按预期 FAIL | 1 | 断言：`期望调用顺序 ['rv-implementer','rv-reviewer','rv-repairer']，实际 ['rv-implementer','rv-reviewer']` |
| rv-2 | `bash .../scripts/rv-2-post-pr-repair.sh` | PASS | 0 | `rv-2-post-pr-repair.txt` |
| rv-2 负控 | `bash .../scripts/rv-2-post-pr-repair.sh self` | 按预期 FAIL | 1 | 断言：`期望修复者=本次实现者 rv-implementer，实际 rv-supervisor` |
| rv-3 | `bash .../scripts/rv-3-routing-fixes.sh` | PASS（3/3 段） | 0 | `rv-3-routing-fixes.txt` |
| rv-3 负控 | `bash .../scripts/rv-3-routing-fixes.sh baseline` | 按预期 FAIL（3/3 段） | 1 | `rv-3-routing-fixes.baseline.txt` |
| rv-4 | `bash .../scripts/rv-4-default-unchanged.sh` | PASS（3/3 记录逐行一致） | 0 | `rv-4-default-unchanged.txt` |
| rv-5 | `uv run pytest -o addopts="" tests/` | PASS 2267 passed | 0 | `rv-5-tests.txt` |
| rv-5 守卫负控 | 删掉 `agent_runner_closeout.py` 的 `config=` 后重跑守卫 | 按预期 FAIL | 1 | `rv-5-tests.txt` |

## 2. 关键观察值（原样摘录，不重建）

### rv-1（pre-PR 审-修分工）

- 进程自报的可执行文件顺序：`rv-implementer → rv-reviewer → rv-repairer`；审核者 argv 含
  `--mode deliberate --sandbox read-only`（声明式只读形态真的生效），修复者是 `--mode run`。
- 审核者的提示词含 `Read-only review mode`；修复者的提示词含本轮 finding 标题原文
  `RV-FINDING: tighten the implementation`。
- 分支上恰好 2 个提交：`impl commit`（实现）、`repairer fix per findings`（修复者的
  commit-request 触发）；**没有** `reviewer illegal patch` —— 审核者越权写出的提交请求没有
  触发任何提交。
- Issue 评论正文（`tasks/evidence/P1-FEAT-20260917-102125-stage-repair-agent-routing/rv-1-pre-pr-split.txt`）逐字如下：

```markdown
<!-- iar:event version=1 phase=pre_pr_review cycle=1 head=e3292c9136ea811c6c6509204c586df93504aa3d -->

## Agent Runner Pre-PR Review

- Verdict: changes requested
- Reviewer: rv-reviewer
- Repairer: rv-repairer
- Head Before: `90bccb88e2e9e5d4e9aa30f09030b996f1d06011`
- Head After: `e3292c9136ea811c6c6509204c586df93504aa3d`
- Verification: passed
- Findings: 0 critical, 1 high, 0 medium, 0 low
- Action: repairer 'rv-repairer' patched and runner committed follow-up changes

Reviewer ran in read-only mode but wrote a commit request; it was discarded (no commit was made from it).

### Findings

| Severity | Category | File | Line | Title | Recommendation |
|---|---|---|---|---|---|
| high | code | impl.txt | 1 | RV-FINDING: tighten the implementation | Tighten it. |
```

> 同一脚本里若审核者**既**改了文件**又**没写提交请求，评论会再多一行
> `Reviewer edited files despite read-only mode; those edits were not rolled back and are
> carried into this cycle's repair commit.`（PRD §2 决策二要求"同样在评论里点名"）。
> 该分支由 `tests/test_agent_review.py::test_run_pre_pr_review_split_mode_names_reviewer_file_edits`
> 覆盖；rv-1 的假审核者只写提交请求、不落文件，因此呈递物里只有上面那一行。

### rv-2（PR 后修复执行者路由）

- 调用序列：`rv-implementer → rv-supervisor → rv-implementer → rv-supervisor`；修复者与
  supervisor 是两个不同可执行文件，且修复者就是本次实现者（`repair_agent = "executor"` 生效）。
- 修复者（第二次 `rv-implementer`）收到的提示词里含 supervisor 本轮 finding 的标题原文
  `RV-SUPERVISOR-FINDING: repair the PR branch`。
- 分支上 2 个提交（实现 + 修复），修复提交的 `git show --stat` 含 `repair.txt`。

### rv-3（三处路由修正）

| 段 | 断言 | 补丁后 | 基线（96b4669） |
|---|---|---|---|
| (a) | `allow_same_agent=false` 且实现者为 codex 时审核者不是 codex | 审核者 bin = `rv-claude`（注册表派生的第一个 ≠ 实现者） | 审核者 bin = `codex`（硬编码回落，断言红） |
| (b1) | `iar review` 取配置里的 supervisor | `['rv-supervisor']` | `[]`（漏传 config → 只在配置里注册的 agent 无法解析，整段报错） |
| (b2) | `--agent` 覆盖配置 | `['rv-alt']` | `[]` |
| (c) | 只在临时配置里注册的 agent 作审核者能被拉起 | `['rv-implementer', 'rv-reviewer']`，无 `not registered` | 只有 `['rv-implementer']`（审核阶段解析失败） |

代码层面对照（自动摘录）：补丁后 `review_once.py` 命中 `resolve_supervisor_agent` 2 次、
`agent_review.py` 不再含 `else "codex"`；基线代码 `review_once.py` 命中 `choose_agent`、
`agent_review.py` 含 `else "codex"` 1 处。

### rv-4（默认配置行为不变）

| 记录 | patched | baseline | diff |
|---|---|---|---|
| agent 调用序列 | `['codex','codex','codex','codex','codex','codex']` | 同左 | identical |
| 标签编辑序列 | `+agent/running -agent/ready` → `+agent/supervising -agent/ready` → `+agent/running -agent/ready` → `+agent/supervising -agent/ready` → `+agent/review -agent/ready` | 同左 | identical |
| 提交数 + 提交标题 | `3 / repairer fix per findings / reviewer patch / impl commit` | 同左 | identical |

两边都未写 `pre_pr_review.repair_agent` / `post_pr_supervisor.repair_agent`（默认 `self`）。

### rv-5（测试与守卫）

- 基线（`/tmp/iar-rv-baseline @ 96b4669`）：`2252 passed`。
- 本分支：`2267 passed`（= 2252 + 新增 15 条：三层映射 4 条、pre-PR 审-修分工 5 条、
  repair_agent 解析器 4 条、PR 后修复路由 1 条、supervisor 解析优先级 1 条）。
- AST 守卫负控：删掉 `agent_runner_closeout.py` 的 `config=` → 守卫失败并指名
  `agent_runner_closeout.py:522`；恢复后通过。

## 3. 与 PRD 的具体偏差（实现期的合理扩展）

1. **新增两个模块**：PRD §6 写的是"不新增模块"，但 `agent_review.py` 加完新逻辑后非空行数
   会涨到 1088，超过 `check_max_file_lines.py` 的 1000 行阈值（hook 是 warn-only，但仓库规范
   要求新代码不把文件推过线），因此按职责拆出两块：
   - `agent_review_repair.py`（138 行）：审-修分工的四个构件
     （`COMMIT_REQUEST_RELATIVE_PATH` / `resolve_reviewer_profile` /
     `build_commit_request_reminder_prompt` / `run_review_repair_agent`）；
   - `agent_review_comment.py`（99 行）：结果评论渲染（`build_pre_pr_review_result_comment`
     + `_escape_cell`），纯展示逻辑。
   `agent_review.py` 现为 **927 非空行**（限值 1000）；`agent_runner_feedback.py` 918 行。
   共享修复提示词构建器仍放在 `agent_runner_feedback.py`（pre-PR 与 post-PR 两边共用，
   不适合放进 review 专属模块）。
2. **`run_agent_with_prompt_resilient` 的关键字参数改为 `**agent_call_options` 透传**：
   jscpd（`min-lines=5`）判定它与 `run_agent_with_prompt` 的签名重复 12 行，而本次改动必须
   在两个入口都加 `profile`，必然触碰到该重复片段。为消除重复而不是加豁免，改为
   "一个入口声明参数、另一个原样透传"，所有调用点保持不变；AST 守卫相应允许"定义这两个
   入口的模块内部做 `**` 透传"，其余调用点仍必须显式传 `config=`。
3. **`run_verifier_agent` 新增 `config` 关键字参数**（默认 `None`）：该函数此前不接收
   `config`，FR-8 要求它的调用点传配置，因此加了一个可选形参；唯一生产调用方已传真实配置。
4. **`agent_runner_publication.py` 的 supervisor 解析改用共享函数**：以
   `fallback_agent=selected_agent` 保持既有语义（配置优先、`auto` 时回落本次实现者），
   只是把两处重复表达式收敛到 `resolve_supervisor_agent`。
5. **harness 的假 agent 角色由提示词判定**（而非可执行文件名）：rv-3(a) 需要用
   "把内置 agent 的 bin 覆盖成假二进制"来观察注册表派生结果，同一个假二进制必须能扮演
   实现 / 审核 / 修复 / 监督四种角色。这条只影响 `tasks/evidence/**/scripts/`，不进代码 diff。

以上偏差都不改变 PRD 的行为承诺；FR-10/FR-11 的验证结果见 rv-4 与 §1。

## 4. 未覆盖 / 已知限制

- **harness 与本机共享 `~/.iar`**：Issue 评论里的 `Attempt History` 会混入其他 harness 运行的
  记录。这不影响任何断言（断言只看 `agent.log` / `git log` / 假 gh 捕获的评论正文），
  rv-1 的呈递物已过滤成只剩 pre-PR review 结果评论。
- **只读约束的强度不均**：按 PRD §12 刻意接受——`deliberate` 用途对声明它的 agent 是沙箱级
  只读，对其余 agent 只是提示词约束，最终由"丢弃提交请求"兜底。rv-1 同时验证了这两条
 （`--sandbox read-only` 真的进了 argv + 越权提交请求真的被丢弃）。
- **`executor` 的标签漂移**：拿不到本次实现者的入口按 Issue 标签回落（rv-3 的 (b1)/(c)
  走的是"只在配置里注册"的路径；`rework` 路径的回落只在单测层面覆盖，未做 e2e）。
- 本机 `tests/test_cli_console.py` 依赖固定端口，全量跑时会偶发占用冲突（环境 flake，
  单独复跑即绿）；本次交付记录里出现过一次，已在提交前的复跑中确认非回归。

## 5. 人读入口

```bash
open "/Users/zata/code/keda-worktrees/stage-repair-agent-routing/tasks/evidence/P1-FEAT-20260917-102125-stage-repair-agent-routing/rv-1-pre-pr-split.txt"
```

10 秒自查：正文里 `- Reviewer:` 与 `- Repairer:` 是否为两个不同名字（`rv-reviewer` /
`rv-repairer`）；是否有 `Reviewer ran in read-only mode but wrote a commit request; it was
discarded` 一行。

## 6. 独立 verifier 复核与整改

第一轮独立复核（verifier agent，`P1-FEAT-20260917-102125-stage-repair-agent-routing.verifier-report.md`）
结论 **PASS with caveats**，绑定修订 `067ab98…` / `c592ce29…`。它自己重跑了全部 RV 与负控制、
自设计了三个对抗探针（配 `rv-alt` 作修复者、未注册 `rv-ghost`、executor 不可知回落），
未发现"配置写了不生效"或"默认行为被改变"的可复现反例，但提出 5 条 finding。整改如下：

| Finding | 内容 | 整改 |
|---|---|---|
| F1 | 复核期间执行器仍在写代码树，结论只能绑定修订 | 本条是流程问题：现已冻结写入，并由 verifier 对冻结修订做收尾重跑（见 verifier-report 的冻结轮次） |
| F2 | `executor` 回落来源只有实现、没有测试/证据；§9.2 引用了并不存在的"rv-1 中回落来源日志行" | 新增 4 条解析器单测（`test_resolve_repair_agent_*`，含 `caplog` 断言回落来源日志行）；§9.2 的证据引用改指该测试；`recover_publish` 路径显式标注 `executor_agent=None` 与其理由 |
| F3 | 非 self 模式日志把修复提交写成"reviewer wrote commit request / pushing reviewer patch"，与事实相反 | 三处日志改用 `patch_author`（`repairer '<agent>'` / `reviewer`），见 rv-1 运行日志输出 |
| F4 | 证据陈旧：用例 2262→2263；`agent_review.py` 实际 1012 行而报告称 980；守卫负控行号 80→522 | 拆出 `agent_review_comment.py`，`agent_review.py` 回到 **927 非空行**；用例数与行数、行号全部按实测更新（`rv-5-tests.txt` 附文件规模段） |
| F6 | `recover_publish.py:532` 未传 `executor_agent` | 这是 PRD D-03 的预期回落（该路径确实不知道本次实现者）；已显式传 `executor_agent=None` 并加注释说明来源，不再是"顺手漏掉" |

verifier 另外指出「lead 指定的探针 #2 前提不成立——`iar review` 从不调用 `execute_repair`，
该入口不解析 `repair_agent`」：属实，我在下任务时给错了前提；`iar review` 的修复走
`post_pr_rework_requested` marker → `iar run` 的 rework 路径，那条路径的 `repair_agent`
解析由 `agent_runner_issue_handlers` 覆盖且已传 `executor_agent=None`（同上）。

### 第二轮（冻结修订）结论

verifier 第二轮复核绑定**代码哈希**（比整树哈希更合适——证据 `.md` 不参与）：

```
git diff HEAD -- src tests | shasum -a 256
341440c90a7872d3f7a3a3e7dc2b20a7c6d1bca286c26cbe50f2da6f324a8c77
```

结论 **PASS**。它用隔离的 `RV_WORK_ROOT=/tmp/iar-rv-verifier` 完整重跑：rv1=0/self=1、
rv2=0/self=1、rv3=0(3/3)/baseline=1(3/3)、rv4=0；三个自设探针 A=0/B=0/C=1；
pytest 收集 2267 条，唯一失败是固定端口 flake（该文件单跑 17 passed）；架构 hook 通过。

harness 并发缺陷（rv-4 用固定 `/tmp/iar-rv/rv-4-*`，两个进程同时跑同一个 oracle 会互删目录）
已修：`RV_WORK_ROOT` 默认带上进程号 `$$`，需要复用时显式设置。
