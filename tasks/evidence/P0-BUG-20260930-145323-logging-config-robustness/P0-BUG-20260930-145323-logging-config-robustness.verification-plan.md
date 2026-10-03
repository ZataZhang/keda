# Verification Plan · P0-BUG-20260930-145323-logging-config-robustness

- PRD：`tasks/pending/P0-BUG-20260930-145323-logging-config-robustness.md`（§7 Realistic Validation Plan 的本页投影）
- Issue：https://github.com/ZataZhang/keda/issues/175
- Worktree：`/Users/zata/code/keda/.iar-worktrees/issue-175`（分支 `issue-175`，基线 HEAD `f0af2d60`）
- 证据目录：`.iar/evidence/`（worktree 本地，被 `.gitignore` 排除）；采集脚本 `.iar/evidence/scripts/`（同样不进代码树）
- 结构化清单：`.iar/evidence/evidence.json`（`version: 1`，`language: "zh-CN"`）

## 环境与执行约定

1. **`PYTHONPATH` 已在仓库侧钉死**：本会话环境里 `PYTHONPATH=/tmp/keda-cli-src/src` 指向一份**改动前**的源码副本，会排到 `sys.path` 前面顶掉 worktree 代码。`pyproject.toml` 的 `[tool.pytest.ini_options] pythonpath = ["src"]` 让 pytest 在加载 conftest 前就把本树 `src` 放到 `sys.path[0]`，因此 `just test all` 与直接 `uv run pytest` 验的都是 worktree 代码。代价：**用 `PYTHONPATH` 覆盖层跑的 pytest 红跑必须显式加 `-o pythonpath=''`**，否则 overlay 被本树压住而假绿（`.iar/evidence/scripts/` 已统一处理）。`uv run iar` 之类的真实命令不读该 ini，仍靠覆盖层本身。
2. `pytest` 默认 `addopts = "--testmon"`，故所有 oracle 用 `-o addopts=''` 强制全量收集。
3. **红跑（negative control）只作用在 /tmp 下的源码副本**：`build_src_overlay` 把 worktree 的 `src` 复制一份，再用 `git show HEAD:<path>` 回滚成改动前版本（或按 item 需要把副本里的某一处改坏），然后把它作为 `PYTHONPATH` 覆盖层运行**同一条命令**。worktree 的生产代码里没有任何时钟开关、故障开关或测试专用配置键。
4. rv-5 用 `IAR_CONFIG` 指向 `~/.iar/config.toml` 的**临时副本**（只把 `[agent_runner.console]` 的 `history_db_path` / `process_registry_path` / `process_log_dir` 三项改到 /tmp）来确定性走到"没有进程日志记录"的回退分支：不写 `~/.iar`，副本含凭据因此**从不打印其内容**，脚本结束时删除。
5. 时钟推进只发生在证据脚本内部（`daemon_roll_demo.py` 把 logger 模块的 `datetime` 换成可推进的替身），用来模拟"跑过午夜的 daemon"。

## Oracle 一览

| id | 行为 | 真实入口 / 命令 | 期望观察 | mock 边界 | tier | reviewer |
|---|---|---|---|---|---|---|
| rv-1 | 非法 `LOG_LEVEL` 不再崩溃 | `LOG_LEVEL=bogus uv run iar agent doctor claude --json`（另有 `info`、空值两个变体） | `exit_code=0`；出现 `无效日志级别 'bogus'，已降级为 INFO`；无 `AttributeError`/Traceback；警告同时落进当天日志文件。`info` 变体：`exit_code=0`、级别按 INFO 生效、出现 `不是 logging 预定义名，已按 INFO 解析` 且不出现降级警告 | 不 mock：真实 CLI + 真实 Logger 初始化 | R1 | human |
| rv-2 | 跨天切换产生新日文件且保持命名 | `uv run python .iar/evidence/scripts/daemon_roll_demo.py <tmp> --expect-rotated` + `pytest -k 'rotation or daily'` + `pytest -k 'rollover_failure_does_not_raise'` / `pytest -k 'survives_old_stream_flush_failure'` | 红跑：只有一个文件、跨天行仍写在里面；绿跑：两个文件均匹配 `^app-\d{4}-\d{2}-\d{2}\.log$`，各含自己那一行；不出现 `app.log.<date>`。只读目录场景：日切开不出新文件时不向调用方抛异常、记录继续进旧文件、按目标日期只提示一次，目录恢复后自动切换；旧流收尾失败（ENOSPC / EIO）场景：丢弃那一段缓冲并照常切到新文件，同样不把异常抛回调用方 | 只替换时钟与临时目录权限；`_setup_logger`/`_DailyFileHandler`/清理不 mock | R2 | verifier |
| rv-3 | 保留天数由配置决定，日切清理 | 同一 demo 加 `LOG_RETENTION_DAYS=3` / `=5`，预置 2 天前与 1 天前的日文件；另跑 `LOG_RETENTION_DAYS=abc` 的真实配置链路 | `=3`：2 天前文件在日切后被删、1 天前保留；`=5`：2 天前保留（唯一变量是配置值）；未配置默认 14；`abc` 回退 14 且进程正常启动 | 不 mock：真实 `_cleanup_old_logs` 与真实配置字段 | R1 | verifier |
| rv-4 | 合法级别零变化、格式与默认级别不变 | `pytest -o addopts='' tests/test_logger.py -q` + `formatter_parity_check.py` + `level_probe.py`（`LOG_LEVEL=DEBUG/WARNING`）+ 真实 CLI 落盘行 | `32 passed`；`FORMATTER IDENTICAL TO HEAD`；root_level 20/10/30 分别对应 INFO/DEBUG/WARNING；真实日志行仍为 `时间 - logger - 级别 - 文件:行 - 消息` | 不 mock：真实 setup 路径 | R1 | verifier |
| rv-5 | 当日日志的目录与文件名只有一个来源 | 真实终端：`IAR_CONFIG=<~/.iar/config.toml 的临时副本，托管进程存储三项指向 /tmp> LOG_DIR=<tmp> LOG_FILE=<tmp>/logs/app.log uv run iar logs --repo-id keda --kind daemon`；再 `uv run python .iar/evidence/scripts/path_compare_check.py` + `pytest tests/test_logger.py tests/test_cli_registry.py -k 'daily_log_path or fallback'` + `rg -n 'f"app-\|app-\{' src/backend` | 绿跑：提示路径 == 本次真正写出的 `<tmp>/logs/app-<今天>.log`；`shared_function : True`（infrastructure == engines/core == api 同一对象）；`rg` 只命中 `logger.py` 的 `daily_log_path()`。红跑（HEAD 副本，同一条真实命令）：提示指向副本自己的 `logs/app-<今天>.log`，与本次真正写入的临时目录分叉 | 真实终端命令不打桩；`IAR_CONFIG` 隔离副本让回退分支可确定到达（~/.iar 只读、用完即删）。进程内的 `path_compare_check.py` 与单测仍把"托管进程记录存储"这一无关依赖打桩为空 | R1 | verifier |
| rv-6 | handler 幂等 | `uv run python .iar/evidence/scripts/root_occupied_demo.py <tmp>` + `pytest -k 'idempotent or existing_handler'` | 红跑：`daily_files_opened : []`（root 非空即整体跳过）；绿跑：`keda_handler_count : 2`、二次 setup 仍为 2、`third_party_still_attached : True` | 不 mock：真实 root handler 操作 | R1 | verifier |
| rv-7 | 真实入口下当天文件按约定生成且非空 | `LOG_DIR=<tmp> LOG_FILE=<tmp>/app.log uv run iar agent doctor claude --json`，再用同目录跑 `iar --repo-id no-such-repo daemon status` | `<tmp>/app-<今天>.log` 存在（`CONVENTION OK`）、不出现 `app.log.<date>`；第二个真实入口后文件 108 字节且含本次运行日志行；默认配置下 `logs/app-<今天>.log` 即 `iar logs` 回退提示指向的文件 | 不 mock：真实 CLI + 真实文件系统 | R1 | human |
| rv-8 | 文档与配置注释同步 | `rg -n 'log_retention_days' config.toml docs/...` + `uv run mkdocs build --strict`；红跑用 `git show HEAD:` 版本 | `config.toml [app]` 有 `# log_retention_days = 14`；`configuration.md` 有该键与轮转/清理说明；strict 构建通过；HEAD 版本 rg 无命中（exit_code=1） | 不 mock：对仓库真实文件做文本断言并真实构建 | R0 | verifier |

## 门禁（全量回归）

- `env -u VIRTUAL_ENV -u PYTHONPATH uv run python hooks/shared/check_architecture.py` → 架构依赖方向全部合法
- `env -u VIRTUAL_ENV -u PYTHONPATH uv run pytest -o addopts='' tests/test_logger.py tests/test_cli_registry.py -q`
- `just test`（本地档：先 `just lint --full` 再全量 pytest）
- `env -u VIRTUAL_ENV -u PYTHONPATH uv run mkdocs build --strict`

## 失败排查（沿用 PRD §7 Failure triage）

- rv-1 仍抛 `AttributeError` → `rg -n "getattr\(logging" src/backend` 确认是否还有旁路级别解析。
- rv-2 出现 `app.log.<date>` → 误用了标准 `TimedRotatingFileHandler`，回到 `_DailyFileHandler`。
- rv-4 / rv-6 失败先区分"legit 行为回归"与"测试断言过时"。
- rv-5 若守卫拒绝 api→infrastructure 直连 → 走 engines 层转出（本次已按该分支实现）。
