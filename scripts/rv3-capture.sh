#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
unset_repository_path=$(mktemp -d)
github_repository_path=$(mktemp -d)
gitea_control_path=$(mktemp -d)
trap 'rm -rf "$unset_repository_path" "$github_repository_path" "$gitea_control_path"' EXIT
for repository_path in "$unset_repository_path" "$github_repository_path" "$gitea_control_path"; do
  git -C "$repository_path" init -q
done
printf '[agent_runner.repository]\nid = "rv3"\n' > "$unset_repository_path/.kedacode.toml"
printf '[agent_runner.repository]\nid = "rv3"\nplatform = "github"\n' > "$github_repository_path/.kedacode.toml"
printf '[agent_runner.repository]\nid = "rv3-control"\nplatform = "gitea"\n' > "$gitea_control_path/.kedacode.toml"
printf 'negative control real entry: UV_CACHE_DIR=%s uv run kc --repo "%s" issue list\n' "$UV_CACHE_DIR" "$gitea_control_path"
uv run kc --repo "$gitea_control_path" issue list > "$gitea_control_path/output.txt" 2>&1
gitea_control_status=$?
cat "$gitea_control_path/output.txt"
printf 'negative_control_exit=%s\n' "$gitea_control_status"
if [ "$gitea_control_status" -ne 1 ] || ! grep -qi 'not implemented' "$gitea_control_path/output.txt"; then
  printf 'negative_control_assertion=FAIL_expected_platform_dispatch\n'
  exit 1
fi
printf 'negative_control_assertion=EXPECTED_FAIL_no_platform_hint_rule_for_github_does_not_apply_to_gitea\n'
printf 'real_entry_a: UV_CACHE_DIR=%s uv run kc --repo "%s" issue list\n' "$UV_CACHE_DIR" "$unset_repository_path"
uv run kc --repo "$unset_repository_path" issue list > "$unset_repository_path/output.txt" 2>&1
unset_status=$?
cat "$unset_repository_path/output.txt"
printf 'exit_a=%s\n' "$unset_status"
printf 'real_entry_b: UV_CACHE_DIR=%s uv run kc --repo "%s" issue list\n' "$UV_CACHE_DIR" "$github_repository_path"
uv run kc --repo "$github_repository_path" issue list > "$github_repository_path/output.txt" 2>&1
github_status=$?
cat "$github_repository_path/output.txt"
printf 'exit_b=%s\n' "$github_status"
if [ "$unset_status" -ne "$github_status" ]; then
  printf 'acceptance_assertion=FAIL_exit_codes_differ\n'
  exit 1
fi
if grep -Eqi 'not implemented|Invalid KedaCode local config' "$unset_repository_path/output.txt" "$github_repository_path/output.txt"; then
  printf 'acceptance_assertion=FAIL_platform_or_config_message_present\n'
  exit 1
fi
printf 'platform_or_config_message_count=0\nacceptance_assertion=PASS\n'
