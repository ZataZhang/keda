"""Generate frontend-admin/public/versions.json from Conventional Commits.

The manifest is consumed at runtime by the frontend to render a "What's New"
modal after each version change. Two modes are emitted:

- ``production`` — HEAD is pointed at by a tag matching ``v<semver>``; the tag
  name is the version and the diff range covers the previous tag → HEAD.
- ``staging`` — no production tag points at HEAD; the short commit SHA is the
  version and the diff range covers the latest production tag (if any) → HEAD.

Release tags must follow ``vX.Y`` or ``vX.Y.Z``. Anything else is treated as
a non-version tag and falls through to staging mode.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "frontend-admin" / "public" / "versions.json"

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|perf|refactor|docs|build|ci|chore|revert|test|style)"
    r"(?P<scope>\([^)]+\))?"
    r"(?P<bang>!)?"
    r":\s+(?P<subject>.+)$"
)
BREAKING_BODY_RE = re.compile(
    r"^BREAKING[ -]CHANGE:\s+(?P<desc>.+)$",
    re.MULTILINE,
)
PRODUCTION_TAG_RE = re.compile(r"^v\d+\.\d+(?:\.\d+)?$")

TYPE_GROUP_LABELS: dict[str, str] = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "perf": "Performance",
    "refactor": "Refactors",
    "revert": "Reverts",
}
MAINTENANCE_LABEL = "Maintenance"


@dataclass(frozen=True)
class CommitEntry:
    """Reduced view of a single commit used for grouping and breaking detection."""

    type: str
    scope: str
    subject: str
    breaking: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Manifest:
    """Schema written to ``frontend-admin/public/versions.json``."""

    version: str
    mode: str
    generatedAt: str
    previousVersion: str | None
    commitRange: str
    groups: dict[str, list[str]]
    breaking: list[str]


def _run_git(*args: str) -> str:
    """Run a git command inside the repository and return stdout.

    Args:
        *args: Arguments appended to ``git -C REPO_ROOT``.

    Returns:
        str: Command stdout, stripped of trailing whitespace.

    Raises:
        subprocess.CalledProcessError: If the command exits with a non-zero
            status (e.g. when the requested ref does not exist).
    """

    completed_process = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed_process.stdout


def _run_git_safe(*args: str) -> str:
    """Like :func:`_run_git` but returns an empty string on failure."""

    try:
        return _run_git(*args)
    except subprocess.CalledProcessError:
        return ""


def detect_mode_and_version(head_sha: str) -> tuple[str, str]:
    """Resolve the build mode and human-readable version for ``head_sha``.

    Args:
        head_sha (str): Full commit SHA currently checked out.

    Returns:
        tuple[str, str]: ``(mode, version)``. ``mode`` is either
        ``"production"`` or ``"staging"``. ``version`` is the matching tag
        name (kept with its ``v`` prefix) or the 7-character short SHA.
    """

    tag_lines = _run_git("tag", "--points-at", head_sha).split()
    production_tags = [tag for tag in tag_lines if PRODUCTION_TAG_RE.match(tag)]
    if production_tags:
        return "production", production_tags[0]
    short_sha = _run_git("rev-parse", "--short=7", head_sha).strip()
    return "staging", short_sha


def find_previous_production_tag(head_sha: str) -> str | None:
    """Find the most recent production tag reachable from ``head_sha``.

    Args:
        head_sha (str): Commit to search backwards from.

    Returns:
        str | None: The previous tag name, or ``None`` if no production tag
        exists in the repository history.
    """

    raw = _run_git_safe(
        "describe",
        "--tags",
        "--abbrev=0",
        "--match",
        "v[0-9]*",
        f"{head_sha}^",
    ).strip()
    if raw and PRODUCTION_TAG_RE.match(raw):
        return raw
    return None


def find_latest_production_tag() -> str | None:
    """Find the most recent production tag reachable from HEAD (not strictly before)."""

    raw = _run_git_safe(
        "describe",
        "--tags",
        "--abbrev=0",
        "--match",
        "v[0-9]*",
    ).strip()
    if raw and PRODUCTION_TAG_RE.match(raw):
        return raw
    return None


def parse_commits(commit_range: str) -> list[CommitEntry]:
    """Walk ``git log <range>`` and return conventional-commit entries.

    Args:
        commit_range (str): A git revision range such as ``v1.2.2..abc1234``
            or a single SHA for a single-commit range.

    Returns:
        list[CommitEntry]: One entry per non-merge commit whose subject
        matches the conventional-commit regex. Non-conventional commits are
        silently skipped.
    """

    raw = _run_git_safe(
        "log",
        "--no-merges",
        commit_range,
        "--pretty=format:%H%n%s%n---BODY---%n%b%n===END===",
    )
    entries: list[CommitEntry] = []
    for raw_block in raw.split("===END==="):
        block = raw_block.strip()
        if not block:
            continue
        header_part, _, body = block.partition("---BODY---")
        header_lines = header_part.strip().splitlines()
        # Format was: <sha>\n<subject>\n ; skip the SHA at index 0.
        subject_line = header_lines[1] if len(header_lines) > 1 else ""
        match = CONVENTIONAL_RE.match(subject_line)
        if match is None:
            continue
        breaking_descriptions = list(BREAKING_BODY_RE.findall(body))
        if match.group("bang") == "!" and not breaking_descriptions:
            # The `!` shorthand didn't get a verbose body; surface the subject
            # so the UI can still warn the user.
            breaking_descriptions.append(match.group("subject").strip())
        entries.append(
            CommitEntry(
                type=match.group("type"),
                scope=match.group("scope") or "",
                subject=match.group("subject").strip(),
                breaking=tuple(breaking_descriptions),
            )
        )
    return entries


def group_entries(
    entries: Sequence[CommitEntry],
) -> tuple[dict[str, list[str]], list[str]]:
    """Group commit subjects by conventional-commit type for the modal UI.

    Args:
        entries (Sequence[CommitEntry]): Parsed commits, in display order
            (newest first).

    Returns:
        tuple[dict[str, list[str]], list[str]]: ``(groups, breaking)`` where
        ``groups`` is keyed by display label (``"Features"``,
        ``"Bug Fixes"``, ``"Maintenance"`` etc.) and ``breaking`` is the
        flattened list of breaking-change descriptions.
    """

    groups: dict[str, list[str]] = {label: [] for label in TYPE_GROUP_LABELS.values()}
    groups[MAINTENANCE_LABEL] = []
    breaking: list[str] = []
    for entry in entries:
        label = TYPE_GROUP_LABELS.get(entry.type, MAINTENANCE_LABEL)
        scope_text = entry.scope.strip("()")
        rendered = f"{entry.subject} ({scope_text})" if scope_text else entry.subject
        groups[label].append(rendered)
        breaking.extend(entry.breaking)
    return groups, breaking


def _resolve_range(head_sha: str, mode: str) -> tuple[str, str | None]:
    """Decide which commit range and previous version to report.

    Args:
        head_sha (str): Full commit SHA currently checked out.
        mode (str): Either ``"production"`` or ``"staging"``.

    Returns:
        tuple[str, str | None]: ``(commit_range, previous_version)``.
    """

    if mode == "production":
        previous = find_previous_production_tag(head_sha)
        if previous is not None:
            return f"{previous}..{head_sha}", previous
        return head_sha, None
    # Staging: show the candidate release that would be cut from HEAD.
    previous = find_latest_production_tag()
    if previous is not None:
        return f"{previous}..{head_sha}", previous
    return head_sha, None


def build_manifest() -> Manifest:
    """Compose the manifest for the current HEAD.

    Returns:
        Manifest: Structured payload ready to be serialized to JSON.
    """

    head_sha = _run_git("rev-parse", "HEAD").strip()
    mode, version = detect_mode_and_version(head_sha)
    commit_range, previous_version = _resolve_range(head_sha, mode)
    entries = parse_commits(commit_range)
    groups, breaking = group_entries(entries)
    return Manifest(
        version=version,
        mode=mode,
        generatedAt=datetime.now(timezone.utc).isoformat(),
        previousVersion=previous_version,
        commitRange=commit_range,
        groups=groups,
        breaking=breaking,
    )


def write_manifest(manifest: Manifest, output_path: Path) -> None:
    """Serialize the manifest to JSON at ``output_path``.

    Args:
        manifest (Manifest): Payload to write.
        output_path (Path): Destination file. Parent directories are created
            if they do not exist.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(manifest)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    output_path.write_text(serialized, encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the CLI entrypoint.

    Args:
        argv (Sequence[str] | None): Optional argument list; defaults to
            ``sys.argv[1:]`` when ``None``.

    Returns:
        argparse.Namespace: Parsed arguments.
    """

    parser = argparse.ArgumentParser(
        description="Generate the frontend 'what's new' manifest from git history.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint that builds and writes the manifest.

    Args:
        argv (Sequence[str] | None): Optional argument override; used by
            tests to drive the script without touching ``sys.argv``.

    Returns:
        int: Process exit code (``0`` on success).
    """

    args = parse_args(argv)
    manifest = build_manifest()
    write_manifest(manifest, args.output)
    print(f"Wrote {manifest.mode} manifest for version {manifest.version} to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
