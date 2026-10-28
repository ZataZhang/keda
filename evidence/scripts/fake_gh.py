#!/usr/bin/env python3
"""文件型假 gh：只实现 daemon 认领 / 对账 / 评论 / 标签路径用到的子命令。

状态保存在 ``$FAKE_GH_STATE/gh_store.json``，每次调用追加到 ``calls.jsonl``。
故障注入：``$FAKE_GH_STATE/faults.json`` 里 ``fail_running_list: N`` 表示接下来
N 次 ``agent/running`` 计数查询返回失败，用于验证 fail-closed。
"""

import json
import os
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ["FAKE_GH_STATE"])
STATE_DIR.mkdir(parents=True, exist_ok=True)
STORE_PATH = STATE_DIR / "gh_store.json"
FAULTS_PATH = STATE_DIR / "faults.json"
LOG = STATE_DIR / "calls.jsonl"
REPO = os.environ.get("FAKE_GH_REPO", "fake/rv316")

args = sys.argv[1:]
with LOG.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\n")


def load():
    """读取假 GitHub 存储。"""
    if STORE_PATH.exists():
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    return {"issues": [], "labels": [], "comments": []}


def save(store):
    """写回假 GitHub 存储。"""
    STORE_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def out(payload, code=0):
    """输出并退出。"""
    print(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def fail(message, code=1):
    """错误输出并退出。"""
    print(message, file=sys.stderr)
    sys.exit(code)


def expand(value):
    """展开 gh 的 ``@file`` 参数取值。"""
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def issue_view(store, issue):
    """把内部 Issue 记录转成 API 形状。"""
    comments = [item for item in store["comments"] if item["issue"] == issue["number"]]
    return {
        "number": issue["number"],
        "title": issue["title"],
        "body": issue.get("body", ""),
        "state": issue.get("state", "open"),
        "labels": [{"name": name} for name in issue.get("labels", [])],
        "user": {"login": "fake-user"},
        "html_url": f"https://github.com/{REPO}/issues/{issue['number']}",
        "created_at": issue.get("created_at", "2026-10-20T00:00:00Z"),
        "updated_at": issue.get("updated_at", "2026-10-20T00:00:00Z"),
        "comments": len(comments),
    }


def find_issue(store, number):
    """按编号找 Issue。"""
    return next((issue for issue in store["issues"] if str(issue["number"]) == str(number)), None)


def add_comment(store, target, body):
    """追加评论并落盘。"""
    store["comments"].append({
        "issue": target["number"],
        "body": body,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    save(store)


def consume_fault(name):
    """消费一次性故障注入，返回是否应该失败。"""
    if not FAULTS_PATH.exists():
        return False
    faults = json.loads(FAULTS_PATH.read_text(encoding="utf-8"))
    remaining = int(faults.get(name, 0))
    if remaining <= 0:
        return False
    faults[name] = remaining - 1
    FAULTS_PATH.write_text(json.dumps(faults), encoding="utf-8")
    return True


if not args:
    fail("no args")

if args[:2] == ["auth", "status"]:
    out("""github.com
  ✓ Logged in to github.com account fake (keyring)
  - Active account: true
  - Git operations protocol: https
  - Token: gho_****
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'""")

if args[:2] == ["api", "user"]:
    out({"login": "fake-user"})

if args[:2] == ["api", "rate_limit"]:
    out({"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 9999999999}}})

store = load()

if args[:2] == ["label", "list"]:
    out(store["labels"])

if args[:2] == ["label", "create"]:
    name = args[2]
    if not any(label["name"] == name for label in store["labels"]):
        store["labels"].append({"name": name})
        save(store)
    out({"name": name})

if args[:2] == ["issue", "create"]:
    title = args[args.index("--title") + 1] if "--title" in args else "created"
    body = ""
    if "--body-file" in args:
        body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
    labels = [args[index + 1] for index, token in enumerate(args) if token == "--label"]
    number = max([issue["number"] for issue in store["issues"]] + [100]) + 1
    store["issues"].append({"number": number, "title": title, "body": body,
                            "state": "open", "labels": labels})
    save(store)
    out(f"https://github.com/{REPO}/issues/{number}")

if args[:2] == ["issue", "comment"] and len(args) > 2:
    target = find_issue(store, args[2])
    if target is None:
        fail(f"no issue {args[2]}")
    if "--body-file" in args:
        body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
    elif "--body" in args:
        body = args[args.index("--body") + 1]
    else:
        body = sys.stdin.read()
    add_comment(store, target, body)
    out(f"https://github.com/{REPO}/issues/{target['number']}#issuecomment-1")

if args[:2] == ["issue", "edit"] and len(args) > 2:
    target = find_issue(store, args[2])
    if target is None:
        fail(f"no issue {args[2]}")
    for index, token in enumerate(args):
        if token == "--add-label":
            name = args[index + 1]
            if name not in target["labels"]:
                target["labels"].append(name)
        elif token == "--remove-label":
            name = args[index + 1]
            target["labels"] = [label for label in target["labels"] if label != name]
    save(store)
    out("")

if args[:2] == ["issue", "close"] and len(args) > 2:
    target = find_issue(store, args[2])
    if target is not None:
        target["state"] = "closed"
        save(store)
    out("")

if args[:2] == ["issue", "view"] and len(args) > 2:
    target = find_issue(store, args[2])
    if target is None:
        fail(f"no issue {args[2]}")
    view = issue_view(store, target)
    if "--json" in args:
        fields = args[args.index("--json") + 1].split(",")
        out({key: view.get(key) for key in fields})
    out(view)

if args[:2] == ["issue", "list"]:
    wanted = [args[index + 1] for index, token in enumerate(args) if token in ("--label", "-l")]
    state = args[args.index("--state") + 1] if "--state" in args else "all"
    picked = []
    for issue in store["issues"]:
        if state == "open" and issue.get("state") != "open":
            continue
        if not all(label in issue.get("labels", []) for label in wanted):
            continue
        view = issue_view(store, issue)
        if "--json" in args:
            fields = args[args.index("--json") + 1].split(",")
            view = {key: view.get(key) for key in fields}
        picked.append(view)
    out(picked)

method = "GET"
rest = list(args[1:])
slurp = "--slurp" in rest
rest = [token for token in rest if token not in ("--paginate", "--slurp")]
if rest[:1] == ["-X"]:
    method = rest[1]
    rest = rest[2:]
path = rest[0] if rest else ""
fields = {}
index = 1
while index < len(rest):
    token = rest[index]
    if token in ("-f", "-F") and index + 1 < len(rest):
        key, _, value = rest[index + 1].partition("=")
        if key.endswith("[]"):
            fields.setdefault(key, [])
            fields[key].append(expand(value))
        else:
            fields[key] = expand(value)
        index += 2
    elif token == "--input" and index + 1 < len(rest):
        fields.update(json.loads(Path(rest[index + 1]).read_text(encoding="utf-8")))
        index += 2
    else:
        index += 1

if path.startswith("repos/") and "/issues" in path:
    tail = path.split("/issues", 1)[1]
    if tail in ("", "/"):
        wanted_state = fields.get("state", "open")
        wanted_labels = fields.get("labels", "")
        counting_query = (
            wanted_state == "open"
            and wanted_labels == "agent/running"
            and str(fields.get("per_page", "")) != "1"
        )
        if counting_query and consume_fault("fail_running_list"):
            fail("injected failure while listing agent/running Issues")
        per_page = int(str(fields.get("per_page", "30")).replace("_", ""))
        picked = []
        for issue in store["issues"]:
            if wanted_state == "open" and issue.get("state") != "open":
                continue
            if wanted_state == "closed" and issue.get("state") != "closed":
                continue
            if wanted_labels:
                required = set(wanted_labels.split(","))
                if not required.issubset(set(issue.get("labels", []))):
                    continue
            picked.append(issue_view(store, issue))
        out(picked[:per_page])
    issue_part = tail.strip("/")
    number, _, action = issue_part.partition("/")
    target = find_issue(store, number)
    if target is None:
        fail(f"no issue {number}")
    if action == "comments" and method == "GET":
        picked = [
            {"id": position + 1, "body": item["body"],
             "user": {"login": "fake-user"}, "created_at": item["at"]}
            for position, item in enumerate(
                comment for comment in store["comments"] if comment["issue"] == target["number"]
            )
        ]
        out([picked] if slurp else picked)
    if action == "comments" and method == "POST":
        add_comment(store, target, fields.get("body", ""))
        out({"id": len(store["comments"])})
    if action.startswith("labels/"):
        label_name = action.split("/", 1)[1].replace("%2F", "/")
        if method == "DELETE":
            target["labels"] = [name for name in target["labels"] if name != label_name]
            save(store)
            out([])
        if method == "POST":
            if label_name not in target["labels"]:
                target["labels"].append(label_name)
            save(store)
            out([{"name": name} for name in target["labels"]])
    if action == "labels" and method == "POST":
        requested = fields.get("labels") or fields.get("labels[]") or []
        if isinstance(requested, str):
            requested = requested.split(",")
        for name in [str(item).strip() for item in requested if str(item).strip()]:
            if name not in target["labels"]:
                target["labels"].append(name)
        save(store)
        out([{"name": name} for name in target["labels"]])
    if action == "labels" and method == "DELETE":
        target["labels"] = [name for name in target["labels"] if name != fields.get("name", "")]
        save(store)
        out([])
    if action == "" and method == "GET":
        out(issue_view(store, target))
    fail(f"unhandled api {method} {path} fields={fields}")

save(store)
fail(f"unhandled command: {args}")

