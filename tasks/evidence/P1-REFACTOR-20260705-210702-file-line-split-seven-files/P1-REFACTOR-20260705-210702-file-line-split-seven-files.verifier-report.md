# Verifier Report（**第 2 轮** · 只读审查）: P1-REFACTOR-20260705-210702 拆分 7 个超 1000 行后端 Python 文件

> 独立审查（verifier 与 executor 分离）。**本轮为第 2 轮**：第 1 轮 PASS 后一次外部复审提出 5 项，
> 执行者整改了 3 项并动了代码面，故第 1 轮全部正向证据作废、已在代码面指纹 `ae0e2ade…` 的树上重采。
> 本文件按本轮要求**覆盖**了第 1 轮那份 verifier-report（第 1 轮的自持指纹 `7f6f2e14…` 与 `orchestrate 479 / settings 379` 等数值
> 均对应整改前的旧树，不再适用；其正文已不在工作区，仅在 execution 侧的历史记录里可查）。
>
> 审查对象：`HEAD = 7e71ef488e25e5240b822b28398ec9b99ab60843`（分支 `main`）+ 未提交工作区。
> 审查时点：2026-09-15。**本报告只写我自己跑出来的观察**；执行者的结论仅作对照项，不构成依据。
> 除本报告外我未留下任何仓库文件改动；全部临时注入（负控）均已还原（§6 清单）。

## 0. 结论

**PASS**（可归档）。第 2 轮 3 项整改**确实修好且未引入新问题**；执行者对 2 项外部复审"不成立"的判定
**独立复核成立**。另发现 **7 处文档/证据表述不准确**（§4），全部非阻塞，不改变代码与行为结论。

- 我独立复现了本轮全部底线：**7 个目标文件 + `settings.py` 行数逐数字相同**、**行数门禁无输出 exit 0 / 白名单 0 条**、
  **CLI 56 条命令路径 `--help` 逐字节一致**（比执行者的 25 段更宽）、**全量测试 2071 = 基线 2071**、
  **四道门禁（pre-commit all-files / lint --reuse / mkdocs --strict / check_architecture）全绿**。
- 本轮整改的核心（rv-6 注入面刷新）我走了**红→绿→还原三态**，并在**基线树**上验证了"拆分前同位置是生效的"这一前提；
  另外用 AST 穷举确认**没有第二条绕过刷新面的入口**。
- 死别名整改我用**反证法**验证：删掉保留的 4 个别名 → 3 个生产模块 `ImportError`（说明是活别名）；
  被移除的 4 个 + settings 拆出后不可达的 12 个，经四类访问路径穷举**零消费者**。
- 未发现旁路：白名单为空名单、阈值未改、测试断言零改动（9/9 文件 assert/用例/skip 计数逐一相等）、无隐藏动态加载。

### 0.1 我自持的树指纹（审查前 / 审查后两次测量）

```text
代码面（与 rv-0 同口径）：git diff HEAD -- src tests hooks docs | sha256
  审查开始前：ae0e2adecba5c89845986ec7c790ec52b4fd653559b261c531a0514d3fa9b6c7
  审查结束后：ae0e2adecba5c89845986ec7c790ec52b4fd653559b261c531a0514d3fa9b6c7   ← 同值
全树（参考）：1e76bc50eecaa241d2b314e3d0028c0d19dc6621a138e1a5dd2f743496816d4e（前/后同值）
git status --porcelain 条数：27（前/后同值）
```

即：本轮的 **4 次源码注入（rv-1 临时文件、rv-4 标点、rv-6 移除刷新调用、死别名移除）全部还原无残留**，
且我**没有**依赖执行者的 rv-0 文件——`ae0e2ade…` 是我自己算出来的，与 rv-0 声明值相同。
（本报告与证据目录都是 untracked，故"写报告"这一步不影响上述两个口径的哈希——写完后复测仍同值。）

---

## 1. 我亲自执行的命令与观察

### 1.1 行数门禁（rv-1）与目标文件行数（rv-2）

| # | 命令（原样） | 我的观察 | 与执行者声明 |
|---|---|---|---|
| 1 | `uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 --glob "*.py" src/backend --allow-list-file hooks/max_file_lines.allowlist.txt` | **无任何输出，EXIT=0**（连 `[WARNING]` 都没有） | 一致 |
| 2 | 同上，先造 `src/backend/api/_rv1_negctl_tmp.py`（1101 非空行） | `[ERROR] src/backend/api/_rv1_negctl_tmp.py: 1101 非空行，超过上限 1000 行。` **EXIT=1** | 一致（负控成立） |
| 3 | 同上 + 临时白名单副本（`/tmp`，不动仓库文件）写入该文件 | `[WARNING] …（已在白名单中）` **EXIT=0** | 证实"白名单机制仍在"；当前 0 条，未被使用 |
| 4 | `rm` 负控文件 + 清 `__pycache__` 后重跑 #1 | 无输出，**EXIT=0**，负控文件确认不存在 | 一致（红→绿闭环） |
| 5 | `hooks/shared/check_max_file_lines.py:count_non_empty_lines` 逐文件统计（与钩子同函数，避免口径差） | `cli 251 / cli_typer 63 / run_agent_once 638 / orchestrate 494 / validation 717 / factory 128 / github_client 264`，`settings 381` | **8/8 逐数字相同** |
| 6 | 7 个目标文件逐个 `check_max_file_lines.py --max-lines 800 <file>` | **7/7 EXIT=0** | 一致 |
| 7 | `grep -c . hooks/max_file_lines.allowlist.txt` + 人读文件 | 7 行**全是注释**，**有效条目 0** | 一致 |
| 8 | 新增 5 模块非空行（同 #5 口径） | `worktree_create 199 / attempt 118 / issue_handlers 542 / settings_sources 189 / agent_runner_settings 585` | 与 §7.2「实际交付边界」相同 |

### 1.2 rv-6 注入面探针（本轮整改核心）——我自己走红→绿

```text
命令：PYTHONPATH=. uv run --no-sync python tasks/evidence/<…>/rv-6.injection-hub-probe.py
（每次运行前清 src/**/__pycache__）

① 正向        处理器解析到的 choose_agent 是 orchestrate 上的补丁值 ? True      EXIT=0
② 负控        我把 blocked_continue.py:121 的 refresh_runtime_dependencies() 换成 pass
              处理器解析到的 choose_agent 是 orchestrate 上的补丁值 ? False     EXIT=1   ← 红
③ 还原后复跑  处理器解析到的 choose_agent 是 orchestrate 上的补丁值 ? True      EXIT=0   ← 绿
```

**我不满足于探针本身**，另做了两项独立取证：

1. **拆分前语义核对**（在 `git worktree add --detach /tmp/verify2-baseline HEAD` 的基线上跑）：

   | | 基线（HEAD） | 当前树 |
   |---|---|---|
   | `blocked_continue._process_blocked_resolution.__module__` | `…agent_runner_orchestrate` | `…agent_runner_issue_handlers` |
   | 其 `__globals__ is orchestrate.__dict__` | **True** | **False** |
   | 补丁 `orchestrate.choose_agent = SENTINEL` 后 handler 看到的是补丁值？ | **True** | **False**（未刷新时） |

   → 执行者的前提"拆分前同样位置的补丁是**生效**的"**成立**，这不是新增语义而是**恢复**语义。

2. **"唯一入口"排查（我自己做，不采信结论）**：
   - AST 穷举 `src/` + `tests/` 中对 4 个 `_process_*` 与 `_guard_blocked_issue_has_resolution` 的**全部调用点**：
     `src` 侧只有 2 处——`blocked_continue.py:123`（其上一行即刷新调用）与 `agent_runner_orchestration_runtime.py` 的派发内部。
   - `agent_runner_orchestration_runtime` 的导入方**只有一个**（`agent_runner_orchestrate.py:163` 的惰性导入）；
     该模块顶层只有 3 个函数（`run_once` / `process_prd_rework_issues` / `_process_single_issue`），
     **全部**被 orchestrate 的三个 wrapper 包住，而这三个 wrapper 都在调用前执行 `_orchestration_runtime_module()`（即刷新）。
   - daemon 侧：`run_agent_daemon.py:148/196` 与 `run_agent_repositories_once.py:123/138` 用的都是 orchestrate 的 wrapper（已同步），
     **没有**任何模块直接调用 runtime 的派发函数。
   - 无 `importlib` / `__import__` / `sys.modules` 形式的动态加载（全 `src/` 扫描，命中的只有 memory 子系统的 `importlib.import_module`，与本 PRD 无关）。
   - **附加缺口搜寻**：把"从旧模块搬走的名字"（run_agent_once 8 个、orchestrate 6 个、settings 48 个）逐一与 tests/src 里的补丁点做**别名解析后**的交叉匹配——
     唯一命中是 6 处 `monkeypatch.setattr(orchestrate, "_process_ready_issue", …)`，而该名字**在同步名单内**，
     其余**没有任何补丁/属性访问仍指向旧模块的搬走名字**（不存在第二处同类静默失效）。

   → 结论：**`blocked-continue` 确实是唯一绕过派发刷新的入口，D-14 的整改面是完整的**。

3. **残留缺口的量化（我主动找的）**：handler 函数体还从自己命名空间解析 9 个名字（`AppConfig` / `IssueSummary` /
   `IContentGenerator` / `IGitHubClient` / `IProcessRunner` / `ReviewEventMarker` / `AttemptResult` / `Path` / `Callable`），
   它们不在同步名单里。逐个核对：**全部是类型/标注名，tests 与 src 中零处对其打补丁**（同上 AST 交叉匹配），
   故该残留是**惰性**的、当前不产生静默失效。（执行者证据报告已把同类观察记为"runtime 另有 16 个未登记同步的名字，HEAD 同构"。）

### 1.3 settings 死别名（D-11 改写）

| # | 检查 | 观察 |
|---|---|---|
| 1 | AST 找 `from backend.infrastructure.config.settings import <name>` / `import …config.settings as X` + `X.<name>` / `patch("…config.settings.<name>")` 字符串目标 | 保留的 4 个**都有消费者且都走 `...config.settings.X` 路径**：`factory.py:80`、`factories/__init__.py:51`（两个 `resolve_*`）、`factory_config_builder.py:39`、`tests/test_agent_spec_config.py:29`、`tests/test_agent_runner_config.py:29`（两个 Profile 类） |
| 2 | 同名检查机制自检（防止我的检测器漏报） | 同一脚本正确检出 `settings_module._PROJECT_ROOT_PATH`、`settings_module.load_agent_runner_local_settings` → 检测器有效 |
| 3 | 对象同一性 | 4 个别名 `getattr(settings, X) is getattr(实现模块, X)` 全 True |
| 4 | **反证（我自己注入）**：把 4 个别名的 import 语句整体删掉 | `import backend.engines.agent_runner.factory` / `.factories` / `.factory_config_builder` **全部 ImportError**：`cannot import name 'AgentRunnerAgentProfileSettings' from '…config.settings'` → 别名是**load-bearing**，不是装饰 |
| 5 | 被移除的 4 个（`_default_runner_command` / `_load_toml_section_data` / `_load_registry_toml_section_data` / `resolve_config_toml_path`） | `hasattr(settings, X)` 全 False；全仓文本级扫描（tracked + untracked，排除 build/site/pyc）**零消费者**——命中项只有：新实现模块内部、`tests/*` 走新模块（`settings_sources` / `agent_runner_settings`）、以及 2 处文档散文（`test_agent_runner_init.py:743`、`test_cli_console.py:148`） |
| 6 | 迁移必要性核对（HEAD 版测试打在哪） | HEAD 的 `test_preview_settings.py:53/82` 打 `settings_module._find_config_toml`、`test_agent_runner_config.py:87` 打 `settings_module._load_toml_section_data`，当时**确实生效**（实现在 settings.py 内）；本轮已改打 `settings_sources` → 死别名的判定成立 |
| 7 | `__all__` 一致性 | 27 个导出名全部可达；`from …settings import *` 正常（无悬空名，否则 mkdocstrings/star-import 会炸） |
| 8 | 不可达名字总数（我实测） | HEAD 定义型顶层名 60 个 → 当前可达 44 个 → **16 个不可达** = 12 个（拆到新模块后零引用，全私有）+ 4 个（第 2 轮移除的死别名）。逐个四类访问路径穷举：**零消费者** |

> 口径说明：PRD D-11 写"61 个顶层名中 49 个仍可从 settings 访问、12 个零引用私有名不再可达"。
> 我的计数是 60 / 44 / 16（16 = 12 + 4 个死别名，后者在 D-11 里单列）。**逐个名字核对后无实质分歧**，
> 只是"总数 61 vs 60"的计数约定差 1（我按"模块体内定义型顶层名，排除 import 与 dunder"计数）。见 §4-G4。

### 1.4 CLI 行为零变化（rv-4）——覆盖比证据更宽

我自己写采集器（读 help 文本里的 `Commands` 块**递归**枚举命令树，`COLUMNS=200`，独立子进程，两棵树同参数）：

```text
命令：uv run --no-sync python /tmp/verify2-cli-help-diff.py
      （当前树：PYTHONPATH=/Users/zata/code/keda/src；基线树：PYTHONPATH=/tmp/verify2-baseline/src）

共枚举到 56 条命令路径（含 root），全部 OK（rc=0/0，逐字节相同、sha256 逐条相同）：
<root> agent{doctor,list} ask blocked-continue completion{install,show} console
container{auth import,down,logs,up} daemon{run,status} deliberate init issue{create,list}
labels{sync} logs loop{cancel,create,list,run} loop-daemon recover
registry{list,reinit,remove,scan,start,stop,sync} repl review review-daemon
roadmap{advance} run takeover workflow{install} worktree{cleanup,create,path,remove}
→ 全部 56 条命令路径 --help 逐字节一致（exit 0）
```

- 枚举自洽：每一层的 help 文本都已被验证逐字节相同，故父命令的子命令集合必然相同，**不存在"基线有而当前没有"的漏枚举**。
- **负控（我自己注入）**：把 `cli_typer_runner.py:86` 的 `run` help 末尾 `.` 改成 `!`（**同尺寸改写，刻意踩 `.pyc` 陷阱**）
  → diff 非空且**精确命中该 2 处文本**（Commands 列表 + `iar run --help` 正文），EXIT=1；还原后 diff 一致、EXIT=0。
- 我实测：同尺寸改写**没有**被 `.pyc` 判为命中（源文件 mtime 落在新的一秒 → 重新编译）。即陷阱是**时序相关**的、
  并非必然触发；执行者记录的"必须清 `__pycache__`"是正确且廉价的规避，我全程照做。
- 归因提示（不影响结论）：本轮工作区**没有改动任何 `src/backend/api/` 文件**，所以 rv-4 对本轮而言是"配置侧不回归"的守边
  （`settings.py` 拆分会经 `AgentRunnerSettings` 影响 CLI 启动与默认值渲染，故 rv-4 仍有意义）。

### 1.5 测试集（rv-5）

```text
当前树：cd /Users/zata/code/keda && uv run --no-sync pytest -o addopts='' -q tests/
        → 2071 passed in 66.61s   EXIT=0
基线树：cd /tmp/verify2-baseline && PYTHONPATH=/tmp/verify2-baseline/src \
        /Users/zata/code/keda/.venv/bin/python -m pytest -o addopts='' -q tests/
        → 2071 passed in 63.36s   EXIT=0
→ 通过数 2071 = 2071，与执行者声明一致
```

**两次假失败被我定位为采集口径问题（不是回归）**，过程留痕：

1. 若基线从 `/Users/zata/code/keda` 目录跑：`test_agent_config_consistency.py::test_agent_runner_reads_root_config_toml` 失败——
   原因是该用例断言 `_find_config_toml() == <测试文件所在仓库根>/config.toml`，而 `_find_config_toml()` 从 **cwd** 向上找 `config.toml`；
   cwd 不是基线树时必然不等（HEAD 与本轮代码同构）。**基线必须在基线树的 cwd 下跑**。
2. 若带 `COLUMNS=200`：`test_agent_runner_init.py` 的 2 条用例失败（rich 按宽度换行把
   `Please review verification_commands` 拆开）。我在**当前树**上复现了同样 2 条失败（`COLUMNS=200 → 2 failed，不带 → 39 passed`），
   证明这是**既有测试对终端宽度的脆弱性**，与本 PRD 无关。

**测试断言零改动（我独立复核）**：9 个被改测试文件的 `assert` 数 / `test_*` 函数数 / skip·xfail 数在 HEAD 与当前**逐一相等**
（35/10/0、126/40/0、17/6/0、134/52/0、46/6/0、16/17/0、26/5/0、97/32/0、39/12/0）。

### 1.6 门禁

| # | 命令 | 观察 |
|---|---|---|
| 1 | `just lint --full` | **打印 `✅ just lint --full flag valid: main @ 7e71ef48` 后退出——命中了 `.git/.last_linted_commit` 缓存，钩子根本没跑**（见 §4-G6） |
| 2 | `uv run --no-sync pre-commit run --all-files --show-diff-on-failure`（= #1 真正执行的命令） | 17 个钩子：**15 Passed + 2 Skipped（SQLAlchemy/schema，无匹配文件），EXIT=0**，与"15 个钩子全 Passed"声明一致；跑前跑后**没有文件被 auto-fix 改动** |
| 3 | `just lint --reuse` | jscpd / pylint-duplicate-code / check-architecture / check-guidelines-consistency / check-max-file-lines **5/5 Passed，EXIT=0** |
| 4 | `uv run --no-sync mkdocs build --strict` | EXIT=0（只有既有的 nav/anchor INFO） |
| 5 | `uv run --no-sync python hooks/shared/check_architecture.py` | **扫描 232 个文件，无违规，EXIT=0** |
| 6 | **显式传路径**（规避 `--all-files` 漏掉未跟踪文件）：`pre-commit run ruff --files <9 文件>`、`… run ruff-format --files <9 文件>`、`… run check-max-file-lines --files <9>`、`… run jscpd --hook-stage manual --files <9>`、`… run pylint-duplicate-code --hook-stage manual --files <9>` | **全部 Passed**；9 文件 sha256 前后一致（钩子未做 auto-fix） |
| 7 | pin 的 ruff 版本与格式 | `~/.cache/pre-commit/repoxjll_w1y/py_env-python3.13/bin/ruff --version` → **ruff 0.7.4**（与 `.pre-commit-config.yaml` 的 `rev: v0.7.4` 一致）；`ruff format --check <9 文件>` → **9 files already formatted，EXIT=0** |

> #6 是对执行者第 2 轮"方法修正"的**独立确认**：`--all-files` 的作用域是 `git ls-files`，
> 5 个新模块（untracked）确实不在其中；显式 `--files` 后它们被真正检查且通过。

### 1.7 结构与搬迁核对（rv-3）

| # | 检查 | 我的观察 |
|---|---|---|
| 1 | 5 个新模块顶层 `def`/`class` 清单 | 3 / 4 / 5 / 12 / 27 个，与 `rv-3.module-outline.txt` 的**行号与名字逐条相同** |
| 2 | 5 个新模块的模块级 docstring | **全部存在**且描述单一语义（中文） |
| 3 | 被拆文件顶层符号数 HEAD → 当前 | `run_agent_once 22→15`、`orchestrate 16→12`、`settings 50→11`、`blocked_continue 4→4`，**与证据逐数字一致** |
| 4 | 跨层 import 命中 | `src` 全域 `from backend.engines` = **73 = 73**；`api/` 下 `from backend.engines` = **44 = 44**；`api/` 下 `from backend.infrastructure` = **0 = 0**；`src` 全域 `from backend.(engines\|infrastructure)` = **133 vs 128（+5，全部是 `infrastructure.config` 层内互通）** |
| 5 | 新增行跨层检查 | `git diff HEAD -- src` 的新增行里匹配 `from backend.(engines\|infrastructure)` 的 **4 条全部是 `backend.infrastructure.config.*`（层内）**，**真正跨层 0 条**（§4-G1 是措辞问题） |
| 6 | `hooks/shared/check_architecture.py` / 规范层文档 | `git diff --stat HEAD` 对 `check_architecture.py`、`docs/ai-standards/code-reuse.md`、`CLAUDE.md`、`AGENTS.md` **全为空**（未触碰） |

---

## 2. 对三项整改的判断

| 整改 | 是否真修好 | 依据（我自己跑的） | 是否引入新问题 |
|---|---|---|---|
| ① rv-6 / D-14 注入面刷新 | **是** | 探针红（False/exit 1）→绿（True/exit 0）三态闭环；基线树验证"拆分前同位置生效"为真；AST 穷举确认无第二处绕过；把刷新调用删除只影响该入口（无其它调用点） | **无**。新增的 `refresh_runtime_dependencies()` 是纯转发 `_orchestration_runtime_module()`；`blocked_continue` 早已模块级 import orchestrate，**不构成循环导入**（orchestrate 不 import blocked_continue，实测导入顺序无异常）。也不会误覆盖测试补丁：tests 中不存在"打 handlers 补丁 + 经 `blocked_continue_issue`"的组合（AST 核对 0 处） |
| ② D-11 settings 死别名移除 | **是** | 保留的 4 个：删除后 3 个生产模块立刻 `ImportError`（=活别名）；移除的 4 个：全仓（含 patch 字符串目标）零消费者 | **无**。`__all__` 无悬空名、star-import 正常、mkdocs `--strict` 通过、全量测试 2071 通过。**注意**：这是**有意的对外面收窄**——`patch("…config.settings.<被移除名>")` 现在会**响亮报 AttributeError**（旧行为是静默 no-op），属 D-11 明确要的效果 |
| ③ `worktree_create` docstring 笔误 | **是（当前文本自洽）** | 现文本 `path_command` 只在"三段流水线"描述中出现一次、语义正确 | **无**。局限：该文件是 untracked，我无法 diff 出"改前"文本，只能确认**当前无重复列举**（见 §5 第 5 条） |

---

## 3. 对执行者"2 项不成立"判定的复核结论

| 外部复审项 | 执行者判定 | **我的独立复核** | 是否支持执行者 |
|---|---|---|---|
| ② `agent_runner_settings.py` class 前"只有 1 个空行" | 不成立 | **不成立成立**：脚本逐行统计 27 处顶层 `class`/`def`，**每一处前面都是 2 个空行**；`AgentRunnerLabelSettings` 在 **L29**，L27/L28 均为空行；含"注释块算进间隔"的严格口径也无例外；pin 的 ruff 0.7.4 `format --check` = already formatted | **支持** |
| ③ `agent_runner_attempt.py` 末尾"多 2 空行"（称在第 141 行） | 不成立 | **不成立成立**：`wc -l` = **139**（第 141 行不存在）；文件末 4 行 repr = `[…"exc_info=True,", ")" , ""]`，即**以单个 `\n` 结尾、无多余空行**；4 处顶层定义前均为 2 空行；`ruff format --check` 通过 | **支持** |

**方法前提复核**：执行者承认"`pre-commit run --all-files` 覆盖不到未跟踪的新文件"属实——我确认
`git ls-files` 对 5 个新模块命中 0 个；并按修正后的方法显式传路径重跑（§1.6 #6）全部通过。
因此"第 1 轮 lint --full 未覆盖新模块"这一**自我披露是诚实的**，且已在本轮补齐。

---

## 4. 我找到的缺口 / 不准确项（**全部非阻塞**）

**G1（证据表述不准确）** §9 "`git diff HEAD` 中新增行**零条**匹配 `from backend.(engines|infrastructure)`"。
实测：`src/` 新增行有 **4 条**命中（都是 `backend.infrastructure.config.agent_runner_settings/settings_sources`，**层内**互引），
`tests/` 另有 4 条、`docs/` 1 条。**"零条跨层"成立，"零条匹配"不成立**，建议改为"零条**跨层**新增 import（命中的全是 infrastructure 层内）"。

**G2（证据表述不准确）** §1 / §7.3 / §9 称架构门禁"严格态仍以 `["infrastructure", "engines"]` 把守"。
实测 `hooks/shared/check_architecture.py:41` 是 `"api": ["infrastructure"]`，且脚本 docstring 明示 **`api → engines` 是过渡期放宽**
（即该边**不被禁止**）。这是对门禁强度的**高估**。实质结论不受影响：我实测 `api/` 下 `from backend.engines` = **44 = 44**、真跨层新增 **0**，
拆分没有新增/减少跨层边。建议把这句改成"门禁配置未变（`git diff` 为空），且跨层命中集合不变（44 = 44）"。

**G3（§13 数字口径）** "三轮拆分合计**净减 1717 行**"。实测 1717 是 `git diff --numstat` 对 3 个被拆文件的
**删除行数**（1717），同一 diff 还**新增 142 行**到这些文件：真实净减应为 **1575 行（原始行）**／**1353 行（非空行，2866→1513）**；
新增 5 模块 1633 非空行。建议改为"删除 1717 行、回填 142 行，净减 1575 行（非空行口径 1353）"。

**G4（D-11 计数口径）** 见 §1.3 末注：我按"模块体内定义型顶层名"数到 **60 / 44 / 16**，
PRD 写 **61 / 49 / 12**（12 不含第 2 轮移除的 4 个死别名，合计恰为我数到的 16）。**结论一致、总数差 1**，属计数约定差异，建议补一句口径说明即可。

**G5（第 1 轮 F-3 建议未逐字落实）** 第 1 轮 verifier 建议"在 `RUNTIME_DEPENDENCY_NAMES` 旁注明『打本模块的补丁仅对直接调用有效，派发会覆盖』"。
本轮只把规则写进了 `refresh_runtime_dependencies()` 的 docstring（针对**入口**）与 handlers/orchestrate 的机制说明，
**没有**逐字补"handler 侧补丁会被派发覆盖"的告警。§13 称"已修正或记录"——属"记录"而非"修正"。非阻塞（第 1 轮已定为非阻塞）。

**G6（验证方法陷阱，值得写进规范）** `just lint --full` 会因 `.git/.last_linted_commit` 缓存**直接短路**并打印
`✅ flag valid`，**不跑任何钩子**。我实测命中。所以"`just lint --full` 全绿"这句话本身**不构成证据**——
必须附"钩子实际执行"的日志或直接跑 `pre-commit run --all-files`。执行者的证据里写了"15 个钩子全 Passed"，与真实执行一致，
但建议在证据/验证计划中显式写明该缓存行为（否则复现者会拿到假绿）。

**G7（环境遗留）** `git worktree list` 仍有执行者的 `/private/tmp/keda-rv4-baseline`（第 1 轮 verifier 已建议清理）。
另：rv-6 探针会经真实代码路径写出 `.iar/memory/short_term/keda/123/context.json`（gitignored）。我**未删除**这两项（只读原则）。

> 另记一项**与本 PRD 无关**的既有脆弱性：`tests/test_agent_runner_init.py` 的 2 条用例对终端宽度敏感
> （`COLUMNS=200` 时在**当前树**与**基线树**上同样失败）。建议另行处理，不属于本 PRD 范围。

---

## 5. PRD §9 验收项逐条核对（20 项）

### Human-Confirmed（3）

| §9 条目 | 证据匹配度 | 我的核对 |
|---|---|---|
| 三个 `core/` 拆分边界已确认 + rv-2/rv-3 通过 | **匹配** | 我重算 7 文件行数 8/8 相同；新模块顶层符号与 docstring 与 `rv-3.module-outline.txt` 逐条相同 |
| `cli.py`/`cli_typer.py` CLI 零变化 + cli_typer→cli import 边未断 | **匹配（更强）** | 我覆盖 **56 条命令路径**（非 25 段）逐字节一致；`cli_typer_app.py:28` 的 `from backend.api.cli import _run_parsed_command, error_console` 仍在 |
| daemon 并发顺序未变 + 相关测试全绿 | **匹配** | 全量 2071 = 2071；claim marker / worktree / merge_queue 用例在列并通过 |

### Architecture Acceptance（3）

| §9 条目 | 证据匹配度 | 我的核对 |
|---|---|---|
| 四层依赖方向一致、无新增跨层 import | **实质匹配（措辞见 G1）** | `check_architecture.py` 232 文件 ✅；真跨层新增 0 条 |
| `rg` 命中集合与拆分前一致（44 = 44） | **匹配** | 我实测 `api/` 下 `from backend.engines` = 44 = 44（同口径） |
| `check_architecture.py` 配置未变 | **匹配** | `git diff --stat HEAD` 为空 |

### Behavior Acceptance（3）

| §9 条目 | 证据匹配度 | 我的核对 |
|---|---|---|
| `iar --help` 与全部子命令逐字节一致 | **匹配（更强）** | 56/56 命令路径一致 + 我自己做的标点负控红→绿 |
| HTTP 路由签名 / schema 不变 | **匹配** | 本轮工作区**没有任何 `src/backend/api/**` 改动**（`git status` 逐文件核对），routes 用例全绿 → 契约按构造不变 |
| daemon / loop / 并发测试全绿 | **匹配** | 全量 2071 通过、0 failed |

### Documentation Acceptance（3）

| §9 条目 | 证据匹配度 | 我的核对 |
|---|---|---|
| 新增子模块带 docstring | **匹配** | 5/5 模块 docstring 存在；pin 的 ruff（含 D100–D107）显式 `--files` 通过 |
| 三者表述一致、规范层未修改 | **匹配** | `code-reuse.md`/`CLAUDE.md`/`AGENTS.md` diff 为空；`docs/` 仅新增两个模块的 mkdocstrings 条目与三分说明（mkdocs `--strict` 过） |
| 豁免单最终 0 条 | **匹配** | 文件仅剩 7 行注释；门禁无输出 exit 0；机制仍在（我用临时白名单副本验证 WARNING 降级路径） |

### Validation Acceptance（5）

| §9 条目 | 证据匹配度 | 我的核对 |
|---|---|---|
| `just lint --full` 全绿（15 钩子） | **匹配但方法需注记（G6）** | 我直接跑其内部命令：17 钩子 = 15 Passed + 2 Skipped，EXIT=0；`just lint --full` 本身会短路成假绿 |
| `just test` 全绿（rv-5） | **匹配** | 我用 `pytest -o addopts=''` 全量口径跑：当前 2071 / 基线 2071（我**未**跑 `just test all` 这条 recipe 本身，见 §6） |
| 7 个目标文件全 ≤ 800（rv-2） | **匹配** | 逐数字相同 + 7/7 `--max-lines 800` exit 0 |
| rv-1 negative_control 成立 | **匹配** | 我自造 1101 行文件 → `[ERROR]` exit 1 → 删除后无输出 exit 0 |
| rv-4 negative_control 成立 | **匹配** | 我自己改 `run` help 标点 → diff 非空且精确命中 2 处 → 还原后一致 |

### Delivery Readiness（3）

| §9 条目 | 证据匹配度 | 我的核对 |
|---|---|---|
| 推荐方案完整实现：7 文件拆分 + 白名单清零 | **匹配** | 7 文件全 ≤ 800；`settings.py` 381（≤1000）；白名单 0 条 |
| 无遗留回归；CLI/HTTP/daemon 零变化 | **匹配** | 2071 = 2071；56 命令路径逐字节一致；无 api/ 改动 |
| 与 api-engines 迁移 PRD 不冲突 | **匹配（实质）** | 本 PRD 真跨层新增 0，`api→engines` 44 = 44 存量不动；两份 PRD 的代码文件集不相交（该 PRD 在工作区只有 2 行文档改动） |

**§4 的 G1~G7 不影响上述任何一格的"匹配"判定**——它们都是表述/口径/方法注记层面的问题。

---

## 6. 注入与还原清单（证明"只读"与指纹回原）

| # | 注入 | 位置 | 还原方式 | 还原验证 |
|---|---|---|---|---|
| 1 | 造 1101 非空行文件 | `src/backend/api/_rv1_negctl_tmp.py`（新建） | `rm` | 文件不存在 + 门禁 exit 0 |
| 2 | 移除刷新调用 | `blocked_continue.py:121` → `pass` | Edit 反向改回 | 探针回 True + 指纹回 `ae0e2ade…` |
| 3 | help 标点 `.`→`!` | `cli_typer_runner.py:86` | Edit 反向改回 | 单命令 help 对比回"一致" + 指纹回原值 |
| 4 | 删 4 个冗余别名 import | `settings.py:60-76` | Edit 反向改回 | 4 别名可达 + 3 个消费者模块导入 OK + 指纹回原值 |
| 5 | 临时白名单副本 | `/tmp/rv1-allowlist-copy.txt`（仓库外） | `rm` | 仓库白名单文件未被改（指纹含 `hooks/`） |
| 6 | 临时 worktree | `/tmp/verify2-baseline` | `git worktree remove --force` | `git worktree list` 只剩本仓与执行者遗留的一个 |

**没有修改**（只读确认）：`tasks/` 下除本报告外的任何文件（含 PRD）、`hooks/`、`tests/`、`docs/`、`.pre-commit-config.yaml`、`justfile*`。
`git status --porcelain` 全程 27 条（与审查开始时相同）。

---

## 7. 我**没有**验证的部分（明确披露）

1. **真实 `iar daemon` / `iar run` / `iar blocked-continue` 连 GitHub / LLM 的端到端**：无凭据、且会产生远端不可逆副作用。
   rv-6 驱动的是**用例层真实入口** `blocked_continue_issue`（比 CLI 低一层）；`CLI → blocked_continue_issue` 的接线我只做了静态核对
   （`src/backend/api/cli_parsed_commands/runner.py:268/288` 的真实 import 与调用）。
2. **daemon 进程重启以载入新代码**：未做。
3. **CLI 子命令的实际执行路径**：rv-4 只覆盖 `--help`（我覆盖了 56 条命令路径的 help）；非 help 行为由 2071 全量测试间接覆盖。
4. **本轮整改前的历史状态**：`agent_runner_attempt.py` / `agent_runner_settings.py` 等是 untracked，无 VCS 历史，
   故 §3 的 ②③ 只能判定为"**在当前树上不可复现**"，无法判定外部复审当时看到的是哪一版文本。
   同理 ③ 的 docstring 笔误我只能确认"当前文本无重复列举"。
5. **`just test`（testmon 增量口径）与 `just test all` recipe 本身**：我按全量 `pytest -o addopts=''` 跑，
   未跑 testmon 增量路径，也未评估"增量绿"。
6. **前端 / `tests/playwright-e2e/`**：本 PRD 声明无影响，我未运行（改动文件列表里确无前端与 e2e 文件）。
7. **Windows / 多平台分支**：未验证。
8. **性能 / 内存等非功能指标**：未测量。
9. **执行者的 rv-5 负控**（删"合并 attempt 履历"分支 → 3 条用例失败）：我未重做，只阅读了其日志（3 条 fallback 用例失败、日志含
   `switching to next agent`，逻辑自洽）。我自己的负控覆盖了 rv-1 / rv-4 / rv-6 与两处整改的反证。

---

## 8. 复现脚本与产物（均在 `/tmp`，未写入仓库）

- `/tmp/verify2-cli-help-diff.py` — 递归枚举命令树 + 两树逐字节 help 对比（56 条路径）
- `/tmp/verify2-one-help.py` — 单命令 help 对比（rv-4 负控用）
- `/tmp/verify2-presplit-semantics.py` — "handler 从谁的命名空间解析协作者"三态核对（基线 vs 当前）
- `/tmp/verify2-pytest-current.log`、`/tmp/verify2-pytest-baseline3.log` — 全量测试日志（2071 / 2071）
- `/tmp/verify2-lint-full.log`、`/tmp/verify2-lint-reuse.log`、`/tmp/verify2-mkdocs.log` — 门禁日志
- `/tmp/verify2-files-before.txt` / `-after.txt` — 9 个文件显式 lint 前后的 sha256（无变化）
- 另：AST 审计（补丁点 × 同步名单交叉、旧模块残留补丁扫描、settings 别名消费者穷举、不可达名字穷举）以 `uv run --no-sync python - <<'PY'` 内联执行，输出见本报告引用处。

> 结论重申：**PASS**。7 处不准确项（G1~G7）建议在归档前一并修正表述；其中 G1/G2/G3 是 PRD 与证据文件的**事实性措辞**，
> G6 是验证方法注记，G5/G7 是遗留建议与环境卫生。它们都不涉及代码、行为、行数门禁或测试结论。
