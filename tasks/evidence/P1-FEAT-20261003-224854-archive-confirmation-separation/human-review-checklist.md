# 人工验收清单 · 归档与确认语义分离——执行侧完成即归档、人工验收独立记录

- PRD：`tasks/archive/P1-FEAT-20261003-224854-archive-confirmation-separation.md`，已归档，横幅为 🧍 待人工验收。
- PR：
  - 代码 PR 是 ZataZhang/keda#185，已以 `c2fcc4b5` 合入 main。
  - 归档 PR 见 <https://github.com/ZataZhang/keda/pulls?q=is%3Apr+head%3Adocs%2Farchive-confirmation-separation>，合并即验收，见文末。
- 状态：**执行侧已交付，独立 verifier 给出 `PASS`，等你对 2 个决策和 1 项呈递过目表态。** 这 3 项就是 PRD §9.2 Human-Confirmed 的 3 个空框。
- 证据报告：`open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/P1-FEAT-20261003-224854-archive-confirmation-separation.evidence-report.md"`
- 本次没有截图，也没有多选项，所以只有这份 Markdown，没有交互版 HTML。

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时，一句"三项都同意"就够了。也可以直接合并归档 PR：PR 正文带"合并即接受"声明，合并就等于三项都同意。

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 归档只看执行侧，人审项带着 🧍 横幅一起归档 | §2 决策一 → §9.2 Human-Confirmed 第 1 项 | 目录不再代表"人已验收"。若你其实要"归档 = 已验收"，已归档的 PRD 会被误读成你看过了 |
| 2 | 人回答之前，自动合并一律不合并 | §2 决策二 → §9.2 Human-Confirmed 第 2 项 | 这道闸漏了，自动化就会替你验收并合进 main；反过来，误标的老 PRD 会一直停着等你 |
| 3 | §9.1 的两份记录你已亲眼看过 | §9.1 → §9.2 Human-Confirmed 第 3 项 | 没看就确认，前两项的"验收"就只剩机器背书 |

**术语先解释一句**

- *runner*：keda 的执行器，负责认领 Issue、跑 agent、提交并发 PR。
- *归档*：把 PRD 从 `tasks/pending/` 移到 `tasks/archive/`。按新规范，它只表示"执行侧做完了"。
- *执行侧条目 / 人审项*：PRD §9 验收清单里，执行者能自己拿证据证明的条目，以及 Human-Confirmed 组里只有人能回答的条目。
- *验收状态横幅*：PRD 标题下的 `验收状态` 那一行，有 ⬜ 未开工 / 🧍 待人工验收 / ✅ 已验收三种状态。归档以后，判断"人验收过没有"只看它。
- *`[~]`*：清单里"等 runner 门禁"的标记，不是"已完成"。
- *自动合并队列（Autopilot）*：daemon 每轮扫一遍已签核的 PR，依次 rebase、复验、合并。
- *closeout（收尾回合）*：交付检查没过时，runner 再开一轮 agent，只许改记录类文件。
- *oracle*：PRD 里事先写好、能逐条判真假的验收判据，即 rv-1…rv-7。
- *verifier*：独立复核证据的 agent，只读，结论是 `PASS` 或 `REJECT`。
- *负控*：在改动前的代码上跑同一用例，确认它会红；这样绿才有意义。

---

## 决策一 · 归档只看执行侧，人审项带着 🧍 横幅一起归档

**你在拍板的事**：执行侧条目全部完成，runner 就在交付时归档 PRD，哪怕 Human-Confirmed 还有空框。空框和 🧍 横幅原样保留；横幅与清单对不上时，既不归档也不发布。**如果判错**：PRD 在哪个目录不再说明你验收过没有。若你其实要"归档 = 人已验收"，这个改动会让已归档的 PRD 被误读成你已经看过，需要退回旧模型：留在 pending，等你确认后再补一个提交去归档。

**PRD 原话（§2 决策一）**：

> 风险：PRD 在哪个目录不再说明它有没有被人验收过，判断是否已验收要看横幅；今天横幅还停在"未开工"的 PRD，会因此多一轮收尾才能归档（更严格，但不会放过错误状态）。失败交付、执行侧未完成的 PRD 仍然绝不归档。
>
> **请确认：** 接受"归档只看执行侧条目是否全部完成——含待人工验收条目也照常在交付时归档，人审空框与 🧍 横幅保留，横幅不一致则不归档、不发布"吗？
>
> **验收：** 一份只剩人审空框、横幅为"待人工验收"的 PRD 经 runner 交付后位于 archive，空框与横幅原样保留、发布前检查放行；把它的横幅改成"未开工"再交付，则不归档并报"横幅不一致"。

**展开说**：

- **以前**：只要还有一项人审没回答，PRD 就留在 pending，你确认后还得再补一个提交去归档。新版 PRD 规范已经改成"归档 = 执行侧做完"，runner 原先跟它直接打架。
- **现在**：归档只说明工作做完了，你验收过没有交给横幅表达。横幅因此必须说真话，runner 在归档前和发布前都要核对它。核对不上，就开一轮只许改横幅那一行、外加一条变更记录的收尾回合。
- **边界**：
  - 横幅还停在 ⬜ 的 PRD 会多一轮收尾。
  - 本机 PRD skill 太旧、读不出横幅时，跳过这项检查。
  - 失败交付（失败 Draft PR）照旧不归档。
- **本 PRD 自己就是第一份按这条规则归档的 PRD**：它现在就在 archive 里，带着 🧍 和 3 个空框，等你回答。

**证据**（rv-1，真实 git 仓库 + 真实 skill；全文见证据报告「rv-1：交付前后的真实 git 记录」）：

```text
== after delivery ==
$ git status --short
R  tasks/pending/example.md -> tasks/archive/example.md
$ ls -A tasks/pending
(empty)
== archived PRD: banner line and Human-Confirmed group ==
> 🧍 **验收状态**：待人工验收 — 执行侧已完成，仅剩 1 项 Human-Confirmed 未确认，证据包见 §9。
### Human-Confirmed

- [ ] decision 1: answered by a human
```

- **"把横幅改成未开工再交付"那一半由 rv-3 覆盖**（verifier 项）：四种横幅不一致的情形在交付和发布两道门禁都报 `ACCEPTANCE_BANNER_MISMATCH`，PRD 都没有移动，9 passed。
- **负控**：改动前同一用例是红的，报 `FileNotFoundError: … tasks/archive/example.md`。旧代码遇到人审空框就不归档。
- **自举**：归档后的本 PRD 同时过了 PRD 技能的归档检查和 keda 自己的发布前检查，见 `raw/installed-v5/archive-dogfood.log`。
- **复跑（可选）**：

  ```bash
  cd /Users/zata/code/keda-worktrees/feat/archive-confirmation-separation && .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider --no-header -v -rP tests/test_agent_runner_prd_delivery.py::test_delivery_archives_prd_awaiting_human_review_real_git
  ```

**你的回复**：`同意` / `有差异：<说明>`

---

## 决策二 · 人回答之前，自动合并一律不合并

**你在拍板的事**：自动合并队列只要发现 PRD 的人审组还有没回答的项，就记为"等人验收"并跳过：不 rebase、不复验、不合并。没回答的项包括空框，也包括被误标成 `[~]` 的人审项；PRD 在 pending 还是 archive 都一样处理。**如果判错**：这道闸一旦漏掉，提前归档后自动化就会替你行使验收权，把你还没确认的改动合进 main。PRD 把这一点列为最高风险档 R3。反过来，保守口径的代价是：误把人审项标成 `[~]` 的老 PRD 会一直停着，等你改成 `[x]`。

**PRD 原话（§2 决策二）**：

> 风险：误把人审项标成"等门禁"的老 PRD 会一直停在"等人验收"，需要人把它改成已勾才放行——这是有意的保守：宁可多等，也不替人验收。
>
> **请确认：** 接受"自动合并只在人审组全部回答后才合并；已归档但仍待人工验收的 PR 一律跳过等人"吗？
>
> **验收：** 一条独立验证已通过、签核已完成的 PR，其 PRD 已归档且横幅为"待人工验收"：自动合并队列本轮显示"等人验收"，托管平台上没有任何 rebase 或合并动作；人勾选并把横幅改成"已验收"后，下一轮继续往下走。

**展开说**：

- **以前**：队列只看 pending 路径。PRD 一旦进了 archive，队列就看不到还有人审空框，会照常 rebase 并合并。
- **现在**：队列在 pending 和 archive 两处都找 PRD，只要还有没回答的人审项就跳过。你勾选并把横幅改成 ✅ 以后，下一轮照常往下走。
- **注意**：你在 PR 分支上勾选后，Issue worktree 要同步到最新 head，队列才看得到。这是既有行为，见 PRD §12。
- **归档 PR 不走这条队列**：它不是从 Issue 来的，由你手动合并。

**证据**（rv-5，经自动合并的公开入口 `process_merge_queue`，PRD 是真实文件；全文见证据报告「rv-5：自动合并两轮记录」）：

```text
== round 1 ==
exit_code=0 outcomes=['skipped_human_review']
github calls: ['list_issues_by_label', 'list_issue_comments', 'get_pull_request_context']
process calls: []
...
== round 2 ==
exit_code=0 outcomes=['merged']
```

- **第一轮**：没有 `merge_pull_request`，也没有任何 git 命令。
- **两轮之间**：测试从磁盘把人审项改成 `[x]`、横幅改成 ✅。
- **第二轮**：git 命令里出现了 `git rebase origin/main`。
- **另有参数化 5 例**：其中 `archive-~-skipped_human_review` 证明误标成 `[~]` 的人审项也会被拦下。
- **负控**：改动前 `assert ['merged'] == ['skipped_human_review']` 失败。旧代码看不到已归档的 PRD，直接合并了。
- **复跑（可选）**：

  ```bash
  cd /Users/zata/code/keda-worktrees/feat/archive-confirmation-separation && .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider --no-header -v -rP tests/test_agent_runner_merge_queue.py::test_archived_prd_awaiting_human_holds_until_the_human_answers
  ```

**你的回复**：`同意` / `有差异：<说明>`

---

## 第三项 · §9.1 呈递区过目

**你在拍板的事**：确认 §9.1 列给你的两样东西，也就是 rv-1、rv-5 的真实记录，你已经亲眼看过，而且就是你要的结果。本次没有前端改动，呈递物是终端记录原文，没有截图。上面决策一、二的"证据"已经嵌了两段记录的关键行；完整记录在证据报告对应小节。**如果判错**：没看就确认，前两项的"验收"就只剩机器背书。

**PRD 原话（§9.1 呈递表，原样）**：

| # | 你要看什么（对应 oracle） | 呈递物（交付时填实际路径） | 想自己复核？ |
|---|---|---|---|
| 1 | 只剩人审空框、横幅为"待人工验收"的 PRD 在交付时就进了 archive，空框与横幅原样保留（rv-1） | 证据报告「rv-1：交付前后的真实 git 记录」一节：`open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/P1-FEAT-20261003-224854-archive-confirmation-separation.evidence-report.md"` | 看归档后片段：横幅行仍是 🧍 待人工验收，Human-Confirmed 下仍有 `- [ ]`；终端记录里 after 段 `git status --short` 为 `R  tasks/pending/example.md -> tasks/archive/example.md`、pending 目录为 `(empty)` |
| 2 | 自动合并遇到已归档、人审未答的 PR 只会"等人验收"，人回答后才继续（rv-5） | 同一份证据报告「rv-5：自动合并两轮记录」一节（同上 `open` 命令） | 第一轮 `outcomes=['skipped_human_review']`、github calls 里没有 `merge_pull_request`、`process calls: []`；第二轮 `outcomes=['merged']` 且 process calls 含 `git rebase origin/main` |

**你的回复**：`同意`（即"已看过"） / `有差异：<说明>`

---

## 由执行器与 verifier 自验、不需要你逐项看的部分

- **其余 oracle**：rv-2（执行侧未完成不归档）、rv-3（横幅不一致不归档不发布）、rv-4（发布前检查的三种情形）、rv-6（PR 正文的路径与声明句）、rv-7（兼容回归），以及 FR-6（agent 提示词）。全部通过，见证据报告「Oracle 结果」。
- **门禁**：
  - Drift Guard 10 行全部符合；
  - 全量 pytest `2786 passed, 1 skipped`；
  - 架构守卫、ruff、`mkdocs build --strict`、`just test`、`just lint --repo` 通过；
  - 没有依赖、schema 或前端改动。
- **树绑定**：去掉 PRD 文件与证据目录之后的代码树 id 是 `81b833554c9c8be9481f21fa7d80a88a2c204468`。合并后用它核对"合进去的就是验证过的那份代码"。
- **独立 verifier `PASS`**：`open "/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation/tasks/evidence/P1-FEAT-20261003-224854-archive-confirmation-separation/P1-FEAT-20261003-224854-archive-confirmation-separation.verifier-report.md"`。它列了 3 条 NON-BLOCKING，都已在证据报告里披露。

## 需要你知情、不需要表态的流程偏差

- 代码 PR ZataZhang/keda#185 先于独立 verifier 合入了 main，这是你 2026-10-04 的决定。verifier 后来审的就是 main 上的代码，结论 `PASS`。
- 本机的 v5 skill 是在 #185 合并之后才装上的，用 `install_remote_template_skills(force=True)` 代替了 `iar init`。CI 从模板 main 安装 skill，不受影响。
- 归档这一轮 `just test` 的 testmon 增量档显示 `no tests ran`。全量覆盖另跑了一次关掉 testmon 的 pytest，结果为 `2786 passed`。
- `ed30c170` 的 push CI 被取消、没有结论。那个状态的"绿"由本地全量证明。

## 本次明确不涉及

- 合并时自动补记验收，即比对代码树、自动勾选、自动翻横幅。这属于后续 PRD。
- 路线图上"已归档、待人工验收"的视图。
- 数据库、前端、第三方依赖、合并策略与自动合并开关。

## 你回答之后会发生什么

- **合并归档 PR，或在对话里回"三项都同意"**：agent 只回填验收记录，也就是勾选这 3 个框、横幅改为 ✅ 已验收、追加一条 Change Log。如果是合并（squash 也算），还要先核对合并后的代码树仍是 `81b83355…`。
- **写了"有差异"**：
  - 如果是 PRD 自己的 oracle 或范围没达成，PRD 移回 `tasks/pending/` 重开；
  - 如果是需求本身变了，另开关联 PRD。
