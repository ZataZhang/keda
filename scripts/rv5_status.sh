#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
cd "$repository_root"

printf 'NEGATIVE_CONTROL=The live test first asserts that a Gitea failure status is not SUCCESS; pytest.raises(AssertionError) requires this wrong expectation to fail.\n'
printf 'EXPECTED_FAIL=An incorrect SUCCESS expectation raises AssertionError; the test fails if that assertion unexpectedly passes.\n'
printf 'GREEN_COMMAND=bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/gitea_up.sh && source /private/tmp/issue416-gitea/runtime.env && UV_CACHE_DIR=/private/tmp/issue416-uv-cache uv run --no-sync pytest -o addopts= tests/test_gitea_client_live.py -k status -q\n'
bash "$script_directory/gitea_up.sh"
source /private/tmp/issue416-gitea/runtime.env
export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
uv run --no-sync pytest -o addopts= tests/test_gitea_client_live.py -k status -q
if [[ "${RV_CAPTURE_BROWSER:-0}" == "1" ]]; then
  evidence_directory="$repository_root/tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client"
  node "$script_directory/rv5_status_screenshot.mjs" \
    "$RV_GITEA_BASE_URL" "$evidence_directory/rv-5-gitea-status.png"
fi
