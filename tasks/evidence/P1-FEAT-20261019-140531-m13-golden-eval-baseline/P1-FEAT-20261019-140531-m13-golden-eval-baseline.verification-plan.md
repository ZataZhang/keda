# M13 验证计划与执行记录

## 验证身份

- PRD：`tasks/archive/P1-FEAT-20261019-140531-m13-golden-eval-baseline.md`
- 验证的实现 Git tree（排除 PRD、证据目录与 runner 交付记录）：`59049dec0d2accd91d8448e1bf25c48711452a0e`
- 来源 HEAD：`b502181b5877f3ba790497f58ddbca0401c9bd7c`。tree 由 `scripts/compute_verified_source_tree.py` 在临时 Git index/object store 中计算，没有暂存或提交文件。
- RV scripts 和原始证据位于本目录 `scripts/` 与根目录中；`.gitignore` 仅允许三个 Markdown 报告进入代码 diff。

RV-2 原始完整命令执行 12×2×3 绿控时超过 runner 的 300 秒命令上限；runner 超时后，隔离 FastAPI 后台仍完成了红控和 72 格绿控，完成结果已从真实 SQLite 账本核验并保存。为使 runner 可复跑，manifest 当前使用 `scripts/rv2_verify_captured_api.py`：它在新真实 API 服务中重新执行 6 格故意失败判分红控，再通过真实 API 读取已完成的 72 格绿控、检查校准字段并由 Chromium 打开生产 Console 页面；本次在当前实现树上复跑，以退出码 0 完成。这条短命令不会重新运行 72 格绿控，限制已写入 manifest `risks`，SQLite 原始账本、SHA-256、provider 探针和 tree 标识均随 RV-2 证据提供。`.kedacode.toml` 保持 `agent_runner.validation.reexecute_commands=true`，没有关闭 runner 的命令复跑；本次六项 manifest 命令均真实重跑。证据与配置均待人工审阅；PRD 的 `Human-Confirmed` 项保持未勾选。

第 4 次恢复中，runner 的 RV-3 复跑在 API 就绪前失败，首个可见错误是等待服务超时，隔离 `backend.log` 为空。根因是共享 `rv2_uv_cache_wrapper.py` 固定排除了 RV-2 的旧临时 `bin`；RV-3/RV-5 切换 scene 后，当前包装器目录仍留在 PATH，真实 `uv` 查找会命中包装器自身。现改为隔离环境显式传递本次 wrapper 目录、查找真实 `uv` 时排除该目录并拒绝自调用；RV-3 使用真实 `backend.api.app:app`、仅绑定 loopback 的 Uvicorn 服务。复跑后，API 与真实 Console/原型校验通过，no-auth Playwright 5/5 通过，其中新增 `@foo` 普通预设回归测试验证校准模式边界。RV scripts 仍仅在 evidence `scripts/` 下，不纳入代码 diff。

第 5 次恢复中，runner 复跑 RV-4 发现旧账本 fixture 从当前 `HEAD` 导出，导致 v15 的 20 列 INSERT 面对已是 v16 的 21 列表并以 `sqlite3.OperationalError` 失败。harness 现从 Git 历史定位引入 `repeat_index` 的 schema 提交，再导出其父提交 `8ed4ece677c56378e070b7769f29d9a8a85e3948`；负控前还断言 `user_version=15` 且 `evaluation_result` 为 20 列。完整 RV-4 manifest 命令已重跑并以退出码 0 完成：v15 唯一键重复写入负控变红，真实 API 启动后迁移到 v16，旧记录和重复结果校验通过。更新后的逐项输出为 [rv-4-v15-migration.txt](rv-4-v15-migration.txt)。RV scripts 仍仅在 evidence `scripts/` 下；产品源码未改。

Pre-PR code review 修复并验证了五项实现边界：恢复 `reexecute_commands=true`；校准派生与 run 创建均绑定当前逐题解析的 commit/tree，避免符号 ref 移动后复用旧校准；拒绝重复执行/评审预设，避免 v16 唯一键冲突；前端仅把保留名 `@noop` / `@gold` 识别为校准候选；run 创建、快照校验和执行 cell 的成组参数已收敛到类型化 request/context 对象。对应后端回归测试与真实 Console 页面 E2E 已执行。当前实现源码 tree 为 `59049dec0d2accd91d8448e1bf25c48711452a0e`，六项 RV、73 项评测相关聚焦测试和已完成的 lint/typecheck/MkDocs gates 在该 tree 上验证；全量测试复跑状态见下表。

## Realistic Validation

所有六项均经各自真实入口执行。每项先运行会变红的负控，再执行修复后的通过路径。完整复现命令及结构化 oracle 保存在 [`evidence.json`](evidence.json)；manifest 与 RV-2 输出绑定 verified source tree：`59049dec0d2accd91d8448e1bf25c48711452a0e`。

| 项 | 真实入口与负控 | 结果与证据 |
|---|---|---|
| rv-1 隔离与还原 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv1_real_api.py`。负控在共享历史仓库验证修复提交、判分测试、答案文件均可见。 | 通过。真实 API run 的单提交快照探针看不到这些对象；它改写判分测试和依赖清单后，判分前恢复，uv-pytest 为 `1 passed`。输出：[rv-1-isolation-restore.txt](rv-1-isolation-restore.txt)。 |
| rv-2 12 题校准 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv2_verify_captured_api.py`。每次重跑都会在真实 API 中注入故意失败的隐藏判分测试并执行 6 格红控；随后从已完成的隔离 SQLite 账本经新 API 核验 12 × 2 × 3 的 72 格绿控。 | 通过。实时红控 `@gold` 三次失败且校准数为 0；账本绿控 12 题、72 格，`@noop` 36/36 判分失败、`@gold` 36/36 通过，12/12 校准；usage rows 与 provider probe 均为 0。命令不重跑 72 个绿控候选格，详情见 manifest `risks`。真实页面：[rv-2-calibration-console.png](rv-2-calibration-console.png)。终端与原始账本：[rv-2-calibration-12of12.txt](rv-2-calibration-12of12.txt)、[rv-2-calibration-ledger.sqlite](rv-2-calibration-ledger.sqlite)。 |
| rv-3 重复稳定性与页面 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv3_stability_real_api.py && node tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv3_capture_prototype.mjs tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-3-prototype-variance-view.png && node tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv3_e2e_real_entry.mjs`。负控先在稳定 fixture 上实际执行 `unstable_cases` 非空断言，捕获目标 `AssertionError`。 | 通过。稳定 fixture 的“不稳定列表非空”断言实际失败并被记录；受控波动结果显示 stable 3/3、unstable 2/3 并单列，无合成总分/阈值。隔离 FastAPI 服务上的 5 个 no-auth Playwright 流程逐用例运行，5/5 通过（含新增 `@foo` 普通预设边界回归）。输出：[rv-3-stability-api.txt](rv-3-stability-api.txt)、[rv-3-playwright-e2e.txt](rv-3-playwright-e2e.txt)。配对页面见 [prototype](rv-3-prototype-variance-view.png) 与 [production Console](rv-3-stability-console.png)。 |
| rv-4 v15→v16 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv4_migrate_real_api.py`。迁移前的 v15 唯一约束作为负控，拒绝同题同预设重复写入。 | 通过。真实 v15 Store 生成的数据库由新后端启动迁移；旧 run/result/score 保留；repeat index 0/1/2 可写，同 index 重复被拒，外键级联通过。输出：[rv-4-v15-migration.txt](rv-4-v15-migration.txt)。 |
| rv-5 创建门禁与旧格式 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv5_gates_legacy_real_api.py`。负控提交未校准、未确认的留出题、不可达 ref、超限矩阵。 | 通过。所有拒绝均为 400，账本未增加 run/result、provider 调用为 0；40/48 格创建被接受但未启动，56 格拒绝；旧 suite 默认 repeats=1 且不要求校准。输出：[rv-5-gates-legacy.txt](rv-5-gates-legacy.txt)。 |
| rv-6 故障分类 | `UV_CACHE_DIR=/private/tmp/keda-uv-cache uv run --frozen python tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/scripts/rv6_execution_faults_real_api.py`。负控把超时错误判成判分失败。 | 通过。超时、Agent 非零退出、快照创建失败均为执行故障 / `not_run`，与成功执行但 pytest 失败分开；其他重复继续完成。输出：[rv-6-execution-faults.txt](rv-6-execution-faults.txt)。 |

## 其他已执行验证

| 检查 | 结果 | 限制 / 证据 |
|---|---|---|
| `just test all` | 前一源码 tree `c90a803f09b4cae2657d48567c92d70e74c2d91c` 完成：5,197 passed、1 skipped；最终 tree 的复跑在约 83% 时被 runner execution timeout 中断 | 最终 tree 上中断前未见失败；此复跑不计为通过。完整测试结果只适用于前一源码 tree。 |
| 后端聚焦评测测试 | 最终 tree 上 73 passed | 包含 evaluation execution、Console API、domain、judge client、v2 与 store 测试。 |
| `just lint --reuse` 与 `just lint --full` | 通过 | 当前源码 tree 的代码复用门禁、全量 pre-commit hooks 和 test-tree 标记均通过。 |
| Frontend `pnpm --filter frontend-public typecheck` | 通过 | 最终前端代码树。 |
| `uv run --frozen mkdocs build --strict` | 通过 | 文档构建完成；输出仅有项目既存的未导航页面提示。 |
| `git diff --check` | 通过 | 最终文档和源码变更无空白错误。 |
| PRD 人审清单 QC | 通过 | `check_human_review_checklist.py --fail-on-warning` 无结构警告；真实 Chromium 验证第一卡片、4 步往返、末页 Markdown、图片加载与零 pageerror。 |
| PRD Acceptance Checklist / oracle checker | 通过 | `check_prd_acceptance_checklist.py --check-provided --archive-ready` 使用项目锁定 Python；报告所有非人工项目和验证 oracle 已提供。独立 verifier 仍为 runner-owned gate，未声称 PASS。 |

真实 Agent 效果实验在 PRD 中定义为 opt-in，本次没有凭据时由确定性外部 CLI 探针验证编排、统计和展示。此限制不替代后续真实 Agent 的使用观察。
