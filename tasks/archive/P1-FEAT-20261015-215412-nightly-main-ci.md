
# Main CI 夜间巡检 — 2026-10-15

- GitHub Issue: https://github.com/ZataZhang/keda/issues/288

> ✅ **验收状态**：已验收 — 2026-10-28 按合并态追认（交付 PR #289 于 2026-10-15 合并）。本次未逐项人工复核；遗留与未闭环项见文末 Change Log。

这是 `keda` 仓库的每日 main CI 巡检任务。最近一次 main workflow 的结论是：
`failure`。

## 目标

确认 main 的最新 GitHub Actions workflow 是否失败；如果失败，定位根因并提交最小
修复 PR。如果没有可操作的问题，不要为了产生改动而制造 PR。

## 执行规则

1. 先运行 `gh run list --branch main --limit 5`，再用
   `gh run view <run-id> --log-failed` 查看失败 job 的日志。
2. `failure`、`timed_out`、`cancelled` 或 `action_required` 时，复现真实失败入口，
   修复源代码 / 测试 / 配置，并运行与改动匹配的验证；本仓库日常验证优先使用
   `just test`，必要时补跑对应 lint 或文档构建。
3. 目前已知的硬门禁问题是单个 Python 文件非空行超过 1000 行；如果仍然命中，
   优先拆分触发门禁的文件，不要把文件加入 allowlist 绕过检查。
4. 最近一次 main run 为 `success` 或 `none` 时，调度器已通过 `skip_when` 直接跳过，不会
   生成本任务；能看到本 Issue 说明需要排查（含 `unavailable`：无法读取 CI 状态）。不要主动
   扩大范围做重构或依赖升级。

## 完成条件

- 有失败：修复、验证并创建 draft PR，PR 描述中引用本 Issue 和失败 run。
- 若复查发现最新 run 其实已经恢复：在 Issue 留下检查结果并关闭 Issue，不创建 PR。
- 如果外部 GitHub 权限或 workflow 状态无法确认，保留 Issue 为 blocked，说明需要的
  人工动作，不要猜测结论。

## Acceptance Checklist

执行侧条目逐条绑定已采集证据（目录 `tasks/evidence/P1-FEAT-20261015-215412-nightly-main-ci/`）；
`[~]` 为 runner-owned 后置门禁（独立 verifier、PR CI、main CI 复跑），本检查先于它们，
不在此处勾选。

- [x] rv-1：CI 逐字相同的 `check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend`（空 allowlist）在最终代码树 exit 0、无 `[ERROR]`。 — 证据 `rv-1-max-file-lines.txt`（修复前同命令即 CI 报错原文）。
- [x] rv-2：受影响面回归（agent_runner / backlog / verifier / supervisor 测试，`--no-testmon`）全绿。 — 证据 `rv-2-targeted-tests.txt`（1988 passed, 0 failed）。
- [x] rv-3：公共名再导出 + orchestrate 三模块 monkeypatch 依赖注入接缝冒烟通过（facade 派发、`refresh_runtime_dependencies` 后 rework 模块协作者就位）。 — 证据 `rv-3-api-seam-smoke.txt`。
- [x] rv-4：四层架构方向检查 `check_architecture.py` 无违规。 — 证据 `rv-4-arch-check.txt`。
- [x] rv-5：`mkdocs build --strict` 通过（`time-display.md` 指向更新后）。 — 证据 `rv-5-mkdocs-strict.txt`。
- [x] rv-6：`just lint --repo` 全量本地门禁（ruff / docstring / 复用 + 全量 pytest）：静态与复用检查通过，套件仅剩 rv-7 一条存量失败。 — 证据 `rv-6-just-lint-repo.txt`。
- [x] rv-7：随包 operator skill 主文件恢复 ≤70 非空行；读取该文件的 4 个测试模块全绿。 — 证据 `rv-7-skill-budget.txt`（skill 27 + 其余 92 passed）。
- [~] 本改动关联 PR 的 CI（`Validate Template` job：max-file-lines + 全量 pytest）转 green。 — runner-owned gate: PR CI。
- [~] 独立 verifier 通过并归档。 — runner-owned gate: independent verifier + archive。
- [~] main 分支 CI workflow 复跑转 green（真实失败入口）。 — runner-owned gate: main CI scheduled/rerun。

### Human-Confirmed
> 2026-10-28 按合并态追认：以下各项由用户一次性确认，未逐项人工复核；遗留与未闭环项见文末 Change Log。

- [x] 人工确认：本 PR 合并后 main 分支下一次 CI（或复跑）为 green、夜间巡检闭环成立——即确认「按 PRD 规则 3 拆分两个超限文件 + 修复被门禁掩盖的存量 skill 预算守卫」这一最小修复口径可接受，无需回退或改走 allowlist。

## Change Log

### 2026-10-15 执行记录：根因定位为 1000 非空行硬门禁，按 PRD 规则 3 拆分文件
- Type: execution-record
- Before: main CI 最新 run `38745391997`（CI workflow / Validate Template /
  Check max file lines）为 `failure`：`agent_runner_failure.py` 1002 非空行、
  `agent_runner_orchestration_runtime.py` 1001 非空行；本 PRD 无执行记录。
- After: 两个超限文件按既有拆分惯例（见 `agent_runner_stale_attempt.py` 同源先例、
  `hooks/max_file_lines.allowlist.txt` 空名单约定）拆出兄弟模块：
  `agent_runner_failure.py`（分类与异常，294 非空行）+
  `agent_runner_failure_format.py`（评论/attempt history 渲染，757 非空行，公共名
  由原入口再导出）；`agent_runner_orchestration_runtime.py`（928 非空行）+
  `agent_runner_orchestration_prd_rework.py`（PRD rework 阶段，86 非空行，
  monkeypatch 依赖注入接缝由 orchestrate 扩展为三模块同步）。未修改任何行为逻辑，
  未把文件加入 allowlist。
- Reason: 消除 main CI 硬门禁失败，且 allowlist 约定禁止新文件豁免、超限即拆分。
- Impact: 用户可见行为不变；CI `Check max file lines` 在最终代码树通过，本地证据见
  `tasks/evidence/P1-FEAT-20261015-215412-nightly-main-ci/`（rv-1 门禁复现、
  rv-2 定向回归、rv-3 公共 API/接缝冒烟、rv-4 架构方向、rv-5 mkdocs --strict）。
- Review: 执行侧交付完成，待本改动关联 PR 的 CI 复跑确认（真实入口为 main
  workflow，runner-owned），人工验收待 PR 合并后记录。

### 2026-10-15 执行记录：修复门禁转绿后暴露的存量 skill 常驻预算守卫失败
- Type: execution-record
- Before: 拆分修复让 `Check max file lines` 通过后，CI `Validate Template` job 会继续
  执行同 job 内的全量 pytest 步（`ci.yml:157`，此前一直被门禁先失败所跳过）。本地以
  `just lint --repo` 复现，全量套件报 `test_packaged_skill_main_file_keeps_shared_baselines`
  失败：随包 operator skill 主文件非空行 71 > 常驻预算 70。该失败与本次拆分无关，是
  `8f8642d8` 设定 70 行预算后被 `e74eceb0`（issue-scoped auto-merge）新增条目顶到的存量超额。
- After: 仅改源文件、不动守卫预算——把 SKILL.md 的 Routes 段末尾两个独立散文段
  （命令权威引用 / legacy `iar` 别名）合并为一行，非空行 71→70；不删任何被守卫断言的
  token。读取该文件的 4 个测试模块全绿（skill 27 + 其余 92）。
- Reason: PRD 目标是让 main CI 该 job 真正转绿；门禁修复会把此前被掩盖的这条 pytest
  失败暴露到同一 job，不修则交付 PR 的 CI 仍红。按 AGENTS.md 守卫规则修触发它的源文件，
  不放宽 70 上限。
- Impact: 用户可见行为不变（仅 skill 主文件排版合并，内容零删减）；CI `Validate Template`
  的 pytest 步不再因该守卫失败。证据 `rv-6-just-lint-repo.txt`、`rv-7-skill-budget.txt`。
- Review: 执行侧交付完成，随本 PRD 一并待 runner PR CI 复跑 + 人工验收。

### 2026-10-15 执行记录：补全 PRD 的 Acceptance Checklist 与验收状态横幅
- Type: structure-fix
- Before: 本巡检 PRD 为简式结构（目标 / 执行规则 / 完成条件 / Change Log），无
  `## Acceptance Checklist` 章节、无验收状态横幅，runner 交付检查因
  `Acceptance Checklist section missing` 进入 recovery（attempt 1/5）。
- After: 在标题下补 `🧍 待人工验收` 横幅（作为清单投影），并把已有 rv-1–rv-7 证据
  整理成 Acceptance Checklist：执行侧 7 条按已采集证据勾 `[x]`，PR CI / 独立 verifier /
  main CI 复跑三条 runner-owned 后置门禁写 `[~]`，人工验收口径 1 条留 `Human-Confirmed`
  空框 `[ ]`。未改任何已交付代码、测试或 oracle 语义。
- Reason: 交付门禁要求简式 PRD 也带结构化验收清单与一致的横幅；`[~]` 项先于 verifier
  无法在此满足，按 runner 指示改写为后置门禁而非勾上或留空。
- Impact: 仅文档结构变化；runner 交付检查通过，真实入口确认仍由 PR CI / main CI 复跑承担。
- Review: 执行侧交付完成，待 runner PR CI 复跑 + 人工验收（横幅如实为 `🧍 待人工验收`）。

### 2026-10-28 · 按合并态批量追认验收
- Type: human acceptance
- Before: 横幅为待人工验收，§9 Human-Confirmed 尚有 1 项空框；交付 PR #289 已于 2026-10-15 合并，但未记录人工验收。
- After: 2026-10-28 按合并态追认，全部 Human-Confirmed 项勾选，横幅改为已验收。本次未逐项人工复核；勾选不表示未执行项已验证。遗留与未闭环：§9 独立 verifier 仍未闭合，证据目录无 verifier 报告文件。
- Reason: 交付代码已在主干运行 13 天，逐项补审无法改变既成事实；按用户 2026-10-28 的决定以合并态追认关闭验收账本，原有风险披露全部保留。
- Impact: 仅验收记录变化，无实现、契约、证据或门禁变化。
- Review: 用户 2026-10-28 在对话中明确批准批量追认；本条目未声称逐项复核。
