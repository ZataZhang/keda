# 独立验证与代码审查报告 · Issue #314

## Current candidate follow-up · 2026-10-27

- 当前候选实现树：`bfaad53e278da7a66af2ffad1d0732654f569fc4`。四项真实入口 RV 在这棵树上全部通过，四项负控均按预期转红。
- 独立性 review 发现仓库的 verifier preset 可与 builder 同为 codex；`_choose_verifier_agent` 已改为跳过同一 agent、选回退链里的不同 agent，并在没有候选时 fail closed。回归测试覆盖两种情况。
- 当前验证：相关九模块 **149 passed**；`just test` 实际输出 `no tests ran`，不作为测试通过证据；`SKIP=check-test-flag just lint --full`、reuse lint 与严格 MkDocs 构建通过。
- Microsoft Edge smoke 已完成：首卡、三卡翻页和结果 Markdown 通过；0 个 page error，3 条 textarea 可访问性警告。
- 旧 Qoder GREEN 绑定到此前候选树，不能覆盖本轮文件行数修复。Qoder 首次检查刷新后的候选树时发现 manifest 与报告仍指向旧哈希；这些记录现已统一绑定到当前树。Qoder 对 builder HEAD `e58f70b3c4213d1598f0f667b08e0cce061d81be` 的只读独立复核返回 **PASS**，candidate implementation tree 为 `bfaad53e278da7a66af2ffad1d0732654f569fc4`。它提出的三个非阻塞证据呈递问题已在 `deb4a48586f5dea9cbf057c395ebc86034283d5a` 修复，且没有改变该候选实现树。执行者之后核对最终 PR head `fb4d3329e65d36ac22c3d04a5f1544f2ea426ddb` 与 squash merge `ec5ad44e37676c65165de7a56039b0caf622d6e2` 的完整 tree 均为 `f2ae48f91ad861868b9299e3be1a8461b51e6f22`，过滤候选树仍为 `bfaad53e278da7a66af2ffad1d0732654f569fc4`。

## 评审基准

- 需求来源：`tasks/archive/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease.md` 与 GitHub Issue #314。
- 要实现：GitHub 上按仓库共享机器在线状态；`kc peers` 表格/JSON；含 machine_id 的独立租约；远端过期对账；续租失败后自停；push/PR 前认领确认；兼容无租约旧认领。
- 明确不做：新协调服务、数据库迁移、标签状态机重构、租约有效时强制接手、续传另一台机器的本地 WIP、Console 机器页面。
- 真实交付边界：CLI 与 daemon、GitHub Issue/comment adapter、claim arbitration/reconcile/admission/publication、machine identity/config、guide 与 packaged operator skill。
- 最终候选实现树：`bfaad53e278da7a66af2ffad1d0732654f569fc4`（不含本 PRD 与 evidence delivery records），RV source HEAD tree（commit `40652d14` 的完整 tree）`ab6d0208a74baf73ed0b11f8dc3edb1ad98ab376`。

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
- 完整单元套件：先前 tree `6658504731e5dbc2cfc5b751d470123789c3464c` 上 `uv run pytest -o addopts="" tests/` → **5043 passed, 1 skipped**；默认租约改为 1200 秒后的当前树未重跑全量套件。当前树 `just test` 受 testmon 标记影响，输出 `no tests ran`，未计入测试通过证据。
- `uv run mkdocs build --strict`: **通过**（仅仓库既有 unnav-page 与 docstring formatter 提示）。
- rv-1 至 rv-4：全部在最终候选实现树 `bfaad53e278da7a66af2ffad1d0732654f569fc4` 上重跑，每项先观察到负控变红、再去掉注入后同一真实入口转绿；详见 `evidence.json` 与四份原始输出。
- PRD archive-readiness checker：**通过**。
- 文档同步：`docs/guides/agent-runner.md` 已更新租约时限口径、发布前归属确认与 machine_id 判定口径；`kc` CLI 子命令/旗标/退出码表面未新增，随包 `kedacode-operator` skill 无需改动。

## Runner-owned verdict gate

**历史 verdict（不满足独立性）**：commit `0a8d9cf06c54f547c93cd2d40ed2b1260e4bd320`（tree `8b8c0d22461ab066fef954d7b88b376a3f3884af`）上的 GREEN 使用 codex verifier，而 builder 同为 codex；它也早于当前候选树。该结果不得作为本 PR 的独立 verifier PASS。

**当前候选树门禁：PASS。** Qoder 以只读独立 verifier 身份审查 builder HEAD `e58f70b3c4213d1598f0f667b08e0cce061d81be`，重新计算 candidate implementation tree `bfaad53e278da7a66af2ffad1d0732654f569fc4`，并独立重跑九个相关测试模块（149 passed），返回 **PASS**。RV-1 至 RV-4、manifest 和此候选树一致，RV source HEAD tree 为 `ab6d0208a74baf73ed0b11f8dc3edb1ad98ab376`（commit `40652d14` 的完整 tree）。Qoder 另指出的三个非阻塞记录问题已由执行者修复并核对：HTML 清单已发布到 evidence branch，rv-3 PID 与原始输出相同，历史 Change Log 用语不再暗示旧 verdict 覆盖当前树。修复仅变更交付记录；当时的 PR follow-up head `deb4a48586f5dea9cbf057c395ebc86034283d5a` 是中间 docs-only 版本；最终 PR head `fb4d3329e65d36ac22c3d04a5f1544f2ea426ddb` 以及 squash merge `ec5ad44e37676c65165de7a56039b0caf622d6e2` 的完整树均为 `f2ae48f91ad861868b9299e3be1a8461b51e6f22`，过滤候选树仍可复算为 `bfaad53e278da7a66af2ffad1d0732654f569fc4`。完整单元测试与真实 GitHub 首次写入等披露边界见下文。

**Verifier 披露的非阻塞覆盖边界**：

1. 所有写入路径仍由无凭据 fake `gh` 验证；真实仓库尚未创建状态 Issue，首次真实写入仍需运行环境观察。Presence 写失败会作为 warning 处理，不阻止任务。
2. FR-6 的预算耗尽→`agent/failed` 和待返工→保持 `agent/running` 两个分支为单元测试覆盖；四项真实入口 RV 只走重新入队分支。
3. 若内部调用直接绕过受支持的 CLI/daemon 入口（它们会先启动协调心跳），或调用与停机并发，`prepare_claim_comment` 可能返回 `None`，现有仲裁会发布无 `kc:lease` 的 claim；按兼容规则它不会被远端回收。Verifier 将其评为窄范围的非阻塞退化窗口。该风险保留供后续改进，当前 RV 不宣称覆盖此路径。
4. 人审清单浏览器 smoke 已通过，但 9.1 中对人读呈递内容的最终接受，仍按 PR 内显式的 merge-acceptance 契约在合并时记录。

**人审 smoke：已完成**：Microsoft Edge 中首卡显示、三卡翻页与结果 Markdown 生成均通过；0 个 page error。仍有 3 条 textarea 缺少 id/name 的可访问性警告，记为低优先级 UI 改进项，不阻挡当前功能验收。

## 评审总结

🔴 严重: 0  ✅
🟠 高（代码）: 0  ✅（第一轮 1 项已修复并加回归测试）
🟡 中: 0  ✅（第一轮 2 项已修复）
🔵 低: 0  ✅（第一轮 5 项已修复或按披露处理）

需求状态: ✅ 与 PRD 范围一致
代码审查: ✅ 两轮审查发现全部闭环（当前未解发现: 0）
验证状态: ✅ 当前候选树 RV 与 149 个相关测试通过；Qoder 独立 verifier 在同一 candidate implementation tree 上返回 PASS。全量单测沿用旧默认值树结果，未在 1200 秒默认值后重跑。浏览器 smoke 已通过并披露 3 条可访问性警告。
文档状态: ✅ 通过

结论: ✅ candidate implementation tree `bfaad53e278da7a66af2ffad1d0732654f569fc4` 已通过四项 RV、149 个相关测试与 Qoder 独立 verifier。真实 GitHub 写入、两项 FR-6 处置的 RV 覆盖和 9.1 人工接受边界如上披露；PRD 人工验收状态在 squash merge 前保持待验收。

```json
{
  "verdict": "pass",
  "summary": "Qoder independently reviewed builder/RV source HEAD e58f70b3c4213d1598f0f667b08e0cce061d81be and recomputed candidate implementation tree bfaad53e278da7a66af2ffad1d0732654f569fc4. RV-1 through RV-4 passed with negative controls; nine relevant pytest modules passed (149 passed); all 25 manifest assertions matched their raw outputs. At the time of that review, intermediate follow-up PR head deb4a48586f5dea9cbf057c395ebc86034283d5a kept the same filtered tree, and its three delivery-record findings were corrected. This original review does not claim to cover the final PR head."
  "findings": []
}
```
