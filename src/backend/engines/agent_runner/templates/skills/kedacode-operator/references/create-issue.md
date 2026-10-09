# 建 issue — create an Issue from a PRD file or a one-line request

## When to use

The user says 「把 tasks/pending/xxx.md 建成 issue」「建个 issue」「开个 Issue」「发 issue」, or translates the CLI directly: `kc issue create`. A question about whether an Issue already exists is read-only — that is the 查 issue route, not this one.

## Confirm before acting (each check has a concrete consequence)

1. **The PRD file exists and is readable** — the path is positional input to `kc issue create`; a missing or unreadable file fails before the GitHub Issue is created, so fix the input rather than treating creation as successful.
2. **The PRD does not already have a linked Issue** — read the `- GitHub Issue:` line inside the PRD file first, then double-check with `kc issue list --repo <path> --state open`. An explicitly named file with an existing link is refused by default, and directory discovery skips linked files. Do not use `--force` to bypass this check casually: it replaces the PRD link with a new Issue and can leave duplicate work under the old Issue.
3. **No in-flight PR already delivers this PRD** — ask `kc backlog ci status --prd <prd-path> --repo-id <repo-id>`; if a PR for this PRD is still open, a fresh Issue re-queues work that is already moving.
4. **Whether the user wants the Issue to enter the queue now** — creating an Issue and enqueuing it (a daemon may pick it up) are different intents. PRD-path creation also back-writes the local PRD link and, by default, stages, commits, and pushes that PRD; only `--from-prompt` creation leaves local files untouched. Creation **enqueues by default**, so pass `--no-ready` when the user asked only to create the Issue and will run it explicitly later. Since enqueueing is the default, ask first when the user's wording does not make the intent clear.
5. **The working directory resolves to exactly one registered repository** — when it matches several, commands refuse to guess; pass `--repo <path>` or `--repo-id <id>` so the create targets the repo the user means.

## Execute and never do

- `kc issue create tasks/pending/<file>.md` creates and publishes the GitHub Issue, back-writes the link to the PRD, and **enqueues it by default**; add `--no-ready` when the user asked only to create it: `kc issue create tasks/pending/<file>.md --no-ready --repo <path>`.
- An Issue with no PRD behind it comes from a one-line request: `kc issue create --from-prompt "<一句话需求>"` — mutually exclusive with the PRD-path argument (neither or both is a usage error). Add `--require-validation` only when the user wants validation gates in the body.
- Creating an Issue **writes GitHub state**; PRD-path creation also updates and, by default, publishes the local PRD — confirm the target, readiness choice, repository, and these side effects under the hub's existing-authorization rule before running it.
- Never turn 「看看这个 PRD 有没有 issue」or any other read-only check into a create, and never invent a PRD path the user did not name.

## Recovery

- Wrong or duplicate Issue: close the extra one (`gh issue close <N> --comment "duplicate of <M>"`) and keep the one the PRD links back to; fix the PRD's `- GitHub Issue:` line if it now points at a closed Issue.
- Creation does not run an implementation Agent or create its Issue worktree. For `--from-prompt`, cleanup is GitHub-side only; for a PRD-path creation, also inspect the back-written local link and any already-published PRD commit. Correct the link through the normal authorized publication flow; do not reset Git history or discard unrelated changes.

## Related

When the user wants this Issue run right away after creation, continue with the 跑一次 route (`${CODEBUDDY_SKILL_DIR}/references/run-once.md`).
