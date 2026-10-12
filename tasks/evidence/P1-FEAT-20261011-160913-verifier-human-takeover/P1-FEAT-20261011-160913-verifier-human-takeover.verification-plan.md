# 验证计划

## 范围与规则

按 PRD §7 的 rv-1、rv-2 顺序验证最终工作树。每项先运行能让对应断言变红的负控，再运行修复后路径；证据按 `rv-<item_number>-<slug>.<ext>` 单项保存于本目录，原始输出与全部 RV 脚本位于同目录 `scripts/` 与 `rv-*.txt`，受仓库 `.gitignore` 证据白名单约束（仅 `*.md` 与 `evidence.json` 进入代码 diff，`.txt`/`.png`/`scripts/*.py` 为原始件不入库）。真实入口不替换为 mock；仅在 PRD §7 mock_boundary 明确允许处替换外部 GitHub 与 verifier CLI。

本轮复核源代码提交为 `85d69699fff75d5ee78d683a48aeb78347f7f7b5`，证据 `.txt` 绑定 record-excluded tree `356fd11f581a665315176042dfcb8fe8d386b21e`（见各 `rv-*.txt` 绑定块）。后续只更新本证据目录中的报告文本时，该 record-excluded tree 保持不变；runner 仍需在最终提交后按机器契约记录最终 committed tree 与证据哈希。

## 真实入口与执行环境边界

rv-1、rv-2 在 §7 声明的最高保真入口为真实浏览器 `just e2e tests/workflows/verifier-takeover.spec.ts`（UI → Console API → SQLite → runner → verifier 进程组）。当前 Playwright spec 用 `page.route` stub Issue 详情和接管动作两个产品 API，因此只覆盖真实 Dashboard 组件树上的 UI/API 契约，不能作为 §7 `real_entry` 验收证据。本执行环境也缺 `tests/playwright-e2e` 的 node_modules，且 `fixtures/session.fixture.ts` 需要可登录的完整后端栈，故 spec 未执行；真实页面截图与 trace 留待实现不 stub 产品 API 的用例后在人工/CI 环境补跑（PRD §9 对应项以 `[~]` runner-owned gate 挂起，未伪造）。执行侧以**真实跨进程 oracle**（接管请求与被取消的 verifier 分属两个 OS 进程、走同一 SQLite WAL 文件；真实 `SubprocessRunner` 进程组 SIGKILL 与回收；真实行级 CAS 竞态）+ 后端契约测试覆盖进程/DB/CAS/取消/竞态语义。两张 prototype PNG 为交付原型 `docs/prototypes/verifier-human-takeover.html` 的忠实无头渲染，仅作 §9.1 信息层级设计参考呈递，**非真实运行页面截图**。

## Oracle 执行计划与结果

| 项目 | 最高可行入口与执行 | 负控与预期失败 | 结果与证据 |
|---|---|---|---|
| rv-1 · R3 | `scripts/rv1_takeover_handoff_probe.py`：verifier 替身子进程登记 PID 后长阻塞；发起接管的 Console CAS 来自另一 OS 进程（延迟 2s 写 running→takeover_requested，同一 console.db WAL）；runner 侧 `cancel_check` 看门狗秒级观察、仅对目标进程组 SIGKILL 并回收后抛 InterruptedError；独立探针（新连接 + os.kill）判定最终事实。 | `scripts/rv1_negative_control.py`：真实代码对过期 head 返回 `binding_mismatch` 且不触发取消；把 head 比对降级为放行后同一过期请求被接受为 `takeover_requested` 并触发取消。 | 负控红→恢复后绿：`RV1_PROBE_OK: True`（FINAL_STATE taken_over、CANCELLED_BY_HUMAN、HANDOFF_CONFIRMED、AUTOPILOT_HOLD、VERIFIER_GROUP_GONE、CONTROL_PROCESS_SURVIVED、MERGE_GATE_HELD 全 True）；`RV1_NEGATIVE_CONTROL_OK: True`。自动化契约层 119 passed（含新增 `tests/test_agent_runner_issue_control.py`）。见 `rv-1-verifier-takeover.txt`、`rv-1-takeover-negative-control.txt`、`rv-1-automated-tests.txt`、`rv-1-prototype-reference-*.png`。真实浏览器层未执行，见上。 |
| rv-2 · R3 | `scripts/rv2_race_and_staleness_probe.py`：真实共享 SQLite + 真实线程并发覆盖 S1 完成先赢、S2 接管先赢、S3 并发唯一终态、S4 HEAD 漂移丢弃旧 verdict、S5 外部已合并停止后续。 | 内置 S4 负控：把漂移检测改用初始 head 快照（模拟过期结果保护失效），要求过期 verdict 被错误保留时探针变红。 | 负控红→恢复后绿：`RV2_PROBE_OK: True`、`S1..S5` 各态 True、`RV2_EXPECTED_FAIL` + `S4_NEG_STALE_NOT_RECORDED: True`。自动化竞态/fail-closed 层 175 passed（merge queue/supervisor/review_once 对 autopilot_hold 阻断，含 require_verifier_pass 关闭时）。见 `rv-2-verifier-race.txt`、`rv-2-automated-tests.txt`。 |

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

pytest 项目 `addopts` 含 `--testmon`，会按文件改动选择性反选用例；本证据链一律用 `-o addopts=""` 关闭 testmon 以全量运行，得到稳定计数。修复后 rv-1 层 119、rv-2 层 175、§9 Validation 第 1 条原列 8 文件合集 274；新增的 `tests/test_agent_runner_issue_control.py` 单独为 20 项。

`just test all`（独立 verifier 的实际门禁命令）首跑为 4 failed / 4245 passed：4 项都是既有 golden 配置断言在托管 runner 环境下读到宿主机器级 `config.toml`（注入的 `KEDACODE_CONFIG`/`IAR_CONFIG` 在 conftest 导入期就把 `settings.config` 单例定型，会话级剥离 fixture 来不及；本机那份相对仓库根缺 PRD map check 规则段且 `pre_pr_review.timeout_seconds` 为 900），与本 PRD 行为无关。测试侧新增 `tests/support/agent_runner.py::root_config_app_config()` 后复跑 **4249 passed / 1 skipped**——断言强度不变、`config.toml` 与生产代码未动。

PRD checker `--check-provided --archive-ready` 现为 PASS（Final Reconciliation 四必填字段完整、验收状态横幅 `🧍 待人工验收` 与 §9 一致：`Human-Confirmed` 两项留空、真实浏览器/PR-runner 相关非人工项以 `[~]` runner-owned gate 表达）。runner-owned 的独立 verifier 门禁与 PR 发布/归档仍为 `[~]`，执行器不代为勾选、不自行 `git mv` 归档。
