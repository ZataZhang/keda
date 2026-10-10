# 卡住了 — triage a running, stuck, or failed Issue

## When to use

The user says 「怎么不动了」「卡住了」「失败了怎么办」「为什么没跑」, asks about a label like `agent/blocked`, or wants to retry a failed Issue. Reading live output alone is 看进度; starting work is 跑一次.

## Confirm before acting

The viewing paths tell you what an Agent is doing; the Issue's labels tell you what the runner will do next. Read the label before acting, and read the live `Attempt History` comment before retrying anything.

| Label | Meaning | Next step |
|---|---|---|
| `agent/ready` | Queued and eligible for the daemon's next autonomous pick. Priority order is defined under Safety and compatibility. **It is not a precondition for execution**: a human-named `kc run --issue <N>` target needs no label at all. | Nothing, or `kc run --all-ready --dry-run` to preview the selection. |
| `agent/waiting` | A declared dependency is unmet. | Resolve the upstream Issue; do not re-queue. |
| `agent/running` | Claimed. The claim comment records host, PID, and the selected agent; the first claim is settled by a claim-marker election, so exactly one process wins and the loser withdraws its own comment. | Use the viewing paths on the 看进度 route. An explicit `kc run --issue <N>` against a live claim fails with a conflict naming the holder instead of double-running. |
| `agent/supervising` / `agent/review` | A PR exists and the supervisor is working or has asked for human review. | A Draft PR is **not** the end of the pipeline; read the supervisor's comment on the PR. |
| `agent/failed` | The pass ended without a publishable result. | Read the `Attempt History` comment, fix the cause, then re-queue. |
| `agent/blocked` | Needs a human decision (for example forbidden paths). | Resolve the cause, then `kc blocked-continue --issue <N>`. |
| `validation/verifier-passed` | The independent verifier signed off; a new head commit clears it again. | Nothing until the PR is reviewed. |

### When a run makes no visible progress

An Agent that prints nothing for the configured inactivity timeout is killed and retried up to `max_recovery_attempts`. Exhausting those retries is **not** automatically a failed Issue: the runner may switch to the next Agent in `agent_fallback_order` and restart the same Issue. A `transient` attempt reporting no output before the kill usually points at the Agent or its environment, not at the task.

1. Read the `Attempt History` comment: per-attempt Agent, failure type, and duration.
2. Check the latest claim comment for the Agent actually in use; a different name means fallback already happened.
3. Read the `[iar-invocation-start]` / `[iar-invocation-end]` markers in `kc logs --issue <N>`: how many real subprocess calls happened, which `executor=` ran each, what `outcome=` / `failure_category=` each ended with, and how they chain via `retry_of=`. Field-by-field reading is on the 看进度 route (`${CODEBUDDY_SKILL_DIR}/references/watch.md`).
4. Only after retries *and* fallback are exhausted does the Issue become `agent/failed`.

What the markers do **not** tell you, so do not over-read them:

- **Log activity is not delivery.** Calls can start, end `ok`, and still never reach a PR — that is exactly the failure mode this tracing was added for. Confirm delivery against the PR / label state, not against how busy the log looks.
- **A start with no end is a gap, not a verdict.** Read it as *unclosed* by default; call it *interrupted* only after separately confirming the process exited (the `pid=` in the claim comment is gone). Never invent a terminal state for it.
- **`model_source=unknown` is honest, not broken.** It means the executor did not self-report a model, a binding was dropped on fallback, or the record predates this feature. It is never back-filled from the current config.
- **Coverage is one process layer on one machine.** Sub-Agents spawned inside an Agent are `unobserved`, and the ledger is the local `~/.kedacode/console.db` — no cross-machine aggregation.
- **`[iar-invocation-coverage-incomplete]` means the list may be incomplete**, not that the run failed. Business results are unaffected by observation faults.

These markers alone stay diagnosable: they carry no threshold and take no action. The optional, **off-by-default** stall supervisor is a separate component — see the next subsection.

### When a stalled run *should* have been cancelled and was not

`[agent_runner.stall_supervisor]` adds a per-attempt observer that asks one read-only Agent whether a frozen attempt is thinking or stuck, and cancels the process group only when the answer is actionable. It writes `[iar-stall-check]`, `[iar-stall-diagnosis]`, `[iar-stall-handoff]`, and `[iar-stall-cancel]` lines into the same per-Issue log, so `kc logs --issue <N>` shows why it acted — or why it did not:

- **Supervision is off** (`enabled = false`, the default): no observer thread, no model call, behavior is byte-for-byte what it was before the feature. Absence of `[iar-stall-*]` lines usually means this.
- **The call was not supervisable**: only runs with an Issue identity and a run-phase profile get an observer. `kc ask`, deliberation, and read-only diagnosis calls never do.
- **The supervisor has no read-only `generate` profile**: it self-disables and records one audit line rather than diagnosing with a write-capable Agent.
- **The verdict was not `stalled`**: `blocked` and `uncertain` hand off (`[iar-stall-handoff]`) and touch nothing. A `stalled` answer missing its summary or evidence is downgraded to `uncertain` — a mis-spelled word does not earn the right to kill a process.
- **The recheck before cancelling failed**: the attempt had already finished, the scene fingerprint changed while the model was thinking, or `probe_live_attempt` could not confirm that this attempt owns those PIDs. Any of these hands off instead.
- **A frozen fingerprint is not a frozen stdout**: progress is HEAD, branch, status/diff fingerprints, finished-call count, and last event time — deliberately not log volume, because a loop that prints the same error looks busy and is not progressing.

Cancelling is not a new recovery path: the cancelled attempt flows into the existing failure classification, retry ladder, and gates. Turn it on only when the user accepts that KedaCode may terminate a live writing process; both intervals must be positive or config loading refuses before any spawn.

## Recover

- **Failed publish** — the work is fine but the push, PR, or state update was not: `kc recover --issue <N>` resumes it. Do not re-queue and redo the work.
- **Back to the queue** — after fixing the cause, `gh issue edit <N> --add-label agent/ready --remove-label agent/failed` lets the next pass pick it up.
- **Where the code is** — `kc worktree path --branch issue-<N>` prints the worktree root. A re-run reuses that worktree, so uncommitted work in it is usually still wanted.
- **Ambiguous target** — when the working directory matches more than one registered repository, several commands refuse to guess; pass `--repo-id <id>` (or `--repo`).

Label edits (`gh issue edit`) and `kc blocked-continue` change what the runner may execute next — confirm the decision with the user before applying them, and never use them to talk a read-only question into a re-run.

## Related

Conflict codes from a live claim are produced by runs; their invocation details are on the 跑一次 route (`${CODEBUDDY_SKILL_DIR}/references/run-once.md`).
