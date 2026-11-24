# Verifier Report — P2-FEAT-20261120-145338-companion-human-action-notifications

- 验证对象：`main` HEAD `616d0ea7`（PR #427 squash）+ 工作区中未提交的 spec 修复（D1）
- 验证日期：2026-11-24（第 2 轮复核，覆盖第 1 轮 REJECT 报告）
- 验证者：独立 verifier（只写了本报告，没有改动任何仓库文件）

Verdict: PASS

**附带条件（必须写进 Final Reconciliation，否则本 PASS 不成立）：**

1. **C1（D2，完成消息）**：第 11 项按 §9 原文“PR 证据评论**或完成消息**”接受为**有条件通过**。完成消息验证者看不到，所以这一项不是验证者核实过的结论。完成消息必须原样引用 `rv-1-notification-calls.txt` 的 8 轮记录，并附上证据路径。用户或协调方看到的消息里如果没有这些内容，这一项回到 not proven，本 PASS 随之失效。
2. **C2（D1 修复尚未进 main）**：加强后的 rv-4 断言目前只在工作区（`git status` 显示 spec 为 `M`，未提交）。main 上的 spec 仍然是旧的空断言。按仓库规则不能自动提交，所以要在用户同意后通过后续 PR 合入，并在 Final Reconciliation 里记为 follow-up。生产代码 `page.tsx` 不需要任何改动。

## 第 1 轮缺陷的处理情况

- **D1（已解决，条件 C2）**：`git diff -- tests/playwright-e2e` 已复核。`installTauriStub(page, markAsDesktopHost = true)` 在参数为 false 时只安装 `__TAURI_INTERNALS__.invoke` 记录器，不设置 `window.isTauri`；“a plain browser never requests notifications”用例先调用 `installTauriStub(page, false)`。
  - 反向对照：`rerun-616d0ea7/negative-control-guard-removed.txt` 记录了临时去掉 `if (!nativeDesktopApp) return` 后该用例变红，收到 1 条 `notify_task`（`Keda Main#21 待你审阅`），exit=1。这个结果与第 1 轮验证者探针的对照组一致。
  - `git diff -- "frontend-public/app/(companion)/app/companion/page.tsx"` 为空，确认已恢复。
  - 验证者复跑修复后的 spec：4 passed（11.4s），与 `rerun-616d0ea7/rv-1-e2e-run-after-d1-fix.txt` 一致。
- **D2（有条件接受，见 C1）**：PR #427 正文与评论都没有 9.1 原样内容（`gh pr view 427 --json body,comments`，`comments: []`）。§9 原文允许改用完成消息。执行器不在未经用户同意的情况下发对外评论，这一做法合理。
- **D3（非阻塞，转 follow-up）**：e2e 仍未覆盖“首轮已处于 blocked 不补发”和 `已失败` 标题，这两点目前只靠代码阅读证明。
- **e2e 包 typecheck 更正**：`tsc --noEmit -p tests/playwright-e2e` 退出码是 2，不是 0，执行器已在后续消息中更正。验证者复跑，唯一的错误是 `tests/smoke/idea-inbox.spec.ts(124,40): error TS2339: Property 'text' does not exist on type '{}'`。
  - 该文件在工作区未改动，最后一次修改是 `c350a4d6`，早于 `616d0ea7`（已用 `git merge-base --is-ancestor` 确认）。
  - `companion-notifications.no-auth.spec.ts` 没有类型错误。
  - 所以这是 main 上已有的问题，与本 PRD 无关，不影响判定；建议另行登记修复。§9 要求的 `frontend-public` typecheck/lint 不受影响（见第 5 项）。

## 逐项判定（§9 执行侧勾选项）

| # | 区域 | 勾选项 | 判定 | 证据 / 命令 |
|---|---|---|---|---|
| 1 | Behavior | running → human_review 恰好 1 条 `…#21 待你审阅`；同状态与 recheck 往返无新增；重新 running 后再进入新增 1 条 | proven | `rv-1-notification-calls.txt`：8 轮记录，第 2、7 轮各 1 条待你审阅，第 3~6 轮无新增；验证者两轮复跑均为 4 passed；代码：`status==="running"` 时执行 `notifiedKinds.delete`，`recheck` 的类别为 null，直接 `continue`、不改动记录 |
| 2 | Behavior | 首轮已处于 human_review / blocked 的任务不补发 | proven（blocked 部分只靠代码阅读，D3） | human_review：spec 用例 2 断言 0 条；blocked：基线轮的判断与类别无关（`isBaselineRound` 时只 `set`、不 `invoke`） |
| 3 | Behavior | 阻塞 / 失败提醒标题文案不变（`已阻塞` / `已失败`） | proven（`已失败` 只靠代码阅读，D3） | `rv-4-blocked-notification.txt`：`Keda Main#21 已阻塞`；`git diff 616d0ea7^ 616d0ea7`：旧字面量原样迁入 `ATTENTION_TITLE_WORDS` |
| 4 | Frontend | 正文只来自 `issue_title ?? status_reason ?? 兜底文案` | proven | `rg -n "blocking_reason\|last_error_summary\|failure_reason" "frontend-public/app/(companion)/app/companion/page.tsx"` 无输出（exit 1）；spec 用例 3 断言阻塞摘要的 marker 没有出现在通知里 |
| 5 | Frontend | `pnpm --dir frontend-public typecheck` 与 `lint` 通过（输出存证） | proven | `rerun-616d0ea7/typecheck.txt`（exit=0）和 `rerun-616d0ea7/lint.txt`（0 errors，exit=0）；验证者复跑 `frontend-public` 的 `tsc --noEmit`（exit 0）和 `eslint` 页面文件（exit 0）。e2e 包已有的 TS 错误见上文，不属于本项 |
| 6 | Frontend | 非桌面宿主 0 次 invoke、无 pageerror（rv-4） | proven（修复在工作区，C2） | 修复后的用例 4 装了记录器、没有标记宿主，结果为 0 次调用、无 pageerror；去掉守卫的反向对照变红（`rerun-616d0ea7/negative-control-guard-removed.txt`）；第 1 轮验证者的独立探针结论相同 |
| 7 | Documentation | `desktop-companion.md` 两处“系统通知”说明列出四类提醒、防刷屏规则，并写明“已关闭”不区分合并与手动关闭 | proven | `git diff 616d0ea7^ 616d0ea7 -- docs/guides/desktop-companion.md`：macOS 节完整说明；Windows 节列出四类提醒和防刷屏规则，并注明“与 macOS 相同” |
| 8 | Documentation | `summary.md` 指向本 PRD；`ideas.md` 未改写 | proven | `git diff 616d0ea7^ 616d0ea7 -- tasks/inbox/ideas.md` 为空；`summary.md` 的“已升级”节新增了指向本 PRD 的条目 |
| 9 | Validation | 真实页面入口 e2e 通过，终端输出存为 `rv-1-e2e-run.txt` | proven | `rv-1-e2e-run.txt`、`rerun-616d0ea7/rv-1-e2e-run.txt`、`rerun-616d0ea7/rv-1-e2e-run-after-d1-fix.txt`（均为 4 passed）；验证者复跑：`next dev -p 3961` + `PLAYWRIGHT_SKIP_STACK_BOOT=1 ... playwright test --project=no-auth tests/smoke/companion-notifications.no-auth.spec.ts`，4 passed |
| 10 | Validation | 负向对照：实现前运行该 spec 为红 | proven | `negative-control-run.txt`：实现前用例 1 失败，各轮通知均为 `[]`；另有 `rerun-616d0ea7/negative-control-guard-removed.txt` 覆盖非宿主守卫 |
| 11 | Delivery Readiness | PR 证据评论或完成消息原样携带 9.1 内容（rv-1 调用记录内联） | **有条件接受（C1），验证者无法核实** | `gh pr view 427 --json body,comments`：PR 正文和评论里都没有这些内容；能否通过取决于完成消息是否原样引用 8 轮记录并附上证据路径 |

跳过的三项：两条 Human-Confirmed，以及“独立 verifier 判定 PASS”本身。rv-3（opt-in，`required_for_acceptance: false`）未采集，不影响判定。

## mock 边界核对

spec 只用 `page.route` 顶替了 HTTP 读响应：`runtime/tasks`、`desktop-companion/status`、`/api/auth/me`、issue logs。另外用 `addInitScript` 注入 Tauri invoke 桥。后三个接口只是页面渲染需要的附属读接口，不参与通知判定，这一点与 PRD mock_boundary 的字面有轻微偏差。`DesktopCompanion` 的轮询、`handleSummary` 和 `companionAttentionKind` 都走生产代码，没有被绕过。

## Follow-ups（写入 Final Reconciliation）

- C2：经用户同意后，把 spec 的 D1 修复通过后续 PR 合入 main。
- D3：补 e2e 用例，覆盖“首轮已处于 blocked 不补发”和 `已失败` 标题（非阻塞）。
- main 上已有问题：`tests/playwright-e2e/tests/smoke/idea-inbox.spec.ts(124,40)` 的 TS2339，与本 PRD 无关，另行登记。
- rv-3 真机系统通知截图：opt-in，可在合并后补采。

## 验证者执行的命令（两轮合计）

- `rg -n "blocking_reason|last_error_summary|failure_reason" "frontend-public/app/(companion)/app/companion/page.tsx"`：无输出
- `git diff 616d0ea7^ 616d0ea7 -- tasks/inbox/ideas.md`：空
- `git diff -- tests/playwright-e2e`：复核 D1 修复
- `git diff -- "frontend-public/app/(companion)/app/companion/page.tsx"`：空
- `frontend-public`：`tsc --noEmit` exit 0；`eslint "app/(companion)/app/companion/page.tsx"` exit 0
- `tests/playwright-e2e`：`tsc --noEmit -p .` exit 2，唯一错误在 `idea-inbox.spec.ts`；`git merge-base --is-ancestor c350a4d6 616d0ea7^` 为真
- 修复前、修复后各跑一次 spec：都是 4 passed。dev server（端口 3961）用后已停止。运行 spec 会重写 `rv-1-notification-calls.txt` 和 `rv-4-blocked-notification.txt`，重写后内容不变
- 第 1 轮一次性探针（scratchpad 内，不入库）：只装记录器、不设 `isTauri` 时，`invokeCalls: []`；设 `isTauri=true` 时记录到 3 条
- `gh pr view 427 --json body,comments`
