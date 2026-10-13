# 验证计划

## 范围与规则

按 PRD §7 的 rv-1、rv-2 顺序验证最终工作树。每项先运行能让对应断言变红的负控，再运行修复后路径；证据按 `rv-<item_number>-<slug>.<ext>` 单项保存于本目录，原始输出与全部 RV 脚本位于同目录 `scripts/` 与 `rv-*.txt`，受仓库 `.gitignore` 证据白名单约束（仅 `*.md` 与 `evidence.json` 进入代码 diff，`.txt`/`.png`/`scripts/*.py` 为原始件不入库）。真实入口不替换为 mock；仅在 PRD §7 mock_boundary 明确允许处替换外部 GitHub 与 verifier CLI。

第七轮把 rv-1、rv-2 的全部 12 份 `rv-*.txt`（10 份共用同一绑定头，2 份浏览器 capture 由 harness 自带绑定块与真实 stdout） 在同一工作树逐字重跑并重绑（不再沿用任何旧 capture 的正文），锚点口径见每份 capture 开头的绑定块：`HEAD=1db934ae9d539efd5405dbb692b722469f0d5d60` / `TREE=ed153d9136ae3a51ef3bcf9177a7b8c479e0e7cc`（= `git rev-parse 1db934ae^{tree}`，即本 PR 当前 head，GitHub 现在即可解析）、`RUN_ON`=该 HEAD 之上应用本轮 `CODE_DELTA`（3 个文件：`src/backend/core/use_cases/agent_runner_failure_handoff.py`、`tests/test_agent_runner_recovery.py`、`docs/guides/agent-runner.md`）的工作树，复核命令 `git diff --name-only 1db934ae -- src tests docs frontend-public`；`DELIVERY_TREE` 只写在 `.txt` 绑定块里（本 manifest、两份报告与 PRD 自身都在那棵树内，写进来会自指）。真实浏览器 capture 另在 `rv-1-browser-runner-facts.json` / `rv-1-browser-runner-e2e.txt` 里自记 `CAPTURE_CODE_HEAD` / `CAPTURE_CODE_WORKTREE_TREE` / `CAPTURE_CODE_DELTA`。PRD 与 evidence Markdown 是记录件，不改变已验证的行为代码 tree。

## 真实入口与执行环境边界

rv-1 的最高保真入口已由 opt-in `tests/playwright-e2e/tests/workflows/verifier-takeover-real-entry.spec.ts` 实现，并由 `tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_browser_runner_e2e.py` 启动。浏览器访问生产 `/app/dashboard`，通过真实详情 GET 与 takeover POST 穿过 Console API、独立 SQLite CAS、runner wait/cancel loop 与 verifier process group；没有 `page.route`。隔离 harness 使用 file-backed `gh` transport 和真实阻塞子进程替代 verifier CLI，截图与 trace 写入证据目录。该路径不验证真实 GitHub transport；人工仍需对照 prototype 查看两视口截图。旧 `verifier-takeover.spec.ts` 继续作为 API-mocked UI contract，不作为 rv-1 证据。

## Oracle 执行计划与结果

| 项目 | 最高可行入口与执行 | 负控与预期失败 | 结果与证据 |
|---|---|---|---|
| rv-1 · R3 | `scripts/rv1_takeover_handoff_probe.py` 与 `scripts/rv1_console_http_real_entry_probe.py` 覆盖跨进程取消、SQLite CAS 和真实 uvicorn 接管 API；`scripts/rv1_verifier_log_identity_probe.py` 用生产 Logger/handler/formatter 写真实日文件后按操作员口径 grep，钉住 1000 行门禁拆出去的模块仍记在文档承诺的 logger 名下。随后运行 `scripts/rv1_browser_runner_e2e.py`，真实浏览器在隔离栈确认 POST 202、run_id/head_sha 绑定、handoff fresh read、列表与标签面板转为 review、Draft PR 保留、hold 生效及 verifier 进程退出；桌面/窄视口截图与 trace 存档。 | `scripts/rv1_negative_control.py` 验证 stale-head 拒绝；HTTP 探针覆盖 stale/missing/oversized binding；日志探针 `--negative-control` 把三个拆分模块的 `_logger` 改回随模块文件名走（仓库源码不改）。 | 跨进程、HTTP 与浏览器入口通过；浏览器原始输出、facts、两张截图和 trace 见 `rv-1-browser-*` 与 `rv-1-verifier-takeover*`。日志身份正控五类关键行 `OPERATOR_GREP_HITS: 5/5`、负控 `0/5` + `RV1_LOG_EXPECTED_FAIL`（见 `rv-1-verifier-log-identity.txt`）。E2E 使用外部 GitHub transport 替身；真实 GitHub 和人工视觉签收不在本证据内。 |
| rv-2 · R3 | `scripts/rv2_race_and_staleness_probe.py`：真实共享 SQLite + 真实线程并发覆盖 S1 完成先赢、S2 接管先赢、S3 并发唯一终态、S4 HEAD 漂移丢弃旧 verdict、S5 外部已合并停止后续。 | 内置 S4 负控：把漂移检测改用初始 head 快照（模拟过期结果保护失效），要求过期 verdict 被错误保留时探针变红。 | 负控红→恢复后绿：`RV2_PROBE_OK: True`、`S1..S5` 各态 True、`RV2_EXPECTED_FAIL` + `S4_NEG_STALE_NOT_RECORDED: True`。自动化竞态/fail-closed 层 222 passed（merge queue/supervisor/review_once/interactive_decision 对 autopilot_hold 阻断，含 require_verifier_pass 关闭与 repo_id 解析不出时兜底落闸）。见 `rv-2-verifier-race.txt`、`rv-2-automated-tests.txt`。 |

## 通用验证命令

```bash
# rv-1 链（真实跨进程 oracle + 负控 + 入口契约层）
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_takeover_handoff_probe.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_negative_control.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest -o addopts="" -q --no-header tests/test_console_actions.py tests/test_agent_runner_console_api.py tests/test_agent_runner_issue_control.py tests/test_process_runner.py

# rv-2 链（竞态/过期 oracle + fail-closed 层）
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv2_race_and_staleness_probe.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest -o addopts="" -q --no-header tests/test_console_store.py tests/test_run_verifier_agent.py tests/test_agent_runner_merge_queue.py tests/test_agent_runner_supervisor_entrypoints.py tests/test_pr_supervisor.py

# 门禁
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just test
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just test all
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just lint --full
(cd frontend-public && pnpm typecheck && pnpm lint && pnpm build)
```

pytest 项目 `addopts` 含 `--testmon`，会按文件改动选择性反选用例；本证据链一律用 `-o addopts=""` 关闭 testmon 以全量运行，得到稳定计数。第七轮重跑后 rv-1 契约层 135、rv-2 竞态/fail-closed 层 222、§9 Validation 第 1 条原列 8 文件合集 281、受影响后端合集 17 文件 542；`tests/test_agent_runner_issue_control.py` 单独为 33 项。受影响后端合集本轮由 16 文件扩到 17 文件（加入 `tests/test_agent_runner_recovery.py`，18 项）：第七轮改的正是该文件里的 logger 身份用例，不把它纳入 capture 就等于新用例只有报告文字没有运行证据。

`just test all`（独立 verifier 的实际门禁命令）首跑为 4 failed / 4245 passed：4 项都是既有 golden 配置断言在托管 runner 环境下读到宿主机器级 `config.toml`（注入的 `KEDACODE_CONFIG`/`IAR_CONFIG` 在 conftest 导入期就把 `settings.config` 单例定型，会话级剥离 fixture 来不及；本机那份相对仓库根缺 PRD map check 规则段且 `pre_pr_review.timeout_seconds` 为 900），与本 PRD 行为无关。第三轮把修复收在根因：`tests/conftest.py` 的会话级隔离 fixture剥离两名宿主变量后重建 `config.agent_runner`，使 `build_app_config()` 与 CI 解析到同一份仓库根配置（断言仓库根真实内容的用例仍显式 pin 来源：`tests/support/agent_runner.py::root_config_app_config()`）；复跑 **4270 passed / 1 skipped**——断言强度不变、`config.toml` 与生产代码未动。

PRD checker `--check-provided --archive-ready` 现为 PASS（Final Reconciliation 四必填字段完整、验收状态横幅 `🧍 待人工验收` 与 §9 一致：`Human-Confirmed` 两项留空、真实浏览器/PR-runner 相关非人工项以 `[~]` runner-owned gate 表达）。runner-owned 的独立 verifier 门禁与 PR 发布/归档仍为 `[~]`，执行器不代为勾选、不自行 `git mv` 归档。
