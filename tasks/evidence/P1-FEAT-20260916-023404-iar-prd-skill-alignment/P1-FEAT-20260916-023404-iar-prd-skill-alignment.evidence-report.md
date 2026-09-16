# Evidence Report — P1-FEAT-20260916-023404-iar-prd-skill-alignment

对应 PRD：`tasks/pending/P1-FEAT-20260916-023404-iar-prd-skill-alignment.md` §9 Acceptance Checklist。
验证计划：同目录 `*.verification-plan.md`。本文逐条解释每份证据对应哪个验收项、显示了什么、为什么能证明成立。

## rv-1 → FR-7 / §9 决策一呈递、Behavior Acceptance

- **证据**：`rv-1-init-gitignore.txt`（真实 CLI `iar init` 在 mktemp 临时仓库执行）
- **显示什么**：dry-run 计划含 tasks/evidence 白名单与 skill 安装计划；实写后临时仓库 `.gitignore` 的 iar 托管块内含 `tasks/evidence/**`、`!tasks/evidence/**/`、`!tasks/evidence/**/*.md` 三条；第二次 init 报告幂等无变更。
- **为什么成立**：走的是真实 CLI 入口（非单测 mock），覆盖"新仓库一次 init 即 provision 齐备"的行为样例；幂等重跑证明托管块机制未被破坏。

## rv-2 → FR-5 / §9 决策二、Behavior Acceptance

- **证据**：`rv-2-prompt-contract.txt`（`tests/test_agent_runner_prompt_contract.py`，7 用例）
- **显示什么**：execution/recovery/continuation/closeout 四类 prompt + validation 行 + Issue body 中，格式教学片段（六字段示例、复选框语法教学、rv 命名教学）零命中；契约指针恰好一次；runner 私有语义（归档归属、manifest 规范）仍在。负向控制：临时删指针行后 3 个用例变红，还原回绿。
- **为什么成立**：断言直接作用在真实 prompt 构建函数的输出字符串上；负向控制证明测试能区分"指针缺失"这一失败模式，不是恒绿断言。

## rv-3 → FR-1/FR-2/FR-3 / §9 决策一、Behavior Acceptance

- **证据**：`rv-3-evidence-commit.txt`（`tests/test_agent_runner_evidence_commit_semantics.py`，4 用例）
- **显示什么**：tmp_path 真实 git 仓库中，证据写入 `tasks/evidence/<prd-stem>/` 后 `git add -A` + commit，`git ls-tree` 显示 commit 树含三份 `.md`、不含 `.png`/oracle 脚本；`git add -f` 强制加入 `.png` 后 `ensure_no_evidence_paths_in_changes` 拒绝发布；显式 `evidence_dir=".iar/evidence"` 的 legacy 用例锁定旧行为（任何证据路径都拒绝）。负向控制：移除白名单后 `.png` 确实进入 commit 树。
- **为什么成立**：关键值取自真实 `git ls-tree`（跨越"证据写入 → git add -A → commit 树"完整边界），非 stage 区中间态；负向控制证明测试能区分白名单缺失的失败；legacy 用例证明决策一的兼容性承诺（显式旧配置行为逐字节不变）。

## rv-4 → FR-6 / §9 决策二、Behavior Acceptance

- **证据**：`rv-4-skill-preflight.txt`（`tests/test_prd_skill_preflight.py`，6 用例）
- **显示什么**：skill 缺失、契约版本为 v0、无版本标记三种场景均 fail fast 且报错含 `iar init` 指引；正常 skill 放行；经 `run_preflight_checks` 真实挂接点（daemon 领取 Issue 前的既有单点）验证。
- **为什么成立**：通过 `IAR_PRD_SKILL_PATH` env 覆盖触发真实预检代码路径，不 mock 预检逻辑本身。

## rv-5 → §9 Validation Acceptance（回归）

- **证据**：`rv-5-full-regression.txt`
- **显示什么**：`uv run pytest tests/ -x -q --no-testmon`（仓库全量约定，justfile `JUST_FULL_TEST_FLAGS`）在最终代码树上 **2112 passed, exit=0**；另 `just test`（617 受影响用例）、`just lint --reuse`、pre-commit 全套钩子（ruff/架构层依赖/max-file-lines/check-test-flag）、`uv run mkdocs build --strict` 全过。
- **为什么成立**：最终代码树上的全量回归，覆盖 RV 复跑缓存、串味检测、closeout、prd_lock 守卫等既有行为面；守卫测试 151 个零修改全绿。

## rv-6 → FR-4 / §9 决策二、决策三、Delivery Readiness

- **证据**：`rv-6-contract-sync.txt`（`scripts/capture_rv-6-contract-sync.sh`，正式运行 PASS）
- **显示什么**：模板仓库 `skills/prd/SKILL.md` 含 Machine Contract (v1) 章节且声明 `Machine-Contract-Version: 1`，与 keda 侧 `SUPPORTED_MACHINE_CONTRACT_VERSION = 1`（`src/backend/core/shared/prd_machine_contract.py`）相等；模板仓库与 `~/.kimi-code/skills/prd/` 副本逐字节一致；契约章节含全部六项约定关键词（Change Log 六字段、表格不算数教训、`[~]` 语义、rv-id 命名、证据目录布局、Delivery Dependencies、两条横幅标记）。
- **为什么成立**：三向一致性（模板仓库 ↔ 本机 live 副本 ↔ keda 支持版本）由脚本直接断言，非人工目测。
- **决策三补充证据**：模板仓库提交 `453ac6f feat(prd): add Machine Contract v1 section to prd skill` 已推送 GitHub（`dac9e9f..453ac6f main`），diff 仅含 `skills/prd/SKILL.md`。keda 侧改动按约定留在工作区未提交，提交时机由用户决定。

## §9 其余验收项映射

- **Architecture Acceptance**：`prd_machine_contract.py` 零 backend 导入（模块 docstring 声明架构约束，pre-commit 架构层依赖钩子通过）。
- **Documentation Acceptance**：`docs/guides/agent-runner.md`（证据门禁链路、配置示例、init 章节新增「.gitignore 托管块与 prd skill 契约」小节）、`docs/ai-standards/testing.md`（两条流合一）、`docs/guides/prd-standard.md`、`scripts/README.md` 已同步；`mkdocs build --strict` 通过。
- **实施偏离声明**（已按 living-guide 写回 PRD Change Log）：① 预检挂接点落在 `run_preflight_checks`（领取 Issue 前的既有单点）而非 PRD 建议的 execution_loop——更符合"开跑前失败"的行为样例；`blocked_continue` 直入口不经过它。② 契约指针只由 PRD 块与 Issue body 携带，validation 行只写方向指引——避免同一 prompt 命中两次违反 rv-2 oracle。

## 遗留说明

- `ensure_no_misplaced_evidence_helpers` 的报错文案仍写 `<root>/scripts/`（不带 prd-stem 子目录）；门禁判定本身正确放行子目录，仅文案精度可待后续收敛。
- verifier 独立性说明：本次 verifier 为同模型的新鲜上下文独立 agent（非不同 AI 工具），这是手工流相对 `just ai implement` 自动流的已知限制。
