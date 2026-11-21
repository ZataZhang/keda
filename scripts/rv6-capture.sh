#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
BASELINE_COMMIT=1ad17462d893edfafaf6b41f7a420bab02ed0140
negative_output_path=/private/tmp/keda-issue-398-rv6-negative.txt
printf "negative control: git show %s:docs/guides/configuration.md | rg -n 'platform.*gitea|gitea.*platform'\n" "$BASELINE_COMMIT"
git show "$BASELINE_COMMIT:docs/guides/configuration.md" | rg -n 'platform.*gitea|gitea.*platform' > "$negative_output_path" 2>&1
negative_status=$?
cat "$negative_output_path"
printf 'negative_control_exit=%s\n' "$negative_status"
if [ "$negative_status" -eq 0 ]; then
  printf 'negative_control_assertion=FAIL_baseline_docs_already_contain_contract\n'
  exit 1
fi
printf 'negative_control_assertion=EXPECTED_FAIL_base_configuration_guide_lacks_platform_contract\n'
printf 'real_entry: UV_CACHE_DIR=%s uv run mkdocs build --strict\n' "$UV_CACHE_DIR"
uv run mkdocs build --strict > /private/tmp/keda-issue-398-rv6-build.txt 2>&1
build_status=$?
cat /private/tmp/keda-issue-398-rv6-build.txt
printf 'build_exit=%s\n' "$build_status"
if [ "$build_status" -ne 0 ]; then
  printf 'acceptance_assertion=FAIL_strict_docs_build\n'
  exit 1
fi
printf 'documentation search: rg -n "platform" docs/guides/configuration.md\n'
rg -n 'platform' docs/guides/configuration.md
if ! rg -qi 'platform.*gitea|gitea.*platform' docs/guides/configuration.md; then
  printf 'acceptance_assertion=FAIL_platform_and_gitea_not_documented\n'
  exit 1
fi
printf 'cross_reference search: rg -n "platform" docs/guides/agent-runner.md\n'
rg -n 'platform' docs/guides/agent-runner.md
if ! rg -qi 'github_repo.*platform|platform.*github_repo' docs/guides/agent-runner.md; then
  printf 'acceptance_assertion=FAIL_cross_reference_missing\n'
  exit 1
fi
printf 'acceptance_assertion=PASS\n'
