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

- `kc logs` without `--issue` keeps its old meaning: it tails the log of a *managed process* (`--kind daemon` or `--kind review_daemon`), not a specific Issue. `--issue` and `--kind` are mutually exclusive.
- `/ps` reads whatever the background command writes to stdout. KedaCode keeps Issue number, attempt switches, and key actions recognizable on stdout, but `/ps` truncates to recent lines — complete history lives in `kc logs --issue` or the web console.
- An exited Issue keeps its last log file; "no output yet" means the Issue has not started or its log was cleaned up, and the CLI says so explicitly instead of printing nothing.

## Never do

Every command on this page reads; none of them starts work. A 「看一眼」request is never an authorization to launch a run — if the user asks to (re)start execution after seeing the output, that is a new explicit request on the 跑一次 route.

## Recovery

An empty log view is information, not a failure to fix: the CLI prints why (not started yet, or cleaned up). If the Issue should have been running, check claim state via the 卡住了 route (`${CODEBUDDY_SKILL_DIR}/references/triage.md`).

## Related

The daemon's own process logs are covered above; starting or stopping daemons lives on the 后台跑 route (`${CODEBUDDY_SKILL_DIR}/references/daemon.md`).
