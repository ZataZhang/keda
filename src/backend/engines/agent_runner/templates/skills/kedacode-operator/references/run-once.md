# 跑一次 — execute one pass (single run, not a daemon)

## When to use

The user says 「跑一下」「跑一次」「执行这个 Issue」「把 197 跑起来」「现在就做这个」, or translates the CLI directly: `kc run`. Unattended repeated processing is 后台跑, not this route; viewing what a run is doing is 看进度.

## Confirm before acting

1. **The user asked for execution, not a preview.** If the wording is 「看看会跑哪些」「先预览」, answer with a dry-run and stop — `--dry-run` writes nothing and must not be presented as having run work.
2. **A target is named.** Which Issue (or the ready queue) the user means decides the invocation; running a different Issue than the one named is a wrong side effect nobody asked for.
3. **Side effects are explained before running: this writes GitHub state, runs Agents, creates branches/worktrees, and can open or update PRs.** For a queue run (`--all-ready`) confirm the user really wants the whole ready queue advanced, not just one Issue.

## Choose NORMAL, FAST, or DIRECT before acting

| Tier | Runner stages bypassed | Stages and duties retained |
|---|---|---|
| NORMAL | None | Review Agent, repository verification commands, RV re-execution, independent verifier, and configured post-PR supervisor. |
| FAST (`--fast-merge`) | RV re-execution and independent verifier. | Review Agent, repository verification commands, configured post-PR supervisor, clean committed publication tree, and truthful unverified PR notice. Single target only; stack dependencies are refused. |
| DIRECT (single-target `--direct-pr` or explicit Issue label) | RV re-execution, independent verifier, pre-PR review Agent, runner-side repository verification commands, and the publication chain's inline post-PR supervisor. | Builder/publication prerequisites, claim/dependency rules, no-PRD/readable-body admission, truthful DIRECT notice, PR CI, and the operator's necessary validation before merge. No automatic merge is enabled. |

- **Background review remains separate.** DIRECT skips the supervisor call inside the publication chain, not an independently running review-daemon/review pass. DIRECT finishes its handoff in `agent/review`, including cleanup-only recovery, regardless of inline supervisor configuration. The round checkpoint is completed only after this transition succeeds. A background reviewer may still select the Issue under its normal selection rules; the DIRECT marker is not a reviewer-daemon exclusion rule.
- **Assess the actual change before selecting a bypass.** Use NORMAL for changes to public constructors or call contracts, cross-layer contracts, schema/migrations, auth/security boundaries, or unclear scope. Few files, a plausible reason, or a green unrelated test do not make such work low risk. Do not label an Issue DIRECT merely to get a stalled run through a failing gate.
- **Final-tree validation remains mandatory for FAST and DIRECT.** Run the relevant existing checks and the highest feasible real entry for the affected behavior; after code changes, rerun affected validation against the resulting Git tree. Never fabricate PASS, weaken assertions, skip necessary checks, reuse expired evidence, or present injected/component previews as a real user flow. Failed checks or evidence too weak to prove the intended behavior require NORMAL or a disclosed blocker, not a bypass around the deficiency. A PR marker declares what was bypassed; it never proves correctness.
- **Positive examples:** pure comments/docstrings with no executable change can use a bypass after checking the diff for accidental behavior changes and running applicable existing documentation/lint checks. A local text correction may qualify after observing the corrected text through its real production entry (for example the actual `kc <subcommand> --help`, or the production page with its normal layout and providers); a scratch preview or hardcoded injected state cannot substitute for that entry. These are judgments about scope and evidence, never an automatic file-count rule.
- **Authorization continues from the session.** Confirm the target, tier and side effects against existing explicit authorization under the hub rule; setting the label is a GitHub write, not a new approval service. No reason parameter, signature, authorization subcommand, FAST label or extra confirmation round is required.

## Carry DIRECT across machines and recover its handoff

Checkpoint reads trust the authenticated author or a fresh GitHub repository permission check for another author who can manage Issues. Public comments cannot grant DIRECT by imitating the checkpoint marker. Read/permission errors block the pass rather than masquerading as an absent round; unrelated comments do not require permission lookups. Prior-PR absence and same-round association require a successful full-context query; failed, empty or malformed responses are unknown, and only a successful empty list means no open PR.

1. Upgrade **every possible claimant** before relying on this protocol. Old runners may ignore the label and execute NORMAL; installing a newer Skill alone does not upgrade an already-running runner process. Sync the configured label with `kc labels sync`, then, when already authorized, mark the named Issue with `gh issue edit <N> --add-label direct-pr` (substitute the configured label name).
2. The label is an explicit Issue-level choice, not a daemon/global switch. It does not enqueue an Issue, grant a claim, alter priority, or bypass dependencies. The claim winner fresh-reads labels/body before starting affected stages: NORMAL + label becomes DIRECT; DIRECT + label stays DIRECT; FAST + label is refused. PRD anchors and unreadable bodies remain fail-closed. A dry-run only previews and never claims or consumes a label.
3. Once admitted, the selected tier is fixed for that execution round, including Agent fallback and recovery. Adding/removing the label while execution is in flight does not switch its current stages. Failed execution or failed publication preserves the label for retry.
4. Consumption requires the current **unfinished publication round** association as well as matching repository, Issue, branch, PR head, and DIRECT marker. A historical PR, even at the same branch/head, does not authorize consuming a new label. The runner records the candidate and absence of an old PR before creating; an unrelated old publication is reported rather than silently adopted.
5. Publication, label deletion and workflow handoff are separate writes. Once the round's PR is confirmed, failure to delete the label or finish handoff reports its URL as cleanup pending, not complete success. The next recovery confirms the unfinished round and only completes cleanup/handoff—no Agent rebuild or duplicate PR, including a crash after label deletion. If the Issue's current body gained a PRD anchor or changed, cleanup-only recovery may finish the already-published round; that exemption cannot start a new DIRECT build.
6. Wait for label consumption **and** workflow handoff to finish before marking a new round DIRECT. Concurrently re-adding the same label inside this cleanup window is unsupported: GitHub labels have no version, so it cannot express a distinguishable new choice. External manual creation of an identical same-branch/head/marker PR inside the recorded create window also lacks an atomic attribution guarantee; coordinate such manual publication with the active claimant.

## Execute and never do

- Preview the next pass: `kc run --all-ready --dry-run --max-issues 3` (or `kc run --issue <N> --dry-run` for one Issue) — read-only; shows the selected ready Issues and their priority; does not run an Agent or claim work.
- Execute one pass: `kc run --issue <N> --max-issues 1` for one targeted Issue; `kc run --all-ready --max-issues 1` for the whole ready queue. Runs configured Agents and may update GitHub, create branches/worktrees, and open or update PRs.
- Drive several Issues at once: one `kc run --issue <N>` invocation per Issue, each with its own target; never combine this with `--all-ready`, which claims the same ready queue twice.
- **Aggregate one explicit batch**: `kc run --all-ready --aggregate-pr --max-issues 4` opts into one total Draft PR for this pass only. The queue must select at least two Issues in one repository; the option is rejected with `--issue`, a PRD path, multi-repository mode, `--direct-pr` or `--fast-merge`. A dry-run previews batch size and known PRD paths. Every selected task must pass its existing flow before the runner integrates source heads and verifies the combined tree.
- **Aggregate or retry completed source PRs**: use `kc pr aggregate --issue 101 --issue 102` when tasks finished in separate runs or when only integration / publication / closeout needs retry. Add `--dry-run` to inspect eligibility and the unique PRD set without writing branches or PRs. It accepts only same-repository Issues with one eligible source PR each. The total PR stays Draft. Source PRs close only after total PR required checks succeed. Pending/failed checks leave sources open; after a partial close, completed sources retain their superseded marker and unclosed sources stay open. The command reports each closeout state and a retry command. No aggregate action deletes source branches or auto-merges the total PR.
- Bypass gates only when the user explicitly asked for it: `kc run --issue <N> --fast-merge` publishes the Draft PR unverified (single target only); `kc run --issue <N> --direct-pr` is for an Issue **without a `PRD path:` anchor** and additionally skips the review Agent. Neither flag is a daemon flag, neither arms auto-merge, and both are usage errors with `--all-ready`. To make the direct choice outlive this invocation, put the `direct-pr` label on the Issue instead — any upgraded claimer (another machine, a batch pass, the daemon) then publishes that Issue on the direct track, and the winner consumes the label once the Draft PR is confirmed; `--fast-merge` on such an Issue is a usage error, and a PRD-anchored Issue is refused outright.
- Never add autopilot-style flags to `run` — check unknown flags against `kc schema --json` before inventing any invocation.

## Recovery

- Exit `5` naming a live claim holder: the work is already running — view it via 看进度 instead of re-running.
- The pass built but failed to publish (push/PR/state update): `kc recover --issue <N>` resumes publication without redoing the work. For an unfinished labelled DIRECT round with its PR already confirmed, it only completes that round's label/workflow handoff; do not re-queue a new build.
- The working directory matched several registered repositories and the command refused: re-run with `--repo-id <id>`.

## Related

After a run opens a PR, CI observation lives on the CI 交付 route (`${CODEBUDDY_SKILL_DIR}/references/ci.md`).
