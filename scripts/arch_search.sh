#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../" && pwd)"
cd "$repository_root"

printf 'COMMIT=%s\n' "$(git rev-parse HEAD)"
printf 'COMMIT_TREE=%s\n' "$(git rev-parse 'HEAD^{tree}')"

printf 'GITEA_GH_SEARCH=rg -n "gh" src/backend/infrastructure/gitea_*.py\n'
if rg -n '"gh"' src/backend/infrastructure/gitea_*.py; then
  printf 'RESULT=FAIL Gitea adapter contains a gh command literal.\n' >&2
  exit 1
else
  search_status=$?
  [[ "$search_status" == "1" ]]
  printf 'RESULT=PASS no gh command literal in Gitea adapters.\n'
fi

printf 'PR_NUMBER_HELPER_SEARCH=rg -n '"'"'pulls\?'"'"' src/backend/core/shared/models/pull_request_url.py\n'
rg -n 'pulls\?' src/backend/core/shared/models/pull_request_url.py

printf 'DEPENDENCY_DIFF=git diff --quiet -- pyproject.toml uv.lock\n'
git diff --quiet -- pyproject.toml uv.lock
printf 'RESULT=PASS no dependency manifest changes.\n'

printf 'FILE_NONEMPTY_LINES=\n'
for python_file in \
  src/backend/infrastructure/gitea_client.py \
  src/backend/infrastructure/gitea_pr_ops.py; do
  nonempty_lines="$(awk 'NF {count++} END {print count+0}' "$python_file")"
  printf '%s=%s\n' "$python_file" "$nonempty_lines"
  [[ "$nonempty_lines" -le 1000 ]]
done

printf 'INTERFACE_DIFF=git diff --quiet -- src/backend/core/shared/interfaces/agent_runner.py\n'
git diff --quiet -- src/backend/core/shared/interfaces/agent_runner.py
printf 'RESULT=PASS IGitHubClient interface signatures unchanged.\n'

printf 'GITHUB_CLIENT_DIFF=git diff --quiet -- src/backend/infrastructure/github_*.py\n'
git diff --quiet -- src/backend/infrastructure/github_*.py
printf 'RESULT=PASS GitHub client files unchanged.\n'
