"""用实际 iar、Git 和脚本执行器核对讨论失败时的退出码、输出与状态。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.keda_eval_historical_cli import _run

pytest_plugins = ("tests.keda_eval_historical_cli",)


@pytest.fixture
def deliberation_repo(isolated_repo: tuple[Path, dict[str, str]]) -> tuple[Path, dict[str, str]]:
    repository, environment = isolated_repo
    code, output = _run(repository, environment, "init")
    assert code == 0, output
    return repository, environment


def _setup_script_executors(environment: dict[str, str], scenario: str) -> None:
    provider_bin = Path(environment["PATH"].split(os.pathsep)[0])
    program = rf"""#!{sys.executable}
import json
import pathlib
import sys

provider = pathlib.Path(sys.argv[0]).name
role = pathlib.Path.cwd().name
scenario = {scenario!r}
if provider == "kimi" and "--quiet" in sys.argv:
    print("unsupported fixture flag", flush=True)
    sys.exit(73)
failed = (
    scenario == "all"
    or (scenario == "participant" and provider == "kimi")
    or (scenario == "synthesis" and role == "synthesizer")
)
if failed:
    body = "fixture partial output from " + provider
elif role == "synthesizer":
    body = "## Recommendation\n\nfixture synthesis output\n"
else:
    body = "fixture completed role " + role + " using " + provider
if provider == "claude":
    print(json.dumps({{"type": "stream_event", "event": {{
        "type": "content_block_delta", "delta": {{"type": "text_delta", "text": body + "\n"}}
    }}}}), flush=True)
    print(json.dumps({{"type": "stream_event", "event": {{"type": "message_stop"}}}}), flush=True)
else:
    print(body, flush=True)
sys.exit(17 if failed else 0)
"""
    for provider in ("claude", "codex", "kimi"):
        executable = provider_bin / provider
        executable.write_text(program, encoding="utf-8")
        executable.chmod(0o755)


def _run_failure_case(
    deliberation_repo: tuple[Path, dict[str, str]], scenario: str, *, strict: bool = False
) -> tuple[int, str, Path]:
    repository, environment = deliberation_repo
    _setup_script_executors(environment, scenario)
    output_dir = repository.parent / "discussion-output"
    code, output = _run(
        repository,
        environment,
        "deliberate",
        "Review a retry policy.",
        "--agents",
        "architect,skeptic",
        "--rounds",
        "1",
        "--synthesizer",
        "codex",
        "--session-id",
        "smoke-evaluation",
        "--output",
        str(output_dir),
        *(("--strict",) if strict else ()),
    )
    return code, output, output_dir / "smoke-evaluation"


def _failed_participants(session: Path) -> list[dict[str, object]]:
    session_path = session / "session.json"
    assert session_path.is_file(), list(session.glob("*"))
    return json.loads(session_path.read_text(encoding="utf-8"))["failed_agents"]


@pytest.mark.parametrize("strict", (False, True))
def test_participant_failure_retains_output_and_retries_same_role(
    deliberation_repo: tuple[Path, dict[str, str]], strict: bool
) -> None:
    code, output, session = _run_failure_case(deliberation_repo, "participant", strict=strict)
    assert (code != 0) is strict, output
    failures = _failed_participants(session)
    skeptic_failure = next(
        row
        for row in failures
        if row.get("profile", row.get("profile_id")) == "skeptic"
        and row.get("original_agent", row.get("attempted_agent")) == "kimi"
    )
    assert skeptic_failure["fallback_agent"] in ("claude", "codex")
    assert skeptic_failure["reason"]
    transcript = (session / "transcript.md").read_text(encoding="utf-8")
    workspace_output = "\n".join(
        output_file.read_text(encoding="utf-8")
        for output_file in (session / "workspaces/skeptic").rglob("*.md")
    )
    assert "fixture partial output from kimi" in workspace_output
    assert "fixture completed role skeptic" in workspace_output
    assert "fixture completed role architect" in transcript
    assert "fixture completed role skeptic" in transcript
    assert "fixture synthesis output" in (session / "result.md").read_text(encoding="utf-8")


def test_all_agents_failed_exits_nonzero_and_preserves_records(
    deliberation_repo: tuple[Path, dict[str, str]],
) -> None:
    code, output, session = _run_failure_case(deliberation_repo, "all")
    assert code != 0, output
    failed_profiles = {
        row.get("profile", row.get("profile_id")) for row in _failed_participants(session)
    }
    assert {"architect", "skeptic"} <= failed_profiles


def test_synthesizer_failure_keeps_completed_participants(
    deliberation_repo: tuple[Path, dict[str, str]],
) -> None:
    code, output, session = _run_failure_case(deliberation_repo, "synthesis")
    assert code == 0, output
    assert any(
        row.get("profile", row.get("profile_id")) == "synthesizer"
        for row in _failed_participants(session)
    )
    transcript = (session / "transcript.md").read_text(encoding="utf-8")
    assert "fixture completed role architect" in transcript
    assert "fixture completed role skeptic" in transcript
    assert "fixture synthesis output" not in (session / "result.md").read_text(encoding="utf-8")
