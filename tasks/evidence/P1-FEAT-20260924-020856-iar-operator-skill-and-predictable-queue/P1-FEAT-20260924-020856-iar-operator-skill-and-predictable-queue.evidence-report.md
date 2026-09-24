# Evidence Report — IAR 操作 Skill 与可预期队列

PRD: `tasks/pending/P1-FEAT-20260924-020856-iar-operator-skill-and-predictable-queue.md`

## 验证结果

- `just lint --reuse`: PASS。
- `SKIP=check-test-flag just lint --full`: PASS。
- `just test all`: **2508 passed in 124.39s**。
- `uv run mkdocs build --strict`: PASS。现有提示包含未纳入导航页和两处既存失效锚点；本次文档页未新增，构建退出码为 0。
- `uv build --wheel --out-dir /tmp/iar-operator-dist`: PASS；wheel 中含打包的 IAR operator Skill。

## Realistic Validation Oracles

### rv-1 — Queue order

测试：`tests/test_agent_runner_orchestrate.py::test_run_once_dry_run_orders_ready_issues_by_priority_then_number` 与 `::test_run_once_execution_uses_the_same_priority_order_as_dry_run`。

乱序候选同时覆盖 P0、同级 P1、P3 与无优先级。dry-run 选择顺序和执行传给 worker 的顺序均为 `#7, #10, #11, #8, #9`，对应 P0、P1、P1、P3、unset。dry-run 明示未设置优先级为 `unset (after P3)`，并说明排序覆盖 GitHub 返回的 100 条候选窗口。

### rv-2 — Issue list real CLI

测试：`tests/test_issue_list.py::test_issue_list_real_cli_filters_fixture_results_by_state_and_label`。

真实 `main`/Typer 参数解析、CLI handler 和 `list_issues_with_prs` use case 运行；隔离 GitHub fixture 提供 open+label、open-only、closed+label 三条 Issue。JSON 仅包含 `#41`，并确认查询传入 `state=open` 和 `label=agent/ready`。

### rv-3 — Machine Contract preflight

测试：`tests/test_prd_skill_preflight.py` 与 `tests/test_agent_runner_orchestrate.py::test_run_once_unknown_machine_contract_performs_no_github_writes`。

v3 通过；v1、缺少 marker、缺文件和 v99 被拒绝。v99 真实 runner 调用返回 `1`，fake GitHub client 调用记录为 `[]`。拒绝提示只建议检查/更新 `prd` Skill，不建议盲目 `--force`。

### rv-4 — Packaged Skill install plan

从 wheel 安装到隔离 Python target 后，通过该安装包的 CLI 在隔离 HOME/repo 执行 `iar init --dry-run`：

- 干净 HOME：`Would install packaged IAR operator skill: <isolated HOME>/.codex/skills/iar-operator`。
- 预先存在同名用户 Skill：`Would preserve existing user skill (conflict; use --force to replace): <isolated HOME>/.codex/skills/iar-operator`。
- 两次运行均为 dry-run；冲突 sentinel 内容保持原样。

原始捕获位于实施环境 `/tmp/iar-operator-rv4/{clean-home,conflict-home}.out`，未加入仓库。完整 Skill 交由 PR 页面链接展示。

### rv-5 — Operator Skill wording

Skill 逐段核对了 init、PRD 入队、Issue 查询、dry-run、单次执行、registry daemon lifecycle 和副作用说明，并与现有 CLI/指南一致。最终文本：[`iar-operator/SKILL.md`](../../../src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md)。

## 限制

未执行真实 GitHub live smoke。GitHub 边界由 hermetic fixture 覆盖；没有数据库 schema 变更或前端页面变更。队列顺序只承诺 GitHub 返回的最多 100 个 ready Issue 候选窗口。

## 人审导航 / Human Review Navigation

请在 PR 页面阅读 `src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md` 全文，并在 evidence comment 检查 rv-4 干净 HOME 与冲突 HOME 安装计划、§2 两项已确认决策及其用户可见行为。合并 PR 即代表接受 PR body 列出的决定/结果并授权合并后的 PRD 归档；合并前 §9 `Human-Confirmed` 保持未勾选。
