# 独立 Verifier 验收审查报告 · iar 完成度判定结构性盲区加固（第二轮复审）

PRD：`tasks/pending/P1-FEAT-20260705-161739-completeness-judgment-hardening.md`
分支 / worktree：`feat/completeness-judgment-hardening` @ `/Users/zata/code/keda-worktrees/feat/completeness-judgment-hardening`（HEAD `da502c2`）
第一轮：2026-09-16 · **REJECT**（阻塞项 B-1）
第二轮复审：2026-09-16 · 审查方：独立 verifier（只读）
性质：本报告只做只读核验 + 一次可完全还原的临时变异实验；**未执行任何 git 写操作**，源码 / 测试 / PRD 正文均无净改动。

---

## 结论：**PASS**

| 项 | 第一轮 | 第二轮 |
|---|---|---|
| **B-1** rv-7 负控未按 PRD 字面执行 + 报告解释错误 | 阻塞 | **已解决**（我亲手复跑，见 §3.5） |
| **N-1** `new-tests.txt` 混入既有用例 | 非阻塞 | **已解决**（14 条全部为新增，见 §3.8） |
| **N-2** `agent_runner_supervisor.py` 落点与 PRD §7.2 表述不一致 | 非阻塞 | 维持（PRD 内部表述问题，实现取 FR 一边，归档时修 PRD） |
| **N-3** key 文件 diff 无字符预算上限 | 非阻塞 | 维持（配置驱动下的自担风险） |
| **O-1 / O-2 / O-3** 观察项 | 记录 | 维持，不阻塞 |

实施者声明的 4 项修复我逐条独立复核，**全部属实**。全量测试、架构门禁、lint 我全部重跑，与实施者自述一致。

---

## 1. 复审范围与方法

- 复核实施者提交的 4 项修复：对抗自检重做、`evidence-report.md` 更正记录、`new-tests.txt` 重生成、rv-6 记账。
- **亲手复跑** rv-7 负控：落盘变异 → 跑用例 → 还原 → 校验 sha256 与还原后结果（§3.5）。
- 独立复跑全量测试、架构门禁、`just lint --full` / `--reuse`、四组用例（§3.2/3.3/3.6/3.7）。
- 用 `git diff HEAD` 独立数新增测试，与 `new-tests.txt` 做集合比对（§3.8）。
- 复核工作区未被我的实验污染：`git status --porcelain` 前后一致（§3.9）。

---

## 2. Acceptance Checklist 逐条对账（第二轮状态）

### 2.1 Acceptance Evidence Package（§9 第 1 组，5 项）

| # | 条目 | 判定 | 依据 |
|---|---|---|---|
| 1 | 高风险 oracle 结果 rv-1 / rv-2 / rv-4 / rv-5 | **证据充分** | 第一轮我复跑过 rv-1/rv-2 真实 subprocess 四象限；本轮分组重跑 rv-4（2 passed）、rv-5（4 passed）仍绿（§3.7） |
| 2 | 风险地图 Predicted → Reconciled（4 条） | **证据充分** | 4 条预测均给出实测；新增"畸形断言抛错"有 `test_evidence_block_rejects_malformed_stdout_assertion` 落证 |
| 3 | 对抗自检（两条） | **成立（第一轮为"不成立"）** | 见 §3.5：我亲手完整取反 → 3 failed 含 PRD 点名用例；非法 JSON 那条第一轮已确认成立 |
| 4 | 对锁定契约的 diff（3 条） | **证据充分** | 旧 manifest 解析、旧 LLM 输出静默空 tuple、`key_paths` 默认 `()` 退化，三项均有负控测试 |
| 5 | 低风险门禁（arch / lint --full / lint --reuse / test） | **证据充分** | 本轮四项全部重跑，全绿（§3.2/3.3/3.6） |

### 2.2 Human-Confirmed（3 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| 补丁 1 `EvidenceBlock.stdout_assertions` + 关键词断言 | **证据充分** | `structured_evidence.py`（schema 解析）+ `agent_runner_validation.py:543`（`_is_stdout_assertion_satisfied`）/`:565`（`_validate_stdout_assertions`）/`:674`（插入点）；rv-1 正例 REJECTED / 负控 PASSED |
| 补丁 3 `build_supervisor_prompt` 分层 diff | **证据充分** | `pr_supervisor.py:504` 调 `_build_layered_diff`，原 `diff_text[:6000]` 已删除；rv-4 两组用例 + 同文件内负控 |
| 补丁 4 跨 cycle finding 累积 | **证据充分** | `_load_previous_findings` / `_persist_findings` 在 `run_post_pr_supervisor_cycle` 首尾成对；cycle1→cycle2 端到端用例本轮仍绿 |

### 2.3 Architecture Acceptance（4 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| 新增逻辑全在 `core/use_cases/`，无跨层导入 | **证据充分** | diff 中 core 侧只有 3 个 use_case 文件 + 1 个 shared models；`FindingDetail` 在 `core/shared/models/` |
| `check_architecture.py` exit 0 | **证据充分** | 本轮复跑：241 文件扫描，"✅ 架构依赖方向全部合法，无违规" |
| dataclass / Pydantic settings / factory 三处同步 | **证据充分** | 本轮 grep 复核**五处**齐全且默认值一致：`models:617-621`、`settings:378-382`、`factory:484-489`、`config.toml:436-442`、`.iar.toml:209-215` |
| artifact 落 `.iar/state/issue-<N>/` 且被 gitignore | **证据充分** | `git check-ignore -v .iar/state/issue-1/findings.json` → `.gitignore:90:.iar/` |

### 2.4 Dependency Acceptance（3 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| 零新 npm 依赖 | **证据充分** | `git status` 中无 `frontend-*/package.json` |
| 零新 Python 依赖 | **证据充分** | `git diff --stat HEAD -- pyproject.toml uv.lock` 输出为空 |
| 不引入新 I/O 抽象 | **证据充分** | 复用 `IProcessRunner` / `CommandResult`；artifact 读写用普通 `Path`，与 `_rv_reexec_cache_relpath` 同模式 |

### 2.5 Behavior Acceptance（6 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| exit 0 但 stdout 缺关键词 → `ValidationEvidenceError`（rv-1） | **证据充分** | 真实 subprocess 复跑（第一轮）+ 本轮变异反证（§3.5） |
| 旧 evidence.json 仍解析（rv-2） | **证据充分** | 正例 PASSED + 负控非法 JSON REJECTED |
| 关键路径文件 diff 全量出现（rv-4） | **证据充分** | 5 个 `CORE-<n>-`+900 字符逐字节完整；负控 `key_paths=()` 时被 2000 预算截掉 |
| cycle N prompt 含 cycles 1..N-1 未解决 findings（rv-5） | **证据充分** | prompt 含 `Previous unresolved findings from cycles 1..1:`；负控不传时不含该段 |
| artifact 落盘位置与 gitignore | **证据充分** | `test_supervisor_finding_tracker_persists_and_loads_findings` 断言 `<tmp>/.iar/state/issue-42/findings.json` 真落盘 |
| 现有 PRD 协议兼容 | **证据充分** | 新增字段全部带默认值；2105 全绿可作证 |

### 2.6 Frontend Acceptance（1 项）

`No frontend impact` — **证据充分**：diff 无 `frontend-*` 文件，PRD 未要求视觉证据。

### 2.7 Documentation Acceptance（3 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| `docs/guides/agent-runner.md` 加 stdout_assertions 用法 | **证据充分** | `agent-runner.md:2492` `#### evidence.json 的 stdout_assertions（防假绿灯）`，含 JSON 示例与字段说明 |
| 同文档加分层 diff + finding 累积说明 | **证据充分** | `agent-runner.md:1210` `#### Supervisor diff 分层注入与跨 cycle finding 累积`，含 toml 示例 |
| `docs/ai-standards/testing.md` 加 oracle 示例 | **证据充分** | `testing.md:221` `### 完成度判定加固类改动的 oracle 示例`，含正例/负控/expected_fail |

（`mkdocs.yml` 无需改动：只改既有页面，未新增文档页。）

### 2.8 Validation Acceptance（5 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| 全量 pytest exit 0，tests >= 2100（rv-7） | **证据充分** | 本轮复跑 `2105 passed in 76.21s` |
| rv-1/2/4/5/7 跑绿，evidence 落证据目录 | **证据充分（附条件）** | 五条 oracle 均绿；落点是 `tasks/evidence/<PRD>/` 而非 PRD 字面的 `.iar/evidence/`——见 O-2（实际做法更对，建议归档时改 PRD） |
| `rg key_paths\|stdout_assertions` 三处出现、无 typo | **证据充分** | 本轮 grep 复核，字段名全仓一致 |
| `check_architecture.py` exit 0 | **证据充分** | 已复跑 |
| `just lint --full && just lint --reuse && just test` 全绿 | **证据充分** | 本轮三项全 Passed |

### 2.9 Delivery Readiness（4 项）

| 条目 | 判定 | 依据 |
|---|---|---|
| 3 个补丁全部落地，无 Phase 拆分 | **证据充分** | 补丁 1/3/4 在同一次 diff 内，无 TODO / 无 feature flag |
| 无未批准的并行抽象 | **证据充分** | finding tracker 未单开 module，全放 `pr_supervisor.py`（符合 D-06） |
| 无 open regression blocker | **证据充分** | 2105 全绿 + 4 项门禁绿 |
| 跑真实 issue 端到端（rv-6, opt-in） | **不适用（未执行，已如实记账）** | PRD §7.6 自标 `required_for_acceptance: false`；`evidence-report.md:100` 已明确记为"opt-in **未执行**"，并声明 Delivery Readiness 第 4 条不打勾。**第一轮复审建议第 3 项已落实** |

---

## 3. 本轮我独立复跑的命令与关键输出

### 3.1 变异前基线（确认代码已还原）

```
$ grep -rn "ADVERSARIAL" src/ tests/ | wc -l
0
$ uv run pytest -o addopts="" tests/test_agent_runner_validation.py -k stdout -q
3 passed, 115 deselected in 0.04s
$ shasum -a 256 src/backend/core/use_cases/agent_runner_validation.py
d4a55bc31259aa0faf88119cb4337defd34d80e540a01364c309379aa184a9e3  ...
```

### 3.2 全量测试

```
$ uv run pytest -o addopts="" tests/ -q
2105 passed in 76.21s (0:01:16)
```
与实施者自述一致（PRD 记录基线 2091 + 新增 14）。

### 3.3 架构门禁

```
$ uv run python hooks/shared/check_architecture.py
🔍 正在检查架构依赖方向... 架构依赖检查 — 共扫描 241 个文件
✅ 架构依赖方向全部合法，无违规。   （exit 0）
```

### 3.4 依赖与漂移

```
$ git diff --stat HEAD -- pyproject.toml uv.lock frontend-public/package.json   → （空）
$ rg -n "6000|max_diff_chars" src/backend/core/use_cases/pr_supervisor.py
  240: def _truncate_diff_text(diff_text, max_diff_chars)
  254: （仅注释"补丁前整个 diff 被截断到 6000 字符"）
  504: max_diff_chars=config.post_pr_supervisor.max_diff_chars
```
字符预算唯一落点是 `max_diff_chars`，`6000` 仅剩注释一处。五处配置同步（§2.3）已 grep 复核。

### 3.5 对抗自检 —— **亲手复跑，B-1 解除**

按 PRD §7.6 rv-7 负控的字面要求，对 `agent_runner_validation.py:549` 做**整体取反**：

```python
# 原
return pattern_matched if assertion.must_match else not pattern_matched
# 变异后
return (not pattern_matched) if assertion.must_match else pattern_matched
```

```
$ uv run pytest -o addopts="" tests/test_agent_runner_validation.py -k stdout -q
FAILED tests/test_agent_runner_validation.py::test_ensure_validation_commands_pass_enforces_stdout_substring
FAILED tests/test_agent_runner_validation.py::test_ensure_validation_commands_pass_allows_matching_stdout
FAILED tests/test_agent_runner_validation.py::test_ensure_validation_commands_pass_enforces_stdout_must_not_contain
3 failed, 115 deselected in 0.08s
```

与实施者记录的 `adversarial-check.txt` **逐条一致**（三条全红，含 PRD 点名的 `enforces_stdout_substring`；失败原因 `Failed: DID NOT RAISE ValidationEvidenceError`）。这正好复现了我第一轮用 monkeypatch 得到的对照表第一行，也反证了旧解释"该用例对方向不敏感"不成立。

还原校验（四重）：

```
$ cp /tmp/_adv_backup.py src/.../agent_runner_validation.py
$ grep -rn "ADVERSARIAL" src/ tests/ | wc -l      → 0
$ shasum -a 256 src/.../agent_runner_validation.py
d4a55bc31259aa0faf88119cb4337defd34d80e540a01364c309379aa184a9e3   ← 与变异前逐字节相同
$ uv run pytest -o addopts="" tests/test_agent_runner_validation.py -k stdout -q
3 passed, 115 deselected in 0.04s
$ git status --porcelain                          → 与实验前完全一致（14 M + 1 A + 1 ??）
```

**判定**：rv-7 负控成立，补丁 1 最核心的 `must_match=True` 路径已被变异覆盖，B-1 解除。

### 3.6 lint

```
$ just lint --full   → ruff / ruff-format / PRD checklist / guidelines /
                       max-file-lines / architecture / guard-test-mod 全部 Passed
$ just lint --reuse  → jscpd / pylint-duplicate-code / architecture /
                       guidelines / max-file-lines 全部 Passed
```
（lint flag 写入落在仓库外，`git status` 前后一致。）

### 3.7 分组重跑

```
rv-4:   tests/test_pr_supervisor.py -k "layered or key_paths"                     → 2 passed
rv-5:   (pr_supervisor + supervisor_entrypoints) -k "previous or finding"         → 4 passed
补丁 1:  tests/test_agent_runner_validation.py -k "stdout or legacy_manifest"      → 4 passed
schema: tests/test_agent_runner_structured_evidence.py -k "stdout_assertions or legacy" → 4 passed
```

### 3.8 `new-tests.txt` 集合比对（N-1 解除）

```
$ git diff HEAD -U0 -- tests/ | grep "^+def test_" | sed ... | sort   → 14 条
$ sed -n '2,15p' new-tests.txt | sed 's/.*:://' | sort                → 14 条
$ diff <前者> <后者>                                                  → 无差异
```

新增 14 条（structured_evidence 4 + validation 4 + pr_supervisor 5 + entrypoints 1）全部是 `git diff HEAD` 的**新增行**，无既有用例混入；第一轮点名的 `test_parse_reviewer_decision_extracts_findings_array`（属 `tests/test_agent_review.py`，未被本次 diff 修改）**已从清单移除**。实施者声称属实。

### 3.9 工作区洁净度

实验前后 `git status --porcelain` 输出逐字节一致，仅本报告文件（唯一可写文件）内容变化。

---

## 4. 问题清单（第二轮状态）

### 阻塞

**B-1 · rv-7 负控未按 PRD 字面执行，且证据报告的解释有误** → **已解决**

- 对抗自检已按 PRD 字面重做：完整取反 → 3 failed（含 `test_ensure_validation_commands_pass_enforces_stdout_substring`），证据已替换进 `adversarial-check.txt`，与我亲手复跑结果逐条一致（§3.5）。
- 代码已还原，sha256 与变异前一致，相关用例恢复 3 passed，`grep ADVERSARIAL` = 0，工作区无残留。
- `evidence-report.md:67` 新增"更正记录"，明确写出首次只翻了 `must_match=False` 分支（结果 `..F`）并承认"该用例对方向不敏感"这句解释不成立；旧的错误解释已从证据报告中清除（我 grep `不敏感 / 方向翻转 / stdout 为空`，证据报告内仅剩更正记录中作为被否定对象的引用）。

当前无阻塞项。

### 非阻塞

**N-1 · `new-tests.txt` 混入既有用例** → **已解决**（见 §3.8）

**N-2 · `agent_runner_supervisor.py` 未按 §7.2 改动，但 FR 层面满足** → 维持
PRD §7.1/§7.2 写"由 `_run_supervisor_with_repair_loop` 调 `_load_previous_findings` / `_persist_findings`"，实际两个调用都落在 `run_post_pr_supervisor_cycle` 内（`pr_supervisor.py:1178` / `1284`）。这与 FR-8 / FR-10 字面一致，且 `agent_runner_supervisor.py:125` 与 `review_once.py:207` 两个调用方自动受益，语义等价。属 PRD 内部表述不一致，实施取了更合理的一边。建议归档时修订 PRD §7.2，不改代码。

**N-3 · 关键文件 diff 无字符预算上限** → 维持
`_build_layered_diff` 对命中 `key_paths` 的文件逐字节全量输出，不受 `max_diff_chars` 约束。若下游把 `key_paths` 配成 `["src/"]` 这类宽前缀，prompt 体积会失控（补丁前 6000 字符是硬上界）。现有缓解只有"零命中打 WARNING"。配置驱动下的用户自担风险，PRD §12 风险 4 预见了"前缀写错"但未覆盖"前缀过宽"。建议后续加软上限，不阻塞。

### 观察项

**O-1 · 默认配置下 prompt diff 段不再逐字节等价旧行为** → 维持
`key_paths=()` 时输出变为 `Changed files (N):` 清单 + 截断 diff，比补丁前多一份**无长度上限**的文件清单。功能上是改善，但 FR-6 写的"退化为现行为"严格说不成立。2105 全绿未受影响，仅作记录。

**O-2 · 证据落点与 PRD 字面不符** → 维持
PRD §9 写"已记录到 `.iar/evidence/rv-1.txt`"，实际落在 `tasks/evidence/<PRD>/`。实际做法更对（`.iar/` 被 gitignore，写那里等于证据不进版本库）。建议归档时把 PRD 该句改为 `tasks/evidence/`。

**O-3 · worktree 内有本次无关的文件** → 维持
`tasks/pending/P1-PERF-20260916-102117-iar-console-dashboard-snapshot-sync.md` 处于 `A`（已 staged）状态，与本 PRD 无关，疑似另一会话产物。本轮 `git status` 复核仍然存在。**归档本 PRD 时必须确认它不被一并提交。**

---

## 5. 两处偏差裁决（维持第一轮结论）

### 5.1 未实现 `[validation].stdout_assertions_enabled` 开关 —— 可接受，非阻塞

PRD 内部矛盾：§3 列了该开关，但 §6 明确写"不引入新配置开关（直接用 evidence manifest 自描述）"，§10 FR-1/FR-2 也没有它。§6 是机制裁决、FR 是绑定需求，两处一致地不要求该开关；manifest 自描述已等价提供 opt-in（rv-2 已验证未声明即不检查）。**按 §6 / FR 执行正确。** 建议归档时把 §3 这一行删掉或标注"未实现，见 §6"。

### 5.2 rv-6 未执行 —— 可接受，已按"未执行"记账

PRD §7.6 自标 `required_for_acceptance: false`，`mock_boundary` 写明无 token 标 opt-in，Failure triage 也说"在无凭据环境下标 opt-in"。实施者已在 `evidence-report.md:100` 明确记为"opt-in **未执行**"，并声明 Delivery Readiness 第 4 条不打勾——**记账正确**。真正的端到端行为已由 `test_supervisor_loop_injects_previous_findings_into_cycle_2_prompt`（连续两轮跑真实 `run_post_pr_supervisor_cycle` + 真落盘 artifact）与 rv-1 真实 subprocess 复跑覆盖。建议 follow-up 里补一次带 token 的 rv-6。

---

## 6. 最终裁决

| 第一轮复审建议 | 落实状态 |
|---|---|
| 1【阻塞】按完整取反重做 rv-7 负控，并更正错误解释 | **已落实**，我亲手复跑验证（§3.5） |
| 2【顺手】从 `new-tests.txt` 移除 `test_parse_reviewer_decision_extracts_findings_array` | **已落实**（§3.8） |
| 3【顺手】rv-6 明确记为"opt-in 未执行" | **已落实**（§2.9 / §5.2） |
| 4【可选】归档时修订 PRD §3 / §7.2 / §9 表述 | 未做，属归档阶段事项，不阻塞验收 |

**结论：PASS。** 建议进入归档流程，归档时同步处理 O-2 / O-3 与 N-2（均为 PRD 表述或提交范围问题，不需改代码）。
