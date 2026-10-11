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

### 4. 仓库范围覆盖（只改所选仓库，global 与另一仓不动）

![rv-2 仓库范围：review 绑定到仓库覆盖的预设](rv-2-lifecycle-settings-repository.png)

> 验证层级：真实生产路由，scope 切到仓库并选中 `rv2repo`。local-only 图像。

```bash
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-2-lifecycle-settings-repository.png"
```

期望：review 行显示 `ui-made-preset` / 生效 Agent `claude` / 绑定来源「仓库 .kedacode.toml」，
而 global 仍是 `gpt-review` / `codex`。这一张补齐了第七轮评审指出的缺口——早前的浏览器证据
只在 global 范围点过保存，repository 路径要靠独立 verifier 自行补探。

### 5. CLI 真实入口记录（rv-1 / rv-3）

```bash
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-1-lifecycle-cli.txt"
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-3-compat.txt"
open "tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/rv-3-delete-integrity.txt"
```

期望：A 段负控逐条 `[exit=2] …（负控如预期变红）`；B/C 段正控写盘后**新 CLI 进程**读回
相同值；末行哨兵 `RV-1 RESULT: PASS` / `RV-3 RESULT: PASS` / `RV-3-DEL RESULT: PASS`，
且每段都有「判别性检查失败数：0」。

### 6. 页面负控（PRD §7.6 给 rv-2 声明的 oracle，第七轮补齐）

`rv-2-lifecycle-page-run.txt` 现在在同一真实页面里带这四条，reviewer 可直接 grep：

| 检查 | 期望观察 |
|---|---|
| 提交无 `reasoning_effort_args` 模板的推理深度 | `PATCH 422`，页面错误位点名 `preset 'ui-bad-effort' … reasoning-effort argument template`；global / 所选仓库 / 另一仓**三份文件字节全等** |
| 仓库范围删除只有全局层声明的预设 | `PATCH 422`，detail 指认仓库文件里没有 `[agent_runner.presets.ui-made-preset]` 并说明该改哪一配置范围；预设仍声明在 global |
| 新加 codex 回退候选的预设下拉 | 只列 codex 自己的预设（`option-sonnet-high` 计数 0）——跨 agent 组合在页面里构造不出来 |
| 有效的 repository-only 绑定 | `PATCH 200`，仓库文件落 `review = "ui-made-preset"`，global 与 `rv2other` 逐字节不变，fresh CLI 两个 scope 各读各的 |

### 执行者已替你核对过什么

- 九键、逐字段来源、继承与「不支持」标示：由后端聚合视图下发，前端不持有第二份映射
  （`rg LIFECYCLE_AGENT_ENTRY|LIFECYCLE_AGENT_KEYS src tests frontend-public` 无前端命中）。
- 写入保留式：`tests/test_toml_section_editor.py` 钉住「同值写回 no-op / 数组表保住位置与
  注释 / 纯删除不建文件」，第七轮又补上「沿用目标权限位（0644 不被写成 0600）」
  「一批多段改动只 `os.replace` 一次、失败整批不落盘」「删不掉的预设 / 解绑不到的阶段必须
  失败」「坏 TOML 不被覆盖」「读-改-写全程持锁」「缺失文件不被凭空新建」；
  `tests/test_repository_registry.py` 钉住第二个写入者已委托同一原语（无固定名临时文件）。
- 兼容性：`kc agent presets`、`kc agent doctor --lifecycle`、单次 `--preset/--model/
  --reasoning-effort`、旧 Agent-only 端点与 PRD 覆盖优先级均有契约测试（rv-3 段内 57 passed）。
- 未替你做的：`Human-Confirmed` 五项（PRD §9.2）按约定留空，需你在真实 Console 里点一遍。

## 证据与交付树的绑定

| 检查点 | 采集脚本 | 结果 | 绑定树 |
|---|---|---|---|
| rv-1 | `scripts/rv1_capture.sh` | `RV-1 RESULT: PASS` | 产物首部自带 `tree stamp`（本轮 `working_state_tree be8b0ae2…`） |
| rv-2 | `scripts/rv2_run.sh`（自拉起生产栈） | `RV-2 RESULT: PASS` + `RV-2 WRAPPER RESULT: PASS` + 真实 Playwright `7 passed`；负控另跑一次 `NEGATIVE-CONTROL RED-CONFIRMED 3/3` | 同上（正控与负控各一次，同一树） |
| rv-3 | `scripts/rv3_capture.sh` + `scripts/rv3_delete_integrity.sh` | `RV-3 RESULT: PASS` + `RV-3-DEL RESULT: PASS` | 同上 |
| 定向复验 | `scripts/rv1_final_tree.sh` | `RV-1-FINAL RESULT: PASS`（389 passed） | `head_commit b10a7e06`，`working_state_tree be8b0ae2…` |
| 全量测试 | `JUST_FULL_TEST_FLAGS=--no-testmon just test all`（`scripts/r5_just_test_all.sh`） | `R5-FULL-SUITE RESULT: PASS（3866 passed, 1 skipped）` → `rv-just-test-all.txt` | 同上 |

> **本轮（第七轮评审修复）之后**：代码侧改动集中在**唯一一份 TOML 落盘原语**与其调用方——
> 权限沿用目标现状（不再把 `config.toml` 写成 0600）、`<name>.lock` 跨进程写锁覆盖整个
> 读-改-写、`registry_editor` 改委托同一原语（不再用固定名 `<name>.tmp`）、多段保存收敛成
> 一次读-改-写、删不掉的预设 / 解绑不到的阶段改为 422 / `USAGE(2)`、坏 TOML 不再被覆盖。
> 另删除无生产调用者的 `build_fallback_candidate_specs`，文档与随包 skill 改指真实阶梯。
> rv-1 / rv-2（正控 + 负控）/ rv-3 / rv-3-DEL / rv-1-final 与全量测试均已在含这些改动的树上
> 整体重跑并全绿（上表）：真实 Playwright 7 passed、窄屏横向溢出 0px、全量
> `3866 passed, 1 skipped`（较第六轮 3848 多出的 18 条即本轮新增判别断言）。
> **每个 rv 产物现在首部都自带 `tree stamp · head_commit / head_tree / working_state_tree /
> working_tree_clean`**，不再只有 `rv-1-final-tree-reverification.txt` 一处；本轮
> `working_tree_clean=no`，runner 落交付 commit 后其 tree 应等于各产物打印的 `working_state_tree`。

## 已知限制（不粉饰）

1. **e2e 认证入口**：本 worktree 无 `PLAYWRIGHT_IDENTIFIER/PLAYWRIGHT_PASSWORD`，全套
   e2e 中 19 项因缺凭据 / registry 数据失败；已用 HEAD 基线对照确认与本次改动无关，
   `lifecycle-settings.spec.ts` 的 7 项在真实栈上全绿。
2. **模型目录不校验**：预设的 model / effort 只做「能否按已声明参数模板注入」的校验，
   不查 provider 目录、不验模型 ID 在线有效或账号权限（PRD 行为样例已声明该边界）。
3. **verifier-report.md 缺席**：`<prd-stem>.verifier-report.md` 由 runner 的独立 verifier
   出具，执行器不代写；本报告只覆盖执行器侧的 verification-plan 与 evidence-report。
