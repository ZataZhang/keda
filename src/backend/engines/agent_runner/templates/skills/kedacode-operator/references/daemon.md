# 后台跑 — run and manage daemons (persistent background processing)

## When to use

The user says 「挂后台」「让它自己跑」「有个 daemon 在跑吗」「停掉后台」, or translates the CLI directly: `kc daemon run` / `kc registry list`. A single pass on named Issues is 跑一次, not this route.

## Confirm before acting

1. **Starting a daemon is a persistent side effect** — it keeps polling the ready queue and running Agents unattended; confirm the user asked for background processing, not one pass.
2. **Check whether one already serves this repository** — `kc daemon status` and `kc registry list` answer this read-only; a second queue poller would double-claim the ready queue (the mutex rule is in Safety and compatibility on the main file).
3. **The repository target is unambiguous** — when the working directory matches several registered repositories the commands refuse to guess; pass `--repo-id <id>`.
4. **Concurrency is the user's call** — default `max_concurrent_issues=1` (sequential); raising `--concurrency` makes one daemon advance several Issues in parallel. Each pass, the daemon resolves one ceiling `min(Backlog「并发」policy, capacity)` (never-saved policy inherits the capacity) and applies it to both the backfill gate (which only runs while auto-advance is on) and the ready-claim gate (claims = max(0, ceiling − live `agent/running` count; a failed count fails closed to zero). Running recovery, blocked resolution, and `direct_pr_cleanup` retain their existing quota and bypass only this new-claim ceiling. If the same label-filtered query blocks running-recovery discovery, a bounded scan of open Issues retries that existing-work lane. Explicit `kc run` paths stay exempt. The console settings/API and 「全局开始」 resolve capacity from repository config; they cannot see this daemon process's explicit `--concurrency` override. If set, the daemon's runtime ceiling can be lower than the console value—check its `Concurrency ceiling` log line.

## Execute and never do

- Let work run unattended: `kc daemon run --repo-id <id> --concurrency N` — long-lived process that keeps polling the ready queue. The daemon command group has no `stop` subcommand — stop it with `kc registry stop --repo-id <id>`.
- Advance one Issue on the direct track without any daemon flag: the daemon never takes `--direct-pr`, but an Issue carrying the configurable `direct-pr` label is resolved to the direct tier by **an upgraded claim winner**, from a fresh read after the claim and per Issue — siblings in the same pass keep their own selected gates. Upgrade/restart all claimant runner processes first; old runners may ignore this label. The chosen tier stays fixed for the execution round, including fallback/recovery. The label chooses a tier only: it does not make an Issue claimable, change priority, or bypass the PRD-anchor refusal.
- DIRECT bypasses only the publication chain's inline post-PR supervisor. A separately running supervise-daemon may still review the published Issue under the existing workflow labels; marking DIRECT does not disable that background process.
- Inspect or manage persistent runner processes: `kc registry start|stop`, `kc registry list`, `kc daemon status`, `kc logs`; see "启动与停止托管 daemon" in `docs/guides/agent-runner.md`. `registry start` launches persistent runner and supervise-daemon processes by default; `daemon status` and `logs` inspect them, `registry stop` terminates managed processes.
- Never start a daemon just to *view* progress — that is what the 看进度 route is for.

## Recovery

- A daemon that should stop: `kc registry stop --repo-id <id>`.
- Work must be taken over from a live daemon immediately: run `kc run --all-ready --takeover` (add `--yes` in scripts) — destructive: it interrupts **all** of the daemon's in-flight Issues, so require the user's explicit consent first.
- A labelled Issue keeps its `direct-pr` label after a failed execution or a failed PR creation — that is intentional: the unfinished round retains its DIRECT choice for retry. If the log says the PR was published but the label cleanup is pending, do **not** rerun the work: the next recovery confirms the unfinished publication-round association plus the same repository, Issue, branch, head and `iar:direct-pr` marker, then only completes label/workflow handoff—even after a crash where the label was already removed. An old same-head PR alone cannot consume a new choice. Wait for consumption and handoff before re-adding the label; a concurrent re-add in that window is unsupported.

## Related

For publication-tier decision/validation duties, read `${CODEBUDDY_SKILL_DIR}/references/run-once.md`; Issue-level failures and retry semantics for work a daemon abandoned are on the 卡住了 route (`${CODEBUDDY_SKILL_DIR}/references/triage.md`).
