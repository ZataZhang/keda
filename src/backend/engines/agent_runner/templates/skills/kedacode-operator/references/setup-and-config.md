# 装环境与查 Agent 配置 — initialize a repository, inspect presets, introspect the command tree

## When to use

The user says 「装环境」「初始化仓库」「把这个仓库登记进来」「kc init」, 「重装 skill」「刷新 skill」「kc skill install」, or asks what agents/models/presets exist (「有哪些模型预设」「这条命令收什么旗标」), or translates the CLI directly: `kc init` / `kc skill install` / `kc agent presets` / `kc agent doctor` / `kc schema`. This route also owns the two interactive surfaces: 「裸 kc 进哪个入口」「 kc session 和 kc repl 有什么区别」「起个预览」「dev server 起不起来」 — `kc session` and `kc preview start|status|stop` are documented below.

## Confirm before acting

1. **`kc init` writes** — repository config, `.gitignore` entries, installed Skills in the user skill roots, a registry entry, and a GitHub label sync. Run `kc init --dry-run` first and show the plan; only a previewed init earns the real one.
2. **Skill conflict semantics** — a user-owned `kedacode-operator` Skill with different content is preserved by default. `kc session` stops before starting the provider until the conflict is resolved; it never treats the conflicting content as verified packaged instructions. The overwrite-style `--force` can replace the user's own Skills and is **not a version-repair tool**: pass it only when the user explicitly asked to replace those files.
3. **The remote template download needs network and GitHub access** — `kc init` and `kc skill install` both clone the `prd` / `code-reviewer` template skills; a label-sync failure during init is reported, not fatal, but the user should hear why.
4. **Repository ambiguity** — when the working directory matches several registered repositories, later commands refuse to guess; init reports the repo id it registered, and other routes pass it as `--repo-id <id>`.

## The native executor entry (`kc session`)

Bare `kc` in a terminal **is** `kc session`: KedaCode resolves `[agent_session].default_agent` (or `--agent`), checks the packaged operator Skill, hands the terminal over, and returns the provider's exit code unchanged. It does not interpret the conversation, does not change permission policy, and does not start project services. Non-TTY `kc` prints help and exits **1** so pipes and CI cannot read a wrapped failure as success. The Keda REPL that interprets Agent output stays at `kc repl` with its own `[agent_runner.repl]` section — the two entries are deliberately separate risk surfaces.

- `kc session --agent <name>` — override the default executor. The Agent must declare an `interactive` profile; otherwise the entry fails fast instead of silently falling back.
- If the installed operator Skill differs from the packaged version, `kc session` preserves the user's files and stops before starting the provider. Resolve the conflict explicitly, then start a new session.
- `kc session` never authorizes you to start a dev server, install dependencies, or open a tunnel. The human is now talking to the provider directly; your role is the same operator Skill, nothing wider.

## On-demand project preview (`kc preview start|status|stop`)

The preview dev server starts **only when the user asked for it in the conversation**. KedaCode never guesses that intent and never starts one on entry.

- `kc preview start` — start the repository's dev server from `[agent_session.preview].argv` (an exact argv array such as `["npm", "run", "dev"]`, never shell text, so no `sh -c` path exists). With no configured argv it falls back to "the repository's single candidate plus explicit user confirmation"; pass `--confirm` in scripts only after the user confirmed.
- `kc preview status` — read-only: managed process state and its loopback URL.
- `kc preview stop` — stop the KedaCode-owned process group; never a foreign PID.

`[agent_session.preview].ready_url` must point at a loopback host (`localhost`, `127.0.0.1`, `::1`); a routable host is a **config-load error**, raised before any process spawns. A live preview already registered for the repository blocks a second `start` and names `kc preview stop` instead of double-spawning. Tunnels, external preview platforms, and "auto-start the dev server because the user might want it" are out of scope.

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
