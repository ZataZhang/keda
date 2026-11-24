#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
scenario_root="/private/tmp/issue416-gitea/rv2"
repository_path="$scenario_root/repository"
config_path="$scenario_root/home/.kedacode/config.toml"
count_path="$scenario_root/request-count.txt"
ready_path="$scenario_root/listener-ready"
shutdown_path="$scenario_root/listener-shutdown"
cd "$repository_root"

tree_hash="$(git rev-parse 'HEAD^{tree}')"
printf 'TREE_HASH=%s\n' "$tree_hash"
printf 'ENTRY_POINT=uv run --no-sync kc --repo <repo> issue list --output json\n'
rm -rf "$scenario_root"
mkdir -p "$(dirname "$config_path")" "$repository_path"
cat > "$config_path" <<'TOML'
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "http://127.0.0.1:3399"
token_env = "RV_GITEA_PROBE_TOKEN"
TOML
cat > "$repository_path/.kedacode.toml" <<'TOML'
[agent_runner.repository]
id = "rv2-unregistered"
platform = "gitea"
TOML
git -C "$repository_path" init --initial-branch=main --quiet
git -C "$repository_path" remote add origin http://127.0.0.1:3399/o/r.git

python3 -u -c '
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

count_path, ready_path, shutdown_path = map(Path, sys.argv[1:])
count_path.write_text("0", encoding="utf-8")

class RequestCounter(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.request_count += 1
        count_path.write_text(str(self.server.request_count), encoding="utf-8")
        response_body = json.dumps({"message": "request counter only"}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    do_POST = do_GET
    do_PATCH = do_GET

    def log_message(self, format_string, *arguments):
        return

listener = HTTPServer(("127.0.0.1", 3399), RequestCounter)
listener.request_count = 0
listener.timeout = 0.2
ready_path.write_text("ready", encoding="utf-8")
while not shutdown_path.exists():
    listener.handle_request()
listener.server_close()
' "$count_path" "$ready_path" "$shutdown_path" &
listener_pid=$!
cleanup_listener() {
  touch "$shutdown_path"
  wait "$listener_pid"
}
trap cleanup_listener EXIT
for startup_attempt in $(seq 1 50); do
  if [[ -f "$ready_path" ]]; then
    break
  fi
  sleep 0.1
done
[[ -f "$ready_path" ]]

export RV_GITEA_PROBE_TOKEN=breakme
export HOME="$scenario_root/home"
unset KEDACODE_CONFIG
export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
set +e
unregistered_output="$(uv run --project "$repository_root" --no-sync kc --repo "$repository_path" issue list --output json 2>&1)"
unregistered_status=$?
set -e
printf '%s\n' "$unregistered_output"
[[ "$unregistered_status" == "1" ]]
[[ "$unregistered_output" == *"127.0.0.1"* ]]
[[ "$unregistered_output" == *"gitea_instances"* ]]
request_count="$(cat "$count_path")"
[[ "$request_count" == "0" ]]
printf 'UNREGISTERED_EXIT_CODE=%s requests=%s\n' "$unregistered_status" "$request_count"

cat > "$config_path" <<'TOML'
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "http://127.0.0.1:3399"
token_env = "RV_GITEA_PROBE_TOKEN"
TOML
probe_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(20))')"
export RV_GITEA_PROBE_TOKEN="$probe_token"
set +e
registered_output="$(uv run --project "$repository_root" --no-sync kc --repo "$repository_path" issue list --output json 2>&1)"
registered_status=$?
set -e
unset RV_GITEA_PROBE_TOKEN probe_token
printf '%s\n' "$registered_output"
request_count="$(cat "$count_path")"
[[ "$request_count" -gt "0" ]]
printf 'NEGATIVE_CONTROL=registered host reached listener; requests=%s exit_code=%s\n' \
  "$request_count" "$registered_status"
printf 'EXPECTED_FAIL=if the request counter control is working, registration causes requests>0\n'
touch "$shutdown_path"
wait "$listener_pid"
trap - EXIT
