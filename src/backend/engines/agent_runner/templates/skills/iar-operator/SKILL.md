---
name: iar-operator
description: Use IAR to initialize a repository, enqueue PRDs, inspect Issues and the ready queue, run work once, manage IAR daemons, or view live agent output per Issue.
---

# IAR Operator

Use this skill when the user asks to operate the IAR CLI or its managed runner. First identify whether they want setup, enqueueing, read-only inspection, one execution pass, persistent background processing, or viewing agent output. Do not turn a request to inspect, preview, or "just check progress" into execution.

## Choose the operation

| User intent | Command or guide | Effect |
|---|---|---|
| Initialize IAR in the current repository | `iar init --dry-run`, then `iar init` | Preview is read-only. Init writes repository config, installs Skills, registers the repository, and syncs GitHub labels. |
| Put a PRD in the queue | `iar issue create --help`; see "PRD 与 Issue 操作" in `docs/guides/agent-runner.md` | Creates or publishes a GitHub Issue and can make it ready for the runner. Confirm the intended PRD and readiness before writing. |
| Open an Issue straight from a plain-language request (no PRD) | `iar issue create --from-prompt "<一句话需求>"` | Writes the generated body **without** a `PRD path:` anchor and **without** an acceptance section, so the Issue is never mistaken for a PRD-backed deliverable; zero workspace diff. Add `--require-validation` only when the user wants validation gates in the body. It enters the queue only with `--ready`; by default a human runs it with `iar run --issue <N>`. Mutually exclusive with the PRD-path argument (neither or both is a usage error). |
| Inspect Issues | `iar issue list --help` | Read-only GitHub query; use state, label, and PR filters as needed. |
| Inspect model presets (agent + model + reasoning effort) | `iar agent presets` | Read-only config listing; shows every defined `[agent_runner.presets.<name>]` entry. |
| Preview a stage's or preset's exact command line | `iar agent doctor <agent> --preset <name> --json`; `iar agent doctor --lifecycle <key>` | Read-only argv resolution. `--lifecycle` accepts the nine lifecycle keys; fix/closeout without an implementer context report `follows_implementation` instead of argv. |
| Preview the next execution pass | `iar run --all-ready --dry-run --max-issues 3` (or `iar run --issue <N> --dry-run` for one Issue) | Read-only preview. Shows the selected ready Issues and their priority; does not run an Agent or claim work. |
| Execute one pass | `iar run --issue <N> --max-issues 1` for one targeted Issue; `iar run --all-ready --max-issues 1` for the whole ready queue | Runs configured Agents and may update GitHub, create branches/worktrees, and open or update PRs. **A target is required**: bare `iar run` is a usage error; autopilot flags do not exist on `run`. |
| Publish a Draft PR without the validation gates | `iar run --issue <N> --fast-merge` (single target only) | One-shot fast track: after the builder commits, skip rv re-exec + independent verifier and open the Draft PR annotated as unverified (`iar:fast-merge` marker + human notice). Rejected with `--all-ready` or on a `stack`-dependency Issue (usage error). Not a daemon flag; does not arm auto-merge. |
| Publish a Draft PR with no PRD behind it | `iar run --issue <N> --direct-pr` (single target only) | Direct tier for an Issue **without a `PRD path:` anchor**: after the builder commits, skip the rv re-exec + independent verifier *and* the review Agent + runner-side verification commands, then open the Draft PR (`iar:direct-pr` marker + human notice; quality gates move to CI on the PR). Rejected with `--all-ready`, with `--fast-merge` (usage error), and on a PRD-backed Issue or an unreadable body (fail-closed). Not a daemon flag; does not arm auto-merge. |
| Drive several Issues at once | One `iar run --issue <N>` invocation per Issue, each with its own target | Invocations overlap safely: a one-shot `run` does not take the repository daemon lock, and every Issue works in its own worktree with its own claim lock. Never combine this with `--all-ready`, which claims the same ready queue twice. |
| Let work run unattended | `iar daemon run --repo-id <id> --concurrency N` | Long-lived process that keeps polling the ready queue; `--concurrency` controls how many Issues one daemon advances in parallel (default `max_concurrent_issues=1`, sequential). It owns the repository daemon lock, so a *queue-polling* `iar run --all-ready` refuses while it is alive; an explicitly targeted `iar run --issue <N>` still runs. The daemon command group has no `stop` subcommand — stop it with `iar registry stop --repo-id <id>`. |
| Observe CI/CD delivery for backlog PRDs | `iar backlog ci status --json --repo-id <repo-id>`; `iar backlog ci status --prd <prd-path> --repo-id <repo-id>` | Read-only. Shows the raw GitHub checks state, repair rounds, stored/global/effective auto-repair policy and problem cards; `--json` prints the same DTO as the Console API on stdout. |
| Set the CI auto-repair policy | `iar backlog ci policy --global on --repo-id <repo-id>`; `iar backlog ci policy --prd <prd-path> inherit --repo-id <repo-id>` | `--global` writes the repository-level `post_pr_supervisor.auto_repair_ci` in `.iar.toml`; `--prd` writes the latest `iar:ci-auto-repair-policy` marker on the PRD's Issue (`inherit` clears the override). Targets are mutually exclusive. |
| Request one manual CI repair | `iar backlog ci repair --prd <prd-path> --dry-run --repo-id <repo-id>` | Explicitly requests a single repair through the existing manual-repair path (idempotent per head SHA, still gated by max attempts, worktree and forbidden-path checks). `--dry-run` reports what would happen with zero side effects. |
| Inspect or manage persistent runner processes | `iar registry start|stop`, `iar registry list`, `iar daemon status`, `iar logs`; see "启动与停止托管 daemon" in `docs/guides/agent-runner.md` | `registry start` launches persistent runner and review-daemon processes by default; `daemon status` and `logs` inspect them, `registry stop` terminates managed processes. |
| Ask what a command actually accepts | `iar schema --json` | Read-only introspection derived from the live command tree: every command, flag, type, required flag, enum choices, default, and an example. |

Use the repository guide as the authoritative command reference. Start with `iar --help` or the relevant subcommand `--help` when command details differ by IAR version. Do not claim `--dry-run` executes work.

## Consuming `iar` from a script or another Agent

Whenever the reader of the output is a program rather than a human, pass `--json` explicitly. `--json` is an alias of `--output json`; with neither flag the output stays the human table, **including when stdout is a pipe**, so never assume a machine format.

- In JSON mode stdout carries data only. Progress, warnings, and errors go to stderr, so `command > out.json` cannot be polluted by log lines.
- A failure in JSON mode writes one envelope to stderr: `{"error", "message", "suggestion", "retryable", "exit_code"}`. `suggestion` is a runnable command, and the only safe way to continue is `$?` — do not parse English.
- `iar ask` and `iar deliberate` reuse `--output` for an output **directory**, so `--output json` there means a directory literally named `json`. Neither takes `--json`; read their human summary or the written session files instead.
- Exit codes published by `iar --help` and by `iar schema --json` → `exit_codes.values`. The Name column is exactly the envelope's `error` value, so `$?` and stderr never disagree:

| Code | Name | Meaning | What the caller does |
|---|---|---|---|
| `0` | `ok` | Requested work completed (read-only output counts as success). | Continue. |
| `1` | `error` | Failure with no published category. | Read stderr, then retry or escalate. |
| `2` | `usage_error` | Flags or arguments do not form a valid command: bare `iar run`, mutually exclusive targets (e.g. `--from-prompt` with or without a PRD path), or a single-target-only flag used with `--all-ready`. | Fix the invocation; retrying unchanged fails again. |
| `3` | `not_found` | Target missing: repository, registry entry, agent, executable, or log. Also an explicitly targeted Issue that cannot be read or is closed. | Switch target, or run the `suggestion`. |
| `4` | `permission_denied` | Not authorized: GitHub auth or a disabled repository. | Ask the human to authenticate or enable it; do not retry silently. |
| `5` | `conflict` | Current state blocks the request: `--all-ready` while a daemon polls the same queue, an explicitly targeted Issue that is live-claimed by another process or is `agent/blocked` with no unblock request, workflow template file already installed, loop entry already exists. | Rename, pass `--force` when the user asked for it, stop the competing process, or run the `suggestion` (for a live claim it names the holder host and PID; for a blocked Issue it is `iar blocked-continue --issue <N>`). |
| `10` | `dry_run_ok` | The plan is valid and nothing was written. Usable as a CI gate. | Treat as green only after checking the plan body. |

## Viewing agent live output

Four distinct paths, driven by the user's intent. Never start `iar run` or `iar registry start` just because the user asked to *view* progress — a read-only request gets a read-only command.

| Intent | Path | Boundaries |
|---|---|---|
| Start a task and watch a quick summary via Codex `/ps` | Ask Codex to start `iar run --issue <N> --repo <path> --max-issues 1` (or `iar logs --repo <path> --issue <N> --follow`) in a background terminal of the *current* session, then type `/ps` | `/ps` only lists background terminals started by this same Codex session, and shows at most the last few non-empty output lines. Use it for a quick glance, not full history. |
| Follow one Issue's full output from a second terminal | `iar logs --repo <path> --issue <N> --follow` (omit `--follow` for a one-shot tail) | Works for tasks started anywhere (this session, another terminal, the daemon). First output is a tail window; `--follow` polls by offset, announces and switches to the new attempt on retry, and prints an explicit empty state when the Issue has not started or its log was cleaned. |
| Just view an existing task's progress (no new execution) | `iar logs --repo <path> --issue <N>`; use `iar issue list --repo <path>` to find the Issue number first if unknown | Read-only: never starts or restarts work. A task launched outside this Codex session never appears in `/ps` — use this command instead. |
| View output in the web console | Open the Backlog page, enter the PRD detail for the PRD linked to the Issue, and switch to the "实时输出" tab | Same log source as the CLI; polls every few seconds with pause/resume. The tab exists only for PRDs with a linked Issue. |

Notes that keep expectations accurate:

- `iar logs` without `--issue` keeps its old meaning: it tails the log of a *managed process* (`--kind daemon` or `--kind review_daemon`), not a specific Issue. `--issue` and `--kind` are mutually exclusive.
- `/ps` reads whatever the background command writes to stdout. Keda keeps Issue number, attempt switches, and key actions recognizable on stdout, but `/ps` truncates to recent lines — complete history lives in `iar logs --issue` or the web console.
- An exited Issue keeps its last log file; "no output yet" means the Issue has not started or its log was cleaned up, and the CLI says so explicitly instead of printing nothing.

## Triage a running or failed Issue

The viewing paths above tell you what an Agent is doing; the Issue's labels tell you what the runner will do next. Read the label before acting, and read the live `Attempt History` comment before retrying anything.

| Label | Meaning | Next step |
|---|---|---|
| `agent/ready` | Queued and eligible for the daemon's next autonomous pick. Priority order is defined under Safety and compatibility. **It is not a precondition for execution**: a human-named `iar run --issue <N>` target needs no label at all. | Nothing, or `iar run --all-ready --dry-run` to preview the selection. |
| `agent/waiting` | A declared dependency is unmet. | Resolve the upstream Issue; do not re-queue. |
| `agent/running` | Claimed. The claim comment records host, PID, and the selected agent; the first claim is settled by a claim-marker election, so exactly one process wins and the loser withdraws its own comment. | Use the viewing paths above. An explicit `iar run --issue <N>` against a live claim fails with a conflict naming the holder instead of double-running. |
| `agent/supervising` / `agent/review` | A PR exists and the supervisor is working or has asked for human review. | A Draft PR is **not** the end of the pipeline; read the supervisor's comment on the PR. |
| `agent/failed` | The pass ended without a publishable result. | Read the `Attempt History` comment, fix the cause, then re-queue. |
| `agent/blocked` | Needs a human decision (for example forbidden paths). | Resolve the cause, then `iar blocked-continue --issue <N>`. |
| `validation/verifier-passed` | The independent verifier signed off; a new head commit clears it again. | Nothing until the PR is reviewed. |

### When a run makes no visible progress

An Agent that prints nothing for the configured inactivity timeout is killed and retried up to `max_recovery_attempts`. Exhausting those retries is **not** automatically a failed Issue: the runner may switch to the next Agent in `agent_fallback_order` and restart the same Issue. A `transient` attempt reporting no output before the kill usually points at the Agent or its environment, not at the task.

1. Read the `Attempt History` comment: per-attempt Agent, failure type, and duration.
2. Check the latest claim comment for the Agent actually in use; a different name means fallback already happened.
3. Only after retries *and* fallback are exhausted does the Issue become `agent/failed`.

### Recovering

- **Failed publish** — the work is fine but the push, PR, or state update was not: `iar recover --issue <N>` resumes it. Do not re-queue and redo the work.
- **Back to the queue** — after fixing the cause, `gh issue edit <N> --add-label agent/ready --remove-label agent/failed` lets the next pass pick it up.
- **Where the code is** — `iar worktree path --branch issue-<N>` prints the worktree root. A re-run reuses that worktree, so uncommitted work in it is usually still wanted.
- **Ambiguous target** — when the working directory matches more than one registered repository, several commands refuse to guess; pass `--repo-id <id>` (or `--repo`).

## Safety and compatibility

- Explain GitHub writes, Agent execution, and persistent background processes before carrying them out. A queue preview is read-only.
- **`iar run` requires a target** (breaking change): pass `--issue <N>`, a PRD path (resolved via the PRD's `- GitHub Issue:` link — a PRD without one must first go through `iar issue create`), or `--all-ready` for the historical queue-draining behavior. Bare `iar run` fails with a usage error.
- **An explicit target does not need `agent/ready`**: readiness gates the daemon's *autonomous pick*, not a human-named run. Any open Issue can be run with `iar run --issue <N>` — including one made by hand with no PRD anchor and no labels. What an explicit target cannot bypass is claim state, and the answer is loud instead of a silent skip: `agent/running` with a live holder → conflict `5` naming the holder host and PID; `agent/blocked` with no unconsumed unblock request → conflict `5` with `iar blocked-continue --issue <N>` as the suggestion; unreadable or closed → `3 not_found`. A dead local holder (PID gone) is not a conflict — the run resumes the in-flight work. An empty ready queue stays the normal, silent, exit-0 case.
- **The daemon mutex covers queue polling only**: `iar run --all-ready` refuses with a conflict while a live daemon serves the same repository, because two pollers would double-claim the same ready queue; an explicitly targeted `iar run --issue <N>` coexists with a live daemon. Stopping the daemon is a separate, explicit step (`iar registry stop --repo-id <id>`; the daemon command group exposes only `run` and `status`, no stop), or pass `--takeover` (add `--yes` in scripts) to stop the daemon gracefully, reclaim its in-flight Issues, and take over — destructive, because it interrupts *all* of the daemon's in-flight Issues. With no daemon alive, several `iar run --issue <N>` invocations for different Issues overlap freely, because each Issue gets its own worktree (`.iar-worktrees/issue-<N>`) and its own claim lock. Two concurrent `--all-ready` invocations remain unsafe.
- **First claim is settled by election, not by luck**: GitHub offers no compare-and-swap on labels, so each candidate posts its own `iar:claim` marker, waits a short grace window, reads the comment thread back, and the *earliest* bid (`started_at`, then host, then PID — a total order, so every observer picks the same winner) transitions the Issue to `agent/running`. Everyone else rewrites its own comment to `iar:claim-withdrawn` and skips the Issue **without touching labels**, so losing a race is silent rather than a failed Issue. Only same-round bids count: markers without a `started_at` field, and bids outside the concurrent window, are historical claims and are excluded so rework can never be locked out. A same-host bid whose PID is dead is dropped; a foreign-host bid cannot be probed and is treated as alive (fail-closed).
- **`iar run --fast-merge` (fast track, one-shot)**: after the builder commits, skip the validation gates (rv re-exec + independent verifier) and publish the Draft PR immediately, annotated as unverified. The PR body carries a machine-readable `<!-- iar:fast-merge issued=<N> -->` marker plus a human notice to verify manually before merging. It is defined for a single targeted Issue only: it does not combine with `--all-ready` (usage error), and it is rejected for Issues that declare a `stack` dependency (an unverified upstream would poison every fork on the chain). The daemon has no `--fast-merge` and there is no config key for it — the bypass is this run only.
- **`iar run --issue <N> --direct-pr` (direct tier, one-shot)**: for an Issue with **no `PRD path:` anchor**, publish the Draft PR after the build stage while skipping the review Agent and the runner-side verification commands on top of the fast-merge bypass; the PR body carries `<!-- iar:direct-pr issued=<N> -->` and a notice that CI on the PR now carries the quality gate. It is rejected on a PRD-backed target or when the Issue body cannot be read (fail-closing keeps an unarchived, unchecked PRD from being bypassed), does not combine with `--all-ready`, and is mutually exclusive with `--fast-merge` (usage error, not "whichever is stronger"). The daemon has no `--direct-pr`.
- **`iar issue create --from-prompt "<需求>"` (Issue without a PRD)**: takes either PRD path arguments or `--from-prompt`, never both and never neither (usage error). The generated body deliberately carries **no** `PRD path:` anchor and **no** acceptance section, so the runner treats it as a plain Issue; `--require-validation` is the opt-in that appends the Realistic Validation section. It writes only GitHub state — no local file is created and the working tree stays clean — and falls back to the deterministic template when AI content generation is unavailable or fails. It enters the ready queue only with `--ready`; by default a human runs it explicitly.
- **Autopilot belongs to the daemon, not `run`**: `iar daemon run --autopilot` / `iar daemon run --no-autopilot` overrides the *scheduling* autopilot for that daemon run (flag > repo `.iar.toml` > global, locked for the process). It never arms auto-merge — merging stays behind the `safety.auto_merge` + `autopilot.enabled` config switches. To promote a pending PRD manually, use `iar backlog advance`. `run` has no `--autopilot` and no `--concurrency`: Issue-level parallelism is a daemon-only knob inside one process (default `max_concurrent_issues=1`, sequential), and across processes it comes from issuing one `iar run` per Issue.
- A user-owned `iar-operator` Skill with different content is preserved by default. `iar init --dry-run` reports the conflict. Only pass `--force` when the user explicitly requests replacing that file.
- The PRD runner accepts the current Machine Contract v3. Older and unknown versions fail preflight before GitHub state writes. Updating the installed `prd` Skill is a separate action; do not recommend blind `iar init --force`, because it can replace user-owned Skills.
- Ready Issue priority comes from `priority/P0` through `priority/P3` labels. Selection order is P0, P1, P2, P3, then Issues without one of those labels; ties use ascending Issue number. Dry-run and execution share this ordering.
- The runner orders the candidate window returned by GitHub (currently at most 100 ready Issues per pass); do not describe it as a global sort across Issues outside that window.

## Lifecycle reference

For repository setup, Issue/PRD commands, one-shot runs, viewing and triaging agent output, registry-managed daemons, logs, and shutdown steps, read [`docs/guides/agent-runner.md`](docs/guides/agent-runner.md). In particular, use its daemon lifecycle section before starting persistent processes.
