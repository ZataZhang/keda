"""Gitea 1.21 真服务端客户端验收。"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import LabelConfig
from backend.infrastructure import gitea_client as gitea_client_module
from backend.infrastructure.gitea_client import GiteaApiClient, GiteaRepositoryBinding
from backend.infrastructure.process_runner import SubprocessRunner

_COVERED_ABSTRACT_OPERATIONS = frozenset(
    {
        "close_issue",
        "comment_issue",
        "comment_pr",
        "create_draft_pr",
        "create_issue",
        "delete_issue_comment",
        "edit_issue_body",
        "edit_issue_comment",
        "edit_issue_labels",
        "find_merged_pr_by_head",
        "find_open_pr_by_head",
        "get_issue",
        "get_pull_request_context",
        "get_remote_base_sha",
        "issue_supervision_lock",
        "list_issue_comment_entries",
        "list_issue_comments",
        "list_issues_by_label",
        "list_pr_comments",
        "list_pull_requests_for_issue",
        "list_ready_issues",
        "list_review_candidate_issues",
        "list_rework_prd_issues",
        "merge_pull_request",
        "presence_creation_lock",
        "sync_labels",
        "update_pull_request_body",
    }
)


@dataclass(frozen=True)
class LiveGiteaRepository:
    """真实 Gitea 仓库、本地 Git 检出与接口客户端。"""

    base_url: str
    token: str = field(repr=False)
    owner: str
    repository: str
    owner_repo: str
    repo_path: Path
    client: GiteaApiClient

    def api(self, method: str, endpoint_path: str, **kwargs: object) -> requests.Response:
        """使用独立 requests 调用读取或准备真实 Gitea 状态。"""
        return requests.request(
            method,
            f"{self.base_url}/api/v1/{endpoint_path.lstrip('/')}",
            headers={"Authorization": f"token {self.token}"},
            timeout=(5, 30),
            **kwargs,
        )


def _git(repository_path: Path, *arguments: str) -> str:
    """在 live test 仓库运行真实 Git 命令。"""
    git_process = subprocess.run(
        ["git", "-c", "credential.helper=", *arguments],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return git_process.stdout.strip()


def _start_feature_branch(
    live_repository: LiveGiteaRepository,
    branch_name: str,
    file_name: str,
) -> None:
    """创建并推送一个真实 head branch，供 Gitea PR API 读取。"""
    repository_path = live_repository.repo_path
    _git(repository_path, "switch", "-c", branch_name)
    (repository_path / file_name).write_text(
        f"live Gitea change for {branch_name}\n",
        encoding="utf-8",
    )
    _git(repository_path, "add", file_name)
    _git(repository_path, "commit", "-m", f"Add {file_name}")
    _git(repository_path, "push", "--set-upstream", "origin", branch_name)
    time.sleep(3)
    branch_list_response = live_repository.api(
        "GET",
        f"repos/{live_repository.owner_repo}/branches",
    )
    branch_list_response.raise_for_status()
    branch_payloads = branch_list_response.json()
    assert isinstance(branch_payloads, list)
    assert any(
        isinstance(branch_payload, dict) and branch_payload.get("name") == branch_name
        for branch_payload in branch_payloads
    ), f"Gitea did not expose pushed branch {branch_name!r} via its API."


def _issue_number(issue_url: str) -> int:
    """从 Gitea HTML URL 读取 Issue 编号。"""
    issue_number = urlsplit(issue_url).path.rstrip("/").split("/")[-1]
    assert issue_number.isdigit()
    return int(issue_number)


def _pull_number(pull_url: str) -> int:
    """从 Gitea HTML URL 读取 PR 编号。"""
    pull_number = urlsplit(pull_url).path.rstrip("/").split("/")[-1]
    assert pull_number.isdigit()
    return int(pull_number)


@pytest.fixture
def live_gitea_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> LiveGiteaRepository:
    """创建固定运行在真实 Gitea 1.21 的临时仓库和 Git 检出。"""
    base_url = os.environ.get("RV_GITEA_BASE_URL", "").rstrip("/")
    token = os.environ.get("RV_GITEA_TOKEN", "")
    username = os.environ.get("RV_GITEA_USERNAME", "")
    if not base_url or not token or not username:
        pytest.skip("Set RV_GITEA_BASE_URL, RV_GITEA_USERNAME and RV_GITEA_TOKEN for live Gitea.")

    version_response = requests.get(f"{base_url}/api/v1/version", timeout=(5, 30))
    version_response.raise_for_status()
    assert str(version_response.json().get("version", "")).startswith("1.21.")

    api_headers = {"Authorization": f"token {token}"}
    user_response = requests.get(
        f"{base_url}/api/v1/user",
        headers=api_headers,
        timeout=(5, 30),
    )
    user_response.raise_for_status()
    owner = str(user_response.json()["login"])
    repository = f"rv-{uuid.uuid4().hex[:12]}"
    create_repository_response = requests.post(
        f"{base_url}/api/v1/user/repos",
        headers=api_headers,
        json={"name": repository, "private": False},
        timeout=(5, 30),
    )
    create_repository_response.raise_for_status()

    repo_path = tmp_path / "repository"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("Gitea live adapter fixture\n", encoding="utf-8")
    _git(repo_path, "init", "--initial-branch=main")
    _git(repo_path, "config", "user.name", "KedaCode Live Test")
    _git(repo_path, "config", "user.email", "keda-live-test@example.invalid")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "-m", "Create live test base")
    remote_url = f"{base_url}/{owner}/{repository}.git"
    _git(repo_path, "remote", "add", "origin", remote_url)

    askpass_path = tmp_path / "git-askpass.sh"
    askpass_path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  *Username*) printf '%s\\n' \"$RV_GITEA_USERNAME\" ;;\n"
        "  *Password*) printf '%s\\n' \"$RV_GITEA_TOKEN\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass_path.chmod(0o700)
    monkeypatch.setenv("GIT_ASKPASS", str(askpass_path))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    _git(repo_path, "push", "--set-upstream", "origin", "main")

    registered_hostname = urlsplit(base_url).hostname or "127.0.0.1"
    monkeypatch.setattr(gitea_client_module, "state_home", lambda: tmp_path / "state")
    client = GiteaApiClient(
        GiteaRepositoryBinding(
            repo_path=repo_path,
            base_url=base_url,
            host=registered_hostname,
            owner_repo=f"{owner}/{repository}",
        ),
        token=token,
        process_runner=SubprocessRunner(),
    )
    return LiveGiteaRepository(
        base_url=base_url,
        token=token,
        owner=owner,
        repository=repository,
        owner_repo=f"{owner}/{repository}",
        repo_path=repo_path,
        client=client,
    )


def test_live_gitea_client_covers_every_interface_operation(
    live_gitea_repository: LiveGiteaRepository,
) -> None:
    """逐项调用客户端接口，并用新 HTTP 请求读取 Gitea 已保存的结果。"""
    assert _COVERED_ABSTRACT_OPERATIONS == IGitHubClient.__abstractmethods__
    client = live_gitea_repository.client
    repository = live_gitea_repository
    assert not hasattr(client, "_token")
    assert repository.token not in repr(client)
    labels = LabelConfig()
    client.sync_labels(labels)
    independent_labels_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/labels",
    )
    independent_labels_response.raise_for_status()
    repository_label_names = {
        label_payload["name"] for label_payload in independent_labels_response.json()
    }
    assert labels.ready in repository_label_names
    assert labels.rework_prd in repository_label_names

    ready_issue_url = client.create_issue(
        title="Live ready issue",
        body="Initial issue body.",
        labels=(labels.ready,),
    )
    ready_issue_number = _issue_number(ready_issue_url)
    rework_issue_url = client.create_issue(
        title="Live rework issue",
        body="Second issue for label filtering.",
        labels=(labels.rework_prd,),
    )
    rework_issue_number = _issue_number(rework_issue_url)
    assert ready_issue_url.startswith(f"{repository.base_url}/{repository.owner_repo}/issues/")
    assert [issue.number for issue in client.list_ready_issues(labels.ready, 10)] == [
        ready_issue_number
    ]
    assert [issue.number for issue in client.list_rework_prd_issues(labels.rework_prd, 10)] == [
        rework_issue_number
    ]
    review_candidates = client.list_review_candidate_issues(
        (labels.ready, labels.rework_prd),
        10,
    )
    assert {issue.number for issue in review_candidates} == {
        ready_issue_number,
        rework_issue_number,
    }
    assert [
        issue.number for issue in client.list_issues_by_label(labels.ready, 10, state="open")
    ] == [ready_issue_number]
    assert client.get_issue(ready_issue_number).title == "Live ready issue"
    assert any(
        issue.number == ready_issue_number for issue in client.list_repository_items(state="open")
    )

    client.edit_issue_labels(
        ready_issue_number,
        add=(labels.running,),
        remove=(labels.ready,),
    )
    independent_issue_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/issues/{ready_issue_number}",
    )
    independent_issue_response.raise_for_status()
    independent_issue = independent_issue_response.json()
    assert labels.running in {
        label_payload["name"] for label_payload in independent_issue["labels"]
    }
    assert labels.ready not in {
        label_payload["name"] for label_payload in independent_issue["labels"]
    }

    client.edit_issue_body(ready_issue_number, "Edited issue body.")
    independent_issue_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/issues/{ready_issue_number}",
    )
    independent_issue_response.raise_for_status()
    assert independent_issue_response.json()["body"] == "Edited issue body."
    client.comment_issue(ready_issue_number, "Initial Gitea comment.")
    issue_comment_entries = client.list_issue_comment_entries(
        ready_issue_number,
        require_success=True,
    )
    assert issue_comment_entries
    comment_id = issue_comment_entries[0][0]
    assert "Initial Gitea comment." in client.list_issue_comments(
        ready_issue_number,
        require_success=True,
    )
    client.edit_issue_comment(comment_id, "Edited Gitea comment.")
    independent_comment_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/issues/comments/{comment_id}",
    )
    independent_comment_response.raise_for_status()
    assert independent_comment_response.json()["body"] == "Edited Gitea comment."
    client.delete_issue_comment(comment_id)
    deleted_comment_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/issues/comments/{comment_id}",
    )
    assert deleted_comment_response.status_code == 404

    with client.presence_creation_lock():
        assert (Path(repository.repo_path) / "README.md").is_file()
    with client.issue_supervision_lock(ready_issue_number) as lock_acquired:
        assert lock_acquired is True

        def try_acquiring_duplicate_lock() -> bool:
            with client.issue_supervision_lock(ready_issue_number) as duplicate_lock_acquired:
                return duplicate_lock_acquired

        with ThreadPoolExecutor(max_workers=1) as lock_executor:
            duplicate_lock_acquired = lock_executor.submit(try_acquiring_duplicate_lock).result(
                timeout=5
            )
        assert duplicate_lock_acquired is False

    branch_name = f"rv-feature-{uuid.uuid4().hex[:8]}"
    _start_feature_branch(repository, branch_name, "feature.txt")
    assert client.get_remote_base_sha("origin", "main")
    client.comment_issue(ready_issue_number, "Runner claimed this Issue.")
    pull_request_url = client.create_draft_pr(
        title="Live adapter pull request",
        body=f"Closes #{ready_issue_number}\n\nLive Gitea operation test.",
        base_branch="main",
        cwd=repository.repo_path,
    )
    pull_request_number = _pull_number(pull_request_url)
    independent_pull_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/pulls/{pull_request_number}",
    )
    independent_pull_response.raise_for_status()
    independent_pull_request = independent_pull_response.json()
    assert independent_pull_request["title"].startswith("WIP: ")
    assert "Closes #" in independent_pull_request["body"]
    assert client.find_open_pr_by_head(branch_name, require_success=True) == pull_request_url
    draft_context = client.get_pull_request_context(branch_name, require_success=True)
    assert draft_context is not None
    assert draft_context.is_draft is True
    assert draft_context.mergeable is None
    number_context = client.get_pull_request_context_by_number(
        pull_request_number,
        require_success=True,
    )
    assert number_context is not None and number_context.number == pull_request_number
    client.comment_pr(pull_request_number, "Pull request comment.")
    assert "Pull request comment." in client.list_pr_comments(pull_request_number)
    client.update_pull_request_body(
        pull_request_number,
        f"Closes #{ready_issue_number}\n\nUpdated Gitea PR body.",
    )
    client.mark_pull_request_ready(pull_request_number)
    independent_pull_response = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/pulls/{pull_request_number}",
    )
    independent_pull_response.raise_for_status()
    ready_pull_request = independent_pull_response.json()
    assert not ready_pull_request["title"].startswith("WIP: ")
    assert "Updated Gitea PR body." in ready_pull_request["body"]
    assert client.get_pull_request_context(branch_name, require_success=True).is_draft is False
    linked_pull_requests = client.list_pull_requests_for_issue(
        repository.owner_repo,
        ready_issue_number,
    )
    assert any(pull_request.number == pull_request_number for pull_request in linked_pull_requests)

    client.close_issue(rework_issue_number)
    independently_closed_issue = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/issues/{rework_issue_number}",
    )
    independently_closed_issue.raise_for_status()
    assert independently_closed_issue.json()["state"] == "closed"
    client.merge_pull_request(pull_request_number, method="squash")
    independently_merged_pull = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/pulls/{pull_request_number}",
    )
    independently_merged_pull.raise_for_status()
    assert independently_merged_pull.json().get("merged_at")
    assert client.find_merged_pr_by_head(branch_name, require_success=True) == pull_request_url
    independently_closed_linked_issue = repository.api(
        "GET",
        f"repos/{repository.owner_repo}/issues/{ready_issue_number}",
    )
    independently_closed_linked_issue.raise_for_status()
    assert independently_closed_linked_issue.json()["state"] == "closed"


def test_live_gitea_status_aggregation_fails_closed_and_names_failed_check(
    live_gitea_repository: LiveGiteaRepository,
) -> None:
    """从真实 Gitea commit status API 分别读取 failure、success 与 unknown。"""
    repository = live_gitea_repository
    client = repository.client
    status_cases = (
        ("failure", "rv-failing-check", "failure"),
        ("success", "rv-success-check", "success"),
        ("unknown", "rv-unknown-check", None),
    )
    for case_name, check_name, check_state in status_cases:
        branch_name = f"rv-status-{case_name}-{uuid.uuid4().hex[:6]}"
        _start_feature_branch(repository, branch_name, f"{case_name}.txt")
        pull_request_url = client.create_draft_pr(
            title=f"Status aggregation {case_name}",
            body="Live status aggregation test.",
            base_branch="main",
            cwd=repository.repo_path,
        )
        pull_request_number = _pull_number(pull_request_url)
        independent_pull_response = repository.api(
            "GET",
            f"repos/{repository.owner_repo}/pulls/{pull_request_number}",
        )
        independent_pull_response.raise_for_status()
        head_sha = independent_pull_response.json()["head"]["sha"]
        if check_state is not None:
            create_status_response = repository.api(
                "POST",
                f"repos/{repository.owner_repo}/statuses/{head_sha}",
                json={
                    "state": check_state,
                    "context": check_name,
                    "description": f"{case_name} status fixture",
                },
            )
            create_status_response.raise_for_status()

        fresh_client = GiteaApiClient(
            GiteaRepositoryBinding(
                repo_path=repository.repo_path,
                base_url=repository.base_url,
                host=urlsplit(repository.base_url).hostname or "127.0.0.1",
                owner_repo=repository.owner_repo,
            ),
            token=repository.token,
            process_runner=SubprocessRunner(),
        )
        pull_context = fresh_client.get_pull_request_context(branch_name, require_success=True)
        assert pull_context is not None
        if case_name == "failure":
            with pytest.raises(AssertionError):
                assert pull_context.checks_state == "SUCCESS"
            assert pull_context.checks_state == "FAILURE"
            assert any(check_name in summary_line for summary_line in pull_context.checks_summary)
        elif case_name == "success":
            assert pull_context.checks_state == "SUCCESS"
            assert pull_context.checks_summary == ()
        else:
            assert pull_context.checks_state is None
            assert pull_context.checks_summary == ()
