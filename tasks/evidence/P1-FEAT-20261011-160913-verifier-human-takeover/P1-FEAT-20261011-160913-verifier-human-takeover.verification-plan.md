# 验证计划

## 范围与规则

按 PRD §7 的 rv-1、rv-2 顺序验证最终工作树。每项先运行能让对应断言变红的负控，再运行修复后路径；证据按 `rv-<item_number>-<slug>.<ext>` 单项保存于本目录，原始输出与全部 RV 脚本位于同目录 `scripts/` 与 `rv-*.txt`，受仓库 `.gitignore` 证据白名单约束（仅 `*.md` 与 `evidence.json` 进入代码 diff，`.txt`/`.png`/`scripts/*.py` 为原始件不入库）。真实入口不替换为 mock；仅在 PRD §7 mock_boundary 明确允许处替换外部 GitHub 与 verifier CLI。

本轮（第四轮评审修复）复核的已提交实现为 `abc3a55790e065ae9483d7920f41d12ac18153ea`，其 tree `a295951a7a33200b0a8a723a4da70d6950816eec` 可由任何 reviewer 用 `git rev-parse abc3a557^{tree}` 解析。8 份证据 `.txt` 在该提交之上、承载本轮拆分改动的工作树上逐字复跑，绑定块给出 `HEAD`/`TREE`/`RUN_ON`/`CODE_DELTA`/`RECORD_EXCLUDED` 五项（见各文件开头），不再引用只能按临时 index 配方重算、且不可从 ref 解析的合成 record-excluded tree（前三轮的 `b6e92e21…` / `e66a427e…`）。本轮改动为把 1007 非空行的 `run_verifier_agent.py` 按 #279 的做法拆成 `agent_runner_verdict.py`（verdict 协议）、`agent_runner_verifier_response_log.py`（原始响应落盘）、`agent_runner_verdict_pr_effects.py`（PR 侧副作用），编排与候选 agent 选择留在原模块、协议名继续重新导出，捕获结果与上一轮逐字一致（135 / 221 / 280 / 523 passed、两支探针 OK）。后续只更新本证据目录中的报告文本时，代码断言不变；runner 仍需在最终提交后按机器契约记录最终 committed tree 与证据哈希。

## 真实入口与执行环境边界

rv-1、rv-2 在 §7 声明的最高保真入口为真实浏览器 `just e2e tests/workflows/verifier-takeover.spec.ts`（UI → Console API → SQLite → runner → verifier 进程组）。当前 Playwright spec 用 `page.route` stub Issue 详情和接管动作两个产品 API，而这两段正是 rv-1 `must_cross` 要求真实穿过的部分，因此**该 spec 即使在完整栈上跑绿也不能作为 §7 `real_entry` 验收证据**（不是"没跑"而是"跑法不对"，去掉 stub 才是可执行路径）。环境事实已在第三轮修复时校正：`just e2e-install` 能落地 `tests/playwright-e2e` 依赖与 chromium（本轮实测 `npm ci` 成功、本机已有 chromium 缓存），真正缺的是**隔离的可登录全栈**——本机 `kc console` 正占用机器级 `console.history.db` 与本 Issue 的真实注册表，若在其上种一条假 `running` 控制行并点真实接管，会把伪造的控制状态与 `autopilot_hold` 写进操作员的真实控制面，故执行侧不在本机跑真实接管。真实页面截图与 trace 未生成；rv-1 的处置（在隔离栈去 stub 跑一次，或明确接受「跨进程 oracle + UI/API 契约层」的组合保真度）已作为第 3 条 `Human-Confirmed` 交回人工，PRD §9 相应项保持 `[~]`，未伪造。执行侧以**真实跨进程 oracle**（接管请求与被取消的 verifier 分属两个 OS 进程、走同一 SQLite WAL 文件；真实 `SubprocessRunner` 进程组 SIGKILL 与回收；真实行级 CAS 竞态）+ 后端契约测试覆盖进程/DB/CAS/取消/竞态语义。两张 prototype PNG 为交付原型 `docs/prototypes/verifier-human-takeover.html` 的忠实无头渲染，仅作 §9.1 信息层级设计参考呈递，**非真实运行页面截图**。

## Oracle 执行计划与结果

| 项目 | 最高可行入口与执行 | 负控与预期失败 | 结果与证据 |
|---|---|---|---|
| rv-1 · R3 | `scripts/rv1_takeover_handoff_probe.py`：verifier 替身子进程登记 PID 后长阻塞；发起接管的 Console CAS 来自另一 OS 进程（延迟 2s 写 running→takeover_requested，同一 console.db WAL）；runner 侧 `cancel_check` 看门狗秒级观察、仅对目标进程组 SIGKILL 并回收后抛 InterruptedError；独立探针（新连接 + os.kill）判定最终事实。 | `scripts/rv1_negative_control.py`：真实代码对过期 head 返回 `binding_mismatch` 且不触发取消；把 head 比对降级为放行后同一过期请求被接受为 `takeover_requested` 并触发取消。 | 负控红→恢复后绿：`RV1_PROBE_OK: True`（FINAL_STATE taken_over、CANCELLED_BY_HUMAN、HANDOFF_CONFIRMED、AUTOPILOT_HOLD、VERIFIER_GROUP_GONE、CONTROL_PROCESS_SURVIVED、MERGE_GATE_HELD 全 True）；`RV1_NEGATIVE_CONTROL_OK: True`。自动化契约层 135 passed（含 `tests/test_agent_runner_issue_control.py` 与修复层用例：verdict 收回 / 孤儿请求失效 / 无 repo_id 兜底落闸 / PR 编号回写，以及本轮新增的接管请求存储异常 fail-closed、SQLite 镜像常量对齐核心端口、PR 读失败仍完成 Issue 侧交班、看门狗取消分类优先于超时分类回归用例）。见 `rv-1-verifier-takeover.txt`、`rv-1-takeover-negative-control.txt`、`rv-1-automated-tests.txt`、`rv-1-prototype-reference-*.png`。真实浏览器层未执行，见上。 |
| rv-2 · R3 | `scripts/rv2_race_and_staleness_probe.py`：真实共享 SQLite + 真实线程并发覆盖 S1 完成先赢、S2 接管先赢、S3 并发唯一终态、S4 HEAD 漂移丢弃旧 verdict、S5 外部已合并停止后续。 | 内置 S4 负控：把漂移检测改用初始 head 快照（模拟过期结果保护失效），要求过期 verdict 被错误保留时探针变红。 | 负控红→恢复后绿：`RV2_PROBE_OK: True`、`S1..S5` 各态 True、`RV2_EXPECTED_FAIL` + `S4_NEG_STALE_NOT_RECORDED: True`。自动化竞态/fail-closed 层 221 passed（merge queue/supervisor/review_once/interactive_decision 对 autopilot_hold 阻断，含 require_verifier_pass 关闭与 repo_id 解析不出时兜底落闸）。见 `rv-2-verifier-race.txt`、`rv-2-automated-tests.txt`。 |

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

pytest 项目 `addopts` 含 `--testmon`，会按文件改动选择性反选用例；本证据链一律用 `-o addopts=""` 关闭 testmon 以全量运行，得到稳定计数。本轮修复层落地后 rv-1 契约层 135、rv-2 竞态/fail-closed 层 221、§9 Validation 第 1 条原列 8 文件合集 280；`tests/test_agent_runner_issue_control.py` 单独为 33 项。

`just test all`（独立 verifier 的实际门禁命令）首跑为 4 failed / 4245 passed：4 项都是既有 golden 配置断言在托管 runner 环境下读到宿主机器级 `config.toml`（注入的 `KEDACODE_CONFIG`/`IAR_CONFIG` 在 conftest 导入期就把 `settings.config` 单例定型，会话级剥离 fixture 来不及；本机那份相对仓库根缺 PRD map check 规则段且 `pre_pr_review.timeout_seconds` 为 900），与本 PRD 行为无关。第三轮把修复收在根因：`tests/conftest.py` 的会话级隔离 fixture剥离两名宿主变量后重建 `config.agent_runner`，使 `build_app_config()` 与 CI 解析到同一份仓库根配置（断言仓库根真实内容的用例仍显式 pin 来源：`tests/support/agent_runner.py::root_config_app_config()`）；复跑 **4270 passed / 1 skipped**——断言强度不变、`config.toml` 与生产代码未动。

PRD checker `--check-provided --archive-ready` 现为 PASS（Final Reconciliation 四必填字段完整、验收状态横幅 `🧍 待人工验收` 与 §9 一致：`Human-Confirmed` 两项留空、真实浏览器/PR-runner 相关非人工项以 `[~]` runner-owned gate 表达）。runner-owned 的独立 verifier 门禁与 PR 发布/归档仍为 `[~]`，执行器不代为勾选、不自行 `git mv` 归档。
