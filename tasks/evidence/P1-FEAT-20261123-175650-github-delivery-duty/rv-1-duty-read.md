# RV-1 · Issue / PR 手动查询

真实入口已完成复查。新版与旧版均区分了旧失败与当前检查，PRD 规定的旧版错误仍缺实证。人工呈递待维护者验收。

## 交付分支实测

在实现提交 `9da79d9b` 上以 `kc skill install` 刷新岗位技能，在两个新 Codex 会话分别询问 Issue #430 和 PR #436 还需要谁处理。两次查询都查到了关联关系和 GitHub 当时的 PR head。查询前后，Issue 时间线为 30→30 条，PR 时间线为 8→8 条。调用轨迹中的写操作为 0。 [Issue #430](https://github.com/ZataZhang/keda/issues/430) · [PR #436](https://github.com/ZataZhang/keda/pull/436)

## 隔离 GitHub 实测

| 实际输入 | 可复核的答复和出处 | 复查动作 | 写入 |
|---|---|---|---|
| 从私有测试 Issue #17 问谁需要处理 | 旧提交 `fcf73a5` 的提交检查为失败。关联 Draft PR #18 的当前 head 为 `f7d49ee`，同名检查待完成。出处为两个提交的 GitHub commit status 与当前 PR。 | 等当前检查结束。若失败，由 PR 作者看最新日志。 | 0，Issue 和 PR 时间线一致 |
| 从同一份 Draft PR #18 问谁需要处理 | 反查到 Issue #17。PR 当前为 `f7d49ee`，检查待完成；失败来自旧提交 `fcf73a5`。 | 等当前检查，按本次结果复查。 | 0，Issue 2→2 条，PR 2→2 条，完整快照一致 |
| 从私有测试 PR #14 问谁需要处理 | 反查到 Issue #10；真实 runner 完成本地提交 `e77285ed` 后在推送阶段失败，GitHub PR head 仍为 `29bd17a`。出处为 Issue 的 attempt 与发布失败事件、runner 日志及 GitHub PR head。 | 维护者确认待交付的提交，由有权限的执行器复查发布后监督。 | 0，Issue 和 PR 时间线一致 |

对照组使用改动前的随包版本，缺少 `duty.md`。第一次旧版答复等待当前检查。第二次在 Issue 正文保留旧 SHA 的真实失败和当时的重试建议，再以相同问题从两个新会话查询。两版均等待 `f7d49ee` 的检查，前后快照相等，写调用各为 0。原验收预期旧版建议立即重试或发起写调用，本次观察结果仍不满足该预期。

同一份私有 PR 的首次补测前后快照不相等。复跑保留了两次原始快照，并验证内容完全一致。

本地证据包保留真实终端节选、会话轨迹、提交状态及查询前后的快照。呈递材料来自真实 CLI 会话，仍待维护者查看。 [可执行复跑清单](https://github.com/ZataZhang/keda/blob/iar-evidence/issue-430/evidence.json)

## 首轮执行故障

Issue #430 的首轮恢复尝试在启动 `codex` 后失败，错误为 `workspace routing discovery timed out`，CLI 退出码为 1。此前连续出现 5/5 次重连失败。该故障发生在 runner verification 启动之前，属于会话路由基础设施故障，不能证明 duty 路由本身失败或通过。

Issue：<https://github.com/ZataZhang/keda/issues/430>

本轮没有成功启动新 Codex 会话，没有经过“已安装技能 → 新路由 → `duty.md` → 真实 `gh` / `kc` 读取 → 会话答复”的链路。没有执行 PR 入口查询，也没有取得查询前后 Issue / PR 时间线、当前 PR head、检查 SHA 或会话写调用计数。之前上下文中的 GitHub 只读浏览不构成 PRD 要求的真实 Codex 查询证据。
