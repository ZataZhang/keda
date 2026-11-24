# RV-1 · Issue / PR 手动查询

**状态：`REVIEW_INCIDENT / INCONCLUSIVE`。** 本文件记录未完成的真实入口验证，不是行为通过证据。

## 观察到的执行故障

Issue #430 的最近恢复尝试在启动 `codex` 后失败，错误为 `workspace routing discovery timed out`，CLI 退出码为 1。此前连续出现 5/5 次重连失败。该故障发生在 runner verification 启动之前，属于会话路由基础设施故障，不能证明 duty 路由本身失败或通过。

Issue：<https://github.com/ZataZhang/keda/issues/430>

本轮没有成功启动新 Codex 会话，没有经过“已安装技能 → 新路由 → `duty.md` → 真实 `gh` / `kc` 读取 → 会话答复”的链路。没有执行 PR 入口查询，也没有取得查询前后 Issue / PR 时间线、当前 PR head、检查 SHA 或会话写调用计数。之前上下文中的 GitHub 只读浏览不构成 PRD 要求的真实 Codex 查询证据。

## 负控与缺失证据

PRD 负控要求在保留旧失败记录、同时新 head 有进行中检查的真实现场，使用未安装 `duty.md` 的技能版本查询。本轮未能启动真实会话，因此负控未执行；没有观测到“建议立即重试”或写调用，也没有证明这个 oracle 会红。

预期红灯（**未观测**）：无岗位参考的答复根据旧失败记录建议立即重试，或会话调用轨迹出现写操作。查询前后时间线不变、head SHA 一致、观察时点与交接字段齐全均未核实。

PRD 的主演示脚本需要真实 Issue、PR 和 Codex 会话。本轮没有录制 `demo-*.mp4`，没有用静态文本或 mock 代替录屏。

## 后续

在 workspace routing 恢复后的新 Codex 会话中安装 / 刷新随包技能，分别从真实 Issue 与其真实关联 PR 入口执行查询；记录 head / check SHA、时间线前后快照、写调用计数，并完成上述旧技能负控与主演示录屏。
