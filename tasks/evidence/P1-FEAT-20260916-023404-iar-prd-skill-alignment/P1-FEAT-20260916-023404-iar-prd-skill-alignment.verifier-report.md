# Verifier Report — P1-FEAT-20260916-023404-iar-prd-skill-alignment

- **结论**：**PASS**
- **审查人**：独立 verifier Agent（未参与实施，仅依据自行核实的文件内容、代码 diff 与命令输出）
- **审查日期**：2026-09-16
- **审查对象**：`tasks/pending/P1-FEAT-20260916-023404-iar-prd-skill-alignment.md`（§7.6 oracle / §9 验收清单）、keda 工作区未提交改动、模板仓库 `~/code/zata_code_template` commit `453ac6f`

## 复跑结果（verifier 亲自执行）

| 复跑项 | 命令 | 结果 |
|---|---|---|
| rv-2/3/4 目标测试 | `uv run pytest tests/ -k "prompt_contract or evidence_commit_semantics or skill_preflight" -q --no-testmon` | **17 passed**, exit=0 |
| rv-6 契约同步脚本 | `bash tasks/evidence/.../scripts/capture_rv-6-contract-sync.sh` | **PASS**（版本相等、副本逐字节一致、六项约定关键词全 OK） |
| rv-5 全量回归 | `uv run pytest tests/ -x -q --no-testmon` | **2112 passed in 71.77s**, exit=0（与证据文件的 2112 passed 一致，收集总数 2112 交叉吻合） |

## 逐 rv-id 核对

### rv-1（init gitignore 白名单 provisioning，reviewer: human）— 成立

证据 `rv-1-init-gitignore.txt`：临时仓库从 fresh state 起步（先断言无 .gitignore）；真实 CLI `iar init --dry-run` 输出含 `tasks/evidence/**`、`!tasks/evidence/**/`、`!tasks/evidence/**/*.md` 三条与 skill 安装计划且未写文件；实写后 `.gitignore` 全文显示白名单三条位于 `# >>> iar >>>` 托管块内；第二次运行幂等无变更；skip=True 被跳过。
**限制披露**：实写步骤调用的是 init 编排的同一函数 `ensure_gitignore_entries`（脚本头部有说明），而非完整 CLI 写路径——动机是避免污染全局 registry 与联网拉 skill，属合理边界；dry-run 走的是真实 CLI 入口，且 `tests/test_agent_runner_init.py` 的 diff 显示 init 编排层测试已更新为六条目并锁定块外跳过语义。代码层核实：`repository_gitignore.py` 的 `IAR_GITIGNORE_SECTIONS` 确实新增白名单段；`cli_init.py` 的 `--force` 确实传入 `RemoteTemplateSkillInstallOptions`（dry-run 与实写两处）。

### rv-2（prompt 瘦身 golden 测试）— 成立

证据 `rv-2-prompt-contract.txt`：7 用例全绿。阅读 `tests/test_agent_runner_prompt_contract.py` 核实断言真实性：
- 教学零命中片段清单（`_TEACHING_FRAGMENTS`）覆盖六字段示例（`- Type: <scope`、`- Before:`、`- Review:`、`with Type, Before, After, Reason, Impact, and Review`、`Markdown tables are NOT parsed`）与 rv 命名/截图分工教学（`named \`rv-<item-number>-<slug>.<ext>\` (PNG`、`截图/screenshot, pdf` 等）；
- 每个 prompt 断言契约指针恰好一次、归档归属规则恰好一次、`.iar/evidence` 字面量零命中；closeout 额外断言 `[~]` 出口规则一次；validation 行断言指针不复述但指方向；单源测试断言指针文本只有一处定义。
- **负向控制在案**：临时删除 `_build_prd_closeout_instruction` 指针行后 3 用例（execution/recovery/continuation）变红的输出已记录，还原后 7 passed。
- **核查说明**：「复选框语法教学」零命中是平凡成立的——比对 HEAD 旧代码，旧 prompt 字符串中本就不存在独立的复选框语法教学块（只有解析器注释），`[~]` 语义按 PRD 决策二属 runner 私有语义且被刻意保留；复选框语法现已由 skill 契约 §2 承载。不构成缺口。
- 代码层核实：`git diff` 确认 `PRD_CHANGE_LOG_FORMAT_EXAMPLE` 常量整块删除、closeout 的引用替换为 `PRD_MACHINE_CONTRACT_POINTER`；`PRD_ARCHIVE_OWNERSHIP_RULE`、`RUNNER_OWNED_CHECKLIST_ITEM_RULE` 单源保留于 `agent_runner_feedback.py` 并被 closeout 引用；manifest 规范 `build_structured_evidence_prompt_suffix` 单源存在于 `agent_runner_structured_evidence.py:895`，其余两处为引用非复述。

### rv-3（证据提交语义集成测试）— 成立

证据 `rv-3-evidence-commit.txt`：4 用例全绿。阅读 `tests/test_agent_runner_evidence_commit_semantics.py` 核实：
- 关键值取自真实 `git ls-tree -r --name-only HEAD`（commit 树，非 stage 区），git 走真实 subprocess 无 mock；
- 白名单由真实 `ensure_gitignore_entries` provision；
- legacy 用例（显式 `evidence_dir=".iar/evidence"`）存在且锁定旧行为：info/exclude 整目录排除、连 `.md` 强制加入也被 `ensure_no_evidence_paths_in_changes` 拒绝；
- 负向控制在案：移除白名单后 `rv-1-shot.png` 确实进入 commit 树（输出中 `True`），证明测试能区分失败。
- 代码层核实：`ensure_no_evidence_paths_in_changes`（`agent_runner_validation.py:930`）白名单语义正确——`evidence_dir_uses_task_subdirs`（`models/agent_runner.py:38`，仅当值等于默认 `tasks/evidence` 时为真）决定放行 `.md`、拦截其余；legacy 分支任何证据路径都拒绝。`resolve_evidence_dir` 按 prd-stem 分目录、无 PRD 兜底 `issue-<N>` 由 resolution_rules 用例锁定。

### rv-4（预检 fail fast）— 成立

证据 `rv-4-skill-preflight.txt`：6 用例全绿。阅读 `tests/test_prd_skill_preflight.py` 核实三种失败场景均存在且断言报错含 `iar init` 指引：skill 缺失（:48）、v0 版本不符（:59，另断言 "v0" 字样）、无版本标记（:72）；放行路径（:83）与真实挂接点 `run_preflight_checks`（:96）各一。
代码层核实调用链：`agent_runner_publish.py:125` `run_preflight_checks` → `ensure_prd_machine_contract_available()`（`generated_content.py:742`，两种报错均含 `iar init` 修复指引）；`run_once`（`agent_runner_orchestration_runtime.py:420`）在领取 Issue 前调用，失败返回退出码 1；daemon 路径经 `run_agent_daemon.py:196` → `run_once`，符合行为样例「开跑前直接失败」。

### rv-5（全量回归）— 成立

证据 `rv-5-full-regression.txt` 显示 2112 passed exit=0；verifier 复跑同一命令得 **2112 passed in 71.77s exit=0**，在最终代码树上真实通过。证据中 lint/mkdocs/pre-commit 的声称未复跑，但全量 pytest（含 151 个守卫测试）已亲自验证。

### rv-6（skill 契约一致性）— 成立

证据 `rv-6-contract-sync.txt` 显示 PASS；verifier 复跑脚本得同样 PASS。另独立核实：
- `~/code/zata_code_template` commit `453ac6f` 的 `--stat` 显示 diff 仅含 `skills/prd/SKILL.md`（+80 行），符合决策三「模板仓库 diff 仅限契约相关内容」；`git status -sb` 显示本地 main 与远端 `zata/main` 无 ahead/behind，即已推送；
- 亲自阅读 SKILL.md 的 `## Machine Contract (v1)` 章节：含独立 `Machine-Contract-Version: 1` 标记行、Change Log `###`+六字段（含"Markdown tables are NOT parsed"教训）、复选框语法与 `[~]` 语义、rv-id 命名、证据目录布局与三份报告文件名及 gitignore 白名单、Delivery Dependencies 块、两条横幅标记——六项齐全；
- keda 侧 `SUPPORTED_MACHINE_CONTRACT_VERSION = 1`（`src/backend/core/shared/prd_machine_contract.py:16`）与 skill 声明相等；`~/.kimi-code/skills/prd` 与模板仓库副本逐字节一致（脚本 diff 断言）。

## §9 验收清单逐项状态

**Human-Confirmed**
- [x] 决策一：rv-3 的 `git ls-tree` 输出显示 commit 树含三份 `.md`、不含 `.png`/oracle 脚本；legacy 用例全绿（复跑确认）。
- [x] 决策二：rv-2 教学零命中 + 指针存在（复跑确认）；rv-6 六项约定齐全（复跑 + 亲自阅读 SKILL.md 确认）。
- [x] 决策三：模板仓库 `453ac6f` diff 仅 `skills/prd/SKILL.md`，已推送（`main...zata/main` 无落后）；keda 侧改动留工作区未提交（按约定提交时机由用户决定）。
- [x] 9.1 呈递区：rv-1 证据文件含临时仓库 `.gitignore` 全文与两次 init 输出（含验收消息）；自验方式（搜 `tasks/evidence` 见排除行与 `!*.md` 放行）可行。

**Architecture Acceptance**
- [x] `prd_machine_contract.py` 仅依赖标准库（亲自读全文件：只有 `re` 与 `__future__` 导入），位于 `core/shared/` 与解析器同目录，无新跨层依赖；四层方向未被破坏。

**Behavior Acceptance**
- [x] rv-1：真实 CLI dry-run + 同一编排函数实写，幂等（限制见 rv-1 节）。
- [x] rv-2：教学零命中、指针与 runner 语义各一次（复跑 7 passed）。
- [x] rv-3：`.md` 入 commit / 强制加入拦截 / legacy 兼容（复跑 4 passed）。
- [x] rv-4：缺失 / v0 / 无标记三场景 fail fast 且含 `iar init` 指引（复跑 6 passed）。

**Documentation Acceptance**
- [x] `docs/guides/agent-runner.md`：新增「`.gitignore` 托管块与 prd skill 契约」小节，证据门禁链路、配置示例（`evidence_dir = "tasks/evidence"`）、verifier 响应落盘路径、前端视觉证据段落均已改写为新约定（读 diff 确认）。
- [x] `docs/ai-standards/testing.md`：「证据留存策略」段不再区分两条流，明确「本地 just ai implement 流与 iar daemon 流使用同一约定」，与 agent-runner.md 表述一致；`docs/guides/prd-standard.md` 与 `scripts/README.md` 的 RV 脚本路径同步改写。

**Validation Acceptance**
- [x] rv-5：复跑 2112 passed exit=0。
- [x] rv-6：复跑脚本 PASS。

**Delivery Readiness**
- [x] 三份证据文件齐：verification-plan / evidence-report / verifier-report（本文件）。
- [x] 独立 verifier 逐项核对：本报告。
- [x] 9.1 呈递内容在证据目录内原样存在（完成消息是否原样携带由实施者的交付消息负责，超出本报告可核范围）。
- [x] 模板仓库已提交并推送、本机副本已同步（rv-6 复跑确认）。

## 偏离评估（PRD Change Log 已声明两处）

1. **预检挂接点在 `run_preflight_checks` 而非 execution_loop**：核实后认为**不破坏 PRD 意图**。`run_preflight_checks` 是 `run_once` 领取任何 Issue 之前的既有单点，比执行循环入口更早，恰好是行为样例「开跑前直接失败」的字面实现；daemon 每轮经 `run_agent_daemon.py:196` → `run_once` 覆盖。已知边界：`blocked-continue` 直入口不经过预检（Change Log 已披露）——该入口是恢复既有任务而非"开跑"，且 prompt 构建本身不依赖 skill 文本，风险可接受。
2. **契约指针只由 PRD 块与 Issue body 携带，validation 行只指方向**：**不破坏 rv-2 oracle**。oracle 要求「指针行与 runner 语义块各一次命中」，测试断言每个 prompt 内指针恰好一次；若 validation 行复述全文会命中两次反而违反 oracle。单源纪律由 `test_prompt_contract_pointer_single_source` 锁定。

## 其他观察（不阻塞）

- 工作区含与本 PRD 无关的未提交改动（`frontend-public` roadmap 组件等），但全量回归在其在场下 2112 passed，不影响本 PRD 的任何 oracle。
- rv-1 实写步骤走编排函数而非完整 CLI 写路径（脚本已披露动机），dry-run 为真实 CLI；human reviewer 复核时可留意。
- 证据报告遗留说明的两项（`ensure_no_misplaced_evidence_helpers` 报错文案精度、verifier 为同模型新鲜上下文）如实披露，属实。

## 最终结论

**PASS**。六条 oracle 的证据均真实支撑其声称的行为，关键复跑（rv-2/3/4 目标测试 17 passed、rv-5 全量 2112 passed、rv-6 脚本 PASS）由 verifier 亲自在最终代码树上执行通过；代码层抽查（字面量残留、版本常量、预检调用链、白名单语义、runner 私有语义单源）全部与证据一致；两处偏离均有 PRD Change Log 记录且不破坏意图。
