#!/usr/bin/env bash
set -u
export UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache
BASELINE_COMMIT=1ad17462d893edfafaf6b41f7a420bab02ed0140
export BASELINE_COMMIT
negative_output_path=/private/tmp/keda-issue-398-rv5-negative.txt
printf 'negative control: UV_CACHE_DIR=%s uv run python -c "from backend.core.shared.models.repository_platform import validate_repository_platform; assert validate_repository_platform(\\"gitlab\\") == \\"gitlab\\""\n' "$UV_CACHE_DIR"
uv run python -c 'from backend.core.shared.models.repository_platform import validate_repository_platform; assert validate_repository_platform("gitlab") == "gitlab"' > "$negative_output_path" 2>&1
negative_status=$?
cat "$negative_output_path"
printf 'negative_control_exit=%s\n' "$negative_status"
if [ "$negative_status" -eq 0 ]; then
  printf 'negative_control_assertion=FAIL_invalid_platform_was_accepted\n'
  exit 1
fi
if ! grep -q "Invalid repository.platform value 'gitlab'" "$negative_output_path"; then
  printf 'negative_control_assertion=FAIL_unexpected_error\n'
  exit 1
fi
printf 'negative_control_assertion=EXPECTED_FAIL_invalid_platform_is_rejected\n'
printf 'real_entry: UV_CACHE_DIR=%s uv run pytest tests/test_agent_runner_config.py tests/test_repository_platform_dispatch.py -q --testmon-noselect\n' "$UV_CACHE_DIR"
uv run pytest tests/test_agent_runner_config.py tests/test_repository_platform_dispatch.py -q --testmon-noselect > /private/tmp/keda-issue-398-rv5-targeted.txt 2>&1
targeted_status=$?
cat /private/tmp/keda-issue-398-rv5-targeted.txt
printf 'targeted_exit=%s\n' "$targeted_status"
if [ "$targeted_status" -ne 0 ]; then
  printf 'acceptance_assertion=FAIL_targeted_tests\n'
  exit 1
fi
printf 'real_entry: CI=1 PYTEST_ADDOPTS=--testmon-noselect JUST_LOCAL_TEST_TARGET=tests/test_agent_runner_config.py JUST_TEST_TIMEOUT_SECONDS=240 UV_CACHE_DIR=%s just test\n' "$UV_CACHE_DIR"
CI=1 PYTEST_ADDOPTS=--testmon-noselect JUST_LOCAL_TEST_TARGET=tests/test_agent_runner_config.py JUST_TEST_TIMEOUT_SECONDS=240 just test > /private/tmp/keda-issue-398-rv5-just-test.txt 2>&1
just_test_status=$?
cat /private/tmp/keda-issue-398-rv5-just-test.txt
printf 'just_test_exit=%s\n' "$just_test_status"
if [ "$just_test_status" -ne 0 ]; then
  printf 'acceptance_assertion=FAIL_just_test\n'
  exit 1
fi
if grep -qi 'no tests ran' /private/tmp/keda-issue-398-rv5-just-test.txt; then
  printf 'acceptance_assertion=FAIL_just_test_selected_no_tests\n'
  exit 1
fi
if ! grep -qi ' passed' /private/tmp/keda-issue-398-rv5-just-test.txt; then
  printf 'acceptance_assertion=FAIL_just_test_missing_pass_summary\n'
  exit 1
fi
if grep -Eqi '[0-9]+ failed' /private/tmp/keda-issue-398-rv5-just-test.txt; then
  printf 'acceptance_assertion=FAIL_just_test_failures_reported\n'
  exit 1
fi
printf 'just_test_assertion=PASS\n'
printf 'architecture search: rg -n "GitHubCliClient\\(" src/backend\n'
if ! client_constructor_matches=$(rg -n 'GitHubCliClient\(' src/backend); then
  printf 'acceptance_assertion=FAIL_no_client_constructor_match\n'
  exit 1
fi
printf '%s\n' "$client_constructor_matches"
client_constructor_count=$(printf '%s\n' "$client_constructor_matches" | wc -l | tr -d ' ')
if [ "$client_constructor_count" -ne 1 ] || [[ "$client_constructor_matches" != src/backend/engines/agent_runner/factories/__init__.py:* ]]; then
  printf 'acceptance_assertion=FAIL_client_constructor_not_unique_to_factory\n'
  exit 1
fi
printf 'architecture search: rg -n "\\"gitea\\"" src/backend\n'
if ! platform_literal_matches=$(rg -n '"gitea"' src/backend); then
  printf 'acceptance_assertion=FAIL_no_gitea_literal_match\n'
  exit 1
fi
printf '%s\n' "$platform_literal_matches"
platform_literal_count=$(printf '%s\n' "$platform_literal_matches" | wc -l | tr -d ' ')
if [ "$platform_literal_count" -ne 1 ] || [[ "$platform_literal_matches" != src/backend/core/shared/models/repository_platform.py:* ]]; then
  printf 'acceptance_assertion=FAIL_gitea_literal_not_unique_to_shared_module\n'
  exit 1
fi
if ! baseline_factory_call_matches=$(git grep -n 'create_github_client(' "$BASELINE_COMMIT" -- src/backend); then
  printf 'acceptance_assertion=FAIL_no_baseline_factory_calls\n'
  exit 1
fi
if ! working_factory_call_matches=$(rg -n 'create_github_client\(' src/backend); then
  printf 'acceptance_assertion=FAIL_no_worktree_factory_calls\n'
  exit 1
fi
baseline_factory_call_count=$(printf '%s\n' "$baseline_factory_call_matches" | wc -l | tr -d ' ')
working_factory_call_count=$(printf '%s\n' "$working_factory_call_matches" | wc -l | tr -d ' ')
printf 'factory_calls_baseline=%s\n' "$baseline_factory_call_count"
printf 'factory_calls_worktree=%s\n' "$working_factory_call_count"
if [ "$baseline_factory_call_count" -ne "$working_factory_call_count" ]; then
  printf 'acceptance_assertion=FAIL_factory_call_count_changed\n'
  exit 1
fi
uv run python - <<'PY'
import ast
import os
import subprocess
from pathlib import Path

factory_file_path = Path("src/backend/engines/agent_runner/factories/__init__.py")
working_factory_source = factory_file_path.read_text(encoding="utf-8")
baseline_commit = os.environ["BASELINE_COMMIT"]
baseline_factory_source = subprocess.run(
    ["git", "show", f"{baseline_commit}:{factory_file_path.as_posix()}"],
    check=True,
    capture_output=True,
    text=True,
    encoding="utf-8",
).stdout


def client_factory_signature(source_text: str) -> tuple[str, str]:
    module_node = ast.parse(source_text)
    function_node = next(
        node
        for node in module_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "create_github_client"
    )
    return ast.dump(function_node.args), ast.dump(function_node.returns)


if client_factory_signature(baseline_factory_source) != client_factory_signature(
    working_factory_source
):
    raise SystemExit("create_github_client signature changed")
print("create_github_client signature and return annotation: unchanged")
PY
signature_status=$?
if [ "$signature_status" -ne 0 ]; then
  printf 'acceptance_assertion=FAIL_factory_signature_check\n'
  exit 1
fi
interface_diff_stat=$(git diff --stat -- src/backend/core/shared/interfaces/agent_runner.py)
if [ -z "$interface_diff_stat" ]; then
  printf 'github_client_interface_diff=empty\n'
else
  printf '%s\n' "$interface_diff_stat"
  exit 1
fi
printf 'test_worktree_status:\n'
git status --short -- tests
if ! git ls-files --error-unmatch tests/test_repository_platform_dispatch.py >/dev/null; then
  printf 'acceptance_assertion=FAIL_platform_test_file_not_tracked\n'
  exit 1
fi
if [ -n "$(git status --short -- tests)" ]; then
  printf 'acceptance_assertion=FAIL_unexpected_tests_worktree_changes\n'
  exit 1
fi
printf 'test_worktree_status_assertion=tracked_and_clean\n'
printf 'acceptance_assertion=PASS\n'
