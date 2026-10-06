# Realistic Validation 验证计划 — 任意 Issue 可执行（Issue-first 交付入口）

PRD: `tasks/pending/P1-FEAT-20261006-122336-any-issue-execution.md`
Issue: <https://github.com/ZataZhang/keda/issues/215>
分支: `issue-215` @ `2542ecbc`（实现未提交，由 runner 提交）
采集日期: 2026-10-06 ~ 2026-10-07

## 验证目标

证明「任意 Issue 可执行」这条能力在**真实入口**上成立，而不是只在单元测试里成立：一个手工建的、没有 PRD 锚点的 Issue 能端到端跑出 Draft PR（rv-1）；一句话能建出无锚点的可用 Issue（rv-2）；路线图不再承诺一道实现里并不存在的强制链（rv-3）；证据门禁的开关由旗标而非 agent 决定（rv-4）；既有 PRD 路径逐字不变（rv-5、rv-6）；`--direct-pr` 真的只剩机械步骤（rv-7）；显式定向不再受就绪标记约束且不可领取时报明确错误（rv-8）；首次领取是真 CAS（rv-9，本次唯一 R3）；daemon mutex 收窄后共存仍成立（rv-10）；随包 skill 与 docs 没有残留相反表述（rv-11）。

本 PRD 的实现侧几乎不含新的功能性分支（§2 末尾已声明），所以验证计划的重量全放在**「既有路径真的能跑」的证明**上：rv-1 是首要证据，此前该路径只有单元测试与代码阅读支撑。

## Oracle 分层（风险序 rv-9(R3) → rv-1 / rv-7 / rv-4 / rv-8(R2) → 其余 R1/R0）

| id | tier / reviewer | 真实入口 | mock 边界 | 判别力负控（实测为红） |
|---|---|---|---|---|
| rv-1 (R2, human) | 手工建的真实 Issue #216（无锚点、自由文本正文）→ 真实 `iar run --issue 216` → 真实 Draft PR #217 | 无 | 人为加 `- PRD path: tasks/pending/P1-FEAT-20261006-000000-nonexistent-probe-prd.md`（该文件不存在）锚点后，真实日志出现 `PRD delivery check failed … (1/5)(2/5)` 恢复循环，且 #219 **没有任何 PR**、工作树无新提交、无 commit-request |
| rv-2 (R1, verifier) | 真实 `iar issue create --from-prompt`，远端读回 #220/#222/#224 正文 + 主检出 `git status` 差分 | 仅内容生成失败注入走测试夹具 | 把「默认剥离验收小节」方向写反 → 锚点/验收段计数断言失败；干净正文放行、两种 PRD 指涉判为不合格（证明 0 命中有判别力） |
| rv-3 (R2, human) | 真实文件 `ROADMAP.md` 全文检索 | 无 | 同一判据扫 `git show main:ROADMAP.md`（旧文本）→ 废止表述命中 **5 次** |
| rv-4 (R2, verifier) | 默认态 #220 vs `--require-validation` 态 #222 的真实运行时门禁判定对照，含前端改动两态 | 无 | 默认态判定为 True 即失败；显式开启态无证据文件必须抛 `ValidationEvidenceError`；前端改动 + 非图片证据仍被拒（截图要求是真的） |
| rv-5 (R1, verifier) | 改动前（主检出旧 CLI）/ 改动后同一组 PRD 路径命令差分 + `iar schema --json` + `tests/test_iar_operator_skill.py` | 无 | 从 `_ALLOWED_FLAGS` 摘掉新旗标 → 守卫副本 `1 failed, 10 passed`（副本随后删除） |
| rv-6 (R1, verifier) | `git diff main -- …/agent_runner_reclaim.py` 静态断言 + 既有 `tests/test_agent_runner_reclaim.py` | 无 | 把归还路径改成不释放 label → `3 failed, 14 passed`，随后文件与基线逐字节一致 |
| rv-7 (R2, human) | 同一 Issue 两类档位真实对照：`iar run --issue 218 --direct-pr`（→ Draft PR #221，head `a0c5cd9af`）vs `iar run --issue 220 --fast-merge`（→ Draft PR #225） | 无 | 让 `--fast-merge` 也跳过 pre-PR review → 钉住档位嵌套关系的 3 条测试 FAILED（证明「两者可区分」不是恒真） |
| rv-8 (R2, verifier) | 真实 CLI：无 workflow 标记 Issue 的显式定向准入；队列发现侧一轮；blocked / 不存在 / 已关闭 / 被活跃认领 / 已发布 五种不可领取态的退出码 | 无 | 队列侧若越过就绪标记自主挑选则 ② 失败；把「静默返回 0」改掉则 5/3 断言失败 |
| rv-9 (R3, human) | **两个真实 OS 进程**抢同一真实 Issue：完整保真度样本 #218（两个 `iar run`，A 发布到 Draft PR #221、B 撤销退出）+ 零 token 可重复样本 #226（直接调生产 `arbitrate_first_claim`，A=WIN/rc0、B=LOSE/rc3） | harness 只把「选 agent 跑」这段换成直调，认领/回读/仲裁/标签转移全部是生产代码；完整保真度腿无 mock | 摘掉回读校验退回裸 write → 两个进程都 `verdict=WIN`，且 `test_two_arbitrations_against_one_issue_leave_exactly_one_winner` FAILED |
| rv-10 (R1, verifier) | 真实 `iar daemon run` + 真实 `~/.iar/daemon-locks/keda.lock` + 真实锁文件观察，三种 `iar run` 并发对照 | 独立持有者用 `scripts/rv-10-claim-holder.py` 真实认领并 sleep 保持 PID 存活（不跑 agent，省 token） | 移除单实例锁判定 → `--all-ready --dry-run` 由 exit 5 变 exit 0（队列轮询不再被拦） |
| rv-11 (R1, verifier) | 真实文件 `SKILL.md` / `docs/guides/agent-runner.md` 全文检索 + 守卫测试 + `iar schema --json` + `--help` 交叉核对 | 无 | 同一判据扫 `git show main:` 旧版 SKILL.md → 旧绝对表述被报出 |

`reviewer: human` 的六项（rv-1 / rv-3 / rv-7 / rv-9，以及 §9.1 呈递的两条 URL 型对照）都有可打开的呈递物，见 `human-review-checklist.md`；`reviewer: verifier` 的项只在失败时上浮。

`R2`/`R3` 的关键值来源与禁止旁路：rv-1 的 critical value 是 Issue 正文的锚点存在性（`gh issue view --json body` 实时读取，非缓存），forbidden bypasses（fixture Issue、直调 use_case、用带锚点 Issue 冒充）全部未触碰；rv-7 的阶段序列取自真实运行日志的行级证据（含被跳过门禁的审计行与被执行门禁的实跑行）；rv-9 的 must_cross 是真实 GitHub 标签 + 真实认领评论 + 两个真实进程。

## 单元 / 契约层（仓库门禁）

- 新增：`tests/test_agent_runner_claim_arbitration.py`（CAS 正反两向 + 永久卡死防护）、`tests/test_agent_runner_direct_pr.py`（档位嵌套与跳过范围）、`tests/test_issue_create_from_prompt.py`（一句话建 Issue、锚点剥离、矛盾旗标）、`tests/test_run_target_admission.py`（显式定向准入与五种不可领取态）。
- 追加：`tests/test_agent_runner_run_targeting.py` 两条 `--dry-run` 回归（本轮发现的定向 dry-run 崩溃，见 Change Log）。
- 守卫：`tests/test_iar_operator_skill.py` 登记三个新旗标，并断言 SKILL.md **不残留**旧的绝对表述。
- 门禁命令：`just lint`、`CI=true just test all`、`uv run mkdocs build --strict`。

## 证据绑定最终代码树

- 复现脚本在 `.iar/evidence/scripts/rv-*.{sh,py}`；每条目一份 `.iar/evidence/rv-*.txt` 与真实运行 `.log`；结构化 manifest 为 `.iar/evidence/evidence.json`（`version: 1`、`language: "zh-CN"`、11 个 item 块，每块含 `item_number/item_name/command/evidence_files/output_summary/explanation/risks/negative_control/expected_fail`）。原始证据（`.txt`/`.log`）不进代码 diff，本目录只提交 `.md` 报告；打开命令见各报告。
- 冻结凭证（合并前这些路径若变更须重采）：
  `git diff HEAD -- src tests ROADMAP.md docs config.toml | shasum -a 256 = ec409c84a7098a8102a3e84d263494cca0c5f57c100b9cb6ab13091ad8df3b2b`
  新增未跟踪文件：`git ls-files --others --exclude-standard -- src tests | xargs shasum -a 256 | shasum -a 256 = 927e8e3fccc1f7b4858b4ddcca6f2a159326fc4d0481b50018d7c6c9adedd529`
- 负控期间的临时源码注入（rv-6 归还路径、rv-7 档位嵌套、rv-9 回读校验、rv-10 单实例锁、rv-5 守卫副本）全部在采集脚本内还原并逐字节校验（`cmp -s` / numstat 前后一致 / 标记行计数归零），最终树不含任何验证期改动；rv-9/rv-10 的还原同时写在 `EXIT` trap 里，防止中途中断留下注入。

## 成本与保真度的取舍（如实披露）

- rv-9 的完整保真度腿只有**一个**真实双进程样本（#218，两个真实 agent，成本高）；可重复、零 token 的那条腿把「选 agent 跑」替换成直调生产 `arbitrate_first_claim`，认领/回读/仲裁/标签转移仍是生产代码与真实 GitHub 状态。两条腿的负控各自独立跑红。
- rv-10 的共存证据是 `--dry-run` 层面的（不真起 agent），因为同仓真实执行会与 daemon 抢 TTL；这一点与由此暴露的 TTL 隐患一并写入 Change Log 与证据文件 `[8]`。
- 跨机语义只在单机上以「独立 host 身份 + 真实 PID」等效呈现（PRD §12 已声明该限制）：未就绪不被任何 daemon 领取这条为真实观测，「另一台物理机器领走」未做双机验证。
