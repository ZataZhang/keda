# 装环境与查 Agent 配置 — initialize a repository, inspect presets, introspect the command tree

## When to use

The user says 「装环境」「初始化仓库」「把这个仓库登记进来」「kc init」, 「重装 skill」「刷新 skill」「kc skill install」, 「修复 operator skill」「kc skill repair-operator」, 「这台机器装了哪些 agent」「该用哪个 agent」「跑不起来说 CLI 不存在」「注册一个自己的 agent」, or asks what agents/models/presets exist (「有哪些模型预设」「这条命令收什么旗标」), or translates the CLI directly: `kc init` / `kc skill install` / `kc skill repair-operator` / `kc agent list` / `kc agent setup` / `kc agent add` / `kc agent presets` / `kc agent doctor` / `kc schema`. This route also owns the two interactive surfaces: 「裸 kc 进哪个入口」「 kc session 和 kc repl 有什么区别」「起个预览」「dev server 起不起来」 — `kc session` and `kc preview start|status|stop` are documented below.

## Confirm before acting

1. **`kc init` writes** — repository config, `.gitignore` entries, installed Skills in the user skill roots, a registry entry, and a GitHub label sync. Run `kc init --dry-run` first and show the plan; only a previewed init earns the real one.
2. **Skill conflict semantics** — a prior packaged `kedacode-operator` Skill that has not been edited refreshes automatically when the user next starts a native session. User changes to managed files are preserved by default; `kc session` stops before starting the provider until the conflict is resolved. To inspect or replace one copy only, use `kc skill repair-operator --skill-dir <absolute-path> --dry-run` and, only when the user explicitly asks to replace its managed files, add `--force`.
3. **The remote template download needs network and GitHub access** — `kc init` and `kc skill install` both clone the `prd` / `code-reviewer` template skills; a label-sync failure during init is reported, not fatal, but the user should hear why.
4. **Repository ambiguity** — when the working directory matches several registered repositories, later commands refuse to guess; init reports the repo id it registered, and other routes pass it as `--repo-id <id>`.
5. **`kc agent setup` and `kc agent add --write` write machine-level config** — the first edits the global lifecycle matrix in `~/.kedacode/config.toml`, the second appends an agent registration to the same file. Both refuse to choose for the user: preview with `kc agent setup --dry-run` (or `kc agent add` without `--write`, which only prints the TOML), then apply the choice the user named. Never run either to make a failing run "go through" by swapping the user's agent without saying so.

## The native executor entry (`kc session`)

Bare `kc` in a terminal **is** `kc session`: KedaCode resolves `[agent_session].default_agent` (repository `.kedacode.toml` override > machine-wide `config.toml`, default `~/.kedacode/config.toml`; `--agent` overrides one invocation), checks and refreshes the packaged operator Skill in that Agent's own Skill directory, hands the terminal over, and returns the provider's exit code unchanged. Exact prior packaged versions refresh automatically; user-edited managed files remain untouched and block startup. It does not interpret the conversation, does not change permission policy, and does not start project services. Non-TTY `kc` prints help and exits **1** so pipes and CI cannot read a wrapped failure as success. The Keda REPL that interprets Agent output stays at `kc repl` with its own `[agent_runner.repl]` section — the two entries are deliberately separate risk surfaces.

- `kc session --agent <name>` — override the default executor. The Agent must declare an `interactive` profile; otherwise the entry fails fast instead of silently falling back.
- If the installed operator Skill differs from the packaged version because a managed file was edited, `kc session` preserves it and stops before starting the provider. Preview a one-directory repair with `kc skill repair-operator --skill-dir <absolute-path> --dry-run`; add `--force` only after the user asks to replace that copy's managed files. Extra files in the Skill directory are preserved.
- `kc session` never authorizes you to start a dev server, install dependencies, or open a tunnel. The human is now talking to the provider directly; your role is the same operator Skill, nothing wider.

## On-demand project preview (`kc preview start|status|stop`)

The preview dev server starts **only when the user asked for it in the conversation**. KedaCode never guesses that intent and never starts one on entry.

- `kc preview start` — start the repository's dev server from `[agent_session.preview].argv` (an exact argv array such as `["npm", "run", "dev"]`, never shell text, so no `sh -c` path exists). With no configured argv it falls back to "the repository's single candidate plus explicit user confirmation"; pass `--confirm` in scripts only after the user confirmed.
- `kc preview status` — read-only: managed process state and its loopback URL.
- `kc preview stop` — stop the KedaCode-owned process group; never a foreign PID.

`[agent_session.preview].ready_url` must point at a loopback host (`localhost`, `127.0.0.1`, `::1`); a routable host is a **config-load error**, raised before any process spawns. A live preview already registered for the repository blocks a second `start` and names `kc preview stop` instead of double-spawning. Tunnels, external preview platforms, and "auto-start the dev server because the user might want it" are out of scope.

## Availability, guided selection, and the pre-run gate

A `config.toml` that names an agent says nothing about whether this machine has that
executable. Availability has exactly one source of truth — the registered `bin` resolved on
`PATH` — and every surface reads the same serialization, so CLI and Console never disagree.

- `kc agent list` (read-only) marks each agent `available (<resolved path>)` or
  `missing (not found in PATH)`; machine output carries `available` plus `bin_path` (`null`
  when missing). `kc agent presets` adds `agent_available` for the agent each preset binds —
  a valid preset can still point at a CLI this machine does not have.
- `kc agent setup` writes the **global layer** lifecycle matrix, offering only agents actually
  installed here, for the three stages that really invoke an agent (`implementation`,
  `verifier`, `review`). Scripted calls state the choice: `kc agent setup --agent claude --yes`,
  `kc agent setup --stage verifier --agent pi --yes`,
  `kc agent setup --stage review --preset plan --yes`. `--dry-run` is read-only (it takes no
  write lock), `--fix` removes global-layer bindings pointing at unavailable agents in one
  batch, and JSON output reports `unbind_stage_preset` — binding a bare agent first unbinds the
  stage's preset, because a preset otherwise masks the matrix forever.
- **Validation never reaches the file.** An unregistered name, an unknown stage, or a bad
  `kc agent add` block exits `USAGE(2)`; an agent that is registered but not installed here
  exits `NOT_FOUND(3)`; either way no write lock is taken and the config stays byte-for-byte.
  A non-TTY call with no explicit choice is refused as `USAGE(2)` rather than guessing an agent.
- **The pre-run gate blocks before any work.** `kc run` / the daemon check those three stages
  (presets included) *before* claiming an Issue or creating a worktree, and exit `NOT_FOUND(3)`
  naming the stage, preset, agent, and `bin`, with `kc agent setup` as the next command. The
  fallback chain deliberately does not rescue a missing primary — "this agent failed, try
  another" is not "this machine never had it". A temporary swap must be explicit (`--agent`).
- The first interactive use of `kc` may print a one-time notice on stderr pointing at
  `kc agent setup` / `kc agent list`, then write `~/.kedacode/.agent-setup-hint-shown`. It is
  suppressed for non-TTY, shell completion, machine output, `--help` / `--version`, and the
  `agent` / `completion` subcommands, and it never changes the current command's exit code.
- Custom agents come from `kc agent add <name> --bin <bin> [--arg … --prompt-delivery …
  --prompt-flag … --output-protocol …]`, which prints the TOML block by default and only
  persists it with `--write --yes`. A `bin` that is not installed yet does not block
  registration — it is a reminder on stderr (configuring the agent before installing its CLI
  is a legal order), and
  `kc agent list` confirms availability once the CLI is installed.

## Execute and never do

- Initialize: `kc init --dry-run`, then `kc init`.
- Refresh user-level Skills only (already-initialized repo, renamed packaged skill, or an explicit skill-refresh request): `kc skill install --dry-run`, then `kc skill install`. It never reads or writes `.kedacode.toml`, so it also works where `kc init` would stop at the existing config; it takes no repository (reject `--repo` / `--repo-id` / `--config` as a usage error).
- Repair one `kedacode-operator` copy without touching any other Agent root or remote Skill: `kc skill repair-operator --skill-dir <absolute-path> --dry-run`; add `--force` only for an explicitly requested overwrite of that copy's managed files. The path must name the exact `kedacode-operator` directory.
- Check what this machine can actually run, read-only: `kc agent list` (or `--json` for `available` / `bin_path`) — `missing (not found in PATH)` means the registration is fine and the executable is not there.
- Guide the stage choice by what is installed: `kc agent setup --dry-run [--json]` to preview, then `kc agent setup --agent <installed-name> [--stage <implementation|verifier|review>] --yes`. Pass a name the machine has; do not invent one, and do not pick a stage the user did not ask about.
- Clean up stale bindings, read-only first: `kc agent setup --fix --dry-run` then `kc agent setup --fix --yes` — it removes **global-layer** stage/preset bindings that point at agents this machine cannot run, and leaves available ones alone.
- Register a custom agent: `kc agent add <name> --bin <bin> [--arg <flag> …] [--prompt-delivery argv_tail|flag|stdin] [--prompt-flag <flag>] [--output-protocol <id>]` prints the TOML block; add `--write --yes` only when the user asked to persist it.
- Inspect model presets (agent + model + reasoning effort), read-only: `kc agent presets` — lists every defined `[agent_runner.presets.<name>]` entry.
- Preview a stage's or preset's exact command line, read-only: `kc agent doctor <agent> --preset <name> --json`; `kc agent doctor --lifecycle <key>` — the nine lifecycle keys are accepted; fix/closeout without an implementer context report `follows_implementation` instead of argv.
- The agent profile listings (`kc agent doctor <agent> --all-profiles`, and the profiles shown for each registered agent) cover five profiles: `run` / `deliberate` / `generate` / `repl` / `judge`. `judge` is the tool-disabled form used by model evaluation (empty cwd, no built-in tools); only `claude` / `codebuddy` / `qoder` / `pi` declare it, and an agent without it cannot be a judge.
- View the effective lifecycle matrix and executor fallback chain, read-only: `kc agent lifecycle list [--scope effective|global|repository] [--repo-id <id>] [--output json]` prints the nine stages with effective agent/model/effort, bound preset and per-field source; `kc agent fallback list [--scope …] [--repo-id <id>] [--output json]` prints the ordered fallback candidates with each candidate's preset and the `max_agent_switches` budget. `effective` resolves to a repository when the cwd or a selector uniquely matches one registered repo, otherwise global.
- Persist lifecycle settings, **always an explicit write scope**: `kc agent lifecycle set <stage> --preset <name> --scope global|repository [--repo-id <id>]` binds a stage to a named preset; `kc agent lifecycle unset <stage> --scope …` removes only that stage's binding in the target layer; `kc agent preset set <name> --agent <a> [--model <id>] [--reasoning-effort <v>] --scope …` upserts a preset triple (omitted flags mean "do not set that field"). Writes go to global `config.toml` or the named repository `.kedacode.toml`; repository writes require `--repo-id`/`--repo`, `--scope global` refuses those selectors (usage error, nothing written), preset names are trimmed before they are written, and a missing scope, unknown stage/preset/agent, or a preset whose agent mismatches fail before touching the file. An unset/delete that **cannot take effect in the target layer** — unbinding a stage only bound in the other layer, or deleting a preset the target file never declares — is a usage error (exit 2) naming the layer to edit, not a silent success: nothing is written, so the stage keeps the binding the user meant to remove. Multi-section saves (preset definitions plus stage bindings, candidate chain plus budget) land in a single read-modify-write, so a failed save leaves the file byte-for-byte unchanged; a target file that is not valid TOML is reported as a general error (exit 1) telling the user to repair it, never silently rewritten. Each write holds a per-file lock (`<config>.lock`) across "read baseline → validate → write", so two concurrent writers cannot drop each other's keys or replace the candidate array from a stale snapshot; a write that finds that lock still busy after the bounded wait exits 5 (conflict) with the file unchanged — re-run the command rather than assuming it partially applied.
- Edit executor fallback candidates: `kc agent fallback candidate add --agent <a> [--preset <p>] [--position <n>] --scope …`, `… preset set <position> --preset <p>` / `preset unset <position>`, `… remove <position>`, `… move <position> --to <n>` — candidates are ordered `(agent, preset)` pairs, the same agent may repeat with a different preset, an exact duplicate is rejected, and `max_agent_switches` counts candidate steps. A candidate's preset applies only when the chain actually reaches it. Write target follows `--scope`: `--scope global` writes the machine-level `config.toml` (the chain every repository inherits); `--scope repository --repo-id <id>` writes `[[agent_runner.runner.agent_fallback_candidates]]` into **that repository's** `.kedacode.toml`, where a non-empty array takes the chain over wholesale for that repo — the command materializes the currently effective chain (plus that repo's `max_agent_switches`) into the repo file, after which the repo no longer follows machine-level edits. Use `--scope global` unless the user explicitly wants a per-repository chain; the Console page's fallback section only ever writes the machine-level file.
- When any flag, default, or enum is unknown to you, introspect instead of guessing: `kc schema --json` is derived from the live command tree.
- Never run `kc init --force` to "fix" an installation problem — skill refresh has its own entry in `kc skill install`; treat `presets` / `doctor` / `schema` / `lifecycle list` / `fallback list` / `agent list` as read-only, and treat `lifecycle set/unset` / `preset set` / `fallback candidate …` / `agent setup` (without `--dry-run`) / `agent add --write` as persistent writes that need an explicit `--scope` (and `--repo-id` for repository writes) — never let a bare one-shot `--preset/--model/--reasoning-effort` be mistaken for a persistent command, and never write these without the user asking to persist it.

## Recovery

- A conflict report from `kc session`, `kc init`, or `kc skill install` means the modified Skill files were preserved — nothing was lost; use the one-directory repair command and act only on the user's explicit overwrite request.
- `kc init` refusing to run because `.kedacode.toml` already exists is not an installation failure: run `kc skill install` for the Skill side, and reserve `kc init --force` for a genuine config rebuild (it rewrites the whole file).
- A wrong repo id registered at init: re-run `kc init --dry-run` to see the current registration before deciding.
- `kc run` / the daemon exiting `NOT_FOUND(3)` with a stage, preset, agent, and `bin` named in the message is a **pre-work** refusal: nothing was claimed and no worktree was created. Install that CLI, or ask the user which installed agent to bind (`kc agent list`, then `kc agent setup`); `kc agent setup --fix` clears global-layer bindings this machine cannot honor. Do not paper over it by editing the fallback chain — the fallback ladder means "this agent failed", not "this machine never had it".
- `kc agent setup` refusing with `USAGE(2)` (unregistered name, unknown stage, no choice on a non-TTY pipe) or `NOT_FOUND(3)` (registered but not installed) wrote nothing; correct the choice and re-run, no cleanup needed.

## Related

Once the environment is set up, everyday operations start from the routing table in the main file — most commonly 建 issue (`${CODEBUDDY_SKILL_DIR}/references/create-issue.md`).
