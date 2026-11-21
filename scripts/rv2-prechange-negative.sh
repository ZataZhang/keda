#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
repo_path=$(mktemp -d)
git -C "$repo_path" init -q
printf '[agent_runner.repository]\nid = "rv2"\nplatform = "gitlab"\n' > "$repo_path/.kedacode.toml"
printf 'negative control command: uv run kc --repo "%s" agent lifecycle list --scope repository\n' "$repo_path"
uv run kc --repo "$repo_path" agent lifecycle list --scope repository > "$repo_path/output.txt" 2>&1
command_status=$?
cat "$repo_path/output.txt"
printf 'exit=%s\n' "$command_status"
if [ "$command_status" -ne 0 ] && grep -qi 'platform' "$repo_path/output.txt"; then
  printf 'negative_control=failed_to_turn_red\n'
  exit 1
fi
printf 'negative_control=expected_red_unknown_value_was_accepted\n'
