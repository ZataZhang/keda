#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
negative_repo_path=$(mktemp -d)
repository_path=$(mktemp -d)
trap 'rm -rf "$negative_repo_path" "$repository_path"' EXIT
git -C "$negative_repo_path" init -q
git -C "$repository_path" init -q
printf '[agent_runner.repository]\nid = "rv1-control"\nplatform = "github"\n' > "$negative_repo_path/.kedacode.toml"
printf '[agent_runner.repository]\nid = "rv1"\nplatform = "gitea"\n' > "$repository_path/.kedacode.toml"
printf 'negative control: UV_CACHE_DIR=%s uv run kc --repo "%s" issue list\n' "$UV_CACHE_DIR" "$negative_repo_path"
uv run kc --repo "$negative_repo_path" issue list > "$negative_repo_path/output.txt" 2>&1
negative_status=$?
cat "$negative_repo_path/output.txt"
printf 'negative_control_exit=%s\n' "$negative_status"
if grep -qi 'not implemented' "$negative_repo_path/output.txt"; then
  printf 'negative_control_assertion=unexpected_match\n'
  exit 1
fi
printf 'negative_control_assertion=EXPECTED_FAIL_missing_gitea_message\n'
printf 'real_entry: UV_CACHE_DIR=%s uv run kc --repo "%s" issue list\n' "$UV_CACHE_DIR" "$repository_path"
uv run kc --repo "$repository_path" issue list > "$repository_path/output.txt" 2>&1
repository_status=$?
cat "$repository_path/output.txt"
printf 'exit=%s\n' "$repository_status"
if [ "$repository_status" -ne 1 ]; then
  printf 'acceptance_assertion=FAIL_expected_exit_1\n'
  exit 1
fi
for expected_text in gitea platform 'not implemented' github '.kedacode.toml'; do
  if ! grep -qi "$expected_text" "$repository_path/output.txt"; then
    printf 'acceptance_assertion=FAIL_missing_%s\n' "$expected_text"
    exit 1
  fi
done
if grep -qi 'Traceback' "$repository_path/output.txt"; then
  printf 'acceptance_assertion=FAIL_traceback_present\n'
  exit 1
fi
printf 'acceptance_assertion=PASS\n'
