# Verifier Report: api → engines 直连迁移至 core 编排层并恢复架构门禁

对应 PRD：`tasks/pending/P1-REFACTOR-20260703-184226-api-engines-layer-migration.md`
审查人：独立 verifier（只读审查，与执行者分离）
审查时点：2026-09-16，审查树 main `d953a49`（`git status` 仅有 PRD 与另一 pending PRD 的文档改动，无源码改动被核实）
实现提交：`fea15af`（PR #141）；模板同步提交：`d953a49`

## 结论

**PASS**（附「归档前必改」3 项 + 建议跟进 3 项，均为文档/证据口径问题，不改变 5 个 oracle 的判定）

判定依据：rv-1 ~ rv-5 全部由 verifier 亲自复跑复现成功，PRD §9 五项最小开挖读作均落到可执行证据；
未发现「证据不能证明验收项」的致命缺口，也未发现用静态扫描冒充行为验证之外的隐瞒
（唯一弱 oracle 是 rv-5，其「调用顺序不变」本质上靠 diff 审阅支撑，见 Note 3）。

---

## 一、逐 oracle 判定

### rv-1 · 架构检查严格态通过 + api → engines 清零 —— **PASS**

verifier 自跑（非引用执行者产物）：

| 命令 | 结果 |
|---|---|
| `rg -n "from backend\.engines" src/backend/api/` | 无输出，exit 1（0 命中）|
| `uv run --no-sync python hooks/shared/check_architecture.py` | 扫描 241 文件，0 违规，exit 0 |
| `uv run pre-commit run check-architecture --all-files` | `Check architecture layer dependencies................Passed` |
| 基线对照：`rg -N "from backend\.engines" <baseline@ec621a4>/src/backend/api/ \| wc -l` | **44 行 / 20 文件** —— 与 PRD §1 声明的 44 处、20 文件完全一致，计数量级属实 |

- `FORBIDDEN_IMPORTS["api"]` 现状为 `["infrastructure", "engines", "composition"]`（`hooks/shared/check_architecture.py:42`）。
  相对 PRD FR-2 字面的 `["infrastructure", "engines"]` 是**超集**：`d953a49` 同步上游版时带回 composition root 规则。
  对 engines 的禁止未被削弱，FR-2 实质成立；建议归档前把 FR-2 措辞改为「至少包含 engines」（见必改项 3）。
- 负向对照（`rv-1.negative.txt`）：向 `cli.py:295` 加回 `from backend.engines.agent_runner.factory import ...` 后，
  检查器报 `[backend/api] → [engines] ... exit=1`。该行为与我对检查器源码的阅读一致
  （`hooks/shared/check_architecture.py:151` 的 `_extract_imported_modules` 同时覆盖 `ast.Import` 与 `ast.ImportFrom`），
  负向对照确实能判负。

### rv-2 · core facade 覆盖 15 个 engines 能力且无重复职责 —— **PASS**（证据产物不完整，见必改项 1）

verifier 用 AST 做了一次完整的符号级盘点，口径强于执行者的 `rg`：

1. **基线侧**：解析 `ec621a4` 的 `api/` 全部 `.py`，取所有 `from backend.engines.agent_runner*` 的符号
   → **20 个文件、15 个 engines 模块、120 个符号**。
2. **当前侧**：逐个基线文件比对同名符号是否仍被 import → **120 个符号中 118 个原样改打 `core/use_cases/agent_runner_*`**；
   唯一差异是 `cli_parsed_commands/container.py` 的 `ContainerAuthController` / `ContainerOpsController`
   被 `create_default_container_auth_importer()` / `create_default_container_ops_controller()` 取代；
   读 `src/backend/core/use_cases/agent_runner_container.py:239-256` 确认这两个工厂**返回的就是这两个类的无参实例**
   —— 语义等价，非能力丢失。
3. **15 个模块的落点（verifier 自绘）**：

| 基线 engines 模块 | 当前入口 |
|---|---|
| `factory`（含原 `factories` 的 `build_app_config*` re-export）| `core/use_cases/agent_runner_factory.py` |
| `repository_local` | `agent_runner_repository_local.py` |
| `takeover` + `takeover_interactive` | `agent_runner_takeover.py`（两个模块均绑定）|
| `failure_resolver` | `agent_runner_failure_resolver.py` |
| `persistence.loop_state_json` | `agent_runner_loop_state.py` |
| `worktree_cli` | `agent_runner_worktree_cli.py` |
| `workflow_install` + `remote_template_skills` | `agent_runner_init_assets.py`（两个模块均绑定）|
| `output_protocols` | `agent_runner_output_protocols.py` |
| `container_auth` / `container_ops` | 并入既有 `agent_runner_container.py` |
| `live_terminal` / `runner_live_view`（+ 隐藏共用依赖 `live_panels`）| git rename 迁入 `src/backend/api/agent_runner_views/`（FR-4 成立）|

共 **12 个** engines 模块经 core facade 运行时绑定，`factories` 经 factory re-export，2 个呈现模块迁归 api —— 15 个全覆盖，无遗漏。

- 执行期偏差 D-05（facade 用 `importlib.import_module` 而非静态 import）有仓内先例且披露充分：
  `src/backend/core/agent/memory/_composition.py:70/82/94` 同样用运行时解析绑定下层；
  权威规则 `docs/ai-standards/architecture.md:50` 明确「core 不得导入 engines」，静态写法确会被挂，**故该实现是唯一静态合规路径**这一理由成立。
- `rg -n "from backend\.engines" src/backend/core/` → 0 行（verifier 复核），§9 Dependency Acceptance 的口径成立。

### rv-3 · iar CLI 行为零变化 —— **PASS**（verifier 自行扩到全量子命令）

- 基线与当前确实是两棵不同的树（独立 provenance 探针）：
  `PYTHONPATH=/tmp/keda-baseline-api-engines/src` 下 `cli_typer_runner.__file__` =
  `/tmp/keda-baseline-api-engines/src/backend/api/cli_typer_runner.py`；当前为 `/Users/zata/code/keda/src/...`。排除恒真。
- 执行者的三条命令全部复现：`iar --help`(79 行) / `iar run --help`(27 行) / `iar daemon --help`(38 行) diff 为空、exit 0 = 0。
- **verifier 加强取证**：把 `iar --help` 解析出的全部 **24 个子命令**（agent/ask/blocked-continue/completion/console/container/
  daemon/deliberate/init/issue/labels/logs/loop/loop-daemon/recover/registry/repl/review/review-daemon/roadmap/run/
  takeover/workflow/worktree）逐一做 before/after diff → **24/24 diff 为空且退出码一致**。
  这比只测 3 条更贴近 FR-6「全部子命令逐字节一致」，建议把这条补进证据报告。
- 负向对照（`rv-3.negative.diff.txt`）显示改 docstring 后 diff 非空 → 判据能判负，空 diff 非恒真。
- 限制（PRD 自身已备案）：只到 `--help` 级，未实跑连 LLM/GitHub 的真实 `iar run`；§7.6 Failure triage 已把这一档标为
  opt-in / post-merge fallback，配合全量 2091 passed，我认为可接受，但应保留该披露，不要升级为「端到端运行行为已验证」。

### rv-4 · HTTP 路由契约零变化 —— **PASS**（负向对照未执行）

- `uv run pytest tests/ -q -k "agent_runner" -o addopts=""` → **906 passed, 1185 deselected**（verifier 复跑，与 `rv-4.pytest-agent-runner.log` 一致）。
- 缺口：`verification-plan.md` 明确写「负向对照 … 本次不额外破坏代码」，即 rv-4 的 negative_control **未实际执行**，
  无法证明 `-k agent_runner` 这 906 项真的覆盖 `routes/agent_runner*` 的响应契约。
  缓解：verifier 自跑全量 `uv run pytest tests/ -q -o addopts=""` → **2091 passed**；且 `git show fea15af -- src/backend/api/`
  逐-file 审阅显示改动仅 import 行与 container 的两次等价替换。故不阻塞，但归档时应把「负向对照未执行」写在明处（已写在 verification plan，PRD §9 也应提一句）。

### rv-5 · 迁移不改变 daemon 并发调用顺序 —— **PASS**（oracle 本身偏弱）

- `uv run pytest tests/ -q -k "daemon or loop or concurrent" -o addopts=""` → **146 passed, 1945 deselected**（verifier 复跑一致）。
- 诚实性说明：`-k daemon/loop/concurrent` 只有在测试本身断言调用顺序时才能发现顺序变化；现有 146 项主要覆盖锁/并发语义。
  **真正支撑 FR-8 的是迁移 diff 的性质**：审阅 `git show fea15af -- src/backend/api/` 后确认 api 侧改动是纯 import 落点替换
  （外加 container 的两处「直接构造类 → core 默认装配工厂」等价替换，返回值、调用前后顺序未变）。
  建议在 §9 表达为「diff 审阅 + 146 passed」而非「测试证明调用顺序不变」（见建议跟进 2）。
- rv-5 亦无负向对照产物，同 rv-4 处理。

### 门禁与包络

| 项 | verifier 复跑结果 |
|---|---|
| 架构 hook（真实 pre-commit 路径）| Passed |
| 全量 pytest | **2091 passed in 75s** |
| `rg "过渡期\|放宽" CLAUDE.md / hooks/shared/check_architecture.py / docs/architecture/system-design.md / docs/ai-standards/architecture.md` | 0 命中（exit 1）|
| `CLAUDE.md:40-41` | 「api/ 只可导入 core/，不得直接导入 engines/ 或 infrastructure/」—— FR-5 成立，且与两份权威文档一致 |
| `rg -n "importlib\|__import__\|getattr.*engines\|backend\.engines" src/backend/api/` | 仅 2 处无关既有命中（`importlib.resources.files`、`importlib.metadata.version`）+ 1 处「声明不直连 engines」的 docstring —— §9「无动态绕过」成立 |

---

## 二、发现的问题

### 归档前必改（不改代码，只改文档/证据口径）

1. **证据报告 rv-2 段落与代码不符（事实性错述）**。
   `evidence-report.md` 第 57 行把 `agent_runner_monitor` 注为「原 live_terminal / runner_live_view 呈现能力」。
   实际 `agent_runner_monitor` 是**迁移前就存在**的 core 监控用例（`build_issue_snapshot` / `detect_anomalies` / `build_overview`），
   `git show fea15af -- src/backend/api/routes/agent_runner.py` 显示它对 monitor 的 import 只是按 ruff 规则重排位置，内容未变。
   呈现能力的真正落点是 `src/backend/api/agent_runner_views/`（`live_terminal.py` / `runner_live_view.py` / `live_panels.py`），
   由 `cli_parsed_commands/{agent,runner}.py` 直接 import，**不经任何 core facade**。请修正该行与计数表，否则归档后审查者会被带偏到错误文件。

2. **rv-2 证据产物不完整（仅列出 5 处 importlib 绑定）**。
   `rv-2.core-facade-coverage.txt` 用单行正则 `import_module\("backend\.engines[^"]*"\)` 扫描，漏掉所有**跨行调用**，
   因此只列出 5 个绑定（factory / takeover / worktree_cli / container_auth / container_ops）并据此给出「去重后 5 个目标模块」。
   verifier 用 AST/多行正则复扫得到 **12 个目标模块、13 个绑定点**（含 repository_local、failure_resolver、output_protocols、
   init_assets ×2、loop_state、takeover_interactive）。结论方向不变，但证据文件应当换成上表的完整清单。

3. **PRD 顶部横幅与 §9 已与当前仓库状态矛盾**。
   PRD 第 6-17 行的 2026-07-31 横幅仍写「本 PRD 现在卡着一次模板同步」「check_architecture.py 被暂缓同步，keda 继续使用带放宽的本地版本」，
   第 26 行仍写「截至本次更新 … 仍为本地放宽版（第 3 次 hold back 模板同步）」。但 `d953a49` 已完成同步
   （commit message：「check_architecture.py 解除 hold back … 首次零漂移」），当前文件即上游版（含 composition root 规则）。
   归档前必须改写/删除这两处，否则归档文档会留下与代码相反的陈述。同一批条款里 FR-2 的 `["infrastructure", "engines"]`
   也应改成「至少包含 `engines`（现为 `["infrastructure", "engines", "composition"]`）」。

### 建议跟进（不阻塞）

1. **ast 门禁挡不住运行时绑定**。检查器只做 AST，`importlib.import_module("backend.engines...")` 对它是隐形的；
   本 PRD 用它让 core facade 合法地拿到 engines，意味着同样的手法也能让未来的 `api/` 代码隐性回搭 engines。
   建议后续 PRD 跟进：把 `import_module("backend.<layer>.…")` 字符串纳入 `check_architecture.py` 的扫描，或落地 composition root 目录。
2. **rv-5 的证据表述过强**。「146 passed」证明的是 daemon/loop/并发语义仍绿，调用顺序不变主要由 diff 审阅支撑，建议措辞对齐 reality。
3. **PRD §9 第 5 项测试数据已过期**：写的是 `just test all` 2084 passed，当前树（含 `d953a49` 新增的 `test_prd_status.py` 等）实测 **2091 passed**。
   归档时更新数字或注明测于哪个 commit。
4. **D-05 措辞需微调**：它称「绑定时机 … 与迁移前完全一致」，对 5 处模块级绑定成立，但 `agent_runner_container.py:245/255`
   是**函数内**绑定，导入时机从 CLI 模块加载期推迟到命令执行期（仅影响失败暴露时机，无功能影响），宜如实标注。
5. **证据报告里一段不可复现的引用**：报告第 28-30 行引 `rg -n '"api":' hooks/shared/check_architecture.py` 输出为
   `38:    "api": ["infrastructure", "engines"],     # 严格态已恢复`。当前 HEAD（第 42 行）与 HEAD~1（第 38 行）两版都**没有**
   这个尾部注释。属排版性失真，建议改成实际输出或删掉注释。

---

## 三、验收清单（§9）抽查结论

抽查的方法是把每一条 `[x]` 追到一条 verifier 自己能跑出的命令：

- Architecture Acceptance 3 条、Behavior Acceptance 3 条、Validation Acceptance 5 条 → 均有 verifier 自跑命令复现（见上文），**未见裸勾**。
- Documentation Acceptance 3 条 → `CLAUDE.md` 与两份权威文档表述一致、`rg 过渡期|放宽` 0 命中，成立。
- Dependency Acceptance 3 条 → 静态与动态两条口径均复核（AST 符号盘点 + importlib 扫描 + §7.3 隐藏入口检查），成立。
- Human-Confirmed 3 条 → 均有 rv-id 指向，`failure_resolver` 上移候选与 composition root 搬迁作为遗留项**已在 PRD 内显式声明**，未见用豁免掩盖缺陷。
- 唯一需要注意的是：上述抽查成立的前提是**先修完「归档前必改」第 1-3 项**，否则 PRD/证据报告本身存在与代码不符的陈述。

## 四·补 · 归档后复核复跑（verifier 第二轮，全部自跑）

> 目的：确认归档 commit（晚于「必改 1-3」落地）没有夹带源码变化，并复跑 5 个 oracle。
> 复跑树：main `178ef77`；基线 `/tmp/keda-baseline-api-engines` @ `ec621a4`。

| 检查 | 结果 | 与首轮一致 |
|---|---|---|
| `rg -n "from backend\.engines" src/backend/api/` | 0 命中（exit 1）| ✅ |
| `uv run --no-sync python hooks/shared/check_architecture.py` | 241 文件 0 违规，exit 0 | ✅ |
| `FORBIDDEN_IMPORTS` 现状 | `:41 core: [engines, infrastructure, api, composition]`；`:42 api: [infrastructure, engines, composition]` | 与首轮一致（FR-2 字面仍待改）|
| rv-1 负向对照（**本轮由 verifier 亲自重做**：`cli.py` 末尾追加 engines import → 检查 → 还原）| 报 `[backend/api] → [engines] src/backend/api/cli.py:295`，exit 1；还原后 `git status` 干净、复查 0 违规 | ✅ 与执行者产物逐字一致 |
| rv-2 api 侧 import 落点 | 33 行命中全部为 `backend.core.use_cases.agent_runner_*`；`core/use_cases` 静态 `from backend.engines` 0 行 | ✅ |
| rv-2 core 侧 `import_module("backend.engines…")` | 5 处（factory / takeover / worktree_cli / container_auth:245 / container_ops:255）| 与执行者产物一致；**完整清单（12 模块 / 13 绑定点）仍待补，见必改 2** |
| rv-3（**换用同 venv 口径**）| `iar --help` 79 / `run --help` 27 / `daemon --help` 38 行，diff **0 字节**，两侧 exit 0 | ✅ |
| rv-3 provenance | CUR `/Users/zata/code/keda/src/...` vs BASE `/tmp/keda-baseline-api-engines/src/...` | ✅ 非恒真 |
| rv-4 | 906 passed, 1185 deselected | ✅ |
| rv-5 | 146 passed（连续 3 次）| ✅（见 Note A）|
| 全量 pytest | **2091 passed in 83s** | ✅ |
| `just lint --full` | **exit 0，17 个 hook 全 Passed** | ✅（见 Note B）|
| 文档 `rg "过渡期\|放宽"` | CLAUDE.md / system-design.md / ai-standards/architecture.md 均 0 命中 | ✅ |
| 呈现模块归属 | `src/backend/api/agent_runner_views/` = `live_terminal` / `runner_live_view` / `live_panels`；`engines/` 下 0 命中 | ✅ |
| api 动态绕过 | `import_module\|__import__` 在 `src/backend/api/` 0 命中 | ✅ |

**Note A · rv-5 出现一次不可复现的失败（判定：既有测试 flaky，非本 PRD 引入）**
复跑时 `tests/test_cli_console.py::TestListenHostIsNotConfigurable::test_callback_always_binds_loopback`
失败 1 次（当时与其他命令并发执行）；随后单独跑该用例通过、`-k "daemon or loop or concurrent"` 连跑 3 次全 146 passed、
全量 2091 passed，且**基线树（`ec621a4`）同样 146 passed（1938 deselected）**。结论：daemon 行为无回归；
该 flaky 属既有测试隔离问题，建议另开 issue 跟进，不阻塞本 PRD 归档。

**Note B · `just lint --full` 的一次假红**
复跑期间有一次 `check-yaml` 报 "files were modified by this hook" 而失败；单独 `pre-commit run check-yaml --all-files`
与整体重跑均为 Passed（exit 0）。属 hook 自动改写文件的瞬态，非代码/配置问题。

**Note C · rv-3 复现路径的坑（建议写进 verification-plan）**
`/tmp/keda-baseline-api-engines/.venv` 是**空 venv**（无 typer、无 `iar`）。此时在那里执行
`uv run --no-sync iar --help`，会退回到 PATH 上的 `~/.local/bin/iar`（uv tool 环境，typer 版本与主仓不同），
于是出现 `--interval <int>` vs `[int]`、`--agent <a|b>` vs `[a|b]` 这类**纯版本差异**，看起来像 3 条 help 全变了。
正确口径是：**用主仓 `.venv/bin/iar` + `PYTHONPATH` 换源码**（两跑同一个解释器、同一套依赖，只换 `backend` 包），
本轮即用此法得到 0 字节 diff。执行者首轮产物（79/27/38 行、空 diff）与该口径结果一致，说明其取证本身没踩坑。

**Note D · §9 第 5 项 2084 → 2091 的由来已查明**
基线树 `-k "daemon or loop or concurrent"` 为 1938 deselected，当前为 1945 deselected，差 7 条用例，
与 2084 → 2091 一致（`d953a49` 之后新增测试）。§9 的数字是当时真实值，归档时建议注明测于哪个 commit。

**Note E · 归档 commit 与必改项的时间差**
`178ef77`（归档）落地时，「必改 1」（evidence-report 第 57 行把 `agent_runner_monitor` 误注为呈现能力）
与「必改 2」（rv-2 证据文件只列 5 处绑定）尚未修正，FR-2 字面仍为 `["infrastructure","engines"]`；
PRD 顶部 2026-07-31 横幅的改写目前只在工作区、未提交。verifier 结论仍为 PASS（5 个 oracle 均复现且成立），
但建议补一个 follow-up commit 收掉这几处文档/证据口径，否则归档文档会留下与代码相反的陈述。

---

## 四、verifier 执行的命令留痕

```text
rg -n "from backend\.engines" src/backend/api/                      # exit 1，0 命中
uv run --no-sync python hooks/shared/check_architecture.py          # 241 文件 0 违规，exit 0
uv run pre-commit run check-architecture --all-files                # Passed
uv run pytest tests/ -q -k "agent_runner" -o addopts=""             # 906 passed, 1185 deselected
uv run pytest tests/ -q -k "daemon or loop or concurrent" -o addopts=""  # 146 passed, 1945 deselected
uv run pytest tests/ -q -o addopts=""                               # 2091 passed
uv run --no-sync iar --help / <24 个子命令> --help  × baseline/current # 24/24 diff 空
rg -N "from backend\.engines" /tmp/keda-baseline-api-engines/src/backend/api/ | wc -l   # 44
AST 符号盘点：基线 20 文件 / 15 模块 / 120 符号 → 当前 118 原样迁移 + 2 等价工厂替换
```
