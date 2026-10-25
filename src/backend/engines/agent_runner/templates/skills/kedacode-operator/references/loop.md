# 定时任务 — register and run recurring loops (scheduled Issue generators)

## When to use

The user says 「每天凌晨一点检查…」「定时跑一个任务」「周期性」「夜间巡检」「loop」, or translates the CLI directly: `kc loop create` / `kc loop list`. A one-off pass on named Issues is 跑一次, and keeping the queue poller alive is 后台跑 — neither is this route.

## Confirm before acting

1. **A loop is a persistent schedule with a bill attached** — every fire creates an Issue that an agent then works on. Confirm the schedule, the target repository, and which daemon advances it before registering.
2. **The CLI registers a recipe; it does not write one.** Author `tasks/loops/<id>.md` first (YAML frontmatter + a body that is the task's full instructions). The frontmatter `id` must equal the `<id>` you pass to `kc loop create`, otherwise it is a usage error.
3. **Pick who advances it (`managed_by`)**: `agent-daemon` is advanced by the regular `kc daemon` before its queue phases, so a stopped daemon creates no new Issue and a freshly created Issue is claimed in the same pass (recommended for loops whose Issues the runner executes); the default `loop-daemon` is advanced by the separate loop-daemon process and keeps creating Issues even when no runner daemon is up.
4. **Pick what a fire produces (`output`)**: the default `issue` creates one plain Issue whose body is the full recipe text — no PRD file, nothing pushed, nothing to clean up (right for work that repeats, e.g. a nightly CI check); `prd` renders a timestamped PRD per fire bound one-to-one to its Issue and runs the PRD lifecycle (archive, per-PRD evidence), and with `publish_prd: true` it commits and pushes that PRD to the base branch — set it explicitly, and only when that is wanted. A loop registered before `output` existed keeps running as `prd` until its recipe is re-registered. `kc loop create` prints the `output=` it registered.
5. **Cron runs in UTC unless the recipe sets `timezone`** (an IANA name such as `Asia/Shanghai`). State timestamps stay UTC; `kc loop list` shows local time.

## Recipe frontmatter (the fields that matter)

Required: `id`, `schedule` (5-field cron, or `10m` / `1h` / `1d`), `repo_id`. Optional: `issue_type` (`feature` / `refactor` / `bug`), `agent`, `labels` (a list; `loop/<id>` is always added), `queue_ready` (default true: label `agent/ready`), `timezone`, `priority`, `slug`, `managed_by`, `output`, and two scheduling helpers: `pre_command` (a shell command whose `KEY=value` stdout lines become `{{KEY}}` template variables; `{{date}}` and `{{loop_id}}` are built in) and `skip_when: KEY=v1,v2` (skip the fire, with no Issue and no agent cost, when that variable matches — e.g. skip a CI check when the last run is green). A missing variable never skips.

Writing `pre_command`: keep it on one line (the frontmatter parser has no `|` blocks). In a double-quoted value write an inner `"` as `\"` (the YAML escapes `\\`, `\n`, `\t` are honored too); in a single-quoted value write `'` as `''`. `kc loop run <id> --dry-run` runs the `pre_command` and renders the template, so a broken command shows up there before registration: the error reads `undefined variable '<name>' ... The pre_command failed first: pre_command exited with N; stderr: …` — fix the command, not the frontmatter.

## Execute and never do

- Preview with no side effects: `kc loop run <id> --dry-run` — renders the title and labels and reports what would happen; it does not call GitHub, write a file or touch the schedule.
- Register: `kc loop create <id> --recipe tasks/loops/<id>.md`; override the schedule with `--cron "0 1 * * *"` or `--every 1d`, the target with `--repo-id <id>` / `--repo <path>`, and replace an existing entry with `--force`. The schedule, `skip_when`, `output` and `managed_by` are **copied into the state at registration**, so after editing a recipe re-register it (`--force`) or the old values keep running.
- Inspect: `kc loop list` (add `--json` when a program reads it). A loop that is not creating Issues shows the reason on the line under it (「最近未造票」).
- Fire immediately: `kc loop run <id> --now` — this really creates an Issue (and, with `output: prd`, a PRD file), so ask first; preview with `--dry-run` before the first real fire.
- Remove: `kc loop cancel <id>` deletes only the local schedule entry; it closes no Issue and cancels no running work.
- Never edit the loop state file by hand, and never fire a loop repeatedly "to test" — each real fire is a billable Issue.

## Pause rule

The next fire of an `agent-daemon` loop waits while any open `loop/<id>` Issue exists, in any state including "PR created, awaiting human review"; it resumes once that Issue is closed (merging its PR closes it) and then fires once for the missed slot. This keeps a repair loop from redoing work that an unmerged PR already carries.

## Recovery

- Nothing fires at the scheduled time: the daemon that advances it is not running (an `agent-daemon` loop needs `kc daemon` for that repository, restarted after a KedaCode upgrade), or the loop is paused — read the reason in `kc loop list`: the previous Issue is still open (merge or close it), or `skip_when` matched.
- An edited recipe has no effect: re-register it with `kc loop create <id> --recipe <path> --force`.
- Remove a loop for good: `kc loop cancel <id>`.

## Related

Keeping the daemon that advances an `agent-daemon` loop alive is the 后台跑 route (`${CODEBUDDY_SKILL_DIR}/references/daemon.md`); the Issue a fire creates is then handled like any other Issue (`${CODEBUDDY_SKILL_DIR}/references/triage.md` when it gets stuck). The full recipe reference is `docs/guides/loop.md`.
