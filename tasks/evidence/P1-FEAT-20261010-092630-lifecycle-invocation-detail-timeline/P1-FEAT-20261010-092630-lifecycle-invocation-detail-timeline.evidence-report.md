# 证据报告：生命周期时间线接入调用明细与展示去噪

> PRD：`tasks/archive/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md`（执行侧已交付、横幅 🧍 待人工验收；归档已由 runner 在交付时执行，PR 与独立 verifier 由 runner 在执行器门禁后完成，本目录下的 `….verifier-report.md` 由 runner 的独立 verifier 轮次写入）。
> 判据以 PRD §7.6 为唯一事实源；怎么跑见同目录 `….verification-plan.md`。
> 原始证据（截图、JSON、日志、harness 脚本）都在本目录，按仓库 `.gitignore` 只留本机、不进提交；下文引用的记录均从这些文件原样摘出。**本报告里内嵌的图片在 GitHub 上不渲染（本地证据），每张图都附了 `open` 命令。**

## 人审导航 / Human Review Navigation

你只需要看 PRD §9.1 的两项；需要你回答的是 §9.2 的 2 个 `Human-Confirmed` 空框，汇总在同目录 `human-review-checklist.md`（交互版 `human-review-checklist.html`）。一条命令打开审查页：

```bash
just prd review tasks/archive/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md
```

| # | 看什么 | 打开方式 | 逐项期望值 | 状态 |
|---|---|---|---|---|
| 1 | rv-1：真实 Console 的「执行过程」——执行会话分组、Agent 尝试展开的调用序列、去噪后的行；页面调用行与 SQLite 账本逐条一致 | 本报告「rv-1」一节；`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/rv-1-lifecycle-invocation.png"` | ① 两个执行会话各成组且标识可见（`rv-harness#273#session-a` / `-b`）<br>② 展开尝试后可见调用行：阶段「编写代码 / 运行验证」、结果「正常退出 / 异常退出 / 未闭合」、耗时「1s / 未记录」<br>③ `rv-1-page-vs-ledger.txt` 首行 `=5 -> timeline rows=3; HTTP rows=3; page rows=3`，末行 `PASS`<br>④ 「开始执行」行内含「已被领取」标记；「重试」是尝试行标记而非独立行；观测行徽章为「观测记录」，整页无「未开始」 | ✅ PASS（三档视口） |
| 2 | 目标形态可点击原型：Hub → 原型 → 五套场景 → 展开交互 → 返回 Hub | `uv run mkdocs serve` 后 <http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html>（Hub 入口 `/prototypes/hub.html`）；记录 `open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/prototype-roundtrip.json"` | ① `failureCount=0`<br>② 五套场景 `正常轨迹 / 失败与重试 / 观测写入失败 / 调用明细不可用 / 旧记录（无归属）` 全部走通（desktop + narrow）<br>③ 展开按钮 `ariaExpandedAfterOpen=true` / 关闭后 `false`<br>④ 整页零 `pageerror` | ✅ PASS |
| 3 | 负控：同一批断言在注入故障的副本上是红的 | 本报告「反例」一节 | ① rv-1：篡改 fresh read 的终态 → `FAIL: 第 2 行终态与账本不一致`<br>② rv-2/rv-3：注入单点故障 → 对应用例 `AssertionError`<br>③ rv-4：折叠组置空 → e2e `2 failed`<br>④ 原型：两处注入 → 2 / 4 项红 | ✅ 全部按设计变红 |
| 4 | 独立 verifier | `open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.verifier-report.md"`（runner 轮次写入后存在） | ① 首行 `PASS`<br>② 无 `BLOCKER` / `SECURITY` 小节<br>③ 本报告披露的三处限制被接受或给出可执行异议 | ⏳ 由 runner 的 verifier 轮次产出 |
| 5 | PR 与 CI | 本 PRD 的 PR 与 CI 由 runner 在交付门禁后创建/发布（Issue #273：<https://github.com/ZataZhang/keda/issues/273>）；发布后应带回 PR 链接与 CI 结果 | ① required gates 全绿<br>② PR 正文带「合并即验收」声明与唯一 PRD 路径 | ⏳ runner-owned |

**已替你核对过的内容**

- **真实入口只有 rv-1 一项。** rv-1 是真实 uvicorn + 真实静态 console + 真实 SQLite + 真实 Chromium（只把外部执行器换成确定性计划 fixture，PRD 允许的边界）。原型往返（`docs/prototypes/prd-lifecycle-observability.html`）与 rv-3/rv-4 的 DOM 断言都跑在注入 fixture 的层级上——前者是 `prototype` 保真度、后者 `mock_boundary` 已在 PRD oracle 里自认——**不得**读作真实入口验证；生产边界（真实 Console 页面 + 真实账本）只由 rv-1 支撑。
- **证据绑定的代码树分两次。** rv-1..rv-4 的原始日志（`rv-4-e2e.txt` 13:00 等）绑在修复前的树：`repo_head=051e0ec6`，且当时 spec 只有 7 个用例，未覆盖 4a94bdb0 新增的「账本里的每条调用都要么显示、要么被披露」。Pre-PR 评审发现该缺口后，在含评审修复的工作树上重跑，产出 `rv-3-rv-4-e2e-final-tree.txt`（9 passed = 8 用例 + setup，头部记录 `repo_head` 与 `code_paths_diff_sha256`），并重写 `rv-1-final-tree-binding.txt`。**旧日志保留不删**，其结论仅适用于各自记录的树。独立 verifier 的 PASS 绑在 `051e0ec6`（见 `verifier-response.txt` 首行 builder sha），其对抗项正是 4a94bdb0 所修的问题，因此修复后行为尚无独立复核，需由人工验收或下一轮 verifier 覆盖。
- **全量门禁全绿**：`just test all` → 3830 passed, 1 skipped；`just lint --reuse` / `just lint --full`、前端 typecheck/build、`mkdocs build --strict`、新旧 e2e spec 均通过。
- **三处基线事实与限制已披露**（见文末「披露与限制」）：基线 `just test all` 的 5 个失败已一并修复；前端 eslint 的 5 个基线错误不在本 PRD 范围；420px 视口横向溢出属既有布局。
- **没有触碰 CLI 表面**：`git diff --name-only 44a33d6b..HEAD -- src/backend/api/` 为空；`[iar-invocation-*]` 标记未变。

## 结论摘要

| 项 | 结论 | 关键证据 |
|---|---|---|
| rv-1（R2，真实入口） | PASS | `rv-1-page-vs-ledger.txt`、三档截图、`rv-1-harness-provenance.txt` |
| rv-2（R2，端点契约） | PASS（23 passed） | `rv-2-pytest.txt` |
| rv-3（R1，三类降级） | PASS（3 passed + 截断用例补跑 1 passed + e2e 4 用例 / 旧 spec 5 passed） | `rv-3-pytest.txt`、`rv-3-e2e.txt`、`rv-3-legacy-spec.txt`、`rv-3-*.png` |
| rv-4（R1，去噪与抽屉） | PASS（e2e 8 passed） | `e2e/rv-4-*.png` |
| 原型往返 | PASS（failureCount=0） | `prototype-roundtrip.json` |
| 反例 | 五类全部按设计变红 | `rv-1/rv-2/rv-3/rv-4-negative-control.txt`、`prototype-roundtrip-negative-control.txt` |
| 独立 verifier | ⏳ runner 轮次 | `….verifier-report.md`（待生成） |
| 人工验收 | 🧍 待人工 | `human-review-checklist.md` |

## rv-1：真实入口（真实 Console + 真实 SQLite + 真实浏览器）

命令（PRD §7.6 的 real_entry，harness 脚本在 `scripts/` 下）：

```bash
SKIP_CONSOLE_SYNC=1 bash tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/scripts/capture_real_console.sh
```

边界：隔离 `HOME` + `KEDACODE_CONFIG`/`IAR_CONFIG` 指向一次性配置（注册表里只有一个一次性仓库，脚本会硬校验解析出的仓库路径，隔离失效即失败）；真实 uvicorn 提供真实 FastAPI + 真实静态 console + 真实 SQLite；**外部执行器由确定性计划 fixture 代替**（PRD rv-1 允许的 mock 边界），不跑完整 runner 调度（那会建分支/推 PR，属共享状态变更）；GitHub 只做只读调用。

比对结论（`rv-1-page-vs-ledger.txt` 原样摘出）：

```text
ledger invocation events (fresh read)=5 -> timeline rows=3; HTTP rows=3; page rows=3
row 1 id=inv-39e91cb0b542 run=rv-harness#273#session-a attempt=1 phase=implementation outcome=ok (ledger-consistent=True) duration=1s
row 2 id=inv-9519ed35befc run=rv-harness#273#session-a attempt=1 phase=verification outcome=failed (ledger-consistent=True) duration=1s
row 3 id=inv-dbcc13a5a110 run=rv-harness#273#session-b attempt=2 phase=implementation outcome=unclosed (ledger-consistent=True) duration=未记录
PASS: 页面调用行与 SQLite 账本独立 fresh read 逐条一致（条数 / 会话与尝试归属 / 终态 / 耗时口径 / 执行器）
```

页面观测（`rv-1-page-rows.json`，桌面 1440）：`sessionGroupCount=2`、`attemptToggleCount=2`、`invocationRowCount=3`、`timelineContainsNotStarted=false`、`pageErrors=[]`。

桌面截图（真实页面、真实账本；local-only）：

![rv-1 桌面：会话分组 + 尝试展开调用明细](rv-1-lifecycle-invocation.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/rv-1-lifecycle-invocation.png"`

窄窗口（1100px，同一页面同一数据；local-only）：

![rv-1 1100px 窄窗口](rv-1-lifecycle-invocation-narrow-window.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/rv-1-lifecycle-invocation-narrow-window.png"`

420px（仅用于披露既有布局限制，**不作为**时间线窄屏证据；local-only）：

![rv-1 420px：多栏布局横向溢出（既有布局）](rv-1-lifecycle-invocation-narrow.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/rv-1-lifecycle-invocation-narrow.png"`

窄屏 420px 的观测 JSON（`rv-1-page-rows-narrow.json`）记录：`sessionGroupCount=2`、`attemptToggleCount=2`、`invocationRowCount=3`、`pageErrors=[]`，以及两处键盘激活回落 `attempt toggle #0/#1 pointer click intercepted; activated via keyboard`——窄视口下时间线区域被横向溢出的布局挤出可视区，展开改用 focus + Enter（页面既有可达路径，展开后 `aria-expanded=true` 经断言），这是自动化采集的适配，不是断言绕过。

## rv-2：端点契约与关联完整性

命令与结果（`rv-2-pytest.txt`）：

```text
uv run pytest -q tests/test_prd_lifecycle.py tests/test_prd_invocation_detail.py -k 'invocation or degrade'
23 passed, 28 deselected
```

覆盖：会话标识注入（有上下文才写、不覆盖调用方键、无上下文不写）、`invocations` 字段挂接与向后兼容（旧响应/旧事件无字段）、行字段与账本逐条一致（含 `unclosed` / `incomplete` 保持、模型三态、重试链）、`available` / `truncated` 语义、损坏 `detail_json` 降级为空字典。实现前的红跑与单点注入红跑见 `rv-2-red-before-implementation.txt`、`rv-2-red-injection-before-implementation.txt`。

## rv-3 / rv-4：降级披露与展示去噪

```text
# rv-3（rv-3-pytest.txt）
uv run pytest -q tests/test_prd_invocation_detail.py -k 'unavailable or truncated'
3 passed, 13 deselected
# 补跑被上面词干漏掉的截断用例（"truncated" 不匹配 "truncates"）
uv run pytest -q tests/test_prd_invocation_detail.py -k 'truncates'
1 passed, 15 deselected

# rv-3 页面披露聚焦重跑（rv-3-e2e.txt；4 个 rv-3 用例 + auth setup）
PLAYWRIGHT_IDENTIFIER=local-operator PLAYWRIGHT_PASSWORD=local-operator \
  just e2e tests/smoke/backlog-prd-lifecycle-invocation.spec.ts --grep rv-3   # 5 passed

# rv-4（新 e2e spec，8 passed，rv-4-e2e.txt）与旧 spec 兼容回归（5 passed，rv-3-legacy-spec.txt）
PLAYWRIGHT_IDENTIFIER=local-operator PLAYWRIGHT_PASSWORD=local-operator \
  just e2e tests/smoke/backlog-prd-lifecycle-invocation.spec.ts     # 8 passed
PLAYWRIGHT_IDENTIFIER=local-operator PLAYWRIGHT_PASSWORD=local-operator \
  just e2e tests/smoke/backlog-prd-lifecycle.spec.ts                # 5 passed
```

e2e 断言覆盖：同秒 `started`/`claimed` 合并为一行、同秒 `attempt`/`retry` 折叠为尝试行标记、观测事件徽章为「观测记录」且整页不出现「未开始」、三类缺口披露且其余区域照常渲染、旧响应缺 `invocations` 字段回落平铺、`aria-expanded` 键盘可达、未知事件类型与损坏 detail 不抛错。（e2e 的读端点由确定性 fixture 顶替，页面与浏览器真实，不参与真实 SQLite 穿越判定。）

![rv-4 同秒折叠与会话分组](e2e/rv-4-timeline-sessions-folded.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/e2e/rv-4-timeline-sessions-folded.png"`

![rv-4 尝试展开：调用行、未闭合与重试链](e2e/rv-4-invocation-rows-expanded.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/e2e/rv-4-invocation-rows-expanded.png"`

![rv-3 调用明细不可用披露](e2e/rv-3-invocations-unavailable.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/e2e/rv-3-invocations-unavailable.png"`

![rv-3 读取截断披露](e2e/rv-3-invocations-truncated.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/e2e/rv-3-invocations-truncated.png"`

![rv-3 旧响应无 invocations 字段：平铺 + 无归属披露](e2e/rv-3-legacy-flat-timeline.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/e2e/rv-3-legacy-flat-timeline.png"`

## 原型往返（Hub → 原型 → 五套场景 → 返回 Hub）

```text
node scripts/capture_prototype.cjs --base-url http://127.0.0.1:8001
{ "failureCount": 0, "failures": [] }
```

`prototype-roundtrip.json` 记录桌面 1440 与窄屏 420 两档：`hubCards=6`、五套场景全部走通、`ariaExpandedAfterOpen=true` / `AfterClose=false`、`notStartedBadges=0`、`foldedChips` 与 `neutralObservationBadges` 符合目标形态、零 `pageerror`。

![原型桌面往返](prototype-roundtrip-desktop.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/prototype-roundtrip-desktop.png"`

![原型窄屏往返（420px）](prototype-roundtrip-narrow.png)

`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/prototype-roundtrip-narrow.png"`

## 反例（negative control）

| 注入点 | 命令 | 结果 |
|---|---|---|
| rv-1：fresh read 副本第 2 条终态 `failed → ok` | `bash scripts/negative_control_rv1.sh` | 退出码 1，`FAIL: 第 2 行终态与账本不一致`（`rv-1-negative-control.txt`） |
| rv-2：`retry_of` 展示字段丢弃 | `bash scripts/negative_control_backend.sh rv-2` | 退出码 1，对应用例 `AssertionError`（`rv-2-negative-control.txt`） |
| rv-3：截断判定写死 `False` | `bash scripts/negative_control_backend.sh rv-3` | 退出码 1，`assert truncated_detail.truncated is True` 失败（`rv-3-negative-control.txt`） |
| rv-4：前端 `SAME_SECOND_MERGE_GROUPS` 置空 | `PLAYWRIGHT_IDENTIFIER=… bash scripts/negative_control_rv4.sh` | 退出码 1，e2e `2 failed, 6 passed`（`rv-4-negative-control.txt`） |
| 原型：折叠组置空 / 观测事件集合置空 | `bash scripts/negative_control_prototype.sh` | 两次红跑：2 项 / 4 项失败（`prototype-roundtrip-negative-control.txt`） |

所有注入脚本都用临时备份还原源文件（本环境不做 git 写操作）；每次还原后 `git status` 确认代码路径无残留改动。

## 门禁结果（最终树上）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `just test all` | `3830 passed, 1 skipped`（修复基线 5 个失败后的首次全量）；交付前在含全部交付改动的最终树状态上再跑一次：`3830 passed, 1 skipped in 312.57s` |
| 本地核心档 | `just test` | `3757 passed, 1 skipped, 36 deselected`（并刷新 flag） |
| 复用/重复/架构/行数 | `just lint --reuse` | 全 Passed（jscpd、pylint 重复码、架构方向、规范一致性、1000 行上限） |
| 全量 pre-commit | `just lint --full` | 通过（`check-test-flag` 有效） |
| 架构依赖 | `uv run python hooks/shared/check_architecture.py` | 「架构依赖方向全部合法，无违规」 |
| 前端类型 | `pnpm --dir frontend-public typecheck` | 通过 |
| 前端构建 | `just console-sync`（`pnpm build` + 静态同步） | 成功（10 条路由全部静态导出） |
| 前端 lint | `pnpm --dir frontend-public lint` | 5 errors / 103 warnings，**与基线 `44a33d6b` 完全相同**（在基线文件副本上复现同样 5 个 `set-state-in-effect`），新增文件零错误；CI 的 frontend job 不跑 eslint |
| 文档 | `uv run mkdocs build --strict` | 退出码 0 |
| e2e（新） | `just e2e tests/smoke/backlog-prd-lifecycle-invocation.spec.ts` | 8 passed |
| e2e（旧，兼容回归） | `just e2e tests/smoke/backlog-prd-lifecycle.spec.ts` | 5 passed（spec 未修改） |
| 人审清单自检 | `scripts/validate_human_review_checklist.cjs` | 3 张卡、4 张内嵌图全部加载、零 pageerror、可生成 Markdown（`human-review-checklist-validation.txt`） |
| 结构检查 | prd skill 的 `check_human_review_checklist.py` | OK（警告级检查无提示） |

## 最终树绑定

```text
# rv-1-harness-provenance.txt（采集时刻）
collected_at_utc=2026-10-11T05:02:17Z
repo_head=71f697aeabae74597abf0e2490afd9bb5ae8ead5
working_tree_diff_sha256=456519c8bae26d2b81765937c94b91b6c6bad32a0b19242283d102195ad1392a

# rv-1-final-tree-binding.txt（交付记录路径排除后）
code_paths_diff_sha256=e70640867a6eb1f906329a92c77c8967dea9da4bb7f82c05fe4aba0a6f4fa2ed
code_path_file_sha256:
  bfe925a6…  docs/guides/agent-runner.md
  182cd182…  tests/playwright-e2e/tests/smoke/backlog-prd-lifecycle-invocation.spec.ts
  71588319…  tests/test_cli_schema.py
  9e4a2852…  tests/test_kedacode_operator_skill.py
```

代码路径改动仅上述 4 个文件（+22/-2、+36、+4/-1、+15/-5），实现主体（`prd_invocation_detail.py`、DTO、前端组件与原型）包含在快照 `71f697ae` 中；`just test all`、e2e、harness 与反例都在该树状态上复跑。此后只写交付记录（PRD 与本目录 `.md`），不再改动代码路径。

## 披露与限制

1. **基线守卫修复**：开工时基线 `44a33d6b` 的 `just test all` 有 5 个失败（`kc lifecycle` 子命令上线时漏同步 argparse 门面守卫与随包 skill 的 9 份 references 计数）。本 PRD 一并修复（测试常量/白名单、路由关键词表与 docs 计数），`lifecycle` 与 `schema`/`tokens` 同列 `FACADE_EXEMPT_COMMANDS`（只读 Typer 原生命令，不经 argparse 执行后端）；CLI 表面零变化。详见 PRD Change Log。
2. **前端 lint 基线为红**：5 个 `set-state-in-effect` 错误位于本 PRD 未触及的文件（基线副本上复现同样 5 个），CI 不跑该 lint；因此前端验收口径为「无新增错误」。若要 eslint 全绿需另开 PRD。
3. **420px 视口**：真实 Console 多栏布局横向溢出（既有布局、非本 PRD 改动），时间线被挤出视口；该视口只作披露，窄屏可用性由 1100px 窄窗口截图与原型 420px 往返证明。420px 档的展开操作在自动化中用键盘激活完成（页面既有可达路径），已记入观测 JSON。
4. **rv-1 的 mock 边界**：外部执行器为确定性计划 fixture（两次会话、一次失败重试、一次未闭合调用）；runner 编排、FastAPI、core 组装、SQLite、Next.js 页面与浏览器交互全部真实。未跑完整 runner 调度（会建分支/推 PR，属共享状态变更，本次执行环境禁止）。
5. **e2e 保真度层级**：rv-3/rv-4 的 e2e 顶替读端点响应（确定性 fixture），页面与浏览器真实；它们不参与「真实 SQLite 穿越」判定，后者由 rv-1 harness 承担。
6. **runner-owned 项**：独立 verifier 轮次、PR 创建与 CI、归档均未在本轮完成——这是门禁设计，不是遗漏。

## 未完成事项（都要等 runner / 人工）

1. 独立 verifier 轮次（runner 创建 PR 前执行，结论写入 `….verifier-report.md`）。
2. PR 创建与 required gates（runner 提交后）。
3. 人工回答 §9.2 的 2 个 `Human-Confirmed` 空框，回复格式见 `human-review-checklist.md`。
