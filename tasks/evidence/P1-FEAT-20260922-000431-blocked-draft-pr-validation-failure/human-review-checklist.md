# 人工验收清单：跨 claim 交接失败上下文，并发布人可审阅的失败 Draft PR

打开方式：`just prd review tasks/pending/P1-FEAT-20260922-000431-blocked-draft-pr-validation-failure.md`
（本目录另有同名 `.html` 交互版，逐项点选后会把结论汇编成可复制的 Markdown。）

PRD：`tasks/pending/P1-FEAT-20260922-000431-blocked-draft-pr-validation-failure.md`
分支：`blocked-draft-pr-validation-failure` · base `87ab96ee` · 变更集指纹 `8a3dc39acad9`

## 先说清楚：这次要你判的是 4 件事

机器门禁（rv-1…rv-4，共 14 个新用例 + 全量 2488 测试 + `mkdocs --strict` + `just lint --repo`）
已经全绿并绑定最终树，**不需要你复核**。下面每一项都是"只有你能判"的：要么需要真实 GitHub
仓上的人工操作，要么是一个需要你签字接受的取舍。

判错的后果各不相同，所以在动手前先看清第一项。

**回答格式**：每项给一句结论即可 —— `同意` / `选 X` / `差异：<具体不符的点>`。
四项可以在一次回复里一起给。

---

## 1. 真实 GitHub 上，失败 Draft PR 是否真的可用（§9.1 第 1 行）

**你在判什么**：这套机制在真仓里到底跑不跑得通——交接评论是否出现、Draft PR 是否出现、
两者是否同源。**判错后果**：如果实际不成立而你没发现，我们会把一个只在 fake client 上成立
的功能当作已交付合入主干，下一轮 claim 仍然拿不到上下文，而这正是本 PRD 存在的理由。

PRD 原文（§9.1 第 1 行）：

> （**手动**）耗尽后有安全 commit 时，出现 Draft PR，正文说清"上一轮做到哪、卡在哪、缺什么、
> 下一步"并回链原始诊断；同时 Issue 上有一条带 marker 的交接记录；PRD 仍在 `tasks/pending/`

**展开说**：runner 的 recovery 预算烧完时，它会先在 Issue 上发一条带
`<!-- iar:failure-context checkpoint=… attempts=… verifier=… evidence=… -->` 的评论，
然后把同一份内容（外加一段指向该评论的链接）作为 Draft PR 的正文发出去。你要确认这两件
东西在真仓里都出现了，且说的是同一件事。

**怎么验**：

```bash
# 前提：有一个真的跑到 recovery 耗尽的 Issue
gh issue view <N> --comments | grep -n "iar:failure-context"
gh pr list --head issue-<N> --state all
gh pr view <PR> --json body | grep -n "iar:failure-context-ref\|#issuecomment-"
ls tasks/pending/ | grep blocked-draft-pr   # PRD 必须还在 pending
```

**已通过的部分**：正文措辞与 marker 内容由真实渲染器产出，原文已存成文本证据，可直接读：
`rv-1b-rendered-handoff-record.txt`（三个场景：判红 / 未形成结论 / 无快照）。你不必为了看
"记录长什么样"去跑真仓——那一格已经交叉核对过；**你要判的是它在真 GitHub 上成立**。

---

## 2. 这个 Draft PR 不能因为"看起来像 PR"就被合掉（§9.1 第 2 行 · 风险最高）

**你在判什么**：安全边界。**判错后果**：未验收的代码进了主干。

PRD 原文（§9.1 第 2 行）：

> （**手动**）该 PR 无法被签核、合并或归档，即使 Draft 标志被手动取消

**展开说**：本 PRD 刻意**不新增任何门禁**，靠的是既有那条"缺 `validation/verifier-passed`
标签即拒绝签核、合并、归档"（`agent_runner_merge_queue.py`，受
`autopilot.require_verifier_pass` 控制）。这个设计假设的唯一软肋是：Draft PR 现在会带着
"看起来挺完整"的正文出现在 PR 列表里，人（或某个自动化）可能手滑取消 Draft 直接合。
所以这一格要你亲眼看一次门禁真的拦住。

**怎么验**：

```bash
gh pr view <PR> --json labels,state,mergeable      # 期望：无 validation/verifier-passed
# 手动取消 Draft 后，再走一次签核 / 合并入口，期望：被明确拒绝（记录拒绝原文）
```

需要记录：受测 PR 的 head/tree、labels、checks 与 pending PRD 路径。
判定依据是既有门禁，本 PRD 未新增任何门禁——如果它**没有**被拒，那是既有门禁的缺陷被本
功能暴露出来了，必须回来改本 PRD 的前提。

---

## 3. 交接上下文确实跨过 claim 边界了吗（§9.1 第 3 行）

**你在判什么**：主要收益是否落地。**判错后果**：功能"测试全绿"但下一轮 agent 其实没读到
东西，"一直验证不通过"的问题原样存在。

PRD 原文（§9.1 第 3 行）：

> 交接上下文确实回灌给了下一轮：新 claim 的 prompt 里出现上一轮结论

**展开说**：新 claim 复用既有提交失败时，会构造续作 prompt；现在它会先从 Issue 评论里
按 latest-wins 捞出**最近一条**交接记录（限量、超长截断），塞进 prompt，并明确标注"这是上一轮
的陈述，不是裁定"。

**已有证据（机器取证，可直接读）**：`rv-2-context-reflow.txt` 8 个用例全 PASS，其中两条走
`_process_ready_issue` 真实入口。请核对 prompt 片段：应含上一轮 verifier 判定摘要与缺失呈递物，
且**不含**更早的过期记录。这一格你只需读文件确认，不必跑真仓。

---

## 4. 三项主动放弃的能力，你是否接受（§9.2 Human-Confirmed + §12）

这三条不是"没做完"，是 PRD 明确决定不做（§11 Non-Goals、§13 D-03/D-08/D-11）。
**判错后果**：将来有人把它当漏写去"补上"，反而造出第二个事实源。

### 4a. 失败性质不进机器状态

> （§9.2 Human-Confirmed 第 2 项）确认"失败性质的区分只在正文里、不进机器状态"这一取舍被接受

产品失败还是"审核事故（verifier 自己坏了/超时/没吐判定）"，**只由 Agent 在正文里陈述**，
机器不判定、不打标签。代价是这段文字不可机器核对：executor 理论上可以把产品失败说成审核事故。
缓解是两条既有硬约束：正文必须回链原始诊断供人对照；且无论正文怎么写都改不了标签与合并态。
最坏情况是"人读到了误导说明"，不是"未验收代码被合入"。

- [ ] 接受这个代价
- [ ] 不接受 —— 要求补一个机器判定器（这会推翻 D-03，需要重开 PRD）

### 4b. 不新增 `validation/blocked` 标签

> （§9.2 R3 组）失败 Draft PR 不得被签核、合并、归档 —— 完全沿用既有门禁

理由：合并门禁已由"缺 `validation/verifier-passed`"覆盖，再加一个 `validation/blocked`
只会变成第二个事实源。

- [ ] 接受
- [ ] 不接受 —— 要求新标签（需同时新增门禁，改动面显著变大）

### 4c. 限流打断后，下一轮仍然没有上下文

> （§13 D-11）`ProviderCapacityError` **不写**交接记录

限流不代表本轮工作没通过，写成失败会污染"交接 = 这轮没通过验收"的语义。**已知代价**：
被限流打断的 claim，下一轮只能靠工作树上的 checkpoint 反推。这是刻意接受，不是漏写。

- [ ] 接受，将来若实测空转成本够高再单开 PRD
- [ ] 不接受 —— 现在就放宽到限流也写

---

## 5. 一处与 PRD 原文不一致的实现选择，请确认知情

PRD §6/§7.1 原写"PR 正文由既有 `IContentGenerator` 生成（把交接 payload 作为上下文喂进去）"，
同时 §7.2 要求发布原语本身不改。实测这两条不能同时成立：`create_draft_pr` 内部自己组装上下文、
不接受外部注入，且内容生成被关闭时**根本不调用 generator**，回链就会丢——而 FR-6 要求"即使内容
生成被关闭"回链也必须在。

实现改为：发布原语不动，在发布**之后**补写一次 PR 正文尾部（读 `get_pull_request_context` 的
body、按锚点替换后 `update_pull_request_body` 写回）。已记入 §14 Change Log「实现发现一」。

- [ ] 知情并接受
- [ ] 要求回到"改发布原语签名"的方案

---

## 附：本清单之外已确认无事的项目

- 数据库结构、前端页面：本 PRD 无涉及。
- 既有正常成功路径（green/yellow）、Issue 失败评论、publish recovery、forbidden-path 与
  remote/branch 安全检查：14 个新用例 + 全量 2488 测试通过，未回归。
- `require_prd_archived=False` 只有耗尽路径能传：已固化为常驻白名单断言，不依赖人工抽查。
