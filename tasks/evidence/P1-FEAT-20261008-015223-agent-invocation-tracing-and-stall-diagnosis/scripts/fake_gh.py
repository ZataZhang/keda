"""Realistic Validation 夹具用的 ``gh`` 替身（Issue #242）。

只实现 ``GitHubCliClient`` 真正会发出的那些 argv 形态，状态落在一个 JSON 文件里
（``RV_GH_STATE``），因此**新起的 CLI 进程**读到的状态与写它的那次运行一致。

绝不联网：任何未识别的 argv 都以非零退出并把 argv 原样打印到 stderr，让缺口
立刻暴露，而不是静默假装成功。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    raw_path = os.environ.get("RV_GH_STATE")
    if not raw_path:
        raise SystemExit("fake_gh: RV_GH_STATE is not set")
    return Path(raw_path)


def _load() -> dict[str, Any]:
    with _state_path().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, path)


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _read_body_file(argv: list[str]) -> str:
    if "--body-file" not in argv:
        return ""
    body_path = Path(argv[argv.index("--body-file") + 1])
    return body_path.read_text(encoding="utf-8")


def _issue_view(state: dict[str, Any], number: int, fields: list[str]) -> dict[str, Any]:
    issue = state["issues"][str(number)]
    projected: dict[str, Any] = {}
    for field in fields:
        if field == "labels":
            projected["labels"] = [{"name": name} for name in issue["labels"]]
        elif field in issue:
            projected[field] = issue[field]
    return projected


def _json_fields(argv: list[str]) -> list[str]:
    if "--json" not in argv:
        return []
    return [name for name in argv[argv.index("--json") + 1].split(",") if name]


def _label_flags(argv: list[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            values.extend(part for part in argv[index + 1].split(",") if part)
    return values


def main(argv: list[str]) -> int:
    state = _load()

    if argv[:2] == ["auth", "status"]:
        print("✓ Logged in to github.com account rv-harness (fake)")
        return 0

    if argv[:2] == ["repo", "view"]:
        _emit({"nameWithOwner": state["repo_full_name"]})
        return 0

    if argv[:1] == ["api"]:
        _emit({"permission": "admin"})
        return 0

    if argv[:2] == ["label", "create"]:
        return 0

    if argv[:2] == ["issue", "view"]:
        number = int(argv[2])
        issue = state["issues"].get(str(number))
        if issue is None:
            print(f"fake_gh: issue #{number} not found", file=sys.stderr)
            return 1
        if "--comments" in argv:
            _emit({"comments": issue["comments"]})
            return 0
        _emit(_issue_view(state, number, _json_fields(argv)))
        return 0

    if argv[:2] == ["issue", "list"]:
        requested_labels = _label_flags(argv, "--label")
        listed = []
        for number_text, issue in sorted(state["issues"].items(), key=lambda kv: int(kv[0])):
            if issue["state"] != "open":
                continue
            if requested_labels and not set(requested_labels) <= set(issue["labels"]):
                continue
            listed.append(_issue_view(state, int(number_text), _json_fields(argv)))
        _emit(listed)
        return 0

    if argv[:2] == ["issue", "comment"]:
        number = int(argv[2])
        body = _read_body_file(argv)
        state["issues"][str(number)]["comments"].append(
            {
                "body": body,
                "url": f"{state['repo_html_url']}/issues/{number}#issuecomment-{len(body)}",
                "author": {"login": "rv-harness"},
                "viewerDidAuthor": True,
            }
        )
        _save(state)
        print(f"{state['repo_html_url']}/issues/{number}#issuecomment-fake")
        return 0

    if argv[:2] == ["issue", "edit"]:
        number = int(argv[2])
        issue = state["issues"][str(number)]
        added = _label_flags(argv, "--add-label")
        removed = _label_flags(argv, "--remove-label")
        issue["labels"] = [
            name for name in issue["labels"] if name not in removed or name in added
        ]
        for name in added:
            if name not in issue["labels"]:
                issue["labels"].append(name)
        if "--body-file" in argv:
            issue["body"] = _read_body_file(argv)
        _save(state)
        return 0

    if argv[:2] == ["pr", "list"]:
        head = argv[argv.index("--head") + 1] if "--head" in argv else None
        matched = [
            pull for pull in state["pulls"] if head is None or pull["headRefName"] == head
        ]
        fields = _json_fields(argv)
        _emit(
            [
                {field: pull.get(field) for field in fields if field in pull}
                if fields
                else pull
                for pull in matched
            ]
        )
        return 0

    if argv[:2] == ["pr", "create"]:
        body = _read_body_file(argv)
        title = argv[argv.index("--title") + 1] if "--title" in argv else "fixture PR"
        base = argv[argv.index("--base") + 1] if "--base" in argv else "main"
        number = len(state["pulls"]) + 1
        head_sha = state.get("head_sha", "0" * 40)
        pull = {
            "number": number,
            "url": f"{state['repo_html_url']}/pull/{number}",
            "title": title,
            "body": body,
            "headRefName": state.get("head_branch", f"issue-{number}"),
            "headRefOid": head_sha,
            "baseRefOid": head_sha,
            "baseRefName": base,
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [],
            "labels": [],
            "comments": [],
        }
        state["pulls"].append(pull)
        _save(state)
        # 真客户端把 stdout 最后一行当 PR URL。
        print(pull["url"])
        return 0

    if argv[:2] == ["pr", "comment"]:
        number = int(argv[2])
        for pull in state["pulls"]:
            if pull["number"] == number:
                pull["comments"].append(_read_body_file(argv))
        _save(state)
        return 0

    if argv[:2] == ["pr", "edit"]:
        number = int(argv[2])
        for pull in state["pulls"]:
            if pull["number"] != number:
                continue
            for name in _label_flags(argv, "--add-label"):
                if name not in pull["labels"]:
                    pull["labels"].append(name)
            for name in _label_flags(argv, "--remove-label"):
                pull["labels"] = [
                    label for label in pull["labels"] if label != name
                ]
            if "--body-file" in argv:
                pull["body"] = _read_body_file(argv)
        _save(state)
        return 0

    if argv[:2] == ["pr", "view"]:
        number = int(argv[2])
        for pull in state["pulls"]:
            if pull["number"] == number:
                fields = _json_fields(argv)
                _emit(
                    {field: pull.get(field) for field in fields if field in pull}
                    if fields
                    else pull
                )
                return 0
        return 1

    print(f"fake_gh: unhandled argv: {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
