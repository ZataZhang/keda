## 人审导航 / Human Review Navigation

| 看什么 | 呈递物 | 10 秒自检 |
|---|---|---|
| 在一台机器上看到两台机器及各自在做的 Issue（rv-1） | `tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/rv-1-kc-peers.txt`；打开：`open "$(git rev-parse --show-toplevel)/tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/rv-1-kc-peers.txt"`。这是终端原始输出；本项没有截图或其他静态图片。 | 表格里 B 那一行的"正在处理"列是 `#42`；JSON 中 `machines` 长度为 2 |

执行者已用两个独立状态目录、两个真实 daemon 与真实 `kc peers` 交叉核对这份输出。对话中可打开工作树里的文本文件；也可运行 `just prd review tasks/archive/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease.md` 定位交互清单。PR #330 已由执行者接管；当前 candidate tree `bfaad53e278da7a66af2ffad1d0732654f569fc4` 的四项 RV 与九模块测试已通过（149 passed）；manifest 与报告刚完成哈希对齐，Qoder 最终独立复核待完成。GitHub CI 以最终 PR head 的结果为准。

## 验证身份

- Current executor candidate implementation tree：`bfaad53e278da7a66af2ffad1d0732654f569fc4`
- Base HEAD tree：`ab6d0208a74baf73ed0b11f8dc3edb1ad98ab376`
- 四项 RV 各自在最终实现树上重跑；证据命令、输出摘要、expected fail、解释和风险见同目录 `evidence.json`。
- 原始终端文件按 RV item 分开保存；RV harness、假 GitHub/agent 与 tree helper 都位于 `scripts/`，不进入代码 diff，脚本和输出不含凭据。

## 风险优先证据

### 决策一 · rv-2（R3）

负控令假 GitHub 对已过期认领返回新鲜更新时间，真实 daemon 因而不接手；默认 1200 秒租约的 green 场景分别观察到 TTL 前 30 秒（1170s）仍运行和 TTL 后 30 秒（1230s）回到队列。旧版无租约认领在模拟 24 小时后仍保持 running，基线 `kc:claim` 解析器读到 host、pid、started_at 与 agent。

- 原始证据：`rv-2-remote-lease.txt`
- Green 要点：`remote_lease_expired_comments=1`；`issue_42_labels=agent/ready`；`lease_marker=absent`（#43）
- Negative control：返回新鲜 `updated_at` 后，#42 仍为 running、过期评论为 0，oracle 按预期失败。

### 决策一 · rv-3（R3）

负控让没有竞争认领的真实 `kc run` 推送了本地裸仓库分支并尝试创建 PR，保护 oracle 按预期失败。Green 场景覆盖 push 前与 PR 前的两次归属确认；续租失败到 120 秒 TTL 的半程后，进程登记簿确认并终止了真实 agent 子进程。

- 原始证据：`rv-3-self-fence.txt`
- Green 要点：子进程 `alive_after_exit=false`；失租后远端 ref `absent`；PR 创建 0 次；失租后的标签写入 0 次（初始领取的两次写入除外）
- Negative control：关闭竞争认领更新后，远端 ref 从 absent 变为 commit，且实际尝试 `gh pr create`。

### 决策二 · rv-4（R2）

负控拒绝删除重复状态 Issue 的 presence 标签，两个真实 daemon 留下两条 open 状态板，唯一板 oracle 按预期失败。Green retry 和并发首次启动都收敛为一个 open 状态板。关闭状态上报后 `kc run` 不更新状态评论但仍续租；同主机名的不同 machine ID 保持为不同机器，远端有效租约不被回收。

- 原始证据：`rv-4-presence.txt`
- Green 要点：`open_presence_issues=1`；`GREEN PRESENCE SWITCH`；`GREEN MACHINE-ID LOCALITY`
- Negative control：`open_presence_issues=2`，要求唯一板的断言按预期失败。

### 双机可见 · rv-1（R1）

负控隐藏 B 的状态评论，真实 `kc peers --json` 仅返回一台机器，长度为 2 的断言按预期失败。去除故障后真实表格与 JSON 均显示两台在线机器，B 正在执行 #42、agent 为 codex，状态 Issue 有两条评论。

- 原始证据：`rv-1-kc-peers.txt`
- Green 要点：表格有 #42/codex；JSON 的 `machines` 长度为 2；`status_issue=#100 presence_comments=2`
- Negative control：隐藏 B 后 `machines=1`，oracle 按预期失败。

## 先前复核树的其他交付验证（tree 6658504731e5dbc2cfc5b751d470123789c3464c）

- 相关测试：`uv run pytest -o addopts="" tests/test_agent_runner_claim_lease.py tests/test_coordination_heartbeat.py tests/test_machine_presence.py tests/test_agent_runner_reconcile.py tests/test_run_target_admission.py tests/test_agent_runner_claim_arbitration.py tests/test_agent_runner_reclaim.py tests/test_github_client.py tests/test_kc_peers.py` — **149 passed**。
- 配置映射回归：设置 TTL 为 900 秒的认领评论包含 `ttl="900"`。
- `SKIP=check-test-flag just lint --full` — **passed**；`just lint --reuse` — **passed**；`uv run mkdocs build --strict` — **passed**。
- `just test` 在当前状态输出 `no tests ran`，因此不计入通过证据。完整单元套件 `uv run pytest -o addopts="" tests/` 在先前 tree `6658504731e5dbc2cfc5b751d470123789c3464c` 上为 **5043 passed, 1 skipped**；默认租约改为 1200 秒后的当前树未重跑全量套件。当前树九个相关模块不带 testmon 过滤为 `149 passed`。
- PRD archive-readiness checker 与最终独立 verifier 将在 PRD 归档位置和交付记录更新后复跑；上一轮 Qoder GREEN 只覆盖旧候选树，当前不作为 PASS。

## 人审清单浏览器检查

清单文件：`tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/human-review-checklist.md`；交互伴侣：`tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/human-review-checklist.html`。Markdown 与 HTML 的三项标题和选择项已静态核对；模板固定脚本未改。静态 HTMLParser 检查得到 4 张卡、3 组单选、3 个文本框和结果卡。

实际浏览器 smoke 已于 2026-10-27 在 Microsoft Edge 完成：首卡正常显示，三张卡均可翻页，结果页可生成 Markdown；DevTools 记录 0 个 page error。仍有 3 条可访问性警告，来自三个备注 textarea 缺少 id/name；这不影响当前交互路径，但后续可为输入框补充可访问名称。


## 先前复核轮次（默认租约 600 秒）

代码审查修复后的四项 RV 曾在 candidate tree `6658504731e5dbc2cfc5b751d470123789c3464c` 上重跑通过；九模块 pytest 为 149 passed，整套测试为 5043 passed、1 skipped。该轮验证使用 600 秒默认租约，结果不代表随后改成 1200 秒后的当前树。

更早的 commit `0a8d9cf0` 上虽有 **GREEN**，但其 verifier agent 为 codex，与 builder 相同，且候选树为 `8b8c0d22461ab066fef954d7b88b376a3f3884af`；该结论既非独立复核，也不覆盖当前树。当前候选树为 `bfaad53e278da7a66af2ffad1d0732654f569fc4`；刷新后的报告和 manifest 已统一使用此标识，最终独立 verifier 将在提交后的 HEAD 上运行并同时确认过滤后的候选树。人审清单已在 Microsoft Edge 完成 smoke，结果与警告见上文。

## 默认租约 1200 秒后的当前验证

四项 RV 在 candidate tree `bfaad53e278da7a66af2ffad1d0732654f569fc4` 上重新运行，负控与绿色路径均符合预期；rv-2 直接验证默认 TTL 为 1200 秒，1170 秒不接手，1230 秒后回收。四项输出和 manifest 已与该树哈希同步。

- 九个相关模块无 testmon 过滤：**149 passed**。生命周期/verifier 两模块的既有计数已移除，因为其命名文件不在当前分支。
- `just test`：输出 `no tests ran`，不作为测试通过证据；`SKIP=check-test-flag just lint --full` 与 `just lint --reuse` 通过。
- `just lint --reuse`：passed；`uv run mkdocs build --strict`：passed。
- 前一候选树 `735690ebd1fccd8215261885511ae90f166752eb` 的 `just lint --repo` 为 **5138 passed, 1 skipped**；这项历史结果不冒充当前树结果。
- Microsoft Edge 人审清单 smoke：首卡显示、三卡翻页、结果 Markdown 生成均通过；0 个 page error，3 条 textarea 可访问性警告。
- `kc peers` 的人读输出已在 rv-1 表格与 JSON 中检查；Qoder 对旧候选树的 GREEN 不覆盖本轮候选树，刷新后独立复核待完成。9.1 内容是否符合预期仍由显式 merge-acceptance 契约在 PR 合并时确认。
