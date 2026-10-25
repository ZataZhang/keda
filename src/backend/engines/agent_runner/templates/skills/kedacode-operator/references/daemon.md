# 后台跑 — run and manage daemons (persistent background processing)

## When to use

The user says 「挂后台」「让它自己跑」「有个 daemon 在跑吗」「停掉后台」, or translates the CLI directly: `kc daemon run` / `kc registry list`. A single pass on named Issues is 跑一次, not this route.

## Confirm before acting

1. **Starting a daemon is a persistent side effect** — it keeps polling the ready queue and running Agents unattended; confirm the user asked for background processing, not one pass.
2. **Check whether one already serves this repository** — `kc daemon status` and `kc registry list` answer this read-only; a second queue poller would double-claim the ready queue (the mutex rule is in Safety and compatibility on the main file).
3. **The repository target is unambiguous** — when the working directory matches several registered repositories the commands refuse to guess; pass `--repo-id <id>`.
4. **Concurrency is the user's call** — each repository has one parallel limit: `--concurrency` (this daemon process, every target) > the Backlog page setting > `[agent_runner.runner].max_concurrent_issues` (default `1`, sequential); `kc daemon run --all` resolves it per repository. The page setting overrides the config file (no `min`). The daemon does not wait for a pass to finish: Issues run in background threads and every polling interval reaps finished ones and claims up to the free slots (claims = max(0, limit − max(locally in-flight, live `agent/running` count)); a failed count fails closed to zero), so a long Issue no longer blocks newly ready ones. Locally in-flight Issues are skipped by the ready, running-recovery, blocked-resolution and `direct_pr_cleanup` lanes and by crash reconciliation. Reconcile, deliberation, PRD rework and Backlog advance keep running every interval while Issues execute. Explicit `kc run` paths stay exempt and still wait for their Issues before exiting. `kc daemon` rejects the retired `--max-issues` (exit `2`).

   **Reading the value actually in use** (answer "how many run at once?" from this, not from the config file):
   - `kc daemon status --repo-id <id> --json` → each `daemon` row carries `parallel_limit`, `parallel_limit_source` (`cli_override` = the daemon's `--concurrency`, `policy` = Backlog page setting, `config` = `max_concurrent_issues`) and `in_flight`. The daemon refreshes these every polling interval in `~/.kedacode/daemon-locks/<repo_id>.parallel-limit.json` and deletes the file on exit.
   - `null` there means no live daemon on this machine, or a daemon started before this release (restart it to get the value). Then the next daemon start will use: the Backlog page setting if saved, otherwise `max_concurrent_issues` — read both with `GET /api/v1/agent-runner/backlog/autopilot?repo_id=<id>` (`effective_max_parallel`, `ceiling_source`, `max_parallel`, `runner_capacity`) when the console runs.
   - The daemon log prints `Parallel limit for '<repo>': limit=N source=<…> in_flight=M` every interval.
   - The console Backlog control shows the daemon's live value when one runs, e.g. 「并行上限 5（daemon 命令行 --concurrency；页面设置 4 未生效）」.

   **Changing it — pick the layer deliberately and tell the user which one you changed:**
   - One daemon process, temporary: restart it with `kc daemon run --repo-id <id> --concurrency N`. It beats the page setting and config for that process only and is invisible to other machines.
   - This machine, persistent: the Backlog page setting (1–10). Stored only in this machine's console database (`~/.kedacode/console.db`); it does **not** edit `.kedacode.toml` and other machines do not see it. 「恢复为配置文件值」 deletes it.
   - Every machine: edit `[agent_runner.runner].max_concurrent_issues` in the repository's `.kedacode.toml` and commit it. A page setting saved on any machine still overrides it on that machine.
   - Never claim a page change "updated the config", and never edit `.kedacode.toml` when the user asked to change the page setting.

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
