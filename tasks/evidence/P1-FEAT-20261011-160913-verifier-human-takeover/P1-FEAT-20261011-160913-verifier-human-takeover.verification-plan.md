# 验证计划

## 范围与规则

按 PRD §7 的 rv-1、rv-2 顺序验证最终工作树。每项先运行能让对应断言变红的负控，再运行修复后路径；证据按 `rv-<item_number>-<slug>.<ext>` 单项保存于本目录，原始输出与全部 RV 脚本位于同目录 `scripts/` 与 `rv-*.txt`，受仓库 `.gitignore` 证据白名单约束（仅 `*.md` 与 `evidence.json` 进入代码 diff，`.txt`/`.png`/`scripts/*.py` 为原始件不入库）。真实入口不替换为 mock；仅在 PRD §7 mock_boundary 明确允许处替换外部 GitHub 与 verifier CLI。

### 第九轮（评审 findings 2/3/4 的行为修复）绑定与口径

本轮改的是行为源代码与界面，因此**全部 capture 在 `HEAD + 本轮未提交改动` 的代码状态上重跑**：`HEAD=82ab9f182dec2b8fa193ddb8d2b97e88213a8b7d` / `TREE=1e4c8907d3be6bba533a27fb8e1f0de1a1657d5c`，远端可解析锚点 `PUSHED_ANCHOR=82ab9f18...`（`zata/issue-278` 与本地 head 一致），`CODE_DELTA` 即 `git diff --name-only 82ab9f18 -- src tests docs frontend-public` 列出的 13 个文件（界面 1、后端 5、测试 4、文档/Skill 2、契约端口 1），`DELIVERY_TREE` 由 `scripts/rv_bind_tree.sh` 在记录件定稿后绑到 runner 的下一个提交之 tree。第八轮写在本文与各份 capture 里的 `HEAD=885ca6f6` / `TREE=e69e7059` / `PUSHED_ANCHOR=1db934ae`（tree `ed153d91`）**只是历史**，不再声称它是本 PR 当前 head——那正是评审 finding 1 指出的绑定失效：`agent_runner_validation_gate.py` 在 `evidence_marker.head_sha != pr_context.head_sha` 时会重置清单，旧 head 的证据包等于没有证据。

本轮 capture 由 11 份（含新增 `rv-1-takeover-contract-spec.txt`）+ 2 份 chain stdout + 2 份浏览器 capture 与截图/trace 组成，逐份自带绑定头。计数随新增回归改数：入口契约层 **141 passed**（+6），竞态/fail-closed 层 **222 passed**（不含本轮新增用例，逐字复跑未变），§9 原列 8 文件集 **284 passed**（+3），受影响后端合集 **549 passed**（+7）。

### rebase 带进来的标签改名（本轮发现的证据侧红）

重跑真实浏览器 harness 时 `ISSUE_MOVED_TO_REVIEW` 与 `verifier-takeover-real-entry.spec.ts` 的列表/标签断言一起变红，根因不是接管行为，而是判据写死了 `agent/review`：main 已把 review 态的规范名改成 `agent/automated-review-passed`（`config.labels.review` 承载，前端 chip 去掉 `agent/` 前缀显示）。判据现按运行侧同一份配置解析（harness 把解析值打进 `REVIEW_LABEL`，spec 用 `REVIEW_STATE_CHIP` 匹配两种可接受拼写），并新增 `NO_VERDICT_PASSED_LABEL` 断言确认被接管的这轮没有落下 `validation/verifier-passed`。需要人拍板的是**语义**而非拼写：在 main 的新标签体系里，接管后的 Issue 落在「automated review passed」这个标签上，而这条链路的本意是「机器结论作废、交回给人」——是否改投 `human/awaiting-review`，属需求层决定，执行侧不擅自改。

### 第八轮历史口径（保留原文，锚点已作废）

第八轮（runner 的 recovery attempt 1）把 rv-1、rv-2 的全部 capture（10 份共用同一绑定头的 `rv-*.txt` + 浏览器 harness 自写的 `rv-1-browser-runner-e2e.txt`（自带完整绑定块）与 `rv-1-browser-playwright.txt`（Playwright 原样 stdout，本轮起开头加一行 `BINDING=` provenance 回指同次运行的绑定块与 `HEAD`，因为单开这一份也要能追溯到它跑在哪棵树上） + 本轮新增的 2 份 `rv-<item>-chain-stdout.txt`）在当前 HEAD 的干净代码状态上逐字重跑并重绑。锚点口径见每份 capture 开头的绑定块：`HEAD=885ca6f64607f20827a75e2f660666a9dd84b933` / `TREE=e69e7059883a76a9f16b753d77e33a3e1c545ed4`（当时的本地 head；**现已不是本 PR 的 head**，当前锚点见上方「第九轮」小节）、`RUN_ON`=HEAD 自身的代码状态、`CODE_DELTA=(none)`（`git diff --name-only 885ca6f6 -- src tests docs frontend-public` 输出为空，本轮不改任何入库行为代码）；远端已推送、GitHub 现在仍可解析的锚点是 `1db934ae9d539efd5405dbb692b722469f0d5d60` / tree `ed153d9136ae3a51ef3bcf9177a7b8c479e0e7cc`，写在每份绑定块的 `PUSHED_ANCHOR` 里。`DELIVERY_TREE` 只写在 `.txt` 绑定块（本 manifest、三份报告与 PRD 自身都在那棵树内，写进来会自指），由 `scripts/rv_bind_tree.sh` 在记录件全部定稿后用不碰真索引的临时索引（`GIT_INDEX_FILE=$IDX git read-tree HEAD && git add -A . && git write-tree`）算出并绑入，绑完再重算一次校验未漂移。真实浏览器 capture 另在 `rv-1-browser-runner-facts.json` / `rv-1-browser-runner-e2e.txt` 里自记 `CAPTURE_CODE_HEAD` / `CAPTURE_CODE_WORKTREE_TREE` / `CAPTURE_CODE_DELTA`。PRD 与 evidence Markdown 是记录件，不改变已验证的行为代码 tree。

## stdout 断言口径（第八轮修的实际缺陷）

runner 校验 Realistic Validation 的方式是：执行 manifest 里该 item 声明的 `command`，再把它**自己的 stdout** 逐条与 `stdout_assertions` 比对。上一轮我按「marker 能否在证据文件里 grep 到」核对，这是两种不同的东西，于是声明了打不中的断言：

| item | 断言 | 上一轮为什么打不中 | 本轮怎么让它真起来 |
|---|---|---|---|
| rv-1 | `'2 passed'` | 那是 Playwright 的计数摘要；harness 用 `capture_output=True` 收走并只写进 `rv-1-browser-playwright.txt`，命令 stdout 里从来没有这一串（正是 runner 报的「exit 0 但断言 #37 未命中」） | `scripts/rv1_browser_runner_e2e.py` 把从真实输出里抽出的摘要行原样 `print` 到 stdout：`PLAYWRIGHT_TALLY: 2 passed (17.2s)`；抽不到摘要行就报错退出，不自己造计数。manifest 断言改为这条带标签的真实行，并加由该摘要推出的 `PLAYWRIGHT_TALLY_PASSED: True`（passed 出现且无 failed/flaky/did not run） |
| rv-2 | `'281 passed'`、`'542 passed'` | 那两层 pytest 根本没进 item 2 的 `command`，只在各自 `.txt` 里 | item 2 的 `command` 扩成 探针 → 222 层 → 281 层 → 542 层，三条计数都由真实运行逐字打进 stdout |

按 runner 口径复核的入口（逐 item 跑 `command`、只比对 command 自身 stdout，未命中即非零退出并标明该串只在 stderr 里还是哪儿都没有；同时把两条 command 的原样 stdout 写成 `rv-1-chain-stdout.txt` / `rv-2-chain-stdout.txt`，让 reviewer 用同一口径复核而不必再拼各步 capture）：

```bash
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv_stdout_assertions.py
```

本轮没有修改任何 oracle 判定逻辑、没有放宽断言、没有给命令加 `|| true` 之类兜底；改的是「让命令真的把它声称的东西打进 stdout」。证据侧脚本全部收进本目录 `scripts/`：`rv_capture.sh` 逐份重跑并现算绑定头、`rv_recapture_all.sh` 按 marker 校验后落盘且任一失败即中止、`rv_bind_tree.sh` 绑交付 tree、`rv1_frontend_checks.sh` 取代原先依赖宿主固定 `/tmp` 路径的前端命令（三个 exit code 原样打印，任一非零则整脚本非零退出并打 `RV1_FRONTEND_CHECKS: FAIL`）、`rv_stdout_assertions.py` 做上述复核、`rv_upd_manifest.py` 记录本轮 manifest 的改动口径。`.gitignore` 已把它们与 `.txt` 一并排除在交付 diff 之外，没有一个 RV 脚本进入代码 diff。

## 第九轮断言与证据增补（行为修复层）

| item | 变化 | 判据 |
|---|---|---|
| rv-1 | 入口契约层计数断言 `'135 passed'` → `'141 passed'` | 同一命令链（4 份探针 + `pytest tests/test_console_actions.py tests/test_agent_runner_console_api.py tests/test_agent_runner_issue_control.py tests/test_process_runner.py`）逐字重跑；新增的 6 条回归全部在该 stdout 里被计数，不是报告文字 |
| rv-1 | 新增断言 `REVIEW_LABEL: ` 与 `NO_VERDICT_PASSED_LABEL: True` | 真实浏览器 harness 自己 print：前者报出它按运行侧 `config.labels.review` 解析到的标签名（值不钉进 manifest，避免把同一个拼写脆弱点从 capture 搬进断言），后者确认被接管的这轮没落 `validation/verifier-passed` |
| rv-1 | 新增 capture `rv-1-takeover-contract-spec.txt`（不接进 command 链，与 `rv-1-frontend-checks.txt` 同一处置） | 入口 `bash scripts/rv1_contract_spec.sh` = `just e2e tests/workflows/verifier-takeover.spec.ts`，本轮两条界面契约用例（mounted-requested / takeover_failed）的 Playwright 原样输出，`RV1_CONTRACT_SPEC: OK` 只在无 failed/flaky/did not run 时才打 |
| rv-1 | 前端契约层加第四层：Playwright 包 `tsc --noEmit` | 判据是「接管 spec 0 条类型错误」；该包另有一处与本 Issue 无关的既有报错 `tests/smoke/idea-inbox.spec.ts`，整包 exit code 与原样报错一并打出，不并入断言 |
| rv-2 | 计数断言 `'281 passed'` → `'284 passed'`、`'542 passed'` → `'549 passed'`（`'222 passed'` 未变） | item 2 的 `command` 三层 pytest 逐字重跑。284 = 281 + 3：+1 来自 `tests/test_console_actions.py`（未落盘的请求不得留下 accepted 审计）、+2 来自 `tests/test_process_runner.py`（终止身份只读一次并贯穿两轮拆除）。549 = 542 + 7：上面这 3 条，加 `tests/test_agent_runner_issue_control.py` 的 3 条（CAS 空手两次不得受理 / 重试必须真的落盘 / 映射 409），再加 **1 条不是本 PR 写的**：本轮自己新增的就是 6 条（`test_agent_runner_issue_control.py` 33→36、`test_console_actions.py` 12→13、`test_process_runner.py` 55→57，逐文件 `--collect-only` 核过），`542` 是在 rebase 之前的 `da043953` 上量的，rebase 把 main 的测试增量带进了这 17 个文件里本 PR 没碰的那些（`git diff --stat da043953 HEAD -- tests/` 可见 `test_pr_supervisor.py` / `test_review_once.py` 等），本轮重跑如实反映最终 tree——这正是 finding 1 说"旧 capture 按构造覆盖不到 rebase 带进来的改动"的具体形态 |
| rv-2 | 负控不变 | `RV2_EXPECTED_FAIL` + `S4_NEG_STALE_NOT_RECORDED: True` 仍由同一探针现场制造红→绿，探针一字未改 |

## 真实入口与执行环境边界

rv-1 的最高保真入口已由 opt-in `tests/playwright-e2e/tests/workflows/verifier-takeover-real-entry.spec.ts` 实现，并由 `tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_browser_runner_e2e.py` 启动。浏览器访问生产 `/app/dashboard`，通过真实详情 GET 与 takeover POST 穿过 Console API、独立 SQLite CAS、runner wait/cancel loop 与 verifier process group；没有 `page.route`。隔离 harness 使用 file-backed `gh` transport 和真实阻塞子进程替代 verifier CLI，截图与 trace 写入证据目录。该路径不验证真实 GitHub transport；人工仍需对照 prototype 查看两视口截图。旧 `verifier-takeover.spec.ts` 继续作为 API-mocked UI contract，不作为 rv-1 证据。

## Oracle 执行计划与结果

| 项目 | 最高可行入口与执行 | 负控与预期失败 | 结果与证据 |
|---|---|---|---|
| rv-1 · R3 | `scripts/rv1_takeover_handoff_probe.py` 与 `scripts/rv1_console_http_real_entry_probe.py` 覆盖跨进程取消、SQLite CAS 和真实 uvicorn 接管 API；`scripts/rv1_verifier_log_identity_probe.py` 用生产 Logger/handler/formatter 写真实日文件后按操作员口径 grep，钉住 1000 行门禁拆出去的模块仍记在文档承诺的 logger 名下。随后运行 `scripts/rv1_browser_runner_e2e.py`，真实浏览器在隔离栈确认 POST 202、run_id/head_sha 绑定、handoff fresh read、列表与标签面板转为 review、Draft PR 保留、hold 生效及 verifier 进程退出，并把 Playwright 自己的计数摘要原样打进 stdout；桌面/窄视口截图与 trace 存档。前端契约层另由 `scripts/rv1_frontend_checks.sh` 覆盖（typecheck + 6 个受影响文件 ESLint + build）。 | `scripts/rv1_negative_control.py` 验证 stale-head 拒绝；HTTP 探针覆盖 stale/missing/oversized binding；日志探针 `--negative-control` 把三个拆分模块的 `_logger` 改回随模块文件名走（仓库源码不改）。 | 跨进程、HTTP 与浏览器入口通过；浏览器原始输出、facts、两张截图和 trace 见 `rv-1-browser-*` 与 `rv-1-verifier-takeover*`，命令整链原样 stdout 见 `rv-1-chain-stdout.txt`（本轮 `PLAYWRIGHT_TALLY: 2 passed (17.2s)`）。日志身份正控五类关键行 `OPERATOR_GREP_HITS: 5/5`、负控 `0/5` + `RV1_LOG_EXPECTED_FAIL`（见 `rv-1-verifier-log-identity.txt`）。E2E 使用外部 GitHub transport 替身；真实 GitHub 和人工视觉签收不在本证据内。 |
| rv-2 · R3 | `scripts/rv2_race_and_staleness_probe.py`：真实共享 SQLite + 真实线程并发覆盖 S1 完成先赢、S2 接管先赢、S3 并发唯一终态、S4 HEAD 漂移丢弃旧 verdict、S5 外部已合并停止后续。本 item 的 `command` 第八轮起把三层 pytest 一并跑完（222 / 281 / 542），原样 stdout 见 `rv-2-chain-stdout.txt`。 | 内置 S4 负控：把漂移检测改用初始 head 快照（模拟过期结果保护失效），要求过期 verdict 被错误保留时探针变红。 | 负控红→恢复后绿：`RV2_PROBE_OK: True`、`S1..S5` 各态 True、`RV2_EXPECTED_FAIL` + `S4_NEG_STALE_NOT_RECORDED: True`。自动化竞态/fail-closed 层 222 passed（merge queue/supervisor/review_once/interactive_decision 对 autopilot_hold 阻断，含 require_verifier_pass 关闭与 repo_id 解析不出时兜底落闸）。见 `rv-2-verifier-race.txt`、`rv-2-automated-tests.txt`。 |

## 通用验证命令

```bash
# rv-1 链（真实跨进程 oracle + 负控 + 运维触点 + 入口契约层 + 真实浏览器，即 manifest item 1 的 command）
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_takeover_handoff_probe.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_negative_control.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_console_http_real_entry_probe.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_verifier_log_identity_probe.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_verifier_log_identity_probe.py --negative-control && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest -o addopts="" -q --no-header tests/test_console_actions.py tests/test_agent_runner_console_api.py tests/test_agent_runner_issue_control.py tests/test_process_runner.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_browser_runner_e2e.py

# rv-1 前端契约层（不在 item 1 的 command 链里，按 capture 内记录的命令单独跑）
UV_CACHE_DIR=/private/tmp/issue256-uv-cache bash tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv1_frontend_checks.sh

# rv-2 链（竞态/过期 oracle + fail-closed 层 + §9 原列 8 文件集 + 受影响后端合集，即 manifest item 2 的 command）
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv2_race_and_staleness_probe.py && \
UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest -o addopts="" -q --no-header tests/test_console_store.py tests/test_run_verifier_agent.py tests/test_agent_runner_merge_queue.py tests/test_agent_runner_supervisor_entrypoints.py tests/test_pr_supervisor.py tests/test_review_once.py tests/test_interactive_decision.py

# 按 runner 口径复核 stdout 断言，并把两条 command 的原样 stdout 写成 rv-<n>-chain-stdout.txt
UV_CACHE_DIR=/private/tmp/issue256-uv-cache PYTHONPATH=src uv run python tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/scripts/rv_stdout_assertions.py

# 门禁
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just test
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just test all
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just lint --full
(cd frontend-public && pnpm typecheck && pnpm lint && pnpm build)
```

pytest 项目 `addopts` 含 `--testmon`，会按文件改动选择性反选用例；本证据链一律用 `-o addopts=""` 关闭 testmon 以全量运行，得到稳定计数。第八轮在当前 HEAD 的干净代码状态上重跑后 rv-1 契约层 135、rv-2 竞态/fail-closed 层 222、§9 Validation 第 1 条原列 8 文件合集 281、受影响后端合集 17 文件 542；`tests/test_agent_runner_issue_control.py` 单独为 33 项；真实浏览器入口的 Playwright 计数 2 passed（auth setup + 接管用例）。受影响后端合集自第七轮由 16 文件扩到 17 文件（加入 `tests/test_agent_runner_recovery.py`，18 项）：第七轮改的正是该文件里的 logger 身份用例，不把它纳入 capture 就等于新用例只有报告文字没有运行证据。

`just test all`（独立 verifier 的实际门禁命令）首跑为 4 failed / 4245 passed：4 项都是既有 golden 配置断言在托管 runner 环境下读到宿主机器级 `config.toml`（注入的 `KEDACODE_CONFIG`/`IAR_CONFIG` 在 conftest 导入期就把 `settings.config` 单例定型，会话级剥离 fixture 来不及；本机那份相对仓库根缺 PRD map check 规则段且 `pre_pr_review.timeout_seconds` 为 900），与本 PRD 行为无关。第三轮把修复收在根因：`tests/conftest.py` 的会话级隔离 fixture剥离两名宿主变量后重建 `config.agent_runner`，使 `build_app_config()` 与 CI 解析到同一份仓库根配置（断言仓库根真实内容的用例仍显式 pin 来源：`tests/support/agent_runner.py::root_config_app_config()`）；复跑 **4270 passed / 1 skipped**——断言强度不变、`config.toml` 与生产代码未动。第七轮加入 1 条 logger 身份用例后为 **4274 passed / 1 skipped**；第八轮不改任何入库代码，在同一 `HEAD=885ca6f6` 上复跑仍是 **4274 passed / 1 skipped**（253 秒，exit 0，`just test` / `just lint --full` 旗标同步刷到该 head）。第九轮在交付树（`HEAD=82ab9f18` / `TREE=1e4c8907` + 本轮 13 个文件的 `CODE_DELTA`）复跑：rv-1 入口契约层 **141**、rv-2 竞态/fail-closed 层 **222**（不含本轮新增用例）、§9 原列 8 文件集 **284**、受影响后端合集 **549**；逐文件 `--collect-only` 钉住本轮新增的就是 6 条（`test_agent_runner_issue_control.py` 33→36、`test_console_actions.py` 12→13、`test_process_runner.py` 55→57），542→549 里多出的那 1 条属 rebase 带进来的 main 侧用例；界面契约层新增 capture **Playwright 7 passed**，真实浏览器入口 **Playwright 2 passed (14.2s)**。`just test all` 在同一棵树上 **4301 passed / 1 skipped**（326 秒，exit 0；与第八轮引用的 4274 之差 = 本 PR 新增 6 条 + main 侧增量），`just lint --repo` exit 0，`RV_STDOUT_ASSERTIONS: OK`（item 1 的 42 条 + item 2 的 18 条 pattern 全部在各自 `command` 自身 stdout 命中）。

PRD checker `--check-provided --archive-ready` 现为 PASS（Final Reconciliation 四必填字段完整、验收状态横幅 `🧍 待人工验收` 与 §9 一致：`Human-Confirmed` 两项留空、真实浏览器/PR-runner 相关非人工项以 `[~]` runner-owned gate 表达）。runner-owned 的独立 verifier 门禁与 PR 发布/归档仍为 `[~]`，执行器不代为勾选、不自行 `git mv` 归档。
