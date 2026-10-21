#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../../.." && pwd)"
evidence_file="${1:-}"

if [[ -n "$evidence_file" ]]; then
    mkdir -p "$(dirname "$evidence_file")"
    set +e
    PYTHONUNBUFFERED=1 UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/issue-290-uv-cache}" \
        uv run --project "$repo_root" python "$script_dir/rv-1-real-entry.py" \
        --repo-root "$repo_root" >"$evidence_file" 2>&1
    run_status=$?
    set -e
    cat "$evidence_file"
    exit "$run_status"
else
    PYTHONUNBUFFERED=1 UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/issue-290-uv-cache}" \
        uv run --project "$repo_root" python "$script_dir/rv-1-real-entry.py" \
        --repo-root "$repo_root"
fi
