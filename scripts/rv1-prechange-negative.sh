#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
repo_path=$(mktemp -d)
git -C "$repo_path" init -q
printf '[agent_runner.repository]\nid = "rv1"\nplatform = "gitea"\n' > "$repo_path/.kedacode.toml"
printf 'negative control command: uv run kc --repo "%s" issue list\n' "$repo_path"
uv run kc --repo "$repo_path" issue list > "$repo_path/output.txt" 2>&1
command_status=$?
cat "$repo_path/output.txt"
printf 'exit=%s\n' "$command_status"
if grep -qi 'not implemented' "$repo_path/output.txt"; then
  printf 'negative_control=failed_to_turn_red\n'
  exit 1
fi
printf 'negative_control=expected_red_missing_not_implemented\n'
