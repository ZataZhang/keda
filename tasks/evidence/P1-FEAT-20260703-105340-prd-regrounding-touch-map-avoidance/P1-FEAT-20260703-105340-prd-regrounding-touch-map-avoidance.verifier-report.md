# 独立 verifier 报告 · P1-FEAT-20260703-105340 执行开工前的 PRD 引用核验

- 审查对象：分支 `feat/prd-regrounding-touch-map-avoidance`；worktree `/Users/zata/code/keda-worktrees/feat/prd-regrounding-touch-map-avoidance`；基线 `main @ 4717191`
- 审查方式：只读 Explore agent；未执行任何 git 写操作、未运行 `just`/lint/e2e、未写任何文件；全部结论来自代理自己读到的代码与自己跑出的命令输出
- 轮次：第一轮全量复核（2026-09-17；审查期间工作树被并发改动，见"发现 8"）

## 结论摘要

**PASS with caveats**。核心命题成立：默认 execution 模板的规则段确实落在 `config.toml`，rv-1 的 3 条用例真的渲染自 `config.toml`（用独立剥离探针证伪了"假绿"），两条相反方向约束与位置断言都有效，`src/` 零改动、占位符闭集不变、既有契约全绿、证据包的退出码与叙述一致。**无阻塞级问题**。遗留均为口径/交付礼仪类：改动集已不再是"3 个文件"（PRD 出现未 staged 的 Change Log，evidence 目录未跟踪）、§9.1 呈递物路径未回填、Python 兜底模板与 config.toml 语义分叉（正常配置下不可达）。

## 逐项复核

| 命题 | 结论 | 代理给出的关键证据 |
|---|---|---|
| A1 运行时 execution 模板来自 `config.toml` 而非 Python 兜底 | 成立 | `agent_runner_feedback.py:267` `template = prompt_config.phases.get(phase, _DEFAULT_EXECUTION_TEMPLATE)`；`_DEFAULT_EXECUTION_TEMPLATE` 在 `:86-120`，**不含**规则段；`factory_config_builder.py:462-465` `PromptConfig(phases=dict(prompt_settings.phases))`；`factories/__init__.py:95-97` `build_app_config()` 取 `config.agent_runner`；`config.toml:461` 定义 `execution`。独立探针：`build_app_config().prompts.phases` 键为 `['execution']`，模板含 header=True（len=1416） |
| A2 `.iar.toml` 只覆盖部分 prompt 且不覆盖 `phases.execution` 时的来源 | config.toml（含规则段） | `factory_config_merge.py:122-135` `_merge_prompt_config`：`phases = dict(base_config.phases)`，仅当 override 含 `phases` 时 `phases.update(...)` —— **逐 phase 合并**。本 worktree `.iar.toml:176-177` 的 `[agent_runner.prompts.phases]` 为空表，`update({})` 为 no-op，故 execution 仍取全局 config.toml |
| A3 若本地覆盖了 `phases.execution` 是否完全接管 | 成立 | 同上 `phases.update` 会整键替换该 phase，仓库本地覆盖后完全不受影响，PRD/文档"完全不变"的说法正确 |
| A4 Python 兜底无规则段是否真实缺口 | 正常配置下不是真实缺口，是潜在口径分叉 | 独立探针：裸 `PromptConfig()` 渲染 → header=False 但 `Execution rules:`=True，证明兜底确实无规则段；但 `AgentRunnerPromptSettings.phases` 默认空 dict（`agent_runner_settings.py:325`）只影响未加载 config.toml 的构造；仓库根 `config.toml` 恒定义 `execution`，且 `.iar.toml` 无法删除该键，故兜底在实际 run 路径不可达。PRD"零 src 改动"据此忽略该处，判断成立 |
| B rv-1 新用例真的测到 config.toml | 成立 | `tests/test_agent_runner_prompt_contract.py:134,138` 用 `config = build_app_config()` → `config.prompts`（非裸 `PromptConfig()`）。独立探针剥离规则段后渲染 → header=False（绿必须来自模板真实段）。`resolve_config_toml_path()` 实测返回 worktree 的 `config.toml` |
| B 用例运行结果 | 3/3 绿 | `uv run pytest -p no:cacheprovider -o addopts="" tests/test_agent_runner_prompt_contract.py -k map_check -v` → `3 passed, 7 deselected in 0.09s` |
| C1 两条相反方向约束是否都被断言 | 成立且真实 | `:150` 断言 `follow the current code and adapt the plan`，`:151-153` 断言 `implement it as specified`；两条字符串在 `config.toml:474,475` 中确实存在 |
| C2 位置断言能否拦"塞到模板末尾" | 能 | `:156-160` 断言 `index("Issue body:") < index(header) < index("Execution rules:")`。独立探针把规则段移到 `Execution rules:` 之后 → 用例正确抛 `AssertionError`（"位置不符合约定"） |
| C3 负控正控是否足够 | 足够 | `:228` `stripped_template != template` 防"没删到"；`:241-242` 期望 `AssertionError`；`:244-245` 断言剥离后 header 不在、`Issue body:`/`Execution rules:` 仍在，排除"渲染损坏"伪因 |
| C4 `_strip_map_check_block` 边界 | 基本健壮，有理论边界 | `:174-182` 用 `line.strip()==header` 起、`line.startswith("Execution rules:")` 止。探针：模板出现两次同名标题 → 从头删到 Execution rules（残留 header 数=0，可接受）；**规则段处于文件末尾且其后无 `Execution rules:`** 时会吞掉其后所有行。真实模板恒有 `Execution rules:` 跟随，且该函数仅测试内使用，无生产影响 |
| D1 既有 4 类契约与 `test_agent_runner_prompt.py` 未被破坏 | 成立 | 仅 `tests/test_agent_runner_prompt_contract.py` 在改动集中（`test_agent_runner_prompt.py` 未出现）；diff 无删除行。`uv run pytest -p no:cacheprovider -o addopts="" tests/test_agent_runner_prompt_contract.py tests/test_agent_runner_prompt.py -q` → `37 passed in 0.09s` |
| D2 占位符闭集不变 / `.format()` 不炸 | 成立 | `git diff -U0 HEAD -- config.toml \| rg '^\+.*\{[a-z_]+\}'` 零命中（exit=1）；新 4 行内无 `{`/`}`（`rg '\{\|\}'` exit=1）；独立渲染成功无 `KeyError` |
| E1 文档记录规则/格式/非门禁 | 成立，5 处命中 | `rg -c "PRD map check" docs/guides/agent-runner.md` = 5（2238、2242、2296、2298、2306）；`:2300-2308` 覆盖三条规则、收尾声明格式、`增益指令，不是门禁…不阻塞、不报错、不回滚`。文档"覆盖过 `phases.execution` 的仓库完全不受影响"与 A3 合并逻辑一致，未说过头 |
| E2 原版专有名词零命中（PRD 指定 scope） | 成立（scope 内） | `rg -n "regrounding\|touch-map" config.toml src/backend/ tests/ docs/` 零命中；`git diff --name-only HEAD -- src/` 零命中 |
| E3 改动集只有 3 个文件 | **当时已不成立（口径）** | 首轮 `git status --short` = 3 个 staged 文件 + 未跟踪 evidence；18:06 后 PRD 出现未 staged 修改（`git diff --stat`：1 file changed, +12/-3），当时实为 3 staged + 1 unstaged PRD + 未跟踪 evidence 目录 |
| F1 `.txt` 有无隐藏失败/矛盾退出码 | 无 | `rg "EXIT=" rv-3-test-all.txt` 仅 `2269:EXIT=0`；`rg -c "FAILED\|ERROR"` 无命中；无小写 `failed`/`Traceback`（除 PASSED 行） |
| F2 rv-1 渲染两段 | 均含规则段、位置正确、路径真实 | `rv-1-rendered-execution-prompt.txt` WITH/WITHOUT-PRD 均在 `Issue body:` 后 `Execution rules:` 前含规则段；worktree 路径为真实的 `/var/folders/.../T/tmpcyk_hg2j`；Execution rules 8 条与 `config.toml:479-486` 逐字一致 |
| F3 `just test all` 是否被编辑过/前后矛盾 | 单次成功，无矛盾 | 文件头 `### just test all (第 2 次，lint 自动格式化已落盘)`，尾 `2252 passed in 98.66s` + `EXIT=0`；全文 2269 行 ≈ 2252 条 PASSED + 头尾，无残留的旧 `EXIT=1` 或失败段 |
| F4 基线负控真实 | 成立 | 独立复跑 `git show 4717191:config.toml \| rg 'PRD map check'` exit=1；`git show 4717191:tests/...py \| rg 'map_check'` exit=1 |
| G1 FR-1~FR-4 / rv-1 / rv-3 与实现是否对得上 | 对得上 | 模板插入点、4 条规则、声明格式、文档小节、零新增变量/配置键，均逐条命中；`config.toml:455-456` 的可选注释也落地 |
| G2 rv-2 跳过是否合规、理由是否成立 | 合规且理由成立 | PRD §7.6 `rv-2: required_for_acceptance: false`，§9.2 明确"无条件环境显式记录跳过理由并以 rv-1 全绿替代"；`verification-plan.md:23-29` 与 `evidence-report.md:41-49` 均记录三条理由（需新建 Issue 属共享状态、oracle 无额外判别力、rv-1 直接覆盖） |
| G3 §9.1/§9.2 是否有未满足项 | 有交付礼仪类未满足，非本 PR 阻塞 | 见"发现 4"：§9.1 呈递物路径未回填到 PRD 表；§9.2 勾选框当时仍空；PRD 顶部横幅仍 `⬜ 未开工`；"完成回复原样带上呈递表内容"属 team-lead 交付动作，代理无法验证 |

## 反例探针

1. **模板来源剥离探针**（期望：绿必须来自 config.toml 真实段；实际：符合）
   `PYTHONPATH=src uv run python -c ...` 取 `build_app_config().phases['execution']`，手工删掉 5 行规则段后用 `PromptConfig(phases={"execution": stripped})` 渲染 → `STRIPPED has header: False`；真实渲染 `REAL has header: True`、`REAL pos ok: True`。
2. **Python 兜底探针**（期望：兜底无规则段；实际：证实）裸 `PromptConfig()` 渲染 → `FALLBACK has header: False` / `FALLBACK has Execution rules: True`。这既证明兜底确实缺规则段，也说明 A4 的分叉真实存在（只是正常配置不可达）。
3. **位置断言抗性探针**（期望：规则段被挪到模板尾部必须红；实际：正确红）
   构造 `Issue body: … Execution rules: … PRD map check …` 模板调 `_assert_map_check_contract` → `AssertionError: moved-to-end 的 PRD map check 段位置不符合约定`。
4. **剥离 helper 边界探针**（期望：找出边界 bug）
   同名标题出现两次 → 残留 header=0、Execution rules 保留；规则段在 EOF 且无 `Execution rules:` → 其后行被吞（仅测试 helper 的理论边界）。
5. **生效配置路径探针**（期望：确认不是 `~/.iar/config.toml` 或其他仓）`resolve_config_toml_path()` → `/Users/zata/code/keda-worktrees/feat/prd-regrounding-touch-map-avoidance/config.toml`，`is_worktree: True`（且 `IAR_CONFIG` 未劫持）。
6. **范围探针（扩大版）**（期望：找残留专有名词）`rg "regrounding|touch-map" --glob '!tasks/**' --glob '!site/**'` → 命中 `roadmap.md:85`、`roadmap.md:326`（在 PRD oracle scope 之外，evidence-report 漂移 #6 已披露）。

## 发现与处置建议

| # | 发现 | 严重度 | 建议处置 |
|---|---|---|---|
| 1 | 改动集当时是 3 staged 文件 + **1 个未 staged 的 PRD 修改**（`tasks/pending/P1-...md`，`git diff --stat` +12/-3）+ 未跟踪 `tasks/evidence/.../`。若只提交 staged 文件，PRD Change Log 与 evidence `.md` 会漏提交，PRD 自身"证据见 evidence-report.md"将悬空 | 建议 | 提交前 `git add` 显式纳入 PRD 与 evidence 目录下的 `.md`（`.txt` local-only）。这正是"partial commit 会被 check-test-flag 误报"的场景，务必一次性 add |
| 2 | Python 兜底模板 `_DEFAULT_EXECUTION_TEMPLATE`（`agent_runner_feedback.py:86-120`）不含规则段，与 config.toml 语义分叉；正常配置不可达，但"另一入口构造裸 PromptConfig"或全局 config.toml 缺失 execution 时会静默失去规则 | 口径 | 可接受（PRD 明确零 src 改动）。已在 PRD Change Log 与 evidence-report 漂移 #1 登记，保留登记、不再扩大改动 |
| 3 | rv-1 evidence 渲染里的 PRD 文本与测试 fixture `_PRD_TEXT`（`test_...py:57-68`）不同（evidence 只有 `# PRD: Demo` + `## 9. Acceptance Checklist` + `- [ ] item`），说明 `rv-1-rendered-execution-prompt.txt` 是独立脚本渲染而非用例 stdout | 口径 | 证据仍属真实 config.toml 渲染，可接受；建议 evidence-report 注明"该渲染由独立脚本产出、与用例 fixture 不同"以免回溯者误判 |
| 4 | 交付礼仪未完成：PRD §9.1 呈递物列仍写通用路径未回填实际文件名；§9.2 勾选项当时仍全部 `- [ ]`；顶部横幅仍 `⬜ 未开工`；"完成回复原样带上呈递表内容"代理无法验证 | 建议（交付前） | 交付/归档前按 PRD 流程回填与勾选；本条不阻塞本 PR |
| 5 | `evidence-report.md:57` 引"PRD §14 Change Log"，但 PRD 无 §14（Change Log 是无编号 `##` 章节） | 口径 | 改为"PRD Change Log" |
| 6 | 根目录 `roadmap.md:85`、`:326` 仍以"待交付"口吻描述 re-grounding/`iar:touch-map`；在 PRD oracle 指定 scope 外，故验收照字面通过但仓库仍有陈旧措辞 | 口径 | 已在 evidence-report 漂移 #6 登记为范围外跟进项；除非决定夹带，否则保持不动 |
| 7 | 测试文件 `import pytest` 置于 first-party import 之后（`test_...py:42`）；`pyproject.toml:132-134` 的 `[tool.ruff]` 只设 target-version/line-length、无 isort select，故 lint 不会拦（rv-3 lint 证据 Passed） | 口径 | 非本次缺陷；AGENTS.md 声称的 D100-D107 规则在本仓库 pyproject 未配置属既有口径差异，不建议在本 PR 处理 |
| 8 | **审查期间工作树被并发修改**：首轮 `git status` 仅 3 个 staged 文件；PRD mtime 18:06:23、`evidence-report.md` mtime 18:06:07（首轮 `ls` 时尚不存在）。复核是针对不同时间点的快照 | 口径 | 最终提交前以最新一次 `git status`/`git diff --name-only HEAD` 为准重核范围；本次结论对应的代码/配置内容在审查期间未再变化（config.toml 17:58、测试 18:03） |

### 执行者对发现的处置（交付时回填）

| # | 处置 | 复核方式 |
|---|---|---|
| 1 | 提交前统一 `git add`，把 PRD（含新 Change Log）与 evidence 两份 `.md` 一并纳入同一次 commit | 提交后 `git show --stat HEAD` 逐一核对文件清单 |
| 2 | 保留登记，不改 `src/` | PRD Change Log + evidence-report 漂移 #1 |
| 3 | evidence-report「呈递物内联」节补充口径说明 | 见 evidence-report 该节首段 |
| 4 | 已回填 §9.1 呈递物路径、勾选 §9.2、更新顶部横幅；完成回复原样携带 §9.1 呈递表 | PRD §9 与完成消息 |
| 5 | 已改为"PRD Change Log" | evidence-report 漂移表 #1 |
| 6 | 保持不动，登记为范围外跟进项 | evidence-report 漂移 #6 |
| 7 | 已把 `import pytest` 移到 stdlib 组（`test_...py:10`），与 `test_roadmap_advance.py` 的既有布局一致 | 重跑 `-k map_check` |
| 8 | 提交前重核 `git status` / `git diff --name-only HEAD` | 见处置 #1 |

## 代理明确无法验证的部分

1. **rv-2 真实 `iar run` 收尾声明行**：未跑（无沙箱仓/gh 登录态/agent CLI 条件，且属共享状态变更）。只能验证"跳过在 PRD 允许范围内且理由成立"，不能验证该行在真实执行中会出现或格式正确。
2. **`just test all` 与 `just lint --repo` 本身**：受只读约束未重跑，仅审阅了 `rv-3-*.txt` 文本；只能确认文本内部一致（2252 passed / EXIT=0、18 个 hook Passed/Skipped），不能独立复现。
3. **基线 2249 的测试数**：需要 checkout 基线，禁止；无法核对"本分支 +3"的基数。
4. **提交/PR 的最终内容**：审查时尚未提交，无法确认哪些文件会真正入库（尤其发现 1 的 PRD 与 evidence `.md`）、也无法确认完成回复是否原样带上 §9.1 呈递表内容。
5. **agent 遵守率**：提示指令性质，PRD §12 自认为不可保证，在任何环境下都无法验证。
6. **`roadmap.md` 残留是否可接受**：属产品/范围判断，应由人决定。
7. **`~/.iar/config.toml` 对已安装 `iar` CLI 的影响**：只能确认在 worktree cwd 下解析到 worktree config.toml（测试路径正确）；从其他 cwd 调用已安装 CLI 的全局配置是否含该段，不在本 PR 交付物内，未验证。

## 代理实际执行过的命令与输出摘要

1. `git status --short`（首轮）→ `M  config.toml` / `M  docs/guides/agent-runner.md` / `M  tests/test_agent_runner_prompt_contract.py` / `?? tasks/evidence/...`；`git log --oneline -3` → HEAD `4717191`。
2. `git diff HEAD -- config.toml` → 在 `Issue body:` 与 `Execution rules:` 之间新增 4 条规则 + 空行；变量注释区新增 2 行。
3. `git diff HEAD -- docs/guides/agent-runner.md` → 示例模板块 +6 行；新增「PRD map check」小节 +14 行。
4. `git diff HEAD -- tests/test_agent_runner_prompt_contract.py` → 新增 helper×2 + 用例×3（无删除行）。
5. `uv run pytest -p no:cacheprovider -o addopts="" tests/test_agent_runner_prompt_contract.py -k map_check -v` → `3 passed, 7 deselected in 0.09s`。
6. `uv run pytest -p no:cacheprovider -o addopts="" tests/test_agent_runner_prompt_contract.py tests/test_agent_runner_prompt.py -q` → `37 passed in 0.09s`。
7. `git diff -U0 HEAD -- config.toml | rg '^\+.*\{[a-z_]+\}'` → 无输出（exit=1）。
8. `rg -n "PRD map check" -A4 config.toml | rg '\{|\}'` → 无输出（exit=1）。
9. `git diff --name-only HEAD -- src/` → 无输出。
10. `rg -n "regrounding|touch-map" config.toml src/backend/ tests/ docs/` → 无输出（exit=1）。
11. 独立探针 `PYTHONPATH=src uv run python -c ...` → `phases keys: ['execution']` / `execution tpl has header: True` / `REAL pos ok: True` / `STRIPPED has header: False` / `FALLBACK has header: False` / `FALLBACK has Execution rules: True`。
12. `resolve_config_toml_path()` → `.../keda-worktrees/feat/prd-regrounding-touch-map-avoidance/config.toml`；`is_worktree: True`。
13. `git show 4717191:config.toml | rg 'PRD map check'` → 无输出（exit=1）；基线测试文件 `rg 'map_check'` 同样 exit=1。
14. 边界探针 → `twice ->Execution rules present: True | headers left: 0`；`eof tail -> Issue body kept: True`；`moved-to-end correctly red: … 位置不符合约定`。
15. `rg "EXIT=" rv-3-test-all.txt` → `2269:EXIT=0`；`rg -c "FAILED|ERROR"` 无命中；`tail -25` → `2252 passed in 98.66s` + `EXIT=0`；`head -15` → `### just test all (第 2 次，lint 自动格式化已落盘)`。
16. `rg -n "regrounding|touch-map" --glob '!tasks/**' --glob '!site/**' .` → `roadmap.md:85`、`roadmap.md:326`。
17. `rg -n "evidence_dir" config.toml` → `396:evidence_dir = ".iar/evidence"`（印证漂移 #3 的 legacy 值）。
18. `rg -c "PRD map check" docs/guides/agent-runner.md` → `5`。
19. `git diff --stat`（18:06 后）→ `tasks/pending/P1-FEAT-...md | 15 +++++++++++---`（发现 1）；`git diff` 显示该 PRD 新增一条 Change Log（含 `mock_boundary`/`negative_control` 修正）与 §7.6 两处改写；`rg $'\x1b' <PRD>` 无命中（文件无残留 ANSI）。
20. `ls tasks/evidence/.../*.md` → `...verification-plan.md` 与 `...evidence-report.md` 均在（后者 18:06:07 才出现）。

---

## 执行者后记（非 verifier 原文）

- 本报告为 verifier 回报的原文，除「发现与处置建议」表后新增的**处置回填表**与本节外未作改动。
- 复核期间工作树确实在变（当时正在写 PRD Change Log 与 evidence 报告），故报告中的"E3 改动集只有 3 个文件"是**当时快照**的事实陈述；最终提交前的文件清单见处置 #1 的核对结果。
- 代理为只读 Explore 代理，未写任何文件；本报告由执行者落盘。
