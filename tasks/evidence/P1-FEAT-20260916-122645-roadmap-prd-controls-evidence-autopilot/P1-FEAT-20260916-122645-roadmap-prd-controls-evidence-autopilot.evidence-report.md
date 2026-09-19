# 证据报告 · Roadmap 单 PRD 控制、归档证据与 Autopilot 自动推进

PRD：`tasks/archive/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot.md`
分支：`feat/roadmap-prd-controls-evidence-autopilot`（worktree `/Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot`）
基线代码树：`4d4dd12`（`git merge-base HEAD main`，即本 PRD 的开工基点）
执行时间：2026-09-20
执行器：CodeBuddy Code（会话内自证）+ 独立 verifier 复核

## 人审导航 / Human Review Navigation

本 PRD 有三个（组）人读呈递物，全部在**最终实现树**上采集，证据目录：
`tasks/evidence/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot/`

| # | 呈递物 | 绝对路径 | 打开命令 | 期望观察值 |
|---|---|---|---|---|
| rv-1 | 录屏：默认依赖图选中 → 统一详情 → 规范 start API | `/Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot/tasks/evidence/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot/rv-1-roadmap-single-start.webm` | `open "…/rv-1-roadmap-single-start.webm"` | 默认依赖图点节点后右侧出现**一个且仅一个**「开始此 PRD」；点击后按钮转「启动中…」；不再整页替换为 PRD 原文 |
| rv-2 | 录屏：Autopilot 开关 + 降级文案 | `…/rv-2-autopilot-control.webm` | `open "…/rv-2-autopilot-control.webm"` | 初始「Daemon 未运行」「自动合并未启用」；开关打开后「Daemon 运行中」，自动合并仍「未启用」 |
| rv-2 | 文本：写回前后 `.iar.toml` 逐行 diff + 真实 HTTP | `…/rv-2-config-diff.txt` | `open "…/rv-2-config-diff.txt"` | diff 只有一对 `enabled = false → true`；同级键 4 个、未知子表 1 个、注释 2 条全保留；GET→PATCH→GET 一致；未知仓库 400 |
| rv-3 | 截图：真实 console + 真实文件系统的「验收证据」标签页 | `…/rv-3-archived-evidence.png` | `open "…/rv-3-archived-evidence.png"` | 列出真实文件名与大小；`.hidden.md` / `scripts/oracle.py` / `escape.md` 均不出现 |
| rv-3 | 文本：路径攻击矩阵 + 负控 | `…/rv-3-path-attack-matrix.txt` | `open "…/rv-3-path-attack-matrix.txt"` | 10 个恶意 token 全部 400 且不泄露仓外内容（同表内 1 个合法 token 为 200）；fresh-state probe 5→6→5；空态给出解析位置；基线代码树 GET/PATCH 404 |

### rv-3 真实页面截图（就地嵌入）

![rv-3 归档证据页：真实 console + 真实文件系统](rv-3-archived-evidence.png)

> **本地图片**：该 PNG 是 2026-09-20 用 Playwright 对真实 `iar console`（临时 Git 仓，见 §2.2）采集的 1600×1200 截图；`tasks/evidence/**` 只把 `*.md` 纳入版本控制，因此在 GitHub 上此图显示为坏图，请在本地用下面的命令打开：
> `open "/Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot/tasks/evidence/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot/rv-3-archived-evidence.png"`

**执行器已交叉核对**：rv-1/rv-2 的录屏来自 `just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts`；rv-3 的截图来自独立脚本 `scripts/rv-3-archived-evidence-screenshot.sh`，不依赖 route stub；三条人读项的自动化等价断言见 §2。

---

## 0. 一句话结论

后端链条（受限 `.iar.toml` 写回、证据只读访问、Autopilot 状态聚合、API 契约、现有持续调度与双重合并门禁）与前端页面全部真实跑通：全量后端 **2306 passed**；E2E 两次运行合计 **12 passed**（新 spec 5 + 既有 PRD 原文 spec 3 + console 静态入口 spec 3 + auth setup 1）；`just lint --reuse` / `pre-commit --all-files` / `just console-sync` / `uv run mkdocs build --strict` 全部退出 0。rv-1 / rv-2 / rv-3 的人读呈递物已在最终实现树采集。

本轮修复了两处实跑才暴露的问题、以及两处独立 verifier 复核提出的问题（§4）：`agent_runner_roadmap.py` 中的重复 context loader、新 E2E spec 用 `check()` 驱动受控开关造成的假失败、`.iar.toml` 中 `enabled` 非布尔时 GET 漏成 500、以及超限文件被列进 manifest 却无法下载。

## 1. 自动化 oracle 结果（真实执行）

| Oracle | 真实入口 | 结果 | 退出码 | 证据 |
|---|---|---|---|---|
| rv-1 | `just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts tests/smoke/roadmap-prd-content.spec.ts` | PASS 9 passed | 0 | §2.1 / `rv-1-roadmap-single-start.webm` |
| rv-1（生产静态入口） | `just e2e tests/workflows/console-served-static.no-auth.spec.ts` | PASS 3 passed | 0 | §2.1 |
| rv-2（写回 + 读回） | `bash tasks/evidence/<stem>/scripts/rv-2-autopilot-config-diff.sh` | PASS 1 行 diff / 未知仓库 400 / 基线 GET+PATCH 404 | 0 | `rv-2-config-diff.txt` |
| rv-3（受限读取 + 攻击面） | `.venv/bin/python tasks/evidence/<stem>/scripts/rv-3-path-attack-matrix.py` | PASS 11 例中 10 例恶意 token 400 + 1 例合法 200，无泄露 | 0 | `rv-3-path-attack-matrix.txt` |
| rv-3（真实页面） | `bash tasks/evidence/<stem>/scripts/rv-3-archived-evidence-screenshot.sh` | PASS 截图 + 自检通过 | 0 | `rv-3-archived-evidence.png` |
| rv-4（持续调度 + 双门禁） | `uv run pytest -o addopts="" tests/test_roadmap_advance.py tests/test_roadmap_autopilot_settings.py -k 'autopilot or upstream or disabled' -v` | PASS 13 passed | 0 | §2.3 |
| rv-5（API / core / writer 契约） | `uv run pytest -o addopts="" tests/test_roadmap_api.py tests/test_roadmap_prd_evidence.py tests/test_repository_settings_editor.py -q`（PRD 的 `real_entry` 原样） | PASS 26 passed | 0 | §2.4 |
| rv-5（三个新增测试文件合计） | `uv run pytest -o addopts="" tests/test_roadmap_autopilot_settings.py tests/test_roadmap_prd_evidence.py tests/test_repository_settings_editor.py -q` | PASS 34 passed | 0 | §2.4 |
| rv-6（全仓质量） | `CI=true just test all`（强制全量、跳过 testmon 增量） | PASS 2306 passed in 79.11s | 0 | §3 |
| rv-6（复用/架构/行数） | `just lint --reuse` | PASS（5 个 hook 全过） | 0 | §3 |
| rv-6（全量 pre-commit） | `SKIP=check-test-flag uv run pre-commit run --all-files --show-diff-on-failure` | PASS（17 个 hook 全过） | 0 | §3 |
| rv-6（前端构建与静态同步） | `just console-sync` | PASS（13 个路由静态导出并同步） | 0 | §3 |
| rv-6（文档） | `uv run mkdocs build --strict` | PASS | 0 | §3 |

## 2. 关键观察值（原样摘录，不重建）

### 2.1 rv-1 · 三种视图共享统一详情与唯一启动入口

`just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts tests/smoke/roadmap-prd-content.spec.ts`
在最终实现树上的输出（节选，逐字）：

```text
  ✓  2 [chromium] › tests/smoke/roadmap-controls-evidence-autopilot.spec.ts:252:3 › … › rv-1 默认依赖图选中可启动 PRD 后走规范 start API (1.4s)
  ✓  4 [chromium] › tests/smoke/roadmap-controls-evidence-autopilot.spec.ts:282:3 › … › rv-1 被依赖阻塞的 PRD 不允许绕过依赖门禁 (612ms)
  ✓  5 [chromium] › tests/smoke/roadmap-controls-evidence-autopilot.spec.ts:296:3 › … › rv-1 三种视图共享同一详情与启动规则 (1.8s)
  ✓  8 [chromium] › tests/smoke/roadmap-controls-evidence-autopilot.spec.ts:316:3 › … › rv-3 已归档 PRD 的验收证据标签展示受限清单 (544ms)
  ✓  9 [chromium] › tests/smoke/roadmap-controls-evidence-autopilot.spec.ts:336:3 › … › rv-2 Autopilot 开关走 PATCH 并如实显示降级条件 (533ms)
  9 passed (9.6s)
```

（该次运行的 9 条 = 新 spec 5 条 + 既有 `roadmap-prd-content.spec.ts` 3 条 + `auth.setup.ts` 1 条。）

断言的是外部可观察量：唯一 start 请求的 URL 必须是
`/api/v1/agent-runner/roadmap/prds/<base64url(prd_path)>/start`，请求体含 repo_id；
被阻塞 PRD 的按钮 disabled 且 `captures.starts` 长度为 0；启动后页面重新拉取列表，
详情状态来自服务端。生产静态入口由 `console-served-static.no-auth.spec.ts`
（3 passed，含「直接刷新 `/app/roadmap` 不 404 且渲染标题」）覆盖。

### 2.2 rv-2 / rv-3 · 真实 console + 真实文件系统

`rv-2-config-diff.txt`（节选，完整见文件）：

```text
--- .iar.toml.before
+++ rv-repo/.iar.toml
@@ -3,7 +3,7 @@
 display_name = "rv repo"
 # 下面这个布尔键就是本 PRD 要写的唯一目标
 [agent_runner.autopilot]
-enabled = false
+enabled = true
 merge_method = "squash"
 require_verifier_pass = true
 auto_sign_off = false
```

```text
## GET before
{"repo_id":"rv-repo","enabled":false,"auto_merge_enabled":false,"daemon_running":false,"max_parallel":2,"config_source":".iar.toml","persisted_enabled":false}
## PATCH enabled=true
{"repo_id":"rv-repo","enabled":true,"auto_merge_enabled":false,"daemon_running":false,"max_parallel":2,"config_source":".iar.toml","persisted_enabled":true}
## GET after (fresh load)
{"repo_id":"rv-repo","enabled":true,...}
## PATCH negative control: unknown repo
{"detail":"仓库 'does-not-exist' 不存在或未启用。"}
HTTP 400
## 负控（基线代码树 4d4dd12，PYTHONPATH=/tmp/iar-rv3-baseline/src）
GET  /autopilot -> HTTP 404
PATCH /autopilot -> HTTP 404
```

`rv-3-path-attack-matrix.txt`（节选）：

```text
列出的文件名: ['P1-FEAT-20260101-rv3-demo.evidence-report.md', '...verification-plan.md', '...verifier-report.md', 'note.txt', 'screenshot.png']
数量: 5（磁盘允许集合应为 5，.hidden.md / scripts/ / escape.md 不在内）
- 合法 evidence report                   -> HTTP 200  泄露仓外内容: 否  # 证据报告 H1
- 路径穿越 ../.iar.toml                    -> HTTP 400  泄露仓外内容: 否  证据文件名不得包含路径分隔符。
- 隐藏文件 .hidden.md                      -> HTTP 400  泄露仓外内容: 否  隐藏文件不可作为验收证据访问。
- 子目录伪装 scripts/oracle.py              -> HTTP 400  泄露仓外内容: 否  证据文件名不得包含路径分隔符。
- 逃逸符号链接 escape.md                     -> HTTP 400  泄露仓外内容: 否  证据文件必须直接位于该 PRD 的证据目录内。
- 非法 base64 编码                         -> HTTP 400  泄露仓外内容: 否  非法的证据文件名编码。
新增前文件数: 5 → 新增 added-later.txt 后: 6（包含该文件） → 删除 note.txt 后: 5
空态: {"exists":false,"files":[],"evidence_dir":"tasks/evidence/P1-FEAT-20260101-empty"}
## 5. 负控
- GET .../prds/{path}/evidence       -> HTTP 404
- GET .../roadmap/autopilot          -> HTTP 404
```

负控用 `git worktree add --detach /tmp/iar-rv3-baseline 4d4dd12` 建基线树，脚本以
`PYTHONPATH=<baseline>/src` 让同一个 venv 的 `iar` 加载基线 `backend`，因此是真正的
「实现前 vs 实现后」对照。rv-2 的 400 负控同样是真实 HTTP。

### 2.3 rv-4 · 现有持续调度链与双重合并门禁

```text
tests/test_roadmap_advance.py::test_gate_disabled_skips_scheduling PASSED
tests/test_roadmap_autopilot_settings.py::test_upstream_merged_promotes_downstream_when_autopilot_enabled PASSED
tests/test_roadmap_autopilot_settings.py::test_autopilot_disabled_promotes_nothing PASSED
tests/test_roadmap_autopilot_settings.py::test_merge_queue_requires_both_switches PASSED
====================== 13 passed, 17 deselected in 0.21s =======================
```

`test_upstream_merged_promotes_downstream_when_autopilot_enabled`：临时仓 `.iar.toml`
经产品自身 `load_agent_runner_local_settings` 读出 `enabled=true`；真实
`advance_roadmap_queue` + fake GitHub/store 下上游合并后 `started == [downstream]`
且下游被打上 `agent/ready`；下游 PRD 显式声明 `Depends on: #1`，确保不是被
discovery 顺手捞起来的。`test_merge_queue_requires_both_switches` 在
`enabled=true` 且 `safety.auto_merge=false` 时得到 `(0, [])` 且零 `merge_pull_request`。

`roadmap_actions.py` / `run_agent_daemon.py` / `agent_runner_merge_queue.py` 在本次 diff
中**无改动**。

### 2.4 rv-5 · API / core / writer 契约

```text
# PRD §7 里 rv-5 的 real_entry 原样命令
uv run pytest -o addopts="" tests/test_roadmap_api.py tests/test_roadmap_prd_evidence.py \
  tests/test_repository_settings_editor.py -q
============================== 26 passed in 0.25s ==============================

# 三个新增测试文件合计（含 autopilot settings 契约）
uv run pytest -o addopts="" tests/test_roadmap_autopilot_settings.py \
  tests/test_roadmap_prd_evidence.py tests/test_repository_settings_editor.py -q
============================== 34 passed in 0.22s ==============================
```

覆盖成功、空态、非法仓库、非法路径（穿越 / 隐藏 / 子目录 / 符号链接 / 非法
base64）、写失败（缺文件 / 缺 `[agent_runner]` 段 / 非法 TOML 原文件不变）、
写后读回不一致拒绝、`enabled` 非布尔时返回 400（而非 500）、超限文件既不进
manifest 也无法读取、legacy 扁平证据目录语义不变。

## 3. rv-6 · 全仓门禁（真实执行，均为最终实现树）

```text
CI=true just test all                        → 2306 passed in 79.11s
just lint --reuse                            → jscpd / pylint-duplicate-code /
                                               check-architecture /
                                               check-guidelines-consistency /
                                               check-max-file-lines 全部 Passed
SKIP=check-test-flag uv run pre-commit
  run --all-files --show-diff-on-failure     → 17 个 hook 全部 Passed
just console-sync                            → 13 个路由静态导出并同步到
                                               src/backend/api/static/console/
uv run mkdocs build --strict                 → exit 0
```

架构红线定向检查（`rg`，在最终实现树执行）：

- `rg -n "tomlkit|os\.replace" src/backend/api src/backend/core/use_cases/roadmap_autopilot_settings.py src/backend/core/use_cases/roadmap_prd_evidence.py` → **无命中**（API 层不碰 TOML，core 只依赖窄端口）。
- `rg -n "_ROADMAP_CACHE" src/backend/api/routes` → 命中只有 list / start 路径与一行说明注释，**不含** autopilot / evidence 端点。
- `rg -n "handleStart|startRoadmapPrd|canStartRoadmapPrd" frontend-public` → 启动入口唯一：页面 `handleStart` → `lib/api/roadmap.ts::startRoadmapPrd` → canonical endpoint；规则函数 `canStartRoadmapPrd` 由卡片与详情共用。
- `rg -n 'tasks/evidence.*prd|evidence_dir.*/' src/backend/core/use_cases/roadmap_prd_evidence.py` → 无手写默认目录拼接（唯一命中是 docstring 与 `evidence_dir / artifact_name` 的合法拼接）。
- `rg -n "tomlkit|os\.replace" src/backend/infrastructure/config` → 命中 `repository_settings_editor.py`（本次新增，仓库级 `.iar.toml`）与 `registry_editor.py`（既有，全局 `config.toml` 的 repositories 子树）。**这是两个不同目标文件的 writer**，符合 PRD D-05/D-08；但 §9 Architecture Acceptance 的字面表述是「写回逻辑单处」，实际应为「每个目标文件单处」，已在 Final Reconciliation 中修正措辞。

## 4. 本轮修复（实跑才暴露的问题）

1. **重复 context loader**：`src/backend/api/routes/agent_runner_roadmap.py` 新增的
   `_load_fresh_contexts()` 与同文件既有 `_resolve_contexts()` 函数体完全相同。已删除
   新函数并复用 `_resolve_contexts`；`tests/test_roadmap_autopilot_settings.py` 的
   monkeypatch 目标同步改到 `_resolve_contexts`（定向与全量测试复跑通过）。
2. **受控开关驱动方式**：新 E2E spec 的 rv-2 用 `locator.check()` 驱动受控复选框，
   首轮实跑报 `Clicking the checkbox did not change its state`——`check()` 在点击后
   立刻校验 DOM 状态，而该开关的 `checked` 由**写后读回的异步响应**驱动。已改为
   `click()` + `expect(...).toBeChecked()`，复跑通过。
3. **GET 在 `enabled` 非布尔时漏成 500**（独立 verifier 提出，已复现）：`.iar.toml`
   写成 `enabled = "yes"` / `1` 时，受限端口的 `read_enabled` 抛
   `RepositorySettingsEditError`（`ValueError` 子类），而 `load_autopilot_state` 未做
   转换、路由只 catch `RoadmapAutopilotError`，于是 `GET /roadmap/autopilot` 返回 500，
   破坏 rv-5 的「稳定契约」要求。已在 `load_autopilot_state` 内把 `ValueError` 统一转成
   `RoadmapAutopilotError`，并新增
   `test_get_autopilot_rejects_non_bool_enabled` 断言返回 400 且 detail 提到「布尔」。
4. **超限文件被列进 manifest 却无法下载**（独立 verifier 提出）：`build_evidence_manifest`
   此前列出所有普通文件，而 artifact 端点对超过 10 MiB 的文件必然 400，页面因此给出
   一个必然失败的动作。已在 manifest 阶段跳过超限文件，并新增
   `test_oversize_file_is_excluded_from_manifest_and_rejected_by_artifact` 保证
   manifest 与 artifact 的「允许集合」一致。

## 5. 已知限制

- **不与 `P1-FEAT-20260918-110027-lifecycle-agent-matrix` 共用 TOML 原语（待该 PRD 合并时收敛）**：该 PRD 分支（`3326a7c`）另建了
  `src/backend/infrastructure/config/toml_section_editor.py`，其 docstring 声明
  「`config.toml` 与 `.iar.toml` 共用本模块」；本 PRD 的基线（`4d4dd12`）不含该文件，
  因此无法在实现期复用。两者**不能同时按当前形态合并**，否则仓库会出现两份
  `.iar.toml` round-trip 原语，违反 D-08。已在本 PR 描述中登记，需由后合并方
  rebase 并改为复用 `repository_settings_editor.py`（本 PR 定义的原语）。
- **`agent_runner_roadmap.py` 现有 464 非空行**：未到 `check_max_file_lines.py` 的 1000
  阈值，也低于 PRD §7 建议的 500 拆分界；本轮删掉一个重复 helper 后仍保持单文件。
  按 PRD §7 指引登记为待复核项：若后续 PRD 继续追加端点，应在同一 URL namespace 下
  按读取/设置子路由拆分。
- **E2E 的 API 边界**：rv-1/rv-2/rv-3 的页面行为验证使用 Playwright route 提供确定性
  `/roadmap/**` 响应（PRD rv-1 `mock_boundary` 已声明）；证据的**文件系统链**不在此
  mock 范围内，由 §2.2 的真实 console 脚本覆盖。

## 6. 变更清单（最终实现树）

后端：

- 新增 `src/backend/infrastructure/config/repository_settings_editor.py`：仓库级
  `.iar.toml` 的 tomlkit round-trip + 同目录临时文件 + 完整模型校验 + `os.replace`，
  只写 `[agent_runner.autopilot].enabled`。
- 新增 `src/backend/core/use_cases/roadmap_autopilot_settings.py`：状态聚合 + 受限写回 +
  写后 fresh load 校验；daemon 状态复用既有 process supervisor 记录。
- 新增 `src/backend/core/use_cases/roadmap_prd_evidence.py`：manifest 与 artifact 读取，
  目录解析一律走既有 `resolve_evidence_dir`，含 basename / containment / 符号链接 /
  大小校验。
- 端口 `IRepositoryAutopilotSettingsEditor` 定义在
  `src/backend/core/shared/interfaces/runner_console.py`；装配新增
  `create_repository_autopilot_settings_editor`（engines factories + core facade 转发）。
- `src/backend/api/routes/agent_runner_roadmap.py`：新增 `GET/PATCH /roadmap/autopilot`、
  `GET /roadmap/prds/{path}/evidence`、`GET /roadmap/prds/{path}/evidence/{token}`，均不
  复用 30 秒缓存；本轮删除重复的 `_load_fresh_contexts` 并复用 `_resolve_contexts`。

前端（`frontend-public`）：新增 `prd-detail.tsx`、`prd-evidence-view.tsx`、
`roadmap-autopilot-control.tsx`；`prd-card.tsx` 提出共享纯函数 `canStartRoadmapPrd`；
`prd-content-view.tsx` 的 `onBack` 改为可选；页面改为 master-detail（三视图共用同一
选择/详情/启动规则）；`lib/api/roadmap.ts` + `types.ts` 新增 DTO 与 wrapper。
`roadmap-graph.tsx` / `roadmap-timeline.tsx` / `roadmap-list.tsx` 未改动（既有
`onOpenContent` prop 已足够）。

测试与文档：新增 3 个测试文件（**34 条**：settings 12 / evidence 14 / settings editor 8）+ 1 个 E2E spec（5 条）；
`tests/smoke/roadmap-prd-content.spec.ts` 的「返回列表」断言改为 master-detail 关闭；
`docs/guides/agent-runner.md`、`docs/api/references.md` 已同步。

## 7. 复现方式

```bash
cd /Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot
export PATH="$PWD/.venv/bin:$PATH"

# rv-1 / rv-2 / rv-3 页面
just console-sync
just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts tests/smoke/roadmap-prd-content.spec.ts
just e2e tests/workflows/console-served-static.no-auth.spec.ts

# 基线 worktree（rv-2 与 rv-3 的真实负控都用它）
git worktree add --detach /tmp/iar-rv3-baseline 4d4dd12

# rv-2 配置写回（真实 console + 临时 Git 仓 + 基线 GET/PATCH 404 负控）
bash tasks/evidence/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot/scripts/rv-2-autopilot-config-diff.sh

# rv-3 攻击矩阵 + 真实页面截图
.venv/bin/python tasks/evidence/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot/scripts/rv-3-path-attack-matrix.py
bash tasks/evidence/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot/scripts/rv-3-archived-evidence-screenshot.sh

# rv-4 / rv-5 / rv-6
uv run pytest -o addopts="" tests/test_roadmap_advance.py tests/test_roadmap_autopilot_settings.py -k 'autopilot or upstream or disabled' -v
uv run pytest -o addopts="" tests/test_roadmap_api.py tests/test_roadmap_prd_evidence.py tests/test_repository_settings_editor.py -q
uv run pytest -o addopts="" tests/test_roadmap_autopilot_settings.py tests/test_roadmap_prd_evidence.py tests/test_repository_settings_editor.py -q
just lint --reuse && just lint --full && CI=true just test all && just console-sync && uv run mkdocs build --strict
```

RV 脚本位于 `tasks/evidence/<prd-stem>/scripts/`，按 Machine Contract 不进入代码 diff
（`tasks/evidence/**` 的 `.gitignore` 白名单只放行 `*.md`）。所有脚本用临时目录 +
覆盖 `HOME`，不写开发机真实的 `~/.iar` 与仓库 `.iar.toml`。
