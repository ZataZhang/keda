#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
repository_path=$(mktemp -d)
trap 'rm -rf "$repository_path"' EXIT
git -C "$repository_path" init -q
printf '[agent_runner.repository]\nid = "rv2"\nplatform = "gitlab"\n' > "$repository_path/.kedacode.toml"
printf 'real_entry: UV_CACHE_DIR=%s uv run kc --repo "%s" agent lifecycle list --scope repository\n' "$UV_CACHE_DIR" "$repository_path"
uv run kc --repo "$repository_path" agent lifecycle list --scope repository > "$repository_path/output.txt" 2>&1
repository_status=$?
cat "$repository_path/output.txt"
printf 'exit=%s\n' "$repository_status"
if [ "$repository_status" -ne 1 ]; then
  printf 'acceptance_assertion=FAIL_expected_exit_1\n'
  exit 1
fi
for expected_text in platform gitlab github gitea; do
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
