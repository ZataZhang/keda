# 证据报告 — 任意 Issue 可执行（Issue-first 交付入口，P1-FEAT-20261006-122336）

- PRD：`tasks/pending/P1-FEAT-20261006-122336-any-issue-execution.md`
- Issue：<https://github.com/ZataZhang/keda/issues/215>
- 代码树：worktree `.iar-worktrees/issue-215`，分支 `issue-215` @ HEAD `2542ecbc`（实现未提交，由 runner 提交）
- 冻结凭证：`git diff HEAD -- src tests ROADMAP.md docs config.toml | shasum -a 256` = `ec409c84a7098a8102a3e84d263494cca0c5f57c100b9cb6ab13091ad8df3b2b`；新增未跟踪文件摘要 = `927e8e3fccc1f7b4858b4ddcca6f2a159326fc4d0481b50018d7c6c9adedd529`
- 原始证据位置：`.iar/evidence/`（不进代码 diff）。打开：`open ".iar/evidence"`；结构化 manifest：`open ".iar/evidence/evidence.json"`
- 结论：**rv-1..rv-11 全部 PASS**，每条都带**实测为红**的判别性负控。两条必须人工知悉的风险（rv-10 的 TTL 回收隐患、定向 `--dry-run` 崩溃已修）见文末与 Change Log。

> 术语：*负控* = 把被验证的东西还原成错误状态再跑同一批断言，确认它会红；绿才有意义。*真实入口* = 真实 CLI 进程 + 真实 GitHub Issue/PR + 真实文件，不是 fixture / TestClient / 直调 use_case。

---

## 复现

每条目一份可重复执行的采集脚本（gitignored，不进 diff）：

```bash
cd /Users/zata/code/keda/.iar-worktrees/issue-215
bash .iar/evidence/scripts/rv-1-no-prd-e2e.sh        # 真实端到端跑一个无锚点 Issue（耗 agent，成本高）
bash .iar/evidence/scripts/rv-1-negative-control.sh  # 同一类 Issue 人为加锚点 → 必须失败
bash .iar/evidence/scripts/rv-3-roadmap-consistency.sh
bash .iar/evidence/scripts/rv-4-validation-toggle.sh
bash .iar/evidence/scripts/rv-5-backward-compat.sh
bash .iar/evidence/scripts/rv-6-reclaim-intact.sh
bash .iar/evidence/scripts/rv-7-direct-vs-fast-stages.sh
bash .iar/evidence/scripts/rv-8-targeted-admission.sh
bash .iar/evidence/scripts/rv-9-first-claim-cas.sh   # 含零 token 的双进程 CAS 腿（可重复）
bash .iar/evidence/scripts/rv-10-daemon-mutex-scope.sh  # 会真实启动 daemon；脚本开头有 ABORT 预检
bash .iar/evidence/scripts/rv-11-skill-docs-sync.sh
```

零成本档：`rv-3 / rv-5 / rv-6 / rv-11` 只读文件与跑测试；`rv-8 / rv-10` 的探针用 `--dry-run`；`rv-9` 的可重复腿直调生产 `arbitrate_first_claim`，不起 agent。

---

## rv-1 · 手工建的无 PRD 锚点 Issue 端到端产出 Draft PR（首要证据，R2/human）

```bash
iar run --issue 216 --repo-id keda
```

真实目标：<https://github.com/ZataZhang/keda/issues/216>（人手写的「Stats 页 Token 用量表的数值列改成右对齐」，正文无 `PRD path:` 锚点）→ <https://github.com/ZataZhang/keda/pull/217>（`isDraft=true`、`OPEN`、`base=main`、`head=issue-216`，正文不含 `iar:fast-merge`/`iar:direct-pr` 旁路标注）。

真实阶段序列（`rv-1-run-216.log`，摘录带时间戳与源码位置）：

```text
23:34:54 run_agent_once.py:679  Starting agent for Issue #216
23:44:44 run_agent_once.py:785  Agent finished for Issue #216 (exit_code=0)
23:49:39 agent_runner_commit.py:356  Running configured pre-commit verification command for Issue #216
23:49:52 agent_review.py:608  Starting pre-PR review for Issue #216 with reviewer 'kimi' (max_attempts=2, timeout=1800s, head=21f2010b…)
23:58:14 agent_review.py:738  Pre-PR review cycle 1/2 … parsed verdict=approved (findings=0)
23:58:16 agent_review.py:996  Pre-PR review approved Issue #216 after 1 cycle(s).
```

负向对照（证明绿色来自「无锚点」这条路径本身，而非门禁整体被关掉）：对同类 Issue #219 人为写入 `- PRD path: tasks/pending/P1-FEAT-20261006-000000-nonexistent-probe-prd.md`（文件不存在）后重跑，真实日志出现 PRD 交付门与恢复循环，且**不产出任何交付物**：

```text
01:47:50 … PRD delivery check failed for Issue #219 … Waiting 30 seconds before recovery attempt 1/5
01:55:24 … recovery attempt 2/5            ← 计数与时间戳序列不可伪造（源码行 run_agent_execution_loop.py:449）
断言：#219 没有任何 PR（含 closed）→ PASS；工作树未产生新提交 → PASS；无 commit-request → PASS
对照：同一时刻无锚点的 #216 日志不含任何 delivery check 行 → PASS
```

证据：`rv-1-no-prd-issue-e2e-run.txt`、`rv-1-run-216.log`、`rv-1-negative-control.txt`、`rv-1-negative-run-219.log`、`rv-1-negative-body-{original,anchored}.md`。
披露：锚点存在但 PRD 缺失时的**门禁耗尽**行为（抛 `MaxRetriesExceededError`、非零退出）由测试层钉住（`tests/test_agent_runner_prd_delivery.py::test_ensure_prd_delivery_ready_raises_when_prd_missing`、`tests/test_agent_runner_recovery.py::test_recovery_loop_exhausted_raises_max_retries`），本次真实运行只观测到 **2/5**（受成本上界主动截断），未跑到耗尽。

## rv-2 · 一句话建 Issue：无 PRD 指针 / 工作区零 diff / 生成失败模板回退（R1/verifier）

```bash
iar issue create --from-prompt "<需求>" --repo-id keda      # 真实建出 #220 / #222 / #224
```

断言与实测：三条真实 Issue 正文的 `PRD path:` 与 `Canonical PRD` 命中数均为 **0**；三次创建前后主检出 `git status` 逐字相同（创建链路零写入仓库）；内容生成器不可用时以确定性模板仍建出可用 Issue（#224 正文逐字保留原提示词，记 WARNING 而非失败）；`--from-prompt` 下显式 `--publish-prd` / `--force` 报 usage error。负控：干净正文放行、两种 PRD 指涉判为不合格（使「0 命中」具备判别力）；把默认剥离验收小节的方向写反则默认态断言失败。相关单测 29 passed。证据：`rv-2-from-prompt.txt`。

## rv-3 · 路线图全文与实现对齐（R2/human）

零成本负控即自证：同一判据扫工作区 `ROADMAP.md` → 废止表述命中 **0**；扫 `git show main:ROADMAP.md`（改动前）→ 命中 **5**。替代契约齐备（Product Boundary 允许无 PRD 执行但仍需就绪标记；Target Workflow / M1 / M8 / Acceptance Checklist / Not Completed / Open Questions 各节一致；M8 为按需能力而非强制前置）。证据：`rv-3-roadmap-consistency.txt`、`rv-3-roadmap-diff.txt`。

## rv-4 · 证据门禁由旗标决定，不由 agent 决定（R2/verifier）

默认态 #220：`validation_required(issue_body)=False`，前端改动也不要求任何证据文件（`ensure_frontend_visual_evidence` 直接返回）。显式 `--require-validation` 态 #222：判定 True 且无证据时实际抛 `ValidationEvidenceError`；前端改动 + **非图片**证据仍被拒（截图要求是真的），补上图片证据后视觉门禁解除。关闭态由 `strip_validation_section` 代码路径保证（剥离小节后台门确定性关闭）。相关单测 154 passed（exit=0）。证据：`rv-4-validation-toggle.txt`。披露：门禁键在英文 `## Realistic Validation` 小节，中文「验收标准」不开门禁——该不对称已写入 Change Log 供人工知悉。

## rv-5 · 既有 PRD 路径逐字不变 + 新旗标为纯增量（R1/verifier）

以主检出旧 CLI 为基线做同一组 PRD 路径命令差分：`--fast-merge` 表面（旗标、组合限制、门禁行为）逐项相同；两者都不给时退出码仍为 **2**；`generated_content.py` 与 `agent_runner_reclaim.py` 相对 main 无 diff。`iar schema --json` 列出 `from-prompt` / `require-validation` / `direct-pr` / `fast-merge`。守卫测试 11 项绿。负控：从 `_ALLOWED_FLAGS` 摘掉新旗标后，复制出的守卫副本 `1 failed, 10 passed`（exit=1），副本随后删除。证据：`rv-5-backward-compat.txt`。披露：差分基线是改动前的主检出而非同一进程的旧二进制，文案变化未被允许（实测逐字相同）。

## rv-6 · 跨机归还协议零改动（R1/verifier）

`git diff main -- src/backend/core/use_cases/agent_runner_reclaim.py` 为空（本次只在别处补首次领取 CAS，未重写归还路径）；既有 `tests/test_agent_runner_reclaim.py` 全绿；存活/死亡 PID 判定与真实进程一致（归还触发条件成立）。负控：把归还路径改成不释放 label → `3 failed, 14 passed`（exit=1），随后文件与基线逐字节一致（numstat 前后相同）。证据：`rv-6-reclaim-intact.txt`。

## rv-7 · `--direct-pr` 只剩机械步骤，且被限制在无锚点 Issue（R2/human）

同一 Issue 两档位真实对照（`rv-7-run-218-direct.log` / `rv-7-run-220-fastmerge.log`）：直发档 #218 → Draft PR <https://github.com/ZataZhang/keda/pull/221>（head `a0c5cd9af…`，PR 正文带 `iar:direct-pr` 机器可读标注），快速档 #220 → Draft PR <https://github.com/ZataZhang/keda/pull/225>。

| 门禁 | `--direct-pr` | `--fast-merge` |
|---|---|---|
| runner 验证命令 | 显式跳过（审计行） | 真实执行（失败并回收续作） |
| 仓库 pre-commit 验证 | 显式跳过 | 真实执行 |
| pre-PR review agent（第二个 agent） | 显式跳过 | 真实执行（日志 5 处） |
| post-PR supervisor | 显式跳过 | 真实执行（日志 2 处） |
| Phase 4.5 rv_reexec + 独立 verifier | 跳过 | 跳过（两档共同点） |
| 证据门禁 | 显式跳过 | 该 Issue 无验收小节，本就不要求 |

直发档侧**不存在第二个 agent 会话**（无 `Starting agent` 之外的 agent 启动行）。三个被拒场景真实给出 usage error：PRD-backed Issue 用直发档（#215 与 #219 各一次，均点名锚点）；`--direct-pr --fast-merge` 同时给出（互斥）；`--all-ready --direct-pr`（只对单目标有效）。负控：让 `--fast-merge` 也跳过 pre-PR review → `tests/test_agent_runner_direct_pr.py` 三条钉嵌套关系的测试 FAILED（exit=1），证明「两者可区分」不是恒真。**第 1 轮 verifier 判 FAIL 的残留注入正是这条负控的还原失败所致**，还原方法论已修复并重采，见文末「独立 verifier 复核」与 `rv-7-nesting-negative-control.txt`。披露：fast 侧的证据门禁对照不是两态证明（该探针 Issue 本身无验收小节），证据门的两态由 rv-4 承担。

## rv-8 · 显式定向不受就绪标记约束；不可领取态给明确错误（R2/verifier）

无 workflow 标记的真实 Issue 被 `iar run --issue N` 直接准入执行，而**队列发现侧仍不领它**（仅 INFO、退出 0）；`blocked` 且无解除标记 → 拒绝并提示 `iar blocked-continue`（exit 5）；不存在 / 已关闭 → exit 3（两种 3 分别为「不存在」「已关闭」）；被活跃认领 → exit 5 且点名持有者 host/PID；已发布过的 #220 被 durable 状态路由回 review 通道，不当新任务重跑；`--all-ready` 空队列静默返回 0。判定层参数化测试 46 passed。负控：守护进程若越过就绪标记自主挑选，② 立即失败。证据：`rv-8-targeted-admission.txt`。

## rv-9 · 首次领取是真 CAS（本次唯一 R3，human）

**完整保真度腿**：两个真实 `iar run` 进程同秒（01:27:18）抢 Issue #218。

```text
A：进入 Phase 1（Starting agent for Issue #218）→ 一路发布到 Draft PR #221
B：01:27:35 agent_runner_orchestration_runtime.py:501
   Issue #218 claim arbitration lost, skipping: Issue #218 was claimed earlier
   by ZataZhangdeMacBook-Air.local (PID 2759); withdrawing this claim.
   ← B 全程未创建工作树
GitHub 线程同时留下赢家的 iar:claim 与输家的 iar:claim-withdrawn
#218 未被卡在 running，已流转到 agent/review；赢家 PR 完好（head=issue-218）
```

**零 token 可重复腿**：两个真实 OS 进程各自调用生产 `arbitrate_first_claim`（`scripts/rv-9-cas-claim.py`）抢 Issue #226：

```text
进程 A：rc=0 verdict=WIN  host=… pid=72054 started_at=2026-10-06T18:30:41.733499+00:00
进程 B：rc=3 verdict=LOSE detail=Issue #226 was claimed earlier by … (PID 72054); withdrawing this claim.
恰好一个赢家；两进程退出码不同（0 / 3）
```

负控（两级）：摘掉回读校验退回裸 write → 两个进程**都** `verdict=WIN`（生产路径下即双执行/双发布），且 `tests/test_agent_runner_claim_arbitration.py::test_two_arbitrations_against_one_issue_leave_exactly_one_winner` FAILED。永久卡死防护：回读失败与自身标记缺失都 fail-closed；探针 Issue #226 采集后复位为可领取状态，未被 CAS 写坏。mock 边界如实声明：可重复腿只把「选 agent 跑」换成直调，认领/回读/仲裁/标签转移全部是生产代码与真实 GitHub 状态。证据：`rv-9-first-claim-cas.txt`、`rv-9-run-{a,b}.log`、`rv-9-harness-{positive,negative}.log`。

## rv-10 · daemon mutex 只挡队列轮询（R1/verifier）

真实 `iar daemon run --repo-id keda --no-autopilot --interval 3600 --max-issues 1` + 真实 `~/.iar/daemon-locks/keda.lock`：

| 场景 | 结果 |
|---|---|
| ③ `iar run --all-ready --dry-run`（daemon 在跑） | exit **5** + `A daemon for repository 'keda' is already running (PID 77464)`，PID 即当前 daemon |
| ① `iar run --issue 219 --dry-run`（同一时刻，daemon 存活） | 被接受，exit 0，产出定向 dry-run 计划，**无 already running 报错** → 共存 |
| ② `iar run --issue 222 --dry-run`（#222 被独立进程真实认领，持有者 PID 77601 存活） | exit **5** + `actively claimed by … (PID 77601)`，报错语义来自认领状态而非 mutex |
| ⑤ 停 daemon 后重跑 `--all-ready --dry-run` | exit 0（锁消失后同一命令被接受；空队列静默） |

负控：移除单实例锁判定后 `--all-ready --dry-run` 由 exit 5 变 exit 0，证明该段代码确实提供「重复领取 ready 队列」的防护。证据：`rv-10-daemon-mutex-scope.txt`、`scripts/rv-10-claim-holder.py`。

**采集事故（如实披露，见证据文件 `[8]`）**：本次真实启动 daemon 时，daemon 的 TTL 回收把**本条交付 Issue #215** 判为陈旧尝试（持有者 PID 55501 **仍存活**）→ 重新入队 → 重新处理 → 撞上 PRD activity lock 冲突 → Issue 被打上 `Agent Runner Failed` / `agent/failed`。已复位标签（`agent/running source/prd type/feature status/backlog priority/P1`）、在 Issue 上留下 Operator Note 说明隐患，并在采集脚本开头加了 ABORT 预检（检测到交付 Issue 正被持有时直接 exit 2，不启动 daemon）。**该隐患是真实产品风险，不是脚本 bug**：reconcile 只按尝试开始时间判 TTL，不看认领持有者 PID 是否存活、也不看 PRD activity lock 心跳；「长耗时的显式执行与同仓 daemon 并存」正是本次决策三新疏通的路径，因此它必须写进 §12 并由人工知悉。

## rv-11 · 随包 skill 与 docs 随行为反转同步（R1/verifier）

`SKILL.md` 不再出现「`iar run` never runs while a daemon serves the same repository」类绝对表述（改为只对队列轮询成立、显式单目标共存）；`agent/ready` 行说明它只约束守护进程；exit code 5 说明补入「显式 target 不可领取」；三个新旗标同时出现在命令表、`_ALLOWED_FLAGS`、`iar schema --json` 与 `--help`；`docs/guides/agent-runner.md` 同步共存语义与直发档小节。守卫测试绿（`test_packaged_skill_documents_direct_pr_and_from_prompt_entries`、`test_packaged_skill_drops_retired_absolute_claims`）。负控：同一判据扫 `git show main:` 旧版会报出旧绝对表述。证据：`rv-11-skill-docs-sync.txt`。

---

## 机器可读 manifest

`.iar/evidence/evidence.json`：`version: 1`、`language: "zh-CN"`、11 个 item 块，每块含 `item_number / item_name / command / evidence_files / output_summary / explanation / risks / negative_control / expected_fail`。校验：

```bash
./.venv/bin/python - <<'PY'
import json, pathlib
root = pathlib.Path(".iar/evidence"); m = json.loads((root/"evidence.json").read_text("utf-8"))
assert m["version"] == 1 and m["language"] == "zh-CN"
assert [b["item_number"] for b in m["items"]] == list(range(1, 12))
missing = [f for b in m["items"] for f in b["evidence_files"] if not (root/f).exists()]
assert not missing, missing
print("manifest OK:", len(m["items"]), "items，所有引用的证据文件在盘")
PY
```

## 已知风险与残余（须人工知悉）

1. **TTL 回收与活持有者冲突（rv-10 实测发生在自己的交付 Issue 上）**——建议后续让 reclaim 同时校验认领评论持有者 PID 存活与 PRD activity lock 心跳。
2. **定向 `--dry-run` 曾崩溃**（本轮发现并修复，附两条回归测试，见 Change Log）。
3. **rv-9 完整保真度腿只有一个真实样本**（成本高），可重复性由零 token 腿承担。
4. **跨机语义为单机等效**（独立 host 身份 + 真实 PID），未做两台物理机验证。
5. **证据门禁的中文标题不对称**（rv-4 披露）。
6. `--direct-pr` 档位的质量把关整体转移到 PR 上的 CI——这是 §2 决策二请人拍板的取舍，不是证据缺口。

## 独立 verifier 复核（两轮）

- **第 1 轮：FAIL**，一个 blocker——rv-7 的负控注入残留在交付树里（`publish_stage.py` 的 `skips_review_and_repo_verification` 停在「FAST 也跳审核」的注入形态）。根因是采集脚本用 `git checkout --` 还原一个**新增未跟踪**文件，命令静默失败，而还原校验用的 `git diff` 对未跟踪文件恒为空，于是旧证据里那句「还原确认：与基线一致」是假话。整改：还原为 `self is PublishStage.DIRECT`（套件 19 passed）、脚本改用基线副本 + `cmp -s` + 属性体内定位检查 + EXIT trap、负控独立重采为 `rv-7-nesting-negative-control.txt`。时间线核对证明 rv-7 / rv-9 的两份**真实运行日志都早于注入时刻**，真实证据本身有效；更正就地追加在 `rv-7-direct-vs-fast-stages.txt [7]`、`rv-10-daemon-mutex-scope.txt [9]` 与 manifest 的对应块。⚠️ 重跑 `rv-7-direct-vs-fast-stages.sh` 会截断该证据文件，`[7]` 的持久副本就是本节。
- 同轮另三条 MAJOR 已如实收窄而非辩解：rv-10 的共存腿只有 `--dry-run` 证据（PASS 不外推到真实并发执行）；rv-10 脚本旧头注「持有者 PID 存活 → reclaim 不会动它」被自己的事故反证，隐患经代码确认属实（`agent_runner_reclaim.py:189`、`agent_runner_reconcile.py:5`，两者都不读 activity lock 心跳）；rv-9 的仲裁依赖回读能看见并发评论，属已披露残余。
- **第 2 轮：PASS**。逐条对抗性复核整改，并给出决定性判据：`grep -rln "publish_stage|PublishStage|skips_review|skips_independent" tests/` 只命中 `test_agent_runner_direct_pr.py`，因此注入生效窗口内采集的 rv-1 / rv-8 / rv-9 / rv-10 结果未被污染；全树再无负控残留。完整两轮记录见 `P1-FEAT-20261006-122336-any-issue-execution.verifier-report.md`。
