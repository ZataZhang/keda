# 人工验收清单 · 任意 Issue 可执行（Issue-first 交付入口，Issue #215）

- PRD：`tasks/pending/P1-FEAT-20261006-122336-any-issue-execution.md`（交付时由 runner 归档到 `tasks/archive/`），横幅 🧍 待人工验收。
- Issue：<https://github.com/ZataZhang/keda/issues/215>。代码 PR 由 runner 创建；PR 正文带「合并即验收」声明，**合并就等于下面 6 项都同意**，不必再到对话里逐条回。
- 状态：**执行侧已交付**——rv-1..rv-11 全绿且各自负控实测为红；等你对 6 项 `Human-Confirmed` 过目表态。独立 verifier 复核、归档、`Human-Confirmed` 勾选不由本清单代答。
- 证据报告：`open "tasks/evidence/P1-FEAT-20261006-122336-any-issue-execution/P1-FEAT-20261006-122336-any-issue-execution.evidence-report.md"`
- 验证计划：`open "tasks/evidence/P1-FEAT-20261006-122336-any-issue-execution/P1-FEAT-20261006-122336-any-issue-execution.verification-plan.md"`
- 原始证据目录（含真实运行日志）：`open ".iar/evidence"`；机器可读 manifest：`open ".iar/evidence/evidence.json"`
- 本地呈递页（人审项 + 真实日志摘录 + GitHub 直链，一条命令打开）：`open ".iar/evidence/human-review-bundle.html"`
  —— 该页在 gitignored 的 `.iar/` 下，属**本地呈递物**；跨机可看的持久呈递面是下面各项里的 GitHub 直链。
- 本清单**不含截图**：本次证据面是真实 Issue/PR 的 URL、真实运行日志与文件 diff，没有图形界面变化（`No frontend impact` 之外的 UI 面），故无 HTML 伴生截图页。

**怎么回复**：每项只回 `同意` / `知悉`，或 `有差异：<说明>`。六项都同意时一句「六项都同意」就够。

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 撤销「无 PRD 的 Issue 不得被当作可执行任务领取」这条产品边界，并接受两个后果：这类 Issue 证据门禁整体关闭（**前端改动也不要求截图**）、路线图原「描述不清必须先反问」规则失去强制力 | §2 决策一 → §9.2 第 1 项 | 合并后要让这类 Issue 重新有闸，等于改对外承诺 + 补一套从未存在的实现；现在改只是把 `--require-validation` 的默认值翻过来 |
| 2 | 新增 `--direct-pr` 档位：runner 侧质量检查**归零**（连仓库自己的测试命令都不跑），唯一门禁转移到 PR 上的 CI | §2 决策二 → §9.2 第 2 项 | 若你其实需要「快但仍有 reviewer」，正确档位是 `--fast-merge`；把 direct 当 fast 用会推出连测试都不过的 PR，而这事后无法由任何门禁拦下 |
| 3 | `--direct-pr` 的边界：只对**无 PRD 锚点**的 Issue 有效（PRD-backed 一律拒绝并提示改用 `--fast-merge`），且与 `--fast-merge` **互斥**（同时给出即报错，不静默取更强） | §2 决策二 → §9.2 第 3 项 | 放宽边界会让 PRD 流程留下未归档、未校验的 PRD；收紧（例如禁止与 `--all-ready` 同现之外的用法）会影响你赶时间的场景 |
| 4 | 知悉本次含**一处 R3（并发正确性）变更**：首次领取从裸 read-modify-write 补成真 CAS；CAS 若写错的反面风险是 Issue 永久卡住 | §2 决策三 → §9.2 第 4 项 | 这是「不双跑」的唯一保证（mutex 已收窄后不再兜底）。双跑 = 两个 agent 同时改同一分支；卡死 = 无人能领。要求单独拆 PRD 也是一种选择 |
| 5 | 接受「显式定向不再要求就绪标记」+「daemon mutex 收窄为只挡队列轮询」，并**知悉共存暴露的 TTL 回收隐患**（见下方「附加知悉项」） | §2 决策三 → §9.2 第 5 项 | 若只想做就绪准入、mutex 不动，你仍然会被本机 daemon 挡住而被迫 `--takeover`（即回到现状） |
| 6 | §1 行为样例表**逐行**符合预期（尤其就绪标记三行、daemon 共存三行、`--direct-pr` 四行） | §1 → §9.2 第 6 项 | 那 21 行就是本功能的验收口径；改一个单元格现在只是改实现，合并后再改是二次破坏对外承诺 |

**术语先解释一句**

- *就绪标记*（`agent/ready`）：含义是「要不要现在进队列」，**不是**「指派给哪台机器」。未就绪 → 任何守护进程都不领；已就绪 → 先轮询到的那台领。
- *显式定向*：`iar run --issue N` / `iar run <PRD路径>`，是人点名，本次起不受就绪标记约束。
- *CAS*：领取时写标记 → 回读全部竞价 → 只有最早的那个继续跑；落败方撤销自己的标记并退出。
- *负控*：把被验证的东西还原成错误状态再跑同一批断言，确认它会红；绿才有意义。
- *档位嵌套*：`--direct-pr` 跳过的范围严格包含 `--fast-merge` 跳过的范围。

---

## 先说一件翻车（已修复，独立复核确认）

第 1 轮独立 verifier 判 **FAIL**：rv-7 的负控注入（让 `--fast-merge` 也跳审核）残留在交付代码 `publish_stage.py` 里。根因不在被测代码，而在证据链自己——采集脚本用 `git checkout --` 还原一个**本次新增的未跟踪文件**，命令静默失败，随后的还原校验又用 `git diff`（对未跟踪文件恒为空），于是打印了「还原确认：与基线一致」这句假话。已还原为生产语义（`tests/test_agent_runner_direct_pr.py` 19 passed），脚本改用基线副本 + 逐字节比较 + 属性体内定位检查，负控独立重采为 `rv-7-nesting-negative-control.txt`；时间线核对证明 rv-7/rv-9 依赖的两份**真实运行日志都早于注入时刻**，真实证据本身没有被污染。第 2 轮复核判 **PASS**，并给出决定性判据（全仓库只有 `test_agent_runner_direct_pr.py` 会走到那个属性，因此注入窗口内采集的其他条目结果不受影响）。完整两轮记录：`open "tasks/evidence/P1-FEAT-20261006-122336-any-issue-execution/P1-FEAT-20261006-122336-any-issue-execution.verifier-report.md"`。

---

## 第 1 项 · 撤销「无 PRD 不得执行」这条边界

**你在确认什么**：这类 Issue 从此**没有自动化质量锚**。证据门禁整体关闭，前端改动也不要求截图；「描述不清必须先反问用户」变成可选——执行方会在需求含糊时自行补全而不是停下来问你。需要强验证时的替代路径是 `--require-validation`（默认关闭）。

**为什么建议接受**：代码里从来没有实现这条强制链，现状是「文档承诺了一个不存在的保护」，比明确放开更危险。

**证据（真实入口，可直接打开）**

- 手工建的、无锚点的 Issue 端到端跑出 Draft PR：<https://github.com/ZataZhang/keda/issues/216> → <https://github.com/ZataZhang/keda/pull/217>（`isDraft=true`、`OPEN`、`head=issue-216`）。日志里走的是默认档：agent 完成 → pre-commit 实跑 → pre-PR review 批准（`verdict=approved`）→ 出 PR，**全程没有 PRD 交付门禁**。
- 判别性负控（证明绿来自「无锚点」这条路径本身，而不是门禁整体被关掉）：给同类 Issue #219 人为加一条指向不存在 PRD 的 `- PRD path:` 锚点后，真实日志立刻出现 `PRD delivery check failed for Issue #219 …（1/5）（2/5）` 恢复循环，且 #219 **没有任何 PR**、工作树没有新提交。
- 门禁开关由旗标决定、不由 agent 决定：默认态 #220 不要求任何证据文件（含前端不要求截图）；`--require-validation` 态 #222 无证据时实际抛 `ValidationEvidenceError`，且前端改动 + 非图片证据仍被拒。
- 路线图不再自相矛盾：同一判据扫工作区 `ROADMAP.md` 命中 0 次，扫改动前版本命中 **5** 次。打开 diff：`open ".iar/evidence/rv-3-roadmap-diff.txt"`。

**回答方式**：`接受`，或 `我要改默认（把 --require-validation 改为默认开启）`。

## 第 2 项 · 新增 `--direct-pr` 档位

**你在确认什么**：该档位下 runner 侧**没有任何质量检查**——不跑 `runner.verification_commands`、不跑 pre-commit 验证、不起 pre-PR review agent、不起 post-PR supervisor、不跑 Phase 4.5 重验与独立 verifier。剩下的只有机械步骤：受控提交 → push → 建 Draft PR。因此**可以推出一个连测试都不过的 PR**，唯一门禁是 PR 上的 CI，而它只在「该仓库 CI 真的会跑且真的会红」时才是门禁。

**证据（同一 Issue 两档位真实对照）**

- 直发档：<https://github.com/ZataZhang/keda/issues/218> → Draft PR <https://github.com/ZataZhang/keda/pull/221>（正文带 `iar:direct-pr` 标注）。
- 快速档：<https://github.com/ZataZhang/keda/issues/220> → Draft PR <https://github.com/ZataZhang/keda/pull/225>。
- 阶段序列对照（同一位置，direct vs fast）：runner 验证命令 跳过/实跑；pre-commit 跳过/实跑；pre-PR review 跳过/**实跑 5 处**；post-PR supervisor 跳过/**实跑 2 处**；Phase 4.5 两档共同跳过。快速档那次还真实经历了 1 回验证失败 + 回收续作才走到发布——这就是直发档放弃掉的那层保护。
- CI 确实会落在这些 Draft PR 上（`gh pr checks` 实测：#221 的 Frontend Build / install.sh 矩阵 / Realistic Validation sign-off / Validate Template / wheel build 全部执行）。
- 本地对照表与日志：`open ".iar/evidence/rv-7-direct-vs-fast-stages.txt"`，原始两份日志 `rv-7-run-218-direct.log` / `rv-7-run-220-fastmerge.log`。
- 负控：让 `--fast-merge` 也跳过 pre-PR review → 钉住档位嵌套关系的 3 条测试 FAILED，证明「两档位可区分」不是恒真。

**如实披露（这一项没有做到的部分）**：行为样例里「故意让仓库测试失败仍出 PR 且 CI 变红」那一行，本次只证明了**前半句的机制**（直发档确实把验证命令整段跳过，因此失败无法阻断发布——由 `tests/test_agent_runner_direct_pr.py::test_direct_pr_skips_every_runner_gate` 钉住）与 **CI 会在 Draft PR 上运行**；没有真造一个红 CI 的直发档 PR 作为端到端样本（成本考虑）。同理「某仓库 CI 不覆盖改动＝完全无把关」这一条无法用本仓库证明，它是你接受该档位时必须背负的前提。

**回答方式**：`接受`，或 `我不需要这个档位（改用 .iar.toml 持久关 pre_pr_review）`。

## 第 3 项 · `--direct-pr` 的边界与互斥

**你在确认什么**：该档位**只对无 PRD 锚点的 Issue 有效**，且与 `--fast-merge` 互斥。三条真实拒绝（均为 usage error，退出码 2，未静默降级）：

```text
iar run --issue 215 --direct-pr   → Issue #215 is PRD-backed (anchor: tasks/pending/P1-FEAT-…-any-issue-execution.md); --direct-pr is only defined for Issues without a PRD anchor…
                                     next: iar run --issue 215 --fast-merge
iar run --issue 218 --direct-pr --fast-merge → mutually exclusive; --direct-pr already includes everything --fast-merge skips…
iar run --all-ready --direct-pr   → defined for a single targeted Issue…
```

读不到 Issue 正文时同样拒绝（fail-closed），避免用「换个档位」绕开 PRD 交付门。证据：`open ".iar/evidence/rv-7-rejections.log"`、`rv-7-prd-anchored-rejection.log`；fail-closed 由 `test_direct_pr_gate_is_fail_closed` 钉住。

**回答方式**：`接受`，或指出你希望放宽/收紧的具体边界。

## 第 4 项 · 知悉 R3：首次领取补成真 CAS

**你在确认什么**：显式定向不再要求就绪标记后，**领取的原子性是唯一的防双跑机制**。原实现是裸的 `get_issue` → 算 labels → `edit_issue_labels`，无回读校验——两个并发领取者可双双写成功、双双认为自己是赢家。本次补真 CAS（认领评论 marker 作见证：写 → 回读全部竞价 → 最早者胜；落败方改写为 `iar:claim-withdrawn` 并退出）。反面风险：CAS 若写错，Issue 可能**永久卡住**（无人能领）。

**证据（两个真实进程抢同一真实 Issue，非单进程顺序模拟）**

- 完整保真度腿：两个真实 `iar run` 同秒启动抢 #218。赢家进入 Phase 1 并一路发布到 Draft PR #221；落败方在 01:27:35 打出 `Issue #218 claim arbitration lost, skipping: … claimed earlier by ZataZhangdeMacBook-Air.local (PID 2759); withdrawing this claim.` 后退出，**全程未创建工作树**。GitHub 线程同时留下赢家的 `iar:claim` 与输家的 `iar:claim-withdrawn`；#218 未被卡在 running，已正常流转到 `agent/review`。打开看：<https://github.com/ZataZhang/keda/issues/218>
- 零 token 可重复腿（直接调生产 `arbitrate_first_claim`，认领/回读/仲裁/标签转移全是生产代码）：抢探针 Issue #226 → 进程 A `rc=0 verdict=WIN`，进程 B `rc=3 verdict=LOSE`，恰好一个赢家。可重复执行：`bash .iar/evidence/scripts/rv-9-first-claim-cas.sh`
- 负控（判别性）：摘掉回读校验退回裸 write → 两个进程**都** `verdict=WIN`（生产路径下这就是双执行/双发布），且 `test_two_arbitrations_against_one_issue_leave_exactly_one_winner` FAILED。
- 永久卡死防护：回读失败与自身标记缺失都 fail-closed；探针 Issue 采集后已复位为可领取状态。

**回答方式**：`知悉`，或要求把首次领取的原子性单独拆成一个 PRD 先交付。

## 第 5 项 · 显式定向不受就绪标记约束 + daemon mutex 只挡队列轮询

**你在确认什么**：① `iar run --issue N` 不再要求 `agent/ready`（不打标记就是零竞争路径）；② 本机 daemon 在跑时，对**另一个** Issue 的显式 run 不再报 CONFLICT，而是与 daemon **共存**（各用各的工作树）；③ 同目标的排他因此**完全落在认领状态/CAS 上**，`--all-ready` 仍被 mutex 拦。守护进程侧自主挑选规则不变。

**证据**

| 场景 | 实测 |
|---|---|
| daemon 在跑，`iar run --all-ready --dry-run` | exit **5** + `A daemon for repository 'keda' is already running (PID 77464)`（PID 即当时那个 daemon） |
| daemon 在跑，`iar run --issue 219 --dry-run` | 被接受，exit 0，产出定向计划，无 already running 报错 → **共存成立** |
| 定向到被独立进程真实持有的 #222 | exit **5** + `actively claimed by … (PID 77601)`，报错点名持有者、语义来自认领状态而非 mutex |
| 停 daemon 后重跑 `--all-ready --dry-run` | exit 0（证明上一条的红确实来自活锁） |
| 无 workflow 标记的 Issue 显式定向 | 直接准入执行；队列发现侧仍不领它（INFO + 0） |
| blocked 无解除标记 / 不存在 / 已关闭 / 已发布过 | 5 并提示 `iar blocked-continue` / 3 / 3 / 路由回 review 不重跑；`--all-ready` 空队列仍静默 0 |
| 两进程同时领同一就绪 Issue | 只有一个赢（见第 4 项） |

负控：移除单实例锁判定后 `--all-ready --dry-run` 由 5 变 0（证明这段代码就是那条防护）；daemon 若越过就绪标记自主挑选则队列侧断言失败。证据：`open ".iar/evidence/rv-10-daemon-mutex-scope.txt"`、`rv-8-targeted-admission.txt`。

### 附加知悉项（本次验证中撞出来的真实隐患，不是假设）

采集这条证据时真实启动了一个 daemon，它把**本条交付 Issue #215 自己**判为陈旧尝试并抢占——尽管持有者 PID 仍存活、PRD activity lock 心跳仍是新的。后果链：重新入队 → 重新处理 → 撞 lock 冲突 → `Agent Runner Failed` / `agent/failed`。已复位标签、在 Issue 上留下 Operator Note，并给采集脚本加了 ABORT 预检。<https://github.com/ZataZhang/keda/issues/215>

根因：`reconcile_stale_attempts` 的 TTL 判定只看尝试**开始时间**，不看认领评论持有者 PID 是否存活，也不看 PRD activity lock 心跳。**而「长耗时的显式执行与同仓 daemon 并存」正是本项决策新疏通的路径**——所以这不是既有噪声，是你接受第 5 项时同时接受的一个已知缺口。建议后续让 reclaim 同时校验持有者 PID 存活与 lock 心跳（已写入 §12 风险与 Change Log）。

另需知悉：为控制成本，共存证据是 `--dry-run` 层面的（未与 daemon 同时真跑 agent）。

**回答方式**：`接受`（含知悉上述隐患），或 `先只做就绪准入、mutex 不动`，或要求把 TTL 校验纳入本次范围。

## 第 6 项 · §1 行为样例逐行确认

**你在确认什么**：PRD §1 那 21 行行为样例逐行符合预期。**改一个单元格就是在改验收标准**，所以请逐行看：`open "tasks/pending/P1-FEAT-20261006-122336-any-issue-execution.md"`（§1 Interpretation → 行为样例）。

实测落点（只列最容易记错的两组）：

- 就绪标记三行：未就绪 → 两侧守护进程都不领（rv-8 队列侧 INFO + 0）；已就绪 → 跑着的那台领走、认领标记 host/PID 与它一致（rv-9 赢家标记）；「换机器」是部署事实，不是 Issue 字段——**本次没有两台物理机，只以独立 host 身份 + 真实 PID 单机等效呈现，此限制已在 §12 披露**。
- `--direct-pr` 四行：只剩机械步骤（第 2 项对照）；故意让测试失败仍出 PR（**机制已证、端到端红 CI 样本未造**，见第 2 项披露）；PRD-backed 被拒（第 3 项真实报错）；两旗标互斥（第 3 项真实报错）。

**回答方式**：对每一行回 `符合`，或指出哪一行与预期不符。
