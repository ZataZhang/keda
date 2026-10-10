#!/usr/bin/env bash
set -euo pipefail

repository_root_path="$(git rev-parse --show-toplevel)"
evidence_root_path="${repository_root_path}/tasks/evidence/P2-FEAT-20261009-171037-agent-preset-performance-stats"
cd "${repository_root_path}"

export UV_CACHE_DIR="/tmp/keda-issue-263-uv-cache"

uv run pytest --no-testmon tests/test_console_stats.py tests/test_agent_runner_console_api.py \
  -k agent_performance -q 2>&1 | tee "${evidence_root_path}/rv-1-api-green.txt"
uv run pytest --no-testmon \
  tests/test_prd_lifecycle.py::test_stats_percentiles_over_completed_runs_only -q 2>&1 \
  | tee "${evidence_root_path}/rv-1-lifecycle-percentile-regression.txt"
uv run python \
  "${evidence_root_path}/scripts/capture_agent_performance_api.py" \
  "${evidence_root_path}/rv-1-api-json.json" 2>&1 \
  | tee "${evidence_root_path}/rv-1-api-json-capture.txt"
uv run python \
  "${evidence_root_path}/scripts/capture_final_tree_evidence.py" 1
