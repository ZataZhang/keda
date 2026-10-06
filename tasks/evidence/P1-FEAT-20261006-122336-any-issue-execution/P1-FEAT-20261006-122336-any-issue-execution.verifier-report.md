# 独立 verifier 复核报告 — P1-FEAT-20261006-122336（任意 Issue 可执行）

复核对象：worktree `.iar-worktrees/issue-215`（分支 `issue-215` @ HEAD `2542ecbc`，实现未提交）。
verifier 与 executor 非同一主体，两轮均为独立复核；第 1 轮判 FAIL，第 2 轮针对整改复核判 PASS。

## 第 1 轮：VERDICT = FAIL（1 个 blocker + 3 个 major + 5 个 minor）

### BLOCKER-1（已整改）：负控注入残留在交付代码里

- 事实：`src/backend/core/shared/models/publish_stage.py` 的 `skips_review_and_repo_verification` 停在注入形态 `self is not PublishStage.NORMAL`（即 `--fast-merge` 也会跳过 pre-PR review agent）。verifier 独立复跑 `tests/test_agent_runner_direct_pr.py` 得 **4 failed, 15 passed**，且该形态与 rv-7 自己的真实运行证据（#220 日志里确有 `Pre-PR review cycle`）直接矛盾。
- 根因：`.iar/evidence/scripts/rv-7-direct-vs-fast-stages.sh` 的负控还原用 `git checkout --`，而该文件本次是**新增未跟踪**文件，命令静默失败（错误行就在旧证据里）；随后的还原校验用 `git diff --name-only`，对未跟踪文件同样恒为空 → 于是打印了「还原确认：与基线一致」这句**假话**，`RESULT` 仍报 PASS。这是证据链自身的失效，不是被测代码的失效。
- 整改（executor 侧，逐项可核）：
  1. 属性还原为 `self is PublishStage.DIRECT`；`tests/test_agent_runner_direct_pr.py` → **19 passed**。
  2. 采集脚本改用 `/tmp` 基线副本 + `trap … EXIT` + `cmp -s` 逐字节校验 + **属性体内定位检查**（不能全文 grep：`return self is not PublishStage.NORMAL` 在 `skips_independent_verification` 里是合法实现）。
  3. 负控独立重采为 `rv-7-nesting-negative-control.txt`：前置发现生产语义行不在位即 `exit 2`（拒绝产出绿色结论）→ 注入态 `4 failed / 15 passed`（红项含 `test_fast_merge_still_runs_review_agent`）→ 还原校验 → 同一套件 `19 passed`。
  4. 时间线核对：`#218` 直发档真实运行 `01:27:18–01:30:40`、`#220` 快速档 `01:42:31–02:11:46`（审核段落 `02:04:18–02:04:47`），**均早于**注入时刻 `02:18:02` → rv-7 / rv-9 的真实运行证据本身有效，无需重采。
  5. 更正记录已就地追加：`rv-7-direct-vs-fast-stages.txt` 的 `[7]`、`rv-10-daemon-mutex-scope.txt` 的 `[9]`、`evidence.json` 的 rv-7 / rv-10 块。注意：重跑 rv-7 原脚本会截断证据文件，`[7]` 的更正内容以本报告与 `evidence-report.md` 为持久副本。

### MAJOR

- **M1 rv-10 的 PASS 超出了已证语句**：① 共存腿只有 `--dry-run` 证据，而 PRD rv-10 要求真实执行。披露是诚实的，标签需收窄 → 已在证据 `[9]` 与 `evidence.json` 明确「本条证明的是 mutex 作用域，不外推到真实并发执行效果」。
- **M2 rv-10 的旧头注与事故自相矛盾**：脚本原句「持有者 PID 存活 → reclaim 不会动它」被自己的 `[8]` 事故反证。verifier 复核代码确认隐患为真：`agent_runner_reclaim.py:189`（TTL 分支「即便 PID 仍存活也视为 stale」）、`agent_runner_reconcile.py:5`（判据「已死或超 TTL」），且两者都不读 PRD activity lock 心跳。脚本正文已改为如实表述；新增的 ABORT 预检是**规避**而非修好 → 该残余风险写入 PRD §12 并请人工知悉。
- **M3 rv-9 的残余竞态**：仲裁正确性依赖回读能看到对手评论（真实 GitHub 的 read-your-writes 不保证看到并发写者的评论），真双盲会出现两个赢家。已写在模块 docstring 与 manifest 的 `risks` 里，属**已披露**而非隐瞒。永久卡死路径未发现：所有 fail-closed 分支都在动标签**之前**抛错。

### MINOR

- rv-7 负控只在注入态跑单元测试，未再真跑一次快速档（已在 `[6]` 披露）。
- rv-4 负控是「剥离方向写反」的变体，不是 PRD 字面指定的注入（已在证据与 `evidence.json` 标注为变体）。
- PRD §9 点名的证据文件名与盘上不一致（`rv-1-any-issue-e2e.txt` vs `rv-1-no-prd-issue-e2e-run.txt`、`rv-9-claim-cas.txt` vs `rv-9-first-claim-cas.txt`、`rv-10-daemon-coexist.txt` vs `rv-10-daemon-mutex-scope.txt`、`rv-7-direct-pr.txt` vs `rv-7-direct-vs-fast-stages.txt`、`rv-11-skill-sync.txt` vs `rv-11-skill-docs-sync.txt`），且 §9.1 提到的 PNG 并不存在（本条以真实 GitHub URL 与文本日志呈递）→ 已按盘上实名修正 PRD，并披露无截图。
- rv-9 赢家日志与 rv-7 直发档日志是同一份文件（一次运行同时服务两条 oracle）→ 已在证据里写明。
- rv-8 的守护进程腿走的是共享发现路径，不是真实 daemon 轮询；真实 daemon 不领无标记 Issue 只由 rv-10 `[1]`（整轮未启动任何 agent）间接覆盖 → 已在证据里注明覆盖方式。

## 第 2 轮：VERDICT = PASS（针对整改逐条对抗性复核）

1. CONFIRMED — `publish_stage.py:28` 为 `self is PublishStage.DIRECT`；`:23` 保留 `is not PublishStage.NORMAL`（正是全文 grep 会误报的那条合法行）。
2. CONFIRMED — 当前树 `tests/test_agent_runner_direct_pr.py` → 19 passed。
3. CONFIRMED — 重采脚本用基线副本 + `cmp -s` + 属性体内定位检查 + EXIT trap；前置不通过即 `exit 2`。
4. CONFIRMED — 重采转录含 [1] 前置 PASS、[2] `4 failed, 15 passed`、[3] 还原 PASS、[4] `19 passed`。
5. CONFIRMED — 两份真实日志的时间戳均早于注入时刻（verifier 指出旧写法把日志末行时刻写成 02:04:46，实际为 02:11:46；不影响结论，已更正）。
6. CONFIRMED — 无其他残留：`git diff main -- agent_runner_reclaim.py` 为空；`agent_runner_claim_arbitration.py:236-283` 写标记→回读→仲裁→撤销落败方→切 running 的顺序完整；`runner.py:184-192` 无 `live_daemon_pid = None`，mutex 仍在 `elif target_issue is None:` 之下；`grep -rn "NEGATIVE CONTROL" src tests` 无命中。verifier 另发现一个 `tests/__pycache__/_rv5_negative_skill_test*.pyc` 残留（gitignored、pytest 不收集），已删除。
7. CONFIRMED — 注入生效窗口（02:18 之后）内采集的 rv-8 / rv-9 / rv-10 / rv-1 结果不受污染，决定性判据是 `grep -rln "publish_stage|PublishStage|skips_review|skips_independent" tests/` **只**命中 `test_agent_runner_direct_pr.py`；rv-2 / rv-4 / rv-11 均早于注入。无需重采、无需为此重跑全量（全量门禁仍照常跑，见下）。

结论：**rv-7 证据包在方法论修复后可接受**；第 1 轮的 blocker 已整改并有独立复核确认。残余风险为 M1（dry-run 层面的共存证据）、M2（TTL 与活持有者的冲突，未修，写入 §12）、M3（已披露的读回依赖），三者均已在证据、manifest 与人审清单里如实收窄，不作为已解决项呈现。

## 复核过程中确认的次要事实

- `hooks/max_file_lines.allowlist.txt` 相对 main 无改动，内容仍为纯注释（空名单）。
- `src/backend/core/use_cases/create_issue_from_prd.py` 非空行 999（main 为 998），距 1000 行上限仅 1 行，后续任何新增都会触发拆分。
- `.iar/` 与 `tasks/evidence/**`（除 `.md`）均在 `.gitignore` 内，原始日志与 HTML 呈递页不会进入代码 diff。
