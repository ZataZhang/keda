# 装环境与查 Agent 配置 — initialize a repository, inspect presets, introspect the command tree

## When to use

The user says 「装环境」「初始化仓库」「把这个仓库登记进来」「kc init」, 「重装 skill」「刷新 skill」「kc skill install」, or asks what agents/models/presets exist (「有哪些模型预设」「这条命令收什么旗标」), or translates the CLI directly: `kc init` / `kc skill install` / `kc agent presets` / `kc agent doctor` / `kc schema`.

## Confirm before acting

1. **`kc init` writes** — repository config, `.gitignore` entries, installed Skills in the user skill roots, a registry entry, and a GitHub label sync. Run `kc init --dry-run` first and show the plan; only a previewed init earns the real one.
2. **Skill conflict semantics** — a user-owned `kedacode-operator` Skill with different content is preserved by default and reported as a conflict. The overwrite-style `--force` can replace the user's own Skills and is **not a version-repair tool**: pass it only when the user explicitly asked to replace those files.
3. **The remote template download needs network and GitHub access** — `kc init` and `kc skill install` both clone the `prd` / `code-reviewer` template skills; a label-sync failure during init is reported, not fatal, but the user should hear why.
4. **Repository ambiguity** — when the working directory matches several registered repositories, later commands refuse to guess; init reports the repo id it registered, and other routes pass it as `--repo-id <id>`.

## Execute and never do

- Initialize: `kc init --dry-run`, then `kc init`.
- Refresh user-level Skills only (already-initialized repo, renamed packaged skill, or an explicit skill-refresh request): `kc skill install --dry-run`, then `kc skill install`. It never reads or writes `.kedacode.toml`, so it also works where `kc init` would stop at the existing config; it takes no repository (reject `--repo` / `--repo-id` / `--config` as a usage error).
- Inspect model presets (agent + model + reasoning effort), read-only: `kc agent presets` — lists every defined `[agent_runner.presets.<name>]` entry.
- Preview a stage's or preset's exact command line, read-only: `kc agent doctor <agent> --preset <name> --json`; `kc agent doctor --lifecycle <key>` — the nine lifecycle keys are accepted; fix/closeout without an implementer context report `follows_implementation` instead of argv.
- View the effective lifecycle matrix and executor fallback chain, read-only: `kc agent lifecycle list [--scope effective|global|repository] [--repo-id <id>] [--output json]` prints the nine stages with effective agent/model/effort, bound preset and per-field source; `kc agent fallback list [--scope …] [--repo-id <id>] [--output json]` prints the ordered fallback candidates with each candidate's preset and the `max_agent_switches` budget. `effective` resolves to a repository when the cwd or a selector uniquely matches one registered repo, otherwise global.
- Persist lifecycle settings, **always an explicit write scope**: `kc agent lifecycle set <stage> --preset <name> --scope global|repository [--repo-id <id>]` binds a stage to a named preset; `kc agent lifecycle unset <stage> --scope …` removes only that stage's binding in the target layer; `kc agent preset set <name> --agent <a> [--model <id>] [--reasoning-effort <v>] --scope …` upserts a preset triple (omitted flags mean "do not set that field"). Writes go to global `config.toml` or the named repository `.kedacode.toml`; repository writes require `--repo-id`/`--repo`, `--scope global` refuses those selectors (usage error, nothing written), preset names are trimmed before they are written, and a missing scope, unknown stage/preset/agent, or a preset whose agent mismatches fail before touching the file.
- Edit executor fallback candidates: `kc agent fallback candidate add --agent <a> [--preset <p>] [--position <n>] --scope …`, `… preset set <position> --preset <p>` / `preset unset <position>`, `… remove <position>`, `… move <position> --to <n>` — candidates are ordered `(agent, preset)` pairs, the same agent may repeat with a different preset, an exact duplicate is rejected, and `max_agent_switches` counts candidate steps. A candidate's preset applies only when the chain actually reaches it. Write target follows `--scope`: `--scope global` writes the machine-level `config.toml` (the chain every repository inherits); `--scope repository --repo-id <id>` writes `[[agent_runner.runner.agent_fallback_candidates]]` into **that repository's** `.kedacode.toml`, where a non-empty array takes the chain over wholesale for that repo — the command materializes the currently effective chain (plus that repo's `max_agent_switches`) into the repo file, after which the repo no longer follows machine-level edits. Use `--scope global` unless the user explicitly wants a per-repository chain; the Console page's fallback section only ever writes the machine-level file.
- When any flag, default, or enum is unknown to you, introspect instead of guessing: `kc schema --json` is derived from the live command tree.
- Never run `kc init --force` to "fix" an installation problem — skill refresh has its own entry in `kc skill install`; treat `presets` / `doctor` / `schema` / `lifecycle list` / `fallback list` as read-only, and treat `lifecycle set/unset` / `preset set` / `fallback candidate …` as persistent writes that need an explicit `--scope` (and `--repo-id` for repository writes) — never let a bare one-shot `--preset/--model/--reasoning-effort` be mistaken for a persistent command, and never write these without the user asking to persist it.

## Recovery

- A conflict report from `kc init` or `kc skill install` means your own Skill files were preserved — nothing was lost; act only on the user's explicit overwrite request.
- `kc init` refusing to run because `.kedacode.toml` already exists is not an installation failure: run `kc skill install` for the Skill side, and reserve `kc init --force` for a genuine config rebuild (it rewrites the whole file).
- A wrong repo id registered at init: re-run `kc init --dry-run` to see the current registration before deciding.

## Related

Once the environment is set up, everyday operations start from the routing table in the main file — most commonly 建 issue (`${CODEBUDDY_SKILL_DIR}/references/create-issue.md`).
