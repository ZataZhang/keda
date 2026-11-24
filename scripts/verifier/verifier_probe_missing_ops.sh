#!/usr/bin/env bash
# 独立验证者探针：rv-6 的 live 测试未覆盖的三个操作，在真实 Gitea 上能否完成。
set -uo pipefail
cd /Users/zata/code/keda/.kc-worktrees/issue-416
source /private/tmp/issue416-gitea/runtime.env
root=/private/tmp/issue416-verifier/missing
rm -rf "$root"; mkdir -p "$root"
git clone -q "$RV_GITEA_BASE_URL/rvadmin/keda-rv.git" "$root/repo" 2>/dev/null
cd "$root/repo"; git config user.email v@example.invalid; git config user.name verifier
git remote set-url origin "http://$RV_GITEA_USERNAME:$RV_GITEA_TOKEN@127.0.0.1:3300/rvadmin/keda-rv.git"
git branch -q target-branch 2>/dev/null; git push -q origin target-branch 2>/dev/null
export RV_PROBE_REPO="$root/repo"
uv run --project /Users/zata/code/keda/.kc-worktrees/issue-416 --no-sync python - <<'PY'
import os, subprocess, sys
sys.path.insert(0, '/Users/zata/code/keda/.kc-worktrees/issue-416/src')
import requests
from pathlib import Path
from backend.infrastructure.gitea_client import GiteaApiClient, GiteaRepositoryBinding

base = os.environ["RV_GITEA_BASE_URL"].rstrip("/")
token = os.environ["RV_GITEA_TOKEN"]
hdr = {"Authorization": f"token {token}", "Content-Type": "application/json"}
api = f"{base}/api/v1/repos/rvadmin/keda-rv"
repo = Path(os.environ["RV_PROBE_REPO"])
client = GiteaApiClient(GiteaRepositoryBinding(repo, base, "127.0.0.1", "rvadmin/keda-rv"), token=token)

def git(*a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)

# 造一个真实开放 PR（带 WIP 前缀，走真实客户端方法）
git("checkout", "-q", "-b", "missing-ops")
(repo / "missing-ops.txt").write_text("x", encoding="utf-8")
git("add", "-A"); git("commit", "-qm", "missing ops probe"); git("push", "-q", "origin", "missing-ops")
url = client.create_draft_pr(title="[Agent] missing ops", body="probe refs #1",
                             base_branch="main", cwd=repo)
pr = int(url.rstrip("/").split("/")[-1])
print(f"开放 PR = #{pr}  ({url})")

print("--- 1) set_pull_request_base(main -> target-branch) ---")
client.set_pull_request_base(pr, "target-branch")
got = requests.get(f"{api}/pulls/{pr}", headers=hdr, timeout=30).json()
print(f"    server base = {got['base']['ref']!r}")
assert got["base"]["ref"] == "target-branch", "服务端 base 未被改写"
print("    OK")

print("--- 2) list_issue_comment_records(含时间戳/作者解析) ---")
issue = int(requests.get(f"{api}/pulls/{pr}", headers=hdr, timeout=30).json()["number"])
client.comment_pr(pr, "record probe comment")
records = client.list_issue_comment_records(pr, require_success=True)
print(f"    records = {len(records)}")
for r in records[:3]:
    print(f"      id={r.comment_id} author={r.author!r} updated_at={r.updated_at} tz={r.updated_at.tzinfo}")
assert records, "未读到任何评论记录"
assert all(r.author for r in records) and all(r.updated_at.tzinfo is not None for r in records)
print("    OK: 带作者与带时区时间戳")

print("--- 3) close_pull_request(带说明评论) ---")
client.close_pull_request(pr, comment="closing from verifier probe")
after = requests.get(f"{api}/pulls/{pr}", headers=hdr, timeout=30).json()
print(f"    server state = {after['state']!r} merged = {after.get('merged')}")
assert after["state"] == "closed", "PR 未被关闭"
body = requests.get(f"{api}/issues/{pr}/comments", headers=hdr, timeout=30).json()
assert any("closing from verifier probe" in c.get("body", "") for c in body), "说明评论未落库"
print("    OK: 已关闭且说明评论可见")

print("--- 4) 关闭后仍可被 find_* 与 context 正确判定 ---")
print(f"    find_open_pr_by_head -> {client.find_open_pr_by_head('missing-ops')!r}")
print(f"    get_pull_request_context(open) -> {client.get_pull_request_context('missing-ops')!r}")
print("MISSING_OPS_PROBE=PASS")
PY
