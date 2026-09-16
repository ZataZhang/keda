# Verifier Report: 修复控制台 e2e 与 roadmap 归档视图的既有失效项

- 对应 PRD：`tasks/pending/P1-BUG-20260914-171901-console-e2e-and-archive-view-defects.md`
- 实现提交：`80ef1bf`（PR #140，2026-09-14）；**审查时点最终树：`178ef77`（2026-09-16）**
- 审查人：独立 verifier（只读审查，与执行者分离）
- 审查方式：逐 oracle 独立复核 + 抽查 §9 全部 19 项验收项的证据支撑度

## 结论

**PASS — 可归档**（无代码级阻塞项）。

rv-1～rv-4 四条 oracle 全部成立，FR-1～FR-4 全部达标；审查中发现的问题**均为 PRD 文本级**，需在归档提交中一并处理（详见「必改项」），无一项要求改代码或重跑实现。

证据报告中曾被标记为「未闭环」的 rv-4 上游 push 项，**经本次独立复核已确认闭环**（见 rv-4 判定），不再构成阻塞。

---

## 逐 oracle 判定

### rv-1 就绪探针默认值可通 — **PASS**

| 复核项 | verifier 独立结果 |
|---|---|
| `run-with-just-stack.sh:208` | `export PLAYWRIGHT_HEALTH_URL="${PLAYWRIGHT_HEALTH_URL:-http://127.0.0.1:$BACKEND_PORT/api/v1/agent-runner/health}"` ✓ |
| `stack-control.mjs:68 / :78` | 默认值 `http://127.0.0.1:8000/api/v1/agent-runner/health`，`PLAYWRIGHT_HEALTH_URL` 覆盖能力保留 ✓ |
| `env.ts:115` | `?? \`${getApiBaseUrl()}/api/v1/agent-runner/health\`` ✓ |
| `.env.e2e.example:13` | 示例值同步为同一路径 ✓ |
| 前提「后端无根 `/health`」 | `rg '/health' src/backend/` 唯一命中 `routes/agent_runner.py:46 @router.get("/agent-runner/health")`（router 前缀 `/api/v1`）→ 根 `/health` 确不存在 ✓ |

三处默认值 + 示例文件全部指向真实存在的探针路径，且均有 override 能力（FR-1 完整）。executor 证据含正向运行日志（`GET /api/v1/agent-runner/health → 200`）与负向对照（改回 `/health` → `404 Not Found` → readiness 超时），构成因果闭环。

> 未独立复跑项：`just e2e` 全链路需起服并会写出 test-results/playwright-report 产物，受只读约束未执行；**以「默认值分布 + 后端路由唯一性 + 负向对照」三项替代取证**，结论等价。

### rv-2 控制台路由全部带 `/app/` 前缀 — **PASS（附文本口径问题）**

| 复核项 | verifier 独立结果 |
|---|---|
| 精确口径 `rg -n --pcre2 "goto\('/(?!(app/\|'))" tests/playwright-e2e/tests tests/playwright-e2e/page-objects` | **零命中** ✓ |
| `goto('/app` 逐文件计数 | roadmap.spec 2、roadmap-realistic 3、idea-inbox 2、pages 1、screenshot 2、console-pages.no-auth 5、`page-objects/AgentRunnerMonitorPage.ts` 1 —— 与 PRD §1 表格**逐项一致** ✓ |
| 路由真实性（verifier 补充取证） | spec 中出现的 goto 路径去重后为 `/app/{dashboard,ideas,processes,repositories,roadmap,stats}`，与权威来源 `app-sidebar.tsx:19-24` 的 href **完全对齐**；`/` 未受影响 ✓ |
| 变更是否波及前端应用代码 | `git diff --stat 80ef1bf^..80ef1bf -- frontend-public frontend-admin` → **空** ✓ |

**文本问题（必改 2）**：verification-plan.md:19 称修正后的 rg 口径「已在 PRD 记录」，但实测 PRD 全文（§1:100、§7:282/319、§7 rv-2 expected:388、§9:468）**仍为原口径** `goto\('/(?!app)`，且该原口径在最终树上有 5 处命中（`no-auth-example.no-auth.spec.ts` 3、`console-served-static.no-auth.spec.ts` 1、README.md 1）——全部是 FR-2 明文豁免的合法根路径 `goto('/')`。照 PRD 字面重跑会被误判为 FAIL。另 PRD §1 写「7 处文件、15 处 goto」，而该表自身逐行相加为 **16**，计数亦有偏差。

### rv-3 归档视图 200 且单条脏数据不打挂端点 — **PASS**

verifier 在**真实仓库数据**上直接调用 `scan_roadmap_prds` 复跑（不经 HTTP、不 mock、不改树）：

| 调用 | 结果 |
|---|---|
| `include_archived=True` | 117 条 prds，其中 **113 条 archived**，`skipped == []` ✓ |
| `include_archived=False` | 4 条 prds，`skipped == []`，响应结构仅新增授权的 `skipped` 字段 ✓ |
| 目标脏数据 PRD `20260626-093939` | **出现在 prds 中未被跳过** → 反证其 `Gate type` 已被修为合法值（`hard`，见 D-05）✓ |

补充核对：

- 容错位点：`roadmap_prd_scanner.py:276-282`，`except ValueError` 收窄捕获 → `skipped_prds` + `WARNING` 日志，位于 **core 用例内**，未上浮 api 层 ✓
- 单元测试 `tests/test_roadmap_prd_scanner.py:152 test_invalid_gate_type_prd_is_skipped_not_fatal`：断言脏数据进 skipped、健康 PRD 照常返回、reason 含 `Gate type` → 真实存在且通过 ✓
- `uv run pytest tests/test_roadmap_prd_scanner.py tests/test_roadmap_api.py -q -o addopts=""` → **13 passed** ✓
- 值域未放宽：`agent_runner_dependencies.py:128` 仍为 `("none","soft","hard")`，该文件未被本提交改动 ✓
- 返回类型适配完整性：`rg "scan_roadmap_prds\(" src/ tests/` 共 5 处生产调用点（`agent_runner_roadmap.py:139`、`roadmap_actions.py:215/374/579`）**全部已适配 `.prds`**，无遗漏调用点 ✓
- 归档 PRD 改动有说明：`tasks/archive/P1-FEAT-20260626-093939-...md:70-71` 改为 `hard`，同行写明原值、判据（取更严一侧）与来源 PRD §13 D-05 ✓
- fixture 已清理：`rg -l "ZZ-RV-FIXTURE" tasks/` 仅命中 3 个 md 的**文字记录**（PRD + 2 份证据文档），无数据文件残留 ✓

> 负向对照（stash 容错 → 500）为 executor 取证，含可复现步骤；因 `git stash` 属 git 写操作，verifier 未复跑，采信并记录。verifier 侧以「整数据表 skipped==[] 且目标 PRD 未被跳过」作了正向侧的等价交叉验证。

### rv-4 共享改动不被 `just sync-template` 回滚 — **PASS（且强于证据报告所述，未闭环项已消解）**

| 复核项 | verifier 独立结果 |
|---|---|
| `_is_upstream_owned` 所有权登记 | `sync_template.sh:383-386` 已为 `scripts/shared/e2e/run-with-just-stack.sh`、`tests/playwright-e2e/scripts/stack-control.mjs`、`tests/playwright-e2e/.env.e2e.example`、`tests/playwright-e2e/README.md` 登记项目所有权（`return 1`），均排在 `scripts/shared/*` glob **之前**（case 取首个匹配），注释写明判据 ✓ |
| `./scripts/sync_template.sh --list`（verifier 实跑） | **`✨ Everything is up to date with the template.`（0 条候选）** —— 优于 executor 报告的「残留 2 条」 |
| 「待人工 push 上游合入」是否闭环 | 模板仓库 `~/code/zata_code_template` 中 commit `289ac16 fix(sync): mark keda e2e health-default files as project-owned` 存在，且 `git branch -r --contains 289ac16` 命中远端 **`zata/main`**（`https://github.com/ZataZhang/zata-codes-template.git`）→ **已推送并入上游 main，该项已闭环，不构成阻塞** |
| 真实同步冲击后的存活证明 | 后续提交 `d953a49 chore(template): sync 9 upstream entries, including long-held-back check_architecture.py`（含 `hooks/shared/check_architecture.py` 等）**未触碰上述 4 个受保护文件**，且 HEAD 上三处健康探针默认值仍为 `/api/v1/agent-runner/health` —— 所有权登记在**一次真实 sync 之后**确实保住了缺陷 A 的修复 ✓ |
| 守卫测试 | `uv run pytest tests/guards/shared/test_sync_template.py -q -o addopts="" -p no:cacheprovider` → **19 passed** ✓ |

> 说明：`--list` 路径审查确认其为只读分支（`LIST_ONLY_MODE` 在 Phase 2 写入前 `exit 0`，所有 `mkdir/cp` 均在 1318 行之后或仅在 `--skill` 模式调用），实跑前后 `git status --porcelain` 无变化。

---

## FR 抽查

- **FR-1** ✓ 四处默认值统一指向 `/api/v1/agent-runner/health`，override 能力保留。
- **FR-2** ✓ 7 个文件 16 处 goto 全部带 `app/` 段；`/`、`/login` 未受影响。
- **FR-3** ✓ 单条容错（跳过 + WARNING + `skipped[prd_path, reason]`）+ 历史脏数据修正为 `hard`；真实数据复跑 `skipped==[]`。
- **FR-4** ✓ 4 个文件登记项目所有权；`--list` 零候选；sync 实证未回滚。

## §9 Acceptance Checklist 19 项抽查结果

| # | 分组 | 条目（摘要） | 判定 |
|---|---|---|---|
| 1 | Human | rv-3 注入脏数据仍 200、进 skipped、原因可查 | ✓ 有_unit test + executor 五步 curl |
| 2 | Human | rv-3 负向对照实跑记录 | ✓ 有记录（stash→500），verifier 未复跑（git 写限制） |
| 3 | Human | rv-4 sync 清单不含本次共享文件 | ✓ verifier 实跑，0 候选 |
| 4 | Architecture | 四层方向未破，容错在 core、api 无新增 try/except | ✓ diff 实证 |
| 5 | Architecture | 公共 Python API 中文 Google Style docstring | ✓ 人工核验 3 处（ruff 二进制在当前 env 缺失，未机器复跑，见限制） |
| 6 | Architecture | 值域仍 none/soft/hard | ✓ |
| 7 | Behavior | `include_archived=false` 行为一致 | ✓（数量因期间其它 PRD 归档自然变化，非回归） |
| 8 | Behavior | `true` 返回 200 且含 archived | ✓ 113 条 archived |
| 9 | Behavior | Gate type 已改合法值并附说明 | ✓ `hard` + 同行判据 |
| 10 | Frontend/Test | rv-1 通过 | ✓ |
| 11 | Frontend/Test | rv-2 通过 + rg 无命中 | ⚠ 实质通过，但 PRD 内 rg 口径未更新（必改 2） |
| 12 | Frontend/Test | 缺陷 B spec 不再 404 | ✓ goto 路径与 `app-sidebar.tsx:19-24` href 逐一对齐 + executor 真实栈日志 |
| 13 | Frontend/Test | frontend-admin 无改动 | ✓ diff 为空 |
| 14 | Docs | e2e README 补 `/app/` 说明 | ✓ README:43 |
| 15 | Docs | agent-runner.md 无前缀路由 + mkdocs strict | ✓ 无命中（mkdocs 未复跑，见限制） |
| 16 | Validation | rv-1~rv-4 全通过 | ✓ |
| 17 | Validation | `just lint --full` / `just test` 全绿 | ⚠ 采信 executor 报告；verifier 以等价子集替代（见限制） |
| 18 | Validation | ZZ-RV-FIXTURE 已删 | ✓ 仅 md 文字记录 |
| 19 | Delivery | 三项缺陷全落地、无遗留 | ⚠ 代码层面成立；**monitor 标题断言遗留项未落 PRD**（必改 3） |

---

## 发现的问题

### 必改项（均为 PRD 文本级，需在归档提交中一并处理；不阻塞代码实现）

1. **§9 十九个复选框全部未勾选**（`rg -c "^- \[x\]"` = 0）。`docs/guides/prd-standard.md:59` 要求「归档前……勾完 Acceptance Checklist」，且 `hooks/shared/check_prd_acceptance_checklist.py` 的 pre-commit 门禁会拦截未勾项。归档动作需由执行方按本报告逐项勾选。
2. **rv-2 判据口径与 PRD 正文不一致**：全文 5 处仍写 `goto\('/(?!app)`（该口径在最终树上仍有 5 处合法根路径命中），而实际采用并在 plan 中声明「已在 PRD 记录」的精确口径 `goto\('/(?!(app/|'))` 在 PRD 中并无记录。另 §1「15 处 goto」与表格逐行相加的 **16** 不符。→ 归档前请同步这 5 处口径与计数。
3. **「实现期发现 2：agent-runner-monitor 标题断言失效」未落 PRD**：该问题只存在于证据报告，**PRD 的 Change Impact Tree、§11 Non-Goals、§12 Follow-ups 均无记载**。verifier 已核实该缺陷属实且确为范围外（`rg "Agent Runner Monitor" frontend-public frontend-admin` 无真实存在，仅 `lib/api/types.ts` 有一段注释碎片；该 spec 不在缺陷 B 清单内，其 goto 已修）。结论：**不构成对三项缺陷验收的阻塞**，但按 §7 Executor Drift Guard 与 §9 第 19 项「无遗留项／已另立 PRD」的要求，归档前应至少在 §12 Follow-ups 记一笔「该 spec 断言的旧版 UI 文案已不存在，需独立 PRD 处理」，否则 PRD 会带着一条未披露的已知失败用例进归档。
4. **PRD 存在未提交的工作区改动**：`git status` 显示该文件有 3 行未入库修改（§5 新增「Frontend impact：No frontend impact」段）。归档提交请勿遗漏。

### Notes（不要求修）

- N1：`D-02` 备注「同一改动已提交模板仓库本地 clone（289ac16），push 合入后闭环」现已过期——实测已推送入上游 `zata/main`，`--list` 亦为 0 候选。建议在归档时把该句改为已闭环状态。
- N2：证据包只有 2 个 md，**原始产物（rv-1 日志、rv-3 curl、rv-4 sync 清单）按 plan 声明留本地、未入库**；而同仓 `tasks/evidence/P1-REFACTOR-20260705-210702-file-line-split-seven-files/` 有提交原始 `.log/.txt` 的先例。归档后他人无法复核原始证据，建议至少补提交关键原始日志（或在本目录显式说明存放位置）。
- N3：`tests/playwright-e2e/README.md:174` 的远端环境示例仍写 `PLAYWRIGHT_HEALTH_URL=https://your-api.example.com/health`。那是对第三方 API 的 override 示例、不是本仓默认值，语义可接受，但与缺陷 A 同类，可选一并提示。
- N4：§7 rv-3 的 incl_archived 计数（executor 记 8 pending / 118 总数）与本次复跑（4 / 117）不同，属期间其它 PRD 被归档的自然数据漂移，非回归。

---

## 审查限制（诚实陈述）

受只读约束 / 环境限制，**未**由 verifier 亲自复跑：

- `just e2e` 全链路（会起服并写出 test-results / playwright-report 产物）→ 以默认值分布 + 后端路由唯一性 + 负向对照日志替代。
- `just test` 全量、`just lint --full`、`just lint --reuse`、`uv run mkdocs build --strict`（都将写缓存 / `.testmon` / `site/`）→ 以等价子集替代：`pytest tests/test_roadmap_prd_scanner.py tests/test_roadmap_api.py`（13 passed）、`pytest tests/guards/shared/test_sync_template.py`（19 passed）、真实数据扫描复跑、ruff 规则的**人工**核验（注意：当前 uv 环境未安装 `ruff` 二进制，机器化 docstring 检查无法执行，故第 5 项以人工核验为准）。

以上替代取证足以支撑对 rv-1～rv-4 与 FR-1～FR-4 的判定；第 17 项验收项目的结论采信 executor 报告。

---

## verifier 实际执行的复核命令清单

```text
git log --oneline -3 ; git status --porcelain
rg -n "PLAYWRIGHT_HEALTH_URL" scripts/shared/e2e/run-with-just-stack.sh tests/playwright-e2e/support/env.ts tests/playwright-e2e/scripts/stack-control.mjs
rg -n --pcre2 "goto\('/(?!(app/|'))" tests/playwright-e2e/tests tests/playwright-e2e/page-objects
rg -c --pcre2 "goto\('/app" tests/playwright-e2e/tests tests/playwright-e2e/page-objects   # 及恰好不错的原口径 rg -c --pcre2 "goto\('/(?!app)"
rg -n "except ValueError|skipped_prds|RoadmapSkippedPrd|skipped" src/backend/core/use_cases/roadmap_prd_scanner.py
rg -n "Gate type" tasks/archive/P1-FEAT-20260626-093939-agent-runner-session-persistence.md
git diff --stat 80ef1bf^..80ef1bf -- frontend-public frontend-admin
git show --stat 80ef1bf
git show 80ef1bf -- src/backend/core/use_cases/roadmap_actions.py
rg -n "_is_upstream_owned" -A 30 scripts/shared/template/sync_template.sh
/usr/bin/env_less ./scripts/sync_template.sh --list        # 只读分支，跑前后 git status 无变化
cd ~/code/zata_code_template && git log --oneline -1 289ac16 && git branch -r --contains 289ac16 && git remote -v
uv run pytest tests/test_roadmap_prd_scanner.py tests/test_roadmap_api.py -q -o addopts=""
uv run pytest tests/guards/shared/test_sync_template.py -q -o addopts="" -p no:cacheprovider
uv run python -c "... scan_roadmap_prds(Path('.').resolve(), include_archived=True/False) ..."   # 真实仓库数据
rg -n "none|soft|hard" src/backend/core/use_cases/agent_runner_dependencies.py
rg -n "scan_roadmap_prds\(" src/ tests/
rg -n '"\(/api/v1\)\?/health' src/backend/ ; rg -n "/health" src/backend/api/routes/*.py
rg -n 'href: "/app' frontend-public/components/layout/app-sidebar.tsx
rg -rn "Agent Runner Monitor" frontend-public frontend-admin
rg -l "ZZ-RV-FIXTURE" tasks/
rg -c "^- \[ \]|^- \[x\]" / rg -c "^- \[x\]" tasks/pending/P1-BUG-...md
git diff -- tasks/pending/P1-BUG-20260914-171901-console-e2e-and-archive-view-defects.md
```

全程未执行任何 git 写操作、未改动仓库中除本文件外的任何文件。
