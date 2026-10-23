"""个人信息防线与"不再静默失败"的测试。

这些用例对应一次真实的归档事故：keda 运行样本带出了 ``gh`` 原始 PR 对象里每个提交作者的姓名与邮箱、
错误摘要里的本机绝对路径、Issue 正文里的示例邮箱，而且 ``github_pr`` 来源被偶发的接口失败整个静默跳过、
命令仍然报成功。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scripts.dataset_export import export_samples as export_module
from scripts.dataset_export.archive import ArchivePrivacyError, archive_samples
from scripts.dataset_export.cli import EXIT_PARTIAL, EXIT_PRIVACY, main
from scripts.dataset_export.export_samples import ExportOptions, export_samples
from scripts.dataset_export.redaction import detect_sensitive_kinds
from scripts.dataset_export.sanitize import sanitize_sample_text, summarize_pull_request
from tests.test_dataset_export import _pull_request_fixture, console_db, sample_repo  # noqa: F401

RAW_PULL_REQUEST: dict[str, Any] = {
    "number": 9,
    "state": "MERGED",
    "mergedAt": "2026-10-01T00:00:00Z",
    "headRefName": "issue-7",
    "headRefOid": "a" * 40,
    "additions": 3,
    "deletions": 1,
    "changedFiles": 2,
    "mergeCommit": {"oid": "b" * 40},
    "title": "含作者信息的原始对象",
    "body": "PR 描述",
    "author": {"login": "someone", "name": "Some One", "id": "U_123", "is_bot": False},
    "commits": [
        {
            "oid": "c" * 40,
            "messageHeadline": "fix: contact owner@corp.com",
            "messageBody": "Co-Authored-By: Claude <noreply@anthropic.com>",
            "authors": [
                {"login": "someone", "name": "Some One", "email": "someone@qq.com", "id": "MDQ6"}
            ],
            "authoredDate": "2026-10-01T00:00:00Z",
        }
    ],
}


def test_pull_request_summary_keeps_only_whitelisted_fields_and_no_author_data() -> None:
    summary = summarize_pull_request(RAW_PULL_REQUEST)

    assert summary["number"] == 9 and summary["state"] == "MERGED"
    assert summary["mergeCommit"] == {"oid": "b" * 40}
    assert summary["commits"] == [
        {"oid": "c" * 40, "messageHeadline": "fix: contact <redacted-email>"}
    ]
    dumped = json.dumps(summary, ensure_ascii=False)
    for forbidden in (
        "someone",
        "Some One",
        "@qq.com",
        "noreply@",
        "authors",
        "messageBody",
        "author",
    ):
        assert forbidden not in dumped


def _keda_run_options(console_db: Path, tmp_path: Path) -> ExportOptions:  # noqa: F811
    return ExportOptions(
        db_path=console_db, output_path=None, use_gh=True, home_path=tmp_path / "home"
    )


def test_keda_run_sample_carries_no_emails_paths_or_authors(
    console_db: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(console_db)
    connection.execute(
        "UPDATE run_records SET error_summary = ? WHERE repo_id = 'keda'",
        ("agent failed in /Users/zata/code/keda/src/x.py, see owner@corp.com",),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        export_module,
        "collect_gh_facts",
        lambda **_: {
            "issue": {
                "title": "标题",
                "body": "联系 alice@example.com，日志在 /Users/zata/logs",
                "url": "u",
            },
            "pull_requests": [RAW_PULL_REQUEST],
        },
    )

    sample = export_samples(_keda_run_options(console_db, tmp_path))[0]

    assert detect_sensitive_kinds(json.dumps(sample, ensure_ascii=False)) == set()
    assert "authors" not in json.dumps(sample)
    assert sample["input"]["redaction_count"] >= 2
    assert (
        sample["runs"][0]["error_summary"]
        == "agent failed in ~/code/keda/src/x.py, see <redacted-email>"
    )
    assert sample["pull_requests"][0]["number"] == 9


def test_sample_sanitizing_is_idempotent() -> None:
    sample = {
        "input": {"title": "t", "body": "mail a@b.com at /Users/zata/x"},
        "runs": [{"error_summary": "boom /Users/zata/y"}],
        "code": {"commits": [{"subject": "x a@b.com"}]},
    }

    sanitize_sample_text(sample)
    first_count = sample["input"]["redaction_count"]
    sanitize_sample_text(sample)

    assert first_count == 2  # 一个邮箱 + 一个本机路径
    assert sample["input"]["redaction_count"] == first_count
    assert sample["code"]["commits"][0]["subject"] == "x <redacted-email>"


def _clean(sample_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "origin": "git_commit",
        "repo_id": "o/r",
        "input": {"title": "t"},
        **extra,
    }


def test_archive_refuses_to_write_anything_when_one_sample_still_has_personal_data(
    tmp_path: Path,
) -> None:
    samples = [_clean("o/r#ok"), _clean("o/r#leaky", note="reach me at leaked@example.org")]

    with pytest.raises(ArchivePrivacyError) as error_info:
        archive_samples(samples, tmp_path)

    assert "o/r#leaky" in str(error_info.value) and "email" in str(error_info.value)
    assert "leaked@example.org" not in str(error_info.value)
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "MANIFEST.json").exists()


@pytest.mark.parametrize(
    "secret",
    [
        "/Users/zata/code/x",
        "ghp_abcdefghijklmnopqrstuv",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_archive_guard_also_catches_paths_tokens_and_private_keys(
    tmp_path: Path, secret: str
) -> None:
    with pytest.raises(ArchivePrivacyError):
        archive_samples([_clean("o/r#x", note=secret)], tmp_path)


# --- gh 重试 ----------------------------------------------------------------


def test_gh_json_calls_retry_transient_failures_then_succeed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outputs = iter([None, "", '{"ok": 1}'])
    delays: list[float] = []
    monkeypatch.setattr(export_module, "run_command", lambda **_: next(outputs))
    monkeypatch.setattr(export_module.time, "sleep", delays.append)

    assert export_module.fetch_gh_json(repo_path=tmp_path, args=["x"]) == {"ok": 1}
    assert delays == [1.0, 3.0]


def test_gh_json_calls_give_up_after_the_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(export_module, "run_command", lambda **_: calls.append(1))
    monkeypatch.setattr(export_module.time, "sleep", lambda _: None)

    assert export_module.fetch_gh_json(repo_path=tmp_path, args=["x"]) is None
    assert len(calls) == 3


# --- 来源被跳过 / 隐私失败时命令不再报成功 -------------------------------------------


def test_cli_exits_partial_when_a_requested_source_was_skipped(
    sample_repo: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(export_module, "fetch_gh_json", lambda **_: None)

    exit_code = main(
        [
            "--db",
            str(tmp_path / "missing.db"),
            "--github-pr-repo",
            str(sample_repo),
            "--output",
            str(tmp_path / "o.jsonl"),
        ]
    )

    assert exit_code == EXIT_PARTIAL
    assert "部分来源被跳过" in capsys.readouterr().err


def test_cli_exits_zero_when_nothing_was_skipped(sample_repo: Path, tmp_path: Path) -> None:  # noqa: F811
    exit_code = main(
        [
            "--db",
            str(tmp_path / "missing.db"),
            "--commits-repo",
            str(sample_repo),
            "--output",
            str(tmp_path / "o.jsonl"),
        ]
    )

    assert exit_code == 0


def test_cli_exits_privacy_error_and_archives_nothing_when_the_guard_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "scripts.dataset_export.cli.export_samples",
        lambda _options: [_clean("o/r#x", note="a@b.com")],
    )

    exit_code = main(
        [
            "--db",
            str(tmp_path / "missing.db"),
            "--commits-repo",
            str(tmp_path),
            "--archive-dir",
            str(tmp_path / "datasets"),
        ]
    )

    assert exit_code == EXIT_PRIVACY
    assert "归档失败" in capsys.readouterr().err
    assert not (tmp_path / "datasets" / "raw").exists()
