# 证据报告 · 执行开工前的 PRD 引用核验（PRD map check）

PRD：`tasks/pending/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance.md`
分支：`feat/prd-regrounding-touch-map-avoidance`（worktree `/Users/zata/code/keda-worktrees/feat/prd-regrounding-touch-map-avoidance`）
基线：`main @ 4717191`
证据采集日期：2026-09-17

## 人审导航 / Human Review Navigation

> 原始证据（渲染全文 txt）按仓库约定**不进 git**（`tasks/evidence/**` 白名单只提交 `.md`），
> 因此下表中的 txt 呈递物是**本机可见、GitHub 不可见**的 local-only 文件；请在本机 worktree 内打开。
> 渲染 prompt 的**全文已内联在本报告末尾**（见「呈递物内联」），GitHub 上也能看到。

| 要看的结果 | 呈递物（本机绝对路径） | 打开命令 | 逐项期望值 | 执行者已核对 |
|---|---|---|---|---|
| 真实 `config.toml` 渲染出的 execution prompt 里新段的位置与措辞（rv-1，对应 §2 决策一） | `.../tasks/evidence/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance/rv-1-rendered-execution-prompt.txt`（local-only；全文另见本报告末节） | `cat tasks/evidence/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance/rv-1-rendered-execution-prompt.txt` | WITH-PRD 与 WITHOUT-PRD 两段渲染都在 `Issue body:` 之后、`Execution rules:` 之前含 `PRD map check (before coding):` 段；段内同时有「已变更 → `follow the current code and adapt the plan`」与「计划新增 → `implement it as specified`」两条**相反方向**约束；声明格式 `PRD map check: none` 可一眼认出 | 已核对：两段渲染各自逐行比对过；位置断言由 `prompt.index()` 用例机器保证（`rv-1-map-check-contract-tests.txt`） |
| 核验契约用例（含负控）机器结论（rv-1） | `.../rv-1-map-check-contract-tests.txt`（local-only） | `cat tasks/evidence/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance/rv-1-map-check-contract-tests.txt` | 3 passed / EXIT=0；其中负控用例在"剥离规则段"的模板上断言核验函数抛 `AssertionError` 且模板其余结构完好 | 已核对：见该文件末行 |
| 基线无该段（red→green 起点） | `.../rv-1-baseline-negative-control.txt`（local-only） | `cat .../rv-1-baseline-negative-control.txt` | `git show 4717191:config.toml \| rg 'PRD map check'` 零命中、基线测试文件里无 `map_check` 用例 | 已核对 |
| 文档已记录规则 / 声明格式 / 非门禁语义（rv-3） | `.../rv-3-docs-and-placeholder-closure.txt`（local-only） | `cat .../rv-3-docs-and-placeholder-closure.txt` | `rg -n "PRD map check" docs/guides/agent-runner.md` 命中 5 处（示例模板 2 处 + 新增小节 3 处）；`rg -n "regrounding\|touch-map" config.toml src/backend/ tests/ docs/` 零命中；`git diff --name-only HEAD -- src/` 零命中 | 已核对：三段命令与退出码都在文件中 |
| 仓库级门禁（rv-3） | `.../rv-3-lint-repo.txt`、`.../rv-3-test-all.txt`（local-only） | `tail -25 .../rv-3-lint-repo.txt`；`tail -5 .../rv-3-test-all.txt` | `just lint --repo`（含 full lint / reuse / `just test` / `mkdocs build --strict`）EXIT=0；`just test all`（`--no-testmon`）**2252 passed**、EXIT=0 | 已核对：两文件末行分别为 `EXIT=0` 与 `2252 passed` |

**执行者已替人完成的核对**：rv-1 的三条断言与 rv-3 的文档/范围/门禁断言全为自动化可复现 oracle，原始输出位置见上表；
人读只需看上表前两行与「呈递物内联」一节。

## Oracle 结果

| Oracle | 结论 | 证据文件 | 关键值 |
|---|---|---|---|
| rv-1 真实 config.toml 渲染含规则段 / 两条相反约束 / 声明格式 / 正确位置 | PASS | `rv-1-map-check-contract-tests.txt`、`rv-1-rendered-execution-prompt.txt` | `3 passed, 7 deselected in 0.31s`、EXIT=0；两段渲染均含规则段且位置正确 |
| rv-1 未关联 PRD 的 Issue 渲染同样命中规则段 | PASS | 同上 | `test_prompt_contract_execution_prompt_map_check_without_prd` PASSED |
| rv-1 负控（删段必须转红） | 期望红 ✅ | 同上 | `..._map_check_negative_control` PASSED：剥离后核验函数抛 `AssertionError`；且剥离后 `Issue body:` / `Execution rules:` 仍在 |
| rv-1 基线负控（实现前必然红） | 期望红 ✅ | `rv-1-baseline-negative-control.txt` | 基线 `config.toml` 与基线测试文件对上 `PRD map check` / `map_check` 均零命中（`rg` exit=1） |
| rv-2 真实 `iar run` 收尾总结含 `PRD map check:` 行 | **SKIP（opt-in，无条件环境）** | 见「rv-2 跳过说明」 | PRD §7.6 已标 `required_for_acceptance: false`；以 rv-1 全绿替代 |
| rv-3 全量回归 | PASS | `rv-3-test-all.txt`、`rv-3-test-all-final.txt` | `just test all`（`--no-testmon`）**2252 passed in 98.66s**、EXIT=0（基线 2249，本分支 +3）；独立 verifier 复核后仅动了测试文件的 import 分组与文档，**终态树**（含 PRD 归档与证据 `.md` 入库）复跑 `rv-3-test-all-final.txt`：**2252 passed in 80.56s**、EXIT=0 |
| rv-3 仓库级门禁 | PASS | `rv-3-lint-repo.txt` | `just lint --repo` EXIT=0；18 个 hook 全 Passed/Skipped、`mkdocs build --strict` 成功（Documentation built in 5.97 seconds） |
| rv-3 文档对齐 | PASS | `rv-3-docs-and-placeholder-closure.txt` | `docs/guides/agent-runner.md` 命中 5 处 |
| rv-3 占位符闭集不变 / 无新增模板变量 | PASS | 同上 | `git diff -U0 HEAD -- config.toml \| rg '^\+.*\{[a-z_]+\}'` 零命中（rg exit=1） |
| rv-3 零 Python 生产代码改动 | PASS | 同上、`rv-3-change-set-diff.txt` | `git diff --name-only HEAD -- src/` 零命中；改动集仅 3 个文件 `config.toml` / `docs/guides/agent-runner.md` / `tests/test_agent_runner_prompt_contract.py`（+149 -0） |
| rv-3 原版机制专有名词不得引入 | PASS | `rv-3-docs-and-placeholder-closure.txt` | `rg -n "regrounding\|touch-map" config.toml src/backend/ tests/ docs/` 零命中（rg exit=1） |

## rv-2 跳过说明

`rv-2` 在 PRD §7.6 中是 `required_for_acceptance: false` 的 opt-in 项，PRD 文本明确"无条件环境显式记录跳过理由并以 rv-1 全绿替代"（§9.2 Validation Acceptance 同款措辞）。跳过理由：

1. 真实执行需要**新建 GitHub Issue** 并驱动本地 agent CLI 跑完整轮编码/提交/发布，属共享状态变更，超出本次"实现 + 提交 + 开 PR"的授权范围；
2. 该 oracle 只能证明"agent 有可能写出这一行"，PRD §12 已自认"rv-2 真实验证仅证明'会发生'，不承诺'必然发生'"——对本改动的唯一可交付行为（规则文本是否真的进了默认提示）没有额外判别力；
3. 该行为由 rv-1 以真实 `config.toml` 渲染 + 负控直接覆盖，判别力更强。

已在上表与本节留痕；PRD §9.2 对应项将按此勾选并写明"以 rv-1 替代"。

## 实现期漂移与披露

实施前按 PRD §7.4 Executor Drift Guard 逐条探针，发现并处置如下：

| # | 漂移 / 发现 | 影响 | 处置 |
|---|---|---|---|
| 1 | `src/backend/core/use_cases/agent_runner_feedback.py` 内有第二份 execution 模板 `_DEFAULT_EXECUTION_TEMPLATE`（`build_prompt` 的 `phases.get(phase, ...)` 兜底，内含 `{memory_block}` 等，与 `config.toml` 版本不同） | **非生产渲染源**：`AgentRunnerPromptSettings.phases` 默认是空 dict，但仓库根 `config.toml` 恒定义 `execution` 键，运行时经 `build_app_config_from_settings` 装配后必有该键，兜底不触发；另外 `factory_config_merge.py` 的 `_merge_prompt_config` 对 `phases` 做**逐 phase** `update`，所以 `.iar.toml` 只覆盖别的 phase 时 `execution` 仍取全局 `config.toml`（含规则段），覆盖了 `execution` 才整体接管 | 按 PRD"零 `src/` 改动"约束**不改**该处；在 PRD Change Log 与本节登记为已知事实 |
| 2 | `tests/test_agent_runner_prompt_contract.py` 原有用例用裸 `PromptConfig()` → `phases={}` → **实际渲染的是 Python 兜底模板**，并非 `config.toml` | PRD rv-1 声称"模板来自真实 config.toml"，若照抄原有写法，新用例会测到兜底模板而**假绿**（兜底模板没有规则段，会让绿色无法成立，但断言若写松就会失效） | 新用例显式改为 `build_app_config().prompts`，让 rv-1 的口径成立；原有用例保持不动（其断言针对的是兜底模板语境，改动它会越界） |
| 3 | 仓库根 `config.toml` 的 `validation.evidence_dir = ".iar/evidence"`（legacy），真实渲染出的 prompt 因此含 `.iar/evidence` 字面量 | 新用例若顺带调用 `_assert_prompt_contract`（内含 `".iar/evidence" not in prompt`）会**误红**；实施期实测撞到并修正 | 新用例只调用 `_assert_map_check_contract`，不复用那组预设代码默认值的断言；既有 4 类契约用例不受影响 |
| 4 | 新用例初版被 ruff-format 重排，导致 `just test all` 首轮 `EXIT=1`（lint hook 改文件） | 仅流程性失败，非回归 | 采纳格式化后重跑；`rv-3-test-all.txt` 为**重跑后**的结果（文件头已注明"第 2 次"） |
| 5 | 新用例注释里写了 PRD slug（含 `regrounding-touch-map`），触发 PRD §9.2 的"专有名词零命中"断言 | 会被自己的验收门禁判红 | 注释改为 PRD 标题文字，断言恢复零命中（见 `rv-3-docs-and-placeholder-closure.txt`） |
| 6 | `roadmap.md`（仓库根，非 `docs/`）第 85/125/134/226/249/306/326 行仍把 re-grounding + 触碰面避让描述为待交付能力 | 本次改动不动它，属**已知的文档陈旧**（该文件整体停留在 2026-07-05 状态） | 明确**不在本 PRD 范围内**（PRD 只授权 `config.toml` / 测试 / `docs/guides/agent-runner.md`，且 roadmap 的 M8/M11 里程碑重写属另一议题）；登记为跟进项，不夹带进本 PR |

## 变更清单（与 PRD §7.2 对照）

| PRD §7.2 条目 | 实际落地 | 与 PRD 一致 |
|---|---|---|
| `config.toml` 插入 `PRD map check (before coding):` 段（4 条规则），位于 `Issue body:` 后、`Execution rules:` 前 | 一致（`config.toml:472-477`） | ✅ |
| `config.toml` 模板变量注释区说明该段无对应占位符 | 一致（`config.toml:455-456`，PRD 标注"可选、不强制"） | ✅ |
| `tests/test_agent_runner_prompt_contract.py` 新增渲染断言 + 无 PRD 渲染断言 + 负控 | 一致（3 个 `map_check` 用例，`-k map_check` 可选中） | ✅ |
| `tests/test_agent_runner_prompt.py` "如既有占位符/渲染用例对模板文本有全量断言则同步；否则不改" | 不改：核查确认该文件没有任何针对 config.toml 模板文本的全量断言，且它渲染的是代码兜底模板（未改动） | ✅（PRD 允许） |
| `docs/guides/agent-runner.md` 记录规则 / 声明格式 / 非门禁语义 | 一致（新增「PRD map check」小节；另同步了上方的示例 `execution` 模板块） | ✅ |

无未预期的额外改动；`git diff --name-only HEAD` 确认为 3 个文件。

## Mock 边界与负控

- **真实**：模板文本来自仓库根 `config.toml`（经真实 `build_app_config()` → `AgentRunnerPromptSettings` 装配为 `PromptConfig`），渲染走真实 `build_prompt`，`{prd_line}` 走真实 `_build_prd_context_block`（worktree 上真实落一份 PRD 文件供其解析）。
- **打桩**：仅 Issue 对象与 worktree 根用测试 fixture（`tmp_path`）。**不 mock 模板、不 mock 渲染函数、不 mock 配置加载**。
- **负控**：
  - 运行内负控（自动化）：用真实模板剥离规则段后渲染 → 核验函数必须抛 `AssertionError`；同时断言 `Issue body:` / `Execution rules:` 仍在，排除"渲染本身坏了"这一伪因。
  - 基线负控：`main @ 4717191` 的 `config.toml` 与测试文件都无该段/该用例（`rg` exit=1），证明这是真实的 red→green 而非"断言本来就绿"。

## 呈递物内联

以下为 `rv-1-rendered-execution-prompt.txt` 全文（真实 `config.toml` 渲染，两段场景）。

口径说明：该渲染由**独立脚本**（`build_app_config()` + `build_prompt()`）产出，**不是** pytest 用例的 stdout，因此其中的 PRD 正文是脚本自带的极简 fixture（`# PRD: Demo` + 一条 `- [ ] item`），与 `tests/test_agent_runner_prompt_contract.py` 里的 `_PRD_TEXT` 不同——两者都只是喂给 `{prd_line}` 的输入，不影响"规则段是否来自 `config.toml`"这一结论。真正的机器断言在 `rv-1-map-check-contract-tests.txt`。

```text
===== WITH-PRD =====
Complete GitHub Issue #42: Demo

Issue URL: https://github.com/example/repo/issues/42
Worktree: /var/folders/n8/x_smwvn16b3b30f7wnl4s2kh0000gn/T/tmpcyk_hg2j
The canonical PRD is inlined below from `tasks/pending/P1-FEAT-20990101-000000-demo.md`. The PRD may evolve during implementation, but Change Log and Acceptance Checklist are separate: when changing the PRD, append a `## Change Log` entry, and only mark an Acceptance Checklist item after its stated behavior was actually executed and evidenced. Never weaken a user-visible, security, scope, or realistic validation requirement without recording the change and its review status. PRD format conventions (Change Log entry structure, Acceptance Checklist syntax, rv-id evidence naming, evidence directory layout) are defined by the prd skill's Machine Contract v1 — read and follow the prd skill. Archiving the PRD is the runner's job alone: never `git mv` it into `tasks/archive/` yourself, and once the runner has archived it, never move it back to `tasks/pending/` — the pre-push gate requires it to stay archived. Canonical PRD: `tasks/pending/P1-FEAT-20990101-000000-demo.md`.

--- BEGIN PRD ---
# PRD: Demo

## 9. Acceptance Checklist

- [ ] item
--- END PRD ---


Issue body:
## Summary

Tracked task.

- PRD path: `tasks/pending/P1-FEAT-20990101-000000-demo.md`


PRD map check (before coding):
- The PRD predates this run; verify the file paths, symbols, and config keys it references still exist in the current worktree.
- When a referenced path or interface has changed, follow the current code and adapt the plan; do not recreate structures the PRD describes that no longer exist.
- When the PRD plans work that is not built yet (e.g. a file it asks you to create), implement it as specified.
- In your final summary, include one line starting with `PRD map check:` listing stale references and how you adapted; write `PRD map check: none` when nothing is stale (an Issue without a PRD is trivially none).

Execution rules:
- Read AGENTS.md and follow repository instructions.
- Only modify files inside the current worktree.
- Do not merge main, delete branches, push, or create PRs; the runner handles publishing.
- Do not run `git add` or `git commit`; the runner exposes a restricted commit proxy.
- After finishing your changes, request a commit by writing `.agent-runner/commit-request.json` as JSON with `commit_message`.
- Do not touch production systems or real business data.
- Implement the requested task with focused tests and docs updates.
- Finish with a concise summary, tests run, and remaining risk.

===== WITHOUT-PRD =====
Complete GitHub Issue #42: Demo

Issue URL: https://github.com/example/repo/issues/42
Worktree: /var/folders/n8/x_smwvn16b3b30f7wnl4s2kh0000gn/T/tmpcyk_hg2j
If the Issue references a PRD, read it before editing.


Issue body:
## Summary

No canonical PRD is attached here.


PRD map check (before coding):
- The PRD predates this run; verify the file paths, symbols, and config keys it references still exist in the current worktree.
- When a referenced path or interface has changed, follow the current code and adapt the plan; do not recreate structures the PRD describes that no longer exist.
- When the PRD plans work that is not built yet (e.g. a file it asks you to create), implement it as specified.
- In your final summary, include one line starting with `PRD map check:` listing stale references and how you adapted; write `PRD map check: none` when nothing is stale (an Issue without a PRD is trivially none).

Execution rules:
- Read AGENTS.md and follow repository instructions.
- Only modify files inside the current worktree.
- Do not merge main, delete branches, push, or create PRs; the runner handles publishing.
- Do not run `git add` or `git commit`; the runner exposes a restricted commit proxy.
- After finishing your changes, request a commit by writing `.agent-runner/commit-request.json` as JSON with `commit_message`.
- Do not touch production systems or real business data.
- Implement the requested task with focused tests and docs updates.
- Finish with a concise summary, tests run, and remaining risk.
```
