## 人审导航 / Human Review Navigation

Issue：[GitHub #334](https://github.com/ZataZhang/keda/issues/334) · PR：[查询 #334 相关 PR](https://github.com/ZataZhang/keda/pulls?q=is%3Apr+is%3Aopen+is%3Aall+334) · CI：[GitHub Actions](https://github.com/ZataZhang/keda/actions)

本地人工审阅包：在 worktree 根目录运行 `just prd review tasks/archive/P1-FEAT-20261019-140531-m13-golden-eval-baseline.md`，或打开 `"/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/human-review-checklist.html"`。该页面使用相对路径加载本地截图，不需要启动应用。

当前没有创建 PR；runner 在执行独立 verifier 与发布流程。实现源码的已验证 Git tree（排除 PRD、证据目录和 runner 交付记录）为 `9c4253e1249b4d730bc4be7528cd4c0636c1ce80`。

| # | 对应 §9.1 验收结果 | 呈递物、打开方式与预期值 |
|---|---|---|
| 1 | 12 道任务全部校准通过，校准期间没有模型调用（rv-2）。 | 文件：`/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-2-calibration-console.png`。![rv-2 keda-history-v1 真实 Console 校准页](rv-2-calibration-console.png) **本地图片，GitHub 上不显示。** 打开：`open "/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-2-calibration-console.png"`。输出：[rv-2-calibration-12of12.txt](rv-2-calibration-12of12.txt)，选题：[rv-2-seed-selection.json](rv-2-seed-selection.json)。预期：页头为“12/12 已校准”；详情逐题为 @noop 3 次判分失败、@gold 3 次判分通过；运行 72 格、usage rows=0、provider probe=0。 |
| 2 | 重复运行显示通过次数与稳定性，不稳定题单列，无合成总分或阈值（rv-3）。 | 目标态原型：`/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-3-prototype-variance-view.png`。![设计意图：重复结果目标态，非运行时证明](rv-3-prototype-variance-view.png) **本地图片，GitHub 上不显示。** 打开：`open "/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-3-prototype-variance-view.png"`。真实 Console：`/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-3-stability-console.png`。![真实 FastAPI Console 页面：重复结果与不稳定题](rv-3-stability-console.png) **本地图片，GitHub 上不显示。** 打开：`open "/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-3-stability-console.png"`。复核输出：[API 与方差探针](rv-3-stability-api.txt)、[真实页面 Playwright E2E](rv-3-playwright-e2e.txt)。预期：stable-outcome 为 3/3 稳定，repeat-outcome 为 2/3 不稳定并单列；无合成总分或阈值。原型为设计目标，后图为真实页面，数据和桌面视口对应。 |
| 3 | 12 题来源、描述与留出资格有审计依据（§7.1）。 | 选题和复核：[rv-2-seed-selection.json](rv-2-seed-selection.json)；公开来源映射：[tasks/evaluation-suites/README.md](../../evaluation-suites/README.md)。打开：`open "/Users/zata/code/keda/.kc-worktrees/issue-334/tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-2-seed-selection.json"`。预期：随机抽查三题可追溯 sample id、来源摘要、base/final、输入复核和旧校准记录；prompt 只写问题与期望行为；留出资格有使用依据。 |

**已替你核对过什么：** rv-2 通过真实 API、Git 快照、uv-pytest、账本读取和 Console 截图交叉核对；rv-3 使用相同代表性结果对照目标原型与真实页面，并通过 4 个既有 E2E 用例；rv-2 seed-selection 与公开 README 的映射一致。图片和命令都已逐项检查。这里没有替 reviewer 勾选 §9.1 人工确认项。PR 发布、独立 verifier 与 CI 由 runner 后续处理；上面的 PR/CI 链接可直接查询。

## 执行证据索引

六项 Realistic Validation 的逐项复现命令、negative control、expected fail、输出断言和风险见 [`evidence.json`](evidence.json) 及 [verification plan](P1-FEAT-20261019-140531-m13-golden-eval-baseline.verification-plan.md)。每项文本输出末尾均记录上述 source tree。原始截图、脚本及执行日志按仓库约定不进入代码 diff；三个 Markdown 报告与 PRD 是可提交交付记录。
