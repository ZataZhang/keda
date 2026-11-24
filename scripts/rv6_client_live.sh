#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
negative_output="/private/tmp/issue416-gitea/rv6-negative.log"
cd "$repository_root"

bash "$script_directory/gitea_up.sh"
source /private/tmp/issue416-gitea/runtime.env
export UV_CACHE_DIR=/private/tmp/issue416-uv-cache

printf 'NEGATIVE_CONTROL=Run the live integration test with an intentionally invalid API token.\n'
set +e
RV_GITEA_TOKEN=deliberately-invalid-token uv run --no-sync pytest -o addopts= tests/test_gitea_client_live.py -q > "$negative_output" 2>&1
negative_status=$?
set -e
if [[ "$negative_status" -eq 0 ]]; then
  printf 'ERROR=The invalid-token negative control unexpectedly passed.\n' >&2
  exit 1
fi
if ! rg -Fq '401' "$negative_output"; then
  printf 'ERROR=The negative control failed without the expected Gitea 401 response.\n' >&2
  exit 1
fi
printf 'EXPECTED_FAIL=The real Gitea API returned HTTP 401 for the invalid token; pytest exited %s.\n' "$negative_status"
printf 'GREEN_COMMAND=UV_CACHE_DIR=/private/tmp/issue416-uv-cache uv run --no-sync pytest -o addopts= tests/test_gitea_client_live.py -q\n'
source /private/tmp/issue416-gitea/runtime.env
uv run --no-sync pytest -o addopts= tests/test_gitea_client_live.py -q
