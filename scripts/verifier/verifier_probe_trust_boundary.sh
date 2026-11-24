#!/usr/bin/env bash
# 独立验证者探针（自研，不复用 builder 脚本）：
#   A) 远端主机未登记 -> 发请求前停止、监听端口计数为 0、提示含主机名与登记方法
#   B) 登记的令牌变量未设置 / 为空 -> 发请求前停止、计数为 0、提示含变量名、输出不含令牌
set -uo pipefail
ROOT=/Users/zata/code/keda/.kc-worktrees/issue-416
root=/private/tmp/issue416-verifier/trust
rm -rf "$root"; mkdir -p "$root/home/.kedacode" "$root/repo"
CANARY="CANARY-TOKEN-9f3a17b2c4d5"

python3 -u -c '
import json,sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
cnt=Path(sys.argv[1]); cnt.write_text("0",encoding="utf-8")
class H(BaseHTTPRequestHandler):
    def _h(self):
        cnt.write_text(str(int(cnt.read_text())+1),encoding="utf-8")
        b=json.dumps({"message":"counter"}).encode()
        self.send_response(404); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    do_GET=do_POST=do_PATCH=do_DELETE=_h
    def log_message(self,*a): return
srv=HTTPServer(("127.0.0.1",3411),H); srv.timeout=0.2
Path(sys.argv[2]).write_text("ready",encoding="utf-8")
while not Path(sys.argv[3]).exists(): srv.handle_request()
srv.server_close()
' "$root/count.txt" "$root/ready" "$root/done" &
lp=$!
for _ in $(seq 1 60); do [[ -f "$root/ready" ]] && break; sleep 0.1; done

mk_repo() {
  rm -rf "$root/repo"; mkdir -p "$root/repo"
  git -C "$root/repo" init --initial-branch=main -q
  git -C "$root/repo" remote add origin "$1"
  printf '[agent_runner.repository]\nid = "trust-probe"\nplatform = "gitea"\n' > "$root/repo/.kedacode.toml"
}
run() {
  HOME="$root/home" KEDACODE_CONFIG= UV_CACHE_DIR=/private/tmp/issue416-uv-cache \
    uv run --project "$ROOT" --no-sync kc --repo "$root/repo" issue list --output json 2>&1
}

echo "########## A) 主机未登记（remote=3411，登记表里只有别的 host）##########"
cat > "$root/home/.kedacode/config.toml" <<'TOML'
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "https://registered-only.example.com"
token_env = "SOME_ENV"
TOML
mk_repo "http://127.0.0.1:3411/o/r.git"
outA="$(run)"; stA=$?
echo "$outA" | tail -3
echo "  exit=$stA  listener_requests=$(cat "$root/count.txt")"
echo "  提示含主机名: $(case "$outA" in *127.0.0.1*) echo YES;; *) echo NO;; esac)"
echo "  提示含登记方法: $(case "$outA" in *gitea_instances*) echo YES;; *) echo NO;; esac)"

echo "########## B1) 主机已登记，但 token_env 指向未设置变量 ##########"
cat > "$root/home/.kedacode/config.toml" <<TOML
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "http://127.0.0.1:3411"
token_env = "RV_UNSET_TOKEN_VAR"
TOML
mk_repo "http://127.0.0.1:3411/o/r.git"
unset RV_UNSET_TOKEN_VAR
outB1="$(run)"; stB1=$?
echo "$outB1" | tail -3
echo "  exit=$stB1  listener_requests=$(cat "$root/count.txt")"
echo "  提示含变量名: $(case "$outB1" in *RV_UNSET_TOKEN_VAR*) echo YES;; *) echo NO;; esac)"

echo "########## B2) token_env 已设置但为空串 ##########"
export RV_UNSET_TOKEN_VAR=""
outB2="$(run)"; stB2=$?
echo "$outB2" | tail -2
echo "  exit=$stB2  listener_requests=$(cat "$root/count.txt")"

echo "########## B3) 令牌存在时请求必须真的发出（证明上面计数为 0 不是假象）##########"
export RV_UNSET_TOKEN_VAR="$CANARY"
outB3="$(run)"; stB3=$?
echo "  exit=$stB3  listener_requests=$(cat "$root/count.txt")  <-- 必须 > 0"
echo "  输出含令牌值: $(case "$outB3" in *"$CANARY"*) echo YES-LEAK;; *) echo NO;; esac)"

echo "########## B4) 异常路径是否泄漏令牌（无效令牌 -> 服务端 401 文本）##########"
printf '%s\n' "$outB3" | grep -c "$CANARY" || true
touch "$root/done"; wait "$lp" 2>/dev/null
echo "TRUST_PROBE_DONE"
