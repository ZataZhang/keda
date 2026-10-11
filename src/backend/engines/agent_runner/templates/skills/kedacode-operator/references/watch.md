# 看进度 — view progress and live agent output (read-only)

## When to use

The user says 「看看 197 现在什么情况」「到哪了」「看下实时输出」「tail 一下日志」, or types `/ps`, or translates the CLI directly: `kc logs`. Deciding what the runner will do next from labels is 卡住了.

## Four distinct paths, driven by the user's intent

| Intent | Path | Boundaries |
|---|---|---|
| Start a task and watch a quick summary via Codex `/ps` | Ask Codex to start `kc run --issue <N> --repo <path> --max-issues 1` (or `kc logs --repo <path> --issue <N> --follow`) in a background terminal of the *current* session, then type `/ps` | `/ps` only lists background terminals started by this same Codex session, and shows at most the last few non-empty output lines. Use it for a quick glance, not full history. |
| Follow one Issue's full output from a second terminal | `kc logs --repo <path> --issue <N> --follow` (omit `--follow` for a one-shot tail) | Works for tasks started anywhere (this session, another terminal, the daemon). First output is a tail window; `--follow` polls by offset, announces and switches to the new attempt on retry, and prints an explicit empty state when the Issue has not started or its log was cleaned. |
| Just view an existing task's progress (no new execution) | `kc logs --repo <path> --issue <N>`; use `kc issue list --repo <path>` to find the Issue number first if unknown | Read-only: never starts or restarts work. A task launched outside this Codex session never appears in `/ps` — use this command instead. |
| View output in the web console | Open the Backlog page, enter the PRD detail for the PRD linked to the Issue, and switch to the "实时输出" tab | Same log source as the CLI; polls every few seconds with pause/resume. The tab exists only for PRDs with a linked Issue. |

Notes that keep expectations accurate:

- `kc logs` without `--issue` keeps its old meaning: it tails the log of a *managed process* (`--kind daemon` or `--kind supervise_daemon`), not a specific Issue. `--issue` and `--kind` are mutually exclusive.
- `/ps` reads whatever the background command writes to stdout. KedaCode keeps Issue number, attempt switches, and key actions recognizable on stdout, but `/ps` truncates to recent lines — complete history lives in `kc logs --issue` or the web console.
- An exited Issue keeps its last log file; "no output yet" means the Issue has not started or its log was cleaned up, and the CLI says so explicitly instead of printing nothing.

## Reading the invocation markers in an Issue log

The same `kc logs --issue <N>` stream carries three structured markers. There is **no new command, flag, JSON summary, or page** for them — grep the log you are already reading.

| Marker | Means | Read it as |
|---|---|---|
| `[iar-invocation-start]` | A real top-level Agent subprocess call is about to start | This call exists; a matching end marker with the same `invocation=` should follow, or the gap needs explaining |
| `[iar-invocation-end]` | That call returned, raised, or was killed on timeout | Its outcome, wall-clock duration, and model facts |
| `[iar-invocation-coverage-incomplete]` | Writing the observation ledger degraded (at most once per run) | This run's call list **may be incomplete**. Business results are unaffected, but do not quote the list as complete evidence |

Start and end share one identity prefix, so attribution in concurrent runs comes from fields, never from adjacent lines:

```text
[iar-invocation-start] invocation=inv-3f9a1c7b2d40 run=keda-main#issue-242#20261008T015223Z-a91f4c0b7e33 issue=242 attempt=1 phase=implementation role=implementer executor=claude model_requested=claude-sonnet-4-5 retry_of=- retry_reason=- log=agent-runner/issues/keda-main/issue-242-20261008-015223.log
[iar-invocation-end] invocation=inv-3f9a1c7b2d40 run=keda-main#issue-242#20261008T015223Z-a91f4c0b7e33 issue=242 attempt=1 phase=implementation role=implementer executor=claude outcome=ok exit_code=0 duration_s=1843.207 model_requested=claude-sonnet-4-5 model_reported=claude-sonnet-4-5-20250929 model_source=executor_report retry_of=- failure_category=-
```

Fields worth knowing:

- `invocation=` — unique per real subprocess call; pair start/end on it.
- `run=` — `<repo_id>#issue-<N>#<UTC timestamp>-<random 12 hex>`. **Does not depend on a PRD**, so PRD-less Issues are covered too.
- `phase=` / `role=` — closed set (`implementation`, `fix`, `review`, `review_repair`, `verification`, `verification_recovery`, `rebase_recovery`, `closeout`, `supervisor`, `supervisor_repair`, `content_generation`, `unspecified`).
- `executor=` — the agent that **actually ran** this call. After a fallback it is the fallback target, not what the config originally wanted.
- `outcome=` — `ok` / `failed` / `timeout` / `error`; `failure_category=` is set only when not `ok` (`nonzero_exit`, `timeout`, `agent_unavailable`, `os_error`, `runtime_error`, `unknown`).
- `duration_s=` — wall clock from a monotonic clock. The executor's own self-reported duration is deliberately not trusted.

Model is recorded as **three separate facts**, and any missing link stays missing — it is never back-filled from the current config:

- `model_requested=` — what was passed down; `未下发` when nothing was.
- `model_reported=` — what the executor self-reported from its own output stream; `未提供` when it did not.
- `model_source=` — only `executor_report` or `unknown`.

So `model_source=unknown` legitimately happens when the executor does not self-report, when a model binding was dropped on executor fallback, or for history recorded before this feature existed.

Retries and fallbacks are **always separate records**, never merged: `retry_of=` points at the previous `invocation=` and `retry_reason=` says why — `transient_failure` (in-place retry), `executor_fallback` (switched agent), `resume_not_started` (asked for session resume but the argv could not express it, so it restarted fresh). "Took three retries to succeed" is therefore four records in a chain.

A start with no matching end is a **gap, not a verdict**. Do not invent a terminal state for it:

- Read it as *unclosed* by default — the end event simply did not land. The process may still be running, the daemon may have been `kill -9`ed, or the ledger write may have degraded.
- Read it as *interrupted* only after you have separately confirmed the process exited (the `pid=` in the claim comment is gone).

What is **not** covered, so an empty read is not proof nothing ran: sub-Agents spawned *inside* an Agent (the ledger's `internal_agent_coverage` is always `unobserved`), cross-machine aggregation (the ledger is the local `~/.kedacode/console.db`), and the deliberation / `kc ask` planner / `kc agent doctor` / REPL / idea-draft / PRD-rework paths, which have no bound Issue run context and stay no-ops.

These markers explain what happened; they do not authorize acting on it. Deciding whether a run is stuck and what to do about it is the 卡住了 route (`${CODEBUDDY_SKILL_DIR}/references/triage.md`). There is no stall threshold, no stall-judgement program, and no auto-takeover behind these markers.

## Never do

Every command on this page reads; none of them starts work. A 「看一眼」request is never an authorization to launch a run — if the user asks to (re)start execution after seeing the output, that is a new explicit request on the 跑一次 route.

## Recovery

An empty log view is information, not a failure to fix: the CLI prints why (not started yet, or cleaned up). If the Issue should have been running, check claim state via the 卡住了 route (`${CODEBUDDY_SKILL_DIR}/references/triage.md`).

## Related

The daemon's own process logs are covered above; starting or stopping daemons lives on the 后台跑 route (`${CODEBUDDY_SKILL_DIR}/references/daemon.md`).
