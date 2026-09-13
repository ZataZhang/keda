"""守护 what's-new manifest 构建脚本的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_MODULE_LOAD_NAME = "scripts.build.build_whats_new_manifest"


def _load_manifest_module() -> ModuleType:
    """Load the manifest script from its file path.

    The module is registered in :data:`sys.modules` under its real package
    name so that ``dataclasses`` can resolve forward references introduced
    by ``from __future__ import annotations``.
    """

    manifest_script_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "build" / "build_whats_new_manifest.py"
    )
    manifest_module_spec = importlib.util.spec_from_file_location(
        _MODULE_LOAD_NAME,
        manifest_script_path,
    )
    assert manifest_module_spec is not None
    assert manifest_module_spec.loader is not None

    manifest_module = importlib.util.module_from_spec(manifest_module_spec)
    sys.modules[_MODULE_LOAD_NAME] = manifest_module
    manifest_module_spec.loader.exec_module(manifest_module)
    return manifest_module


def test_conventional_regex_matches_scoped_commit() -> None:
    """The regex extracts type, scope, and subject from a scoped commit."""

    manifest_module = _load_manifest_module()
    match = manifest_module.CONVENTIONAL_RE.match("feat(frontend-admin): add whats-new modal")
    assert match is not None
    assert match.group("type") == "feat"
    assert match.group("scope") == "(frontend-admin)"
    assert match.group("bang") is None
    assert match.group("subject") == "add whats-new modal"


def test_conventional_regex_matches_unscoped_commit() -> None:
    """Commits without a scope are still recognized."""

    manifest_module = _load_manifest_module()
    match = manifest_module.CONVENTIONAL_RE.match("fix: crash on startup")
    assert match is not None
    assert match.group("type") == "fix"
    assert match.group("scope") is None
    assert match.group("subject") == "crash on startup"


def test_conventional_regex_captures_breaking_bang() -> None:
    """The ``!`` shorthand is captured in the ``bang`` group."""

    manifest_module = _load_manifest_module()
    match = manifest_module.CONVENTIONAL_RE.match("feat(api)!: redesign endpoints")
    assert match is not None
    assert match.group("bang") == "!"


def test_conventional_regex_rejects_non_conventional_subject() -> None:
    """Non-conventional commits are rejected by the regex."""

    manifest_module = _load_manifest_module()
    assert manifest_module.CONVENTIONAL_RE.match("Refactor model loading") is None
    assert manifest_module.CONVENTIONAL_RE.match("WIP: scratch work") is None


def test_breaking_body_re_extracts_body_marker() -> None:
    """``BREAKING CHANGE:`` markers in commit bodies are captured."""

    manifest_module = _load_manifest_module()
    body = "Some context\n\nBREAKING CHANGE: drop legacy auth header\n"
    assert manifest_module.BREAKING_BODY_RE.findall(body) == ["drop legacy auth header"]


def test_breaking_body_re_accepts_hyphen_form() -> None:
    """The hyphen form ``BREAKING-CHANGE:`` is also recognized."""

    manifest_module = _load_manifest_module()
    body = "BREAKING-CHANGE: rename users endpoint"
    assert manifest_module.BREAKING_BODY_RE.findall(body) == ["rename users endpoint"]


def test_production_tag_regex_matches_semver() -> None:
    """Standard and short semver tags are accepted as production tags."""

    manifest_module = _load_manifest_module()
    assert manifest_module.PRODUCTION_TAG_RE.match("v1.2.3") is not None
    assert manifest_module.PRODUCTION_TAG_RE.match("v1.2") is not None
    assert manifest_module.PRODUCTION_TAG_RE.match("v10.20.30") is not None


def test_production_tag_regex_rejects_non_semver() -> None:
    """Non-semver tags are rejected and fall through to staging mode."""

    manifest_module = _load_manifest_module()
    assert manifest_module.PRODUCTION_TAG_RE.match("release-2024") is None
    assert manifest_module.PRODUCTION_TAG_RE.match("main") is None
    assert manifest_module.PRODUCTION_TAG_RE.match("v1") is None


def test_group_entries_buckets_by_type_and_preserves_order() -> None:
    """Commits are grouped by their conventional-commit type label."""

    manifest_module = _load_manifest_module()
    entries = [
        manifest_module.CommitEntry(
            type="feat", scope="(frontend-admin)", subject="new modal", breaking=()
        ),
        manifest_module.CommitEntry(
            type="fix", scope="(backend)", subject="fix crash", breaking=()
        ),
        manifest_module.CommitEntry(type="chore", scope="", subject="bump deps", breaking=()),
    ]
    groups, breaking = manifest_module.group_entries(entries)
    assert groups["Features"] == ["new modal (frontend-admin)"]
    assert groups["Bug Fixes"] == ["fix crash (backend)"]
    assert groups["Maintenance"] == ["bump deps"]
    assert breaking == []


def test_group_entries_renders_scope_only_when_present() -> None:
    """Unscoped commits render without a dangling parenthetical."""

    manifest_module = _load_manifest_module()
    entries = [
        manifest_module.CommitEntry(type="fix", scope="", subject="x", breaking=()),
    ]
    groups, _ = manifest_module.group_entries(entries)
    assert groups["Bug Fixes"] == ["x"]


def test_group_entries_aggregates_breaking_changes() -> None:
    """Breaking descriptions are flattened to a top-level list."""

    manifest_module = _load_manifest_module()
    entries = [
        manifest_module.CommitEntry(
            type="feat",
            scope="(api)",
            subject="redesign",
            breaking=("drop v1 endpoints", "rename /users to /accounts"),
        ),
    ]
    _, breaking = manifest_module.group_entries(entries)
    assert breaking == ["drop v1 endpoints", "rename /users to /accounts"]


def test_group_entries_rolls_unknown_types_into_maintenance() -> None:
    """Types without a dedicated bucket fall into ``Maintenance``."""

    manifest_module = _load_manifest_module()
    entries = [
        manifest_module.CommitEntry(type="ci", scope="", subject="bump gh actions", breaking=()),
        manifest_module.CommitEntry(type="style", scope="", subject="reformat", breaking=()),
    ]
    groups, _ = manifest_module.group_entries(entries)
    assert groups["Maintenance"] == ["bump gh actions", "reformat"]


def test_resolve_range_production_with_previous_tag() -> None:
    """Production mode diffs from the previous tag when one exists."""

    manifest_module = _load_manifest_module()
    head_sha = "abc1234567890"
    monkey_call_log: list[tuple[str, ...]] = []

    def fake_run_git_safe(*args: str) -> str:
        monkey_call_log.append(args)
        if args[:2] == ("describe", "--tags") and args[2] == "--abbrev=0" and args[3] == "--match":
            return "v1.2.2\n"
        return ""

    manifest_module._run_git_safe = fake_run_git_safe  # type: ignore[attr-defined]
    commit_range, previous = manifest_module._resolve_range(head_sha, "production")
    assert commit_range == "v1.2.2..abc1234567890"
    assert previous == "v1.2.2"


def test_resolve_range_production_first_release() -> None:
    """The first production tag diffs from the root commit."""

    manifest_module = _load_manifest_module()
    head_sha = "abc1234567890"

    def fake_run_git_safe(*args: str) -> str:
        return ""

    manifest_module._run_git_safe = fake_run_git_safe  # type: ignore[attr-defined]
    commit_range, previous = manifest_module._resolve_range(head_sha, "production")
    assert commit_range == head_sha
    assert previous is None


def test_resolve_range_staging_uses_latest_production_tag() -> None:
    """Staging mode shows the candidate release diff from the latest tag."""

    manifest_module = _load_manifest_module()
    head_sha = "abc1234567890"

    def fake_run_git_safe(*args: str) -> str:
        return "v1.2.2\n"

    manifest_module._run_git_safe = fake_run_git_safe  # type: ignore[attr-defined]
    commit_range, previous = manifest_module._resolve_range(head_sha, "staging")
    assert commit_range == "v1.2.2..abc1234567890"
    assert previous == "v1.2.2"


def test_detect_mode_and_version_production_when_tag_points_at_head() -> None:
    """A production tag pointing at HEAD yields production mode + the tag name."""

    manifest_module = _load_manifest_module()

    def fake_run_git(*args: str) -> str:
        if args[0] == "tag":
            return "v1.2.3\n"
        return ""

    manifest_module._run_git = fake_run_git  # type: ignore[attr-defined]
    mode, version = manifest_module.detect_mode_and_version("abc1234")
    assert mode == "production"
    assert version == "v1.2.3"


def test_detect_mode_and_version_staging_falls_back_to_short_sha() -> None:
    """No production tag → staging mode + the 7-char short SHA."""

    manifest_module = _load_manifest_module()

    def fake_run_git(*args: str) -> str:
        if args[0] == "tag":
            return ""
        if args[0] == "rev-parse":
            return "abc1234\n"
        return ""

    manifest_module._run_git = fake_run_git  # type: ignore[attr-defined]
    mode, version = manifest_module.detect_mode_and_version("deadbeef")
    assert mode == "staging"
    assert version == "abc1234"
