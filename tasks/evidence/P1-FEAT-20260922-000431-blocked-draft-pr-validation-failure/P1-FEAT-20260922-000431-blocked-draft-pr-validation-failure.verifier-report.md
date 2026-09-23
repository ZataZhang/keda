# 独立 verifier 审查报告：跨 claim 交接失败上下文，并发布人可审阅的失败 Draft PR

| 项 | 值 |
|---|---|
| 审查者 | 独立 verifier Agent（与 executor 不同上下文，未参与实现） |
| 受审对象 | 分支 `blocked-draft-pr-validation-failure`，base `87ab96ee`，工作树未提交变更 |
| 首轮结论 | **PASS WITH FINDINGS** —— 1 个 MAJOR 必须在验收前修 |
| 复审结论 | 首轮 8 项发现全部处置（1 修代码 + 4 补测试 + 1 修措辞 + 2 记为已接受边界）；修后全量 **2493 passed**、`mkdocs --strict` EXIT=0、`just lint --repo` EXIT=0 |
| 最终树 | base tree `07efe13a172eeea10067645abdbfdde3347bae22`，变更集指纹 `8a3dc39acad9c78c5d6a34a5a0138a1a538e7627` |

verifier 独立跑过定向 87 例与全量测试（均 exit 0），并逐条实证复核了 FR-2 / FR-8 / FR-9 /
FR-12 与双导入的类同一性。它**没能**独立复核 rv-4 的 docs build（会话权限拦下
`uv run mkdocs build --strict`），该项由执行器在同一最终树上补跑并记录为 EXIT=0。

## 发现与处置

### 1. MAJOR — FR-11：渲染步骤不在任何守卫内，可以掩盖原始异常 · 已修

`_record_failure_handoff` 只包了评论 / 发布 / 回链三步，而 `attempt_results` 取值、
`resolve_issue_evidence_relpath`、`format_failure_context_comment`（含其对
`parse_verifier_verdict` 的延迟导入）都在 try 之外。verifier 实证：让渲染器抛
`RuntimeError` 会直接从 `_process_ready_issue` 逃出，`MaxRetriesExceededError` 降级成
`__context__`；因为抛的是别的类型，agent fallback 阶梯（只接 capacity / max-retries）
也接不住，最终由 runtime 的通用 handler 拿渲染错误去 `_mark_issue_failed`——正是
FR-11 与 §12 禁止的"把验证没过变成报告写挂了"。

**处置**：把 `_record_failure_handoff` 改成整层守卫，实际步骤移入
`_write_failure_handoff`；三步各自仍吞异常以保留"部分成功"的日志粒度。补
`test_handoff_render_failure_still_preserves_original_exhaustion`，断言上抛对象的
**具体类型**仍是 `MaxRetriesExceededError`（两者同为 `RuntimeError` 子类，只有比
`type(...)` 才有判别力），且无评论、无发布。

### 2. MINOR — rv-2 场景③的测试是同义反复 · 已修

`handoff_section` 为空时仍被 `"\n".join` 塞进列表，多产生一个空行，于是"无记录时 prompt
与本功能不存在时逐字一致"并不成立；而原测试拿改动后的同一个函数跟自己比，实现就算注入了
任意文本也照样通过——**该 oracle 格子实际零判别力**。

**处置**：空段落改为**整个不插入**（`failure_section` / `verification_summary` 保持原样，
那是本功能之前就有的形态）。测试改钉一份**从改动前实现实跑采集的金标准**
`_PRE_FEATURE_CONTINUATION_PROMPT`，并加反向断言"给了记录就必须变长变内容"。

### 3. MINOR — FR-2 缺"缺失呈递物"独立字段；verifier 摘要只读末轮 · 已修

verdict 只从 `attempt_results[-1].detail` 解析。耗尽时末轮常是更早某道门禁拦住的，会把
上一轮**真实的 RED** 误写成"没有形成结论"，把接手方支去查一个不存在的问题——恰是本 PRD
要消除的那类误导。缺失呈递物也只隐含在引用的门禁文本里，没有独立字段，rv-1 也从未断言它。

**处置**：verdict 改为从最近一次**往前找第一个真正形成的判定**，并在判定轮次 ≠ 末轮时把
"判定记于 attempt N、随后跑到 attempt M"写进正文。新增
`_extract_named_deliverables`，原样摘出门禁点名的 `rv-<n>` / `rv-<n>-file.ext`，并显式声明
"本记录不判断它到底缺没缺"（保持事实转述，不引入判定器）。补两条测试。

### 4. MINOR — rv-3 违反自己的 mock_boundary · 已修

PRD 要求"安全筛选…真实"，但原测试把 `checkpoint_uncommitted_progress` 直接 monkeypatch 成
`None`；一个"对只含禁改路径的改动错误返回 SHA"的实现照样能过。

**处置**：新增 `test_exhaustion_with_only_forbidden_changes_publishes_nothing`，在真实 git 仓
里只改 `.env`，让**真实**的 checkpoint 安全筛选自己判定；断言 `.env` 不在任何历史提交中、
仍作为未跟踪文件留在工作区、`checkpoint=none`、发布原语未被调用。

### 5. MINOR — "PR 不含 validation/verifier-passed" 只断言了正文文字 · 已修

判据是标签与当前 tree 的证据，不是正文里那句话。

**处置**：新增 `test_exhaustion_never_grants_the_verifier_passed_label`，检查
`edit_issue_labels` 的实际 add 列表里没有 `validation/verifier-passed`。

### 6. NIT — marker 的 `verifier=` 派生在真实形态下脆弱 · 部分随 #3 缓解

verdict 依赖 `iar:verifier-verdict` marker 能在 `findings=response_text.strip()[:4000]`
的截断里活下来；verifier 通常把 marker 放在最后，长报告会把它截掉，于是真 RED 被读成
"no verdict formed"。**不会误报 PASS**（只有 red / 缺 marker 才走到这里），因此只降级措辞。
#3 的多轮回溯进一步降低命中概率。**残余风险记账**：跨 attempt 也没有 marker 时仍会报
"未形成结论"，属可接受的保守方向（宁可少说，不假称判定）。

### 7. NIT — 评论写入失败时 PR 会指向一条不存在的评论 · 已修

`_resolve_handoff_comment_url` 可能退回 Issue URL；更糟的是 `comment_issue` 本身失败时，
PR 正文仍宣称"Authoritative handoff record"。**处置**：`_attach_handoff_ref_to_draft_pr`
接收 `handoff_comment_posted`，为假时措辞降级为"交接记录未能写入，请查看 Issue 评论列表"。

同类残余：**人类评论逐字引用该 marker 会劫持 latest-wins**。本功能不做鉴权也不为此加状态，
留作已知边界（Issue 本就是协作面，且下一轮 prompt 明确标注"陈述而非裁定"）。

### 8. NIT — 安全措辞夸大与失效的既有不变量 · 已修

`agent_runner_commit.py` 的 docstring 断言"checkpoint 永远不会被推送或合入"——本功能让前半句
失效；已改写为"成功路径仍不推送，唯一例外是耗尽交接发布的 Draft PR，但缺
`validation/verifier-passed` 使其永不被合入"。同时 handler 内那条"其余安全检查全部照旧生效"
的说法被收敛：改动已被 checkpoint 提交，push 侧按未提交状态取变更集的 forbidden/evidence
检查对已提交内容实为空转，真正的禁改隔离发生在 checkpoint 的 staging。

## verifier 判定为成立的项

- **FR-2 收窄**：守卫位置正确；限流与 Ctrl-C 零评论零 PR，且把 hook 挂到整个 except 元组会
  让负向测试转红（说明这两条断言真有判别力）；`KeyboardInterrupt` 从不触碰
  `attempt_results`；三类异常的既有 checkpoint 行为未被改动。
- **FR-8**：全仓 `require_prd_archived=False` 只出现在耗尽 handler 与既有的
  `create_prd_from_issue.py`；默认值仍为 `True`；branch / remote / forbidden / evidence 调用
  仍执行。（verifier 提示白名单是字符串匹配，会漏掉 `require_prd_archived = False` 或经变量
  传参的形式；black/ruff-format 会规范化空白，故当前可接受。）
- **FR-9 / FR-12**：merge queue、`github_labels.py`、`apply_verifier_verdict_to_pr` 未被触碰；
  无新标签 / 枚举 / 判定器（静态断言 + 独立 grep 一致）。
- **回灌限量**：latest-wins 反向遍历 + 保头截尾且显式标注，关键字段均在前 ~1KB 内。
- **双导入的类同一性**：模块级与函数内延迟导入都解析到 `agent_runner_failure` 里同一个类，
  `isinstance` 判定可靠。

## 结论

**PASS（发现已全部处置）**。没有削弱任何门禁，核心闭环经真实入口证明可用。

最强的端到端证据是
`tests/test_agent_runner_checkpoint.py::test_next_claim_reflows_latest_handoff_record_into_continuation_prompt`：
它驱动真实的 `_process_ready_issue`，证明上一轮写在 Issue 上的交接评论确实跨过 claim
边界、进入真正传给 `run_agent_until_committed` 的 continuation prompt，并负向断言过期记录
不会混入——这正是本 PRD 的标题性收益。

## 仍未被机器覆盖的两格（按 PRD 主动放弃）

§9.1 第 1、2 行（真 GitHub 上的失败 Draft PR 呈现，以及它与既有签核 / 合并 / 归档门禁的组合
行为）由 PRD 作者本人手动验证，runner 不产出该证据、也不因缺它而拦下交付。verifier 确认这一
点在 PRD §7.3 / §9.1 / §12 中被如实声明，未伪装成已自动化。
