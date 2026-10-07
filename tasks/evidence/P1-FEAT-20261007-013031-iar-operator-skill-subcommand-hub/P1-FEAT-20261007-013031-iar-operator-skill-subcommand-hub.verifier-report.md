# Issue 234 independent evidence verifier

Verdict: PASS for executor delivery under the explicitly allowed rv-7 fallback and user-authorized presentation exception. This is an independent evidence audit, not implementation.

Audited clean committed HEAD `612493776dd7e74de5aa583968629b5992b8f529`, tree `ed18aa290429aaeaf7af0d85c942b9ac747aab29`, at 2026-10-07T18:51:40.723578+00:00. Repository remained read-only.

## Verified

- Final-resource-fingerprints.json: all eleven actual committed source/resource/test SHA256 values match. Nine final Skill resources match rv-2 snapshot metadata, rv-7 metadata, and actual installed files in the final v2 real-entry fixture. The source_head fields naming pre-commit 23f5abf6 do not purport to be final commit; per-file hashes independently rebind to this clean HEAD.
- rv-1: actual kc init/Typer entry, install orchestration and copytree; nine managed files exactly installed, clean dry-run asserts entire fixture file snapshot unchanged. Actual template clone preserved; only gh is a deny-all shim outside this PRD's GitHub scope.
- rv-2: actual unchanged test resource reader/CLI command tree, full 24 tests clean exit 0, injected references/create-issue.md --definitely-not-a-real-flag produces exit 1 explicitly identifying file/flag; restored run exit 0. testmon disabled. Final resource fingerprints match.
- rv-3 positive matrix: actual init up-to-date baseline, additional user note remains up-to-date, changed managed reference preserve-conflict with exact user bytes, conflict dry-run unchanged fixture snapshot, force restores all nine files. Source helper correctly enumerates only source SKILL.md + references/**/*.md and ignores destination extras; old cleanup enumeration unchanged.
- rv-4/5: 24 targeted tests actually executed after final text fixes; final hub 57 nonempty lines, eight routes/resources and trigger words, target/read-only/introspection/exit-code baselines. Nine-file fulltext reading view exactly contains actual final files, and independent code/document review hashes match.
- rv-6: independently compared actual stdout excluding command header and separately captured stderr. --help is byte-identical (7402 bytes); schema --json byte-identical (103591 bytes) against actual clean rename prerequisite 53497764.
- Gates: full execution log shows testmon disabled, 3446 collected, 3445 passed / 1 skipped in 173.44 s and full lint passed; reuse hooks all pass; strict MkDocs built successfully with existing informational anchor/navigation notices. Final subsequent changes were resource text only and targeted/resource/init probes rerun; no need repeat full Python suite.
- rv-7: actual fresh-HOME Claude initialization skill inventory includes kedacode-operator, and installed description covers all eight triggers. Actual headless process reports Not logged in; route/precheck execution and read-only agent behavior are NOT proven. PRD explicitly permits this INCONCLUSIVE fallback, with fulltext/independent static review. Do not label behavioral rv-7 PASS.
- Documentation changed in the expected two pages; no new page/navigation requirement or CLI surface change.

## Completed missing oracle and final reconciliation

Re-audit at 2026-10-07T18:55:39.652126+00:00: external issue-234-extra-negative has completed actual kc init replay. Read the executable probe, exact snapshot alteration diffs, raw stdout/stderr, result metadata and before/after bytes.

- Fresh fixture with original snapshot code: changed protected create-issue.md yields preserve-conflict and identical user bytes.
- rv-3 negative: only the disposable snapshot's managed iteration narrowed to (SKILL.md,); actual CLI reports operator skill already up to date despite changed reference. Thus the required oracle fails under the intended defect and has discrimination.
- rv-9 negative: only disposable snapshot's default preserve-conflict action changed to overwrite; no --force supplied, actual CLI overwrites protected user file. After bytes exactly equal final shipped create-issue.md, proving the negative control breaches the intended protection.
- Snapshot original code restored, bytecode caches removed, fresh fixture rerun: actual CLI restores preserve-conflict and exact user bytes. Independently verified restored snapshot installer bytes equal final committed installer SHA256 74dcfc109406273d6accaa8cd8c4b9bc204ae644b225bb123414c46f1aa95841. Product worktree remains clean exact HEAD612493776dd7e74de5aa583968629b5992b8f529.

No unresolved executor implementation or oracle blocker remains. PR publication, truthful PRD reconciliation/archival and incorporating this verifier report are subsequent delivery actions. Text-only closeout changes do not require repeating unaffected product checks; any product/resource change invalidates affected fingerprints and probes.

## Authorized exceptions and pending human review

User permits code-based merge and waives evidence presentation ceremony. That exception removes PR comment/local HTML format blocking, not behavioral proof; the previously missing rv-3 negative proof has now been independently audited. Keep four Human-Confirmed boxes unchecked and archived status pending human review. Agent authentication failure remains rv-7 INCONCLUSIVE under the explicit PRD fallback, not product behavior PASS.

PASS
