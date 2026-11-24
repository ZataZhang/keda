#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
scenario_root="/private/tmp/issue416-gitea/rv4"
repository_path="$scenario_root/repository"
config_path="$scenario_root/home/.kedacode/config.toml"
local_config_path="$repository_path/.kedacode.toml"
cd "$repository_root"

tree_hash="$(git rev-parse 'HEAD^{tree}')"
printf 'TREE_HASH=%s\n' "$tree_hash"
printf 'ENTRY_POINT=uv run --no-sync kc --repo <repo> agent lifecycle list --scope repository\n'
rm -rf "$scenario_root"
mkdir -p "$(dirname "$config_path")" "$repository_path"
git -C "$repository_path" init --initial-branch=main --quiet
git -C "$repository_path" remote add origin http://127.0.0.1:3398/o/r.git
cat > "$local_config_path" <<'TOML'
[agent_runner.repository]
id = "rv4-url-policy"
platform = "gitea"
TOML

export HOME="$scenario_root/home"
unset KEDACODE_CONFIG
export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
cat > "$config_path" <<'TOML'
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "http://git.example.com"
token_env = "RV_HTTP_TOKEN"
TOML
set +e
insecure_output="$(uv run --project "$repository_root" --no-sync kc --repo "$repository_path" agent lifecycle list --scope repository 2>&1)"
insecure_status=$?
set -e
printf 'NONLOCAL_HTTP_OUTPUT=%s\n' "$insecure_output"
[[ "$insecure_status" == "1" ]]
[[ "$insecure_output" == *"https://"* ]]
printf 'EXPECTED_FAIL=non-local http base_url must be rejected at configuration load; exit_code=%s\n' \
  "$insecure_status"

cat > "$config_path" <<'TOML'
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "http://127.0.0.1:3398"
token_env = "RV_HTTP_TOKEN"
TOML
loopback_output="$(uv run --project "$repository_root" --no-sync kc --repo "$repository_path" agent lifecycle list --scope repository 2>&1)"
printf '%s\n' "$loopback_output"
printf 'LOOPBACK_HTTP_RESULT=exit_code=0\n'

cat >> "$local_config_path" <<'TOML'

[[agent_runner.gitea_instances]]
base_url = "http://127.0.0.1:3398"
token_env = "RV_REPO_TOKEN"
TOML
set +e
injection_output="$(uv run --project "$repository_root" --no-sync kc --repo "$repository_path" agent lifecycle list --scope repository 2>&1)"
injection_status=$?
set -e
printf 'REPOSITORY_INJECTION_OUTPUT=%s\n' "$injection_output"
[[ "$injection_status" == "1" ]]
[[ "$injection_output" == *"machine-only"* ]]
[[ "$injection_output" == *"config.toml"* ]]
printf 'REPOSITORY_INJECTION_RESULT=exit_code=%s rejected=true\n' "$injection_status"
printf 'EXPECTED_FAIL=if either configuration boundary regresses, its invalid configuration command will exit 0\n'
