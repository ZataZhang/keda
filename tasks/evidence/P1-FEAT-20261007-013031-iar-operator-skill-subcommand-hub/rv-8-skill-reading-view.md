# 最终随包 Skill 全文阅读视图

这是发行资源原文；Human-Confirmed 仍待用户实际审阅。

## SKILL.md

```markdown
---
name: kedacode-operator
description: Use KedaCode (kc) when the user asks to operate the KedaCode CLI or its managed runner — 装环境与查 Agent 配置 (initialize a repository, inspect agent presets/models), 建 issue (create an Issue from a PRD file or from a one-line request), 查 issue (inspect Issues and the ready queue), 跑一次 (run one execution pass, preview or execute), 看进度 (watch progress and per-Issue live agent output), 卡住了 (triage stuck, blocked, or failed Issues), 后台跑 (run and manage daemons in the background), or CI 交付 (observe CI status, set the auto-repair policy, request one manual repair). Read-only requests never start execution.
---

# KedaCode Operator

Use this skill when the user asks to operate the KedaCode CLI or its managed runner. Match the request to one route in the table below, read that route's reference file (an absolute path once the skill is installed), then act. If the wording is a CLI direct translation rather than a phrase below, route by the command name.

## Routes

| Route | The user may say | Read first |
|---|---|---|
| 建 issue | 建个 issue / 把 tasks/pending/xxx.md 建成 issue / 开个 Issue / issue create | `${CODEBUDDY_SKILL_DIR}/references/create-issue.md` |
| 查 issue | 查 issue / issue list / 看看有哪些 Issue / 这个 PRD 有 issue 吗 | `${CODEBUDDY_SKILL_DIR}/references/issue-inspect.md` |
| 跑一次 | 跑一下 / 跑一次 / 执行这个 Issue / run this / 快速通道发 PR / 直接发 PR | `${CODEBUDDY_SKILL_DIR}/references/run-once.md` |
| 看进度 | 看看 197 现在什么情况 / 到哪了 / 实时输出 / logs / tail / `/ps` | `${CODEBUDDY_SKILL_DIR}/references/watch.md` |
| 卡住了 | 怎么不动了 / 卡住了 / 失败了 / agent/blocked / 恢复 | `${CODEBUDDY_SKILL_DIR}/references/triage.md` |
| 后台跑 | 挂后台 / 让它自己跑 / daemon / 起守护 / 停掉它 | `${CODEBUDDY_SKILL_DIR}/references/daemon.md` |
| CI 交付 | CI 状态 / checks 怎么样 / 自动修复 / 修一下 CI / backlog ci | `${CODEBUDDY_SKILL_DIR}/references/ci.md` |
| 装环境与查 Agent 配置 | 装环境 / 初始化仓库 / kc init / 有哪些模型预设 / agent doctor / 这个命令收什么旗标 | `${CODEBUDDY_SKILL_DIR}/references/setup-and-config.md` |

Use the repository guide as the authoritative command reference. Start with `kc --help` or the relevant subcommand `--help` when command details differ by KedaCode version. Do not claim `--dry-run` executes work.

Every example below uses the current command name `kc`. The legacy `iar` command still works as a permanent deprecated alias, but do not write it into new scripts. <!-- legacy-alias -->

## Shared baselines (apply to every route)

- **Reference paths**: use `${CODEBUDDY_SKILL_DIR}` when available; otherwise resolve the absolute parent directory of the installed `SKILL.md` you actually read, then append `references/<file>.md`. Never resolve from the working directory or assume another Agent exposes this variable.
- **Confirmation means checking scope and existing authorization.** An explicit instruction already authorizing the target and side effects remains valid across routes and recovery; explain the action and continue without asking again. Ask only when required information or authorization is missing, the scope expands, or a new irreversible action needs approval. A read-only request never authorizes execution; replacing user-owned Skills still requires explicit replacement authorization.

- **A read-only request gets a read-only command.** Never start `kc run` or `kc registry start` just because the user asked to *view* progress — an inspect, preview, or "just check" request never becomes execution.
- **`kc run` requires a target** (breaking change): pass `--issue <N>`, a PRD path (resolved via the PRD's `- GitHub Issue:` link — a PRD without one must first go through `kc issue create`), or `--all-ready` for the historical queue-draining behavior. Bare `kc run` fails with a usage error.
- **Unknown flag, default, or enum value**: read `kc schema --json` — read-only introspection derived from the live command tree: every command, flag, type, required flag, enum choices, default, and an example — instead of guessing from a table.

## Consuming `kc` from a script or another Agent

Whenever the reader of the output is a program rather than a human, pass `--json` explicitly. `--json` is an alias of `--output json`; with neither flag the output stays the human table, **including when stdout is a pipe**, so never assume a machine format.

- In JSON mode stdout carries data only. Progress, warnings, and errors go to stderr, so `command > out.json` cannot be polluted by log lines.
- A failure in JSON mode writes one envelope to stderr: `{"error", "message", "suggestion", "retryable", "exit_code"}`. `suggestion` is a runnable command, and the only safe way to continue is `$?` — do not parse English.
- `kc ask` and `kc deliberate` reuse `--output` for an output **directory**, so `--output json` there means a directory literally named `json`. Neither takes `--json`; read their human summary or the written session files instead.
- Exit codes published by `kc --help` and by `kc schema --json` → `exit_codes.values`. The Name column is exactly the envelope's `error` value, so `$?` and stderr never disagree:

| Code | Name | Meaning | What the caller does |
|---|---|---|---|
| `0` | `ok` | Requested work completed (read-only output counts as success). | Continue. |
| `1` | `error` | Failure with no published category. | Read stderr, then retry or escalate. |
| `2` | `usage_error` | Flags or arguments do not form a valid command: bare `kc run`, mutually exclusive targets (e.g. `--from-prompt` with or without a PRD path), or a single-target-only flag used with `--all-ready`. | Fix the invocation; retrying unchanged fails again. |
| `3` | `not_found` | Target missing: repository, registry entry, agent, executable, or log. Also an explicitly targeted Issue that cannot be read or is closed. | Switch target, or run the `suggestion`. |
| `4` | `permission_denied` | Not authorized: GitHub auth or a disabled repository. | Ask the human to authenticate or enable it; do not retry silently. |
| `5` | `conflict` | Current state blocks the request: `--all-ready` while a daemon polls the same queue, an explicitly targeted Issue that is live-claimed by another process or is `agent/blocked` with no unblock request, workflow template file already installed, loop entry already exists. | Rename, pass `--force` when the user asked for it, stop the competing process, or run the `suggestion` (for a live claim it names the holder host and PID; for a blocked Issue it is `kc blocked-continue --issue <N>`). |
| `10` | `dry_run_ok` | The plan is valid and nothing was written. Usable as a CI gate. | Treat as green only after checking the plan body. |

## Safety and compatibility

- Explain GitHub writes, Agent execution, and persistent background processes before carrying them out. A queue preview is read-only.
- **An explicit target does not need `agent/ready`**: readiness gates the daemon's *autonomous pick*, not a human-named run. Any open Issue can be run with `kc run --issue <N>` — including one made by hand with no PRD anchor and no labels. What an explicit target cannot bypass is claim state, and the answer is loud instead of a silent skip: `agent/running` with a live holder → conflict `5` naming the holder host and PID; `agent/blocked` with no unconsumed unblock request → conflict `5` with `kc blocked-continue --issue <N>` as the suggestion; unreadable or closed → `3 not_found`. A dead local holder (PID gone) is not a conflict — the run resumes the in-flight work. An empty ready queue stays the normal, silent, exit-0 case.
- **The daemon mutex covers queue polling only**: `kc run --all-ready` refuses with a conflict while a live daemon serves the same repository, because two pollers would double-claim the same ready queue; an explicitly targeted `kc run --issue <N>` coexists with a live daemon. Stopping the daemon is a separate, explicit step (`kc registry stop --repo-id <id>`; the daemon command group exposes only `run` and `status`, no stop), or pass `--takeover` (add `--yes` in scripts) to stop the daemon gracefully, reclaim its in-flight Issues, and take over — destructive, because it interrupts *all* of the daemon's in-flight Issues. With no daemon alive, several `kc run --issue <N>` invocations for different Issues overlap freely, because each Issue gets its own worktree (`.iar-worktrees/issue-<N>`) and its own claim lock. Two concurrent `--all-ready` invocations remain unsafe.
- **First claim is settled by election, not by luck**: GitHub offers no compare-and-swap on labels, so each candidate posts its own `iar:claim` marker, waits a short grace window, reads the comment thread back, and the *earliest* bid (`started_at`, then host, then PID — a total order, so every observer picks the same winner) transitions the Issue to `agent/running`. Everyone else rewrites its own comment to `iar:claim-withdrawn` and skips the Issue **without touching labels**, so losing a race is silent rather than a failed Issue. Only same-round bids count: markers without a `started_at` field, and bids outside the concurrent window, are historical claims and are excluded so rework can never be locked out. A same-host bid whose PID is dead is dropped; a foreign-host bid cannot be probed and is treated as alive (fail-closed).
- **`kc run --fast-merge` (fast track, one-shot)**: after the builder commits, skip the validation gates (rv re-exec + independent verifier) and publish the Draft PR immediately, annotated as unverified. The PR body carries a machine-readable `<!-- iar:fast-merge issued=<N> -->` marker plus a human notice to verify manually before merging. It is defined for a single targeted Issue only: it does not combine with `--all-ready` (usage error), and it is rejected for Issues that declare a `stack` dependency (an unverified upstream would poison every fork on the chain). The daemon has no `--fast-merge` and there is no config key for it — the bypass is this run only.
- **`kc run --issue <N> --direct-pr` (direct tier, one-shot)**: for an Issue with **no `PRD path:` anchor**, publish the Draft PR after the build stage while skipping the review Agent and the runner-side verification commands on top of the fast-merge bypass; the PR body carries `<!-- iar:direct-pr issued=<N> -->` and a notice that CI on the PR now carries the quality gate. It is rejected on a PRD-backed target or when the Issue body cannot be read (fail-closing keeps an unarchived, unchecked PRD from being bypassed), does not combine with `--all-ready`, and is mutually exclusive with `--fast-merge` (usage error, not "whichever is stronger"). The daemon has no `--direct-pr`.
- **`kc issue create --from-prompt "<需求>"` (Issue without a PRD)**: takes either PRD path arguments or `--from-prompt`, never both and never neither (usage error). The generated body deliberately carries **no** `PRD path:` anchor and **no** acceptance section, so the runner treats it as a plain Issue; `--require-validation` is the opt-in that appends the Realistic Validation section. It writes only GitHub state — no local file is created and the working tree stays clean — and falls back to the deterministic template when AI content generation is unavailable or fails. It enters the ready queue only with `--ready`; by default a human runs it explicitly.
- **Autopilot belongs to the daemon, not `run`**: `kc daemon run --autopilot` / `kc daemon run --no-autopilot` overrides the *scheduling* autopilot for that daemon run (flag > repo `.kedacode.toml` > global, locked for the process). It never arms auto-merge — merging stays behind the `safety.auto_merge` + `autopilot.enabled` config switches. To promote a pending PRD manually, use `kc backlog advance`. `run` has no `--autopilot` and no `--concurrency`: Issue-level parallelism is a daemon-only knob inside one process (default `max_concurrent_issues=1`, sequential), and across processes it comes from issuing one `kc run` per Issue.
- A user-owned `kedacode-operator` Skill with different content is preserved by default. `kc init --dry-run` reports the conflict. Only pass `--force` when the user explicitly requests replacing that file.
- **Old-name packaged copy cleanup**: `kc init` also checks every install root for a leftover operator Skill published under the previous name. It removes one only when the directory holds nothing but the known packaged files and its `SKILL.md` matches a past packaged version byte for byte; a modified copy is kept and its path is reported so nothing is lost silently. `kc init --force` removes the old-name copy outright.
- The PRD runner currently accepts Machine Contract v3, v4, and v5. Versions outside the supported set fail preflight before GitHub state writes. Updating the installed `prd` Skill is a separate action; do not recommend blind `kc init --force`, because it can replace user-owned Skills.
- Ready Issue priority comes from `priority/P0` through `priority/P3` labels. Selection order is P0, P1, P2, P3, then Issues without one of those labels; ties use ascending Issue number. Dry-run and execution share this ordering.
- The runner orders the candidate window returned by GitHub (currently at most 100 ready Issues per pass); do not describe it as a global sort across Issues outside that window.

## Lifecycle reference

For repository setup, Issue/PRD commands, one-shot runs, viewing and triaging agent output, registry-managed daemons, logs, and shutdown steps, read [`docs/guides/agent-runner.md`](docs/guides/agent-runner.md). In particular, use its daemon lifecycle section before starting persistent processes.

- Draft PR title/body generation defaults to deterministic publication (`generated_content.draft_pr.enabled = false`), with commit facts and tracked validation-report references. Set this target to `enabled = true` to opt into AI writing; this does not change review, validation, verifier, or merge gates. The body itself never establishes PASS. Existing explicit target settings are respected.
```

## references/ci.md

```markdown
# CI 交付 — observe CI, set the auto-repair policy, request one manual repair

## When to use

The user asks 「CI 怎么样」「checks 为什么红了」「让机器人修一下 CI」「关掉/打开自动修复」, or translates the CLI directly: `kc backlog ci status`. Work execution itself is 跑一次.

## Confirm before acting

1. **Read the current state before changing anything** — `kc backlog ci status --json --repo-id <repo-id>` (or `kc backlog ci status --prd <prd-path> --repo-id <repo-id>`) shows the raw GitHub checks state, repair rounds, and the stored/global/effective auto-repair policy; record the original stored global value and per-PRD override (not merely the effective value) before editing; acting without them can flip a policy the user never mentioned and prevents faithful restoration.
2. **Which target the policy applies to** — `--global` writes the repository-level `post_pr_supervisor.auto_repair_ci` in `.kedacode.toml`; `--prd` writes the latest policy marker on the PRD's Issue. The two targets are mutually exclusive; picking the wrong one changes behavior beyond the PRD the user meant.
3. **A repair request runs an Agent** — even though it is idempotent per head SHA and still gated by max attempts, worktree, and forbidden-path checks, it is execution: preview with `--dry-run` first, and carry it out only on the user's explicit request.

## Execute and never do

- Observe (read-only): `kc backlog ci status --json --repo-id <repo-id>`; `--json` prints the same DTO as the Console API on stdout.
- Set the policy (a write): `kc backlog ci policy --global on --repo-id <repo-id>`; `kc backlog ci policy --prd <prd-path> inherit --repo-id <repo-id>` (`inherit` clears the override).
- Request one manual repair: `kc backlog ci repair --prd <prd-path> --dry-run --repo-id <repo-id>` to see exactly what would happen with zero side effects, then the same command without `--dry-run` only when the user asked for it.
- A status question never authorizes a repair or a policy change — those are separate explicit requests.

## Recovery

- A per-PRD override set by mistake: restore the stored override recorded before editing with `kc backlog ci policy --prd <prd-path> on --repo-id <repo-id>` or the same command with `off`; use `inherit` only if the original override was absent/inherited.
- A global policy set by mistake: restore the recorded repository-level value with `kc backlog ci policy --global on --repo-id <repo-id>` or the same command with `off`, whichever matches the original. If no original value was recorded, inspect history or ask rather than guessing.

## Related

The PR a check belongs to comes from a run; publication tiers and their bypass flags are on the 跑一次 route (`${CODEBUDDY_SKILL_DIR}/references/run-once.md`).
```

## references/create-issue.md

```markdown
# 建 issue — create an Issue from a PRD file or a one-line request

## When to use

The user says 「把 tasks/pending/xxx.md 建成 issue」「建个 issue」「开个 Issue」「发 issue」, or translates the CLI directly: `kc issue create`. A question about whether an Issue already exists is read-only — that is the 查 issue route, not this one.

## Confirm before acting (each check has a concrete consequence)

1. **The PRD file exists and is readable** — the path is positional input to `kc issue create`; a missing or unreadable file fails before the GitHub Issue is created, so fix the input rather than treating creation as successful.
2. **The PRD does not already have a linked Issue** — read the `- GitHub Issue:` line inside the PRD file first, then double-check with `kc issue list --repo <path> --state open`. An explicitly named file with an existing link is refused by default, and directory discovery skips linked files. Do not use `--force` to bypass this check casually: it replaces the PRD link with a new Issue and can leave duplicate work under the old Issue.
3. **No in-flight PR already delivers this PRD** — ask `kc backlog ci status --prd <prd-path> --repo-id <repo-id>`; if a PR for this PRD is still open, a fresh Issue re-queues work that is already moving.
4. **Whether the user wants the Issue to enter the queue now** — creating an Issue and enqueuing it (a daemon may pick it up) are different intents. PRD-path creation also back-writes the local PRD link and, by default, stages, commits, and pushes that PRD; only `--from-prompt` creation leaves local files untouched. `--ready` enqueues; by default a human runs it explicitly. Conflating them is unintended execution.
5. **The working directory resolves to exactly one registered repository** — when it matches several, commands refuse to guess; pass `--repo <path>` or `--repo-id <id>` so the create targets the repo the user means.

## Execute and never do

- `kc issue create tasks/pending/<file>.md` creates and publishes the GitHub Issue and back-writes the link to the PRD; add `--ready` only when the user asked to enqueue: `kc issue create tasks/pending/<file>.md --ready --repo <path>`.
- An Issue with no PRD behind it comes from a one-line request: `kc issue create --from-prompt "<一句话需求>"` — mutually exclusive with the PRD-path argument (neither or both is a usage error). Add `--require-validation` only when the user wants validation gates in the body.
- Creating an Issue **writes GitHub state**; PRD-path creation also updates and, by default, publishes the local PRD — confirm the target, readiness choice, repository, and these side effects under the hub's existing-authorization rule before running it.
- Never turn 「看看这个 PRD 有没有 issue」or any other read-only check into a create, and never invent a PRD path the user did not name.

## Recovery

- Wrong or duplicate Issue: close the extra one (`gh issue close <N> --comment "duplicate of <M>"`) and keep the one the PRD links back to; fix the PRD's `- GitHub Issue:` line if it now points at a closed Issue.
- Creation does not run an implementation Agent or create its Issue worktree. For `--from-prompt`, cleanup is GitHub-side only; for a PRD-path creation, also inspect the back-written local link and any already-published PRD commit. Correct the link through the normal authorized publication flow; do not reset Git history or discard unrelated changes.

## Related

When the user wants this Issue run right away after creation, continue with the 跑一次 route (`${CODEBUDDY_SKILL_DIR}/references/run-once.md`).
```

## references/daemon.md

```markdown
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
```

## references/issue-inspect.md

```markdown
# 查 issue — inspect Issues and the ready queue (read-only)

## When to use

The user asks 「这个 PRD 有 issue 吗」「查 issue」「issue list」「看看有哪些 Issue 在排队」「197 是 PRD 挂的吗」, or translates the CLI directly: `kc issue list`. Anything about live logs or label interpretation belongs to 看进度 and 卡住了.

## Confirm before acting

1. **Pick the right repository** — when the working directory matches several registered repositories, listing refuses to guess; pass `--repo <path>` or `--repo-id <id>`, otherwise you get a usage error and no answer.
2. **Choose filters from the live command tree, not from memory** — `kc issue list --help` or `kc schema --json` shows which state, label, and PR filters exist; a guessed flag name fails the whole query.

## Query and never do

- `kc issue list --repo <path> --state open` — read-only GitHub query; add `--label agent/ready` or `--limit <n>` to narrow it.
- To tell whether an Issue is PRD-backed, read its body for the `- GitHub Issue:` / `PRD path:` anchors (`gh issue view <N>`); the answer decides which route the follow-up request belongs to.
- **Every command on this page is a query: none of them starts, claims, or re-queues work.** A 「查一下」request must never be answered with `kc run` or `kc registry start` — if the user then asks for execution, that is an explicit new request on the 跑一次 route.

## Recovery

A query that fails on an ambiguous or missing repository target is fixed by passing `--repo-id <id>` — nothing was written, so there is nothing to undo.

## Related

For what a label means for the runner's next step, read the 卡住了 route (`${CODEBUDDY_SKILL_DIR}/references/triage.md`).
```

## references/run-once.md

```markdown
# 跑一次 — execute one pass (single run, not a daemon)

## When to use

The user says 「跑一下」「跑一次」「执行这个 Issue」「把 197 跑起来」「现在就做这个」, or translates the CLI directly: `kc run`. Unattended repeated processing is 后台跑, not this route; viewing what a run is doing is 看进度.

## Confirm before acting

1. **The user asked for execution, not a preview.** If the wording is 「看看会跑哪些」「先预览」, answer with a dry-run and stop — `--dry-run` writes nothing and must not be presented as having run work.
2. **A target is named.** Which Issue (or the ready queue) the user means decides the invocation; running a different Issue than the one named is a wrong side effect nobody asked for.
3. **Side effects are explained before running: this writes GitHub state, runs Agents, creates branches/worktrees, and can open or update PRs.** For a queue run (`--all-ready`) confirm the user really wants the whole ready queue advanced, not just one Issue.

## Execute and never do

- Preview the next pass: `kc run --all-ready --dry-run --max-issues 3` (or `kc run --issue <N> --dry-run` for one Issue) — read-only; shows the selected ready Issues and their priority; does not run an Agent or claim work.
- Execute one pass: `kc run --issue <N> --max-issues 1` for one targeted Issue; `kc run --all-ready --max-issues 1` for the whole ready queue. Runs configured Agents and may update GitHub, create branches/worktrees, and open or update PRs.
- Drive several Issues at once: one `kc run --issue <N>` invocation per Issue, each with its own target; never combine this with `--all-ready`, which claims the same ready queue twice.
- Bypass gates only when the user explicitly asked for it: `kc run --issue <N> --fast-merge` publishes the Draft PR unverified (single target only); `kc run --issue <N> --direct-pr` is for an Issue **without a `PRD path:` anchor** and additionally skips the review Agent. Neither is a daemon flag, neither arms auto-merge, and both are usage errors with `--all-ready`.
- Never add autopilot-style flags to `run` — check unknown flags against `kc schema --json` before inventing any invocation.

## Recovery

- Exit `5` naming a live claim holder: the work is already running — view it via 看进度 instead of re-running.
- The pass built but failed to publish (push/PR/state update): `kc recover --issue <N>` resumes publication without redoing the work.
- The working directory matched several registered repositories and the command refused: re-run with `--repo-id <id>`.

## Related

After a run opens a PR, CI observation lives on the CI 交付 route (`${CODEBUDDY_SKILL_DIR}/references/ci.md`).
```

## references/setup-and-config.md

```markdown
# 装环境与查 Agent 配置 — initialize a repository, inspect presets, introspect the command tree

## When to use

The user says 「装环境」「初始化仓库」「把这个仓库登记进来」「kc init」, or asks what agents/models/presets exist (「有哪些模型预设」「这条命令收什么旗标」), or translates the CLI directly: `kc init` / `kc agent presets` / `kc agent doctor` / `kc schema`.

## Confirm before acting

1. **`kc init` writes** — repository config, `.gitignore` entries, installed Skills in the user skill roots, a registry entry, and a GitHub label sync. Run `kc init --dry-run` first and show the plan; only a previewed init earns the real one.
2. **Skill conflict semantics** — a user-owned `kedacode-operator` Skill with different content is preserved by default and reported as a conflict. The overwrite-style `--force` can replace the user's own Skills and is **not a version-repair tool**: pass it only when the user explicitly asked to replace those files.
3. **The remote template download needs network and GitHub access** — `kc init` also clones the `prd` / `code-reviewer` template skills and syncs labels; a label-sync failure is reported, not fatal, but the user should hear why.
4. **Repository ambiguity** — when the working directory matches several registered repositories, later commands refuse to guess; init reports the repo id it registered, and other routes pass it as `--repo-id <id>`.

## Execute and never do

- Initialize: `kc init --dry-run`, then `kc init`.
- Inspect model presets (agent + model + reasoning effort), read-only: `kc agent presets` — lists every defined `[agent_runner.presets.<name>]` entry.
- Preview a stage's or preset's exact command line, read-only: `kc agent doctor <agent> --preset <name> --json`; `kc agent doctor --lifecycle <key>` — the nine lifecycle keys are accepted; fix/closeout without an implementer context report `follows_implementation` instead of argv.
- When any flag, default, or enum is unknown to you, introspect instead of guessing: `kc schema --json` is derived from the live command tree.
- Never run `kc init --force` to "fix" an installation problem, and never treat the presets/doctor/schema commands as writes — they are read-only.

## Recovery

- A conflict report from `kc init` means your own Skill files were preserved — nothing was lost; act only on the user's explicit overwrite request.
- A wrong repo id registered at init: re-run `kc init --dry-run` to see the current registration before deciding.

## Related

Once the environment is set up, everyday operations start from the routing table in the main file — most commonly 建 issue (`${CODEBUDDY_SKILL_DIR}/references/create-issue.md`).
```

## references/triage.md

```markdown
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
3. Only after retries *and* fallback are exhausted does the Issue become `agent/failed`.

## Recover

- **Failed publish** — the work is fine but the push, PR, or state update was not: `kc recover --issue <N>` resumes it. Do not re-queue and redo the work.
- **Back to the queue** — after fixing the cause, `gh issue edit <N> --add-label agent/ready --remove-label agent/failed` lets the next pass pick it up.
- **Where the code is** — `kc worktree path --branch issue-<N>` prints the worktree root. A re-run reuses that worktree, so uncommitted work in it is usually still wanted.
- **Ambiguous target** — when the working directory matches more than one registered repository, several commands refuse to guess; pass `--repo-id <id>` (or `--repo`).

Label edits (`gh issue edit`) and `kc blocked-continue` change what the runner may execute next — confirm the decision with the user before applying them, and never use them to talk a read-only question into a re-run.

## Related

Conflict codes from a live claim are produced by runs; their invocation details are on the 跑一次 route (`${CODEBUDDY_SKILL_DIR}/references/run-once.md`).
```

## references/watch.md

```markdown
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
```
