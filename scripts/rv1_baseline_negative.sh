#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
scene_root="$(mktemp -d)"
trap 'rm -rf "$scene_root"' EXIT

mkdir -p "$scene_root/repo"
git -C "$scene_root/repo" init -q
git -C "$scene_root/repo" remote add origin http://127.0.0.1:3300/example/rv1-baseline.git
cat > "$scene_root/repo/.kedacode.toml" <<'TOML'
[agent_runner.repository]
id = "rv1-baseline"
platform = "gitea"
TOML
cat > "$scene_root/config.toml" <<'TOML'
[agent_runner]
TOML

set +e
KEDACODE_CONFIG="$scene_root/config.toml" \
UV_CACHE_DIR=/private/tmp/issue416-uv-cache \
uv run --no-sync kc issue list --repo "$scene_root/repo" --output json \
  > "$scene_root/output.txt" 2>&1
command_status=$?
set -e

cat "$scene_root/output.txt"
printf 'BASELINE_EXIT_CODE=%s\n' "$command_status"
if [[ "$command_status" -ne 1 ]] || ! rg -q 'not implemented' "$scene_root/output.txt"; then
  exit 1
fi
printf 'EXPECTED_FAIL=Gitea platform is not implemented\n'
