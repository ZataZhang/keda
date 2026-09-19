> **Current verdict (Round 3 最终树确认, 2026-09-20): PASS with caveats。** 唯一增量（新 Playwright e2e spec）经复核为真实入口、无空转；冻结凭证 MATCH；全仓 2326 passed。遗留 1 条 minor 证据引用不准（§9.2 把三文件命令的 53 挂到单文件命令上，见 R3-4）。详见文末「## Round 3（最终树确认）」。
>
> 历史：Round 2 = PASS with caveats（R1 与三个 R3 确认整改；1 条 R3 残留 + 3 条 R4 缺口，不阻断）；Round 1 = REJECT。

# Verifier Report — P1-FEAT-20260918-110027 生命周期 Agent 矩阵

- PRD: `tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md`
- Worktree: `/Users/zata/code/keda-worktrees/lifecycle-agent-matrix`（分支 `lifecycle-agent-matrix`，HEAD `4d4dd12`）
- 角色: 独立 verifier（对抗性复核；只读源码/测试/PRD，仅写本报告）
- 报告时间: 2026-09-20

## 0. 冻结凭证

```
$ git diff HEAD -- src tests | shasum -a 256
4f306c912c2ec91e70be7874a8744cf96932e46205ba78158d5bd98a1c18a563  -
```

**结果：与执行者冻结值逐字一致（MATCH）**，复核期间未发生源码/测试漂移。

## 1. 判定

## **REJECT**

后端测试全绿、API 契约与路径安全无碍，但**§1 行为样例表（PRD 明示其为验收 oracle）第 3、4 行在真实流水线中不成立**：PRD 文件头部覆盖块只被接进 fix / closeout / verifier 三个消费点，`implementation` / `review`（以及 `supervisor` / `deliberate` / `content_generation` / `planner`）的实现阶段选人完全不读 PRD 覆盖。此为本报告唯一 R1 阻断项。

---

## 2. 已独立复跑的命令（非引用执行者报告）

| 命令 | 观察结果 |
|---|---|
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q --no-testmon` | `47 passed in 1.55s` |
| `uv run pytest tests/test_lifecycle_agent_resolution.py -q -k unregistered --no-testmon` | `2 passed, 19 deselected` |
| `git diff HEAD --stat -- alembic src/backend/infrastructure/persistence/console_store.py tests/guards` | 空（无迁移 / 无表结构 / 无守卫改动） |
| 自写探针 `/tmp/verify_lifecycle_probe.py`（TestClient + tmp `IAR_CONFIG`） | 见 §3 各条证据 |

---

## 3. Findings

### R1（阻断）— PRD 文件头部覆盖对 `implementation` / `review` / `supervisor` 阶段不生效

**文件位置**
- `src/backend/core/use_cases/run_agent_once.py:216-234`（`choose_agent`，实现阶段选人）
- `src/backend/core/use_cases/agent_review.py:564`（`run_pre_pr_review` 调 `resolve_reviewer_agent`）
- `src/backend/core/use_cases/agent_runner_publication.py:681,858`（supervisor 解析）
- `src/backend/core/use_cases/run_agent_execution_loop.py:396-400`（`prd_overrides` 唯一的解析点）

**具体失败场景**

按 §1 行为样例表第 3 行：在某 PRD 文件头部写

```
- lifecycle_agents:
  - implementation: kimi
```

由 runner 执行该 PRD 时，**实现阶段仍会用 `choose_agent` 的结果（默认 `claude`），不会用 `kimi`**。第 4 行（同时声明 `implementation` + `review`）同样两个阶段都不生效（`review` 的 PRD 覆盖被 `run_pre_pr_review` 丢在门外）。

根因：`prd_overrides` 只在 `run_agent_execution_loop.py:396` 从 worktree 里的 PRD 文本解析一次，随后**只传给 closeout（:280）、fix（:711）、verifier（:650）**。实现阶段的 `choose_agent`、审核阶段的 `run_pre_pr_review`、监督阶段的 `resolve_supervisor_agent` 都拿不到该映射：

- `choose_agent(issue, config, override_agent)` 签名里根本没有 `prd_overrides` 参数 —— 实现阶段在结构上不可能读到 PRD 覆盖。
- `resolve_reviewer_agent(..., prd_overrides=None)` / `resolve_supervisor_agent(..., prd_overrides=None)` 虽支持该参数，但全仓**没有任何调用方传入**（`rg` 见下）。

**证据**

```text
$ rg -n "resolve_reviewer_agent\(|resolve_supervisor_agent\(" src
agent_review.py:564:    reviewer_agent = resolve_reviewer_agent(issue, config, selected_agent)
agent_runner_publication.py:681: supervisor_agent = resolve_supervisor_agent(issue, config, "auto", fallback_agent=selected_agent)
agent_runner_publication.py:858: supervisor_agent = resolve_supervisor_agent(...)
```

自写探针输出（`/tmp/verify_lifecycle_probe.py`，tmp config + tmp git 仓库，PRD 头部写 `implementation: kimi` + `review: codex`）：

```text
=== D. PRD override integration for review / implementation ===
  parsed PRD overrides: {'implementation': 'kimi', 'review': 'codex'}
  resolve_lifecycle_agent('implementation', prd_overrides=...) = kimi   # 解析函数本身支持
  choose_agent signature: (issue, config, override_agent) -> str        # 但不接收 prd_overrides
  -> choose_agent(issue, config, 'auto') = claude                       # 真实实现阶段选人不读覆盖
  resolve_reviewer_agent signature: (issue, config, selected_agent, *, prd_overrides=None)
  -> real caller run_pre_pr_review calls it WITHOUT prd_overrides
```

即：解析函数能返回 `kimi`，但**真实消费点绕开了它**。这与 FR-4「PRD 头部覆盖可声明一个或多个生命周期键，仅影响该 PRD 的执行」以及 §1 第 3、4 行的 oracle 直接冲突。

**覆盖缺口佐证**：`tests/test_lifecycle_agent_resolution.py:197` / `tests/test_lifecycle_agent_routing.py:68` 只在**函数级**直接给 `resolve_lifecycle_agent` / `resolve_reviewer_agent` 传 `prd_overrides` 断言，没有任何用例驱动真实 `run_pre_pr_review` / `choose_agent` 路径，故该缺口未被测试捕获。

**修复方向**（供参考，不改代码）：把 `prd_overrides` 从执行循环解析点一路穿到 `choose_agent`、`run_pre_pr_review`、publication 的 supervisor 解析；或让这些阶段统一经 `resolve_lifecycle_agent` 取人。

---

### R3（次要）— `update_agent_fallback_order` / `update_agent_labels` 忽略 `repo_id` 写回目标

- `src/backend/api/routes/agent_runner_lifecycle_agents.py:166-186`、`:216-231`：读取视图按 `request.repo_id` 取仓库合并配置，但写回一律 `create_lifecycle_settings_editor(SCOPE_GLOBAL)`。
- 按 FR-10 / FR-12 这两张卡片本就是全局（Settings）语义，因此**不构成功能错误**；但接口同时接受 `repo_id` 却静默写全局，属易误用的 API 形状不一致。建议写入前拒绝 `repo_id` 或明确文档化。

### R3（次要）— PRD 覆盖写回未规范化大小写

- `src/backend/core/use_cases/lifecycle_agent_resolution.py:101-111` + routes `:295`：`upsert_prd_lifecycle_overrides` 校验时 `normalize`（小写）检查，但渲染时写入**原始值**。若 UI/客户端传 `implementation: "KIMI"`，文件里会落 `KIMI`（重新解析才归一为 `kimi`）。功能正确（load 时会 normalize），但会产生非预期 diff 大小写。次要。

### R3（次要）— AST 防漏守卫作用域偏窄

- `tests/test_lifecycle_agent_routing.py:303-348`：守卫只 `ast.parse(execution_loop_module.__file__)` 单文件，且只覆盖 `run_fix_agent` / `run_closeout_agent`。
- 结论：守卫**确实能**捕获"把首参换成 `selected_agent` 变量"的绕过（`first_argument` 非 `ast.Call` 即失败），带反空转断言，非空转；但若未来在**其它模块**新增 fix/closeout 调用点，或对 verifier/review/supervisor 新增调用点，本守卫发现不了。属防漏强度不足，非错误。

---

## 4. 通过项（对抗性复核后确认成立）

- **凭证一致**；无 alembic / `console_store.py` 表结构 / `tests/guards/**` 改动（`git diff --stat` 为空）。
- **架构**：`resolve_lifecycle_agent` 在 `src/backend/core/use_cases/lifecycle_agent_resolution.py`（core 层）；`api/routes/agent_runner_lifecycle_agents.py` 只做 HTTP 适配，生效值计算/校验收敛在 `core/use_cases/lifecycle_agents_console.py`，路由层无业务解析逻辑。
- **优先级链（配置层）**：`resolve_lifecycle_agent` 实现 PRD 覆盖 > 仓库层 > 全局层 > 既有键 > 内置默认（`lifecycle_agent_resolution.py:280-311`）。`test_repository_layer_wins_over_global_layer` 同时断言两层都非空（`global_layer["fix"]=="kimi"`、`repository_layer["fix"]=="pi"`）后解析为 `pi`，**非空转**。
- **九键闭集**：`LIFECYCLE_AGENT_KEYS` 唯一定义于 `lifecycle_agent.py:29-39`；`extra="forbid"` + 逐阶段 `auto`/`executor` 校验（`agent_runner_settings.py`），未知键 / 非法值在加载期 `ValidationError`（`test_unknown_lifecycle_key_rejected_at_load`）。
- **`executor` 仅 fix/closeout**、**`auto` 逐阶段合法性**：配置层与解析层双重拒绝，测试覆盖 `planner/content_generation/fix/closeout` 不接受 `auto`。
- **fail-fast**：未注册 agent 抛 `UnknownAgentError`，消息含阶段名与 agent 名（`lifecycle_agent_resolution.py:355-369`），测试断言含 `fix` 与 `codebuddy`。
- **fix/closeout 真换人**：`run_agent_execution_loop.py:275,706` 的首参确为 `resolve_lifecycle_agent(...)` 调用；`test_fix_agent_uses_matrix_value` / `test_closeout_agent_uses_matrix_value` 捕获传入 `agent_name`，默认对照 `test_fix_agent_defaults_to_selected_agent` 成立。
- **console API 契约（FR-8/10/12）**：
  - `PUT /agent-runner/lifecycle-agents` 未知 `scope` 由 pydantic `pattern="^(global|repository)$"` 拦成 **422**（自测：`{"scope":"bogus"}` → 422）；`scope=repository` 缺 `repo_id` → **400**。
  - **先校验后写**：`validate_lifecycle_agents_update` 在 `editor.update_lifecycle_agents` 之前执行，未注册 agent → 422 且文件不变（`test_unregistered_agent_rejected_by_api` 断言文件逐字不变）。
  - **保留式写入**：`tomlkit` round-trip + 临时文件 `os.replace`（`toml_section_editor.py`）；只动点名键，`None` 删键。`test_fallback_order_write_preserves_other_runner_keys` 断言 `default_agent` / `verification_commands` 原样保留。三层写隔离测试以**磁盘文件逐字对比**为事实源（非内存态），成立。
  - **路径穿越**：`_decode_prd_path` 解码后经 `resolve_prd_content_path` 校验（非绝对路径、`.md` 后缀、`resolve()` 后必须落在 `tasks/pending`/`tasks/archive` 白名单内）。自测：`../../../../etc/passwd` → 400；`tasks/pending/../../../../tmp/x.md` → 400；`/etc/passwd.md` → 400。读写共用同一白名单。
- **零配置基线**：`test_zero_config_baseline_matches_pre_matrix_behavior` 经 `build_app_config_from_settings(AgentRunnerSettings())` 走真实 settings 加载，非手工构造解析结果。
- **文档/配置（FR-9）**：`config.toml:128,140` 含全注释模板；`docs/guides/lifecycle-agent-matrix.md` 存在；`mkdocs.yml:66` 已挂导航。

---

## 5. 我无法验证的项（明确不声称通过）

1. **Playwright e2e**：`tests/playwright-e2e` 未安装依赖、未编写/执行 `pnpm test --grep lifecycle-agent`；本报告未运行任何浏览器用例。
2. **前端真实入口**：Settings「Agent 管理」粘性 Tab / Roadmap 仓库齿轮抽屉 / PRD 覆盖抽屉的浏览器行为、`pnpm typecheck / lint / build` **未由我复跑**。
3. **rv-3 / rv-4 / rv-6 / rv-7 的真实呈递截图**：`tasks/evidence/<stem>/rv-*.png` **不存在**（目录内仅 `.md`），§9.1 呈递区未经人过目。
4. **Human-Confirmed 决策一/二/三/四/五**（§9.2）未经人确认。
5. **全仓回归** `just test`、`uv run mkdocs build --strict`、`just lint`：仅执行者报告，我未复跑。
6. **rv-2 negative control**：PRD 要求记录"在旧实现下断言红色"的负对照，执行者 `.evidence-report.md` **未包含**该记录，我无法验证该负对照确已执行（代码层面新路径已成立，见 §4）。
7. `implementation` / `review` PRD 覆盖的**端到端**行为：因 R1 缺陷，无法验证为通过（当前为不生效）。

---

## 6. 执行者 `.evidence-report.md` 是否过度声称

- **诚实披露到位**：§3 明确列出"截图呈递未执行、e2e 未运行、§9.2 未人确认、不归档"，与我在 §5 的观察一致；数字（47 passed / 2 passed 19 deselected）与我的复跑**逐字一致**。
- **轻度过度声称**：§1「消费点接线：…`choose_agent` …」与 Change Log「FR-1~FR-12 均有实现落点」暗示 PRD 级覆盖全链生效。实际 FR-4 的 PRD 覆盖只覆盖 fix/closeout/verifier，**`implementation`/`review` 未生效**（R1）。建议将 §1/Change Log 的措辞收窄，或在修复 R1 后再主张"全链"。
- 未发现伪造命令输出或把组件级证据冒充真实验证的情况。

---

## 7. 复现指引

```bash
cd /Users/zata/code/keda-worktrees/lifecycle-agent-matrix
git diff HEAD -- src tests | shasum -a 256   # 应等于冻结值
uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q --no-testmon
uv run python /tmp/verify_lifecycle_probe.py   # 见 §3 R1 证据；脚本仅在 /tmp，不落仓
```

---

# Round 2（整改复核）

- 角色: 独立 verifier（第二轮，对抗性复核）
- 复核时间: 2026-09-20
- Worktree: 同上（分支 `lifecycle-agent-matrix`，HEAD `4d4dd12`，整改以**未提交工作区改动**形式落盘）
- 只读源码/测试/PRD，仅写本报告；探针脚本只落 `/tmp`。

## R2-0. 冻结凭证（第二轮）

```
$ git diff HEAD -- src tests | shasum -a 256
593b86655ff313a396997b98f0f1b52d049202c9cce6f89463177208918c7c9c  -
```

**结果：与第二轮冻结值逐字一致（MATCH）**；复核期间无源码/测试漂移。与第二轮冻结值不同、但与第二轮指定值一致，说明整改确实落盘（HEAD 未动，整改为工作区改动）。

## R2-1. 判定

## **PASS with caveats**

- 后端测试全绿且数量由 47 → **53**；R1 阻断项经**独立端到端探针**确认修复；三个 R3 均确认整改。
- 无新增 R1/R2。新增 1 条 R3 残留（非本轮 oracle 覆盖范围）与 3 条 R4 覆盖/文档缺口，均不阻断验收。

## R2-2. 独立复跑的命令（非引用执行者报告）

| 命令 | 观察结果 |
|---|---|
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q --no-testmon` | `53 passed in 1.91s`（第一轮为 47 passed） |
| `uv run pytest tests/test_lifecycle_agent_resolution.py -q -k unregistered --no-testmon` | `2 passed, 19 deselected` |
| 自写探针 `/tmp/verify_r2_probe.py`（真实 tmp git 仓库 + PRD 文件 + `attach_prd_lifecycle_overrides` → 各解析函数） | 全部断言 OK（见 R2-3） |

## R2-3. 整改确认表

| 第一轮 finding | 是否修复 | 证据 |
|---|---|---|
| **R1（阻断）** PRD 覆盖对 `implementation` / `review` / `supervisor` 不生效 | **FIXED** | 机制改为「Issue 携带覆盖」：`_process_single_issue` 在 `agent_runner_orchestration_runtime.py:186` 调 `attach_prd_lifecycle_overrides(issue, repo_path)` 回填 `IssueSummary.lifecycle_overrides`（新字段，`agent_runner.py` diff `+lifecycle_overrides: tuple[...] = ()`）；`choose_agent`（`run_agent_once.py:229-231` → `_resolve_declared_lifecycle_agent` `:367-408` → `effective_prd_overrides` `:252-275`）、`resolve_reviewer_agent`（`:435-441`）、`resolve_supervisor_agent`（`:350-356`）、`_choose_verifier_agent`（`run_verifier_agent.py:529`）统一经 `effective_prd_overrides(issue, ...)` 读取。独立探针逐条验证（见下）。 |
| **R3** fallback-order / agent-labels PUT 收 `repo_id` 却恒写全局 | **FIXED（文档化）** | `agent_runner_lifecycle_agents.py:168-173` 与 `:223-227` docstring 明确"机器级配置、写入目标恒为 `config.toml`、`repo_id` 只用于选校验视角"；行为不变（仍 `create_lifecycle_settings_editor(SCOPE_GLOBAL)` `:181,:234`），与 FR-10/FR-12 一致。 |
| **R3** PRD 覆盖写回未规范化 | **FIXED** | `agent_runner_lifecycle_agents.py:291-305` 先经 `validate_lifecycle_agents_update` 校验并规范化（小写、去空白，`lifecycle_agents_console.py:154` `normalize_lifecycle_agent_value`）再 `upsert_prd_lifecycle_overrides`。 |
| **R3** AST 防漏守卫作用域偏窄 | **FIXED** | `tests/test_lifecycle_agent_routing.py:429-432` 现 `glob("*.py")` 扫描整个 `core/use_cases/`；保留反空转断言 `assert call_sites`（`:441`）与首参必须是 `resolve_lifecycle_agent` 调用的断言（`:443-457`），测试通过 → 非空转。 |

### R1 端到端证据（独立探针 `/tmp/verify_r2_probe.py`，真实 tmp git repo + PRD 头部块）

```
registered agents: ['codex', 'claude', 'kimi', 'pi']
[OK ] pre-attach choose_agent (no overrides) -> claude            # 无覆盖时回落 default_agent
[OK ] attach carried implementation override: kimi
[OK ] attach carried review override: codex
[OK ] effective_prd_overrides(enriched): {implementation: kimi, review: codex}
[OK ] post-attach choose_agent -> kimi (PRD wins)                 # 行为样例表第 3 行
[OK ] post-attach resolve_reviewer_agent -> codex (PRD wins)      # 行为样例表第 4 行
[OK ] pre-attach resolve_reviewer_agent -> kimi                   # 反证：未回填时不生效
[OK ] post-attach resolve_supervisor_agent (undeclared) -> fallback
[OK ] label route wins over PRD override -> claude                # PRD §6 标签路由优先级
[OK ] explicit override wins over PRD -> pi                       # CLI/loop 覆盖最高
[OK ] supervisor PRD override wins -> pi                          # 声明 supervisor 时同样生效
```

即：**行为样例表第 3、4 行的 oracle 现已端到端成立**；且实现阶段的 `agent/*` 标签 / CLI override 仍高于 PRD 覆盖（PRD §6 预期的优先级未被破坏）。

### 独立运行路径排查（是否存在绕过 `_process_single_issue` 的实现/审核/监督选人点）

- `run_once` 的**串行与并行两条路径**都经 `_process_single_issue`（`agent_runner_orchestration_runtime.py:577,589+`），该方法在**唯一**同时掌握 `repo_path` 与 Issue 的位置回填覆盖；四个分支（ready / running_rework / blocked_resolution / running_publish_recovery）均以回填后的 `issue` 传入（`:203-281`）。
- `agent_runner_issue_handlers.py:173,272,458,575` 的 `choose_agent` / `resolve_repair_agent` 调用点均位于 `_process_ready_issue` / `_process_blocked_resolution` / `_process_running_rework` 内，由 `_process_single_issue` 以回填后 Issue 触发 → **覆盖**。
- `agent_runner_worktree_branch.py:140`（rebase 冲突修复选人）经 `execute_rebase` → `_process_running_rework` 链，同样使用回填后 Issue → **覆盖**。
- `review_once.py:190` 的 `resolve_supervisor_agent(issue, config, agent)`：**独立入口**（`iar review` / `review-daemon` / `interactive_decision._execute_review_once`），**未调用** `attach_prd_lifecycle_overrides` → 见 R2-4 新增 R3。

## R2-4. 新增 findings

### R3（残留，非阻断）— 独立 `iar review` 路径不读 PRD 监督覆盖

- 位置：`src/backend/core/use_cases/review_once.py:190`（`resolve_supervisor_agent(issue, config, agent)`），入口 `src/backend/api/cli_parsed_commands/runner.py:163`、`review_daemon.py:45`、`interactive_decision.py:682`。
- 失败场景：某 PRD 头部声明 `supervisor: pi`；runner 的发布路径（`agent_runner_publication.py:681,858`）会用 `pi`，但**独立** `iar review` / review-daemon 处理同一 Issue 时不会（该路径未回填 `lifecycle_overrides`，`issue` 是直接构造的，`:102`）。
- 是否阻断：**否**。§1 行为样例表第 3、4 行只覆盖 `implementation` / `review`，未给 supervisor 的 PRD 覆盖立 oracle；且 runner 主流水线的监督路径已生效。属"独立命令与 runner 语义未完全对齐"，建议后续把 `attach_prd_lifecycle_overrides` 也接入 `review_once`，或在文档中标注该限制。

### R4（测试覆盖缺口）— 编排入口 → attach 的接线无测试锁定

- `test_choose_agent_honors_issue_carried_prd_override` / `test_resolve_reviewer_agent_honors_issue_carried_prd_override` / `test_resolve_supervisor_agent_honors_issue_carried_prd_override` 都是**直接构造** `IssueSummary(lifecycle_overrides=...)`，**不经** `attach_prd_lifecycle_overrides`。
- 影响：若 `agent_runner_orchestration_runtime.py:186` 的 attach 调用被回退，这三个测试**仍会绿**。该接线目前仅由 `test_attach_prd_lifecycle_overrides_reads_prd_header`（单测 attach 本身）+ 我的探针 + 代码阅读保证。建议补一条 `_process_single_issue`（或等价）级别的接线测试。

### R4（症状级）— `--dry-run` 打印的 agent 未计入 PRD 覆盖

- `agent_runner_orchestration_runtime.py:543` 的 dry-run 分支在 `attach` 之前调用 `choose_agent(issue, config, agent)`，故 `--dry-run` 日志可能显示 `default_agent` 而非 PRD 覆盖值。仅影响预览输出，不影响真实执行。

### R4（文档）— 用户指南未显式写明两处 PUT 的 `repo_id`-但写全局

- `docs/guides/lifecycle-agent-matrix.md:42,77` 已把全局层标为"机器级"、回退顺序卡片放 Settings，方向上正确；但"`repo_id` 被接受却恒写 `config.toml`"这一语义只落在 API docstring，未进用户指南。建议补一句以免调用方误用。

### 关于新增测试的可伪证性（对抗性检查）

- 尝试伪证：若 `attach_prd_lifecycle_overrides` 返回原 Issue，`test_*_honors_issue_carried_prd_override` 是否仍绿？**是**——它们直接注入字段（见上 R4）。
- 但它们在**整改前**无法通过：`IssueSummary.lifecycle_overrides` 是本轮新增字段（`git diff` `agent_runner.py` 中为 `+` 行），整改前构造该 Issue 即报错。故这些是有效的回归测试，非空转。
- 未发现伪造命令输出或将组件级证据冒充真实入口验证。

## R2-5. 明确无法验证（不声称通过）

1. **Playwright e2e**：`tests/playwright-e2e` 依赖未装、`pnpm test --grep lifecycle-agent` 未运行。
2. **浏览器真实入口**：Settings「Agent 管理」粘性 Tab / Roadmap 仓库齿轮抽屉 / PRD 覆盖抽屉的浏览器行为，以及 `cd frontend-public && pnpm typecheck && pnpm lint && pnpm build` 均**未由我复跑**。
3. **rv-3 / rv-4 / rv-6 / rv-7 的真实呈递截图**：`tasks/evidence/<stem>/rv-*.png` 仍不存在（目录内仅 `.md`），§9.1 呈递区未过人眼。
4. **Human-Confirmed 决策一至五**（§9.2）未经人确认。
5. **rv-2 negative control**：PRD 要求的"旧实现下断言红色"记录仍未在 `.evidence-report.md` 中出现，我无法验证该负对照确已执行。
6. **全仓回归** `just test`、`just lint`、`uv run mkdocs build --strict`：仅执行者报告，未复跑。
7. `iar review` 独立路径的 supervisor 覆盖（R2-4 R3）——按当前实现为**不生效**，我未能在该路径上验证为通过。

---

## Round 3（最终树确认）

- 角色: 独立 verifier（第三轮，最终树确认）
- 复核时间: 2026-09-20
- Worktree: `/Users/zata/code/keda-worktrees/lifecycle-agent-matrix`，分支 `lifecycle-agent-matrix`，HEAD `cbd3bd5`
- 只读源码/测试/PRD，**未做任何 git 写操作**；探针脚本仅落 `/tmp`；仅写本报告。
- 本轮增量：新增 `tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts`（+174 行）+ PRD §9.2 勾选（`tasks/pending/...md`，不在凭证范围）。执行者拟据此归档 PRD，故本轮重点核验新 spec 的真实性与 §9.2 勾选项的可信度。

## R3-0. 冻结凭证（第三轮）

```
$ git diff HEAD -- src tests | shasum -a 256
e0d1a39e72cb8d4cc38cc34553423b2ec4391daf445ed0e2eafb97d14a442e8f  -
```

**结果：与第三轮冻结值逐字一致（MATCH）**。复核开始与结束两次计算一致，期间无源码/测试漂移。凭证差异仅 `tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts`（`git diff HEAD --stat -- src tests`：`1 file changed, 174 insertions(+)`，无删除/改名；`src/` 侧零内容变更）。

## R3-1. 判定

## **PASS with caveats**

- 增量 scope 与声明一致（仅新 e2e spec，`src/` 无改动）；新 spec 为**真实入口**且断言非空转；本地实跑 **7 passed**（0 skip）。
- 全仓 `uv run pytest tests/ -q --no-testmon` = **2326 passed**，与 §9.2 一致；`pnpm typecheck` exit 0。
- 新增 1 条 **minor** 证据引用不准（R3-4）：§9.2 把"三文件命令的 53 passed"挂到单文件命令 `tests/test_lifecycle_agent_resolution.py -q` 上，该命令实际不产出 53。**其余勾选项均经复跑或文件核对成立。**
- 本轮未新增 R1/R2；Round 2 的 1 条 R3 残留（`iar review` 独立路径不读 PRD supervisor 覆盖）**仍未整改**，本轮确认其依旧存在（不影响本轮增量）。

## R3-2. 独立复跑的命令（非引用执行者报告）

| 命令 | 我的观察结果 | 与 §9.2 声明 |
|---|---|---|
| `git diff HEAD -- src tests \| shasum -a 256` | `e0d1a39e…2e8f` | MATCH |
| `git diff HEAD --stat -- src tests` | 仅新增 spec，174 insertions | 一致 |
| `pnpm test --grep lifecycle-agent`（真实 console `127.0.0.1:8899`） | **7 passed (10.6s)**，0 skipped | 一致 |
| `uv run pytest tests/ -q --no-testmon` | **2326 passed in 136.50s** | 一致 |
| `uv run pytest tests/test_lifecycle_agent_resolution.py -q --no-testmon` | **21 passed** | **不一致（§9.2 写 53）** |
| `uv run pytest tests/test_lifecycle_agent_resolution.py tests/test_lifecycle_agent_routing.py tests/test_lifecycle_agents_console_api.py -q --no-testmon` | **54 passed**（Round 2 时为 53） | 53 的真实来源 |
| `cd frontend-public && pnpm typecheck` | exit 0 | 一致 |
| `rg -n "lifecycle_agents" config.toml docs/` | `config.toml:128,140` + `docs/guides/...:40-65` 命中 | 一致 |
| `rg -n "既有配置键\|legacy" docs/guides/lifecycle-agent-matrix.md` | `:13`、`:46-47` 命中 | 一致 |
| `git diff HEAD --name-only \| grep -E "console_store\.py\|alembic/"` | NO HITS | 一致 |

## R3-3. 新 spec 质量评估（真实入口 / 空转对抗性检查）

`tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts` 覆盖三处界面，均对**真实后端**（`PLAYWRIGHT_BASE_URL=127.0.0.1:8899`）发起真实请求：

- **Settings「Agent 管理」**：test 1（默认停 Tab①「Agent 标签设置」→ 切 Tab②「生命周期 Agent 设置」，断言九行矩阵 + 回退顺序卡片同时出现，并保留原有页面内容）；test 2（九行 `row/select/source` 齐全 + 展开 `verifier` 下拉候选值域 = 已注册 agent + `auto`，排除 `executor`）；test 3（`fix` 行提供 `executor`、不提供 `auto`；`implementation` 行反之）；test 4（回退顺序卡片可用、排序本地态变更）。
- **Roadmap 仓库行齿轮**：test 5（点 `repo-agent-gear-*` 打开 `repo-lifecycle-matrix-matrix`，九行 `row/source` 齐全）。
- **PRD 覆盖抽屉**：test 6（`prd-open-content` → `prd-agent-override-open` → 九行 `row/toggle` 存在；未勾选行下拉 disabled；勾选后 enabled）。

**testid 真实性核对**（防止"断言了一个永不存在的 testid 而空转"）：在 `frontend-public` 源码中逐一确认 testid 存在。`global-lifecycle-matrix-matrix` / `repo-lifecycle-matrix-matrix` 表面 `rg` 为 0，实为组件 `lifecycle-agent-matrix.tsx` 的 `namespace` 前缀动态拼接（`${n}-matrix`），settings/page.tsx 传 `global-lifecycle-matrix`、repository-agent-matrix-sheet.tsx 传 `repo-lifecycle-matrix` —— **是真实 testid，非空转**。

**空转对抗性检查（逐条）**：

- `expect(...).toHaveCount(0)`（`global-lifecycle-matrix-matrix`，spec:60）：**非空转**——同一 test 的 spec:64 对同一 locator 断言 `toBeVisible()`，若区块整体坏掉 64 先失败。
- `toHaveCount(0)`（`lifecycle-matrix-option-verifier-executor` spec:86、`option-fix-auto` spec:94、`option-implementation-executor` spec:100）：**非空转**——相邻行先断言目标 option `toBeVisible()`，下拉整体未渲染时会先失败。
- `toHaveCount(0)`（`lifecycle-matrix-restore-verifier` spec:144）：`lifecycle-matrix-restore-*` testid 确实存在于源码（仅"已在本层声明"时渲染）；该断言的先决条件 `drawerMatrix.toBeVisible()` 已在 spec:136 断言，**非空转**，但语义依赖该仓库未声明 `verifier`（环境相关，见 R3-5）。
- `test.skip(gearCount === 0)` spec:132 / `test.skip(prdCount === 0)` spec:155 / `test.skip(orderBefore.length < 2)` spec:116：**本轮全部未触发**（7 passed、0 skipped），即仓库抽屉与 PRD 抽屉用例**真的跑了**，不构成 masking。在无预置仓库/PRD 的裸环境会 skip——已在 spec 头注释与 §9.2 披露，属可接受降级，非伪造通过。

**范围披露准确性**：spec 头声明"只读交互、不写盘"。核对代码：排序（spec:121 点 `fallback-order-up-*`）只改本地态、**未点保存**；PRD 覆盖（spec:170 点 toggle）只改本地态、**未点保存**；全篇无任何 `save` 点击或写盘请求。**披露属实**——写盘语义不由 e2e 覆盖，而由后端契约测试（`tests/test_lifecycle_agents_console_api.py`，以磁盘内容为事实源）与 `rv-*.png` 覆盖。**方向正确**：e2e 跑在共享仓库上，写盘会污染真实 `config.toml` / `.iar.toml`。

## R3-4. 新增 findings

### minor（证据引用不准，非阻断）— §9.2「53 passed」与所引命令不符

- 位置：PRD §9.2 Architecture Acceptance 第一条（`tasks/pending/P1-FEAT-20260918-110027-lifecycle-agent-matrix.md:505`）："`uv run pytest tests/test_lifecycle_agent_resolution.py -q` 全绿（rv-1 佐证）…… 53 passed"。
- 事实：`uv run pytest tests/test_lifecycle_agent_resolution.py -q --no-testmon` = **21 passed**；不带 `--no-testmon` 时（本仓 testmon 生效）输出 **21 deselected**（测试根本没跑）。53 实为**三文件命令**（resolution + routing + console_api）在 Round 2 的计数（Round 2 报告 `:186` 明载），本轮该三文件命令为 **54 passed**。
- 影响：**不阻断**。底层事实（rv-1 全部用例绿）为真，仅"命令 ↔ 数字"配对复制错误。但勾选项字面陈述不可复现，属证据诚信层面的 minor 瑕疵。
- 建议：把该行改为三文件命令 + 当前计数（`54 passed`），或改为单文件命令 + `21 passed`——二者取其一。

### 低（环境相关断言）— spec:144 依赖仓库未声明 `verifier`

- 该断言在当前 registry 的仓库上成立；若某环境首个受管理仓库恰好声明了 `verifier`，则该用例会**失败**（不是空转，但是环境敏感）。非阻断，若未来在多变仓库环境跑需注意。

### 未消除（沿用 Round 2）— `iar review` 独立路径不读 PRD supervisor 覆盖

- `src/backend/core/use_cases/review_once.py:190` 仍未调用 `attach_prd_lifecycle_overrides`（Round 2 R2-4 R3）。本轮增量未触及该文件，残留不变，仍为非阻断。

## R3-5. 归档就绪结论

**可以归档，但存在 1 条需诚实处理的勾选项。**

- **支持归档**：凭证 MATCH；`src/` 零改动、`tests/` 增量仅为新 spec；新 spec 真实入口、0 skip、无空转；全仓 2326 passed；typecheck 0；§9.2 中被我抽查的 testid/测试名/文档命中/无 alembic 变更**全部成立**。
- **唯一不成立项**：§9.2 `:505` 的"`uv run pytest tests/test_lifecycle_agent_resolution.py -q` …… 53 passed"——命令与计数不匹配（见 R3-4）。**只要把该行的命令/数字校正，归档即为诚实**；若不校正而直接归档，等于在验收证据里留下一条不可复现的陈述（minor）。
- **仍需保留的披露**（不阻断，勿声称已验证）：见 R3-6。

## R3-6. 明确无法验证 / 不声称通过（本轮）

1. **写盘路径**：e2e 刻意只读，`config.toml` / `.iar.toml` / PRD 头部"保存后真的变了"**未由 e2e 覆盖**；其证据为后端契约测试（`tests/test_lifecycle_agents_console_api.py`，11 passed）+ `rv-3*` / `rv-4*` / `rv-6` / `rv-7` 手工截图。本轮未复跑 `just run` 手工链路。
2. **CI / 裸环境**：`gearCount` / `prdCount` / `orderBefore` 相关的 3 个 `test.skip` 在无预置受管理仓库或 PRD 的环境会**跳过**，届时"仓库抽屉 / PRD 抽屉"用例不产生覆盖——不得据"7 passed"推断所有环境都实跑该两条。
3. **`just lint` / `just test all`（写 lint/test flag 文件）/ `just run`**：本轮未执行（避免写共享状态文件）；`mkdocs build --strict`、frontend `pnpm lint` 亦未复跑，仅 `pnpm typecheck` 复跑通过。
4. **rv-2 negative control**：PRD 要求的"旧实现下断言红色"记录仍未见于 `.evidence-report.md`，无法验证已执行（沿用 Round 2 R2-5:5）。
5. **`iar review` 独立路径 supervisor 覆盖**：按当前实现为不生效（R2-4 R3 / R3-4），未能在该路径上验证通过。
