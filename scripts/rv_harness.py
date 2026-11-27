"""隔离 Issue #457 的本地 HTTPS forge、SQLite 与真实 CLI 场景。"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from rv_setup import (
    _make_certificate,
    _write_agent_fixture,
    _write_repository_config,
    _write_verification_fixture,
)

PRD_STEM = "P1-FEAT-20261024-162357-agent-interruption-recovery"
EVIDENCE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ISSUE_NUMBER = 73
OWNER_REPOSITORY = "owner/repo"
FIXTURE_TOKEN = "ghp_rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
FIXTURE_PRD_PATH = "tasks/pending/P2-FEAT-20261127-000000-recovery-rv-fixture.md"


@dataclass
class RecoveryScene:
    """一个临时仓库、运行状态库、HTTPS forge 与真实 CLI 的句柄。"""

    root: Path
    repository_path: Path
    state_home: Path
    database_path: Path
    bare_remote_path: Path
    control_path: Path
    forge_state_path: Path
    agent_log_path: Path
    verification_log_path: Path
    request_log_path: Path
    certificate_path: Path
    server: ThreadingHTTPServer
    server_thread: threading.Thread
    environment: dict[str, str]
    kc_command: str
    server_port: int

    def close(self) -> None:
        """关闭测试服务并删除完整临时场景。"""
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        shutil.rmtree(self.root, ignore_errors=True)


def _write_json(path: Path, payload: object) -> None:
    """以 UTF-8 原子写入 JSON 测试状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    temporary_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；缺失文件表示空状态。"""
    if not path.is_file():
        return {}
    parsed_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed_payload, dict):
        raise RuntimeError(f"Expected a JSON object in {path}.")
    return parsed_payload


def _append_json_line(path: Path, payload: dict[str, object]) -> None:
    """追加一条 UTF-8 JSONL 探针记录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output_file:
        output_file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _git(repository_path: Path, *arguments: str) -> str:
    """在指定真实 Git 仓库执行命令并返回标准输出。"""
    command_result = subprocess.run(
        ["git", *arguments],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return command_result.stdout.strip()


def _new_forge_state() -> dict[str, object]:
    """返回单个隔离 Issue 与空 PR 列表。"""
    return {
        "issue": {
            "number": ISSUE_NUMBER,
            "title": "Agent recovery fixture",
            "body": (
                "Exercise the runner through its real CLI entry point.\n\n"
                f"PRD path: `{FIXTURE_PRD_PATH}`"
            ),
            "state": "open",
            "html_url": f"https://github.com/{OWNER_REPOSITORY}/issues/{ISSUE_NUMBER}",
            "user": {"login": "rv-bot", "id": 1},
            "labels": [{"name": "agent/ready"}, {"name": "agent/codex"}],
            "assignees": [],
            "created_at": "2026-11-27T00:00:00Z",
            "updated_at": "2026-11-27T00:00:00Z",
        },
        "issue_comments": [],
        "pull_requests": [],
        "labels": [
            {"name": "agent/ready", "color": "0e8a16", "description": "Ready"},
            {"name": "agent/running", "color": "fbca04", "description": "Running"},
            {
                "name": "agent/automated-review-passed",
                "color": "0e8a16",
                "description": "Review passed",
            },
            {"name": "agent/supervising", "color": "1d76db", "description": "Supervising"},
            {"name": "agent/blocked", "color": "d93f0b", "description": "Blocked"},
            {"name": "agent/failed", "color": "b60205", "description": "Failed"},
            {"name": "agent/codex", "color": "5319e7", "description": "Codex"},
        ],
        "next_comment_id": 1,
        "next_pr_number": 1,
        "pull_list_reads": 0,
    }


def _load_scene_state(scene: RecoveryScene) -> dict[str, Any]:
    """读取服务端持久化状态。"""
    return _read_json(scene.forge_state_path)


def _save_scene_state(scene: RecoveryScene, state: dict[str, Any]) -> None:
    """保存服务端持久化状态。"""
    _write_json(scene.forge_state_path, state)


def _load_control(scene: RecoveryScene) -> dict[str, Any]:
    """读取只由测试服务使用的故障控制状态。"""
    return _read_json(scene.control_path)


def _set_control(scene: RecoveryScene, **updates: object) -> None:
    """只修改测试侧服务控制状态，不改生产配置或源代码。"""
    control_state = _load_control(scene)
    control_state.update(updates)
    _write_json(scene.control_path, control_state)


def _append_request_log(scene: RecoveryScene, method: str, path: str, outcome: str) -> None:
    """记录服务端观察到的真实 gh 请求，不记录授权头。"""
    _append_json_line(
        scene.request_log_path,
        {
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "outcome": outcome,
        },
    )


def _has_in_flight_operation(scene: RecoveryScene, operation_kind: str) -> bool:
    """检查严格恢复库中的某类发送权是否处于 in_flight。"""
    if not scene.database_path.is_file():
        return False
    with sqlite3.connect(scene.database_path) as database_connection:
        row = database_connection.execute(
            "SELECT 1 FROM runner_external_operations "
            "WHERE operation_kind = ? AND state = 'in_flight' LIMIT 1",
            (operation_kind,),
        ).fetchone()
    return row is not None


def _issue_payload(scene: RecoveryScene) -> dict[str, Any]:
    """返回 GitHub REST 形状的 Issue 对象。"""
    state = _load_scene_state(scene)
    issue_payload = state.get("issue")
    if not isinstance(issue_payload, dict):
        raise RuntimeError("The forge fixture lost Issue #73.")
    return issue_payload


def _paginate_rows(
    handler: BaseHTTPRequestHandler,
    rows: list[dict[str, Any]],
    query: dict[str, list[str]],
    page_size_default: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """按 REST page/per_page 返回本页数据及下一页响应头。"""
    parsed_page = int(query.get("page", ["1"])[0])
    page_size = max(1, min(int(query.get("per_page", [str(page_size_default)])[0]), 100))
    first_index = (parsed_page - 1) * page_size
    page_rows = rows[first_index : first_index + page_size]
    response_headers: dict[str, str] = {}
    if first_index + page_size < len(rows):
        parsed_url = urlparse(handler.path)
        next_query = dict(query)
        next_query["page"] = [str(parsed_page + 1)]
        next_url = f"{parsed_url.path}?{urlencode(next_query, doseq=True)}"
        response_headers["Link"] = f'<{next_url}>; rel="next"'
    return page_rows, response_headers


def _pull_payload(
    scene: RecoveryScene,
    *,
    title: str,
    body: str,
    head_branch: str,
    base_branch: str,
    state: str = "open",
    visible_after_reads: int = 0,
) -> dict[str, Any]:
    """构造带稳定远端身份的 GitHub PR 对象。"""
    state_payload = _load_scene_state(scene)
    pull_number = int(state_payload.get("next_pr_number", 1))
    state_payload["next_pr_number"] = pull_number + 1
    head_sha_result = subprocess.run(
        ["git", "--git-dir", str(scene.bare_remote_path), "rev-parse", f"refs/heads/{head_branch}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    base_sha_result = subprocess.run(
        ["git", "--git-dir", str(scene.bare_remote_path), "rev-parse", f"refs/heads/{base_branch}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if head_sha_result.returncode or base_sha_result.returncode:
        raise RuntimeError("The forge fixture cannot resolve the pushed PR head/base commits.")
    head_sha = head_sha_result.stdout.strip()
    base_sha = base_sha_result.stdout.strip()
    if len(head_sha) != 40 or len(base_sha) != 40:
        raise RuntimeError("The forge fixture produced invalid Git commit identities.")
    pull_payload = {
        "number": pull_number,
        "title": title,
        "body": body,
        "state": state,
        "draft": True,
        "merged_at": None,
        "html_url": f"https://github.com/{OWNER_REPOSITORY}/pull/{pull_number}",
        "url": f"https://127.0.0.1/pull/{pull_number}",
        "user": {"login": "rv-bot", "id": 1},
        "head": {"ref": head_branch, "sha": head_sha, "repo": {"full_name": OWNER_REPOSITORY}},
        "base": {"ref": base_branch, "sha": base_sha, "repo": {"full_name": OWNER_REPOSITORY}},
        "labels": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "visibility_remaining": visible_after_reads,
    }
    pulls = state_payload.get("pull_requests")
    if not isinstance(pulls, list):
        raise RuntimeError("The forge fixture lost its pull request list.")
    pulls.append(pull_payload)
    _save_scene_state(scene, state_payload)
    return pull_payload


def _visible_pull_requests(scene: RecoveryScene, query: dict[str, list[str]]) -> list[dict[str, Any]]:
    """按 head/base/state 过滤 PR，并推进延迟可见状态。"""
    state_payload = _load_scene_state(scene)
    pulls = state_payload.get("pull_requests", [])
    if not isinstance(pulls, list):
        raise RuntimeError("The forge fixture PR list is malformed.")
    visible_rows: list[dict[str, Any]] = []
    requested_head = query.get("head", [""])[0].split(":", 1)[-1]
    requested_base = query.get("base", [""])[0]
    requested_state = query.get("state", ["open"])[0]
    for pull_payload in pulls:
        if not isinstance(pull_payload, dict):
            continue
        remaining_visibility_reads = int(pull_payload.get("visibility_remaining", 0))
        if remaining_visibility_reads > 0:
            pull_payload["visibility_remaining"] = remaining_visibility_reads - 1
            continue
        if requested_head and pull_payload.get("head", {}).get("ref") != requested_head:
            continue
        if requested_base and pull_payload.get("base", {}).get("ref") != requested_base:
            continue
        if requested_state == "open" and pull_payload.get("state") != "open":
            continue
        visible_rows.append(pull_payload)
    _save_scene_state(scene, state_payload)
    return visible_rows


class _RecoveryForgeHandler(BaseHTTPRequestHandler):
    """实现测试需要的最小 GitHub REST 与 GraphQL 边界；未知路径返回 404。"""

    server: ThreadingHTTPServer

    @property
    def scene(self) -> RecoveryScene:
        """读取服务端绑定的隔离场景。"""
        return getattr(self.server, "scene")

    def log_message(self, _format: str, *_arguments: object) -> None:
        """关闭默认访问日志，避免输出到用户终端。"""

    def _read_payload(self) -> dict[str, Any]:
        """读取 JSON body 或 application/x-www-form-urlencoded body。"""
        body_length = int(self.headers.get("Content-Length", "0"))
        body_bytes = self.rfile.read(body_length) if body_length else b""
        if not body_bytes:
            return {}
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            parsed_payload = json.loads(body_bytes.decode("utf-8"))
            return parsed_payload if isinstance(parsed_payload, dict) else {}
        parsed_form = parse_qs(body_bytes.decode("utf-8"), keep_blank_values=True)
        return {field_name: values[-1] for field_name, values in parsed_form.items()}

    def _send_json(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        """发送 JSON response，并记录状态码。"""
        response_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.close_connection = True
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Connection", "close")
        self.send_header("X-GitHub-Request-Id", "RV-FIXTURE-REQUEST")
        for header_name, header_value in (headers or {}).items():
            self.send_header(header_name, header_value)
        self.end_headers()
        self.wfile.write(response_bytes)
        _append_request_log(self.scene, self.command, self.path, f"http-{status_code}")

    def _drop_response(self, reason: str) -> None:
        """在请求到达服务端后直接断开 socket，不返回 HTTP response。"""
        _append_request_log(self.scene, self.command, self.path, reason)
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def _handle(self) -> None:
        """分派到受支持的 REST/GraphQL 路由。"""
        parsed_url = urlparse(self.path)
        api_path = parsed_url.path
        query = parse_qs(parsed_url.query, keep_blank_values=True)
        payload = self._read_payload() if self.command in {"POST", "PATCH", "PUT"} else {}
        control_state = _load_control(self.scene)

        if api_path == "/" and self.command == "GET":
            self._send_json(
                200,
                {"message": "isolated recovery forge"},
                headers={"X-OAuth-Scopes": "repo, read:org, gist"},
            )
            return
        if api_path.endswith("/graphql"):
            self._handle_graphql(payload, control_state)
            return
        if api_path.endswith("/user"):
            self._send_json(200, {"id": 1, "login": "rv-bot", "name": "RV bot"})
            return
        if api_path.endswith("/rate_limit"):
            self._send_json(
                200,
                {"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 1}}},
            )
            return
        if api_path.endswith(f"/issues/{ISSUE_NUMBER}/comments"):
            if self.command == "GET":
                self._handle_issue_comments(query)
                return
            if self.command == "POST":
                self._handle_comment_create(payload, control_state)
                return
        if "/issues/comments/" in api_path and self.command in {"GET", "PATCH"}:
            comment_id = int(api_path.rsplit("/", 1)[-1])
            self._handle_comment_update(comment_id, payload)
            return
        if api_path.endswith(f"/issues/{ISSUE_NUMBER}/labels"):
            if self.command == "GET":
                self._send_json(200, _issue_payload(self.scene).get("labels", []))
                return
            if self.command in {"POST", "PUT", "PATCH"}:
                self._handle_label_update(payload)
                return
        if api_path.endswith(f"/issues/{ISSUE_NUMBER}"):
            if self.command == "GET":
                self._send_json(200, _issue_payload(self.scene))
                return
            if self.command == "PATCH":
                self._handle_issue_update(payload)
                return
        if self.command == "GET":
            issue_match = re.search(r"/issues/(\d+)$", api_path)
            if issue_match:
                pull_payload = self._find_pull(int(issue_match.group(1)))
                if pull_payload is not None:
                    pull_issue_payload = dict(pull_payload)
                    pull_issue_payload["pull_request"] = {
                        "url": pull_payload.get("url"),
                        "html_url": pull_payload.get("html_url"),
                        "diff_url": f"{pull_payload.get('html_url', '')}.diff",
                        "patch_url": f"{pull_payload.get('html_url', '')}.patch",
                    }
                    self._send_json(200, pull_issue_payload)
                    return
        if api_path.endswith("/issues") and self.command == "GET":
            issue_payload = _issue_payload(self.scene)
            query_labels = {
                label_name
                for label_group in query.get("labels", [])
                for label_name in label_group.split(",")
                if label_name
            }
            issue_labels = {
                str(label_payload.get("name"))
                for label_payload in issue_payload.get("labels", [])
                if isinstance(label_payload, dict)
            }
            requested_state = query.get("state", ["open"])[0]
            matching_issues = (
                [issue_payload]
                if issue_payload.get("state") == requested_state
                and (not query_labels or issue_labels.intersection(query_labels))
                else []
            )
            self._send_json(200, matching_issues)
            return
        if api_path.endswith("/pulls"):
            if self.command == "GET":
                self._handle_pull_list(query, control_state)
                return
            if self.command == "POST":
                self._handle_pull_create(payload, control_state)
                return
        if "/pulls/" in api_path and self.command == "GET":
            pull_number = int(api_path.rsplit("/", 1)[-1])
            pull_payload = self._find_pull(pull_number)
            self._send_json(200, pull_payload or {"message": "Not Found"})
            return
        if re.search(r"/commits/[0-9a-f]{40}/check-runs$", api_path) and self.command == "GET":
            self._send_json(200, {"total_count": 0, "check_runs": []})
            return
        if re.search(r"/commits/[0-9a-f]{40}/statuses$", api_path) and self.command == "GET":
            self._send_json(200, [])
            return
        if re.search(r"/commits/[0-9a-f]{40}/status$", api_path) and self.command == "GET":
            self._send_json(200, {"state": "success", "statuses": []})
            return
        if api_path.endswith("/labels") and self.command == "GET":
            self._send_json(200, _load_scene_state(self.scene).get("labels", []))
            return
        if api_path.endswith("/labels") and self.command == "POST":
            self._handle_label_create(payload)
            return
        if api_path.endswith(f"/issues/{ISSUE_NUMBER}/comments"):
            self._send_json(404, {"message": "Unsupported comment operation."})
            return
        if api_path.endswith("/repos/owner/repo") or api_path.endswith("/repository"):
            self._send_json(
                200,
                {
                    "id": "R_rv_fixture_repo",
                    "name": "repo",
                    "full_name": OWNER_REPOSITORY,
                    "default_branch": "main",
                    "html_url": f"https://github.com/{OWNER_REPOSITORY}",
                    "permissions": {"admin": True, "push": True, "pull": True},
                },
            )
            return
        self._send_json(404, {"message": f"Unrecognized fixture route: {self.command} {api_path}"})

    def _handle_issue_comments(self, query: dict[str, list[str]]) -> None:
        """返回真实分页形状的 Issue 评论列表。"""
        state_payload = _load_scene_state(self.scene)
        comment_rows = state_payload.get("issue_comments", [])
        if not isinstance(comment_rows, list):
            raise RuntimeError("The forge fixture comment list is malformed.")
        page_rows, pagination_headers = _paginate_rows(self, comment_rows, query)
        self._send_json(200, page_rows, headers=pagination_headers)

    def _handle_comment_create(
        self,
        payload: dict[str, Any],
        control_state: dict[str, Any],
    ) -> None:
        """保存评论，支持测试侧 429 与提交后断连。"""
        comment_body = str(payload.get("body", ""))
        if control_state.get("deny_report_comment_once") and "恢复报告" in comment_body:
            _set_control(self.scene, deny_report_comment_once=False)
            self._send_json(
                429,
                {"message": "API rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790000000"},
            )
            return
        state_payload = _load_scene_state(self.scene)
        comment_id = int(state_payload.get("next_comment_id", 1))
        state_payload["next_comment_id"] = comment_id + 1
        issue_comments = state_payload.get("issue_comments", [])
        if not isinstance(issue_comments, list):
            raise RuntimeError("The forge fixture comment list is malformed.")
        comment_payload = {
            "id": comment_id,
            "body": comment_body,
            "user": {"id": 1, "login": "rv-bot"},
            "html_url": f"https://github.com/{OWNER_REPOSITORY}/issues/{ISSUE_NUMBER}#issuecomment-{comment_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        issue_comments.append(comment_payload)
        _save_scene_state(self.scene, state_payload)
        if control_state.get("drop_next_comment_response"):
            _set_control(self.scene, drop_next_comment_response=False)
            self._drop_response("persisted-comment-then-socket-drop")
            return
        self._send_json(201, comment_payload)

    def _handle_comment_update(self, comment_id: int, payload: dict[str, Any]) -> None:
        """读取或更新既有 Issue 评论，例如续租认领标记。"""
        state_payload = _load_scene_state(self.scene)
        issue_comments = state_payload.get("issue_comments", [])
        if not isinstance(issue_comments, list):
            raise RuntimeError("The forge fixture comment list is malformed.")
        comment_payload = next(
            (
                comment_row
                for comment_row in issue_comments
                if isinstance(comment_row, dict) and comment_row.get("id") == comment_id
            ),
            None,
        )
        if comment_payload is None:
            self._send_json(404, {"message": "Not Found"})
            return
        if self.command == "PATCH" and "body" in payload:
            comment_payload["body"] = str(payload["body"])
            comment_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_scene_state(self.scene, state_payload)
        self._send_json(200, comment_payload)

    def _handle_issue_update(self, payload: dict[str, Any]) -> None:
        """更新 Issue 状态、正文或标签。"""
        state_payload = _load_scene_state(self.scene)
        issue_payload = state_payload.get("issue")
        if not isinstance(issue_payload, dict):
            raise RuntimeError("The forge fixture lost Issue #73.")
        for field_name in ("state", "title", "body"):
            if field_name in payload:
                issue_payload[field_name] = payload[field_name]
        if "labels" in payload:
            labels_payload = payload["labels"]
            if isinstance(labels_payload, list):
                issue_payload["labels"] = [
                    {"name": str(label_name)} for label_name in labels_payload
                ]
        _save_scene_state(self.scene, state_payload)
        self._send_json(200, issue_payload)

    def _handle_label_update(self, payload: dict[str, Any]) -> None:
        """按 add/remove 语义修改 Issue 标签。"""
        state_payload = _load_scene_state(self.scene)
        issue_payload = state_payload.get("issue")
        if not isinstance(issue_payload, dict):
            raise RuntimeError("The forge fixture lost Issue #73.")
        current_names = {
            str(label_payload.get("name"))
            for label_payload in issue_payload.get("labels", [])
            if isinstance(label_payload, dict)
        }
        raw_labels = payload.get("labels", payload.get("labels[]", []))
        if isinstance(raw_labels, str):
            raw_labels = [raw_labels]
        if isinstance(raw_labels, list):
            current_names = {str(label_name) for label_name in raw_labels}
        else:
            current_names.update(str(label_name) for label_name in payload.get("add", []))
            current_names.difference_update(str(label_name) for label_name in payload.get("remove", []))
        issue_payload["labels"] = [{"name": label_name} for label_name in sorted(current_names)]
        _save_scene_state(self.scene, state_payload)
        self._send_json(200, issue_payload["labels"])

    def _handle_label_create(self, payload: dict[str, Any]) -> None:
        """幂等创建服务端标签。"""
        state_payload = _load_scene_state(self.scene)
        label_rows = state_payload.get("labels", [])
        label_name = str(payload.get("name", ""))
        if isinstance(label_rows, list) and not any(
            isinstance(label_row, dict) and label_row.get("name") == label_name
            for label_row in label_rows
        ):
            label_rows.append({"name": label_name, "color": "cccccc", "description": ""})
        _save_scene_state(self.scene, state_payload)
        self._send_json(201, {"name": label_name})

    def _handle_pull_list(
        self,
        query: dict[str, list[str]],
        control_state: dict[str, Any],
    ) -> None:
        """返回 PR 列表，支持断连、查询故障和延迟可见。"""
        if control_state.get("drop_pull_read_after_agent") and self.scene.agent_log_path.is_file():
            self._drop_response("pull-read-socket-disconnected")
            return
        query_error_status = control_state.get("pull_query_error_status")
        query_error_is_active = not control_state.get("pull_query_error_after_create") or control_state.get(
            "pull_create_attempted"
        )
        if query_error_status and query_error_is_active:
            self._send_json(int(query_error_status), {"message": "Resource not accessible by integration"})
            return
        if (
            control_state.get("drop_pull_reads_after_create")
            and control_state.get("pull_create_attempted")
            and _load_scene_state(self.scene).get("pull_requests")
        ):
            self._drop_response("pull-read-socket-disconnected-after-create")
            return
        state_payload = _load_scene_state(self.scene)
        state_payload["pull_list_reads"] = int(state_payload.get("pull_list_reads", 0)) + 1
        _save_scene_state(self.scene, state_payload)
        pull_rows = _visible_pull_requests(self.scene, query)
        page_rows, pagination_headers = _paginate_rows(self, pull_rows, query)
        self._send_json(200, page_rows, headers=pagination_headers)

    def _handle_pull_create(
        self,
        payload: dict[str, Any],
        control_state: dict[str, Any],
    ) -> None:
        """模拟 REST PR 创建，供显式负控直接重复请求。"""
        self._create_pull_from_payload(payload, control_state)

    def _create_pull_from_payload(
        self,
        payload: dict[str, Any],
        control_state: dict[str, Any],
        *,
        graphql: bool = False,
    ) -> None:
        """按测试控制决定提交、提交后断连或拒绝 PR 创建。"""
        if control_state.get("pull_query_error_after_create") or control_state.get(
            "drop_pull_reads_after_create"
        ):
            control_state["pull_create_attempted"] = True
            _set_control(self.scene, pull_create_attempted=True)
        action = str(control_state.get("pull_create_action", "accept"))
        if action == "deny":
            self._send_json(403, {"message": "Resource not accessible by integration"})
            return
        if action == "drop_without_create":
            self._drop_response("pull-request-not-committed-socket-dropped")
            return
        title = str(payload.get("title", "Recovery fixture PR"))
        body = str(payload.get("body", ""))
        head_branch = str(payload.get("headRefName", payload.get("head", "issue-73"))).split(":", 1)[-1]
        base_branch = str(payload.get("baseRefName", payload.get("base", "main")))
        visibility_delay = int(control_state.get("pull_visibility_delay_reads", 0))
        pull_payload = _pull_payload(
            self.scene,
            title=title,
            body=body,
            head_branch=head_branch,
            base_branch=base_branch,
            visible_after_reads=visibility_delay,
        )
        if action == "persist_and_drop":
            self._drop_response("pull-request-persisted-then-socket-dropped")
            return
        if graphql:
            response_payload: object = {
                "data": {
                    "createPullRequest": {
                        "pullRequest": {
                            "number": pull_payload["number"],
                            "url": pull_payload["html_url"],
                            "isDraft": True,
                        }
                    }
                }
            }
        else:
            response_payload = pull_payload
        self._send_json(201, response_payload)

    def _handle_graphql(
        self,
        payload: dict[str, Any],
        control_state: dict[str, Any],
    ) -> None:
        """响应 gh pr list/create 与仓库查询所需的最小 GraphQL 字段。"""
        query_text = str(payload.get("query", ""))
        variables_payload = payload.get("variables", {})
        variables = variables_payload if isinstance(variables_payload, dict) else {}
        mutation_input = variables.get("input", {})
        mutation_input = mutation_input if isinstance(mutation_input, dict) else {}
        if "createPullRequest" in query_text:
            _append_request_log(
                self.scene,
                "POST",
                "/graphql#createPullRequest",
                "create-mutation",
            )
            self._create_pull_from_payload(mutation_input, control_state, graphql=True)
            return
        if "pullRequests" in query_text:
            if control_state.get("drop_pull_read_after_agent") and self.scene.agent_log_path.is_file():
                self._drop_response("graphql-pull-read-socket-disconnected")
                return
            visible_pulls = _visible_pull_requests(self.scene, {})
            nodes = [
                {
                    "number": pull_payload["number"],
                    "title": pull_payload["title"],
                    "state": str(pull_payload["state"]).upper(),
                    "url": pull_payload["html_url"],
                    "isDraft": pull_payload["draft"],
                    "mergedAt": pull_payload["merged_at"],
                    "headRefName": pull_payload["head"]["ref"],
                    "baseRefName": pull_payload["base"]["ref"],
                    "body": pull_payload["body"],
                }
                for pull_payload in visible_pulls
            ]
            self._send_json(
                200,
                {"data": {"repository": {"pullRequests": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}}},
            )
            return
        if "repository" in query_text:
            repository_payload: dict[str, Any] = {
                "id": "R_rv_fixture_repo",
                "name": "repo",
                "nameWithOwner": OWNER_REPOSITORY,
                "defaultBranchRef": {"name": "main"},
                "viewerPermission": "ADMIN",
                "issue": None,
                "pullRequest": None,
            }
            self._send_json(200, {"data": {"repository": repository_payload}})
            return
        if "viewer" in query_text:
            self._send_json(
                200,
                {"data": {"viewer": {"login": "rv-bot"}}},
            )
            return
        self._send_json(200, {"data": {}})

    def _find_pull(self, pull_number: int) -> dict[str, Any] | None:
        """按编号查询服务端 PR。"""
        pull_rows = _load_scene_state(self.scene).get("pull_requests", [])
        if not isinstance(pull_rows, list):
            return None
        return next(
            (
                pull_payload
                for pull_payload in pull_rows
                if isinstance(pull_payload, dict) and pull_payload.get("number") == pull_number
            ),
            None,
        )

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()


class _RecoveryTlsProxyHandler(BaseHTTPRequestHandler):
    """只将真实 gh 的 GitHub TLS 会话转交给测试侧 HTTPS forge。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_arguments: object) -> None:
        """关闭代理访问日志。"""

    def do_CONNECT(self) -> None:
        """在本机 TLS 端点终止 GitHub 请求，不建立外网连接。"""
        if self.path not in {"github.com:443", "api.github.com:443"}:
            self.send_error(502, "The recovery fixture blocks non-GitHub CONNECT targets.")
            return
        _append_request_log(self.server.scene, "CONNECT", self.path, "tunnel-requested")
        control_state = _load_control(self.server.scene)
        refusal_operation_kind = control_state.get("refuse_in_flight_operation_kind")
        if (
            refusal_operation_kind
            and not control_state.get("refused_in_flight_operation_connect")
            and _has_in_flight_operation(self.server.scene, str(refusal_operation_kind))
        ):
            _set_control(self.server.scene, refused_in_flight_operation_connect=True)
            _append_request_log(
                self.server.scene,
                "CONNECT",
                self.path,
                "origin-connect-refused-before-tls-and-api-request",
            )
            self.send_error(502, "connect: connection refused")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        tls_context = getattr(self.server, "tls_context")
        duplicated_connection = socket.fromfd(
            self.connection.fileno(),
            socket.AF_INET,
            socket.SOCK_STREAM,
        )
        try:
            secure_connection = tls_context.wrap_socket(duplicated_connection, server_side=True)
        except ssl.SSLError as tls_error:
            _append_request_log(
                self.server.scene,
                "CONNECT",
                self.path,
                f"tls-rejected-{tls_error.reason}",
            )
            duplicated_connection.close()
            return
        try:
            _append_request_log(self.server.scene, "CONNECT", self.path, "tls-established")
            _RecoveryForgeHandler(secure_connection, self.client_address, self.server)
        except (OSError, ValueError):
            pass
        finally:
            secure_connection.close()

    def do_GET(self) -> None:
        """拒绝不经 TLS CONNECT 的代理请求。"""
        self.send_error(405, "Only isolated GitHub HTTPS requests are supported.")


def _build_scene(scene_name: str, *, seed_comment_count: int = 0) -> RecoveryScene:
    """建立一个隔离 Git 仓库、HTTPS forge 和真实 runner 配置。"""
    root = Path(tempfile.mkdtemp(prefix=f"kc-rv-{scene_name}-"))
    home_path = root / "home"
    home_path.mkdir()
    state_home = home_path / ".kedacode"
    repository_path = root / "repository"
    repository_path.mkdir()
    bare_remote_path = root / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare_remote_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    _git(repository_path, "init", "--initial-branch=main")
    _git(repository_path, "config", "user.name", "RV Fixture")
    _git(repository_path, "config", "user.email", "rv-fixture@example.invalid")
    (repository_path / "README.md").write_text("Agent Runner recovery fixture.\n", encoding="utf-8")
    (repository_path / ".gitignore").write_text(
        ".kedacode/\n.agent-runner/\n.kc-worktrees/\n",
        encoding="utf-8",
    )
    fixture_prd_path = repository_path / FIXTURE_PRD_PATH
    fixture_prd_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_prd_path.write_text(
        "# PRD: 隔离恢复入口夹具\n\n"
        "- GitHub Issue: https://github.com/owner/repo/issues/73\n\n"
        "> ✅ **验收状态**：已验收 — 验收清单已全部完成。\n\n"
        "此文件仅用于真实入口 RV 的临时仓库，不是产品验收记录。\n\n"
        "## 9. Acceptance Checklist\n\n"
        "### Validation Acceptance\n\n"
        "- [x] rv-fixture: 临时仓库、远端与执行计划已建立。\n",
        encoding="utf-8",
    )
    archive_placeholder_path = repository_path / "tasks" / "archive" / ".gitkeep"
    archive_placeholder_path.parent.mkdir(parents=True, exist_ok=True)
    archive_placeholder_path.write_text("Fixture archive directory.\n", encoding="utf-8")
    _git(
        repository_path,
        "add",
        "README.md",
        ".gitignore",
        FIXTURE_PRD_PATH,
        "tasks/archive/.gitkeep",
    )
    _git(repository_path, "commit", "-m", "chore: initialize recovery fixture")
    _git(repository_path, "remote", "add", "origin", f"https://github.com/{OWNER_REPOSITORY}.git")
    _git(repository_path, "config", "remote.origin.pushurl", bare_remote_path.as_uri())
    _git(repository_path, "push", "--set-upstream", "origin", "main")

    agent_log_path = root / "agent-invocations.jsonl"
    verification_log_path = root / "verification-invocations.jsonl"
    request_log_path = root / "forge-requests.jsonl"
    agent_script_path = _write_agent_fixture(root, agent_log_path)
    verification_script_path = _write_verification_fixture(root, verification_log_path)
    config_path, database_path = _write_repository_config(
        repository_path,
        state_home,
        agent_script_path,
        verification_script_path,
        OWNER_REPOSITORY,
    )

    forge_state_path = root / "forge-state.json"
    forge_state = _new_forge_state()
    if seed_comment_count:
        forge_state["issue_comments"] = [
            {
                "id": comment_id,
                "body": f"Fixture page-boundary comment {comment_id}",
                "user": {"id": 2, "login": "rv-reviewer"},
                "html_url": f"https://github.com/{OWNER_REPOSITORY}/issues/{ISSUE_NUMBER}#issuecomment-{comment_id}",
                "created_at": "2026-11-27T00:00:00Z",
                "updated_at": "2026-11-27T00:00:00Z",
            }
            for comment_id in range(1, seed_comment_count + 1)
        ]
        forge_state["next_comment_id"] = seed_comment_count + 1
    _write_json(forge_state_path, forge_state)
    control_path = root / "forge-control.json"
    _write_json(control_path, {})
    certificate_path = _make_certificate(root)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecoveryTlsProxyHandler)
    setattr(server, "scene", None)
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(certificate_path, root / "forge-key.pem")
    tls_context.set_alpn_protocols(["http/1.1"])
    setattr(server, "tls_context", tls_context)
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    server_thread.start()

    kc_command = shutil.which("kc") or str(Path(sys.prefix) / "bin" / "kc")
    if not Path(kc_command).is_file():
        server.shutdown()
        server.server_close()
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("The real kc CLI is not available in the current uv environment.")
    environment = os.environ.copy()
    prd_skill_path = environment.get("KEDACODE_PRD_SKILL_PATH")
    if not prd_skill_path:
        codex_home = Path(environment.get("CODEX_HOME", Path.home() / ".codex"))
        candidate_skill_path = codex_home / "skills" / "prd" / "SKILL.md"
        if candidate_skill_path.is_file():
            prd_skill_path = str(candidate_skill_path)
    environment.update(
        {
            "HOME": str(home_path),
            "KEDACODE_CONFIG": str(config_path),
            "KEDACODE_NO_UPDATE_CHECK": "1",
            "GH_HOST": "github.com",
            "HTTPS_PROXY": f"http://127.0.0.1:{server.server_port}",
            "https_proxy": f"http://127.0.0.1:{server.server_port}",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "GH_TOKEN": FIXTURE_TOKEN,
            "SSL_CERT_FILE": str(certificate_path),
            "GIT_SSL_CAINFO": str(certificate_path),
            "RV_SCENE_ROOT": str(root),
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        }
    )
    if prd_skill_path:
        environment["KEDACODE_PRD_SKILL_PATH"] = prd_skill_path
    scene = RecoveryScene(
        root=root,
        repository_path=repository_path,
        state_home=state_home,
        database_path=database_path,
        bare_remote_path=bare_remote_path,
        control_path=control_path,
        forge_state_path=forge_state_path,
        agent_log_path=agent_log_path,
        verification_log_path=verification_log_path,
        request_log_path=request_log_path,
        certificate_path=certificate_path,
        server=server,
        server_thread=server_thread,
        environment=environment,
        kc_command=kc_command,
        server_port=server.server_port,
    )
    setattr(server, "scene", scene)
    return scene
