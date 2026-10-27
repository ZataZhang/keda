# 独立 verifier 报告：认领 Issue 时留存原始任务输入快照

PRD：`tasks/archive/P2-FEAT-20261022-133921-issue-input-snapshot-at-claim.md`

**最终结论：PASS**（第二轮复核，见下）。

本文件保存独立 verifier 的两轮复核结果，**原文照录，不做删改**。两轮由同一个 verifier 完成，约束均为只读（不改仓库与 GitHub，临时文件写在 scratchpad 下）。执行者的响应见 PRD 的 Final Reconciliation。

| 轮次 | 被复核的对象 | 结论 | 发现 |
|---|---|---|---|
| 第一轮 | HEAD `5f0c678a`（快照功能与导出器） | PASS | 0 BLOCKER、5 MEDIUM、4 LOW；变异检查 8 个中 1 个存活（M7） |
| 第二轮 | HEAD `8d8f7153`（对第一轮发现的修复）+ 改正后的证据与 PRD | **PASS** | 0 BLOCKER、0 MEDIUM、2 LOW；新增与修改的变异全部变红 |

第二轮的两条 LOW 在归档前已处理：`rv-exporter-pytest.txt` 改为带用例名的 `-v` 输出；本报告落盘。

---

## 第一轮（针对 HEAD `5f0c678a`）

### 结论

**PASS**。实现符合 FR-1 到 FR-7，我亲自复跑的测试和独立计算的 SHA-256 都支持非人工验收项，没有 BLOCKER。证据文件有几处夸大或失实（见 MEDIUM），建议在交给人审前修正。

### 逐项核对

| §9 条目要点 | 核实方式 | 结果 |
|---|---|---|
| 快照文件存在，`schema_version` 为 1，最早一行 `body_sha256` 等于认领时正文的 SHA-256 | 亲自核实 | 本机文件恰好 1 个非空行。我用 `python -I` 独立读取并计算：快照 `body` 的 SHA-256 为 `708f642d…b2d1`，与记录值、`issue-body-original.md` 的 SHA-256 完全一致，字节全等（1761 字符）。字段集合含 `schema_version` 等 13 项，没有评论字段和评判类字段。标签为 `['type/bug','priority/P1']`，不含 `agent/running`。 |
| 同正文行数恒为 1；改正文后追加且旧行字节不变 | 亲自核实 | 对应用例通过，M1 和 M5 变异使其变红。"真实多阶段运行期间始终 1 行"只是该文件现状为 1 行，我无法回看运行过程。 |
| 复用旧工作目录时 `base_sha` 是分叉点且不等于 `head_sha` | 亲自核实 | 测试用真实 git 仓库，M3 变异使其变红。 |
| 写失败照常执行并有 warning | 亲自核实，但覆盖弱于 oracle | 用例通过，M2 变异使其变红。但该用例只调用 `record_issue_input_snapshot`，没有驱动 `_process_ready_issue`，与 §7.6 rv-3 的 `real_entry` 不符（见 MEDIUM）。 |
| 导出器输出 `snapshot_at_claim` 和 `edited_since_claim`；旧 Issue 不变 | 部分亲自核实 | 30 个导出器用例通过。"74 个共同样本 73 个一致"依赖证据文件。 |
| 新代码无跨层导入 | 亲自核实 | 对快照模块 grep `backend.infrastructure/api/console`，无输出。 |
| handlers 非空行小于 1000 | 亲自核实 | 853 行。 |
| 数据库版本未变 | 亲自核实 | `_SCHEMA_VERSION = 15`，`c112e7cb~1` 时也是 15。 |
| 快照路径由 `state_home()` 派生，项目不被弄脏 | 部分亲自核实 | 代码路径和 `test_snapshot_does_not_touch_the_processed_repository` 已核对。运行前后的 `git status` 证据文件自己承认没留档，我也无法核实。 |
| 读取方跳过未知版本并告警 | 亲自核实 | 对应用例通过。 |
| 无前端影响 | 亲自核实 | `git diff --name-only c112e7cb~1 HEAD` 里前端文件数为 0。 |
| 文档同步 | 部分亲自核实 | 两份文档命中快照与 `provenance` 的关键词，我没有逐字核对全部要点。 |
| `mkdocs build` 无报错 | 亲自核实 | 输出到临时目录，无报错输出。 |
| 真实 `kc run --issue 306` 验证落盘 | 亲自核实产物 | 文件内容、SHA-256 和路径都核对了。我无法重放运行过程，但认领评论时间（06:51:57）早于 `captured_at`（06:53:34），顺序一致。PR #307 经 `gh` 确认为 MERGED，分支为 `issue-306`。 |
| 42 个用例全通过 | 亲自核实 | `uv run pytest … -q --no-testmon` 得到 `42 passed`。其中快照 12 个，导出器 30 个，与证据一致。 |
| 证据绑定的代码树 | 亲自核实 | `HEAD^{tree}` 为 `41d8e69b…`；`git diff c112e7cb HEAD` 对快照模块和 handlers 为空。 |
| `just test` / `just lint --full` 通过 | 无法核实 | 我没有运行。 |
| 独立 verifier 报告 `…verifier-report.md` | 无法核实 | 该文件在证据目录里不存在，条目被勾选时它还没生成（见 LOW）。 |
| 完成消息逐字携带 9.1 内容 | 无法核实 | 完成消息不在我可见范围内。 |

### 变异检查

我在 `verifier-tmp/copy` 副本里做变异，用 `PYTHONPATH` 指向副本，没有改动工作目录。每个变异后都恢复原文件，恢复后 42 passed。

| 变异 | 红的用例 |
|---|---|
| M1：相同正文也追加（`if False`） | `test_unchanged_body_is_not_appended_and_edited_body_is_appended_without_touching_old_lines` |
| M2：写失败向外抛（`except` 改为只捕获 `ZeroDivisionError`） | `test_snapshot_failure_only_warns_and_never_raises` |
| M3：`base_sha` 改成 HEAD | `test_snapshot_records_original_body_and_fork_point_not_head`、`test_stack_sequencing_uses_upstream_issue_branch_as_base` |
| M4：`stack` 判断失效 | `test_stack_sequencing_uses_upstream_issue_branch_as_base` |
| M5：追加模式改成 `"w"` | 3 个：`…appended_without_touching_old_lines`、`test_body_that_changes_back_is_appended_again`、`test_corrupt_last_line_is_treated_as_changed_and_old_lines_survive` |
| M6：handlers 不再调用快照 | `test_ready_handler_records_snapshot_after_claim_and_before_agent_starts` |
| M8：`repo_id` 清洗放过 `/` | `test_snapshot_path_lives_under_state_home_and_sanitizes_repo_id` |
| **M7：导出器取最后一条快照而不是最早一条** | **没有变红，42 个全过（存活变异）** |

### 发现

**BLOCKER：无。**

**MEDIUM**

1. **导出器"取最早快照"没有测试保护。**
   - M7 把 `earliest_snapshot = snapshots[0]` 改成 `snapshots[-1]` 后测试照样全过。
   - 原因是 `tests/test_dataset_export.py` 的 `_write_snapshot` 每次只写一条快照，从未构造"两条正文不同的快照"的场景。
   - 这是 FR-5 的核心语义（正文取最早一行），也是 `edited_since_claim` 的参照点。
   - `captured_at` 排序同样没有覆盖。

2. **rv-3 的实际测试弱于 §7.6 的 oracle。**
   - oracle 要求"驱动 `_process_ready_issue` 的真实编排到 Agent 调用"，并断言 Agent 被调用、函数正常返回。
   - 实际的 `test_snapshot_failure_only_warns_and_never_raises` 只在 `record_issue_input_snapshot` 层测试，也没有断言"无快照文件产生"。
   - 处理器层面的"不阻断"靠函数内部 `except Exception` 间接保证，没有被测试直接锁住。
   - Final Reconciliation 没有报告这个差异。

3. **`rv-1` 声称"当前 GitHub 正文与保存的原文一致：True"，实际不成立。**
   - 我用 `gh issue view 306` 取到的正文是 1762 字符，SHA-256 为 `18c6653f…`，末尾多一个 `\n`。
   - 保存的原文是 1761 字符。两者只有 `strip()` 后相等，字节不等。
   - 快照本身与原文字节全等，所以"快照正确"这一结论不受影响。
   - 但"还原并核对一致"的表述失实。如果现在重跑 rv-4 的 `--with-gh` 导出，`edited_since_claim` 会因这个换行变成 `true`，rv-4 的结果因此无法按原样复现。

4. **`human-review-checklist.md` 的"逐字摘自证据文件"里有一条不是原文。**
   - 其中"快照字段清单：`schema_version`、`captured_at`……（另有 `body`）；没有评论字段"在 `rv-1-snapshot-file.md` 里 grep "字段清单" 为 0 次命中。
   - 这是执行者自己写的归纳，却标成了摘录，而且是给人做"决策二"依据的那一条。
   - 此外，`rv-1` 声称"快照文件原文"，但展示的 JSON 里省略了 `body` 字段，只在文末附了一句话。

5. **rv-4 与 §7.6 的 oracle 不一致，Final Reconciliation 只报告了 rv-1 的偏离。**
   - oracle 要求"fixture console.db、假 gh、`--output`"，并要求并列显示快照正文与 GitHub 现状正文。
   - 实际用的是真实 `console.db` 和真实 `gh`；样本摘录里也没有 `body` 字段，无法"并列显示"。
   - "没有临时加的那行字"这一自验项，从摘录里看不出来。
   - "74 个共同样本 73 个一致"所依据的"改动前导出"没有保存在证据里，只能依赖说法。

**LOW**

6. **`repo_id` 为 `".."` 时路径会逃出专用子目录。**
   - `_UNSAFE_DIRNAME_PATTERN` 放行 `.`，`snapshot_file_path(repo_id='..')` 实测得到 `…/dataset/issue-snapshots/../issue-1.snapshots.jsonl`。
   - 只上跳一级，仍在 `dataset/` 下，且实际 `repo_id` 来自 registry，不太可能触发。
   - 测试只覆盖了 `"../evil/repo"`。

7. **PRD 机制描述与实现有小差异，且未入 Final Reconciliation。**
   - §Proposed Solution Summary 写"外层 `try/except Exception` 在接入点"，实际 `try/except` 在 `record_issue_input_snapshot` 内部。
   - 接入点位置是 `get_current_branch` 之后，不是 `get_head_sha` 之后。
   - 两处都不影响行为。

8. **PRD 和检查清单指向的路径当前不存在。**
   - PRD 在 `tasks/pending/`，但清单与证据里的 `just prd review tasks/archive/P2-FEAT-…` 当前指向一个不存在的文件，我确认 `tasks/archive` 下没有该文件。
   - §9 里"独立 verifier 报告 `…verifier-report.md`"也已被勾选，文件却不存在。

9. **`is_edited_since_claim` 的边界。**
   - 它拿 GitHub 现状正文的哈希与最早快照比较。
   - 如果 GitHub 返回的正文与 runner 内存里的 `issue.body` 有空白差异（发现 3 里已经看到一次末尾换行的差异），会产生假阳性。
   - 这是设计取舍而非缺陷，仅作提示。

### 未能核实的部分

- 真实 `kc run --issue 306` 的运行过程，我只核对了落盘产物。
- 运行前后 `git status --porcelain`（证据自己承认没留档）。
- "整个运行期间始终 1 行"。
- rv-4 的"74 个共同样本 73 个一致"，以及 #293 因 PR 状态变化而不同的解释。
- `just test` 和 `just lint --full`。我只跑了：
  - 两个目标测试文件；
  - `-k "agent_runner or run_agent or daemon"` 的较大子集，全部通过，且 `~/.kedacode/dataset/issue-snapshots/` 下仍只有 `keda`，没有被测试污染；
  - mkdocs build。
- `tests/conftest.py` 的会话级 fixture 对全部测试的影响，我没有跑全量。
- 完成消息是否逐字携带 §9.1 的内容。
- 文档的逐字内容，我只做了关键词命中核对。

---

## 第二轮（针对 HEAD `8d8f7153`，复核第一轮发现的修复）

### 结论

**PASS**。上一轮的 MEDIUM 和 LOW 修复都经我亲自验证，新增和修改的变异全部变红；证据数字与"逐字摘自"现在都站得住；没有 BLOCKER。仍有两条 LOW，都不影响判定。

### 逐项核对

| 条目 | 核实方式 | 结果 |
|---|---|---|
| HEAD 与树 | 亲自核实 | HEAD 为 `8d8f7153`，`HEAD^{tree}` 为 `dcd33bec3baf97186cece61ea14cd9cdaa74456c`，与证据记录一致。工作目录里只有 PRD 的改动和证据目录未提交。 |
| 代码改动范围 | 亲自核实 | 相对 `5f0c678a`，`src` 与 `scripts` 只改了 `snapshot_file_path`（纯点号的 `repo_id` 加前缀 `_`）和 `is_edited_since_claim`（比较前去掉 `\r\n` 差异和末尾空白，并移除未使用的 `hashlib` 导入）。 |
| 两个目标测试文件 | 亲自核实 | `uv run pytest … -q --no-testmon` 得到 `48 passed`。快照 16 个，导出器 32 个，分别用 `--co` 核对，与证据一致。 |
| 较大测试子集 | 亲自核实 | `-k "agent_runner or run_agent or daemon"` 得到 `1721 passed, 3112 deselected`。跑完后 `~/.kedacode/dataset/issue-snapshots/` 下仍只有 `keda/issue-306.snapshots.jsonl`，没有被测试污染。 |
| 快照文件本身 | 亲自核实 | 1 个非空行。快照 `body` 与 `issue-body-original.md` 字节全等（3091 字节），SHA-256 为 `708f642d…b2d1`。 |
| Issue #306 已按字节还原 | 亲自核实 | `gh api repos/ZataZhang/keda/issues/306` 与 `gh issue view --json body` 取回的正文，用 `json.load` 后 `encode()` 与原文都是字节全等（3091 字节）。我上一轮看到的多一个换行已不存在。 |
| rv-1 新版 | 亲自核实 | 字段清单一行与实际 13 个字段一致，标签、`base_sha` 和 `head_sha` 与文件相符，快照 JSON 展示了完整 `body`。 |
| rv-4 与 legacy 比对 | 部分亲自核实 | `rv-4-legacy-comparison.md` 的行数核对为：一致 73、不同 1、共 74，与声称相符。并列 diff 的内容自洽。"改动前"导出文件不在仓库里，我无法重放，文件自己也如实写明了这一点。 |
| `human-review-checklist.md` 的引用 | 亲自核实 | 脚本逐条核对：表格行引用均能在 `rv-1-snapshot-file.md` 中逐字找到，PRD 原文引用在 PRD 中找到，`_SCHEMA_VERSION = 15` 在 `rv-arch-and-docs-checks.txt` 中找到。上一轮 grep 为 0 次命中的"字段清单"现在在 rv-1 里有真实行。 |
| rv-2-rv-3 证据 | 亲自核实 | 文件内 16 个 PASSED，含新处理器用例和 3 个参数化路径用例。 |
| PRD Final Reconciliation | 亲自核实 | 已如实写入以下几项：rv-1 与 rv-4 改用真实环境，rv-3 用例补强，`repo_id` 点号加固，"被编辑"判定收紧，`tests/conftest.py` 的隔离 fixture，以及无法事后重放的部分。我没有发现与实现矛盾或夸大的陈述。 |
| §9 勾选项指向的证据文件 | 亲自核实 | 引用的 `rv-1`、`rv-4`、`rv-4-legacy-comparison`、`rv-2-rv-3`、`rv-exporter-pytest`、`rv-arch-and-docs-checks`、`evidence-report` 都在证据目录里，只有 `…verifier-report.md` 缺失（见 LOW-2）。 |
| `just test`（111 passed）、`just lint --full`、mkdocs（最终树） | 无法核实 | 我没有跑 `just`。mkdocs 在上一轮的树上跑过，退出码 0，最终树未重跑。 |

### 变异检查

仍在副本 `verifier-tmp/copy` 里做，用 `PYTHONPATH` 指向副本，没有改动工作目录。副本基线为 48 passed，每个变异后都恢复原文件，恢复后仍是 48 passed。

| 变异 | 红的用例 |
|---|---|
| M7：导出器取最后一条快照（上一轮的存活变异） | `test_exporter_uses_the_earliest_of_several_snapshots_regardless_of_file_order` |
| M9：去掉 `is_edited_since_claim` 的归一化 | `test_whitespace_only_difference_is_not_an_edit` |
| M10：`snapshot_file_path` 放过 `..` | `test_snapshot_path_never_leaves_the_snapshot_area[..]` 与 `[.]`（`"../evil/repo"` 本来就不会逃出，所以只红两个） |
| M11：去掉 `record_issue_input_snapshot` 的 try/except，让异常外抛 | `test_ready_handler_continues_after_snapshot_failure`（处理器层）与 `test_snapshot_failure_only_warns_and_never_raises` |
| M12：排序后再反转快照顺序 | `test_exporter_uses_the_earliest_of_several_snapshots_regardless_of_file_order` |
| M13：去掉按 `captured_at` 排序 | `test_exporter_uses_the_earliest_of_several_snapshots_regardless_of_file_order` |

### 发现

**BLOCKER：无。**

**MEDIUM：无。** 上一轮的 5 条 MEDIUM 都已处理，下面是我对每条的复核：
- 最早快照的测试保护：M7、M12、M13 都被杀掉。
- rv-3 处理器层：新用例驱动 `_process_ready_issue`，并断言 Agent 被调用、有 warning、占位文件内容不变，M11 变异使其变红。
- #306 正文：已字节级还原并复核。
- 清单的伪"摘录"：现在每条引用都有出处。
- rv-4 的 oracle 偏差：PRD 已改写并写入对账，并列 diff 已补入。

**LOW**

1. **`rv-exporter-pytest.txt` 无法支撑 PRD 里的用例名。**
   - PRD §9 写"`rv-exporter-pytest.txt`（含多条快照取最早、仅空白差异不算编辑两个用例）"。
   - 该文件只有 48 个点号和 `48 passed`，没有用例名，grep 两个用例名得 0 次命中。
   - 用例确实存在且通过，我已亲自验证，只是这份证据文件本身不能证明。
   - 建议改用 `-v` 输出或引用 `rv-2-rv-3` 同样的格式。

2. **`…verifier-report.md` 当前不存在，但已被多处引用。**
   - PRD §9 的 Delivery Readiness、`evidence-report` 第 12 行都指向 `P2-FEAT-20261022-133921-issue-input-snapshot-at-claim.verifier-report.md`，证据目录里没有这个文件。
   - 它大概是要由本次复核结果生成，需要在交付前落盘，否则 §9 勾选项指向悬空。
   - 另外，PRD、清单和 rv-1 里的 `just prd review tasks/archive/…` 指向的归档文件也尚不存在，这是迁移前的预期状态。

此外有一个设计提示，不是缺陷：归一化后，仅有末尾空白或换行风格的差异不再算"被编辑"，PRD 的 FR-5 和 Final Reconciliation 已如实写明。

### 未能核实的部分

- `just test`、`just lint --full` 以及"111 passed"，我没有运行。我跑的是两个目标文件和 `-k "agent_runner or run_agent or daemon"` 子集。
- 最终树 `dcd33bec` 上的 `mkdocs build`。上一轮在旧树上验证过，这次没有重跑。
- 全量测试。`tests/conftest.py` 的会话级 fixture 对其余约 3100 个未选中用例的影响没有验证。
- 真实 `kc run --issue 306` 的运行过程，以及"认领那一刻的 GitHub 正文"，只能依赖创建与认领之间无编辑的说法，证据已如实声明。
- "改动前的导出"文件，我没有，因此 73/74 的逐样本一致性只核对了表格自洽，没有重放。
- 运行前后 `git status --porcelain`，证据自己承认没有留档。
- 完成消息是否逐字携带 §9.1 的内容。

---

## 执行者对第二轮两条 LOW 与"未能核实"的处理（附注）

- LOW-1：`rv-exporter-pytest.txt` 已改为 `-v` 输出，现在包含 48 个带用例名的 `PASSED`，两个被点名的用例（`…earliest_of_several…`、`…whitespace_only_difference…`）均在其中。
- LOW-2：本文件即该报告，随归档一并提交；`tasks/archive/…` 下的 PRD 在同一次提交里迁移。
- "最终树 `dcd33bec` 上的 `mkdocs build`"：执行者已在该树上运行，退出码 0（见 `rv-arch-and-docs-checks.txt` 之后的运行记录与 `evidence-report`）；verifier 本人未重跑，此处如实标注为"由执行者核实、verifier 未核实"。
- `just test`（111 passed）与 `just lint --full`：执行者在该树上运行并通过；verifier 未运行。
