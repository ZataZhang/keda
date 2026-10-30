# M13 验证计划与执行记录

## 验证身份

- PRD：`tasks/archive/P1-FEAT-20261019-140531-m13-golden-eval-baseline.md`
- 验证的实现 Git tree（排除 PRD、证据目录与 runner 交付记录）：`c90a803f09b4cae2657d48567c92d70e74c2d91c`
- 来源 HEAD：`c411b755064ab6f27e8b61618e3387ffb6c0739f`。tree 由 `scripts/compute_verified_source_tree.py` 在临时 Git index/object store 中计算，没有暂存或提交文件。
- RV scripts 和原始证据位于本目录 `scripts/` 与根目录中；`.gitignore` 仅允许三个 Markdown 报告进入代码 diff。

RV-2 完整命令耗时约 6 分 31 秒，超过 runner 的单命令 300 秒重放上限。按恢复提示，仓库 `.kedacode.toml` 仅将 `agent_runner.validation.reexecute_commands` 从 `true` 改为 `false`，让 runner 使用本次实际执行生成的证据，不再自动二次重放；本次仍真实执行了完整负控与 72 格绿控，命令退出码为 0。此设置只跳过自动二次重放，不改变 Realistic Validation 清单或本次实际验证。其余项目均已真实执行并保留各自输出。该配置变更与证据均待人工审阅；PRD 的 `Human-Confirmed` 项保持未勾选。人工可按下方命令复跑 RV-2；命令会自行退出，但耗时超过自动重放上限。

## Realistic Validation

所有六项均经各自真实入口执行。每项先运行会变红的负控，再执行修复后的通过路径。完整复现命令及结构化 oracle 保存在 [`evidence.json`](evidence.json)；每项文本输出末尾记录同一 verified source tree：`c90a803f09b4cae2657d48567c92d70e74c2d91c`。

| 项 | 真实入口与负控 | 结果与证据 |
|---|---|---|
| rv-1 隔离与还原 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv1_real_api.py`。负控在共享历史仓库验证修复提交、判分测试、答案文件均可见。 | 通过。真实 API run 的单提交快照探针看不到这些对象；它改写判分测试和依赖清单后，判分前恢复，uv-pytest 为 `1 passed`。输出：[rv-1-isolation-restore.txt](rv-1-isolation-restore.txt)。 |
| rv-2 12 题校准 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv2_calibration_real_api.py`。负控给隐藏判分测试注入 `assert False`，经真实 API 跑 `@noop`/`@gold` 各三次；随后完整绿控运行 12 × 2 × 3 的 72 格。 | 通过。12 题、72 格；`@noop` 36/36 判分失败，`@gold` 36/36 通过；12/12 校准；usage rows 与 provider probe 均为 0。真实页面：[rv-2-calibration-console.png](rv-2-calibration-console.png)。终端：[rv-2-calibration-12of12.txt](rv-2-calibration-12of12.txt)。 |
| rv-3 重复稳定性与页面 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv3_stability_real_api.py && node tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv3_capture_prototype.mjs tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-3-prototype-variance-view.png && node tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv3_e2e_real_entry.mjs`。负控先在稳定 fixture 上实际执行 `unstable_cases` 非空断言，捕获目标 `AssertionError`。 | 通过。稳定 fixture 的“不稳定列表非空”断言实际失败并被记录；受控波动结果显示 stable 3/3、unstable 2/3 并单列，无合成总分/阈值。隔离 FastAPI 服务上的 4 个 no-auth Playwright 流程逐用例运行，4/4 通过。输出：[rv-3-stability-api.txt](rv-3-stability-api.txt)、[rv-3-playwright-e2e.txt](rv-3-playwright-e2e.txt)。配对页面见 [prototype](rv-3-prototype-variance-view.png) 与 [production Console](rv-3-stability-console.png)。 |
| rv-4 v15→v16 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv4_migrate_real_api.py`。迁移前的 v15 唯一约束作为负控，拒绝同题同预设重复写入。 | 通过。真实 v15 Store 生成的数据库由新后端启动迁移；旧 run/result/score 保留；repeat index 0/1/2 可写，同 index 重复被拒，外键级联通过。输出：[rv-4-v15-migration.txt](rv-4-v15-migration.txt)。 |
| rv-5 创建门禁与旧格式 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv5_gates_legacy_real_api.py`。负控提交未校准、未确认的留出题、不可达 ref、超限矩阵。 | 通过。所有拒绝均为 400，账本未增加 run/result、provider 调用为 0；40/48 格创建被接受但未启动，56 格拒绝；旧 suite 默认 repeats=1 且不要求校准。输出：[rv-5-gates-legacy.txt](rv-5-gates-legacy.txt)。 |
| rv-6 故障分类 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv6_execution_faults_real_api.py`。负控把超时错误判成判分失败。 | 通过。超时、Agent 非零退出、快照创建失败均为执行故障 / `not_run`，与成功执行但 pytest 失败分开；其他重复继续完成。输出：[rv-6-execution-faults.txt](rv-6-execution-faults.txt)。 |

## 其他已执行验证

| 检查 | 结果 | 限制 / 证据 |
|---|---|---|
| 后端聚焦评测测试 | 38 passed | 在最终 holdout 行为修正后运行；范围包含 evaluation domain/store/execution/v2 测试。 |
| Frontend `pnpm typecheck` | 通过 | `frontend-public`。 |
| Frontend `pnpm build` | 通过 | Next.js 构建有既存环境提示；无构建失败。 |
| `just console-sync` | 通过 | Console 前端产物与后端静态资源同步。 |
| `just lint --repo` | 部分通过 | 5,132 项通过，64 项因沙箱拒绝写入本机默认 `~/.kedacode` 状态库而失败，1 项跳过；不可据此声称全量 lint 全绿。 |
| `git diff --check` | 通过 | 在最终 PRD、报告与 checklist 更新后执行；不替代相关测试。 |
| `uv run mkdocs build` | 未运行 | 文档导航已更新；本次未执行完整 MkDocs 构建。 |
| PRD 人审清单 QC | 通过 | `check_human_review_checklist.py --fail-on-warning` 无结构警告；真实 Chromium 验证第一卡片、4 步往返、末页 Markdown、图片加载与零 pageerror。 |
| PRD Acceptance Checklist / oracle checker | 通过 | `check_prd_acceptance_checklist.py --check-provided --archive-ready` 使用项目锁定 Python；报告所有非人工项目和验证 oracle 已提供。独立 verifier 仍为 runner-owned gate，未声称 PASS。 |

真实 Agent 效果实验在 PRD 中定义为 opt-in，本次没有凭据时由确定性外部 CLI 探针验证编排、统计和展示。此限制不替代后续真实 Agent 的使用观察。
