# Verifier Report · Issue #175 日志配置健壮化（两轮独立验收 + 处置）

- PRD：`tasks/pending/P0-BUG-20260930-145323-logging-config-robustness.md`
- Worktree：`/Users/zata/code/keda/.iar-worktrees/issue-175`（分支 `issue-175`，基线 HEAD `f0af2d60`，改动未提交）
- 原始报告（worktree 本地，被 `.gitignore` 排除）：
  - 第 1 轮：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/verifier-findings.md"`（18 KB）
  - 第 2 轮：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/verifier-findings-round2.md"`（21 KB）
- 两轮都由独立 verifier 自己复跑命令得结论，不复用执行器叙述。下文是"它的裁决 → 执行器的处置 → 处置后的证据位置"。
- 统一环境前缀 `env -u VIRTUAL_ENV -u PYTHONPATH`：本会话继承的 `PYTHONPATH=/tmp/keda-cli-src/src` 是一份**改动前**的源码副本，会排在 `sys.path` 前面顶掉 worktree 代码；两轮报告都先做了 `backend.__file__` 自检确认验的是 worktree 代码。

## 第 1 轮：`PASS WITH FINDINGS`（1 [MAJOR] + 6 [MINOR] + 1 组正向确认）

绑定：当时源码树指纹 `b23ca667…`（与当时 8 个 rv 文件头部一致）。

| # | 级别 | 它的结论（要点） | 执行器处置 | 处置后的证据 |
|---|---|---|---|---|
| 1 | MAJOR | `_DailyFileHandler.emit` 在 `super().emit` 的 try 之外做日切重开；只读挂载 / 权限变更 / fd 耗尽时 `OSError` 会穿出 `logger.info()`，此后每条记录都抛（改动前无此路径） | 改为**先开新流再关旧流**，`mkdir` / `_open()` 包进 `try`；失败回滚 `baseFilename`、保留旧流继续写、按目标日期只提示一次，目录恢复后自动补上切换 | `test_rollover_failure_does_not_raise_into_caller`；rv-2 只读目录红/绿段（红＝副本里把 `except OSError as error:` 换成 `except KeyboardInterrupt as error:` → `1 failed` + `PermissionError`）；PRD §13 D-10 |
| 2 | MINOR | FR-1 字面把"小写"列为要警告的手误，实现却静默接受 `info`，且有条用例反向锁死"小写不该出警告" | 解析结局从两态扩为 `exact / normalized / invalid`：`normalized` **按操作者要的级别生效**（`debug` 仍是 DEBUG）并出一条"不是 logging 预定义名，已按 … 解析"提示；`invalid` 仍是降级 + "无效日志级别"警告 | rv-1 的 `LOG_LEVEL=info` 段；`test_lowercase_level_resolves_with_normalization_notice`；PRD §13 D-11，FR-1 与 §1 行为样例同步改写 |
| 3 | MINOR | 新键 `LOG_RETENTION_DAYS=abc` 会让任意 `iar` 命令在模块导入期 `ValidationError` 崩溃（改动前该 env 名不存在、被忽略）——交付自带了一个新的"配置手误让进程起不来" | 加 `mode="before"` 校验器把无法解析的值交回默认 14；原有 after 校验器继续钳制非正数 | rv-3 的 `abc` 绿段（`configured_retention : 14`、exit 0）+ 去掉兜底的红段（`ValidationError`、exit 1）；`test_log_retention_days_field_default_and_clamp`；PRD §13 D-12 |
| 4 | MINOR | FR-4 只统一了文件名，**目录**仍是两份推导；`LOG_FILE` 被覆盖时回退提示指向没人写的文件（它用真实 CLI 复现了分叉） | 新增 `daily_log_dir()` 作为目录单点，`daily_log_path(log_dir=None)` 缺省取它，`cli_registry` 改调无参版本 | rv-5 真实终端红/绿对照（HEAD 分叉 vs 修复后逐字符相等）+ `test_cli_registry_fallback_follows_configured_log_dir`；PRD §13 D-13 |
| 5 | MINOR | 文档残留：`agent-runner.md` 仍写"14 天保留期"、`.env.example` 缺该键、`configuration.md` 把 `log_dir` 说成能移动日文件（实际落点是 `log_file` 的父目录） | 三处全改；`configuration.md` 明确"`LOG_DIR` 仅用于保证目录存在，单独设置不会挪走日文件"，并把级别手误的两种提示与非整数回退写进配置项表 | rv-8（含"无非活动 `LOG_*` 赋值"与"文档无过期 14 天表述"两项核对）；该边界另登记 `tasks/inbox/ideas.md` 的 `logging-log-dir-single-source` 作为后续 PRD 入口 |
| 6 | MINOR | rv-5 的"只能打桩"披露不成立：仓库自带 `IAR_CONFIG` 隔离点可以让真实终端走到回退分支 | rv-5 改用真实终端命令 + `IAR_CONFIG` 指向 `~/.iar/config.toml` 的临时副本（只改 `[agent_runner.console]` 三项存储路径到 /tmp，不写 `~/.iar`，用完即删），撤销原披露 | rv-5 红/绿两段真实命令；`evidence.json` 第 5 项 |
| 7 | MINOR | `evidence.json` 叙述里引用了会漂移的 `logger.py:190` 行号 | 移除 manifest 内所有源码行号引用 | `.iar/evidence/evidence.json`：`logger.py:<行号>` 已无残留 |
| INFO | — | 正向确认：生产代码没有测试专用/故障开关；8 项红跑均为改动前行为或 /tmp 副本变异（非 mock 伪造）；`emit` 在 handler 锁下串行、并发写安全；重复 setup 不关闭 `sys.stdout`、不叠加 handler；`isinstance(h, logging.FileHandler)` 兼容；`logging.shutdown` 对重开流无害；转出链 `infrastructure → engines → core → api` 正确 | 无需处置 | 第 2 轮逐项复核未回归 |

## 第 2 轮：`PASS WITH FINDINGS`（第 1 轮 8 项全部 CONFIRMED FIXED；新增 3 条 [INFO]）

绑定：本轮改动面指纹 `be798b79…`；证据包绑定的 5 个 src 文件指纹 `b43c5ec3…`（与 8 个 rv 文件头部逐字一致；它在 19:47 与 20:05 两次重算相同 → 证据采集后源码树未动）。

- **8/8 CONFIRMED FIXED**，每条都附它自己复跑出来的数字。关键项：自建只读目录 + 拨时钟脚本 → 业务调用抛出异常数 0、失败期间旧流仍开着、6 条记录继续进旧文件、恢复后次日文件出现且 `_current_date` 推进、连跨 4 天只出 4 行提示、EMFILE 场景同样 0 抛出；`LOG_LEVEL=debug` 实测 root 与两个 handler 都是 10 且 DEBUG 行真的落盘；`LOG_RETENTION_DAYS` 的 env 与 TOML 两个来源取值表一致（`abc/0/-3/""/3.5/1e3/true → 14`；`7/"7"/" 9 " → 7/7/9`）；真实 `iar logs` 在 HEAD 副本上分叉、在当前树上一致、默认配置下与 HEAD 文本相同（FR-6 未破）；`~/.iar` 整树 size/mtime 快照零变化；证据包凭据扫描零命中（只打印命中计数）；manifest 32 条 `stdout_assertions` 与各自证据文件逐条相符、8 个文件均无 `CHECK FAIL`。
- 门禁复跑（它自己跑的，数字是第 2 轮当时的树）：`tests/test_logger.py` 26 passed；`test_logger + test_cli_registry` 47 passed；`check_architecture.py` 275 文件无违规；`ruff check src tests` All checks passed 且 `format --check` 干净；`mkdocs build --strict` exit 0；全量 `pytest tests/` 2687 passed / 1 skipped；PRD §7 Drift Guard 全部命中预期。
- 它也如实登记了自己没做到的部分：只读目录红跑的复现配方与实现方记录略有差异（它改用自建脚本验证）；`git status` 干净性被本地未跟踪的 `tasks/evidence/` 工件干扰（改用 `-uno` 复核）；ENOSPC 无法在真实文件系统上构造（改用注入 stream 证明）；在 `PYTHONPATH` 覆盖层下跑 `formatter_parity_check.py` 的那次探针是无效实验而非交付缺陷。

### 第 2 轮的 3 条新发现与处置

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| N1 | INFO | 旧流的 `flush()` / `close()` 仍在新流 `try` 之外：旧流 flush 抛 `OSError`（ENOSPC / EIO）时异常仍会上抛，且 `baseFilename` 已指向新日期而 `_current_date` 未推进（状态不一致，还会留下一个空的当天文件）。它判 INFO 的理由：stdlib 每条记录写完即 flush，正常路径日切时缓冲为空，需要写错误与日切同时发生 | **已修**：旧流收尾独立包进 `try/except OSError`，失败只丢弃那一段缓冲并照常切到新文件，`baseFilename` 与 `_current_date` 同步推进。新增 `test_rollover_survives_old_stream_flush_failure`（注入 flush 抛 `OSError` 的 stream），rv-2 增第三段红/绿对照（把副本里该 `except OSError:` 换成捕获不到的类型 → `OSError: No space left on device` + `1 failed`） |
| N2 | INFO | manifest 里还有 2 处 `cli.py:221`，但它核对为"日志行原文引用"（当前 `cli.py:221` 确实是那条 `logger.error("iar failed:\n%s", ...)`），不是叙述引用旧行号 | **不改**：保留原文引用，它逐字等于捕获到的输出；若该日志语句将来挪动，rv-4 / rv-7 的捕获会随之更新 |
| N3 | INFO | `normalized` 提示把别名原样回显：`LOG_LEVEL=warn` → "已按 WARN 解析"（实际生效 30 = `WARNING`），读起来像存在 `WARN` 档 | **已修**：提示改用**生效级别的规范名**（`logging.getLevelName(level)`），`warn` 现在报"已按 WARNING 解析"；`info` 仍是"已按 INFO 解析"，rv-1 断言不受影响 |

（第 2 轮另有一条附注，非 finding：`.env.example` 第 84 行的 `RETENTION_DAYS=30` 属于 docker-compose 备份保留、与日志无关；rv-8 的"无非活动 `LOG_*` 赋值"只针对 `LOG_` 前缀。人审时别把两个 retention 混为一谈。）

## 实现期的内部复核（早于第 1 轮报告落盘，记录在此以免只剩两轮摘要）

1. "降级警告是否真的落进日文件"——用变异实验确认**已有用例钉住**：把 `_setup_logger` 末尾的警告从 `logging` 改成 `print` 后，`test_invalid_log_level_degrades_to_info_with_warning` 的三种输入全部以 `AssertionError: assert '无效日志级别' in ''` 失败（同时纠正了一次误报：有 reviewer 声称这条没有断言）。
2. "root 被外部清空过时旧 handler 句柄泄漏"——**采纳**：新增模块级 `_installed_keda_handlers` 收尾清单与 `test_rebuilt_logger_closes_handlers_no_longer_visible_on_root`（rv-6 第三段红跑：删掉该收尾 → `AssertionError: 旧的日文件句柄必须被关闭…`）。
3. "普通 `logging.FileHandler` 会被算成日文件写入者"——**加固测试**：`_reset_logging_state()` 显式关闭并摘除 root 上所有 handler；两个 handler 类型用例改为断言"恰好一个文件 handler 且是 `_DailyFileHandler` 子类"。
4. 另有 4 条 hygiene 项（rv-8 未覆盖 `.env.example`、`daemon_roll_demo.py` 的"没有任何故障开关"表述过粗、rv-5 与 manifest 关于"只读副本 / 只保留路径行"的不精确措辞、Change Log 的 findings 计数）——全部已改。其中"`.env.example` 里该键是活动赋值"这一前提经复核与树不符（`# LOG_LEVEL=INFO` 与 `# LOG_RETENTION_DAYS=14` 都是注释态，`grep -nE '^[A-Z_]+=' .env.example | rg -i LOG_` 无命中），但"rv-8 检索面没覆盖它"这半成立，已按实质点处置。

## 当前状态（处置 N1 / N3 之后）

- 源码树在第 2 轮之后又动了一次（旧流收尾兜底 + 提示规范名），因此**全部 8 项证据按最终树重采**：头部 `working_tree_fingerprint` 统一为 `c5a9b475…`；机器核对通过——manifest 每条 `stdout_assertions` 与对应证据文件一致，8 个文件均无 `CHECK FAIL`、均以 `ITEM rv-<n> check failures: 0` 收尾。
- 执行器侧门禁（最终树）：`tests/test_logger.py` 27 passed；`test_logger + test_cli_registry` 48 passed；关掉 `--testmon` 的全量 `pytest tests/` **2688 passed / 1 skipped**；`just test`（内含 `just lint --full`）通过；`check_architecture.py` 无违规；`mkdocs build --strict` 通过。
- **N1 / N3 的修复尚未经第 3 轮独立复核**：两者都是第 2 轮判为 [INFO] 的可选项（非阻塞），修复由新用例 + rv-2 的变异红跑自我证明；独立的第 3 轮复核留给 PR 阶段的 verifier / runner。
- 验收面：PRD §9.2 除 4 项 Human-Confirmed（§2 决策一 / 二 / 三 + §9.1 呈递过目）外均已勾选并带证据；PRD 横幅为 `🧍 待人工验收`。归档、PR 创建与 CI 复跑由 runner 负责，不由本报告宣布。
