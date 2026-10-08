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
- When any flag, default, or enum is unknown to you, introspect instead of guessing: `kc schema --json` is derived from the live command tree.
- Never run `kc init --force` to "fix" an installation problem — skill refresh has its own entry in `kc skill install`; never treat the presets/doctor/schema commands as writes — they are read-only.

## Recovery

- A conflict report from `kc init` or `kc skill install` means your own Skill files were preserved — nothing was lost; act only on the user's explicit overwrite request.
- `kc init` refusing to run because `.kedacode.toml` already exists is not an installation failure: run `kc skill install` for the Skill side, and reserve `kc init --force` for a genuine config rebuild (it rewrites the whole file).
- A wrong repo id registered at init: re-run `kc init --dry-run` to see the current registration before deciding.

## Related

Once the environment is set up, everyday operations start from the routing table in the main file — most commonly 建 issue (`${CODEBUDDY_SKILL_DIR}/references/create-issue.md`).
