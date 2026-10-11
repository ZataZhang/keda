# Evidence Report — P1-FEAT-20261009-133425-lifecycle-agent-model-settings

- Issue: <https://github.com/ZataZhang/keda/issues/262>
- Canonical PRD: `tasks/archive/P1-FEAT-20261009-133425-lifecycle-agent-model-settings.md`
- 验证计划与复跑命令：同目录 `<prd-stem>.verification-plan.md`
- PR / CI：PR 由 runner 在本报告之后创建（标题需含 `Closes #262`）；CI 跑完整测试集与
  frontend-build（typecheck + build）。本地已复跑：`pnpm run typecheck` exit 0、
  `next build` exit 0（产出 `/app/settings/lifecycle` 静态路由）、
  `SKIP=check-test-flag uv run pre-commit run --all-files` exit 0、
  `uv run mkdocs build --strict` exit 0。

## 人审导航 / Human Review Navigation

本目录的原始 artifact（`.png` / `.txt` / `evidence.json` / `scripts/`）按仓库约定
**不进版本控制**（`.gitignore` 只放行本目录的 `*.md`）。因此在 GitHub 上图片是坏链——
每条都配了「就地嵌图 + 本地-only 标注 + open 命令」三件套，本地预览可直接看图。

### 1. 生命周期矩阵 + 模型预设 + 执行器回退（桌面，真实生产路由）

![rv-2 桌面：九阶段矩阵、模型预设卡片、执行器回退候选](rv-2-lifecycle-settings-desktop.png)

> 验证层级：**真实生产路由**（浏览器打开后端同源挂载的 `/app/settings/lifecycle/`，
> 数据来自真实 `GET /api/v1/agent-runner/lifecycle-settings`，非 component preview）。
> 本地图像为 local-only，GitHub 上不渲染。

```bash
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-2-lifecycle-settings-desktop.png"
```

逐项期望值：

| 位置 | 期望看到 | 判定 |
|---|---|---|
| 顶部范围选择器 | 「全局 / 仓库」两态，当前选中「全局」，下方注明正在编辑 `全局 config.toml` | 范围可切换且落点如实标注 |
| 区块导航 | 「生命周期矩阵 · 模型预设 · 执行器回退」三个锚点 | 三块在同一页 |
| 矩阵行数 | 固定 **9 行**：实现 / 修复 / 收尾 / 校验 / 审核 / 监督 / 决策 / 内容生成 / 辩论 | 九键齐全 |
| 每行字段 | 生效 Agent、模型、推理深度、绑定预设，且各字段**单独标来源** | 无空白字段 |
| 未显式配置处 | 显示「Agent 默认 / 未显式指定」或「该 Agent 不支持」，**不编造值** | 如实呈递 |
| 审核行 | 已绑定 `gpt-review` → 生效 Agent `codex`、模型 `gpt-5`（来源：预设声明）、推理深度「该 Agent 不支持」（来源：全局 config.toml） | 三元组来自预设 |
| 修复 / 收尾行 | 标注「继承实现阶段」，生效 Agent 显示具体名字 | fix/closeout 继承 |
| 模型预设区 | 双列卡片（`sonnet-high`、`gpt-review`），卡片标「绑定阶段：review」，下方「新建预设」表单 | 预设是配置单元 |
| 执行器回退区 | 3 行有序候选，`claude` 出现两次（一次绑 `sonnet-high`、一次跟随执行器默认），`codex` 绑 `gpt-review`；预算 `max_agent_switches=2` 并注明「最多尝试 3 个候选，按候选步数计数」 | FR-9 / FR-10 |

### 2. 执行器回退候选区（同页放大）

![rv-2 执行器回退候选与预设绑定](rv-2-settings-fallback.png)

> 验证层级：真实生产路由同页截图。local-only 图像。

```bash
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-2-settings-fallback.png"
```

期望：每行「执行器」+「预设」两列；预设下拉只列同执行器的预设；行内显示该候选的
模型 / 推理深度摘要；未绑定行显示「Agent 默认 / 未显式指定」；卡片注明本区固定写全局
`config.toml`（不随生命周期范围切换）。

### 3. 400px 窄屏（不依赖横向滚动）

![rv-2 窄屏 400px 九行矩阵](rv-2-lifecycle-settings-mobile.png)

> 验证层级：真实生产路由，视口 400px。local-only 图像。

```bash
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-2-lifecycle-settings-mobile.png"
```

期望：九行仍全部渲染；横向溢出 **0px**；每行宽度 318px（视口 400px）；「生效 Agent」
字段未被裁剪（292px 可见）。这三项由 `rv2_capture.cjs` 在浏览器里实测，不是目测。

### 4. CLI 真实入口记录（rv-1 / rv-3）

```bash
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-1-lifecycle-cli.txt"
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-3-compat.txt"
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-3-delete-integrity.txt"
```

期望：A 段负控逐条 `[exit=2] …（负控如预期变红）`；B/C 段正控写盘后**新 CLI 进程**读回
相同值；末行哨兵 `RV-1 RESULT: PASS` / `RV-3 RESULT: PASS` / `RV-3-DEL RESULT: PASS`，
且每段都有「判别性检查失败数：0」。

### 执行者已替你核对过什么

- 九键、逐字段来源、继承与「不支持」标示：由后端聚合视图下发，前端不持有第二份映射
  （`rg LIFECYCLE_AGENT_ENTRY|LIFECYCLE_AGENT_KEYS src tests frontend-public` 无前端命中）。
- 写入保留式：`tests/test_toml_section_editor.py` 钉住「同值写回 no-op / 数组表保住位置与
  注释 / 纯删除不建文件」，另有本轮新增的「替换失败不留临时文件」「两次落盘临时名不同」。
- 兼容性：`kc agent presets`、`kc agent doctor --lifecycle`、单次 `--preset/--model/
  --reasoning-effort`、旧 Agent-only 端点与 PRD 覆盖优先级均有契约测试（rv-3 段内 57 passed）。
- 未替你做的：`Human-Confirmed` 五项（PRD §9.2）按约定留空，需你在真实 Console 里点一遍。

## 证据与交付树的绑定

| 检查点 | 采集脚本 | 结果 | 绑定树 |
|---|---|---|---|
| rv-1 | `scripts/rv1_capture.sh` | `RV-1 RESULT: PASS` | 本轮评审修复后的工作树 |
| rv-2 | `scripts/rv2_run.sh`（自拉起生产栈） | `RV-2 RESULT: PASS` + `RV-2 WRAPPER RESULT: PASS` + 真实 Playwright `7 passed` | 同上（含前端拆分后的重建 bundle） |
| rv-3 | `scripts/rv3_capture.sh` + `scripts/rv3_delete_integrity.sh` | `RV-3 RESULT: PASS` + `RV-3-DEL RESULT: PASS` | 同上 |
| 定向复验 | `scripts/rv1_final_tree.sh` | `RV-1-FINAL RESULT: PASS` | `head_commit acaa0584`，`working_state_tree 6e6fe60f…` |
| 全量测试 | `JUST_FULL_TEST_FLAGS=--no-testmon just test all`（`scripts/r5_just_test_all.sh`） | `R5-FULL-SUITE RESULT: PASS（3848 passed, 1 skipped）` → `rv-just-test-all.txt` | 本轮修复后的交付树 |

> **本轮（第六轮评审修复）之后**：代码侧改动为 `toml_section_editor` 的临时文件唯一化
> （含 2 条新回归测试）、前端统一页按职责拆成 4 个 sibling 模块（行为保持），其余为
> 文档 / PRD / 配置模板注释。rv-1 / rv-2 / rv-3 / rv-1-final 与全量测试均已在含这些改动的
> 树上整体重跑并全绿（上表）：`RV-1 / RV-2 / RV-2 WRAPPER / RV-3 / RV-3-DEL / RV-1-FINAL`
> 均 PASS、真实 Playwright 7 passed、窄屏横向溢出 0px、全量 `3848 passed, 1 skipped`
> （较第五轮的 3846 多出的 2 条正是本轮新增的 TOML 写回归断言）。
> 树标识以 `rv-1-final-tree-reverification.txt` 当次打印的 `working_state_tree` 为准；
> 该打印之后仅追加了 PRD Change Log 与本报告的 markdown 编辑，无代码行为变化。

## 已知限制（不粉饰）

1. **e2e 认证入口**：本 worktree 无 `PLAYWRIGHT_IDENTIFIER/PLAYWRIGHT_PASSWORD`，全套
   e2e 中 19 项因缺凭据 / registry 数据失败；已用 HEAD 基线对照确认与本次改动无关，
   `lifecycle-settings.spec.ts` 的 7 项在真实栈上全绿。
2. **模型目录不校验**：预设的 model / effort 只做「能否按已声明参数模板注入」的校验，
   不查 provider 目录、不验模型 ID 在线有效或账号权限（PRD 行为样例已声明该边界）。
3. **verifier-report.md 缺席**：`<prd-stem>.verifier-report.md` 由 runner 的独立 verifier
   出具，执行器不代写；本报告只覆盖执行器侧的 verification-plan 与 evidence-report。
