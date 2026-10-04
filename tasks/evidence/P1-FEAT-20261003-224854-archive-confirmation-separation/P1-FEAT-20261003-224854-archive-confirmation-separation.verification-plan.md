# 验证计划：归档与确认语义分离——执行侧完成即归档、人工验收独立记录

> 本文件是 `tasks/archive/P1-FEAT-20261003-224854-archive-confirmation-separation.md` §7.6
> Realistic Validation Plan 的执行副本。**判据以 PRD §7.6 为唯一事实源**，本文件只记录
> "怎么跑、跑在哪棵树上、用的哪份 skill、结果落在哪个文件"。

## 复现环境

| 项 | 值 |
|---|---|
| worktree | `/Users/zata/code/keda-worktrees/feat/archive-confirmation-separation` |
| 分支 | 首轮：`feat/archive-confirmation-separation`（head `a59ddd16`，已随 ZataZhang/keda#185 以 `c2fcc4b5` squash 合入 main）。归档这一轮：`docs/archive-confirmation-separation`，从 main 的 `c2fcc4b5` 开出 |
| base commit | `1b8afed9c0da6b7128ea346e9363b9c38a7e4513`（= ZataZhang/keda#184 的 head，叠在 ZataZhang/keda#183 之上）。#184 之后以 `ed30c170` squash 合入 main，tree 与 `1b8afed9` 相同 |
| base 与当时 `main` 的 `src/` 差异 | 只有 `prd_machine_contract.py` 一行（§8 Notes (a) 的 `(3, 4)` → `(3, 4, 5)`）；本次改动的 8 个模块在 base 上与当时的 `main` 逐字相同 |
| 被测 PRD skill | 首轮：模板仓 `skills/prd/SKILL.md` @ `2bf2de8`（`Machine-Contract-Version: 5`，即 ZataZhang/zata-codes-template#27 的 head），经 `IAR_PRD_SKILL_PATH` 指定。归档这一轮：本机已安装的 `~/.iar/skills/prd/`，与模板 main `2646b48d` 的 `skills/prd` 逐字相同，不设 `IAR_PRD_SKILL_PATH` |
| 本机已安装的 skill | 首轮时是 `Machine-Contract-Version: 4`（#27 未合并）。归档这一轮之前已装 v5：用 keda 的 `install_remote_template_skills(force=True)` 安装模板 main 的 skill，与 `iar init` 装的是同一份；本机 `.iar.toml` 有定制，`iar init` 会拒绝 |
| Python | 工作树 `.venv`（CPython 3.13.13） |

定向命令统一为：

```bash
# 首轮：本机当时仍装 v4，经 IAR_PRD_SKILL_PATH 指向模板的 v5
IAR_PRD_SKILL_PATH=/Users/zata/code/zata_code_template/skills/prd/SKILL.md \
  .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider --no-header -v <targets>
# 归档这一轮：本机已安装 v5，不设 IAR_PRD_SKILL_PATH
.venv/bin/python -m pytest -o addopts='' -p no:cacheprovider --no-header -v <targets>
```

`-o addopts=''` 必须带：仓库默认 `addopts = "--testmon"` 会按改动增量选择，取证时必须强制真跑。
`IAR_PRD_SKILL_PATH` 与 `tests/conftest.py` 的会话级机制是同一个入口——测试照样执行真实的
`prd_contract.py --json`，只是 skill 文件取自模板仓工作副本，而不是 `~/.iar`。**没有任何用例对
`parse_prd_contract` 打桩**，唯一例外是 rv-7 第二组按 oracle 要求在测试边界模拟旧版 skill。

## Oracle 与执行入口

证据文件一列是首轮的日志。归档这一轮的同名日志在 `raw/installed-v5/` 下。

| id | 判据（摘要） | 实际执行（真实入口 / mock 边界） | 证据文件（`raw/` 下，不进提交） |
|---|---|---|---|
| rv-1 | 执行侧完成、人审 1 个空框、横幅 🧍 → 交付即归档，字节不变，发布前检查放行 | `test_delivery_archives_prd_awaiting_human_review_real_git`：`init_git_repo` 建真实仓 → 依次调 `ensure_prd_delivery_ready`（真实 `SubprocessRunner`）与 `assert_prd_archived_for_publish`；另 3 个 fake-runner 用例钉住 `git add` + `git mv` 调用序列 | `rv-1-delivery-archives-awaiting-human.log` |
| rv-2 | 执行侧 1 项 `[ ]` → 不归档，`CHECKLIST_UNCHECKED` 点名该条 | 同一真实 git 入口；断言信息含该条原文且不含人审项；`git status --porcelain` 为空 | `rv-2-executor-open-stays-pending.log` |
| rv-3 | 四种横幅不一致 → 不归档、不发布；种类可进 closeout；指令只许改横幅 + 追加 Change Log | 真实 git 参数化 4 例（交付门禁 → 手动 `git mv` 后再过发布门禁，两道都抛 `ACCEPTANCE_BANNER_MISMATCH`）；closeout 段经 `run_agent_until_committed` 真实执行循环（agent 与 git 由 fake runner 模拟），断言收尾 prompt 文本与修复后的归档结果；`parse_prd_checklist` 字段 4 例 | `rv-3-banner-mismatch.log` |
| rv-4 | 发布前检查：已归档只剩人审空框放行 / 仍在 pending 拒绝 / 已归档但执行侧未完成拒绝 | `push_changes(require_prd_archived=True)` 参数化 3 例；推送命令由 `FakeProcessRunner` 记录，PRD 是 tmp worktree 里的真实文件 | `rv-4-publish-requires-archive.log` |
| rv-5 | 已归档 🧍 PRD 的 PR：第一轮 `skipped_human_review` 且零 rebase / 零 merge；人回答后第二轮继续 | `process_merge_queue` 两轮（`review_once` 每轮调用的公开入口）；GitHub 与 git 用 fake，PRD 是 worktree 中的真实文件，解析走真实 skill；另参数化 5 例覆盖 pending / archive × `[ ]` / `[~]` / `[x]` | `rv-5-merge-queue-hold.log` |
| rv-6 | PR 正文接受归档或 pending 路径；补锚点写归档路径；声明句改为 acceptance recording；标题与 marker 不变 | `tests/test_agent_runner_pr_body_contract.py` 整文件（纯函数 + `create_draft_pr` 发布路径） | `rv-6-pr-body-contract.log` |
| rv-7 | 无人审项 PRD 零回归；旧版 skill 不触发横幅检查；失败 Draft PR 不归档 | 第一组真实 git，断言 `git status --porcelain` 恰为一条 rename 且字节相等；第二组在测试边界把 `parse_prd_contract` 打桩为去掉 `acceptance_status` / `human_unchecked` 的 v4 形状 JSON；第三组 `publish_changes(require_prd_archived=False)` 参数化 3 例 | `rv-7-compat.log` |
| FR-6 | 提示词教"交付时归档、人审项保持 `- [ ]`、按公式设横幅"，旧语义措辞退场 | `tests/test_agent_runner_prompt_contract.py` 整文件 | `fr-6-prompt-contract.log` |

## 负控（R2 / R3 与人审项）

两层，都不改生产代码：

1. **实现前红跑**：在 `src/` 尚未改动时（`git diff --stat HEAD -- src/` 为空）先跑 rv-1、rv-5 →
   2 failed（`raw/negative-control-rv1-rv5.log`）。
2. **最终版测试 × 未修改源码的判别矩阵**：`git archive 1b8afed9` 导出未修改的 `src/`，拷入最终版
   `tests/`，用同一 venv 与同一 v5 skill 跑本次改动的 7 个测试文件（导入探针证实加载的是导出目录
   里的旧源码）→ **27 failed, 123 passed**（`raw/baseline-discrimination.log`）。逐条应红的都红、
   应绿的都绿，见证据报告"判别矩阵"一节。

## fresh-state 探针

| oracle | 探针 |
|---|---|
| rv-1 | 门禁返回后另起 `git status --porcelain` 与 `git ls-files` 读索引，从磁盘重读归档文件比字节 |
| rv-2 / rv-3 | `_assert_prd_not_moved`：pending 仍在、archive 不存在、`git status --porcelain` 为空 |
| rv-5 | 第二轮前从磁盘改写 PRD（人审项 `[x]`、横幅 ✅），由新一轮 `process_merge_queue` 与新的 fake runner 独立观察 |
| rv-7 | 第一组同 rv-1。第三组断言 `git add` 与 `git mv` 都没有发出，PRD 留在原处且内容不变（`still-pending` 那一例的 PRD 仍在 pending） |

## 低风险门禁

| 门禁 | 命令 | 证据文件 |
|---|---|---|
| 全量 pytest（首轮，v5 skill） | `IAR_PRD_SKILL_PATH=<v5> uv run pytest -o addopts='' -p no:cacheprovider tests/ -q` | `raw/full-suite-v5.log` |
| 全量 pytest（首轮，本机当时安装的 v4 skill，披露用） | 同上但 `env -u IAR_PRD_SKILL_PATH` | `raw/full-suite-installed-v4.log` |
| 全量 pytest（归档这一轮，本机已安装的 v5 skill） | `env -u IAR_PRD_SKILL_PATH uv run pytest -o addopts='' -p no:cacheprovider tests/ -q -rfE` | `raw/installed-v5/full-suite.log` |
| Drift Guard 10 行 | PRD §7.3 原命令（第 8 行加 `-g '!tasks/**'`，第 10 行补文件参数，原因见证据报告） | 首轮 `raw/drift-guard.log`；归档这一轮 `raw/installed-v5/drift-guard.log` |
| 架构 / ruff / mkdocs / 依赖面 | `check_architecture.py`、pre-commit 锁定的 ruff 0.7.4、`mkdocs build --strict`、`git diff --name-only` | 首轮 `raw/static-gates.log`；归档这一轮 `raw/installed-v5/static-gates.log` |
| 提交门禁 | 首轮：`just test`（`IAR_PRD_SKILL_PATH` 指向 v5）+ `just lint`。归档这一轮：`just test` + `just lint --repo`，都不设 `IAR_PRD_SKILL_PATH` | 证据报告"提交门禁"一节；归档这一轮 `raw/installed-v5/commit-gate-just-test.log`、`raw/installed-v5/lint-repo.log` |

## 归档这一轮的验证

首轮时 PRD 一项都没勾：独立 verifier 第 1 轮中途被中断，用户 2026-10-04 决定推迟到归档这一轮再跑。
归档这一轮按顺序做了三件事：

1. **已完成。** ZataZhang/zata-codes-template#27 合并、本机安装 v5 之后，在 main 的 `c2fcc4b5` 上
   **不设** `IAR_PRD_SKILL_PATH`，重跑了本计划的同一批命令。各 oracle 结果与首轮逐条相同，
   Drift Guard 10 行全部符合，全量 pytest `2786 passed, 1 skipped`，`just test` 与 `just lint --repo`
   通过。详见证据报告「归档这一轮：已安装 v5 复跑」。
2. **已完成。** 独立 verifier 第 1 轮（kimi，只读）结论为 `PASS`，无 BLOCKER、无 SECURITY，报告在同目录的
   `….verifier-report.md`。verifier 运行前后工作区一致，只多出它写的这份报告。
3. **已完成。** 勾选 Human-Confirmed 之外的 29 项、完成 §13 Final Reconciliation、横幅改为 🧍、PRD 移到
   `tasks/archive/`，然后复核：
   - `.venv/bin/python ~/.iar/skills/prd/scripts/check_prd_acceptance_checklist.py --check-provided --archive-ready tasks/archive/<prd>.md`
     → `PASS`。系统自带的 `python3` 太旧，跑不了这个脚本，所以用工作树的 `.venv`。
   - 用 keda 自己的 `resolve_prd_worktree_path` → `parse_prd_checklist` → `assert_prd_archived_for_publish`
     只读地读归档后的本 PRD：发布前检查放行，人审空框 3 项，见 `raw/installed-v5/archive-dogfood.log`。

第 1 步补齐了首轮时只能等待的条目：

- Dependency Acceptance："§8 Notes (a)–(c) 已在本 PRD 交付前完成"（Drift Guard 前三行）。
- Validation Acceptance："rv-1 … 真实安装的 v5 skill"、"Drift Guard 全部检查符合"、
  "`uv run pytest -o addopts='' tests/ -q` 全绿"、"`just lint` 与 `just test` 通过"。

在仍装 v4 的机器上，全量 pytest 恰有 8 个用例按设计失败——它们先调用
`require_banner_aware_prd_skill()`，以可行动的信息提示安装 v5，而不是静默跳过横幅断言。
