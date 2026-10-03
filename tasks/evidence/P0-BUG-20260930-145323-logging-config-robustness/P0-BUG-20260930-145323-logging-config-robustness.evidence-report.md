# Evidence Report · 日志配置的健壮化与长驻进程跨天轮转（Issue #175）

- PRD：`tasks/pending/P0-BUG-20260930-145323-logging-config-robustness.md`
- Worktree：`/Users/zata/code/keda/.iar-worktrees/issue-175`（分支 `issue-175`，基线 HEAD `f0af2d60`）
- 原始证据：`.iar/evidence/`（worktree 本地，被 `.gitignore` 排除；本次交付为 CLI 文本捕获，**没有截图类视觉工件**）
- 结构化清单：`.iar/evidence/evidence.json`（`version: 1` / `language: "zh-CN"`，8 个 item）
- 采集脚本：`.iar/evidence/scripts/`（RV 脚本一律不进代码 diff）

## 人审导航 / Human Review Navigation

这一节就是你只需要看一次的部分。四份呈递物都是纯文本终端捕获，**本地文件**（worktree 内，未进版本库），用 `open` 直接看。执行器已交叉核对：每份末尾均为 `ITEM rv-<n> check failures: 0` 且全文无 `CHECK FAIL` 行。

### 1）坏日志级别不再让进程起不来（rv-1，需要你看）

- 文件：`/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-1-invalid-level.txt`
- 打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-1-invalid-level.txt"`
- 10 秒自检：RED 段那条 `LOG_LEVEL=bogus uv run iar agent doctor claude --json` 的 `[exit_code=1]` 与 `AttributeError: module 'logging' has no attribute 'bogus'`；Green 段同一条命令变成 `[exit_code=0]` 并出现这一行 —— `2026-09-30 15:36:28 - backend.infrastructure.logging.logger - WARNING - logger.py:190 - 无效日志级别 'bogus'，已降级为 INFO`。
- 期望值变化：`LOG_LEVEL` 手误时 `iar` 的退出码从 **1 → 0**，且警告行从 **不存在 → 存在**（终端与当天日志文件各一份）。

### 2）长驻 daemon 跨天把日志写进当天文件（rv-2 + rv-7，需要你看）

- 文件 A：`/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-2-daily-rotation.txt`
- 文件 B：`/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-7-daily-file.txt`
- 打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-2-daily-rotation.txt" "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-7-daily-file.txt"`
- 10 秒自检（A）：绿跑 `dir_listing` 里有两个文件 `app-2026-09-30.log` 与 `app-2026-10-01.log`，前者只含 `line before rollover`、后者只含 `line after rollover`，`handler_opened_file` 跨天后指向次日文件；红跑里只有一个文件且两行都写在里面（`EXPECT FAIL: 跨天后没有产生 app-2026-10-01.log`）。没有出现 `app.log.<date>` 这种标准库默认后缀形式。
- 10 秒自检（B）：真实 `iar` 跑完后目录里出现 `app-<今天>.log`（`CONVENTION OK`）；补一条会写日志的真实入口后，同一文件变成 108 字节并含 `2026-09-30 15:44:03 - app - ERROR - cli.py:221 - iar failed: Repository 'no-such-repo' not found in config.`。

### 3）三个待你拍板的决策（含逐项证据与回复格式）

- 清单：`/Users/zata/code/keda/.iar-worktrees/issue-175/tasks/evidence/P0-BUG-20260930-145323-logging-config-robustness/human-review-checklist.md`
- 交互版（每题一次点选，答案存本机，最后生成可复制回执）：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/tasks/evidence/P0-BUG-20260930-145323-logging-config-robustness/human-review-checklist.html"`
- 回复方式：每题回答 `同意` / `改成 X` / `有差异：<说明>`；也可以一句"三题都同意，呈递已看过"。
- **⚠️ 第 4 轮新增，请你在决策二上多停 10 秒**：FR-1 原文"两种情况都必须输出一条明确的 **WARNING**"与交付行为在 `LOG_LEVEL=error` / `critical` 两档不符——那条归一化提示按 `max(生效级别, WARNING)` 发出（生效级别 ≤ WARNING 时逐字仍是 WARNING；更高时按生效级别发，否则会被 `_setup_logger()` 刚设下的门槛吃掉、那两档重新变静默）。PRD 已把 FR-1 更正为"明确可见的提示——默认 WARNING，生效级别更高时按生效级别发出"，取舍见 PRD §13 D-14 与 `Final Reconciliation`，属措辞精确化、可见性承诺未削弱。若你更看重"级别必须是 WARNING"这个字面，请在决策二勾"有差异"，实现可改回固定 WARNING（代价是那两档看不见）。当场验一眼：`env -u PYTHONPATH LOG_LEVEL=error LOG_DIR=/tmp/k175 LOG_FILE=/tmp/k175/app.log uv run iar agent doctor claude --json` → 终端与 `/tmp/k175/app-<今天>.log` 各出现一次 `日志级别 'error' 不是 logging 预定义名，已按 ERROR 解析`，退出码 0（本报告作者已实跑复核）。

### 4）PR 与 CI 链接

- 本报告的交付边界到"执行器完成实现 + 证据 + PRD 更新"为止；Draft PR、PR evidence comment 与 CI 结果由 runner 在本报告之后创建/登记，届时同一批呈递物会进 PR（PR 即最终人审面）。当前 worktree 无 PR 链接，因此这里给出的是本地审阅包，而不是替代 PR 的静默验收。

## 改动摘要（执行器层）

| 文件 | 改了什么 |
|---|---|
| `src/backend/infrastructure/logging/logger.py` | 新增 `daily_log_dir()` + `daily_log_path(log_dir=None)`（日文件的**目录与文件名**唯一产出点）、`_resolve_log_level()`（返回 `exact / normalized / invalid` 三态：合法名不吭声、大小写/空格手误按原意生效并提示、彻底无法解析才降级为 INFO + 警告，全程不抛异常）与 `_log_level_resolution_notice()`、`_DailyFileHandler`（跨天重开当天文件 + 顺带清理，带 `_keda_handler` 私有标记）、`_remove_keda_handlers()` + 模块级 `_installed_keda_handlers` 清单（幂等只清自己，且 root 被外部清空过时也不泄漏旧日文件句柄）；`_cleanup_old_logs` 由方法降为模块内私有函数；保留天数改由 `config.log_retention_days` 提供；降级/归一化提示在 handler 挂好后发出；日切失败（只读挂载、权限变更、fd 耗尽）时先开新流再关旧流，失败就保留旧文件继续写并按目标日期只提示一次，不把异常抛回业务调用 |
| `src/backend/infrastructure/config/settings.py` | 新增 `log_retention_days`（默认常量 14）+ 两级字段校验：`mode="before"` 把无法解析成整数的值（如 `abc`）交回默认 14，`mode="after"` 把非正数回退为 14（env `LOG_RETENTION_DAYS`、TOML `[app]`），保证这个新键自己不会成为启动崩溃源 |
| `src/backend/engines/agent_runner/factory.py`、`src/backend/core/use_cases/agent_runner_factory.py` | 按既有转出链 re-export `daily_log_path`（api 层禁止直连 infrastructure，见 `hooks/shared/check_architecture.py`） |
| `src/backend/api/cli_registry.py` | `_print_logs_fallback` 改为调用无参的 `daily_log_path()`（目录也取自日志模块），删除自行拼日文件名的 `f"app-{today}"`、`resolve_project_root_path()` 推导与多余的 `datetime` 导入；`LOG_FILE` 被覆盖时回退提示跟着走，不再指向没人写的文件 |
| `tests/test_logger.py` | 保留并改写既有 4 个用例（FileHandler 子类断言、命名约定、root 挂载、清理边界），新增 fail-soft 与归一化提示（含高生效级别下提示不被门槛吃掉、空格手误同样出声）/ 跨天轮转（含相对 `LOG_FILE` 的绝对锚定）/ 日切失败兜底（开新流失败、旧流收尾失败两条路径）/ 保留天数配置化（含 `LOG_RETENTION_DAYS` 这个 env 名接到 handler）/ 幂等与句柄收尾 / 路径与目录单点 / 正常路径格式不变共 28 个新增用例（合计 32 passed） |
| `tests/test_cli_registry.py` | 两个 `iar logs` 回退用例改拨日志模块时钟（日文件名已由共享函数产出），不再拨 CLI 自己的时间串 |
| `config.toml`、`.env.example`、`docs/guides/configuration.md`、`docs/guides/agent-runner.md` | `[app]` 段 `# log_retention_days = 14` 注释模板与 `.env.example` 的 `# LOG_RETENTION_DAYS=14`；配置项表说清级别手误的两种提示、非整数保留天数的回退、`LOG_DIR` 不移动日文件（落点由 `LOG_FILE` 决定）；日志特性补跨天切换/日切兜底/第三方 handler 共存；`agent-runner.md` 的 daemon 按天轮转一行与原先写死"14 天保留期"的句子改为按 `log_retention_days` 表述 |

## 八项 oracle 结果（红→绿）

| id | 结论 | 关键观察 | 负控（让它变红的路） | 变红时的样子 |
|---|---|---|---|---|
| rv-1 | 绿 | `LOG_LEVEL=bogus` 真实 CLI `exit_code=0` + `无效日志级别 'bogus'，已降级为 INFO`（终端 + 日文件各一份）；小写 `info` exit_code=0、级别按 INFO 生效并出现 `不是 logging 预定义名，已按 INFO 解析`；空值降级并警告；`LOG_LEVEL=error` 与 `LOG_LEVEL="  INFO  "` 两段同样 exit_code=0 且提示可见（前者按 ERROR 生效并出声） | ① `PYTHONPATH=<HEAD 源码副本>` 跑同一条真实命令；② 副本把提示级别固定回 `logging.WARNING` 后跑 `LOG_LEVEL=error`；③ 副本改回"先 strip 再比较"后跑 `LOG_LEVEL="  INFO  "` | ① `[exit_code=1]` + `AttributeError: module 'logging' has no attribute 'bogus'`；②③ 输出里找不到“不是 logging 预定义名”，且对应用例 `1 failed` / `2 failed` |
| rv-2 | 绿 | 跨天后产生第二个文件，两个文件名都匹配 `^app-\d{4}-\d{2}-\d{2}\.log$`，各只含本天日志行；`pytest -k 'rotation or daily'` 6 passed 全绿；全新目录复跑结果一致 | HEAD 副本（固定 baseFilename 的 `FileHandler`）跑同一 demo | `EXPECT FAIL: 跨天后没有产生 app-<次日>.log` + `EXPECT FAIL: 跨天后的新行仍写在第一天的文件里` |
| rv-3 | 绿 | `LOG_RETENTION_DAYS=3` 时 2 天前文件在日切后被删、1 天前保留；`=5` 时同一文件保留（唯一变量是配置值）；未配置默认 14，非正数与无法解析（`abc`）都回退 14 且进程正常启动 | HEAD 副本（无该配置字段、清理只在启动一次、写死 14） | `log_retention_days : <no such field>` + `EXPECT FAIL: app-<旧日期>.log 应已被按保留天数清理，但仍然存在` |
| rv-4 | 绿 | `FORMATTER IDENTICAL TO HEAD`；`32 passed`；真实链路 INFO/DEBUG/WARNING → root_level 20/10/30；真实 CLI 落盘行仍为 `时间 - logger - 级别 - 文件:行 - 消息` | 副本里把 `fmt` 改短 → 格式用例；去掉级别名 `.upper()` 归一化 → 小写级别用例 | `1 failed`（格式化行与 HEAD 串不一致 / 小写级别被判为非法） |
| rv-5 | 绿 | `rg` 显示 `f"app-` 在 `src/backend` 只命中 `daily_log_path()` 一处；真实终端 `IAR_CONFIG=<隔离副本> LOG_FILE=<tmp>/logs/app.log uv run iar logs --repo-id keda --kind daemon` 打印 `Global app log: <tmp>/logs/app-<今天>.log`，而 `ls` 显示本次运行真正写的就是这个文件；进程内 `shared_function : True`（infrastructure == engines/core == api 同一对象）且 `expectation_summary : PASS` | ① 同一条真实终端命令在 HEAD 副本上跑：提示指向 `<HEAD 副本>/logs/app-<今天>.log`，与本次真正写入的临时目录分叉；② HEAD 副本跑跨层身份用例；③ 副本里把回退提示改成另一套文件名再跑逐字比对 | ① `ImportError: cannot import name 'daily_log_path'`（exit_code=2）；② `EXPECT FAIL: 回退提示与共享函数产出不同` |
| rv-6 | 绿 | root 预置第三方 handler 时 `keda_handler_count : 2`、`daily_files_opened : ['app-<今天>.log']`、`third_party_still_attached : True`；二次 setup 后仍为 2（不叠加）；另有只读目录红/绿对照：改动前 `PermissionError` 抛回业务调用，现在只提示一次并继续写旧文件；日目录绝对锚定的红/绿对照：副本去掉 `os.path.abspath` 后日切把新文件开到当时的 cwd | ① HEAD 副本恢复"root 非空即整体跳过"跑同一 demo；② 副本里删掉已跟踪 handler 的收尾再跑重建单例用例 | ① `keda_handler_count : 0` + `daily_files_opened : []` + `EXPECT FAIL: root 非空时 keda 没有挂上文件 handler（旧行为）`；② `AssertionError: 旧的日文件句柄必须被关闭，否则每次重建都漏一个 fd` + `1 failed` |
| rv-7 | 绿 | 真实 CLI 在临时 `LOG_DIR` 下生成 `app-<今天>.log`（`CONVENTION OK`，无 `app.log.<date>`）；同目录另一真实入口使该文件非空并含本次运行日志行；默认配置下 `logs/app-<今天>.log` 就是回退提示指向的文件 | 副本里把 `daily_log_path` 的命名改坏（`app_<日期>.log`）后跑真实 CLI | `CONVENTION BROKEN`，目录里只有非约定文件名 |
| rv-8 | 绿 | `config.toml [app]` 有 `# log_retention_days = 14`；`configuration.md` 有键说明（含 `LOG_RETENTION_DAYS`、默认值、非正数回退、跨天清理、第三方 handler 共存）；`mkdocs build --strict` 通过 | 对 `git show HEAD:` 的 config.toml / configuration.md 跑同一条 `rg` | rg 无命中，`exit_code=1` |

## 门禁复跑结果

```bash
# 全部命令都在 worktree 根目录执行（pyproject 已把本树 src 钉在 sys.path[0]，不再依赖剥 PYTHONPATH）
uv run pytest -o addopts='' tests/test_logger.py -q                                              # 32 passed
uv run pytest -o addopts='' tests/test_logger.py tests/test_cli_registry.py -q                 # 53 passed
uv run python hooks/shared/check_architecture.py                                               # 架构依赖方向全部合法，无违规
just test                                                                                     # 内含 just lint --full 通过；最终树上增量档 502 passed / 907 deselected
just test all                                                                                  # 第 6 轮最终树（PRD 文档改动后复跑）：2693 passed, 1 skipped，exit 0，两条 flag 均已刷新
uv run pytest tests/ -q --no-header -o addopts=''                                               # 关掉 --testmon 的全量档：2693 passed, 1 skipped
uv run mkdocs build --strict                                                                   # Documentation built in …, exit 0
```

> 环境注记（重要）：本会话继承的 `PYTHONPATH=/tmp/keda-cli-src/src` 指向一份**改动前**的源码副本，会排在 `sys.path` 前面并顶掉 worktree 代码——上一轮 runner 门禁正是因此以 `ImportError: cannot import name 'daily_log_path'` 失败（且那一次 2662 个用例其实全部跑在副本上）。现已由 `pyproject.toml` 的 `[tool.pytest.ini_options] pythonpath = ["src"]` 在仓库侧消除：pytest 加载任何 conftest 之前就把本树 `src` 钉到 `sys.path[0]`，本报告里的每一条结果都来自 worktree 代码（`backend.__file__` 已核对为 `.../issue-175/src/backend/__init__.py`）。
>
> 副作用（采证时必须知道）：同一枚钉子也压过了证据脚本"用 `PYTHONPATH` 覆盖层跑红"的机制，因此 `.iar/evidence/scripts/` 下所有 pytest 红跑统一带 `-o pythonpath=''`；不带这个开关的红跑会跑到本树代码而**假绿**。`.iar/` 被 `.gitignore` 排除，属本地证据工具。

## 独立验收后的加固（第 1 轮 verifier 的 8 条 findings）

第 1 轮独立验收（`.iar/evidence/verifier-findings.md`，绑定当时的树指纹）给出 `PASS WITH FINDINGS`：1 个 [MAJOR] + 6 个 [MINOR] + 1 组正向确认。逐条处置如下，均已重跑证据：

| finding | 处置 | 现在的证据位置 |
|---|---|---|
| [MAJOR] 日切开不出新文件会把 `PermissionError` 抛回业务调用 | **已修**：`_roll_to_new_day` 先开新流、失败回滚并保留旧流继续写，按目标日期只提示一次，目录恢复后自动补上切换；新增 `test_rollover_failure_does_not_raise_into_caller` | rv-2 的只读目录红/绿段 + `-k 'rollover_failure_does_not_raise'` |
| [MINOR] 小写级别被静默接受，与 FR-1 字面（要出警告）矛盾 | **已修**：解析结局扩为 `exact / normalized / invalid` 三态，大小写/空格手误按原意生效 **且** 出一条归一化提示；错拼/空值仍是降级 + 警告 | rv-1 的 `LOG_LEVEL=info` 段 + `test_lowercase_level_resolves_with_normalization_notice` |
| [MINOR] `LOG_RETENTION_DAYS=abc` 会让任意命令启动即崩（新键自带的新崩溃源） | **已修**：`mode="before"` 兜底回退默认 14（非正数仍由 after 校验器回退）；PRD §13 新增 D-12 记录该取舍与其"静默"残留 | rv-3 的 `abc` 绿段 + 去兜底红段（`ValidationError`） |
| [MINOR] FR-4 只收敛了文件名，目录仍是两份约定（`LOG_FILE` 覆盖时提示指向不存在的文件） | **已修**：新增 `daily_log_dir()` 作为目录单点，`daily_log_path()` 缺省取它，`cli_registry` 改调无参版本；PRD §13 新增 D-13 | rv-5 的真实终端红/绿对照 + `test_cli_registry_fallback_follows_configured_log_dir` |
| [MINOR] 文档残留：`agent-runner.md` 仍写"14 天保留期"；`.env.example` 缺该键；`configuration.md` 把 `log_dir` 说成日志目录 | **已修**：三处全部更正（保留期改为按 `log_retention_days` 表述、`.env.example` 增加 `# LOG_RETENTION_DAYS=14`、表中明确"单独设置 `LOG_DIR` 不会移动日文件"） | rv-8 + `rg -n "14 天" docs` |
| [MINOR] rv-5 的打桩其实可以避免（存在真实的 `IAR_CONFIG` 隔离点） | **已修**：rv-5 改用真实终端命令 + `IAR_CONFIG` 临时副本，原披露撤销 | rv-5 红/绿两段 |
| [MINOR] `evidence.json` 里引用了会漂移的 `logger.py:190` 行号 | **已修**：manifest 中不再出现源码行号 | `.iar/evidence/evidence.json` |

第 2 轮独立复核（针对加固后的最终树）结论见 `P0-BUG-20260930-145323-logging-config-robustness.verifier-report.md`。

## 已披露的限制与取舍（不掩盖）

1. **rv-5 的回退分支已升级为真实终端验证**：用仓库自带的 `IAR_CONFIG` 隔离点指向 `~/.iar/config.toml` 的临时副本（只把 `[agent_runner.console]` 的 `history_db_path` / `process_registry_path` / `process_log_dir` 三项改到 /tmp），`iar logs --repo-id keda --kind daemon` 就真的走到"没有进程日志记录"分支；不写 `~/.iar`，副本用完即删（其中含凭据，因此从不打印其内容，证据里只有 CLI 打印的路径行）。红跑用同一条真实命令 + HEAD 源码副本，直接复现"提示与真正写入的文件分叉"。进程内的 `path_compare_check.py` 与单测仍把同一个存储依赖打桩为空，但被调用的始终是真实的 `_print_logs_fallback` 与真实的 `daily_log_path`；`tests/test_cli_registry.py` 的两个回退用例同步转绿。
2. **rv-7 的"文件非空"**：`iar agent doctor claude --json` 这条真实入口本身不产生任何日志记录（改动前后一样，文件会被创建但为 0 字节）。因此"非空 + 含本次运行日志行"用同一临时目录里的另一个真实入口 `iar --repo-id no-such-repo daemon status`（未注册仓库分支，按既有实现记一条 ERROR）来证明；没有伪造日志内容。
3. **`LOG_RETENTION_DAYS` 非整数取值是静默回退**：`abc` 这类值回退默认 14 且不额外警告（配置装载早于日志就绪，日志系统无法报告自己），已在 `docs/guides/configuration.md` 写明；该键的可发现性由 `config.toml` 注释模板、`.env.example` 的 `# LOG_RETENTION_DAYS=14` 与文档三处覆盖（rv-8）。
4. **交互版验收页未做浏览器点击测试**：本 worktree 没有可用的无头浏览器工具；已做 `node --check` 语法校验与 DOM id 接线核对（`wizard/progress/final/result/copyBtn/editBtn` 全部存在），并保留内容一致的 markdown 版作为静态底稿。
5. **决策三的可见副作用**：root 已被 uvicorn / 测试框架占用时，同一条记录会同时进入对方 handler 与 keda handler（期望行为），已写进 `docs/guides/configuration.md` 以免被当成重复日志。

## 第 3 轮（runner 门禁失败后的恢复）：一处验证基础设施缺陷 + 三处实现缺陷

runner 的第 1 次尝试在 stage 前门禁 `just test all` 上失败：`ImportError: cannot import name 'daily_log_path' from 'backend.infrastructure.logging.logger' (/tmp/keda-cli-src/src/backend/...)`。复查后确认这是两类问题叠加，逐项修复并重跑了全部证据：

| 问题 | 性质 | 处置 | 红→绿证据 |
|---|---|---|---|
| 继承来的 `PYTHONPATH=/tmp/keda-cli-src/src` 让 2662 个用例**全部**跑在一份改动前的源码副本上（只有导入新符号的 `test_logger.py` 在收集期炸出来） | 验证基础设施（门禁结论一度不可信） | `pyproject.toml` 新增 `[tool.pytest.ini_options] pythonpath = ["src"]`，pytest 在加载 conftest 前把本树 `src` 钉到 `sys.path[0]` | 红：修复前 `pytest tests/test_logger.py --collect-only` 在污染环境下 `ImportError` + `exit 2`；绿：同一命令 32 collected，`just test all` → `2693 passed, 1 skipped` / exit 0（污染环境与 clean 环境结果一致） |
| `LOG_LEVEL=error` / `critical` 时归一化提示被刚设下的 root 门槛吃掉，FR-1 的"两种情况都必须可见"在这两档落空 | 实现缺陷（MAJOR） | 提示改按 `max(生效级别, WARNING)` 发出 | rv-1：真实 CLI `LOG_LEVEL=error` 绿跑出提示；副本固定回 `logging.WARNING` 后同一命令无提示 + `2 failed` |
| `"  INFO  "`（只有空格）被判为 `exact`、完全不出声，与 §7"大小写 / 空格需要归一化 → normalized"不符 | 实现缺陷（MINOR） | 归一化判定改为"原始串 vs `strip().upper()` 串"比较 | rv-1：真实 CLI 绿跑出提示；副本改回"先 strip 再比较"后无提示 + `1 failed` |
| `LOG_FILE` 配成相对路径时日切把 `baseFilename` 重设为相对路径，daemon `chdir()` 后新文件与保留清理都按"当时 cwd"解析 | 实现缺陷（MINOR，长驻进程真实形态） | `daily_log_dir()` 用 `os.path.abspath` 锚定（不解析符号链接） | rv-2：`test_rollover_stays_anchored_when_cwd_moves` 绿；副本去掉 abspath 后 `1 failed`（`is_absolute`） |

配套：`__all__` 补 `daily_log_dir`；本 PRD 新增/改动函数的 docstring 统一为中文（仓库标准）；`tests/test_logger.py` 用例 27 → 32；八项证据全部在最终树上重采，`.iar/evidence/evidence.json` 的每条 `stdout_assertions` 已对新文件逐条复核。

## 第 4 轮（runner 交付门禁失败后的恢复）：纯文档与验证基础设施，无代码改动

runner 的第 2 次尝试在 stage 前交付检查失败：`Canonical PRD Change Log is incomplete ... entry 1: 审核`。原因是 PRD §14 的第 3 轮条目只写了五个字段，缺 Machine Contract v3 要求的 `审核/Review`——按契约"缺任一字段即视为不完整条目"，整份 PRD 的交付检查判红，与实现无关。本轮处置：

| 项 | 性质 | 处置 | 复核方式 |
|---|---|---|---|
| §14 第 3 轮条目缺 `审核` 字段 | PRD 结构合规 | 补上该字段，如实写明自验范围（32 passed、门禁 exit 0、八项证据重采时间戳晚于最后一次源码改动）与真实缺口（尚未经独立 verifier 复核） | 六字段解析脚本复跑：§14 现 6 个条目 × 6 字段全部命中；`check_prd_acceptance_checklist.py` 由 pending 模式与 archive-ready 模式各跑一次 |
| `.iar/evidence/evidence.json` 是否符合结构化 manifest 要求 | 证据 manifest | 无需改动：8 个条目 `item_number` 均为正整数、`evidence_files` 均为纯文件名且文件存在、`negative_control` / `expected_fail` 均非空、`stdout_assertions` 每条 pattern 都能在对应 `rv-*.txt` 按 `must_match` 复现（含 `must_match: false` 的反向断言），且无 `|| true` 之类兜底 | 逐条脚本比对 manifest ↔ 证据文件；顶层 `version: 1` / `language: "zh-CN"` 已声明 |
| 被测源码是否仍被 `PYTHONPATH` 副本顶掉 | 门禁可信度 | 第 3 轮的 `pythonpath = ["src"]` 仍在生效 | pytest 内断言 `sys.modules["backend.infrastructure.logging.logger"].__file__` == `.../issue-175/src/backend/infrastructure/logging/logger.py`（通过）；对照实验：同一断言写在 `/tmp` 下跑会失败并显示 `/tmp/keda-cli-src/...`，说明断言本身能判别错位 |
| FR-1 措辞与交付行为不符 | PRD 叙述与实现不一致 | 新增 `Final Reconciliation` 叙述复核并据此更正 FR-1（"一条明确的 WARNING" → "明确可见的提示——默认 WARNING，生效级别更高时按生效级别发出"），取舍记为 §13 D-14，D-11 同步；§9.1、人工审阅清单（md + html）与本导航同时披露给审阅者 | 复跑 `env -u PYTHONPATH LOG_LEVEL=error LOG_DIR=/tmp/k175 LOG_FILE=/tmp/k175/app.log uv run iar agent doctor claude --json` → `EXIT=0`，stdout 命中 1 次、`/tmp/k175/app-2026-09-30.log` 命中 1 次（该档的副本变异红跑见 rv-1） |
| 门禁 | 复跑 | 全部文档改动落定后复跑 | `just test all`（内含 `just lint --full`，本轮 flag 失效、真实执行）→ `2693 passed, 1 skipped`，exit 0；`uv run mkdocs build --strict` exit 0；`hooks/shared/check_architecture.py` "架构依赖方向全部合法，无违规" |

源代码、测试与 `.iar/evidence/rv-*.txt` 证据本轮均未改动，因此八项 oracle 的红→绿证据继续沿用第 3 轮重采的版本。

## 第 5 轮（runner 交付门禁：分组标签语法）与第 6 轮（agent 中断 + RV 命令复跑）

### 第 5 轮 · §9.2 分组标签改为标题（纯 PRD 格式）

runner 的第 3 次尝试在 stage 前门禁报 `Acceptance Checklist has unchecked items ... L466–L469`，把 4 项 Human-Confirmed 误判为"执行器可完成却未完成"。根因是分组标签当时是粗体段落，而仓库解析器 `src/backend/core/shared/prd_checklist.py:91-108` 只按 `###`–`####` 标题识别 `Human-Confirmed` 分组。处置是把 §9.2 的七个分组标签统一改成 `####` 标题（括号说明另起一行），**不改任何勾选状态**。本轮无代码/证据改动，判据在下方第 6 轮的解析器复跑里（`execution_unchecked: 0` / `human_pending: [468,469,470,471]`）。

### 第 6 轮 · 证据命令改为可被 runner 原地复跑

runner 的第 4 次尝试报的是 `Agent command failed before runner verification could start`（exit 1）——实现者没跑完就中断，**门禁根本没开始，因此没有任何结论与当时的证据状态绑定**。接手后定位到的真实缺口是：证据脚本刚被改为收尾时把本 item 的证据文件回显到 stdout（`surface_item`），但改完从未复跑，磁盘上的 8 个 `rv-*.txt`（20:59–21:14）早于该改动（21:49）。这个改动是必需的：runner 的 `ensure_validation_commands_pass`（`src/backend/core/use_cases/agent_runner_validation.py:706`）会用 `bash -lc` 在 worktree 里**原地复跑** manifest 的每条 `command`，先要求退出码 0（预算 `validation.reexecute_timeout_seconds=300`），再对**复跑自身的 stdout/stderr** 逐条执行 `stdout_assertions`（`_validate_stdout_assertions`，同文件 `:676`）。只写文件不回显的脚本复跑时 stdout 为空，断言必红——正是"退出码 0 不等于检查点成立"。

| 项 | 性质 | 处置 | 复核方式 |
|---|---|---|---|
| 8 条 manifest `command` 是否真能被 runner 复跑变绿 | 证据门禁 | 用仓库自己的判据复刻该门禁（`/tmp/k175_rv_gate.py`：`extract_realistic_validation_items` → `validate_evidence_manifest` → 逐条 `bash -lc <command>` → `_is_stdout_assertion_satisfied`），并在本轮全部文档改动落地后**再复跑一次** | 两轮均 `manifest OK, items: 8` + 8 条全部 `rc=0` + 37 条 `stdout_assertions` 全部满足 + `TOTAL FAILURES: 0`；首轮耗时 25.5 / 22.0 / 8.1 / 10.1 / 7.1 / 3.2 / 2.7 / 5.7 秒，末轮 12.6 / 12.0 / 3.4 / 4.8 / 4.4 / 2.8 / 2.7 / 5.6 秒（最慢一条远在 300 秒预算内）；两轮逐项计数完全一致 → 命令是可反复复跑的观察，不是一次性表演 |
| Issue 侧门禁前提是否成立 | 门禁可信度 | 直接从 GitHub 取 Issue 正文喂给同一套解析函数 | labels 含 `source/prd` → `validation_required=True`；marker `version=1 language=zh-CN`；清单解析出 8 条 rv-1…rv-8；证据目录解析为 `.iar/evidence`（legacy 扁平语义，不分任务子目录） |
| 重采后的 8 个证据文件是否仍各自成立、是否串内容 | 证据卫生 | 在最终树上重采两轮，末次 23:16–23:17（晚于最后一次源码改动 `logger.py` 20:43；八个文件的 `working_tree_fingerprint` 同为 `1197e596…`） | 逐文件 `CHECK PASS` 计数 23 / 22 / 17 / 13 / 16 / 12 / 7 / 11，`CHECK FAIL` 全为 0，各含自己的 `ITEM rv-N check failures: 0`；严格污染检查：没有任何文件出现别的 item 的 `ITEM rv-N` 行、别的 item 的文件名或别的 item 的标题 → `strict contamination: CLEAN`；无孤儿证据文件 |
| manifest 叙述与重采证据是否漂移 | 证据一致性 | 逐条对账可核对的量化叙述 | item 1 的"全部 23 条 CHECK 为 PASS"与实际 23 一致；§9.1 引用的 rv-7 `108` 字节与 `… - app - ERROR - cli.py:221 - …` 那行真实日志在新证据里仍在（`rv-7-daily-file.txt:28-29`）；按 `must_match` 对磁盘文件复算全部 37 条断言，无一漂移 |
| rv-8 两处 `\|\| echo` 是否属"把失败刷绿" | 需披露的判读 | 不是：这两条是**反向断言**（不该有未注释的 `LOG_*` 赋值、不该残留写死的"14 天"）。grep 无命中时退出码为 1，故用 `|| echo 'NO ACTIVE LOG_* ASSIGNMENT'` / `'NO STALE 14-DAY CLAIM'` 把"什么都没找到"转成可断言的正向标记，再 `assert_pattern present` | 判别力方向相反：若真出现违规行，grep 会打印那一行、标记不出现，检查随即变红；全仓证据脚本 `grep '|| true'` 无命中 |
| PRD 结构合规 | 交付门禁 | 新增 §14 第 6 轮条目（六字段）+ §9.2 `Validation Acceptance` 新增一条"command 可被复跑并全绿"的 `[x]` | `parse_prd_change_log` → 8 条目、`incomplete_entry_fields` 为空；`parse_prd_checklist` → `execution_unchecked: 0`、`human_pending: [468,469,470,471]`（4 项人审仍为 `[ ]`，未代答）；prd skill 结构检查的告警仅剩同样这 4 项空框 |

| 门禁 | 复跑 | 全部文档改动落地后复跑 | `just test all`（内含 `just lint --full`）→ `2693 passed, 1 skipped`，exit 0，`.last_tested_commit` / `.last_linted_commit` 两条 flag 均刷新到当前 HEAD；`just lint` → exit 0；提交期钩子 `hooks/shared/check_prd_acceptance_checklist.py` 对本 PRD → exit 0；`hooks/shared/check_architecture.py` → "架构依赖方向全部合法，无违规" |

源代码、测试、`config.toml` 与 `docs/` 在本轮一字未动（改动面限于本 PRD 的 §9.2 / §14 与本报告，加上被本地排除的 `.iar/evidence/`），八项 oracle 的红→绿证据内容仍是同一批真实观察（真实 CLI 入口、真实日文件、真实 HEAD/变异副本红跑），变化只有采集时间与"回显到 stdout"。§9 勾选状态与 Part A 决策集合不变，4 项 Human-Confirmed 继续等人回答。

## 结论

- 执行器层验收（Architecture / Dependency / Behavior / Frontend / Documentation / Validation / Delivery Readiness）已由证据逐项支撑并勾选。
- PRD §9.1 的四项 Human-Confirmed（§2 决策一 / 决策二 / 决策三 + 呈递过目）**保持未勾选**，等人回答；`🧍 待人工验收` 状态见 PRD 顶部横幅。
- 归档、PR 创建与独立 verifier 复核由 runner 在本报告之后完成。
