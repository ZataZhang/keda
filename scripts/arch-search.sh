#!/usr/bin/env bash
set -euo pipefail
printf 'rg -n "GitHubCliClient\\(" src/backend\n'
rg -n 'GitHubCliClient\(' src/backend
printf 'rg -n "\\"gitea\\"" src/backend\n'
rg -n '"gitea"' src/backend
baseline_calls=$(git grep -n 'create_github_client(' HEAD -- src/backend | wc -l | tr -d ' ')
working_calls=$(rg -n 'create_github_client\(' src/backend | wc -l | tr -d ' ')
printf 'create_github_client baseline=%s worktree=%s\n' "$baseline_calls" "$working_calls"
if [[ "$baseline_calls" != "$working_calls" ]]; then
  exit 1
fi
interface_diff=$(git diff --stat -- src/backend/core/shared/interfaces/agent_runner.py)
if [[ -z "$interface_diff" ]]; then
  printf 'IGitHubClient diff: empty\n'
else
  printf '%s\n' "$interface_diff"
  exit 1
fi
