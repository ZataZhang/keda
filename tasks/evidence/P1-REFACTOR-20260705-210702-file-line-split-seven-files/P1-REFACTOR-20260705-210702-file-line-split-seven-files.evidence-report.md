# Evidence Report: P1-REFACTOR-20260705-210702 拆分 7 个超 1000 行后端 Python 文件

> Executor 于 2026-09-15 在 `/Users/zata/code/keda`（分支 `main`，工作区未提交）采集。
> 所有证据在**同一棵最终代码树**上重新收集，指纹见 `rv-0.tree-fingerprint.txt`。
> 约束性指纹为**代码面**口径 `git diff HEAD -- src tests hooks docs` → `ae0e2ade…`
> （`HEAD=7e71ef48`）。**不用全树口径**：它把 `tasks/` 下的 PRD 与证据文件也算进去，
> 而这些文件在取得证据后还会被编辑，会导致同一份代码算出不同指纹——首版 rv-0 即因此
> 不可复现，已由独立 verifier 复核时发现并改用代码面口径。
>
> **本文档是第 2 轮版本。** 第 1 轮（verifier PASS）之后，一次外部复审指出 5 项问题，
> 其中 3 项成立（注入中枢单向、settings 死别名、docstring 笔误），已整改；2 项不成立
> （两处 ruff-format"违规"经 pin 的 ruff 0.7.4 实测不存在）。整改动了代码面，
> 因此第 1 轮的全部正向证据作废并在新指纹对应的树上重采；负控也全部重做。
> 整改详情与"报错但不成立"两项的证伪见下文《外部复审与整改》一节。
>
> 原始产物（完整 help 文本、测试日志）留本地，本文只保留判定所需摘录。

## 结论速览

| oracle | 判定 | 核心证据 |
|---|---|---|
| rv-1 行数门禁清零 + 白名单清零 | PASS（含红→绿闭环） | 门禁无任何输出、exit 0；白名单条目 0；负控 1101 行文件 → `[ERROR]` exit 1 |
| rv-2 七个目标文件 ≤ 800 | PASS | 251 / 63 / **638** / **494** / 717 / 128 / 264；逐文件 `--max-lines 800` exit 0 |
| rv-3 拆分后职责清晰 | PASS | 5 个新模块顶层符号清单逐一聚焦单一语义；被拆文件符号数 22→15、16→12、50→11 |
| rv-4 CLI 行为零变化 | PASS（含红→绿闭环） | root + 24 子命令共 25 段 / 432 行 help，`diff` **0 字节**、sha256 完全相同 |
| rv-5 测试集不变 | PASS（含红→绿闭环） | 全量 `pytest -o addopts=''` **2071 passed**；基线树同数；负控删掉合并履历分支 → 3 failed |
| rv-6 注入面刷新（第 2 轮新增） | PASS（含红→绿闭环） | `blocked-continue` 入口：打补丁后处理器解析到补丁值 → True/exit 0；移除刷新调用 → False/exit 1 |
| 门禁 lint / reuse / mkdocs / architecture | PASS | 四条全 exit 0；其中全量钩子用 `pre-commit run --all-files` 实跑（15 Passed / 0 Failed），**不用 `just lint --full`**——它会因 `.last_linted_commit` 标记短路（详见下文） |

## 本轮补了什么

对账（见 PRD §12 的 2026-09-15 块）发现本 PRD 三项欠账，本轮一次补齐：

| 文件 | 拆分前 | 拆分后 | 迁出到 |
|---|---|---|---|
| `core/use_cases/run_agent_once.py` | 889 | **638** | `agent_runner_worktree_create.py`(199)、`agent_runner_attempt.py`(118) |
| `core/use_cases/agent_runner_orchestrate.py` | 899 | **494** | `agent_runner_issue_handlers.py`(542) |
| `infrastructure/config/settings.py` | 1078 | **381** | `settings_sources.py`(189)、`agent_runner_settings.py`(585) |

改动面：3 个文件**删除 1717 原始行 / 新增 147 行（净 −1570）**；按非空行口径 889→638、899→494、1078→381，即净减 1353 非空行。5 个新模块合计 1633 非空行（差额为新增模块的 docstring/import）；
另有 9 个测试文件改补丁点、`hooks/max_file_lines.allowlist.txt` 清零、2 个 docs 文件同步，
以及第 2 轮整改触及的 `blocked_continue.py`（+5 行）。

---

## rv-1 — 行数门禁清零，且不再有任何豁免

**正向**（`rv-1.max-file-lines.txt`，与 CI 同一条命令）：

```text
uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend \
  --allow-list-file hooks/max_file_lines.allowlist.txt
EXIT=0
白名单条目数: 0
```

命令**没有任何输出**——既无 `[ERROR]`，也无 `[WARNING]`。这一点比"exit 0"更强：
`f2125d0` 之后 `settings.py` 一直在白名单里以 WARNING 形式存在，现在连 WARNING 都没有了，
说明没有任何文件依赖豁免。

**负控**（`rv-1-negative.max-file-lines.txt`，临时造 1101 非空行的 `src/backend/api/_rv1_negative_control.py`）：

```text
[ERROR] src/backend/api/_rv1_negative_control.py: 1101 非空行，超过上限 1000 行。
EXIT=1
```

负控文件已删除（`ls` 返回 No such file）——红→绿闭环成立，且门禁不是"因为白名单为空所以永绿"。

**provenance**
- 关键值来源：`check_max_file_lines.py` 的真实扫描结果，非脚本统计。
- 穿过的真实边界：CI workflow 的同一条调用（含 `--allow-list-file` 参数）。
- 禁止的旁路：不把阈值从 1000 改高、不把文件塞进白名单；白名单本身清零。
- fresh-state：负控文件是新建的，非改动既有文件。
- 最终树：`rv-0` 指纹。

## rv-2 — 七个目标文件全部 ≤ 800 非空行

`rv-2.line-counts.txt`（非空行口径，与 PRD §1 的 Measurable Objectives 一致）：

```text
  OK      251  src/backend/api/cli.py
  OK       63  src/backend/api/cli_typer.py
  OK      638  src/backend/core/use_cases/run_agent_once.py
  OK      479  src/backend/core/use_cases/agent_runner_orchestrate.py
  OK      717  src/backend/core/use_cases/agent_runner_validation.py
  OK      128  src/backend/engines/agent_runner/factory.py
  OK      264  src/backend/infrastructure/github_client.py
```

追加逐文件硬门禁（比"数字看起来小"更强——由门禁脚本判定）：`--max-lines 800` 对 7 个文件
返回 exit 0。

三个原本不达标的文件都留出了余量（638 / 479 对 800），针对的是本 PRD 记录过的
"拆到位后被新功能推回去"（`run_agent_once` 852→945、`orchestrate` 869→899）的复发模式。

**provenance**
- 关键值来源：直接读文件内容统计非空行，与 `check_max_file_lines.py` 的判定口径一致（两者结论相同）。
- 禁止的旁路：不拆到 800 以下却靠白名单过关（rv-1 已证明白名单为空）。
- 最终树：同上。

## rv-3 — 拆分后职责清晰

`rv-3.module-outline.txt` 记录每个新模块的顶层 `def`/`class` 清单与模块 docstring：

| 新模块 | 顶层符号 | 单一职责 |
|---|---|---|
| `agent_runner_worktree_create.py` | `format_command` / `create_or_reuse_worktree` / `_resolve_repo_id` | worktree 创建复用 + 其命令模板格式化 |
| `agent_runner_attempt.py` | `wait_before_recovery_attempt` / `AttemptPhaseTimer` / `_make_attempt_result` / `_append_attempt_and_notify` | attempt 计时与结果记账 |
| `agent_runner_issue_handlers.py` | 4 个 `_process_*` + `_guard_blocked_issue_has_resolution` | Issue 状态分派处理器 |
| `settings_sources.py` | 12 个路径发现 / TOML source 符号 | 配置发现与 TOML 接入 |
| `agent_runner_settings.py` | 25 个 `AgentRunner*Settings` + `load_agent_runner_local_settings` | Agent Runner 设置模型 |

被拆文件的顶层符号数同步下降：`run_agent_once` 22→15、`orchestrate` 16→11、`settings` 50→11。

**provenance**
- 关键值来源：`grep -nE '^(class |def )'` 静态扫描 + `git show HEAD:<file>` 对比。
- 禁止的旁路：不靠"把函数改成一行 lambdas"或压行数来降行；迁出的是完整函数体（`git diff` 逐行核对）。
- 局限：**职责是否"清晰"含人工判断成分**，本文只提供可复核的符号清单，判断留给 verifier 与人审。

## rv-4 — CLI 行为零变化

`rv-4.help-before.txt` / `rv-4.help-after.txt` / `rv-4.help-diff.txt`：

```text
25 段 help（root + init/run/logs/review/review-daemon/recover/blocked-continue/ask/repl/
deliberate/takeover/loop-daemon/labels/issue/completion/worktree/registry/daemon/workflow/
loop/roadmap/container/console/agent），共 432 行
before sha256: ccc899d9d09abf84ecceef53b9bec95edc31f90664ffef843078bbc40c136b30
after  sha256: ccc899d9d09abf84ecceef53b9bec95edc31f90664ffef843078bbc40c136b30
差异字节: 0
```

**基线取法**：本轮改动全在工作区未提交，故 `HEAD` 即"拆分前"，用独立 worktree
（`git worktree add --detach /tmp/keda-rv4-baseline HEAD`）+ `PYTHONPATH` 指向基线 `src/`
复用同一 venv，`COLUMNS=200` 固定宽度。比 PRD 原定的 8 个子命令更宽：覆盖全部 24 个子命令。

**负控**（`rv-4-negative.help-diff.txt`）：把 `run` 命令 help 末尾句号改感叹号 →
diff 非空（exit 1），diff 精确命中那 2 处文本；还原后 `git diff --stat` 为空。

**provenance**
- 关键值来源：真实 `backend.api.cli:main` 入口（与 `.venv/bin/iar` 同一函数），非直接调用 typer 内部。
- 穿过的真实边界：typer/rich 渲染、`--help` 早退路径、终端宽度探测。
- 禁止的旁路：不去读 PRD 里的期望文本、不用 `--help` 之外的方式（如读源码常量）比对。
- **污染与修正**：首版 `help-after` 曾被 stale `.pyc` 污染（见下文"字节码缓存陷阱"），该版已作废并重采；
  重采时基线侧与当前侧都先清 `__pycache__`。
- 最终树：`rv-0` 指纹。

## rv-5 — 测试集与并发顺序不变

`rv-5.just-test.log`（全量口径，**不接受** `just test` 的 testmon 增量绿）：

```text
2071 passed in 76.16s
```

**基线对照**（`rv-5.just-test-BASELINE.log`，在 `/tmp/keda-rv4-baseline` 的拆分前树上以同一
venv、同一命令 `pytest -o addopts='' -q tests/` 跑）：

```text
2071 passed in 77.93s
```

拆分前后**用例总数与通过数完全相同**（2071 = 2071），符合"纯结构性重构、不新增也不删减用例"
的预期。拆分过程中出现过的失败（先后 6 个、12 个）全部是补丁点错位，逐条修复后回到同一总数；
最终 0 failed、0 skipped 增量。

**负控**（`rv-5-negative.just-test.log`）：删掉 `run_issue_with_agent_fallback` 的
"合并 attempt 履历"分支后：

```text
FAILED tests/test_agent_runner_orchestrate.py::test_fallback_merges_attempt_history_with_agent_labels
FAILED tests/test_agent_runner_orchestrate.py::test_fallback_single_agent_reraises_with_agent_stamp
FAILED tests/test_agent_runner_orchestrate.py::test_run_once_marks_failed_with_merged_history_when_all_agents_fail
3 failed, 2068 passed
```

还原后校验 sha256 与绿色运行时的被测文件字节一致（`cc994b79…`），故绿色证据仍绑定最终树。

**provenance**
- 关键值来源：pytest 真实全量运行，`-o addopts=''` 显式关掉仓库默认的 `--testmon`。
- 穿过的真实边界：真实 git worktree、真实 worktree claim / 标签流转（GitHub 与 LLM 在测试边界 mock）。
- 禁止的旁路：不改测试断言语义、不跳过用例、不用 `-k` 只跑子集。
- 并发顺序：daemon / loop / orchestrator / claim-marker 相关用例全部在 2071 内，无 skip。
- 最终树：同上。

---

## rv-6 — 注入面在不经派发的入口上生效（第 2 轮新增）

`rv-6.injection-hub-probe.txt`（探针源码同目录 `rv-6.injection-hub-probe.py`）：

```text
--- 正向（当前树，含 refresh_runtime_dependencies 调用）---
处理器解析到的 choose_agent 是 orchestrate 上的补丁值 ? True
正向 EXIT=0
--- 负控（临时移除刷新调用）---
处理器解析到的 choose_agent 是 orchestrate 上的补丁值 ? False
负控 EXIT=1（1=红）
--- 还原后复跑 ---
处理器解析到的 choose_agent 是 orchestrate 上的补丁值 ? True
还原后 EXIT=0
```

**探针在测什么**：它把 `orchestrate.choose_agent` 换成 sentinel（模拟
`monkeypatch.setattr(orch, "choose_agent", ...)`），再驱动真实的
`blocked_continue_issue` 走到状态处理器调用点（worktree / 分支 / 洁净度检查与 CAS claim
都用 fake 放行），在处理器被调用的那一刻读出 `agent_runner_issue_handlers.choose_agent`
是不是那个 sentinel。是 → 补丁生效（拆分前的语义）；否 → 静默失效。

**provenance**
- 关键值来源：真实入口 `blocked_continue_issue`（`iar blocked-continue` 的用例层），
  sentinel 直接替换模块属性，非读取配置或常量。
- 穿过的真实边界：入口的四道前置校验 + CAS claim 分支 + 模块级冻结导入。
- 禁止的旁路：不去断言"源码里有 refresh 调用"这种静态检查；不在调用前后手工刷新。
- fresh-state：每次运行前清 `src/**/__pycache__`（同尺寸改写会命中 `.pyc` 缓存陷阱）。
- 最终树：`rv-0` 指纹；负控还原后复跑通过且指纹回原值。

## 外部复审与整改（第 2 轮）

第 1 轮 verifier PASS 之后，一次外部复审给出 5 项"可改进项"并建议前 3 项合入前处理。
逐条实测结果：**3 项成立、2 项不成立**。

| 复审项 | 实测判定 | 依据 / 处置 |
|---|---|---|
| ① 注入中枢对"非派发路径"单向失效 | **成立** | 已复现（patch 后 `handlers.choose_agent` 不变，直到刷新才变）。按 D-14 整改 + rv-6 覆盖 |
| ② `agent_runner_settings.py:27` class 前只有 1 空行 | **不成立** | pin 的 ruff 0.7.4 `format --check` → `5 files already formatted`、exit 0；实测 27 与 28 行都是空行、class 在 29 行 |
| ③ `agent_runner_attempt.py:141` 末尾多 2 空行 | **不成立** | 该文件共 139 行、末尾多余空行 0；"第 141 行"不存在 |
| ④ 死别名 + 注释与事实不符 | **成立（比其所述更广）** | 死别名是 4 个不是 2 个（它还漏了 `_default_runner_command`）。已按 D-11 改写并移除 |
| ⑤ `worktree_create` docstring 把 `path_command` 列了两遍 | **成立** | 已修 |
| 提醒：僵尸 PRD 删除合理 | **同意** | 归档副本已在 `tasks/archive/` 且随 `c7e3b69` 提交 |
| 提醒：`idea.md:26` 指向已删除的 pending 路径 | **同意** | 该引用随本轮清理失效；属工作区待办，未在本次改动中处理 |

**②③ 虽不成立，但它们踩到的前提是真的**：`pre-commit run --all-files` 的作用域就是
`git ls-files`，因此**未跟踪的新文件不在扫描范围内**——`git ls-files` 对 5 个新模块命中
**0** 个。也就是说第 1 轮"`just lint --full` 全绿"这条证据**确实没覆盖 5 个新模块**，
声明强于检查。第 2 轮起改为对新增文件显式 `pre-commit run --files <paths>`（含
`ruff` 与 `ruff-format` 两个钩子），并已对全部 8 个被触及文件补跑通过。

本轮结论：**没有找到新的旁路或失效证据**；①④⑤ 属真实缺陷（其中 ① 是拆分引入的静默
语义回退），已整改并重采证据；②③ 为误报，本文保留证伪过程以备复核。

## 需要人审知道的三个实现决策

### 决策一：orchestrate 继续做「单一补丁中枢」（新增对接而非改测试）

`_process_*` 处理器搬走后，它们内部调用的 11 个协作者（`choose_agent`、`get_current_branch`、
`_reuse_existing_local_commit` 等）会改从新模块的命名空间解析。测试原本在
`agent_runner_orchestrate` 上替换这些依赖（AST 枚举出 16 个被打补丁的名字，跨 3 个测试文件），
照原样搬会全部失效。

两条路：**(a)** 让每个补丁点改打新模块；**(b)** 沿用仓库既有的
`RUNTIME_DEPENDENCY_NAMES` 注入机制，让 orchestrate 在每次派发前把名字同步进
`agent_runner_issue_handlers`。选了 **(b) 为主**，因为
`agent_runner_orchestration_runtime` **本来就**从 orchestrate 取这些名字作默认值
（`RUNTIME_DEPENDENCY_NAMES` 里已有 `choose_agent` / `create_or_reuse_worktree`），
即 orchestrate 本就是这套子系统的依赖注入中枢；只改一个模块会让"同一处替换要打两个模块"，
测试反而更碎。

配套：worktree 模块与 attempt 模块**没有**这样做（它们不在这套中枢里），所以
`test_worktree_cli` 的 `provision_worktree_database` 补丁点、`test_agent_runner_run_once_commit`
的 `time.sleep` 补丁点改打实现所在模块。

代价：orchestrate 的 `__all__` 由 **8 项增至 27 项**（HEAD 用 AST 实测 8，当前 27），
其中若干名字在本模块内已无直接调用点，仅作同步源存在——已在该列表上方就地注释说明。

**第 2 轮补的洞（D-14）**：上面这套机制最初只在**派发路径**上触发。`blocked_continue.py`
在模块加载时就从 orchestrate 冻结导入 `_process_blocked_resolution`，走
`iar blocked-continue` 这条路**永不经过派发**，于是同步不触发、补丁静默失效——
而拆分前该函数住在 orchestrate，同样位置的补丁是生效的。整改：orchestrate 暴露
`refresh_runtime_dependencies()`，`blocked_continue_issue` 在调用处理器前显式调用；
新增 rv-6 探针覆盖（红→绿闭环见 `rv-6.injection-hub-probe.txt`）。
全仓排查确认这是唯一绕过刷新面的调用点（`runtime` 模块的四个派发函数没有别处直接调用）。

> 独立 verifier 复核后补充（见 verifier-report）：该机制确认 load-bearing——禁用 runtime 半边后
> 2 条派发型用例转红；直接调用与派发路径都覆盖到。另有两项与本次改动无关的既有观察记录在案：
> runtime 另有 16 个从 orchestrate 导入但未登记同步的名字（HEAD 同构，非本次回归）；
> handlers 同步理论上会在派发时覆盖直接打在 handlers 上的补丁（当前 0 处触发）。

### 决策二：settings 拆成三段而非两段

`AgentRunnerSettings` 在 `settings.py`、19 个 `AgentRunner*Settings` 要迁出，若只切两段会形成
`settings.py ↔ agent_runner_settings.py` 的**循环导入**（前者为 `AppSettings` 需要后者，
后者为 `IAR_REPOSITORY_CONFIG_FILENAME` 需要前者）。按 AST 实测的跨块引用图，把
"路径发现 + TOML 源 + 配置文件名常量"下沉为 `settings_sources.py`，依赖变成
`sources ← agent_runner_settings` 与 `sources ← settings`，单向无环。

对外的 61 个顶层名字里，**45 个仍可从 `settings` 访问、16 个不再可达**（12 个零引用私有名 + 4 个已移除的死别名）；
已逐个文本级确认在 `src/` 与 `tests/` 中**零引用**（全部是私有 helper 与路径常量）。
其中仍被消费者**按 `...config.settings.X` 路径**取用的 4 个用 PEP 484 的冗余别名
（`X as X`）再导出，保持访问路径不变。

**第 2 轮修正（D-11 改写）**：首版把 8 个名字都当活别名再导出，其中 4 个在测试补丁点
迁移后其实已无消费者——`_default_runner_command`（`test_cli_console` 的补丁点已改打
`agent_runner_settings`）、`_load_toml_section_data` 与 `_load_registry_toml_section_data`
（`test_agent_runner_config` 已改打 `settings_sources`）、`resolve_config_toml_path`
（唯一的"引用"是 `test_agent_runner_init.py:743` docstring 里的一句散文，不是真调用）。
首版注释还断言"仍被按 `...config.settings.X` 引用"，与事实不符。

判定方法也换了：**不再按名字出现次数，而是按消费者实际走哪条 import 路径**逐个 AST 判定
（`ImportFrom` 的 module 是不是 `backend.infrastructure.config.settings`）。4 个死别名已移除——
保留它们会让 `patch("...config.settings.X")` 静默 no-op（真正解析发生在
`settings_sources` / `agent_runner_settings` 的命名空间），而 `AttributeError` 是响亮的。

### 决策三：拆分边界与原计划（§7.2）不一致，按实测图重划

PRD §7.2 计划拆出 `agent_command.py`、`agent_run_loop.py`、
`agent_runner_orchestrate_process_<stage>.py`（每个 `_process_*` 一个文件）。
实际按 AST 跨块引用图重划为 `agent_runner_worktree_create.py` / `agent_runner_attempt.py` /
`agent_runner_issue_handlers.py`，理由：

- `format_command` 只服务 worktree 的三个命令模板（`agent_runner_worktree_probe.py` 也用它），
  与 `choose_agent`（agent 选择语义）不同类；把后者留在 `run_agent_once` 后行数已达标，
  无需为一个 40 行的组再造模块。
- `agent_run_loop.py` 原计划范围（`run_agent_until_committed` + retry + memory 落盘）过大，
  真正自洽且零外部耦合的是 attempt 记账组（仅依赖 `_logger`），故收窄为 `agent_runner_attempt.py`。
- `_process_*` 每函数一个文件会产出 4 个文件、其中 3 个偏小，且四者共享守卫与常量、
  又共用同一个依赖注入面；合为一个"状态处理器"模块内聚更高。

已按 PRD §9 要求记入 §13 Decision Log 并更新 §7.2。

### 附：三个会制造假绿/假红的陷阱（都实际踩到过）

**(1) python 字节码缓存**（本 PRD 相关）：rv-4 负控把 help 里的 `.` 改成 `!` ——
两者都是 1 字节，**源文件大小不变**。改写与还原若落在同一秒，`.pyc` 头部记录的
(mtime, size) 与还原后的源文件相符，Python 会继续用**含 `!` 的旧字节码**：
源文件已还原、`git diff` 干净，CLI 却仍打印 `!`。

本轮确实踩到：首版 `rv-4.help-after.txt` 因此与基线产生 4689 字节伪差异。
处理：作废该版、显式清理 `src/**/__pycache__` 后重采（正反向都重采）。
**复现 rv-4 / rv-6 前必须先清缓存。**

**(2) `just lint --full` 的标记短路**（第 2 轮 verifier 指出）：`just test`（本地档）
通过后会同时刷新 `.last_tested_commit` 与 `.last_linted_commit`；此后 `just lint --full`
命中标记即**直接返回 0 并打印"flag valid"**，并不真的跑钩子。所以
"`just lint --full` exit 0"在跑过 `just test` 之后**不构成全量钩子通过的证据**。

本报告的门禁证据因此改以 **`pre-commit run --all-files`** 为准（实跑 15 Passed / 0 Failed）；
`just lint --full` 只用于确认仓库自己的门禁入口是绿的。

**(3) 未跟踪文件逃过 `--all-files`**（第 2 轮外部复审提出）：`--all-files` 的作用域是
`git ls-files`，**未跟踪的新文件不在其中**（对 5 个新模块命中 0 个）。因此对新增文件
必须显式传路径：`pre-commit run ruff --files <paths>` 与 `... run ruff-format --files <paths>`
（两个独立钩子，pin 的 ruff 为 0.7.4）。

## 未采集 / 非阻塞

- 真实 `iar daemon` 连 GitHub / LLM 的端到端：按 PRD §7.6 标 `opt-in / post-merge`，
  需凭据与远端不可逆副作用；替代证据为 rv-4 全命令 `--help` 烟测 + rv-5 全量测试集。
- daemon 进程重启以载入新代码：由人工在合入后执行。
- 前端：本 PRD 不涉及 `frontend-admin/` / `frontend-public/`，无视觉证据要求。
