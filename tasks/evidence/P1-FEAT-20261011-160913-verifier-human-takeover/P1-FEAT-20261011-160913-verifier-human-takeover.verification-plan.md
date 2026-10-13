# 验证计划

## 范围与规则

按 PRD §7 的 rv-1、rv-2 顺序验证最终工作树。每项先运行能让对应断言变红的负控，再运行修复后路径；证据按 `rv-<item_number>-<slug>.<ext>` 单项保存于本目录，原始输出与全部 RV 脚本位于同目录 `scripts/` 与 `rv-*.txt`，受仓库 `.gitignore` 证据白名单约束（仅 `*.md` 与 `evidence.json` 进入代码 diff，`.txt`/`.png`/`scripts/*.py` 为原始件不入库）。真实入口不替换为 mock；仅在 PRD §7 mock_boundary 明确允许处替换外部 GitHub 与 verifier CLI。

本轮（第六轮评审修复）复核的工作树与绑定口径。绑定块给出 `HEAD`/`TREE`/`DELIVERY_TREE`/`RUN_ON`/`CODE_DELTA`/`RECORD_EXCLUDED` 六项：`HEAD=6894eb6accdb41d5c583a6a889005d116216a8ba` + `TREE=b5ca972d44f5c803130a3e3e1b05fcf1344413ce`（= `git rev-parse 6894eb6a^{tree}`，本轮改动之前**已在远程分支可解析**的最近提交，今天即可 `git cat-file -p` 或 `gh api repos/ZataZhang/keda/git/trees/b5ca972d44f5c803130a3e3e1b05fcf1344413ce` 核对，且已含第五轮拆出的 `agent_runner_verdict.py` / `agent_runner_verifier_response_log.py` / `agent_runner_verdict_pr_effects.py`）；`DELIVERY_TREE` 是 capture 实际所在工作树对应的**交付提交 tree**（runner 以 `git add -A` 从这份工作树生成提交，`git rev-parse <交付提交>^{tree}` 必须等于它）；`CODE_DELTA` 声明本轮 delta 的全部文件；`RECORD_EXCLUDED` 声明 `tasks/pending/`、`tasks/archive/`、`tasks/evidence/` 记录性文件不参与代码断言。 本轮代码 delta 只有 5 个文件（`src/backend/core/use_cases/agent_runner_verifier_response_log.py`、`src/backend/core/use_cases/agent_runner_verdict_pr_effects.py`、`tests/test_run_verifier_agent.py`、`tests/playwright-e2e/tests/workflows/verifier-takeover.spec.ts`、`docs/guides/agent-runner.md`——前两个只把拆分模块的 logger 身份钉回 `run_verifier_agent`，其余是回归用例、spec 披露文案与文档），由 `git diff --name-only 6894eb6a -- src tests docs frontend-public` 复核。本轮代码 delta 已由 runner 在中途落地为提交 `512821b2096275a59241545fa28cc1fcf2675cfb`（`git diff --name-only 512821b2 -- src tests docs frontend-public` 为空，即 capture 所在工作树的代码与该提交逐字节一致）；此后工作树与它只差 4 份记录件（PRD、两份报告、`evidence.json`），与 `.txt`/`scripts/` 同属不参与代码断言的一类。`DELIVERY_TREE` 只写进 `.txt` 绑定块、不写进本报告与 PRD：`.txt`/`.png`/`scripts/*.py` 被 `.gitignore` 的 `tasks/evidence/**` 排除、不进交付 tree，而报告与 PRD 自身就在 tree 里——把 tree 写进被自己计入的文件会自指，重算一次值就变一次；这正是前三轮 `b6e92e21…` / `e66a427e…` 那类「只能按临时 index 配方重算、不可从任何 ref 解析的合成 tree」的成因，本轮起不再用作绑定，也不再用「父提交 tree + CODE_DELTA」间接指代。 捕获结果：rv-1 入口契约 135 passed、rv-2 竞态/fail-closed 222 passed、原列 8 文件合集 281 passed、受影响后端 16 文件合集 524 passed（后三者比第五轮的 221 / 280 / 523 各多 1，来自本轮新增的 logger 身份回归用例），`RV1_PROBE_OK` / `RV1_NEGATIVE_CONTROL_OK` / `RV2_PROBE_OK` 与本轮新增的 `RV1_HTTP_PROBE_OK` 及两支负控 marker 全数复现，前端 `pnpm typecheck` / ESLint（0 errors、27 个既有 JSDoc warnings）/ `pnpm build` 均 exit 0。 后续只更新本证据目录中的报告文本时，代码断言不变；runner 仍需在最终提交后按机器契约记录最终 committed tree 与证据哈希。

## 真实入口与执行环境边界

rv-1 的最高保真入口已由 opt-in `tests/workflows/verifier-takeover-real-entry.spec.ts` 实现，并由 `scripts/rv1_browser_runner_e2e.py` 启动。浏览器访问生产 `/app/dashboard`，通过真实详情 GET 与 takeover POST 穿过 Console API、独立 SQLite CAS、runner wait/cancel loop 与 verifier process group；没有 `page.route`。隔离 harness 使用 file-backed `gh` transport 和真实阻塞子进程替代 verifier CLI，截图与 trace 写入证据目录。该路径不验证真实 GitHub transport；人工仍需对照 prototype 查看两视口截图。旧 `verifier-takeover.spec.ts` 继续作为 API-mocked UI contract，不作为 rv-1 证据。

## Oracle 执行计划与结果

| 项目 | 最高可行入口与执行 | 负控与预期失败 | 结果与证据 |
|---|---|---|---|
| rv-1 · R3 | `scripts/rv1_takeover_handoff_probe.py` 与 `scripts/rv1_console_http_real_entry_probe.py` 覆盖跨进程取消、SQLite CAS 和真实 uvicorn 接管 API。随后运行 `scripts/rv1_browser_runner_e2e.py`，真实浏览器在隔离栈确认 POST 202、run_id/head_sha 绑定、handoff fresh read、列表与标签面板转为 review、Draft PR 保留、hold 生效及 verifier 进程退出；桌面/窄视口截图与 trace 存档。 | `scripts/rv1_negative_control.py` 验证 stale-head 拒绝；HTTP 探针覆盖 stale/missing/oversized binding。 | 跨进程、HTTP 与浏览器入口通过；浏览器原始输出、facts、两张截图和 trace 见 `rv-1-browser-*` 与 `rv-1-verifier-takeover*`。E2E 使用外部 GitHub transport 替身；真实 GitHub 和人工视觉签收不在本证据内。 |
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

pytest 项目 `addopts` 含 `--testmon`，会按文件改动选择性反选用例；本证据链一律用 `-o addopts=""` 关闭 testmon 以全量运行，得到稳定计数。本轮修复层落地后 rv-1 契约层 135、rv-2 竞态/fail-closed 层 222、§9 Validation 第 1 条原列 8 文件合集 281；`tests/test_agent_runner_issue_control.py` 单独为 33 项。

`just test all`（独立 verifier 的实际门禁命令）首跑为 4 failed / 4245 passed：4 项都是既有 golden 配置断言在托管 runner 环境下读到宿主机器级 `config.toml`（注入的 `KEDACODE_CONFIG`/`IAR_CONFIG` 在 conftest 导入期就把 `settings.config` 单例定型，会话级剥离 fixture 来不及；本机那份相对仓库根缺 PRD map check 规则段且 `pre_pr_review.timeout_seconds` 为 900），与本 PRD 行为无关。第三轮把修复收在根因：`tests/conftest.py` 的会话级隔离 fixture剥离两名宿主变量后重建 `config.agent_runner`，使 `build_app_config()` 与 CI 解析到同一份仓库根配置（断言仓库根真实内容的用例仍显式 pin 来源：`tests/support/agent_runner.py::root_config_app_config()`）；复跑 **4270 passed / 1 skipped**——断言强度不变、`config.toml` 与生产代码未动。

PRD checker `--check-provided --archive-ready` 现为 PASS（Final Reconciliation 四必填字段完整、验收状态横幅 `🧍 待人工验收` 与 §9 一致：`Human-Confirmed` 两项留空、真实浏览器/PR-runner 相关非人工项以 `[~]` runner-owned gate 表达）。runner-owned 的独立 verifier 门禁与 PR 发布/归档仍为 `[~]`，执行器不代为勾选、不自行 `git mv` 归档。
