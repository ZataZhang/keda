"""会话来源（``agent_sessions``）与脱敏（``redaction``）的行为测试。

git 仓库与会话文件全部是真实的临时文件；会话内容由测试按 Claude Code 的 jsonl 形态构造。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.dataset_export import agent_sessions
from scripts.dataset_export import export_samples as export_module
from scripts.dataset_export.export_samples import ExportOptions, export_samples
from scripts.dataset_export.redaction import redact_text
from tests.support.agent_runner import create_commit, init_git_repo


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


# --- 脱敏 -------------------------------------------------------------------


def test_redaction_masks_secrets_emails_and_home_paths_and_counts_hits() -> None:
    text = (
        "看下 api_key = sk-abcdefghijklmnopqrstuvwx 和 ghp_abcdefghijklmnopqrstuv，"
        "联系 a.b@corp.com，文件在 /Users/zata/code/x，password: hunter2hunter2"
    )

    result = redact_text(text)

    assert "sk-abcdefghij" not in result.text
    assert "ghp_abcdef" not in result.text
    assert "a.b@corp.com" not in result.text
    assert "hunter2hunter2" not in result.text
    assert "/Users/zata" not in result.text
    assert "~/code/x" in result.text
    assert result.redaction_count == 5
    assert "api_key = <redacted-api-key>" in result.text


def test_redaction_masks_a_whole_private_key_block() -> None:
    key_block = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\ndef\n-----END RSA PRIVATE KEY-----"

    result = redact_text(f"前文\n{key_block}\n后文")

    assert "MIIabc" not in result.text
    assert result.text == "前文\n<redacted-private-key>\n后文"
    assert result.redaction_count == 1


def test_redaction_leaves_ordinary_text_untouched() -> None:
    text = "你看一下 process_runner 的 join 超时，token 数量是 120 个"

    result = redact_text(text)

    assert result.text == text
    assert result.redaction_count == 0


# --- 会话解析 ---------------------------------------------------------------


def _entry(kind: str, session_id: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "sessionId": session_id, "timestamp": "2026-10-01T00:00:00Z", **fields}


def _prompt(session_id: str, text: str, **fields: Any) -> dict[str, Any]:
    fields.setdefault("entrypoint", "cli")
    return _entry("user", session_id, message={"role": "user", "content": text}, **fields)


def _commit_calls(session_id: str, tool_id: str, command: str, output: str) -> list[dict[str, Any]]:
    call = _entry(
        "assistant",
        session_id,
        message={
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}
            ],
        },
    )
    result = _entry(
        "user",
        session_id,
        message={
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": output}],
        },
    )
    return [call, result]


def _write_session(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def test_programmatic_sessions_are_excluded(tmp_path: Path) -> None:
    session_path = tmp_path / "s.jsonl"
    _write_session(session_path, [_prompt("s", "runner 发的任务", entrypoint="sdk-cli")])

    assert agent_sessions.parse_session_file(session_path) is None


def test_parse_takes_first_human_prompt_and_skips_injected_and_sidechain_messages(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "s.jsonl"
    _write_session(
        session_path,
        [
            _prompt("s", "<local-command-stdout>/clear</local-command-stdout>"),
            _prompt("s", "子代理的话", isSidechain=True),
            _prompt("s", "元消息", isMeta=True),
            _prompt("s", "真正的第一句"),
            _prompt("s", "第二句"),
            _entry("pr-link", "s", prNumber=9, prRepository="owner/repo"),
            _entry("pr-link", "s", prNumber=9, prRepository="owner/repo"),
        ],
    )

    parsed = agent_sessions.parse_session_file(session_path)

    assert parsed is not None
    assert parsed.first_prompt == "真正的第一句"
    assert parsed.human_turns == 2
    assert parsed.pr_links == [{"number": 9, "repository": "owner/repo"}]


def test_commit_hashes_come_only_from_git_commit_tool_results(tmp_path: Path) -> None:
    session_path = tmp_path / "s.jsonl"
    _write_session(
        session_path,
        [
            _prompt("s", "做点事"),
            *_commit_calls("s", "t1", "git status", "[main aaaaaaa] 这不是提交输出"),
            *_commit_calls(
                "s", "t2", "git add . && git commit -m 'x'", "[main bbbbbbb] x\n 1 file changed"
            ),
            *_commit_calls("s", "t3", "git commit --amend", "[feat/y ccccccc] y"),
        ],
    )

    parsed = agent_sessions.parse_session_file(session_path)

    assert parsed is not None
    assert parsed.commit_shas == ["bbbbbbb", "ccccccc"]


def test_session_without_human_prompt_is_excluded(tmp_path: Path) -> None:
    session_path = tmp_path / "s.jsonl"
    _write_session(session_path, [_entry("system", "s", content="x")])

    assert agent_sessions.parse_session_file(session_path) is None


def test_project_dir_name_replaces_every_non_alphanumeric_character(tmp_path: Path) -> None:
    repo_path = tmp_path / "my.repo_x"
    repo_path.mkdir()

    project_dir = agent_sessions.claude_project_dir(
        repo_path=repo_path, projects_root=tmp_path / "p"
    )

    assert project_dir.parent == tmp_path / "p"
    assert (
        "/" not in project_dir.name and "." not in project_dir.name and "_" not in project_dir.name
    )


# --- 样本导出 ---------------------------------------------------------------


@pytest.fixture()
def repo_with_work(tmp_path: Path) -> tuple[Path, str, str]:
    """main 上有基线提交；返回(仓库, 基线 sha, 会话里提交的 sha)。"""
    repo_path = init_git_repo(tmp_path / "proj")
    _git(repo_path, "remote", "add", "origin", "https://github.com/owner/repo.git")
    base_sha = create_commit(repo_path, "base")
    work_sha = create_commit(repo_path, "feat: the work done in the session")
    return repo_path, base_sha, work_sha


def _options(tmp_path: Path, repo_path: Path) -> ExportOptions:
    return ExportOptions(
        db_path=tmp_path / "missing.db",
        output_path=None,
        agent_session_repo_paths=(repo_path,),
        sessions_root=tmp_path / "projects",
        home_path=tmp_path / "home",
    )


def _install_session(
    tmp_path: Path, repo_path: Path, entries: list[dict[str, Any]], name: str = "s1"
) -> None:
    project_dir = agent_sessions.claude_project_dir(
        repo_path=repo_path, projects_root=tmp_path / "projects"
    )
    _write_session(project_dir / f"{name}.jsonl", entries)


def test_session_becomes_a_redacted_never_eval_ready_sample(
    tmp_path: Path, repo_with_work: tuple[Path, str, str]
) -> None:
    repo_path, base_sha, work_sha = repo_with_work
    _install_session(
        tmp_path,
        repo_path,
        [
            _prompt(
                "sess-0001-abcdef",
                "修一下 api_key = sk-abcdefghijklmnopqrstuvwx 的问题，文件在 /Users/zata/x",
            ),
            *_commit_calls(
                "sess-0001-abcdef", "t1", "git commit -m w", f"[main {work_sha[:7]}] feat"
            ),
        ],
    )

    samples = export_samples(_options(tmp_path, repo_path))

    assert len(samples) == 1
    sample = samples[0]
    assert sample["origin"] == "agent_session"
    assert sample["repo_id"] == "owner/repo"
    assert sample["sample_id"] == "owner/repo#session-sess-000"
    assert sample["input"]["provenance"] == "session_first_prompt"
    assert "sk-abcdef" not in sample["input"]["body"]
    assert sample["input"]["redaction_count"] == 2
    assert sample["input"]["redaction_reviewed"] is False
    assert sample["code"]["source"] == "session_transcript"
    assert sample["code"]["base_sha"] == base_sha
    assert sample["code"]["final_sha"] == work_sha
    assert sample["labels"]["resolved"] is True
    assert sample["labels"]["resolved_basis"] == "on_default_branch"
    assert sample["labels"]["eval_ready"] is False
    assert "/Users/" not in json.dumps(sample)


def test_session_whose_commits_are_not_on_the_default_branch_is_unresolved(
    tmp_path: Path, repo_with_work: tuple[Path, str, str]
) -> None:
    repo_path, _, _ = repo_with_work
    _git(repo_path, "checkout", "-b", "feat/side")
    side_sha = create_commit(repo_path, "side work")
    _git(repo_path, "checkout", "main")
    _install_session(
        tmp_path,
        repo_path,
        [
            _prompt("s2", "做点事"),
            *_commit_calls("s2", "t1", "git commit -m s", f"[feat/side {side_sha[:7]}] s"),
        ],
    )

    sample = export_samples(_options(tmp_path, repo_path))[0]

    assert sample["labels"]["resolved"] is False


def test_session_with_unresolvable_commits_is_not_a_sample(
    tmp_path: Path, repo_with_work: tuple[Path, str, str], capsys: pytest.CaptureFixture
) -> None:
    repo_path, _, _ = repo_with_work
    _install_session(
        tmp_path,
        repo_path,
        [
            _prompt("s3", "做点事"),
            *_commit_calls("s3", "t1", "git commit -m s", "[main deadbee] gone after rebase"),
        ],
    )

    assert export_samples(_options(tmp_path, repo_path)) == []
    assert "有可对照提交 0" in capsys.readouterr().err


def test_session_links_to_the_pull_request_sample_it_produced(
    tmp_path: Path, repo_with_work: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path, _, work_sha = repo_with_work
    _install_session(
        tmp_path,
        repo_path,
        [
            _prompt("sess-link-1", "做点事"),
            *_commit_calls("sess-link-1", "t1", "git commit -m s", f"[main {work_sha[:7]}] s"),
            _entry("pr-link", "sess-link-1", prNumber=9, prRepository="owner/repo"),
        ],
    )
    pull_request = {
        "number": 9,
        "state": "MERGED",
        "headRefName": "feat/x",
        "headRefOid": work_sha,
        "title": "feat: x",
        "body": "d",
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
        "closingIssuesReferences": [],
        "author": {"is_bot": False},
        "commits": [{"oid": work_sha, "messageHeadline": "feat"}],
        "mergedAt": "2026-10-01T00:00:00Z",
    }

    def fake_fetch_gh_json(*, repo_path: Path, args: list[str]) -> Any:
        if args[:2] == ["repo", "view"]:
            return {"nameWithOwner": "owner/repo"}
        if args[:2] == ["pr", "list"]:
            return [{"number": 9, "headRefName": "feat/x", "author": {"is_bot": False}}]
        return pull_request

    monkeypatch.setattr(export_module, "fetch_gh_json", fake_fetch_gh_json)
    options = ExportOptions(
        db_path=tmp_path / "missing.db",
        output_path=None,
        github_pr_repo_paths=(repo_path,),
        agent_session_repo_paths=(repo_path,),
        sessions_root=tmp_path / "projects",
        home_path=tmp_path / "home",
    )

    by_origin = {s["origin"]: s for s in export_samples(options)}

    assert by_origin["github_pr"]["linked_session_ids"] == ["sess-link-1"]
    assert by_origin["agent_session"]["session"]["linked_sample_ids"] == ["owner/repo#pr-9"]
