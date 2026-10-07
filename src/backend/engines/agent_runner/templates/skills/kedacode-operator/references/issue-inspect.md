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
