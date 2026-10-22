---
id: keda-blocker-monitor-30m
schedule: "30m"
repo_id: keda
managed_by: agent-daemon
issue_type: bug
agent: auto
queue_ready: true
output: issue
timezone: Asia/Shanghai
priority: P1
slug: keda-blocker-monitor
skip_when: needs_attention=0
pre_command: 'blocked="$(gh issue list --repo ZataZhang/keda --state open --label agent/blocked --json number --jq length 2>/dev/null)" || blocked=unknown; failed="$(gh issue list --repo ZataZhang/keda --state open --label agent/failed --json number --jq length 2>/dev/null)" || failed=unknown; case "$blocked:$failed" in *[!0-9:]*|:*|*:) printf "blocked_count=unknown\nfailed_count=unknown\nneeds_attention=unknown\n";; *) total=$((blocked + failed)); printf "blocked_count=%s\nfailed_count=%s\nneeds_attention=%s\n" "$blocked" "$failed" "$total";; esac'
---

# Keda 阻塞与失败巡查 — {{date}}

这是每 30 分钟触发一次的 Keda 任务恢复巡查。触发前检查到的数量：

- `agent/blocked`：{{blocked_count}}
- `agent/failed`：{{failed_count}}
- 合计：{{needs_attention}}

若 GitHub 状态读取失败，计数会显示 `unknown`；此时应先诊断访问故障，不得把它当作队列为空。

## 处理步骤

1. 重新读取当前仓库所有 open 的 `agent/blocked` 和 `agent/failed` Issues，避免按过期计数操作。逐条读取 Issue 正文、最近的 Attempt History、相关 PR 状态，并用 `kc logs --repo-id keda --issue <N>` 查看本次执行日志。
2. 对 `agent/failed`，先分类根因。发布或状态收尾失败使用 `kc recover --issue <N>`；执行失败先修复已确认的代码、配置或本机环境原因，再按恢复流程重新排队。不要在根因未知时盲目重试。
3. 对 `agent/blocked`，仓库维护者已授权自动处理范围明确、可验证的技术阻塞。修好根因后才运行 `kc blocked-continue --issue <N>`，并确认该 Issue 已恢复到正确状态。
4. 不得猜测产品取舍、访问权限、密钥、受保护路径或外部审批。遇到这类需要人决策的阻塞时，保留原 Issue 的 blocked 状态，在 Issue 评论中写明证据、缺少的决定和最小可选项，然后让本巡查 Issue 进入 blocked，等待维护者处理。
5. 不接管仍处于 `agent/running` 的 Issue，也不因日志暂时安静就杀进程或创建第二个执行进程。运行中的任务由 daemon 的 stall supervisor 和原执行流程负责恢复。
6. 处理后再次读取目标 Issue 状态及日志，确认恢复动作确实生效。若重新检查时已没有需要处理的 Issue，关闭本巡查 Issue 并说明本轮没有遗留阻塞；若本轮产生了修复 PR 或仍需人工决定，则保留本巡查 Issue 与其关联信息，不要谎报已解决。

## 巡查结果与 PR 描述

- 若本轮产生代码修复 PR，在本巡查 Issue 正文末尾追加 `## 本轮处置摘要（供 PR 描述生成）`，逐个列出目标 Issue、相关 PR、问题症状与根因、本轮修复、实际验证命令及结果、目标 Issue 当前状态和剩余人工决定。不要只把这些信息写在 Issue 评论里，因为 PR 描述生成读取 Issue 正文、提交记录和 diff，不读取评论。
- 修复提交标题或正文应包含目标 Issue 编号和具体改动，保证 PR 描述生成回退时，提交记录仍能说明改了什么。
- Draft PR 正文必须写明目标 Issue、症状/根因、修复内容、验证结果和未完成事项。保留 `Closes #<本巡查 Issue>` 以关闭本轮巡查并允许下一轮调度；目标 Issue 仍有未完成验收或待人决策时，只用 `Addresses #<目标 Issue>` / `Related to #<目标 Issue>`，不要用会自动关闭目标 Issue 的关键词。

## 约束

- 只处理 Keda 仓库的 `agent/blocked` 与 `agent/failed` Issues，不扩大到普通待办或人工审核中的 Issue。
- 代码修复遵守 `docs/ai-standards/`、使用最高可行的真实入口验证，并按普通 runner 流程提交 Draft PR。
- 只有完成根因修复与验证后，才能恢复原 Issue；验证失败时保留阻塞并记录失败证据。
