# PRD: 在受保护分支的仓库发布与恢复 PRD

- GitHub Issue: https://github.com/ZataZhang/keda/issues/400

> ✅ **交付前置**：无，可立即开工。
> 🧍 **验收状态**：待人工验收。

## Feature Overview（功能一览）

- **FR-1**：协作者可选择把 PRD 发布到对应 `issue-<N>` 分支，只有分支推送和提交链接写回成功后才按请求加入 ready 队列。
- **FR-2**：半完成发布可复用原 Issue；相同内容可幂等重试，远端冲突拒绝覆盖。
- **FR-3**：按 Issue 执行时，runner 从任务分支读取 PRD 与生命周期执行器覆盖。
- **FR-4**：保留默认发布到 base branch 与 `--no-publish-prd` 行为，并同步 CLI 文档和随包 operator skill。

# Part A · 人审层

## 1. Introduction & Goals

### Problem Statement

协作者创建 Issue 后，仓库规则可能拒绝 PRD 发布，留下指向远端不存在文件的 Issue。目标是在不改变默认发布行为的前提下，让 PRD 在主分支受保护时仍能随实现进入评审，并让已经创建的 Issue 可恢复。

### Interpretation (解读回显)

| 标记 | 场景 | 可观察结果 |
|---|---|---|
| 🤖 自动验证 | base branch 拒绝 PRD 推送 | Issue 保持未 ready，并指出复用原 Issue 的恢复方式 |
| 🤖 自动验证 | 协作者显式选择任务分支，或恢复先前失败的 Issue | 远端 PRD 与实现分支一致；只有发布和链接写回完成后才按请求 ready |
| 🤖 自动验证 | 同一内容重复恢复，或远端 PRD 已被改成不同内容 | 相同内容保持幂等；冲突明确失败且远端内容不变 |
| 🤖 自动验证 | 从 main 不含 PRD 的 checkout 按 Issue 执行 | runner 使用发布分支中的 PRD 和执行器覆盖 |

我默默定了这些：默认发布仍指向配置的 base branch；任务分支发布是显式选择；首次只覆盖按 Issue 执行的 runner 入口。

我理解为不做：不改分支保护规则、不增加从 main 扫描 PRD 的队列功能，也不在本次自动验证中操作 #398/#399。

### What The User Gets

协作者可以在主分支受保护时创建可执行 Issue；若创建后发布中断，可沿用原 Issue 安全重试。

### Measurable Objectives

- 发布失败时原 Issue 不进入 ready 队列，并给出可复用该 Issue 的命令。
- 任务分支只包含目标 PRD；远端冲突不被覆盖。
- 按 Issue 执行时，实际 Agent worktree 获得任务分支 PRD 和对应执行器覆盖。

- [ ] 我确认这段解读与行为样例准确表达了我的需求。

需求方已在对话中确认上述方案与执行范围，复选框留给需求方本人操作。

## 2. Human Review Map

需求方已确认显式启用新模式，首版按 Issue 执行，使用 #398 验证已有 Issue 恢复。可观察验收为原 Issue 可执行且 PRD 与实现进入同一个 PR。

## 3. Usage And Impact After Implementation

遇到受保护的主分支时，协作者可以直接选择任务分支。遇到半完成发布时，错误输出给出 Issue 链接和恢复方法。

## 4. Requirement Shape

新建与补发共用发布规则。发布未完成时，Issue 保持未就绪。需要从主分支扫描 PRD 的列表和自动推进留待后续。

# Part B · 执行器层

## 5. Repository Context And Architecture Fit

复用 create_issue_from_prd、现有 Git 发布适配器以及 agent/rework-prd 的任务分支约定。agent_runner_worktree_create 已协调远端同名分支。agent_runner_orchestration_runtime 在 worktree 创建之前调用 attach_prd_lifecycle_overrides，需覆盖这条读取路径。CLI 负责参数和输出，core 编排，底层 Git 操作走现有适配能力。

**Change Impact Tree**

```text
kc issue create
└── api 参数/文本与 JSON 输出
    └── core PRD 发布与恢复 → 临时 Git worktree → issue-<N>
        └── runner 按 Issue 获取分支 PRD → 生命周期 Agent 解析 → 现有实现流程
```

No frontend impact：改动只涉及 CLI、runner 后端和文字操作指南，没有浏览器界面。

## 6. Recommendation

**Proposed Solution Summary（实现机制）**：增加显式任务分支发布选项与已有 Issue 恢复入口。沿用远端基线，在隔离 Git 工作区只提交目标 PRD，以普通非强制推送发布 issue-<N>。校验 PRD 路径和 Issue 的仓库、编号与现有 PRD 锚点。相同目标内容重试可成功并补全之前中断的元数据写回。分支有不同内容时报告冲突，不覆盖分支。Issue 链接固定到发布提交，保留机器锚点。

## 7. Implementation Guide

> This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

核对发布预检、交互和 JSON 行为，复用可用 Git 与 Issue 接口。处理 Issue 创建后发生的回写、推送、元数据或标签更新失败，使恢复可重试。调度在选择执行器前解析任务分支 PRD，并确保实际 worktree 使用该内容。同步配置/CLI 指南与随包 operator skill。

### Risk Classification Register

| Change point | Tier | Decisive dimension | Intervention | Failure-discriminating oracle |
|---|---|---|---|---|
| Task-branch publication and ready ordering | R2 | Persistent Issue/Git state and ordering | Executor + independent verifier | rv-1 real CLI, remote refs and fresh Issue-state probe |
| Existing-Issue recovery and conflict protection | R2 | Idempotency and prevention of remote content loss | Executor + independent verifier | rv-2 real recovery CLI, remote SHA/content comparison |
| Runner branch PRD and executor selection | R2 | Cross-component orchestration before worktree execution | Executor + independent verifier | rv-3 clean checkout through `kc run --issue` |
| Default and no-publish compatibility | R1 | Bounded CLI behavior with direct observable result | Executor + automated gate | rv-4 real CLI plus targeted tests, lint, and docs build |

### 7.6 Realistic Validation Plan

```yaml
- id: rv-1
  behavior: 受保护 main 拒绝默认推送时，显式任务分支发布成功，推送和提交链接写回后才添加 ready。
  reviewer: verifier
  real_entry: "kc issue create tasks/pending/<prd>.md --publish-to-issue-branch"
  expected: "main 未改变；issue-<N> 只包含目标 PRD；Issue 链接固定到该提交；ready 写入发生在远端分支和提交链接写回之后。"
  mock_boundary: "真实 kc CLI、Git worktree、commit、push 和 bare remote；只替换 GitHub API transport。"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "PRD 路径来自 CLI 参数，Issue 编号和 URL 来自真实 create 流程返回给本地 API fixture 的请求。"
  must_cross: "kc CLI → core create → 隔离 worktree → 真实 Git push issue-<N> → Issue body 固定提交链接 → ready label → 独立 remote/state 读取。"
  forbidden_bypasses: "不得直接调用发布 helper、用 mock Git、预置远端成功结果，或只检查 CLI 返回值。"
  fresh_state_probe: "kc 子进程退出后，从 bare remote 读取 main/task branch SHA 与 PRD 文件，并从持久化 gh 状态读取 body/label 写入顺序。"
  final_tree_evidence: "rv-1 在最终发布与 ready 写入路径变更后重跑；交付记录路径排除在代码 tree 外，任何相关源代码变更都要求重采。"
  negative_control: "安装拒绝 refs/heads/main 的远端 Hook，运行不带任务分支选项的默认创建命令。"
  expected_fail: "退出非零，输出 GH013、Issue 未 ready 和 --recover-issue；main SHA 不变。"
- id: rv-2
  behavior: 已创建 Issue 可恢复且重复恢复幂等；远端 PRD 不同则拒绝覆盖。
  reviewer: verifier
  real_entry: "kc issue create tasks/pending/<prd>.md --recover-issue <N>"
  expected: "同一 Issue 首次恢复和同内容重试成功且 SHA 不变；远端不同内容的恢复失败并保留 SHA、文件字节及 Issue 状态。"
  mock_boundary: "真实 kc CLI、Git clone/commit/push/remote refs；只替换 GitHub API transport。"
  tier: R2
  test_layer: integration
  required_for_acceptance: true
  critical_value_source: "Issue number/URL/body 从 Issue API fixture 的创建或读取返回；目标路径从原 Issue PRD path anchor 与 CLI 参数交叉校验。"
  must_cross: "Issue create → branch push failure → fresh recovery CLI → Git remote branch → Issue metadata/ready update → new CLI retry → remote SHA/content probe。"
  forbidden_bypasses: "不得新建第二个 Issue、直接写远端 ref、绕过 PRD 锚点/仓库校验，或用测试替身代替 Git。"
  fresh_state_probe: "每次 kc 命令使用新进程；由单独 Git publisher 写入冲突，再读取 bare remote 的 branch SHA 和 PRD 字节。"
  final_tree_evidence: "在最终 recovery、idempotency 与 conflict guard 变更后重跑；任何相关代码改动都要求重采。"
  negative_control: "预置与本地目标不同的 origin/issue-<N> PRD 字节，然后执行真实 --recover-issue。"
  expected_fail: "退出非零并报告 different content / remote content was preserved；远端 SHA、字节与 Issue 创建计数不变。"
- id: rv-3
  behavior: 从 main 没有 PRD 的干净 checkout 按 Issue 执行时，runner 获取任务分支 PRD 并使用其中的 executor override。
  reviewer: verifier
  real_entry: "kc run --issue <N>"
  expected: "真实 runner 创建 issue-<N> worktree；可观察 Agent 在该分支读取到仅存在于任务分支的 PRD 与 lifecycle override。"
  mock_boundary: "真实 kc runner、Issue resolution/claim、Git fetch 与 worktree；只替换 GitHub API transport 和付费 Agent executable。"
  tier: R2
  test_layer: e2e
  required_for_acceptance: true
  critical_value_source: "Issue 编号、PRD 路径与发布提交锚点来自 Issue API 的读取结果；override 文本来自远端 issue-<N> PRD。"
  must_cross: "干净 main checkout → kc run --issue → Issue 读取 → task branch fetch → worktree 创建 → PRD lifecycle 解析 → 选定 executor 启动并读取其工作目录。"
  forbidden_bypasses: "不得在 main 预置 PRD、直接调用 lifecycle parser、绕过 runner/worktree，或用手工传入的 override 替代分支内容。"
  fresh_state_probe: "Agent fixture 独立进程记录真实 cwd、当前 branch 和 worktree 内 PRD；同时确认 main checkout 无该文件。"
  final_tree_evidence: "在调度前解析、worktree base 与 executor resolution 的最终代码上重跑；任一相关改动都要求重采。"
  negative_control: "从临时 bare remote 删除 issue-<N> ref 后，仍从干净 checkout 运行 kc run --issue。"
  expected_fail: "runner 在 Agent 启动前因无法获取 published PRD branch 退出，Agent probe 不存在。"
- id: rv-4
  behavior: 默认 PRD 发布仍兼容；不发布选项不会推送或加入 ready 队列，相关测试、lint 和文档构建通过。
  reviewer: verifier
  real_entry: "kc issue create <prd> --json；kc issue create <prd> --no-publish-prd --json"
  expected: "默认模式只向未保护的 base branch 发布目标 PRD 并按请求 ready；--no-publish-prd 不推送且保持未 ready；定向测试、全文件 lint 与 MkDocs build 通过。"
  mock_boundary: "真实 kc CLI 与 Git；GitHub API transport 使用本地 fixture；测试和文档命令使用当前 worktree。"
  tier: R1
  test_layer: integration
  required_for_acceptance: true
  negative_control: "在 main 安装拒绝 Hook 后运行默认发布命令并检查结果及 remote state。"
  expected_fail: "默认路径以非零退出、Issue 未 ready 且 main SHA 不变；若默认行为改为绕过 main push、吞掉错误或提前 ready，兼容断言失败。"
```

## 8. Delivery Dependencies

- Depends on tasks/issues: none
- Gate type: none
- Notes: none

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

无主演示：本次只改 CLI 与 runner 后端行为，没有需要截图的界面。人工呈递只有 #398 在实现交付后的真实恢复结果。

| 人工核对结果 | 呈递物 | 打开方式 | 自检 |
|---|---|---|---|
| 原 #398 已复用；PRD 链接固定到任务分支发布提交；ready 在发布成功后出现 | [GitHub Issue #398](https://github.com/ZataZhang/keda/issues/398) | `gh issue view 398 --json url,labels,body` | Issue URL 仍为 #398；正文只有一个 `PRD path:` 锚点；PRD 链接指向包含该文件的 `issue-398` 提交；`agent/ready` 已在发布成功后添加。 |

证据报告中的 `human-review-checklist.md` 集中此项检查步骤，可通过 `just prd review P1-FIX-20261119-180000-prd-publication-on-protected-main.md` 打开。rv-1 至 rv-4 都是 verifier-only 日志与命令检查，不作为人工呈递项。

### 9.2 Acceptance Evidence Package

- `tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main/P1-FIX-20261119-180000-prd-publication-on-protected-main.verification-plan.md`
- `tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main/P1-FIX-20261119-180000-prd-publication-on-protected-main.evidence-report.md`
- `tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main/P1-FIX-20261119-180000-prd-publication-on-protected-main.verifier-report.md`
- `tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main/human-review-checklist.md`
- 本地 manifest：`tasks/evidence/P1-FIX-20261119-180000-prd-publication-on-protected-main/evidence.json`。原始 CLI 输出与 RV scripts 仅作为本地/runner 证据上传，不进入代码 diff。

### Human-Confirmed

- [ ] 检查 #398 恢复的实际结果及分支上的 PRD 链接。

### Implementation Acceptance

- [x] 显式分支发布支持受保护的主分支，默认行为兼容（rv-1、rv-4；证据：`rv-1-protected-main-publish.txt`、`rv-4-compatibility-gates.txt`）。
- [x] 失败信息说明原 Issue 的状态与恢复入口，恢复幂等并拒绝冲突（rv-2；证据：`rv-2-recovery-idempotent-conflict.txt`）。
- [x] 按 Issue 执行使用分支 PRD 与执行器覆盖（rv-3；证据：`rv-3-runner-branch-prd.txt`）。
- [x] CLI 文档和随包 operator skill 已同步（rv-4；证据：`rv-4-compatibility-gates.txt`；MkDocs build 记录见同一文件）。

## 10. Functional Requirements

- **FR-1**：协作者可显式将 PRD 发布到任务分支，并在发布和提交链接写回成功后按请求加入 ready 队列。
- **FR-2**：恢复入口复用原 Issue；相同内容幂等，远端内容冲突时拒绝覆盖。
- **FR-3**：按 Issue 执行时，runner 使用任务分支 PRD 与其中声明的生命周期执行器覆盖。
- **FR-4**：默认发布与不发布行为保持兼容；失败信息说明 Issue 状态与恢复入口。

## 11. Non-Goals

不修改主分支保护、不改 backlog 主分支扫描、不实现多平台 PRD、不在实现任务中操作 #398/#399。

## 12. Risks And Follow-Ups

已有分支可能承载实现，发布入口必须避免覆盖。固定提交链接与实际任务分支后续演进需保持语义明确。#398 恢复成功后再处置 #399。

## 13. Decision Log

### Decision Log

需求方确认先做 #400、显式启用分支发布、首版按 Issue 执行，并以复用 #398 完成实际验收。

### Final Reconciliation

- Interpretation: 实现符合显式任务分支发布、原 Issue 恢复、runner 分支读取和旧默认兼容的确认范围；#398 实地结果留给维护者。
- Public behavior and contracts: 新选项显式启用；默认 main 发布与 `--no-publish-prd` 保持原语义；冲突拒绝覆盖；失败 Issue 不会被提前 ready。
- Related PRD status: #398/#399 未被读取或修改；#398 保留为后续人工验收与恢复流程。
- Requirements and risks: 四项实现要求均由对应 rv 证据覆盖；RV-3 的 Agent fixture 在观察实际 worktree 后退出，因此不宣称已验证付费 Agent 实现或 Draft PR 发布。
- Validation blocker: PRD 行为验证没有未通过项；仍未解决的是 PR 集成状态。当前代码 head `e2ac3012e589adef357c6e605e2dbea4d91c63ec` 与本地 `main` (`51461449cb1069113b3d6a827297f92c16bcff2f`) 的三方内容合并检查无冲突（`git merge-tree --write-tree main HEAD` 返回树 `f49ac9ffc89af8eae3b97aa0bdc0a2c6cd2750e2`），先前报告的 runtime 文件内容冲突已解决。但提交历史仍分叉 10 ahead / 15 behind，merge base 为 `f56f2d1fd1e7b3ee6f1367c84bd7ec8487b37dde`；PR #405 的远端 base SHA 仍是旧值 `6dceaa0ba94c285776e76109a324051d1dd04d79`。因此不能把旧 base 上的 `mergeable=true` 或本地内容合并检查表述成已经同步到当前 `main` 或完成 GitHub 集成验证。
- Validation follow-up: 在允许的分支工作流中将 PR 分支历史同步到当前 `main`，更新 PR #405 正文与验证评论到当前 head/base 和最新证据，再检查 GitHub 对新 base 的集成状态；随后由人工按 9.1 检查真实 #398，Human-Confirmed 项保持未勾选。当前任务的 worktree-only / no-merge / no-push 限制下未执行这些远端或历史操作。
- 自动验证结果：当前代码 head `e2ac3012e589adef357c6e605e2dbea4d91c63ec` 上最新 `kc evidence check` 4/4；五个相关测试文件关闭 testmon 后 203 passed；Round 5 独立 verifier 对当前源代码和测试树判为 `PASS`，其文件哈希与此 head 相同。`just lint --reuse` 通过；本次 `just lint --full` 与 `just test` 分别因无未提交代码变更及已有 head/tree 成功标记而跳过相应全量检查，RV-4 的兼容性门禁仍通过并包含 lint 与 MkDocs build 结果。远端 PR 正文/评论仍是旧快照，详见证据报告。

## 14. Change Log

- 2026-11-19：根据 #400 和对话中确认的范围创建 PRD。

### Complete protected-branch publication and recovery
- Type: implementation / validation / documentation
- Before: 实现验收项未完成，验收状态为 `⬜ 未开工`。
- After: 四项 executor-owned 实现验收项均有 rv 证据并已勾选，状态为 `🧍 待人工验收`；#398 人工验收仍未完成。
- Reason: 完成显式任务分支发布、原 Issue 恢复、runner 分支 PRD 解析，并按验证计划执行自动检查。
- Impact: 记录已验证的交付结果；需求范围不变，#398/#399 保持未操作。
- Review: 独立 verifier `PASS`；Human-Confirmed 项留给维护者实地检查。

### Correct validation records after first independent review
- Type: evidence / validation / documentation
- Before: PRD 清单引用部分旧证据名，manifest 缺少 rv-3/rv-4，报告与人审清单未生成，§7.6 仍是 prose bullets；首轮独立 verifier 因这些交付记录缺陷判为 `BLOCKER`。
- After: 更新 §7.6 为结构化 YAML oracle，补齐四项 manifest、验证计划、证据报告和人审清单；等待第二轮独立复核前，Implementation Acceptance 项保持未勾选。
- Reason: 确保每项验收都能追溯到正确的真实入口输出，避免在 verifier 通过前宣称完成。
- Impact: 实现范围和行为不变；#398/#399 仍未操作，只有人审项保留给维护者。
- Review: 第 1 轮 `BLOCKER`；待独立 verifier 第 2 轮复核后记录最终状态。

### Complete independent verification and handoff
- Type: validation / evidence / documentation
- Before: 第 1 轮独立 verifier 对不完整交付记录判为 `BLOCKER`，Implementation Acceptance 项保持未勾选，验收状态为 `⏸️ 验证阻塞`。
- After: 修正后的 manifest 通过 `kc evidence check` 4/4；独立 verifier 第 2 轮 `PASS`；四项有证据的 Implementation Acceptance 已勾选，状态为 `🧍 待人工验收`；#398 Human-Confirmed 项仍未勾选。
- Reason: 只有独立复核确认实现和证据链完整后才完成执行侧验收。
- Impact: 需求与代码范围不变；未访问或修改 #398/#399，真实 #398 检查留给维护者，归档由 runner 处理。
- Review: 独立 verifier 第 2 轮 `PASS`；RV-3 仅证明真实 runner worktree 的 PRD 路由与执行器选择，不宣称 Draft PR 已发布。

### Resolve pre-PR review findings and revalidate
- Type: implementation / validation / documentation
- Before: pre-PR review found that repeated recovery could retarget the pinned PRD link to a later branch tip, and publication resolved its base through shared `FETCH_HEAD`; tracked evidence still recorded the earlier 174-test snapshot.
- After: recovery preserves the original commit when it contains the same PRD bytes; base fetch uses a unique temporary ref; evidence links and records point to the archived PRD and final 176-test result. The Human-Confirmed #398 item remains unchecked.
- Reason: keep the Issue link anchored to its published PRD snapshot, isolate concurrent Git fetches, and make final-tree evidence traceable.
- Impact: publication defaults and ready ordering are unchanged; all four RV items pass on the repaired tree.
- Review: independent verifier Round 3 `PASS`; final `kc evidence check` 4/4, targeted pytest 176 passed, reuse/full lint and MkDocs build passed.

### Refresh evidence for current review findings
- Type: validation / evidence / documentation
- Before: the in-tree verifier report stopped at Round 3, the latest local evidence manifest still summarized 174 targeted tests, and the PR description/comment referenced older heads.
- After: all four RV commands were replayed against source head `a914f022`; `kc evidence check` passed 4/4, RV-4 recorded 176 targeted tests, and a separate Round 4 verifier passed the implementation and RV evidence. The lifecycle read-failure docstrings are confirmed correct.
- Reason: tie the review response to the current code snapshot and refresh the evidence that had drifted after later source repairs.
- Impact: no product source changed; all four behavior oracles still pass. The branch remains 7 commits ahead / 15 behind main with one real orchestration-runtime merge conflict; this evidence does not claim that the conflict is resolved or the PR integration-ready.
- Review: independent verifier Round 4 `PASS` for current implementation/RV evidence; integration remains blocked pending an allowed branch integration and a new validation snapshot.

### Reconcile current main and review metadata
- Type: validation / evidence / documentation
- Before: Final Reconciliation still described the old runtime conflict and `a914f022` validation snapshot; PR #405 and its validation comment still referenced older head/base and test evidence.
- After: Final Reconciliation points to current head `e2ac3012`, records a clean content-level three-way merge check against `main` `51461449`, and separately discloses the remaining 10-ahead / 15-behind history divergence and stale remote PR base metadata. Latest local evidence reports four RV items passed and 203 targeted tests passed.
- Reason: distinguish the resolved file-content conflict from the still-unsynchronized branch history, and keep local validation claims tied to the current tree.
- Impact: source behavior and human acceptance scope are unchanged; Git history and GitHub metadata remain untouched under the worktree-only/no-merge/no-push constraints. The real #398 human check remains open.
- Review: Round 5 independent verifier `PASS` covers the unchanged current source/test hashes; the fresh local `git merge-tree` check is not a history sync or GitHub integration verdict.
