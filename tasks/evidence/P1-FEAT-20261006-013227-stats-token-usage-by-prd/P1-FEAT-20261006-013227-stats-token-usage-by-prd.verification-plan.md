# Realistic Validation 验证计划 — Stats 页 Token 用量补「按 PRD」维度汇总

PRD: `tasks/pending/P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`
分支: `issue-209` @ `8e6645cf`（实现未提交，由 runner 提交）
Issue: https://github.com/ZataZhang/keda/issues/209

## 验证目标

证明 PRD 维度 token 汇总从真实账本一路走到真实页面：`stats/prd-lifecycle` 端点响应新增 `token_usage_by_prd` 且既有字段一字不动（rv-1）；页面「按 PRD」表与 `iar tokens` 对同一账本、同一窗口的数字逐格相等（rv-2）；账本读失败时端点 200 降级为空数组（rv-3）；Stats 页真实浏览器三表同屏、首行为最大消耗者（rv-4）；「按流程 / 按 agent」两表与 token 提取规则零回归（rv-5）。

## Oracle 分层（风险序 rv-2 → rv-1 / rv-3 / rv-4 / rv-5）

| id | tier/reviewer | 真实入口 | mock 边界 | 判别力负控 |
|---|---|---|---|---|
| rv-1 (R1, verifier) | 真实 HTTP：worktree `iar console --port 8477` + 真实 `~/.iar/console.db` | 无 | 改动前实例（8313 守护）同断言必红（字段缺失，实测 RED） |
| rv-2 (R2, human) | 真实浏览器 DOM vs 真实 CLI 进程 vs fresh 端点响应三方逐格对照 | 无（截图配 `rv-2-page-by-prd.png` + `rv-2-cli-tokens.png`） | 临时把页面 total ×2 重建 → 对照全行检出、非零退出（实测 RED） |
| rv-3 (R1, verifier) | 真实 `iar console` + `IAR_CONFIG` 指向真实账本临时副本，DROP 表使读在请求时刻失败 | 仅故障注入位置在副本库，生产代码无开关 | 同注入下无保护端点 `/console/runs` 必须 5xx（实测 500，证明注入有效） |
| rv-4 (R1, human) | `just console-sync` 后真实 `/app/stats`（真实 FastAPI 静态托管 + 真实 API + 真实账本） | 无 | 临时摘除第三张表重建 → waitForSelector 超时、非零退出（实测 RED） |
| rv-5 (R0, verifier) | 改动前后两个真实服务实例的 `token_usage` 规范化 JSON 逐字节对照 + `aggregate_token_usage` 函数体 HEAD vs 工作区源码对照 | 无 | 断言本身即对照式：任何口径污染立即非零退出；重试环消除账本实时写入噪声 |

rv-2 的 `critical_value_source`：端点 `by_prd[].totals.total_tokens` vs CLI 表同行总量；`must_cross`：HTTP 路由 + SQLite 账本 + 浏览器渲染后可见文本；`forbidden_bypasses` 全部未触碰（无 fixture 响应、无 use_case 直调、无绕端点读库）。

## 单元/契约层（仓库门禁）

- `tests/test_prd_lifecycle.py` 新增 5 例：字段挂载与排序、读集复用（`run_list_calls==1 && event_list_calls==1`）、读失败降级、TestClient 端点契约、不可读账本 200 降级。
- `tests/playwright-e2e/tests/smoke/stats-token-usage-by-prd.spec.ts` 3 例（fixture 响应，CI 确定性）：三表行序与单元格文本、旧响应缺字段 → 表级空态、窗口无用量 → 区级空态；本地经 `just e2e` 真实浏览器 4 passed（含 auth setup）。
- `CI=true just test all`（强制全量 pytest + `SKIP=check-test-flag just lint --full`）。

## 证据绑定最终代码树

- 每个 RV 脚本（capture/setup/oracle）位于 gitignored 的 `.iar/evidence/scripts/`，不进入代码 diff；原始 `.txt`/`.png` 与 `evidence.json` manifest 留在 `.iar/evidence/`（runner 侧），并复制一份到本目录（gitignored，仅 .md 可提交）。
- 冻结凭证：`git diff HEAD -- src tests "frontend-public/app" "frontend-public/lib" | shasum -a 256 = 7099a113c233dabef788c35d5dfe1b66020909cefc87607c56074ae275720471`（合并前这些路径若有变更须重采）。
- 负控期间的临时前端改动（×2 / 摘表）已逐字节还原并经上述 diff 冻结凭证与 `git status` 复核，最终 tree 不含任何验证期改动。
