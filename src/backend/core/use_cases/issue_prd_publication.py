"""把 PRD 安全发布到对应 Issue 的远端任务分支。"""

from __future__ import annotations

from backend.core.use_cases.issue_workspace import issue_branch_name

import logging
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import uuid4

from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import IssueSummary, LabelConfig
from backend.core.use_cases.agent_runner_feedback import extract_prd_path
from backend.core.use_cases.create_issue_from_prd_helpers import (
    ISSUE_LINK_LINE_RE,
    resolve_prd_paths,
    write_issue_link,
)
from backend.core.use_cases.issue_prd_github_link import parse_github_slug_from_url
from backend.core.use_cases.issue_prd_github_link import pin_prd_github_link

_logger = logging.getLogger(__name__)
_PUBLICATION_MARKER_PREFIX = "<!-- kc:prd-publication"
_PUBLICATION_MARKER_RE = re.compile(
    r"<!-- kc:prd-publication version=1 state=(?P<state>pending|published) "
    r"queue_ready=(?P<queue_ready>true|false)"
    r"(?: branch=(?P<branch>(?:kc/(?:[A-Za-z0-9_.%_-]+/){2,3})?issue-\d+) commit=(?P<commit>[0-9a-f]{7,64}))? -->"
)
_ISSUE_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$", re.I)
_PRD_PATH_ANCHOR_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?PRD path:")


@dataclass(frozen=True)
class IssuePrdPublicationRequest:
    """恢复一个已创建 Issue 的 PRD 发布所需信息。"""

    repo_path: Path
    prd_path: Path
    issue_number: int
    git_remote: str
    git_base_branch: str
    queue_ready: bool | None = None
    labels_config: LabelConfig | None = None


@dataclass(frozen=True)
class IssuePrdPublicationResult:
    """已发布 PRD 的远端提交和 Issue 状态。"""

    issue_url: str
    branch: str
    commit_sha: str
    ready: bool


@dataclass(frozen=True)
class PrdPublicationMetadata:
    """Issue 正文中可恢复的 PRD 发布状态。"""

    state: str
    queue_ready: bool
    branch: str | None = None
    commit_sha: str | None = None


class PrdPublicationConflictError(RuntimeError):
    """远端任务分支上的 PRD 与本次目标内容不一致。"""


def validate_issue_prd_path(repo_path: Path, prd_path: Path) -> tuple[Path, Path]:
    """校验 PRD 是仓库内的 Markdown 文件并返回绝对、相对路径。"""
    absolute_prd_path, relative_prd_path = resolve_prd_paths(repo_path, prd_path)
    if relative_prd_path.suffix.lower() != ".md":
        raise ValueError(f"PRD publication requires a Markdown file: {relative_prd_path}")
    relative_path_text = relative_prd_path.as_posix()
    if "/" not in relative_path_text or any(char.isspace() for char in relative_path_text):
        raise ValueError(
            "PRD publication path must include a directory and contain no whitespace so the "
            "runner can parse its Issue PRD anchor."
        )
    if not absolute_prd_path.is_file():
        raise ValueError(f"PRD file does not exist: {relative_prd_path}")
    return absolute_prd_path, relative_prd_path


def pending_prd_publication_marker(*, queue_ready: bool) -> str:
    """生成新建 Issue 时记录 ready 意图的隐藏 marker。"""
    ready_text = "true" if queue_ready else "false"
    return f"<!-- kc:prd-publication version=1 state=pending queue_ready={ready_text} -->"


def ensure_issue_prd_path_anchor(issue_body: str, relative_prd_path: Path) -> str:
    """确保新 Issue 正文只有一个指向目标 PRD 的机器锚点。"""
    expected_path = relative_prd_path.as_posix()
    anchor_lines = [
        body_line
        for body_line in issue_body.splitlines()
        if _PRD_PATH_ANCHOR_LINE_RE.match(body_line)
    ]
    if len(anchor_lines) == 1 and extract_prd_path(issue_body) == expected_path:
        return issue_body

    canonical_anchor = f"- PRD path: `{expected_path}`"
    body_lines = [
        body_line
        for body_line in issue_body.splitlines()
        if not _PRD_PATH_ANCHOR_LINE_RE.match(body_line)
    ]
    if anchor_lines:
        first_anchor_position = next(
            position
            for position, body_line in enumerate(issue_body.splitlines())
            if _PRD_PATH_ANCHOR_LINE_RE.match(body_line)
        )
        insertion_position = sum(
            1
            for body_line in issue_body.splitlines()[:first_anchor_position]
            if not _PRD_PATH_ANCHOR_LINE_RE.match(body_line)
        )
        body_lines.insert(insertion_position, canonical_anchor)
    else:
        if body_lines and body_lines[-1].strip():
            body_lines.extend(["", "## Canonical PRD", ""])
        body_lines.append(canonical_anchor)
    return "\n".join(body_lines).rstrip() + "\n"


def parse_prd_publication_metadata(issue_body: str) -> PrdPublicationMetadata | None:
    """读取 Issue 正文中的 PRD 发布 marker，拒绝未知版本或损坏数据。"""
    marker_lines = [
        body_line.strip()
        for body_line in issue_body.splitlines()
        if _PUBLICATION_MARKER_PREFIX in body_line
    ]
    if not marker_lines:
        return None
    if len(marker_lines) != 1:
        raise ValueError("Issue body contains multiple PRD publication markers.")
    marker_match = _PUBLICATION_MARKER_RE.fullmatch(marker_lines[0])
    if marker_match is None:
        raise ValueError("Issue body contains an unsupported PRD publication marker.")
    if marker_match.group("state") == "published" and not (
        marker_match.group("branch") and marker_match.group("commit")
    ):
        raise ValueError("Published PRD marker is missing its branch or commit SHA.")
    return PrdPublicationMetadata(
        state=marker_match.group("state"),
        queue_ready=marker_match.group("queue_ready") == "true",
        branch=marker_match.group("branch"),
        commit_sha=marker_match.group("commit"),
    )


def _published_issue_prd_reference(
    *,
    issue_number: int,
    issue_body: str,
    issue_url: str = "",
) -> tuple[str, str] | None:
    """验证发布 marker 并返回固定的任务分支和提交。"""
    publication_metadata = parse_prd_publication_metadata(issue_body)
    if publication_metadata is None or publication_metadata.state != "published":
        return None
    expected_branch = issue_branch_name(IssueSummary(issue_number, "", issue_url, "", ()))
    if publication_metadata.branch not in {expected_branch, f"issue-{issue_number}"}:
        raise ValueError(
            f"Issue #{issue_number} publication marker must point to "
            f"'{expected_branch}', got {publication_metadata.branch!r}."
        )
    if publication_metadata.commit_sha is None:
        raise ValueError(f"Issue #{issue_number} published PRD marker is missing its commit SHA.")
    return publication_metadata.branch, publication_metadata.commit_sha


def published_issue_prd_branch(
    *, issue_number: int, issue_body: str, issue_url: str = ""
) -> str | None:
    """返回已发布 PRD 的分支，核对完整仓库身份并兼容旧发布标记。

    Args:
        issue_number: 当前任务编号。
        issue_body: 含发布标记的 Issue 正文。
        issue_url: 当前任务完整 URL；旧离线调用可省略。

    Returns:
        已发布分支名；尚未发布时返回 None。

    Raises:
        ValueError: 发布标记损坏，或分支不属于当前任务。
    """
    published_reference = _published_issue_prd_reference(
        issue_number=issue_number,
        issue_body=issue_body,
        issue_url=issue_url,
    )
    return published_reference[0] if published_reference is not None else None


def read_published_issue_prd_text(
    *,
    issue: IssueSummary,
    repo_path: Path,
    git_remote: str,
    process_runner: IProcessRunner,
) -> str | None:
    """从发布 marker 固定的提交读取 PRD，供调度前解析 lifecycle 覆盖。"""
    published_reference = _published_issue_prd_reference(
        issue_number=issue.number,
        issue_body=issue.body,
        issue_url=issue.url,
    )
    if published_reference is None:
        return None
    issue_branch, published_commit_sha = published_reference

    prd_anchor = extract_prd_path(issue.body)
    normalized_prd_anchor = _normalize_prd_anchor(prd_anchor or "")
    if not normalized_prd_anchor or not normalized_prd_anchor.lower().endswith(".md"):
        raise ValueError(
            f"Issue #{issue.number} has an unsafe or invalid PRD path anchor: {prd_anchor!r}."
        )

    remote_ref = f"refs/remotes/{git_remote}/{issue_branch}"
    fetch_result = process_runner.run(
        ["git", "fetch", git_remote, f"+{issue_branch}:{remote_ref}"],
        cwd=repo_path,
        check=False,
    )
    if fetch_result.return_code != 0:
        raise RuntimeError(
            f"Could not fetch published PRD branch '{git_remote}/{issue_branch}' "
            f"for Issue #{issue.number}: {fetch_result.stderr.strip()}"
        )

    prd_show_result = process_runner.run(
        ["git", "show", f"{published_commit_sha}:{normalized_prd_anchor}"],
        cwd=repo_path,
        check=False,
    )
    if prd_show_result.return_code != 0:
        raise RuntimeError(
            f"Published PRD '{normalized_prd_anchor}' at commit '{published_commit_sha}' "
            f"could not be read from '{git_remote}/{issue_branch}' for Issue #{issue.number}: "
            f"{prd_show_result.stderr.strip()}"
        )
    return prd_show_result.stdout


def recover_issue_prd_publication(
    *,
    request: IssuePrdPublicationRequest,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> IssuePrdPublicationResult:
    """把本地 PRD 发布到已有 Issue 的 ``issue-<N>`` 分支并修复元数据。"""
    if request.issue_number < 1:
        raise ValueError("Issue number must be a positive integer.")
    absolute_prd_path, relative_prd_path = validate_issue_prd_path(
        request.repo_path, request.prd_path
    )
    issue = github_client.get_issue(request.issue_number)
    if issue.number != request.issue_number:
        raise ValueError(
            f"GitHub returned Issue #{issue.number} when Issue #{request.issue_number} was requested."
        )
    if issue.state.upper() != "OPEN":
        raise ValueError(f"Issue #{issue.number} is {issue.state.lower()} and cannot be recovered.")

    github_repo_slug = _resolve_github_repo_slug(
        request.repo_path, request.git_remote, process_runner
    )
    issue_repo_slug, issue_url_number = _parse_issue_url(issue.url)
    if issue_repo_slug.lower() != github_repo_slug.lower():
        raise ValueError(
            f"Issue #{issue.number} belongs to {issue_repo_slug}, but Git remote "
            f"'{request.git_remote}' points to {github_repo_slug}."
        )
    if issue_url_number != request.issue_number:
        raise ValueError(f"Issue URL {issue.url} does not identify Issue #{request.issue_number}.")

    issue_prd_anchor = extract_prd_path(issue.body)
    if issue_prd_anchor is None or _normalize_prd_anchor(issue_prd_anchor) != (
        relative_prd_path.as_posix()
    ):
        raise ValueError(
            f"Issue #{issue.number} PRD path anchor does not match "
            f"'{relative_prd_path.as_posix()}'."
        )

    publication_metadata = parse_prd_publication_metadata(issue.body)
    if request.queue_ready is not None:
        queue_ready = request.queue_ready
    elif publication_metadata is not None:
        queue_ready = publication_metadata.queue_ready
    else:
        # 旧版 kc issue create 默认创建 ready Issue，恢复入口沿用该默认意图。
        queue_ready = True

    prd_text = absolute_prd_path.read_text(encoding="utf-8")
    _validate_or_restore_issue_link(
        prd_text=prd_text,
        absolute_prd_path=absolute_prd_path,
        issue=issue,
        expected_repo_slug=github_repo_slug,
    )
    prd_text = absolute_prd_path.read_text(encoding="utf-8")
    return _publish_and_finalize_issue_prd(
        issue=issue,
        prd_text=prd_text,
        relative_prd_path=relative_prd_path,
        repo_path=request.repo_path,
        git_remote=request.git_remote,
        git_base_branch=request.git_base_branch,
        github_repo_slug=github_repo_slug,
        queue_ready=queue_ready,
        labels_config=request.labels_config or LabelConfig(),
        github_client=github_client,
        process_runner=process_runner,
    )


def publish_prd_for_created_issue(
    *,
    issue: IssueSummary,
    absolute_prd_path: Path,
    relative_prd_path: Path,
    repo_path: Path,
    git_remote: str,
    git_base_branch: str,
    queue_ready: bool,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
    labels_config: LabelConfig | None = None,
) -> IssuePrdPublicationResult:
    """将刚创建 Issue 对应的 PRD 发布到其任务分支。"""
    try:
        github_repo_slug = _resolve_github_repo_slug(repo_path, git_remote, process_runner)
        issue_repo_slug, issue_url_number = _parse_issue_url(issue.url)
        if issue_repo_slug.lower() != github_repo_slug.lower() or issue_url_number != issue.number:
            raise ValueError(
                f"Created Issue URL {issue.url} does not match configured GitHub repository "
                f"{github_repo_slug} and Issue #{issue.number}."
            )
        if _normalize_prd_anchor(extract_prd_path(issue.body) or "") != (
            relative_prd_path.as_posix()
        ):
            raise ValueError(
                f"Created Issue #{issue.number} PRD path anchor does not match "
                f"'{relative_prd_path.as_posix()}'."
            )
        write_issue_link(
            prd_text=absolute_prd_path.read_text(encoding="utf-8"),
            absolute_prd_path=absolute_prd_path,
            issue_url=issue.url,
            force=False,
        )
        prd_text = absolute_prd_path.read_text(encoding="utf-8")
        return _publish_and_finalize_issue_prd(
            issue=issue,
            prd_text=prd_text,
            relative_prd_path=relative_prd_path,
            repo_path=repo_path,
            git_remote=git_remote,
            git_base_branch=git_base_branch,
            github_repo_slug=github_repo_slug,
            queue_ready=queue_ready,
            labels_config=labels_config or LabelConfig(),
            github_client=github_client,
            process_runner=process_runner,
        )
    except Exception as exc:  # noqa: BLE001 - Issue 已创建，错误必须保留可恢复入口。
        if "Resume this Issue with:" in str(exc):
            raise
        issue_state = issue.state.lower()
        recovery_command = (
            f"kc issue create {shlex.quote(relative_prd_path.as_posix())} "
            f"--recover-issue {issue.number} "
            f"{'--ready' if queue_ready else '--no-ready'}"
        )
        raise RuntimeError(
            f"Issue #{issue.number} ({issue.url}) is {issue_state} and is not marked ready; "
            "its PRD publication did not complete. Resume this Issue with: "
            f"{recovery_command}. Cause: {exc}"
        ) from exc


def _publish_and_finalize_issue_prd(
    *,
    issue: IssueSummary,
    prd_text: str,
    relative_prd_path: Path,
    repo_path: Path,
    git_remote: str,
    git_base_branch: str,
    github_repo_slug: str,
    queue_ready: bool,
    labels_config: LabelConfig,
    github_client: IGitHubClient,
    process_runner: IProcessRunner,
) -> IssuePrdPublicationResult:
    """发布目标 PRD、固定 Issue 链接并在最后恢复 ready 标签。"""
    issue_branch = published_issue_prd_branch(
        issue_number=issue.number, issue_body=issue.body, issue_url=issue.url
    ) or issue_branch_name(issue)
    try:
        current_metadata = parse_prd_publication_metadata(issue.body)
        if current_metadata is not None and current_metadata.state == "published":
            published_issue_prd_branch(
                issue_number=issue.number,
                issue_body=issue.body,
                issue_url=issue.url,
            )
        publication_is_current = (
            current_metadata is not None
            and current_metadata.state == "published"
            and current_metadata.branch == issue_branch
            and current_metadata.queue_ready == queue_ready
        )
        if (not queue_ready or not publication_is_current) and labels_config.ready in issue.labels:
            github_client.edit_issue_labels(issue.number, remove=[labels_config.ready])
            issue = replace(
                issue,
                labels=tuple(
                    label_name for label_name in issue.labels if label_name != labels_config.ready
                ),
            )
        if not publication_is_current:
            pending_issue_body = _replace_publication_marker(
                issue.body,
                PrdPublicationMetadata(state="pending", queue_ready=queue_ready),
            )
            if pending_issue_body != issue.body:
                github_client.edit_issue_body(issue.number, pending_issue_body)
                issue = replace(issue, body=pending_issue_body)
        commit_sha = _publish_prd_commit(
            repo_path=repo_path,
            relative_prd_path=relative_prd_path,
            prd_text=prd_text,
            issue_number=issue.number,
            issue_branch=issue_branch,
            git_remote=git_remote,
            git_base_branch=git_base_branch,
            process_runner=process_runner,
        )
        if current_metadata is not None and current_metadata.state == "published":
            previous_prd_result = process_runner.run(
                [
                    "git",
                    "show",
                    f"{current_metadata.commit_sha}:{relative_prd_path.as_posix()}",
                ],
                cwd=repo_path,
                check=False,
            )
            if previous_prd_result.return_code != 0 or previous_prd_result.stdout == prd_text:
                # 原发布提交仍是本次 PRD 快照；分支后续提交不应让恢复操作改写固定链接。
                commit_sha = current_metadata.commit_sha or commit_sha
        pinned_issue_body = pin_prd_github_link(
            issue.body,
            relative_prd_path=relative_prd_path,
            github_repo_slug=github_repo_slug,
            commit_sha=commit_sha,
        )
        published_issue_body = _replace_publication_marker(
            pinned_issue_body,
            PrdPublicationMetadata(
                state="published",
                queue_ready=queue_ready,
                branch=issue_branch,
                commit_sha=commit_sha,
            ),
        )
        github_client.edit_issue_body(issue.number, published_issue_body)
        if queue_ready:
            github_client.edit_issue_labels(issue.number, add=[labels_config.ready])
        elif labels_config.ready in issue.labels:
            github_client.edit_issue_labels(issue.number, remove=[labels_config.ready])
        return IssuePrdPublicationResult(
            issue_url=issue.url,
            branch=issue_branch,
            commit_sha=commit_sha,
            ready=queue_ready,
        )
    except Exception as exc:  # noqa: BLE001 - 保留部分成功状态并给出同一 Issue 的恢复入口。
        ready_label_present = labels_config.ready in issue.labels
        state_text = issue.state.lower()
        ready_text = "has the ready label" if ready_label_present else "is not marked ready"
        recovery_command = (
            f"kc issue create {shlex.quote(relative_prd_path.as_posix())} "
            f"--recover-issue {issue.number} "
            f"{'--ready' if queue_ready else '--no-ready'}"
        )
        raise RuntimeError(
            f"Issue #{issue.number} ({issue.url}) is {state_text} and {ready_text}; "
            "PRD publication to "
            f"{issue_branch} did not fully complete. Resume this Issue with: "
            f"{recovery_command}. Cause: {exc}"
        ) from exc


def _publish_prd_commit(
    *,
    repo_path: Path,
    relative_prd_path: Path,
    prd_text: str,
    issue_number: int,
    issue_branch: str,
    git_remote: str,
    git_base_branch: str,
    process_runner: IProcessRunner,
) -> str:
    """在临时 Git worktree 中仅提交 PRD 并普通推送到任务分支。"""
    branch_probe = process_runner.run(
        ["git", "ls-remote", "--exit-code", "--heads", git_remote, f"refs/heads/{issue_branch}"],
        cwd=repo_path,
        check=False,
    )
    if branch_probe.return_code == 0 and branch_probe.stdout.strip():
        fetch_ref = f"refs/heads/{issue_branch}"
        branch_already_exists = True
    elif branch_probe.return_code == 2 or (
        branch_probe.return_code == 0 and not branch_probe.stdout.strip()
    ):
        fetch_ref = f"refs/heads/{git_base_branch}"
        branch_already_exists = False
    else:
        raise RuntimeError(
            f"Could not inspect remote branch '{git_remote}/{issue_branch}': "
            f"{branch_probe.stderr.strip()}"
        )

    publication_fetch_ref = f"refs/kc/issue-prd-publication/{issue_number}/{uuid4().hex}"
    temporary_worktree_path = Path(tempfile.mkdtemp(prefix=f"kc-issue-{issue_number}-prd-"))
    worktree_added = False
    try:
        # FETCH_HEAD 是共享文件；命名临时 ref 可避免并发 Runner fetch 覆盖本次基线。
        process_runner.run(
            [
                "git",
                "fetch",
                "--no-write-fetch-head",
                git_remote,
                f"+{fetch_ref}:{publication_fetch_ref}",
            ],
            cwd=repo_path,
        )
        base_sha = process_runner.run(
            ["git", "rev-parse", publication_fetch_ref], cwd=repo_path
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_sha):
            raise RuntimeError(f"Git returned an invalid commit SHA for {git_remote}/{fetch_ref}.")
        process_runner.run(
            ["git", "worktree", "add", "--detach", str(temporary_worktree_path), base_sha],
            cwd=repo_path,
        )
        worktree_added = True
        worktree_prd_path = temporary_worktree_path / relative_prd_path
        resolved_worktree_path = temporary_worktree_path.resolve()
        resolved_worktree_prd_path = worktree_prd_path.resolve()
        try:
            resolved_worktree_prd_path.relative_to(resolved_worktree_path)
        except ValueError as exc:
            raise PrdPublicationConflictError(
                f"Remote {git_remote}/{issue_branch} has a path escaping the temporary worktree "
                f"at '{relative_prd_path.as_posix()}'."
            ) from exc
        if worktree_prd_path.is_symlink():
            raise PrdPublicationConflictError(
                f"Remote {git_remote}/{issue_branch} has a symlink at "
                f"'{relative_prd_path.as_posix()}'; PRD publication refused to follow it."
            )
        if worktree_prd_path.exists():
            if not worktree_prd_path.is_file():
                raise PrdPublicationConflictError(
                    f"Remote {git_remote}/{issue_branch} has a non-file at "
                    f"'{relative_prd_path.as_posix()}'."
                )
            remote_prd_text = worktree_prd_path.read_text(encoding="utf-8")
            if remote_prd_text != prd_text:
                raise PrdPublicationConflictError(
                    f"Remote {git_remote}/{issue_branch} has different content at "
                    f"'{relative_prd_path.as_posix()}'; remote content was preserved."
                )
        else:
            worktree_prd_path.parent.mkdir(parents=True, exist_ok=True)
            worktree_prd_path.write_text(prd_text, encoding="utf-8")
            process_runner.run(
                ["git", "add", "--", relative_prd_path.as_posix()], cwd=temporary_worktree_path
            )
            staged_paths = process_runner.run(
                ["git", "diff", "--cached", "--name-only", "--"],
                cwd=temporary_worktree_path,
            ).stdout.splitlines()
            normalized_staged_paths = {staged_path.strip() for staged_path in staged_paths}
            if normalized_staged_paths != {relative_prd_path.as_posix()}:
                raise RuntimeError(
                    "Refusing to publish because the temporary Git worktree staged paths "
                    f"outside the target PRD: {', '.join(sorted(normalized_staged_paths))}"
                )
            prd_slug = relative_prd_path.stem.split("-prd-", maxsplit=1)[-1]
            process_runner.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"docs(prd): publish {prd_slug} for issue #{issue_number}",
                    "--",
                    relative_prd_path.as_posix(),
                ],
                cwd=temporary_worktree_path,
            )

        published_commit_sha = process_runner.run(
            ["git", "rev-parse", "HEAD"], cwd=temporary_worktree_path
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", published_commit_sha):
            raise RuntimeError("Git returned an invalid PRD publication commit SHA.")
        if not branch_already_exists or published_commit_sha != base_sha:
            process_runner.run(
                ["git", "push", git_remote, f"HEAD:refs/heads/{issue_branch}"],
                cwd=temporary_worktree_path,
            )
        return published_commit_sha
    finally:
        if worktree_added:
            cleanup_result = process_runner.run(
                ["git", "worktree", "remove", "--force", str(temporary_worktree_path)],
                cwd=repo_path,
                check=False,
            )
            if cleanup_result.return_code != 0:
                _logger.warning(
                    "Could not remove temporary PRD worktree %s: %s",
                    temporary_worktree_path,
                    cleanup_result.stderr.strip(),
                )
        process_runner.run(
            ["git", "update-ref", "-d", publication_fetch_ref],
            cwd=repo_path,
            check=False,
        )
        shutil.rmtree(temporary_worktree_path, ignore_errors=True)


def _resolve_github_repo_slug(
    repo_path: Path,
    git_remote: str,
    process_runner: IProcessRunner,
) -> str:
    """从指定 Git remote 读取 owner/repo，并确认其为 GitHub URL。"""
    remote_config = process_runner.run(
        ["git", "config", "--get", f"remote.{git_remote}.url"],
        cwd=repo_path,
        check=False,
    )
    if remote_config.return_code != 0:
        raise ValueError(f"Git remote '{git_remote}' is not configured.")
    github_repo_slug = parse_github_slug_from_url(remote_config.stdout.strip())
    if not github_repo_slug:
        raise ValueError(
            f"Git remote '{git_remote}' must identify a GitHub owner/repository "
            "to publish a pinned PRD link."
        )
    return github_repo_slug


def validate_issue_branch_publication_target(
    *,
    repo_path: Path,
    git_remote: str,
    git_base_branch: str,
    process_runner: IProcessRunner,
) -> None:
    """在创建 Issue 前确认 GitHub remote 和远端基础分支可用。"""
    _resolve_github_repo_slug(repo_path, git_remote, process_runner)
    base_branch_probe = process_runner.run(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            git_remote,
            f"refs/heads/{git_base_branch}",
        ],
        cwd=repo_path,
        check=False,
    )
    if base_branch_probe.return_code != 0 or not base_branch_probe.stdout.strip():
        raise RuntimeError(
            f"Could not resolve remote base branch '{git_remote}/{git_base_branch}': "
            f"{base_branch_probe.stderr.strip()}"
        )


def _parse_issue_url(issue_url: str) -> tuple[str, int]:
    """解析 GitHub Issue URL 中的 owner/repo 与编号。"""
    issue_url_match = _ISSUE_URL_RE.fullmatch(issue_url.strip())
    if issue_url_match is None:
        raise ValueError(f"Not a GitHub Issue URL: {issue_url}")
    return f"{issue_url_match.group(1)}/{issue_url_match.group(2)}", int(issue_url_match.group(3))


def _normalize_prd_anchor(prd_anchor: str) -> str:
    """拒绝绝对或越界的 Issue PRD 锚点并返回 POSIX 相对路径。"""
    parsed_anchor = PurePosixPath(prd_anchor)
    if parsed_anchor.is_absolute() or any(
        path_part in {"", ".", ".."} for path_part in parsed_anchor.parts
    ):
        return ""
    return parsed_anchor.as_posix()


def _validate_or_restore_issue_link(
    *,
    prd_text: str,
    absolute_prd_path: Path,
    issue: IssueSummary,
    expected_repo_slug: str,
) -> None:
    """校验本地 Issue 回链；缺失时以 GitHub 的规范 URL 补回。"""
    issue_lines = [
        issue_line.strip()
        for issue_line in prd_text.splitlines()
        if ISSUE_LINK_LINE_RE.match(issue_line)
    ]
    if not issue_lines:
        write_issue_link(
            prd_text=prd_text,
            absolute_prd_path=absolute_prd_path,
            issue_url=issue.url,
            force=False,
        )
        return
    for issue_line in issue_lines:
        issue_url = issue_line.partition(":")[2].strip()
        issue_repo_slug, issue_url_number = _parse_issue_url(issue_url)
        if (
            issue_repo_slug.lower() != expected_repo_slug.lower()
            or issue_url_number != issue.number
        ):
            raise ValueError(
                f"PRD already links to a different Issue: {issue_url}; refusing to replace it."
            )


def _replace_publication_marker(
    issue_body: str,
    publication_metadata: PrdPublicationMetadata,
) -> str:
    """以单一规范 marker 替换正文中的旧 PRD 发布状态。"""
    body_without_marker = "\n".join(
        body_line
        for body_line in issue_body.splitlines()
        if _PUBLICATION_MARKER_PREFIX not in body_line
    ).rstrip()
    ready_text = "true" if publication_metadata.queue_ready else "false"
    if publication_metadata.state == "published":
        branch_text = publication_metadata.branch or ""
        commit_text = publication_metadata.commit_sha or ""
        marker_text = (
            "<!-- kc:prd-publication version=1 state=published "
            f"queue_ready={ready_text} branch={branch_text} commit={commit_text} -->"
        )
    else:
        marker_text = (
            f"<!-- kc:prd-publication version=1 state=pending queue_ready={ready_text} -->"
        )
    if body_without_marker:
        return f"{body_without_marker}\n\n{marker_text}\n"
    return f"{marker_text}\n"


__all__ = [
    "IssuePrdPublicationRequest",
    "IssuePrdPublicationResult",
    "PrdPublicationConflictError",
    "ensure_issue_prd_path_anchor",
    "pending_prd_publication_marker",
    "parse_prd_publication_metadata",
    "published_issue_prd_branch",
    "publish_prd_for_created_issue",
    "read_published_issue_prd_text",
    "recover_issue_prd_publication",
    "validate_issue_prd_path",
    "validate_issue_branch_publication_target",
]
