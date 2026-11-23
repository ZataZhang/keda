# Verification Plan

## Target

Implement the complete Gitea 1.21 platform client behind the existing repository platform dispatch, preserve the GitHub default path, and keep credential routing controlled by machine-local configuration.

## Execution Order

| Oracle | Real entry point | Negative control | Green evidence | Status |
|---|---|---|---|---|
| rv-1 | `RV_CAPTURE_BROWSER=0 bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv1_full_loop.sh` | Empty Gitea commit status must leave the real PR unmerged and Issue open | `rv-1-gitea-full-loop.txt` | API flow passed; required UI screenshots and video blocked by renderer startup permissions |
| rv-2 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv2_unregistered_host.sh` | Register the listener host and confirm it receives a request | `rv-2-unregistered-host.txt` | Passed |
| rv-3 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv3_missing_token.sh` | Set the registered token variable and confirm a request reaches the listener | `rv-3-missing-token.txt` | Passed |
| rv-4 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv4_url_policy.sh` | Non-local HTTP and repository-local registry declarations must be rejected | `rv-4-url-policy.txt` | Passed |
| rv-5 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv5_status.sh` | Deliberately expect a failure status to be SUCCESS and require an assertion failure | `rv-5-status.txt` plus the screenshot required by the PRD marker | Real Gitea 1.21 status test passed; required screenshot blocked by renderer startup |
| rv-6 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv6_client_live.sh` | Run the real integration test with an invalid API token and require HTTP 401 | `rv-6-client-live.txt` | Passed against Gitea 1.21 |
| rv-7 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv7_pytest.sh` | Assert that the new Gitea `/pulls/` URL is unsupported; the stale expectation must fail | `rv-7-pytest.txt` | Passed with testmon disabled: 5660 passed, 3 skipped in 427.80s; local replay timeout is 540s |
| rv-8 | `bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv8_docs.sh` | Search the baseline configuration guide for the new Gitea registry entry | `rv-8-docs.txt` | Passed |

## Repository Gates

- `UV_CACHE_DIR=/private/tmp/issue416-uv-cache just lint --reuse`
- `UV_CACHE_DIR=/private/tmp/issue416-uv-cache SKIP=check-test-flag just lint --full`
- `uv run --no-sync mkdocs build --strict` (also captured by rv-8)

Do not record an RV checkbox as complete until its named output and any required presentation artifacts exist. `rv-1` stays open until both real Gitea screenshots and the demo are captured; `rv-5` stays open until its screenshot-format marker is satisfied. The runner owns independent verification and PRD archiving.
