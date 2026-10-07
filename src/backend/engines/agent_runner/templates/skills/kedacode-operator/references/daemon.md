# 后台跑 — run and manage daemons (persistent background processing)

## When to use

The user says 「挂后台」「让它自己跑」「有个 daemon 在跑吗」「停掉后台」, or translates the CLI directly: `kc daemon run` / `kc registry list`. A single pass on named Issues is 跑一次, not this route.

## Confirm before acting

1. **Starting a daemon is a persistent side effect** — it keeps polling the ready queue and running Agents unattended; confirm the user asked for background processing, not one pass.
2. **Check whether one already serves this repository** — `kc daemon status` and `kc registry list` answer this read-only; a second queue poller would double-claim the ready queue (the mutex rule is in Safety and compatibility on the main file).
3. **The repository target is unambiguous** — when the working directory matches several registered repositories the commands refuse to guess; pass `--repo-id <id>`.
4. **Concurrency is the user's call** — default `max_concurrent_issues=1` (sequential); raising `--concurrency` makes one daemon advance several Issues in parallel.

## Execute and never do

- Let work run unattended: `kc daemon run --repo-id <id> --concurrency N` — long-lived process that keeps polling the ready queue. The daemon command group has no `stop` subcommand — stop it with `kc registry stop --repo-id <id>`.
- Inspect or manage persistent runner processes: `kc registry start|stop`, `kc registry list`, `kc daemon status`, `kc logs`; see "启动与停止托管 daemon" in `docs/guides/agent-runner.md`. `registry start` launches persistent runner and review-daemon processes by default; `daemon status` and `logs` inspect them, `registry stop` terminates managed processes.
- Never start a daemon just to *view* progress — that is what the 看进度 route is for.

## Recovery

- A daemon that should stop: `kc registry stop --repo-id <id>`.
- Work must be taken over from a live daemon immediately: run `kc run --all-ready --takeover` (add `--yes` in scripts) — destructive: it interrupts **all** of the daemon's in-flight Issues, so require the user's explicit consent first.

## Related

Issue-level failures and retry semantics for work a daemon abandoned are on the 卡住了 route (`${CODEBUDDY_SKILL_DIR}/references/triage.md`).
