#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
repository_path=$(mktemp -d)
trap 'rm -rf "$repository_path"' EXIT
git -C "$repository_path" init -q
printf '[agent_runner.repository]\nid = "rv4"\nplatform = "gitea"\n' > "$repository_path/.kedacode.toml"
printf 'negative control real entry: UV_CACHE_DIR=%s uv run kc --repo "%s" issue list\n' "$UV_CACHE_DIR" "$repository_path"
uv run kc --repo "$repository_path" issue list > "$repository_path/issue-output.txt" 2>&1
issue_status=$?
cat "$repository_path/issue-output.txt"
printf 'negative_control_exit=%s\n' "$issue_status"
if [ "$issue_status" -ne 1 ] || ! grep -qi 'not implemented' "$repository_path/issue-output.txt"; then
  printf 'negative_control_assertion=FAIL_expected_platform_access_to_stop\n'
  exit 1
fi
printf 'negative_control_assertion=EXPECTED_FAIL_platform_access_not_local_read\n'
printf 'real_entry: UV_CACHE_DIR=%s uv run kc --repo "%s" agent lifecycle list --scope repository\n' "$UV_CACHE_DIR" "$repository_path"
uv run kc --repo "$repository_path" agent lifecycle list --scope repository > "$repository_path/lifecycle-output.txt" 2>&1
lifecycle_status=$?
cat "$repository_path/lifecycle-output.txt"
printf 'exit=%s\n' "$lifecycle_status"
if [ "$lifecycle_status" -ne 0 ] || ! grep -q 'scope=repository' "$repository_path/lifecycle-output.txt"; then
  printf 'acceptance_assertion=FAIL_local_command_should_succeed\n'
  exit 1
fi
printf 'acceptance_assertion=PASS\n'
