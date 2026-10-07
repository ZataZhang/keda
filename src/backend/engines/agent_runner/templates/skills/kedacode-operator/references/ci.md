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
