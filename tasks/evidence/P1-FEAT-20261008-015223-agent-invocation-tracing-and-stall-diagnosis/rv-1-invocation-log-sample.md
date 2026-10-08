# rv-1 调用日志样例（既有 `kc logs --issue N` 入口原样输出）

打开方式：

```bash
open /Users/zata/code/keda/.iar-worktrees/issue-242/tasks/evidence/P1-FEAT-20261008-015223-agent-invocation-tracing-and-stall-diagnosis/rv-1-invocation-log-sample.md
```

下面的标记行是 `kc logs --repo-id rv-harness --issue <N> --lines 2000` 在**新进程**里
读出来的原文（只截去行首时间戳之外的内容，未做任何改写）。

## 场景 A — 首个执行器失败并回退（Issue #242）

```text
2026-10-08 14:48:39,299 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-start] invocation=inv-4b4918c7e291 run=rv-harness#issue-242#20261008T064834Z-1c8b6023ce85 issue=242 attempt=1 phase=implementation role=implementer executor=claude model_requested=claude-sonnet-4-5 retry_of=- retry_reason=- log=agent-runner/issues/rv-harness/issue-242-20261008-144834.log
2026-10-08 14:48:39,683 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-end] invocation=inv-4b4918c7e291 run=rv-harness#issue-242#20261008T064834Z-1c8b6023ce85 issue=242 attempt=1 phase=implementation role=implementer executor=claude outcome=error exit_code=1 duration_s=0.383 model_requested=claude-sonnet-4-5 model_reported=claude-sonnet-4-5-20250929 model_source=executor_report retry_of=- failure_category=nonzero_exit
2026-10-08 14:48:45,360 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-start] invocation=inv-a6c90caf800b run=rv-harness#issue-242#20261008T064834Z-1c8b6023ce85 issue=242 attempt=1 phase=implementation role=implementer executor=kimi model_requested=未下发 retry_of=inv-4b4918c7e291 retry_reason=executor_fallback log=agent-runner/issues/rv-harness/issue-242-20261008-144834.log
2026-10-08 14:48:45,728 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-end] invocation=inv-a6c90caf800b run=rv-harness#issue-242#20261008T064834Z-1c8b6023ce85 issue=242 attempt=1 phase=implementation role=implementer executor=kimi outcome=ok exit_code=0 duration_s=0.366 model_requested=未下发 model_reported=未提供 model_source=unknown retry_of=inv-4b4918c7e291 failure_category=-
2026-10-08 14:48:47,558 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-start] invocation=inv-6e9fffe93c4a run=rv-harness#issue-242#20261008T064834Z-1c8b6023ce85 issue=242 attempt=1 phase=review role=reviewer executor=kimi model_requested=未下发 retry_of=- retry_reason=- log=agent-runner/issues/rv-harness/issue-242-20261008-144834.log
2026-10-08 14:48:47,673 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-end] invocation=inv-6e9fffe93c4a run=rv-harness#issue-242#20261008T064834Z-1c8b6023ce85 issue=242 attempt=1 phase=review role=reviewer executor=kimi outcome=ok exit_code=0 duration_s=0.114 model_requested=未下发 model_reported=未提供 model_source=unknown retry_of=- failure_category=-
```

解读要点：

- 两次 `phase=implementation` 是**两条独立记录**，`invocation=` 各不相同；
- 第二条的 `retry_of=` 指向第一条，`retry_reason=executor_fallback`；
- `executor=` 记的是实际执行器：先是 `claude`，回退后是 `kimi`；
- `model_source=executor_report` 表示执行器自己报了模型；`unknown` 表示没报，
  此时 `model_reported=未提供`，**不会**用配置值冒充；
- `duration_s=` 是单调时钟算出的墙钟耗时，不采信执行器自报的时长。

## 场景 B — 交付门禁失败一次触发 Fix Agent（Issue #243）

```text
2026-10-08 14:48:59,215 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-start] invocation=inv-abc98dd457ad run=rv-harness#issue-243#20261008T064853Z-f2ca4553f706 issue=243 attempt=1 phase=implementation role=implementer executor=kimi model_requested=未下发 retry_of=- retry_reason=- log=agent-runner/issues/rv-harness/issue-243-20261008-144853.log
2026-10-08 14:48:59,585 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-end] invocation=inv-abc98dd457ad run=rv-harness#issue-243#20261008T064853Z-f2ca4553f706 issue=243 attempt=1 phase=implementation role=implementer executor=kimi outcome=ok exit_code=0 duration_s=0.368 model_requested=未下发 model_reported=未提供 model_source=unknown retry_of=- failure_category=-
2026-10-08 14:49:00,205 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-start] invocation=inv-614526effaef run=rv-harness#issue-243#20261008T064853Z-f2ca4553f706 issue=243 attempt=- phase=fix role=fixer executor=kimi model_requested=未下发 retry_of=- retry_reason=- log=agent-runner/issues/rv-harness/issue-243-20261008-144853.log
2026-10-08 14:49:00,323 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-end] invocation=inv-614526effaef run=rv-harness#issue-243#20261008T064853Z-f2ca4553f706 issue=243 attempt=- phase=fix role=fixer executor=kimi outcome=ok exit_code=0 duration_s=0.116 model_requested=未下发 model_reported=未提供 model_source=unknown retry_of=- failure_category=-
2026-10-08 14:49:01,608 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-start] invocation=inv-8ae03275dab5 run=rv-harness#issue-243#20261008T064853Z-f2ca4553f706 issue=243 attempt=1 phase=review role=reviewer executor=kimi model_requested=未下发 retry_of=- retry_reason=- log=agent-runner/issues/rv-harness/issue-243-20261008-144853.log
2026-10-08 14:49:01,723 INFO backend.core.use_cases.agent_invocation_tracing: [iar-invocation-end] invocation=inv-8ae03275dab5 run=rv-harness#issue-243#20261008T064853Z-f2ca4553f706 issue=243 attempt=1 phase=review role=reviewer executor=kimi outcome=ok exit_code=0 duration_s=0.113 model_requested=未下发 model_reported=未提供 model_source=unknown retry_of=- failure_category=-
```

解读要点：`phase=fix` / `role=fixer` 是独立的一条调用记录，不会被折叠进
`implementation` 那一条。

## 覆盖披露

- 本样例覆盖的阶段：`implementation`、`fix`、`review`（场景 A/B 合计）。
- `verification` 阶段（独立复验门禁）只在 Issue 正文带 Realistic Validation 块与
  `iar:structured-evidence` marker 时才会触发；harness Issue 不带这两者，因此该
  阶段由 `src/backend/core/use_cases/run_verifier_agent.py` 的接线与已提交测试覆盖，
  不在本次 CLI 场景内。
- GitHub 与 agent executable 是夹具；Git、SQLite、CLI 全真。
