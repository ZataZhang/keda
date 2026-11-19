# Verification Plan

PRD: `tasks/archive/P2-FEAT-20261113-140659-repositories-lifecycle-settings-entry.md`

| Acceptance / oracle | Verification command or observation | Evidence |
|---|---|---|
| rv-1 — Repositories tabs and preserved lifecycle settings | `just e2e tests/workflows/lifecycle-settings.spec.ts` through the real logged-in console; capture a separate primary demo and screenshot from `/app/repositories/lifecycle/` | Evidence report §Browser validation; `rv-1-primary-demo.mp4` and `rv-1-implementation.png` on `prd-evidence` |
| rv-2 — Backlog deep link and old URL preserve repository selection | Same real-entry Playwright spec; click the visible Backlog gear and open the legacy URL with the same enabled `repo_id`; run the negative control with the gear `repo_id` removed | Evidence report §Browser validation / §Negative control; `rv-2-repository-scope.png` and `rv-1-negative-control.txt` |
| rv-3 — Settings retains Agent tags and removes old entry; docs use canonical route | Same real-entry Playwright spec; `rg` route audit; `uv run mkdocs build --strict` | Evidence report §Browser validation / §Build, tests, and lint |
| Frontend static contract | `pnpm --dir frontend-public typecheck && pnpm --dir frontend-public build` | Evidence report §Build, tests, and lint |
| Core local suite | `just test` and inspect pytest summary to confirm a nonzero executed test count | Evidence report §Build, tests, and lint |
| Repository lint and reuse | `just lint --repo` and `just lint --reuse` | Evidence report §Build, tests, and lint |
| Final patch hygiene | `git diff --check`; inspect staged paths and ensure raw artifacts / scripts are not committed | Evidence report §Build, tests, and lint; PR file list |
| Independent review and delivery | Independent verifier on committed clean worktree; required GitHub checks on the exact PR head | `P2-FEAT-20261113-140659-repositories-lifecycle-settings-entry.verifier-report.md`; PR checks and evidence comment |

The E2E spec must report a nonzero executed test count. Screenshots and the primary demo are captured only after implementation, from the real authenticated `frontend-public` route and local backend; no model provider is called. The concept PNG is design intent, never runtime evidence. Public raw media and its manifest are retained on the `prd-evidence` branch; the code PR contains only Markdown reports.
