# 独立验证与代码审查报告 · Issue #314

## 评审基准

- 需求来源：`tasks/archive/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease.md` 与 GitHub Issue #314。
- 要实现：GitHub 上按仓库共享机器在线状态；`kc peers` 表格/JSON；含 machine_id 的独立租约；远端过期对账；续租失败后自停；push/PR 前认领确认；兼容无租约旧认领。
- 明确不做：新协调服务、数据库迁移、标签状态机重构、租约有效时强制接手、续传另一台机器的本地 WIP、Console 机器页面。
- 真实交付边界：CLI 与 daemon、GitHub Issue/comment adapter、claim arbitration/reconcile/admission/publication、machine identity/config、guide 与 packaged operator skill。
- 最终候选实现树：`6658504731e5dbc2cfc5b751d470123789c3464c`（不含本 PRD 与 evidence delivery records），base HEAD tree `d03d049a1a95efc7f5b19e2d1a3247884a936ed3`。

## Findings

第二轮代码审查提出 8 项发现，本轮全部处理，未遗留未闭环项：

| 级别 | 发现 | 处理 |
|---|---|---|
| 高 | 仲裁的「本机重入」判定绕过 machine_id（`agent_runner_claim_arbitration.py:322`）：同名同 PID 的两台机器都会早返回，各自把标签切成 `agent/running` | `ClaimBid` 承载该投标声明的 `kc:lease`，重入改由 `_winner_is_own_replay()` 判定：主机名 + PID 相符后仍须过 `is_local_claim`（带租约按机器编号、旧投标才回退主机名）。回归测试 `test_same_host_same_pid_remote_lease_bid_is_not_a_reentry`（保留合法重入的 `test_own_leased_earlier_bid_still_reenters`） |
| 中 | 推送/开 PR 前的归属栅栏在未登记认领时静默放行（`coordination_heartbeat.py:331`） | 先复用 `_resolve_claim_repo_id()` 消化 repo_id/config 漂移；仍找不到认领时改走 `assert_claim_owned_by_machine()` 回读服务端，按机器编号确认最近一条可信认领归属本机，确认不了抛 `ClaimLeaseLost`。回归测试覆盖远端认领被拒、本机认领放行、repo_id 漂移三种情形 |
| 中 | 归档 PRD §9 与 verifier 报告把证据绑到过期的候选树（`1915539…` / `96 passed`） | §9.2、Behavior/Validation Acceptance、Final Reconciliation 与 Change Log 全部改绑本轮四项 RV 实际使用的 `6658504…`，测试数改为 `149 passed`；本报告与 evidence report / verification plan 同步 |
| 低 | 两个 daemon 可各写一条 `remote_lease_expired` 对账评论（`agent_runner_reconcile.py:236` 的读-检查-写） | 写评论前重读线程复核 dedupe hash，把窗口压到「这一读与那一写」之间，并在模块 docstring 里显式披露残余窗口与标签侧 CAS 兜底。回归测试 `test_peer_comment_landing_mid_pass_is_not_duplicated` |
| 低 | guide 夸大租约客户端时限为「单次请求 5 秒」（`docs/guides/agent-runner.md:421`） | 改为按**单次尝试**表述，写明最多 3 次尝试 / 1 秒间隔 / 单轮最坏约 17 秒、续期线程串行导致一条卡住会推迟其它认领，以及自停按单调时钟累计判定 |
| 低 | `kc peers` 把评论作者写的 hostname/version/agent 原样送进 Rich 单元格；presence JSON 未转义 `>`（`peers.py:144`、`machine_presence.py:205,229`） | 新增 `_display_field()`：剥控制字符、`rich.markup.escape`、限长后再入表；presence 正文用 `\\u003c`/`\\u003e` 转义尖括号，`-->` 不再提前闭合 JSON 区块。回归测试 `test_presence_marker_payload_survives_angle_bracket_hostname`、`test_display_field_strips_control_sequences_and_markup`、`test_peers_human_table_never_emits_raw_terminal_controls` |
| 低 | 无心跳时本机带租约的认领被判成远端（`agent_runner_claim_arbitration.py:300`、`run_agent_daemon.py:402-416`） | 新增 `current_machine_id()`：优先用心跳身份，否则读持久 `machine.json`（异常时退回旧判定并告警）；仲裁与 Phase -1 对账都改用它。`tests/conftest.py` 增加会话级 `state_home` 隔离，避免测试写开发机真实状态目录 |
| 低 | 准入读取认领评论失败暴露成裸 traceback（`cli_parsed_commands/runner.py:494`） | CLI 边界把该 `RuntimeError` 映射为 `CliError` + `ExitCode.CONFLICT`（退出码 5），core 仍不引用 ExitCode。回归测试 `test_cli_run_on_unreadable_claim_records_exits_conflict` |

## Validation status

- `SKIP=check-test-flag just lint --full`: **通过**（Ruff、ruff-format、架构分层、指南一致性、文件行数、守卫测试改动检查）。
- `just lint --reuse`: **通过**（重复代码与复用门禁）。
- 最终候选树上的相关九模块 pytest（`-o addopts=""`，不依赖 testmon）：**149 passed**。
- 完整单元套件：`uv run pytest -o addopts="" tests/` → **5043 passed, 1 skipped**；`just test`（testmon 增量档）通过。
- `uv run mkdocs build --strict`: **通过**（仅仓库既有 unnav-page 与 docstring formatter 提示）。
- rv-1 至 rv-4：全部在最终候选实现树 `6658504731e5dbc2cfc5b751d470123789c3464c` 上重跑，每项先观察到负控变红、再去掉注入后同一真实入口转绿；详见 `evidence.json` 与四份原始输出。
- PRD archive-readiness checker：**通过**。
- 文档同步：`docs/guides/agent-runner.md` 已更新租约时限口径、发布前归属确认与 machine_id 判定口径；`kc` CLI 子命令/旗标/退出码表面未新增，随包 `kedacode-operator` skill 无需改动。

## Runner-owned verdict gate

**独立 verifier 的最新结论（针对修复前的树）**：verifier 在 commit `0a8d9cf06c54f547c93cd2d40ed2b1260e4bd320`（candidate tree `8b8c0d22461ab066fef954d7b88b376a3f3884af`）上亲自重跑 rv-1 至 rv-4，判定 **GREEN** 并写入 `iar:verifier-verdict risk=green`，原始记录见 `verifier-response.txt`。

**本最终树的门禁状态：待重跑。** 本轮审查修复改变了实现树，上述 GREEN 不覆盖 `6658504731e5dbc2cfc5b751d470123789c3464c`；该结论只能由 runner-owned 独立 verifier 给出，本报告不代其签署。

**Review incident：** 人审 HTML 的浏览器 smoke 仍未执行。CUA inventory 没有浏览器，Edge 与 Chrome-for-Testing app 均被 UI 自动化权限拒绝；浏览器 pageerror、首卡可见性、分页与末页 Markdown 生成因此为 `REVIEW_INCIDENT / INCONCLUSIVE`，不是产品缺陷，也不视为已通过。runner 应在启用浏览器的环境重试并保留结果。

## 评审总结

🔴 严重: 0  ✅
🟠 高（代码）: 0  ✅（第一轮 1 项已修复并加回归测试）
🟡 中: 0  ✅（第一轮 2 项已修复）
🔵 低: 0  ✅（第一轮 5 项已修复或按披露处理）

需求状态: ✅ 与 PRD 范围一致
代码审查: ✅ 两轮审查发现全部闭环（当前未解发现: 0）
验证状态: ⚠️ 最终树的功能验证通过；独立 verifier 需在最终树重跑，浏览器 smoke 仍待人工环境
文档状态: ✅ 通过

结论: ⛔ 独立 verifier 在最终候选树上重跑、以及浏览器 smoke 完成前，不得写 verifier 结论或据此翻动验收状态；本结论不表示实现功能失败。

```json
{
  "verdict": "pending_final_tree_verification",
  "summary": "All eight review findings are fixed with regression tests, and rv-1 through rv-4 plus the focused and full unit suites pass on final candidate tree 6658504731e5dbc2cfc5b751d470123789c3464c. The independent verifier's GREEN covers the pre-fix tree 8b8c0d22461ab066fef954d7b88b376a3f3884af at commit 0a8d9cf0; the runner-owned verifier must rerun against the final tree, and the real-browser checklist smoke remains inconclusive.",
  "findings": [
    {
      "category": "validation",
      "severity": "medium",
      "file": "tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/verifier-response.txt",
      "line": 746,
      "title": "Independent verifier verdict predates the review-fix tree",
      "description": "The verifier reran the four real-entry scenarios at commit 0a8d9cf0 and recorded GREEN with risk=green, but the review fixes changed the implementation tree to 6658504731e5dbc2cfc5b751d470123789c3464c, so no independent verdict exists for the final candidate tree.",
      "recommendation": "Have the runner-owned independent verifier rerun against final candidate tree 6658504731e5dbc2cfc5b751d470123789c3464c and record its verdict before the PRD acceptance state is advanced."
    },
    {
      "category": "validation",
      "severity": "medium",
      "file": "tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/human-review-checklist.html",
      "line": 1,
      "title": "Real-browser checklist smoke still unavailable",
      "description": "CUA reports no browser sessions and denies access to the installed Edge and Chrome-for-Testing apps, so pageerror, first-card visibility, pagination and result-markdown generation cannot be observed. Static structure checks were not counted as browser verification.",
      "recommendation": "Retry the checklist in an enabled browser environment and record the smoke result; keep it disclosed as inconclusive until then."
    }
  ]
}
```
