#!/usr/bin/env bash
# 独立验证者探针：真实 Gitea 上的提交状态汇总（失败带名称 / 成功通过 / 无状态未知）。
# Gitea 1.21 没有删除 commit status 的接口，因此每个场景使用各自独立的 head 提交。
set -uo pipefail
cd /Users/zata/code/keda/.kc-worktrees/issue-416
source /private/tmp/issue416-gitea/runtime.env
root=/private/tmp/issue416-verifier/status
rm -rf "$root"; mkdir -p "$root"
A="Authorization: token $RV_GITEA_TOKEN"
API="$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv"

git clone -q "$RV_GITEA_BASE_URL/rvadmin/keda-rv.git" "$root/repo" 2>/dev/null || \
  GIT_ASKPASS= git -c "http.extraHeader=$A" clone -q "$RV_GITEA_BASE_URL/rvadmin/keda-rv.git" "$root/repo"
cd "$root/repo"
git config user.email v@example.invalid; git config user.name verifier

export RV_PROBE_REPO="$root/repo"
export RV_PROBE_EXTRA_HEADER="$A"
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
extra = os.environ["RV_PROBE_EXTRA_HEADER"]

client = GiteaApiClient(
    GiteaRepositoryBinding(repo, base, "127.0.0.1", "rvadmin/keda-rv"), token=token)


def git(*args):
    return subprocess.run(["git", "-c", f"http.extraHeader={extra}", *args],
                          cwd=repo, capture_output=True, text=True)


def new_case(name):
    """推一个新分支+新提交+开放 PR，返回 (pr_number, head_sha)。"""
    branch = f"verifier-{name}"
    (repo / f"status-{name}.txt").write_text(name, encoding="utf-8")
    for label, cmd in (
        ("checkout", ("checkout", "-q", "-b", branch)),
        ("add", ("add", "-A")),
        ("commit", ("commit", "-qm", f"verifier {name}")),
        ("push", ("push", "origin", branch)),
    ):
        r = git(*cmd)
        assert r.returncode == 0, f"{label} failed rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    sha = git("rev-parse", "HEAD").stdout.strip()
    pr = requests.post(f"{api}/pulls", headers=hdr, json={
        "title": f"verifier {name}", "body": "probe", "head": branch, "base": "main"}, timeout=30)
    assert pr.status_code == 201, pr.text
    return pr.json()["number"], sha


def read(pr_number):
    ctx = client.get_pull_request_context_by_number(pr_number)
    if ctx is None:
        return "NO_CTX(未读到开放 PR 上下文)", None
    return ctx.checks_state, ctx.checks_summary


def post(sha, state, context):
    return requests.post(f"{api}/statuses/{sha}", headers=hdr, json={
        "state": state, "context": context, "description": "verifier"}, timeout=30).status_code


results = []

# 场景 1：完全没有状态 -> 必须是未知（None），不得是 SUCCESS
pr, sha = new_case("empty")
st, sm = read(pr)
print(f"[无状态]        pr={pr} checks_state={st!r} summary={sm!r}")
assert st is None, f"无状态应为未知，实得 {st!r}"
assert st != "SUCCESS"
results.append("empty->unknown OK")

# 场景 2：单个 failure -> FAILURE 且带检查名称
pr, sha = new_case("red")
print(f"[post failure]  http={post(sha, 'failure', 'verifier/ci-red')}")
st, sm = read(pr)
print(f"[失败状态]      pr={pr} checks_state={st!r} summary={sm!r}")
assert st == "FAILURE", f"期望 FAILURE，实得 {st!r}"
assert sm and any("verifier/ci-red" in s for s in sm), f"汇总未包含检查名称: {sm!r}"
results.append("failure->FAILURE with name OK")

# 场景 3：failure + success 混合 -> 失败必须优先，不得判为通过
pr, sha = new_case("mixed")
post(sha, "success", "verifier/ci-green")
post(sha, "failure", "verifier/ci-red")
st, sm = read(pr)
print(f"[混合状态]      pr={pr} checks_state={st!r} summary={sm!r}")
assert st == "FAILURE", f"混合状态期望 FAILURE，实得 {st!r}"
results.append("mixed->FAILURE (fail wins) OK")

# 场景 4：Gitea 的其它取值 neutral / action_required / timed_out -> 不得当作通过
for weird in ("neutral", "action_required", "timed_out", "cancelled"):
    pr, sha = new_case(f"weird{weird}".replace("_", ""))
    post(sha, weird, f"verifier/ci-{weird}")
    st, sm = read(pr)
    print(f"[{weird:16}] pr={pr} checks_state={st!r} summary={sm!r}")
    assert st != "SUCCESS", f"{weird} 被误判为通过: {st!r}"
    results.append(f"{weird}->not-SUCCESS OK")

# 场景 5：全 success -> SUCCESS
pr, sha = new_case("green")
post(sha, "success", "verifier/ci-green")
st, sm = read(pr)
print(f"[全成功]        pr={pr} checks_state={st!r} summary={sm!r}")
assert st == "SUCCESS", f"全成功期望 SUCCESS，实得 {st!r}"
results.append("success->SUCCESS OK")

print("\n".join("  " + r for r in results))
print("VERIFIER_STATUS_PROBE=PASS")
PY
