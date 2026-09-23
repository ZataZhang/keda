---
name: iar-operator
description: Use IAR to initialize a repository, enqueue PRDs, inspect Issues and the ready queue, run work once, or manage IAR daemons.
---

# IAR Operator

Use this skill when the user asks to operate the IAR CLI or its managed runner. First identify whether they want setup, enqueueing, read-only inspection, one execution pass, or persistent background processing. Do not turn a request to inspect or preview into execution.

## Choose the operation

| User intent | Command or guide | Effect |
|---|---|---|
| Initialize IAR in the current repository | `iar init --dry-run`, then `iar init` | Preview is read-only. Init writes repository config, installs Skills, registers the repository, and syncs GitHub labels. |
| Put a PRD in the queue | `iar issue create --help`; see “PRD 与 Issue 操作” in `docs/guides/agent-runner.md` | Creates or publishes a GitHub Issue and can make it ready for the runner. Confirm the intended PRD and readiness before writing. |
| Inspect Issues | `iar issue list --help` | Read-only GitHub query; use state, label, and PR filters as needed. |
| Preview the next execution pass | `iar run --dry-run --max-issues 3` | Read-only preview. Shows the selected ready Issues and their priority; does not run an Agent or claim work. |
| Execute one pass | `iar run --max-issues 1` | Runs configured Agents and may update GitHub, create branches/worktrees, and open or update PRs. |
| Inspect or manage persistent runner processes | `iar registry start|stop`, `iar registry list`, `iar daemon status`, `iar logs`; see “启动与停止托管 daemon” in `docs/guides/agent-runner.md` | `registry start` launches persistent runner and review-daemon processes by default; `daemon status` and `logs` inspect them, `registry stop` terminates managed processes. |

Use the repository guide as the authoritative command reference. Start with `iar --help` or the relevant subcommand `--help` when command details differ by IAR version. Do not claim `--dry-run` executes work.

## Safety and compatibility

- Explain GitHub writes, Agent execution, and persistent background processes before carrying them out. A queue preview is read-only.
- A user-owned `iar-operator` Skill with different content is preserved by default. `iar init --dry-run` reports the conflict. Only pass `--force` when the user explicitly requests replacing that file.
- The PRD runner accepts the current Machine Contract v3. Older and unknown versions fail preflight before GitHub state writes. Updating the installed `prd` Skill is a separate action; do not recommend blind `iar init --force`, because it can replace user-owned Skills.
- Ready Issue priority comes from `priority/P0` through `priority/P3` labels. Selection order is P0, P1, P2, P3, then Issues without one of those labels; ties use ascending Issue number. Dry-run and execution share this ordering.
- The runner orders the candidate window returned by GitHub (currently at most 100 ready Issues per pass); do not describe it as a global sort across Issues outside that window.

## Lifecycle reference

For repository setup, Issue/PRD commands, one-shot runs, registry-managed daemons, logs, and shutdown steps, read [`docs/guides/agent-runner.md`](docs/guides/agent-runner.md). In particular, use its daemon lifecycle section before starting persistent processes.
