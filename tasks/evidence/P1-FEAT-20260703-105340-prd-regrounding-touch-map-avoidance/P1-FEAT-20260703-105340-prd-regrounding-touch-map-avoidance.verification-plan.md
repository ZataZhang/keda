# 验证计划 · 执行开工前的 PRD 引用核验（PRD map check）

PRD：`tasks/pending/P1-FEAT-20260703-105340-prd-regrounding-touch-map-avoidance.md`
分支：`feat/prd-regrounding-touch-map-avoidance`（worktree `/Users/zata/code/keda-worktrees/feat/prd-regrounding-touch-map-avoidance`）
基线：`main @ 4717191`
执行时间：2026-09-17

## 验收项 → 可执行验证映射

| 验收项 | 验证方式 | 命令 / 入口 | 证据文件 |
|---|---|---|---|
| rv-1 真实 config.toml 渲染的 execution prompt 含规则段、两条相反方向约束、声明格式与正确位置 | integration：`build_app_config()` 从仓库根 `config.toml` 装配 `PromptConfig`，走真实 `build_prompt` | `uv run pytest -o addopts="" tests/test_agent_runner_prompt_contract.py -k map_check` | `rv-1-map-check-contract-tests.txt`、`rv-1-rendered-execution-prompt.txt`（local-only 原始渲染全文） |
| rv-1 未关联 PRD 的 Issue 渲染同样命中规则段 | 同上（第二条用例，Issue body 无 `PRD path:` 锚点） | 同上 | 同上 |
| rv-1 负控（删段必须转红） | 从真实模板剥离 `PRD map check (before coding):` 段后渲染，断言同一核验函数抛 `AssertionError`；同时断言「Issue body:」「Execution rules:」仍在（排除渲染本身损坏） | 同上（`..._map_check_negative_control`） | 同上 |
| rv-2 真实 `iar run` 的收尾总结含 `PRD map check:` 行 | real-entry：沙箱仓 + 本地 agent CLI + `gh` 登录态执行一次 Issue | `uv run iar run --issue <编号>` | 本轮**跳过**，理由见下「rv-2 跳过说明」 |
| rv-3 既有 prompt 契约/渲染测试全绿 | regression：prompt 契约与渲染两个文件 + 全量套件 | `uv run pytest -o addopts="" tests/`、`just test all` | `rv-3-test-all.txt` |
| rv-3 文档记录规则与声明格式 | 真实文件搜索断言 | `rg -n "PRD map check" docs/guides/agent-runner.md` | `rv-3-docs-and-placeholder-closure.txt` |
| rv-3 占位符闭集不变、无新增模板变量 | config.toml diff 上只允许新增固定文本行，不得出现 `{var}` | `git diff -U0 HEAD -- config.toml \| rg '^\+.*\{[a-z_]+\}'`（零命中） | 同上 |
| rv-3 改动集不含 Python 生产代码 | diff 文件清单 | `git diff --name-only HEAD -- src/`（零命中） | 同上 |
| rv-3 仓库级门禁（reuse / mkdocs strict / 全量 lint） | 仓库门禁链 | `just lint --repo` | `rv-3-lint-repo.txt` |
| rv-3 原版机制专有名词不得引入 | 全仓搜索零命中 | `rg -n "regrounding\|touch-map" config.toml src/backend/ tests/ docs/` | `rv-3-docs-and-placeholder-closure.txt` |

## rv-2 跳过说明

`rv-2`（真实 `iar run` 收尾总结含声明行）在 PRD §7.6 中已是 `required_for_acceptance: false` 的 opt-in 项，PRD 明确"无条件环境显式记录跳过理由并以 rv-1 全绿替代"。本轮跳过理由：

1. 真实执行需要**新建一个 GitHub Issue** 并驱动本地 agent CLI 完成整轮编码/提交/发布，属共享状态变更，不在本次"实现 + 提交 + 开 PR"的授权范围内；
2. 该 oracle 能证明的只是"agent 有可能写出这一行"，PRD §12 已明确"rv-2 真实验证仅证明'会发生'，不承诺'必然发生'"——它对"规则文本是否正确进入默认提示"没有额外判别力，而后者恰是本改动的唯一可交付行为，由 rv-1 以真实 `config.toml` 渲染直接覆盖；
3. 因此以 rv-1 全绿替代，并在本文件与本 PRD §9.2 留痕。

## Mock 边界

- **真实**：模板来自仓库根 `config.toml`（经 `build_app_config()` → 真实 `AgentRunnerPromptSettings` 装配），渲染走真实 `build_prompt`；`{prd_line}` 走真实 `_build_prd_context_block`（worktree 上真实落一份 PRD 文件供其解析）。
- **打桩**：仅 Issue 对象与 worktree 根用测试 fixture（`tmp_path`）；不 mock 模板、不 mock 渲染函数、不 mock 配置加载。
- 特别注意：裸 `PromptConfig()` 的 `phases` 是空字典，会**静默回退到代码内置模板 `_DEFAULT_EXECUTION_TEMPLATE`**——那样测到的是 Python 兜底文本而非 `config.toml`。新用例显式用 `build_app_config().prompts` 规避这一陷阱（见「对抗自检」）。

## 负控设计

| Oracle | 负控 | 期望差异 |
|---|---|---|
| rv-1 | 用真实模板，但在渲染前剥离 `PRD map check (before coding):` 段 | 核验函数抛 `AssertionError`（绿 → 红）；同时「Issue body:」与「Execution rules:」仍在，排除"渲染本身坏了"这一伪因 |
| rv-1 执行前基线 | 基线 `main` 上 `config.toml` 不含该段（`git show 4717191:config.toml`） | 同一断言在基线树上必然红——这是本次改动的 red→green 起点 |

## 对抗自检（实施期执行）

- **模板来源陷阱**：先用 `build_app_config().prompts.phases["execution"]` 直接断言规则段在场，确认走的是 `config.toml` 而非代码兜底；负控再证"绿必须来自模板里的段"。
- **断言方向双向**：核验函数同时断言 `follow the current code and adapt the plan`（已变更按当前代码）与 `implement it as specified`（计划新增按计划实施）两条**相反方向**约束，防止只写一条而漏掉另一个失败方向。
- **位置断言**：用 `prompt.index()` 断言 `Issue body:` < 规则段 < `Execution rules:`，防止规则被塞进模板尾部而失去"开工前"语义。
- **既有契约未被稀释**：原有 4 类 prompt 契约用例（execution/recovery/continuation/closeout）保持不动并全绿；新用例不调用 `_assert_prompt_contract`——真实 `config.toml` 的 `validation.evidence_dir` 是 legacy 的 `.iar/evidence`，那组断言预设的是代码默认值 `tasks/evidence`，混用会得出错误结论（实施期已实测撞到并修正）。
- **无第二份渲染源**：用 §7.4 第 5 条探针确认 `repository_local.py` / `.iar.toml` 不含 execution 默认模板；代码内 `_DEFAULT_EXECUTION_TEMPLATE` 只在 `phases` 缺 `execution` 键时兜底，而仓库根 `config.toml` 恒定义该键，故生产渲染源唯一为 `config.toml`（见 evidence report 的「实现期漂移与披露」）。

## 不在本验证计划内

- 不验证 agent 的遵守率（提示指令，非门禁；PRD §12 已登记为风险与后续跟进项）。
- 不验证并行调度避让（本 PRD 已整体移除该机制）。
- 不重跑前端构建与 e2e（改动不触碰 `frontend-*` 与浏览器行为，`mkdocs build --strict` 已覆盖文档构建）。
