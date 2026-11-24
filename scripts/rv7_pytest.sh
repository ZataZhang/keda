#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
cd "$repository_root"

tree_hash="$(git rev-parse 'HEAD^{tree}')"
printf 'TREE_HASH=%s\n' "$tree_hash"
printf 'NEGATIVE_CONTROL=The inverted expectation that a Gitea /pulls/ URL is unsupported must fail against the real parser.\n'
set +e
negative_output="$(UV_CACHE_DIR=/private/tmp/issue416-uv-cache uv run --project "$repository_root" --no-sync python -c 'from backend.core.shared.models.pull_request_url import parse_pull_request_number; assert parse_pull_request_number("https://git.example.com/o/r/pulls/7") is None' 2>&1)"
negative_status=$?
set -e
printf '%s\n' "$negative_output"
[[ "$negative_status" == "1" ]]
[[ "$negative_output" == *"AssertionError"* ]]
printf 'EXPECTED_FAIL=the obsolete behavior assertion failed with exit_code=%s\n' "$negative_status"

export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
printf 'TARGETED_COMMAND=uv run --no-sync pytest -o addopts= tests/test_pull_request_url.py tests/test_gitea_instance_settings.py -q\n'
uv run --project "$repository_root" --no-sync pytest \
  -o addopts= \
  tests/test_pull_request_url.py \
  tests/test_gitea_instance_settings.py \
  -q
printf 'FULL_COMMAND=uv run --no-sync python tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv7_parallel.py\n'
uv run --project "$repository_root" --no-sync python \
  tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv7_parallel.py
