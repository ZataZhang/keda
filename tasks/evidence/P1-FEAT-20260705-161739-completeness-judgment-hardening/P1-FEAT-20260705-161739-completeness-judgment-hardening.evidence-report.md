# 证据报告 · iar 完成度判定结构性盲区加固

PRD：`tasks/pending/P1-FEAT-20260705-161739-completeness-judgment-hardening.md`
分支 / worktree：`feat/completeness-judgment-hardening` @ `../keda-worktrees/feat/completeness-judgment-hardening`
实施日期：2026-09-16 · 实施者：codebuddy（执行锁持有方）

> 2026-09-16 归档后补充：分支已 rebase 到最新 `main`（`404c943`）。main 上另有并行会话把同一份源码实现（三个补丁 + 配置五处同步）提前合入，因此本分支的最终增量是**文档、测试、PRD 归档与证据包**；源码改动与 main 已合入的版本一致，rebase 时冲突全部按 main 侧解决并复核无重复定义。下列测试数字为 rebase 后重跑的结果。
证据目录：`tasks/evidence/P1-FEAT-20260705-161739-completeness-judgment-hardening/`（本目录仅 `*.md` 进版本库，原始输出留在本地磁盘）


> 补充（CI 回合）：`Validate Template / Check max file lines` 报 `pr_supervisor.py` 1186 非空行超 1000 硬上限。
allowlist 明确禁止新增豁免，故把补丁 3 / 补丁 4 的实现切到同级子模块 `pr_supervisor_diff.py` 与
`pr_supervisor_findings.py`（均为 `core/use_cases/`，无新抽象层），`pr_supervisor.py` 回落 951 非空行，
属对 PRD D-06 的被迫偏离，已在归档 PRD 的 Decision Log 末尾记录。

## 1. 高风险 oracle 结果（对应 Part A 风险地图点名处）

### rv-1 · `|| true` 兜底假绿灯被抓（补丁 1）— 证据 `rv-1-rv-2.txt`

真实入口：`ensure_validation_commands_pass` + 生产 `IProcessRunner`（`SubprocessRunner`，命令经 `bash -lc` 真跑）+ 真读盘 manifest 解析，临时 worktree 已 `git init`。

| 用例 | 输入 | 实测结果 |
|---|---|---|
| 正例 | 命令 `[ -d /tmp/definitely-missing-dir ] || true` + `stdout_assertions: [{pattern: REAL_OUTPUT, must_match: true}]` | `REJECTED: Realistic Validation item 1 exited 0 but its stdout assertion #1 failed: must contain 'REAL_OUTPUT' in stdout (severity=high, ...)` |
| **负控** | 同一命令、删掉 `stdout_assertions` | `PASSED`（命令 exit 0） |

结论：判别力确实来自 `stdout_assertions`——同一条兜底命令，加断言被拒、删断言放行。这正是补丁 1 要堵的洞（补丁前只看退出码，兜底命令永远通过）。

### rv-2 · 旧 manifest 向后兼容（FR-1）— 证据 `rv-1-rv-2.txt`

| 用例 | 输入 | 实测结果 |
|---|---|---|
| 正例 | 旧格式 manifest（无 `stdout_assertions`）+ `echo hi` | `PASSED` |
| **负控** | `evidence.json` 写成非法 JSON | `REJECTED: .iar/evidence/evidence.json is not valid JSON: Expecting property name enclosed in double quotes` |

结论：旧 manifest 静默按空断言处理，行为与补丁前一致；解析器确实在跑（非法 JSON 报错）。

### rv-4 · 大 PR 关键文件 diff 全量（补丁 3 / FR-6, FR-7）— 证据 `rv-4.txt`

构造 15 个文件（5 个 `src/backend/core/**` + 10 个 `tests/**`）、总计约 13650 字符的 diff：

- `test_build_layered_diff_includes_full_key_files_and_truncates_rest`：预算 6000 → 5 个关键文件 diff 逐字节完整（`CORE-<n>-` + 900 字符全部在输出里），非关键文件进入 `--- Other files (truncated to 6000 chars) ---`，末尾文件被截掉。
- `test_build_supervisor_prompt_includes_layered_diff_for_key_paths`（走 `build_supervisor_prompt` 真入口，`max_diff_chars=2000`）：
  - 配 `key_paths=("src/backend/core/",)` → prompt 含 `--- Key files (full diff) ---` 与完整 `CORE-4-...`；
  - **负控**：`key_paths=()` → prompt 无 `Key files` 段，`CORE-4-...` 被 2000 字符预算截掉。

### rv-5 · 跨 cycle finding 累积（补丁 4 / FR-8~FR-12）— 证据 `rv-5.txt`

- `test_build_supervisor_prompt_includes_previous_unresolved_findings`：prompt 含 `Previous unresolved findings from cycles 1..1:`、`[high] src/backend/core/use_cases/foo.py:42`、`Missing error handling`；**负控**：不传 `previous_findings` 时 prompt 不含 `Previous unresolved`。
- `test_supervisor_finding_tracker_persists_and_loads_findings`：落盘到 `<worktree>/.iar/state/issue-42/findings.json`（真写盘），读回 1 条且 `cycle_reported=1`；同 key 重复上报不新增条目；`status=resolved` 后出列（读回空）。
- `test_supervisor_loop_injects_previous_findings_into_cycle_2_prompt`（端到端跑两轮 `run_post_pr_supervisor_cycle`）：cycle 1 的 prompt 不含累积段，cycle 2 的 prompt 含 `Previous unresolved findings from cycles 1..1:` 与 cycle 1 报的 finding——即"cycle 之间失忆"已修复。
- `test_parse_supervisor_action_extracts_findings`：LLM 输出 `findings[]` 时被解析进 `findings_detail`；旧输出（无该字段）静默空 tuple，不影响 fail-closed 的 action 解析。

### rv-7 · 测试不回归 — 证据 `rv-7.txt`

`uv run pytest -o addopts="" tests/ -q` → **2129 passed**（rebase 后 main 基线 2115，本 PRD 新增 14 条；`-o addopts=""` 强制全量，规避 testmon 增量陷阱）。

### 对抗自检 — 证据 `adversarial-check.txt`

按 PRD §7.6 rv-7 的负控定义，把 `_is_stdout_assertion_satisfied` 的判定**整体取反**
（`return pattern_matched if assertion.must_match else not pattern_matched`
 → `return (not pattern_matched) if assertion.must_match else pattern_matched`）后重跑
`pytest -o addopts="" tests/test_agent_runner_validation.py -k stdout`：

```
FAILED tests/test_agent_runner_validation.py::test_ensure_validation_commands_pass_enforces_stdout_substring
FAILED tests/test_agent_runner_validation.py::test_ensure_validation_commands_pass_allows_matching_stdout
FAILED tests/test_agent_runner_validation.py::test_ensure_validation_commands_pass_enforces_stdout_must_not_contain
3 failed, 115 deselected；exit=1
```

PRD 点名的 `test_ensure_validation_commands_pass_enforces_stdout_substring` 确实失败，负控成立。
代码已还原（`grep ADVERSARIAL` = 0），还原后 3 条用例恢复全绿（3 passed）。

> 更正记录：首次对抗自检只翻了 `must_match=False` 分支（结果 `..F`），并在报告里解释为"该用例对方向不敏感"——该解释不成立（整体取反时三条全红）。已按 PRD 字面重做并替换证据，上述为更正后的结论。

## 2. 风险地图对账 Predicted → Reconciled

| 预测 | 实测 |
|---|---|
| `EvidenceBlock` 扩展字段会引发下游解析错误 | 未发生：rv-2 证明旧 manifest 仍解析；新增字段全部带默认值，frozen dataclass 追加字段不影响既有构造点 |
| finding artifact 多 worktree 并发会 stale | 未触发：artifact 单进程内写后立即被下一轮读；本次未引入并发场景 |
| `key_paths` 前缀写错会静默失效 | 已缓解：前缀一个文件都没命中时打 WARNING（代码 `_build_layered_diff`），路径统一 `./` 与反斜杠归一化 |
| 断言写坏会静默降级为"不检查" | 未发生：畸形 `stdout_assertions`（缺 `pattern` / 非法 `source` / 非 bool `must_match`）抛 `ValidationEvidenceError` |

### 与 PRD §3 的一处偏差（需人审确认）

§3「新增可选配置」列了 `[validation].stdout_assertions_enabled = false`，但 §6 明确写"不引入新配置开关（直接用 evidence manifest 自描述）"，FR 清单也未包含该开关。**实施按 §6 / FR 执行（未加开关）**：断言完全由 manifest 自描述，未声明即不检查，等价于"默认关闭"。若确需全局开关，建议后续 PRD 单独立项。

## 3. 低风险门禁结果

| 门禁 | 结果 |
|---|---|
| `uv run python hooks/shared/check_architecture.py` | exit 0（241 文件扫描，无跨层导入）— `arch.txt` |
| `just lint --full` | 全绿（ruff / ruff-format / PRD checklist / guidelines / max-file-lines 均 Passed） |
| `just lint --reuse` | 全绿（jscpd / pylint duplicate-code / architecture / guidelines / max-file-lines 均 Passed） |
| `just test`（等价全量 pytest） | 2129 passed |
| 漂移守卫 grep | `drift-guard.txt`：dataclass / Pydantic settings / factory 映射 / `config.toml` / `.iar.toml` 五处字段齐全；`pr_supervisor.py` 中 `6000` 仅存在于注释，字符预算唯一落点是 `max_diff_chars` |
| finding artifact 被排除 | `gitignore-check.txt`：`git check-ignore -v .iar/state/issue-1/findings.json` → `.gitignore:90:.iar/` |
| 依赖变化 | `git diff pyproject.toml uv.lock` 为空；前端无改动（`No frontend impact`） |

## 4. 交付范围对账

- 补丁 1：`StdoutAssertion` + `EvidenceBlock.stdout_assertions` + `_extract_stdout_assertions` + `_validate_stdout_assertions` + 中英 prompt hint —— 已落地
- 补丁 3：`FindingDetail` 之外新增 `PostPrSupervisorConfig.key_paths` / `max_diff_chars` + `_build_layered_diff` 替换整体截断 —— 已落地
- 补丁 4：`_load_previous_findings` / `_persist_findings` / `_extract_supervisor_findings` / prompt 注入 / `SupervisorActionResult.findings_detail` —— 已落地，未新建模块（放在 `pr_supervisor.py`，符合 D-06）
- 三个补丁同时落地，无 Phase 拆分；无并行抽象
- rv-6（真实 GitHub issue 端到端）需 token，标 opt-in **未执行**（PRD 自标 `required_for_acceptance: false`）；Delivery Readiness 第 4 条因此记为"opt-in 未执行"，不打勾

## 5. 未覆盖 / 限制

- 未做真实 GitHub PR 的端到端 supervisor 循环（无 token 环境，PRD 允许 opt-in）。
- finding artifact 的并发 worktree 场景按 PRD 假设（issue ↔ worktree 一一对应）未覆盖。
- 无前端改动，本 PRD 声明 `No frontend impact`，证据包不含视觉证据（PRD 未要求）。
