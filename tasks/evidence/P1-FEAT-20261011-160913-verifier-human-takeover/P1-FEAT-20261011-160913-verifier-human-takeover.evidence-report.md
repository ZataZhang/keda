## 人审导航 / Human Review Navigation

| 要看什么 | 呈递物 | 打开方式 | 观察与状态 |
|---|---|---|---|
| 真实 Dashboard Issue 详情按原型的信息层级展示运行、确认、处理中、接管完成与冲突态；保留 Draft PR 与目标 HEAD；桌面与窄视口均可用。 | **设计参考**（非真实运行页面）：`rv-1-prototype-reference-desktop.png`（1440 视口）与 `rv-1-prototype-reference-narrow.png`（390 视口），为交付原型 `docs/prototypes/verifier-human-takeover.html` 的忠实无头渲染，右上角标注「交互原型 · 模拟数据」。真实页面截图 `rv-1-verifier-takeover.png`/`-narrow.png` 与 Playwright trace 依赖 `just e2e`，本环境未执行，见「验证限制」。 | `open tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/rv-1-prototype-reference-desktop.png`；窄屏：`open tasks/evidence/P1-FEAT-20261011-160913-verifier-human-takeover/rv-1-prototype-reference-narrow.png`；原型源：`open docs/prototypes/verifier-human-takeover.html` | 原型呈递：Issue 详情头部显示运行身份 `run-…` / 阶段 Independent verifier / 开始时间；独立验证卡「Verifier 正在运行」标注目标提交 `4b19de2`、子进程 `verifier · PID 28417`、提示「PR 会保持 Draft，接管不会被记录为验证通过，也不会自动合并」，主操作「接管此 Issue」标注「只影响 #42，不停止 Runner 或其他 Issue」；PR 卡保持 Draft、验证标签为「— validation/verifier-passed（未设）」；窄视口下卡片不越界。 |

![RV-1 接管信息层级设计参考（桌面 1440 视口，忠实渲染自交付原型 verifier-human-takeover.html，非真实 Dashboard 运行页面）](rv-1-prototype-reference-desktop.png)

![RV-1 接管信息层级设计参考（窄 390 视口，卡片不越界；渲染自同一原型，非真实运行页面）](rv-1-prototype-reference-narrow.png)

**人审状态：执行侧交付可复核的交班/竞态证据齐备，等待人工在具备 `just e2e` 环境后过目真实页面。** rv-1 的每条行为期望（只停当前 Issue 的 verifier 进程组、对照进程存活、取消≠verdict、Draft PR 保留、Issue 转人工 review、autopilot_hold 阻断自动合并、过期 head 被绑定校验拒绝）都有真实跨进程 oracle 现场记录与独立探针判定，不依赖被测代码自述。§2「我默默定了这些」与 §9.1 的两个人工确认项仍**不勾选**，由人工裁定。唯一关联任务为 [GitHub Issue #278](https://github.com/ZataZhang/keda/issues/278)。

## 自动证据摘要

| RV | 状态 | 关键结果 | 原始证据 |
|---|---|---|---|
| rv-1 | PASS（executor evidence，跨进程 oracle 层） | 真实双 OS 进程：接管 CAS 由另一进程写入同一 console.db WAL，runner `cancel_check` 看门狗 1.0s 内观察到、仅对 verifier 进程组 SIGKILL 并回收后抛 InterruptedError。独立探针判定 `FINAL_STATE: taken_over`、`CANCELLED_BY_HUMAN`、`HANDOFF_CONFIRMED`、`AUTOPILOT_HOLD`、`VERIFIER_GROUP_GONE`、`CONTROL_PROCESS_SURVIVED`（对照进程存活=单 Issue 精确）、`MERGE_GATE_HELD` 全 True，`RV1_PROBE_OK: True`。过期 head 绑定负控红→绿（`REAL_STALE_HEAD_STATUS: binding_mismatch` vs 降级后 `BUGGY_STALE_HEAD_STATUS: accepted`）。入口契约层 119 passed。 | `rv-1-verifier-takeover.txt`、`rv-1-takeover-negative-control.txt`、`rv-1-automated-tests.txt`、`rv-1-frontend-checks.txt` |
| rv-2 | PASS（executor evidence，竞态/过期 oracle 层） | 真实 SQLite CAS + 真实线程并发覆盖五态：S1 完成先赢、S2 接管先赢阻断 verdict、S3 并发唯一终态、S4 HEAD 漂移丢弃旧 verdict+置 hold+门阻断、S5 外部合并记录 pr_merged 停止后续；`RV2_PROBE_OK: True`。S4 负控（改用初始 head 快照，红线为 `validation/verifier-passed` 被写到已漂移 PR）红→绿。fail-closed 层 175 passed（merge queue/supervisor/review_once 对 autopilot_hold 阻断，含 require_verifier_pass 关闭时）。原列 8 文件合集 274 passed，13 个受影响后端模块合集 421 passed。 | `rv-2-verifier-race.txt`、`rv-2-automated-tests.txt`、`rv-validation-8-file-tests.txt`、`rv-final-affected-backend-tests.txt` |

证据详情与复现命令见本目录 `evidence.json` 与 `P1-FEAT-20261011-160913-verifier-human-takeover.verification-plan.md`。各 RV 的 PASS 表示 executor 在真实跨进程/真实 SQLite 边界收集的证据符合该项 oracle；真实浏览器端到端呈递层与独立 verifier 复核不在本执行侧结论内（见「验证限制」与「结论」）。

## 验证限制

- **真实浏览器端到端入口未覆盖**：PRD §7 的 `real_entry` 是 `just e2e tests/workflows/verifier-takeover.spec.ts`（UI → Console API → SQLite → runner → verifier 进程组）。当前 spec 用 `page.route` stub Issue 详情与接管动作这两个产品 API，因此只覆盖真实 Dashboard React 组件树上的 UI/API 契约，不覆盖 Console API、SQLite 或 runner；即使运行该 spec，也不能作为 §7 `real_entry` 证据。本环境也缺 `tests/playwright-e2e` 的 node_modules，且 `session.fixture.ts` 需要可登录的完整后端栈，故未执行。真实页面截图 `rv-1-verifier-takeover.png`/`-narrow.png` 与 trace **未生成、未伪造**；需增加不 stub 产品 API 的真实入口用例并在可登录栈中运行，PRD §9 的 Frontend Acceptance、Validation Acceptance 第 3 条和相关 Delivery Readiness 保持 `[~]`，`Human-Confirmed` 两项保持未勾。
- **prototype 渲染的性质**：`rv-1-prototype-reference-*.png` 是交付原型 HTML 的忠实无头渲染（Chrome `--headless=new --screenshot`），右上角自带「交互原型 · 模拟数据」标识，仅用于 §9.1 信息层级呈递，**不得**当作真实 Dashboard 运行页面验收（FR-7 明确原型不作为产品页面或验证替身）。
- **替身边界**：外部 GitHub 与 verifier CLI 由 PRD mock_boundary 允许的确定性替身提供（一个真实阻塞子进程代表 verifier 运行）；Console CAS、本机共享 SQLite 控制行、runner 编排、子进程组终止/回收、竞态裁决全部真实运行。不接触真实业务数据；不证明跨主机协调与真实 GitHub 绕过防护（§11 范围外，最终合并保护由仓库 required checks / branch protection 提供）。
- **tree 绑定**：证据 `.txt` 绑定 HEAD `ac74d40d` 与本次评审工作树 tree（见 `rv-*.txt` 开头绑定块）；当前工作树尚无最终 commit，runner 提交后须重绑最终 committed tree 与证据哈希。断言只锁稳定 marker，不锁动态 PID。
- **门禁**：实施修复后的 `just test` 增量档运行 71 passed、157 deselected；验收结论同时由关闭 testmon 的 13 个受影响后端测试模块复跑 **421 passed** 支持。其中 RV-1 专项契约集 119 passed、RV-2 竞态/fail-closed 集 175 passed，PRD §9 原列 8 文件集 274 passed；`just lint --reuse` 与 `just lint --full` exit 0，`uv run mkdocs build --strict` exit 0；前端 `pnpm typecheck` 与 `pnpm build` exit 0，6 个受影响文件逐文件 ESLint 为 0 errors（27 个既有 JSDoc warnings）。未修改 `tests/guards/**`。
- **隐私**：证据不含凭据；唯一主机地址为脚本临时目录（`tempfile.mkdtemp`）与被测子进程自登记的 PID，无外部网络。

## 结论

rv-1、rv-2 的执行侧证据（真实跨进程接管 + 竞态/过期 oracle，含先红后绿负控）已在当前评审工作树复跑，语义覆盖取消精确性、cancel≠verdict、autopilot_hold fail-closed 与竞态收敛。浏览器 spec 仅验证 Dashboard 组件树上的 API-mocked UI 状态，不满足 PRD `real_entry`；真实 UI → Console API → SQLite → runner 路径仍待实现和运行。PRD 位于 `tasks/archive/`，横幅 `🧍 待人工验收`；`Human-Confirmed` 两项及真实浏览器入口相关 `[~]` 项继续保留。独立 verifier 复核与 PR 发布仍为 runner-owned 门禁；本报告不声称人工验收或浏览器端到端验证已通过。
