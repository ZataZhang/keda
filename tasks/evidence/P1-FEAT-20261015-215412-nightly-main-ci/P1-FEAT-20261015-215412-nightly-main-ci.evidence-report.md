# Evidence Report — Main CI 夜间巡检 2026-10-15

## 人审导航 / Human Review Navigation

本次交付无 `reviewer: human` 呈递物：改动是纯内部分层拆分（无 UI、无 CLI 表面、
无行为变化），全部 oracle 为构建/静态检查/测试类（`reviewer: verifier`）。人审的
最终确认物是 **PR 关联的 CI run 由红转绿**（GitHub Actions 链接随 PR 发布，属
runner-owned gate，不在本执行侧证据树内）。

## 失败入口与根因

- 真实失败入口：main 分支 GitHub Actions `CI` workflow，run
  `38745391997`（2026-10-15 13:45Z，https://github.com/ZataZhang/keda/actions/runs/38745391997 ），
  job `Validate Template` / step `Check max file lines (hard limit)` 失败原文：
  - `[ERROR] src/backend/core/use_cases/agent_runner_failure.py: 1002 非空行，超过上限 1000 行。`
  - `[ERROR] src/backend/core/use_cases/agent_runner_orchestration_runtime.py: 1001 非空行，超过上限 1000 行。`
- 根因：`973c8d9b`（stream failure reasons）与 `b29565e4`（lifecycle/recovery
  workflows）分别向两个本已接近上限的文件追加逻辑，越过 1000 非空行硬门禁；
  `hooks/max_file_lines.allowlist.txt` 为**空名单**（约定：新增文件不得进入
  allowlist，超限即拆分），PRD 执行规则 3 同此要求。
- 连锁根因（门禁修复后才暴露）：CI 的 `Validate Template` job 在 `Check max file
  lines` 之后**同一 job 继续跑全量 pytest**（`ci.yml:157`，与 `cd.yml` 同步）。此前
  门禁先失败、pytest 步被跳过，于是 `test_packaged_skill_main_file_keeps_shared_baselines`
  这条「随包 operator skill 主文件常驻 ≤70 非空行」守卫被掩盖了一个多月——门禁转绿后
  它会立即把同一 job 重新判红。该失败与本次拆分无关，属 main 上的存量超额：`8f8642d8`
  设立 70 行常驻预算后，`e74eceb0`（issue-scoped auto-merge）新增标签条目把主文件顶到
  71 行。要让 main CI 真正转绿必须一并修复，故按守卫规则改**源文件**、不动预算（不放宽
  到 71）。

## 修复（最小拆分，行为不变）

- `agent_runner_failure.py`：保留异常类型与失败分类（294 非空行）；评论 /
  attempt history 渲染（含仅被渲染侧使用的 usage-limit 根因行、workflow label /
  rebase HEAD 判定 helper）拆入 `agent_runner_failure_format.py`（757 非空行）。
  原模块 `__all__` 全部公共名再导出，消费方（run_agent_once、agent_review、
  pr_supervisor、recover_publish、failure_marking、reconcile、closeout、tests 等）
  导入路径不变；`agent_runner_stale_attempt.py` 的私有常量导入改指新模块。
- `agent_runner_orchestration_runtime.py`：PRD rework 阶段独立拆入
  `agent_runner_orchestration_prd_rework.py`（86 非空行；与 ready 派发循环零共享
  状态），runtime 降至 928 非空行。orchestrate 的 monkeypatch 依赖注入接缝
  （`RUNTIME_DEPENDENCY_NAMES` + `_orchestration_runtime_module` 同步环）扩展为
  runtime / issue_handlers / prd_rework 三模块，补丁中枢仍在
  `agent_runner_orchestrate`，既有接缝语义不变。
- 文档同步：`docs/ai-standards/time-display.md` 中尝试历史表的模块指向
  `agent_runner_failure_format.py`。
- 随包 operator skill 主文件 `.../skills/kedacode-operator/SKILL.md`：把 Routes 段
  末尾两个独立散文段（命令权威引用 / legacy `iar` 别名）合并为一行，非空行 71→70，
  恢复 `8f8642d8` 设定的常驻预算；不删任何被守卫断言的 token（退出码表、只读禁令、
  `kc schema --json` 自省入口、`requires a target` 契约、`## Routes` 分节）。

## 验收项 ↔ 证据

| rv-id | 验收内容 | 证据（本地，不进版本库） | 结果 |
|---|---|---|---|
| rv-1 | CI 逐字相同的 max-lines 门禁命令在最终代码树通过（空 allowlist，零 ERROR，exit 0） | `rv-1-max-file-lines.txt` | PASS（修复前同命令即 CI 报错原文） |
| rv-2 | 受影响面回归：agent_runner / backlog / verifier / supervisor 全部测试文件（`--no-testmon` 全跑） | `rv-2-targeted-tests.txt` | PASS（1988 passed, 0 failed, 188s） |
| rv-3 | 公共名再导出与依赖注入接缝冒烟（facade 派发 + `refresh_runtime_dependencies` 后 rework 模块协作者就位） | `rv-3-api-seam-smoke.txt` | PASS |
| rv-4 | 四层架构方向检查 `check_architecture.py` | `rv-4-arch-check.txt` | PASS（无违规） |
| rv-5 | `mkdocs build --strict`（time-display 文档指向更新后） | `rv-5-mkdocs-strict.txt` | PASS（exit 0） |
| rv-6 | `just lint --repo` 全量本地门禁（含 ruff / docstring / 复用 / 全量 pytest） | `rv-6-just-lint-repo.txt` | 拆分修复后全量套件仅剩 1 例失败 = 存量 skill 预算守卫（`1 failed, 4461 passed`），据此引出 rv-7；无 ruff / docstring / 复用告警 |
| rv-7 | 随包 skill 主文件恢复 ≤70 非空行；读取该文件的 4 个测试模块全绿 | `rv-7-skill-budget.txt` | PASS（70 行；`test_kedacode_operator_skill` 27 passed + `test_cli_skill_install`/`test_agent_runner_init`/`test_product_identity` 92 passed） |

最终代码树 = 本次交付 commit 的 tree；上述证据全部在拆分与文档改动完成后复跑。
GitHub 侧真实入口确认（main workflow 转绿）由 runner 的 PR CI 复跑承担，属
runner-owned gate。
