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
