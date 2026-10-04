# 证据报告：归档与确认语义分离——执行侧完成即归档、人工验收独立记录

> PRD：`tasks/pending/P1-FEAT-20261003-224854-archive-confirmation-separation.md`，判据以 PRD §7.6 为准。
> 同目录的 `….verification-plan.md` 说明怎么跑。
> 原始日志放在同目录的 `raw/` 下。`.gitignore` 规定它们只留在本机、不进提交。下文引用的记录都从这些日志原样摘出。
>
> **独立 verifier 本轮没有结论。** 第 1 轮复核中途被中断，用户 2026-10-04 决定推迟到归档那一轮再跑，结论届时写入同目录的 `….verifier-report.md`。因此本报告目前只是**执行者自证**，PRD 里一项都还没勾。

## 人审导航

你只需要看 PRD §9.1 的两项：rv-1 和 rv-5。两份记录的原文已经嵌在下文，不用另开文件。想看原始日志的话，下表里的命令可以直接执行。

| # | 看什么 | 打开方式 | 逐项期望值 | 状态 |
|---|---|---|---|---|
| 1 | rv-1：PRD 只剩人审空框、横幅为 🧍，交付时就进了 archive，空框和横幅都原样保留 | 本报告「rv-1：交付前后的真实 git 记录」一节；原始日志：`open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/raw/rv-1-delivery-archives-awaiting-human.log"` | ① after 段的 `git status --short` 为 `R  tasks/pending/example.md -> tasks/archive/example.md`<br>② `ls -A tasks/pending` 为 `(empty)`<br>③ 归档后的横幅行以 `> 🧍 **验收状态**：待人工验收` 开头<br>④ `### Human-Confirmed` 下仍是 `- [ ] decision 1: answered by a human` | ✅ 4 passed |
| 2 | rv-5：自动合并遇到已归档、人审未答的 PR，只会"等人验收"；人回答后才继续 | 本报告「rv-5：自动合并两轮记录」一节；原始日志：`open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/raw/rv-5-merge-queue-hold.log"` | ① 第一轮 `outcomes=['skipped_human_review']`<br>② github calls 里没有 `merge_pull_request`<br>③ `process calls: []`<br>④ 第二轮前，PRD 横幅为 ✅、人审项为 `[x]`<br>⑤ 第二轮 `outcomes=['merged']`，process calls 含 `git rebase origin/main` | ✅ 6 passed |
| 3 | 负控：同一批用例在未修改的源码上是红的 | 本报告「负控与判别矩阵」一节；原始日志：`open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/raw/negative-control-rv1-rv5.log"` 与 `open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/raw/baseline-discrimination.log"` | ① 实现前红跑结果为 `2 failed`<br>② rv-1 读归档文件时报 `FileNotFoundError`<br>③ rv-5 的断言 `['merged'] == ['skipped_human_review']` 失败<br>④ 判别矩阵为 `27 failed, 123 passed`：应变的 27 条全红，应保持不变的全绿 | ✅ |
| 4 | PR 与 CI | PR：<https://github.com/ZataZhang/keda/pulls?q=is%3Apr+head%3Afeat%2Farchive-confirmation-separation><br>CI：<https://github.com/ZataZhang/keda/actions?query=branch%3Afeat%2Farchive-confirmation-separation> | ① PR 叠在 ZataZhang/keda#184 之上（#184 又叠在 ZataZhang/keda#183 之上）<br>② ZataZhang/zata-codes-template#27 合并前，CI 有 8 个横幅用例**按设计失败**：CI 从模板 main 安装 skill，当前仍是 v4。其余用例全绿 | ⏳ 等 (b) |

自己复跑 rv-1、rv-5 的命令（打印的记录与下文一致）：

```bash
cd /Users/zata/code/keda-worktrees/feat/archive-confirmation-separation && IAR_PRD_SKILL_PATH=/Users/zata/code/zata_code_template/skills/prd/SKILL.md .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider --no-header -v -rP tests/test_agent_runner_prd_delivery.py::test_delivery_archives_prd_awaiting_human_review_real_git tests/test_agent_runner_merge_queue.py::test_archived_prd_awaiting_human_holds_until_the_human_answers
```

**已替你核对过的内容**

- **两项人审 oracle 都走真实入口。**
  - rv-1 用的是真实 git 仓库、真实 `SubprocessRunner` 和真实 skill 脚本，没有对 `parse_prd_contract` 打桩。
  - rv-5 走 `process_merge_queue` 公开入口，PRD 是 worktree 里的真实文件。
- **负控有两层。**
  - 实现前，在未改动的 `src/` 上红跑一次。
  - 再把最终版测试拿到未修改的源码上跑一遍：红的恰好是行为发生变化的用例，行为不变的用例照样绿。
- **对抗自检逐条通过**，详见「对抗自检」一节：
  - 失败交付永不归档；
  - 两条既有发布例外没有扩大；
  - 横幅检查只在 skill 读不出横幅时跳过；
  - ⬜ 永远不可归档。
- **锁定契约未变。** PR 正文的 marker 与小节标题逐字未变；`DeliveryGateFailureKind` 只新增了一个成员。
- **门禁全部通过。**
  - 全量 pytest（v5 skill）：`2786 passed, 1 skipped`。
  - 架构守卫、ruff、`mkdocs build --strict` 通过。
  - 本次触及文件的行数上限通过。
  - 没有依赖、schema 或前端改动。
- **证据绑定同一棵最终代码树**，见「绑定最终树」一节。证据收集完之后，`src/`、`tests/`、`docs/`、`hooks/` 都没有再改。

**还没做完、需要你推进的**（详见「尚未完成」一节）：

1. 按 §8 Notes 的顺序合并：先 ZataZhang/keda#183，再 ZataZhang/zata-codes-template#27，再 ZataZhang/keda#184。
2. 执行 `iar init`，安装 v5 skill。
3. 在不设 `IAR_PRD_SKILL_PATH` 的环境下复跑同一批命令。
4. 运行独立 verifier，拿到 PASS。
5. 勾选证据充分支持的执行侧条目（本轮一项未勾），在本 PR 里把 PRD 带着 🧍 归档。
6. 最后合并本 PR。

## 绑定最终树

**主身份：`verified_tree_sha = 81b833554c9c8be9481f21fa7d80a88a2c204468`。**

- 这是 PRD skill `references/pr-evidence-and-merge-acceptance.md`「Evidence Identity」定义的 record-excluded tree：PR head 的 tree 去掉三条 delivery-record 路径后得到的结果。三条路径是 `tasks/pending/<prd>.md`、`tasks/archive/<prd>.md`、`tasks/evidence/<prd-stem>/`。
- PR 的根 tree 包含本报告与 PRD 自身，没法把自己的 id 写进自己，所以才排除这些路径。
- 之后勾选、Final Reconciliation、改横幅、归档移动、Change Log 都只改这些路径，因此都不会让证据过期。改了这些路径之外的任何文件，证据就过期。
- 计算时用临时 index，不碰工作区。下面 `<head>` 是要核对的那次 PR head 提交：

```bash
cd /Users/zata/code/keda-worktrees/feat/archive-confirmation-separation && tmp_index=$(mktemp) && GIT_INDEX_FILE="$tmp_index" git read-tree <head> && GIT_INDEX_FILE="$tmp_index" git rm -r -q --cached --ignore-unmatch -- tasks/pending/P1-FEAT-20261003-224854-archive-confirmation-separation.md tasks/archive/P1-FEAT-20261003-224854-archive-confirmation-separation.md tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation && GIT_INDEX_FILE="$tmp_index" git write-tree; rm -f "$tmp_index"
```

收集证据时，PRD 与证据目录都还没被跟踪，所以这个值就是暂存区的 `git write-tree`。

补充记录 4 棵代码子树的 id。提交后 `git rev-parse HEAD:src` 等命令的结果必须与下表逐一相等：

| 子树 | 最终 id（全部证据所在的树） | base `1b8afed9` 上的 id |
|---|---|---|
| `src/` | `fc77f58eeee18dffb77f97940b00d3772b042cb1` | `74941e60c2c8ec861b0c7f96abd026415e7831d7` |
| `tests/` | `d327fd22027abf0a972b5180f1a55c0c5faf0251` | `21e431611aaae6a413878db17134e8b823b11211` |
| `docs/` | `dd1b2326b459c2a4619051ceb61f94c3a18c79a8` | `19083c03b7d61a8216c48c5921d90515ae7e4d4c` |
| `hooks/` | `540b06dc0c27f0f93259dbbd2c93c6389b3c7023` | `6f18c7c09ad28bc579e0f51a10b6b8dfacab2e55` |

- **base** 是 `1b8afed9c0da6b7128ea346e9363b9c38a7e4513`，即 ZataZhang/keda#184 的 head。它的 `src/` 与 `main`（`b14573b4`）只差 `prd_machine_contract.py` 一行，即 §8 Notes (a)。本次改动的 8 个模块在 base 上与 `main` 逐字相同。
- **被测 skill** 是模板仓 `skills/prd/SKILL.md` @ `2bf2de8`（`Machine-Contract-Version: 5`），即 ZataZhang/zata-codes-template#27 的 head。这份工作副本与该提交逐字一致（`prd_contract.py` 的 sha256 相同）。它经 `IAR_PRD_SKILL_PATH` 指定，测试照样执行真实的 `prd_contract.py --json`。
- **每份原始日志的首行**都写有当次运行的 `src/` 与 `tests/` 子树 id，并标明暂存区与工作区一致。

## Oracle 结果

| id | tier | 审阅 | 用例（真实入口 / mock 边界） | 结果 | 原始日志 |
|---|---|---|---|---|---|
| rv-1 | R2 | 人 | `test_delivery_archives_prd_awaiting_human_review_real_git`：真实 git、真实 `SubprocessRunner`、真实 skill，依次调用交付检查与发布前检查。另有 3 个 fake-runner 用例，钉住 `git add` + `git mv` 的调用序列和两道门禁都放行 | 4 passed | `raw/rv-1-delivery-archives-awaiting-human.log` |
| rv-2 | R1 | verifier | `test_delivery_keeps_prd_pending_while_an_executor_item_is_open_real_git`，入口同 rv-1。断言 `CHECKLIST_UNCHECKED`、信息含 `- [ ] rv-2: executor-owned item 2` 且不含人审项、PRD 未移动 | 1 passed | `raw/rv-2-executor-open-stays-pending.log` |
| rv-3 | R1 | verifier | 真实 git 参数化 4 例：交付检查拦下；随后模拟 agent 违规 `git mv`，发布前检查同样拦下，两道都是 `ACCEPTANCE_BANNER_MISMATCH` 且 `is_closeout_eligible`。closeout 1 例：经 `run_agent_until_committed` 真实循环，agent 与 git 用 fake，断言收尾指令文本与修复后归档。另有字段 4 例 | 9 passed | `raw/rv-3-banner-mismatch.log` |
| rv-4 | R1 | verifier | `push_changes(require_prd_archived=True)` 参数化 3 例：已归档只剩人审空框 → 放行并推送；仍在 pending → 拒绝，信息为 "has not been archived yet"；已归档但执行侧未完成 → 拒绝并点名条目。推送由 `FakeProcessRunner` 记录，PRD 是真实文件 | 3 passed | `raw/rv-4-publish-requires-archive.log` |
| rv-5 | R3 | 人 | `process_merge_queue` 两轮，另有"位置 × 标记"参数化 5 例。GitHub 与 git 用 fake；`create_or_reuse_worktree` 沿用该测试文件既有的 `_stub_worktree` 夹具，返回预置的 worktree 目录（oracle 允许的 git 边界）。**`resolve_prd_worktree_path` 与 `parse_prd_checklist` 均未打桩** | 6 passed | `raw/rv-5-merge-queue-hold.log` |
| rv-6 | R1 | verifier | `tests/test_agent_runner_pr_body_contract.py` 整文件：纯函数，加上 `create_draft_pr` 发布路径 | 29 passed | `raw/rv-6-pr-body-contract.log` |
| rv-7 | R1 | verifier | 第一组：真实 git，`git status --porcelain` 恰为一条 rename 且字节相同。第二组：在测试边界把 `parse_prd_contract` 打桩成去掉 `acceptance_status` / `human_unchecked` 的 v4 形状，横幅为 ⬜ 也照常归档。第三组：`publish_changes(require_prd_archived=False)` 参数化 3 例（与 rv-4 同一组情形）：`git add` 与 `git mv` 都没有发出，PRD 留在原处、内容逐字不变，推送照常发出 | 5 passed | `raw/rv-7-compat.log` |
| FR-6 | — | verifier | `tests/test_agent_runner_prompt_contract.py` 整文件，包括新增的提示词语义断言与旧措辞负断言 | 11 passed | `raw/fr-6-prompt-contract.log` |

各 oracle 的定向命令统一为：

```bash
IAR_PRD_SKILL_PATH=/Users/zata/code/zata_code_template/skills/prd/SKILL.md .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider --no-header -v <targets>
```

`-o addopts=''` 关掉仓库默认的 `--testmon`，保证每条用例真的执行，不被增量选择跳过。

## rv-1：交付前后的真实 git 记录

摘自 `raw/rv-1-delivery-archives-awaiting-human.log`，`-rP` 输出原文。fixture PRD 的执行侧条目是 `[x]` 与 `[~]`，Human-Confirmed 组有 1 个 `[ ]`，横幅为 🧍：

```text
== before delivery ==
$ git status --short
(clean)
$ ls -A tasks/pending
example.md
$ ls -A tasks/archive
.gitkeep
== after delivery ==
$ git status --short
R  tasks/pending/example.md -> tasks/archive/example.md
$ ls -A tasks/pending
(empty)
$ ls -A tasks/archive
.gitkeep
example.md
== archived PRD: banner line and Human-Confirmed group ==
> 🧍 **验收状态**：待人工验收 — 执行侧已完成，仅剩 1 项 Human-Confirmed 未确认，证据包见 §9。
### Human-Confirmed

- [ ] decision 1: answered by a human
```

门禁返回后，测试另起 git 进程做 fresh-state 探针，以下断言全部成立：

- `git status --porcelain` 含这条 rename；
- `git ls-files` 里有归档路径、没有 pending 路径；
- pending 文件不存在；
- 从磁盘重读的归档文件与交付前的 pending 文件逐字节相等；
- 发布前检查 `assert_prd_archived_for_publish` 没有抛错。

## rv-5：自动合并两轮记录

摘自 `raw/rv-5-merge-queue-hold.log`，`-rP` 输出原文。fixture 是一条已带 `validation/verifier-passed`、签核已全勾的 PR，Issue worktree 里只有 `tasks/archive/<prd>.md`：

```text
PRD before round 1:
# PRD: Example

> 🧍 **验收状态**：待人工验收 — 执行侧已完成，仅剩 1 项 Human-Confirmed 未确认，证据包见 §9。

## 9. Acceptance Checklist

### Validation Acceptance

- [x] rv-1: executor-owned item 1

### Human-Confirmed

- [ ] decision 1: answered by a human

== round 1 ==
exit_code=0 outcomes=['skipped_human_review']
github calls: ['list_issues_by_label', 'list_issue_comments', 'get_pull_request_context']
process calls: []
PRD before round 2:
# PRD: Example

> ✅ **验收状态**：已验收 — 验收清单已全部完成。

## 9. Acceptance Checklist

### Validation Acceptance

- [x] rv-1: executor-owned item 1

### Human-Confirmed

- [x] decision 1: answered by a human

== round 2 ==
exit_code=0 outcomes=['merged']
github calls: ['list_issues_by_label', 'list_issue_comments', 'get_pull_request_context', 'merge_pull_request', 'list_issue_comments', 'comment_issue', 'get_issue', 'edit_issue_labels', 'edit_issue_labels']
process calls: ['git rev-parse HEAD', 'git rev-parse HEAD', 'git branch --show-current', 'git fetch origin main', 'git rebase origin/main', 'git diff --check', 'uv run mkdocs build', 'git push --force-with-lease origin issue-88', 'git diff --check', 'uv run mkdocs build', 'git diff --name-only origin/main...deadbeef', 'git rev-parse HEAD']
```

- **两轮之间的 fresh-state。** 第二轮前，测试从磁盘改写 PRD：人审项改为 `[x]`，横幅改为 ✅。随后由新一轮 `process_merge_queue` 和新的 fake runner 独立观察。
- **参数化 5 例**（`位置-标记-期望`）：
  - `pending- -skipped_human_review`
  - `pending-x-skipped_prd_pending`
  - `archive- -skipped_human_review`
  - `archive-~-skipped_human_review`（`[~]` 按保守口径算未回答）
  - `archive-x-None`（继续走到合并）

## 负控与判别矩阵

两层负控都没有改生产代码。

**第一层：实现前红跑。** 运行时 `src/` 尚未改动，`git diff --stat HEAD -- src/` 为空，HEAD 为 `1b8afed9`。结果 `2 failed`，摘自 `raw/negative-control-rv1-rv5.log`：

```text
E       FileNotFoundError: [Errno 2] No such file or directory: '…/test_delivery_archives_prd_awa0/tasks/archive/example.md'
E       AssertionError: assert ['merged'] == ['skipped_human_review']
FAILED tests/test_agent_runner_prd_delivery.py::test_delivery_archives_prd_awaiting_human_review_real_git
FAILED tests/test_agent_runner_merge_queue.py::test_archived_prd_awaiting_human_holds_until_the_human_answers
```

- rv-1：旧交付检查遇到人审待办就提前返回，PRD 留在 pending，归档文件不存在。
- rv-5：旧 hold 只看 pending 路径，归档后的 PRD 被忽略，队列照常 rebase 并合并。

这正是 PRD 写明的 `expected_fail`。

负控选在 `1b8afed9`、而不是字面上的 `main`，是有意为之：`main` 的契约版本集合是 `(3, 4)`，v5 skill 在预检就会失败，红的原因就不是被测行为了。`1b8afed9` 上被测的 8 个模块与 `main` 逐字相同，只多认 v5 这一点。

**第二层：最终版测试 × 未修改源码。** 用 `git archive 1b8afed9` 导出未修改的 `src/`，拷入最终版 `tests/`，用同一 venv 和同一 v5 skill 跑本次改动的 7 个测试文件。导入探针证实加载的是导出目录里的旧源码（`is_prd_archive_path = False`）。结果 `27 failed, 123 passed`，见 `raw/baseline-discrimination.log`。

基线上 rv-1、rv-5 的行为记录（同一份日志，原文）：

```text
== after delivery ==
$ git status --short
(clean)
$ ls -A tasks/pending
example.md
== round 1 ==
exit_code=0 outcomes=['merged']
github calls: [..., 'get_pull_request_context', 'merge_pull_request', ...]
process calls: [..., 'git fetch origin main', 'git rebase origin/main', ...]
```

**判别矩阵**

基线上应变红的 27 条全红，按失败原因分两类：

| oracle | 用例 | 基线上的失败原因 |
|---|---|---|
| rv-1 | `test_delivery_archives_prd_awaiting_human_review_real_git`、`test_open_human_item_no_longer_keeps_prd_pending`、`test_deferred_human_review_gate_no_longer_blocks_archive`、`test_archived_prd_with_open_human_item_passes_both_gates` | **行为红**：归档文件不存在；没有 `git add` / `git mv` 调用；已归档 PRD 被拒，报 `Archived PRD still awaits human review` |
| rv-4 | `test_push_changes_requires_an_archived_prd[archived-only-human-open]`、`[still-pending]` | **行为红**：前者报 `Archived PRD still awaits human review`；后者 `DID NOT RAISE`，即旧代码放行了 pending |
| rv-5 | `test_archived_prd_awaiting_human_holds_until_the_human_answers`、`test_prd_hold_prevents_auto_merge_until_the_human_answers[archive- -…]`、`[archive-~-…]` | **行为红**：结果是 `merged`，不是 `skipped_human_review` |
| rv-6 | `test_contract_prompt_prefix_contains_teaching_anchors`、`test_find_violations_accepts_archived_or_pending_prd_path[archived-path]`、`test_append_missing_contract_anchors_makes_fallback_body_compliant`、`test_append_missing_contract_anchors_only_adds_what_is_missing[tasks/archive/…]`、`test_create_draft_pr_fallback_body_gets_contract_anchors` | **行为红**：归档路径被报 `prd-link`；补锚点写的是 pending 路径与 "post-merge archival" |
| rv-7 第二组 | `test_pre_v5_skill_skips_the_banner_check_real_git` | **行为红**：旧代码因人审空框提前返回，没有 rename |
| FR-6 | `test_archive_rules_teach_executor_side_archiving_and_the_banner_formula` | **行为红**：旧提示词里没有 "archives the PRD at delivery" |
| rv-3 | `test_banner_mismatch_blocks_archive_and_publish_real_git` ×4、`test_banner_mismatch_is_repaired_by_a_banner_only_closeout` | **结构红**：基线根本没有横幅字段，在前置检查处报 `AttributeError: 'PrdChecklistResult' object has no attribute 'acceptance_status'` |
| rv-3 / rv-7 字段 | `TestAcceptanceStatusBanner` ×4、`TestParsePrdChecklist::test_empty_file_returns_no_section`、`test_no_acceptance_section_returns_not_found` | **结构红**：同上（`AttributeError`，或构造参数 `TypeError`） |

"结构红"这一类的说明：

- 它证明基线没有横幅这个概念，但没有演示"不一致仍被归档"这一具体行为。
- rv-3 是 R1，PRD 不要求负控，这里如实标注。
- 在基线上，`awaiting-but-answered` 的 PRD（人审已全勾、横幅仍写 🧍）会被旧交付检查直接归档。原因是旧代码只看人审待办，没有横幅检查。

基线上应保持不变、实际也通过的回归护栏如下，它们在基线和最终代码上都绿：

| oracle | 用例 |
|---|---|
| rv-2 | `test_delivery_keeps_prd_pending_while_an_executor_item_is_open_real_git` |
| rv-4 第三种 | `test_push_changes_requires_an_archived_prd[archived-executor-open]` |
| rv-7 第一组 | `test_prd_without_human_items_archives_exactly_as_before_real_git`：断言 `git status --porcelain` 恰为一条 rename，且归档字节与交付前相同。两边都通过，说明暂存区与字节和改动前一致 |
| rv-7 第三组 | `test_failure_draft_publication_skips_the_archive_gate_and_never_archives` ×3 |
| rv-5 不变部分 | `test_prd_hold_prevents_auto_merge_until_the_human_answers[pending- -skipped_human_review]`、`[pending-x-skipped_prd_pending]`、`[archive-x-None]` |
| rv-6 不变部分 | `[pending-path]` 及其余 23 例 |

## 全量测试

| 运行 | 命令 | 结果 | 原始日志 |
|---|---|---|---|
| v5 skill | `IAR_PRD_SKILL_PATH=<v5> uv run pytest -o addopts='' -p no:cacheprovider tests/ -q` | `2786 passed, 1 skipped in 152.06s`，exit 0 | `raw/full-suite-v5.log` |
| 本机已安装的 v4 skill（披露用） | 同上，但用 `env -u IAR_PRD_SKILL_PATH` | `8 failed, 2778 passed, 1 skipped` | `raw/full-suite-installed-v4.log`、`raw/installed-v4-failure-reasons.log` |

v4 下失败的 8 条全部是横幅用例：

- `test_banner_mismatch_is_repaired_by_a_banner_only_closeout`
- `test_banner_mismatch_blocks_archive_and_publish_real_git` ×4
- `TestAcceptanceStatusBanner::test_banner_state_and_open_human_items_come_from_the_skill`
- `TestAcceptanceStatusBanner::test_unarchivable_banner_states_are_reported_verbatim` ×2

它们先调用 `require_banner_aware_prd_skill()`，遇到 v4 时**故意失败而不是跳过**，并给出可行动的信息：

```text
the prd skill used by this test run does not report `acceptance_status` (Machine Contract < 5);
install the v5 prd skill (`iar init`) or point IAR_PRD_SKILL_PATH at it before running banner assertions
```

跳过会让 CI 在横幅断言一条没跑的情况下仍然显示全绿；失败则把"环境还没升级"摆到明面上。这也是本 PR 在 ZataZhang/zata-codes-template#27 合并前 CI 会红这 8 条的原因。

## 发版窗口检查

§8 Notes 规定了发版顺序 (a)→(d)，于是会有一段窗口：模板 #27 已合并（CI 安装的 skill 变成 v5），本 PR 还没合并。这段时间里，keda `main` 依次处于两种状态：

- `bd735e85`：只合了 ZataZhang/keda#183；
- `1b8afed9`：又合了 ZataZhang/keda#184。

为确认这段窗口里 `main` 不会因 v5 变红，用这两个提交**各自**的 `src/` 与 `tests/`，在 v5 skill 下跑了全量。方法是 `/tmp` 下的本地 clone，用同一 venv，设 `PYTHONPATH=<clone>/src`，让子进程也导入该提交的源码。

| `main` 在窗口内的状态 | 提交 | 契约版本集合 | v5 skill 下全量结果 | 原始日志 |
|---|---|---|---|---|
| 只合了 ZataZhang/keda#183 | `bd735e8533256d87f57673e3c4663c1fd04e9b55` | `(3, 4, 5)` | `2716 passed, 1 skipped in 146.07s`，exit 0 | `raw/release-window-bd735e85-v5.log` |
| 合了 #183 与 #184 | `1b8afed9c0da6b7128ea346e9363b9c38a7e4513` | `(3, 4, 5)` | `2759 passed, 1 skipped in 146.39s`，exit 0 | `raw/release-window-1b8afed9-v5.log` |

两次运行的 `src/` 子树都是 `74941e60…`。日志首行记录了导入探针，证实子进程加载的是 clone 里的源码，不是本 worktree 的 editable 安装。

结论：只要先合 #183、再合模板 #27，`main` 在整个窗口里都是绿的。

反过来，如果先合 #27、`main` 仍是 `(3, 4)`，CI 安装 v5 后会在 PRD 契约预检处失败。这就是 §8 Notes 要求 (a) 先于 (b) 的原因。

## Executor Drift Guard

PRD §7.3 的 10 行检查，输出原文见 `raw/drift-guard.log`。

| # | 检查 | 结果 |
|---|---|---|
| 1 | 契约版本 | ✓ `SUPPORTED_MACHINE_CONTRACT_VERSIONS: tuple[int, ...] = (3, 4, 5)` |
| 2 | 清单钩子 | ✓ `HUMAN_CONFIRMED_GROUP_PREFIX = "human-confirmed"`（第 33、179 行） |
| 3 | 已安装 skill | **✗** `/Users/zata/.iar/skills/prd/SKILL.md` 为 `Machine-Contract-Version: 4`。全部 oracle 用的是模板 `2bf2de8` 的 v5，经 `IAR_PRD_SKILL_PATH` 指定。等 (b) 合并后执行 `iar init` |
| 4 | 归档移动唯一 | ✓ 只有 `agent_runner_feedback.py:476`，位于 `ensure_prd_delivery_ready`，目标经 `resolve_prd_archive_path` 计算 |
| 5 | 旧语义残留 | ✓ 无命中（exit 1） |
| 6 | hold 复用定位函数 | ✓ 只命中 `resolve_prd_worktree_path`（导入行 45、调用行 409），没有 `pending_prd_path` |
| 7 | 不新增横幅文本解析 | ✓ 命中只在 docstring、提示词与 closeout 指令文本中。两个门禁模块里唯一的正则是 `prd_checklist.py:21` 的 `CHECKBOX_RE`：base 上原样存在，逐行匹配复选框，从不读横幅。横幅状态只来自 `contract.get("acceptance_status")` |
| 8 | 孤儿脚本已删 | ✓ `rg -n check_prd_archive_pre_push . -g '!tasks/**'` 无命中，文件不存在。不加过滤时的命中全在 `tasks/` 下的 PRD 文本里 |
| 9 | marker 与标题不变 | ✓ `agent_runner_pr_body_contract.py:40` 的 marker，`:55` 的小节标题 |
| 10 | 导入无环与行数上限 | ✓ `import backend.core.use_cases.agent_runner_merge_queue` 成功。本次触及的每个 `.py` 都过了 `check_max_file_lines.py --max-lines 1000`，`agent_runner_feedback.py` 为 920 行非空行 |

第 10 行的补充说明：仓库里另有 10 个存量超限文件。它们本次未触及，base 与最终行数逐一相同，清单见 `raw/drift-guard.log` 的 10b 段。

## 风险地图对账、对抗自检、锁定契约 diff

### 风险地图对账：Predicted → Reconciled

| 预测 | 实际 | 处理 |
|---|---|---|
| R2 交付检查（rv-1、rv-2） | 一致。只改了 `ensure_prd_delivery_ready` 两个分支的判据，归档移动仍只在一处 | — |
| R1 横幅一致性（rv-3） | 一致。`_validate_acceptance_banner` 放在 `_validate_prd_checklist` 末尾，交付检查两个分支和发布前检查**三个既有调用点**因此同时生效，没有新增调用点 | — |
| R1 发布前检查（rv-4） | 一致。内联的 archive 路径判断收敛成 `is_prd_archive_path`，合并队列也复用它 | — |
| R3 合并队列 hold（rv-5） | 一致。新增 `merge_queue → closeout` 导入边，无环（Drift Guard 第 10 行） | — |
| R1 PR 正文（rv-6） | **未预测的边。** 两条 `require_prd_archived=False` 发布路径（失败 Draft PR、PRD 返工）发布时 PRD 留在 pending。但确定性正文补锚点，以及 agent 正文的教学，都指向 archive 路径，这个路径在那两类 PR 的分支上并不存在 | 没有消费方从 PR 正文读 PRD 路径：合并队列与 closeout 都从 Issue 正文定位。影响只是那两类 PR 里的 PRD 链接指向还不存在的文件；契约校验同时认 pending 路径，不会误报。修复需要让 `create_draft_pr` 感知 PRD 的实际位置，而 publish → closeout 的导入会成环，不在本次范围。已记入 PRD §12 |
| R1 兼容（rv-7） | 一致 | — |
| （未列入） | **升级时的在途 Issue。** 有一种本地提交：runner 升级前就已提交，PRD 仍在 pending 且带 🧍。复用路径 `_reuse_existing_local_commit` 遇到它时，会先由交付检查归档，暂存的 rename 让工作区不再"干净"。结果分两种：常规认领路径（`agent_runner_issue_handlers.py:590`）回落到 agent 执行；running 发布恢复路径 `_process_running_publish_recovery`（`:902`）则报 `has no clean local commit ready for publication`，被通用异常分支标为 failed。对"已提交、仍在 pending、可以归档"的 PRD，旧代码在复用路径上的结果本来就是这样；新代码只是把 🧍 PRD 也算作可以归档 | 只影响升级那一刻的在途 Issue，不做迁移（与 §1 解读回显"不做一次性迁移"及 §11 一致）。已记入 PRD §12 |
| （未列入） | **CI 依赖模板 main 的 skill 版本。** 横幅用例在 v4 下会失败 | 有意失败而不是跳过（理由见「全量测试」）。发版顺序写入本报告与 PR 正文 |
| R0 删除孤儿脚本、文档同步 | 一致 | — |

### 对抗自检

1. **失败交付永不归档。**
   - `ensure_prd_delivery_ready` 只有 3 个调用点，都在成功或复用路径上：`run_agent_execution_loop.py:347`、`:616`，以及 `agent_runner_publication.py:483`。
   - `require_prd_archived=False` 只出现在 `agent_runner_issue_handlers.py:493`（耗尽重试后的失败 Draft PR）和 `create_prd_from_issue.py:333`（返工）。
   - 这两条路径既不调用交付检查，`push_changes` 在 `False` 时也不调用任何 PRD 门禁。
   - rv-7 第三组断言 `git add` 与 `git mv` 都没有发出、PRD 内容逐字不变；`still-pending` 那一例的 PRD 仍在 pending。
2. **两条例外没有扩大。** 既有的静态审计 `test_require_prd_archived_false_appears_only_on_the_exhaustion_path` 用白名单钉住这两个文件，本次没有改它，它仍然通过。
3. **横幅检查只在 skill 读不出横幅时跳过。**
   - `_validate_acceptance_banner` 唯一的跳过条件是 `acceptance_status is None`。
   - 这个值只来自 `contract.get("acceptance_status")`：v5 skill 总会报告该键，横幅缺失或认不出时值为 `""`；只有 v3/v4 才缺键。
   - `""` 与 `not_started` 都按不一致抛错，见 rv-3 的 `[missing]` 与 `[not-started]`。
4. **⬜ 永远不可归档。** `not_started` 既不等于 `awaiting_human` 也不等于 `accepted`，所以一定抛错、不归档，见 rv-3 的 `[not-started]`。
5. **横幅公式与 skill 同口径。** 横幅判据用 `human_unchecked_items`，只算 `[ ]`，与 skill 检查器的 `open_human_count` 一致。合并队列 hold 用 `human_pending_items`，算 `[ ]` 加 `[~]`，更保守，两者不混用。
6. **归档不改 PRD 内容。** rv-1 断言归档文件与交付前逐字节相同。closeout 指令禁止勾选、取消勾选或改写任何复选框。

### 锁定契约 diff

- **PR 正文。**
  - marker `<!-- iar:merge-acceptance version=1 -->` 与小节标题 `## Human Acceptance And PRD Archive` 两行，在 `git diff 1b8afed9` 里都没有出现。
  - 改动的只有声明句（`authorizes post-merge acceptance recording on that PRD …`）、`prd-link` 的说明文字，以及 prompt 教学第 1、2 条。
- **`DeliveryGateFailureKind`。** 只新增 `ACCEPTANCE_BANNER_MISMATCH = "acceptance_banner_mismatch"`，外加 docstring 两行。`is_closeout_eligible` 的定义未变：非 `SUBSTANTIVE` 即可收尾。
- **`PrdChecklistResult`。** 新增两个带默认值的字段 `human_unchecked_items` 与 `acceptance_status`，构造函数向后兼容。
- **`_validate_prd_checklist`。** 返回值从 `bool` 改为 `None`。它是私有函数，3 个调用点都在 `agent_runner_feedback.py`，已同步修改。
- **合并队列的 outcome 字符串**（`skipped_human_review` / `skipped_prd_pending`）未变。

## 低风险门禁（折叠）

<details>
<summary>架构、ruff、文档构建、依赖面、提交门禁</summary>

| 门禁 | 结果 | 证据 |
|---|---|---|
| `env -u PYTHONPATH uv run python hooks/shared/check_architecture.py` | ✓ 共扫描 278 个文件，无违规 | `raw/static-gates.log` |
| ruff 0.7.4（pre-commit 锁定版本）`check` + `format --check`，作用于本次触及的 16 个 `.py` | ✓ `All checks passed!`、`16 files already formatted` | `raw/static-gates.log` |
| `uv run mkdocs build --strict`（输出目录改到 `/tmp`，严格度不变） | ✓ exit 0。两条锚点 INFO 是存量，两个链接在 base 上就已存在 | `raw/static-gates.log` |
| 依赖 / schema / 前端 | ✓ `git diff --cached --name-only 1b8afed9 -- uv.lock pyproject.toml alembic frontend-admin frontend-public` 为空 | `raw/static-gates.log` |
| 提交门禁 `just test` / `just lint` | 见下方「提交门禁」 | — |

</details>

### 提交门禁

| 门禁 | 结果 | 证据 |
|---|---|---|
| `IAR_PRD_SKILL_PATH=<v5> just test`。本地档先跑 `just lint --full`（包括对未跟踪的本 PRD 与报告执行 pre-commit），再跑 `uv run pytest tests/ -q --no-header`。仓库 addopts 带 `--testmon`，但本 worktree 运行前没有 `.testmondata`，所以等于全量 | ✓ `✅ Lint passed. Proceeding to tests...`；`2786 passed, 1 skipped in 161.71s`，exit 0。用例数与「全量测试」的 v5 全量相同 | `raw/commit-gate-just-test.log` |
| 写入的提交标记 | `.last_tested_commit` = `feat/archive-confirmation-separation` @ `1b8afed9…`，有效 tree `72b615e5…`。有效 tree 只含会进入测试的文件，`.md`、`.log` 不计入，所以之后改报告与 PRD 不会让标记失效 | 同上 |
| `just lint`（暂存区模式，与提交钩子是同一组 hook） | 在最终 `git add -A` 之后、提交之前执行；提交钩子再跑一遍同一组 hook。结果写在 PR 正文「门禁」一节 | — |

这里的 `just test` 同样设了 `IAR_PRD_SKILL_PATH`。在本机已安装 v4 skill 的环境下不设它，会有 8 个横幅用例失败（见「全量测试」）。所以 PRD 里"`just lint` 与 `just test` 通过"这一项留到 `iar init` 之后再勾。

## 交付内容

相对 base `1b8afed9`，共 18 个文件，+930 / −406。另有本 PRD，以及本目录的 2 份 `.md` 报告：证据报告与验证计划。verifier 报告要到归档那一轮才有。

| 文件 | 改动 |
|---|---|
| `src/backend/core/shared/prd_checklist.py` | `PrdChecklistResult` 新增 `acceptance_status` 与 `human_unchecked_items`，都直接取自 skill 契约 JSON（+16/−2） |
| `src/backend/core/shared/models/agent_runner.py` | `DeliveryGateFailureKind` 新增 `ACCEPTANCE_BANNER_MISMATCH`（+3） |
| `src/backend/core/use_cases/agent_runner_prd_delivery_gate.py` | 新增 `_validate_acceptance_banner`；`_validate_prd_checklist` 不再以"有人审待办"返回真（+54/−12） |
| `src/backend/core/use_cases/agent_runner_feedback.py` | 交付检查在执行侧完成时即归档；发布前检查要求必须已归档；改写两条提示词规则；新增 `is_prd_archive_path`（+44/−33，非空行 912 → 920） |
| `src/backend/core/use_cases/agent_runner_closeout.py` | 新失败种类的收尾指令：只改横幅一行，并追加一条 Change Log（+14/−1） |
| `src/backend/core/use_cases/agent_runner_merge_queue.py` | PRD hold 按实际位置判断（+16/−9） |
| `src/backend/core/use_cases/agent_runner_pr_body_contract.py` | 改为归档路径与新声明句；marker 与标题不变（+26/−9） |
| `src/backend/core/use_cases/agent_runner_publish.py` | `require_prd_archived` 的 docstring（+4/−3） |
| `hooks/check_prd_archive_pre_push.py` | 删除旧语义的孤儿脚本（−194） |
| `docs/guides/agent-runner.md` | 强制 Closeout 第 2–4 条、PR body 契约第 1–2 条、PRD hold、路线图"已归档"行、依赖等待；写明"已归档 ≠ 已验收"（+28/−11） |
| `tests/`（8 个文件） | 翻转旧语义用例，补 rv-1…rv-7 与 FR-6 用例；新增共享 fixture `build_acceptance_prd` / `require_banner_aware_prd_skill`（+725/−132） |

## 对 PRD 的更正

以下更正都随本次交付写入 PRD，并记在 PRD 的 Change Log 里：

- **Drift Guard 第 7 行，期望值。** 原文只写了"提示词与 closeout 指令文本"，实际 docstring 与注释也会命中，故改为"只出现在提示词、closeout 指令文本与 docstring / 注释中，没有正则或解析代码"。
- **Drift Guard 第 8 行，命令。** 原命令会命中 `tasks/` 下的本 PRD 与证据文本，永远做不到"无命中"，故加 `-g '!tasks/**'`，并注明原因。
- **Drift Guard 第 10 行与 Architecture Acceptance 第 4 项，命令。** `check_max_file_lines.py` 必须带文件参数，原命令不可执行，故改为对本次触及的文件执行，并注明仓库另有存量超限文件。
- **§12 新增两条后续。**
  - 失败 Draft PR / 返工 PR 的 PRD 链接指向还不存在的归档路径。
  - 升级时在途 Issue 的复用路径。

## 尚未完成

**本轮 PRD 一项都没勾。** 横幅保持 ⬜，PRD 留在 `tasks/pending/`；按 v5 规范，⬜ 也涵盖"进行中"。原因有两层：

1. **独立 verifier 还没有结论。** 第 1 轮复核中途被中断，用户 2026-10-04 决定先开 Draft PR，把 verifier 推迟到归档那一轮。没有 verifier `PASS`，执行侧条目一项都不勾。
2. **另有 8 项本来就要等。** 其中 6 项依赖 (b) 合并和 `iar init`（本机安装的 skill 仍是 v4），2 项只能在归档那一轮做。即使 verifier `PASS`，它们也要等到那时才能勾。

### 证据已备齐、等 verifier `PASS` 再勾的 21 项

| PRD 小节 | 条目 | 证据在哪 |
|---|---|---|
| Architecture | 全部 4 项 | 「Executor Drift Guard」第 4、6、7、10 行；「低风险门禁（折叠）」的架构守卫 |
| Dependency | 第 1、2 项：没有新依赖；没有数据库、schema 或前端改动 | 「低风险门禁（折叠）」 |
| Behavior | rv-1 … rv-7，共 7 项 | 「Oracle 结果」「rv-1：交付前后的真实 git 记录」「rv-5：自动合并两轮记录」 |
| Frontend | `No frontend impact` 已记录 | PRD §5、§7.2；diff 不含任何前端路径 |
| Documentation | 全部 2 项 | 「Executor Drift Guard」第 5 行；「低风险门禁（折叠）」的 `mkdocs build --strict` |
| Validation | 第 2、3 项：负控；最终 tree id | 「负控与判别矩阵」「绑定最终树」 |
| Delivery Readiness | 第 1、5、6 项：方案落地；打开方式与期望值；人审导航 | 「交付内容」；PRD §9.1；本报告首节 |

### 还要等 (b)、`iar init` 或归档那一轮的 8 项

| 条目 | 现状 | 什么时候能勾 |
|---|---|---|
| Dependency："§8 Notes (a)–(c) 已在本 PRD 交付前完成" | (a)、(c) 的代码在本分支的祖先里（ZataZhang/keda#183、#184，均未合并）；(b) ZataZhang/zata-codes-template#27 未合并；`iar init` 未重跑；Drift Guard 第 3 行为 ✗ | 三个 PR 合并、`iar init` 之后 |
| Validation："rv-1 … 真实安装的 v5 skill" | 已在 v5 skill 上跑绿，但用的是 `IAR_PRD_SKILL_PATH`，不是安装件 | `iar init` 后，不设 `IAR_PRD_SKILL_PATH` 复跑 rv-1、rv-5 |
| Validation："Drift Guard 全部检查符合" | 第 3 行 ✗ | 同上 |
| Validation："`uv run pytest -o addopts='' tests/ -q` 全绿" | v5 下全绿；本机 v4 下 8 条按设计失败 | 同上 |
| Validation："`just lint` 与 `just test` 通过" | 设了 `IAR_PRD_SKILL_PATH` 时的结果见「提交门禁」 | 同上 |
| Delivery Readiness："无未决回归或上线阻塞项" | 上线依赖 (b)，CI 在 #27 合并前红 8 条 | 同上 |
| Delivery Readiness："完成 §13 Final Reconciliation" | 归档时才做 | 归档那一轮 |
| Delivery Readiness："完成回复已原样带上呈递表内容" | 归档那一轮的完成回复会带上 | 归档那一轮 |

### 归档那一轮

verifier `PASS`、上表前 6 项也都满足之后，在本 PR 分支上追加一个提交：勾选上面两组共 29 项执行侧条目并逐项写明证据，做 Final Reconciliation，把横幅按公式改为 🧍，再把 PRD 移到 `tasks/archive/`。之后由人合并。Human-Confirmed 的 3 项始终由人回答。

## 残留风险

- **"已归档 ≠ 已验收"要看横幅。** 文档已写明；路线图暂不区分（PRD §11 的非目标）。
- **代勾人审项只靠提示词约束。** 这是既有状况：agent 若把人审项直接勾成 `[x]`，合并队列无法区分是人勾还是 agent 勾。若改写成 `[~]`，会被保守口径拦下。合并时的树比对与补记属于后续 PRD。
- **合并后的验收补记暂时没有自动化。** 合并带声明的 PR 之后，PRD 会以 🧍 留在 archive，直到有人手动补记（PRD §12）。
- **合并队列读的是 Issue worktree。** 人在 PR 分支上勾选后，worktree 要同步到最新 head 才能看到。这是既有行为（PRD §12）。
