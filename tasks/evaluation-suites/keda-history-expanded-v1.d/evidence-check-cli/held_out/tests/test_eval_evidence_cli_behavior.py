"""把已验证的行为回放纳入正式题库；不固定未声明的旗标与 JSON 字段。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from tempfile import TemporaryDirectory
import pytest
from tests.eval_evidence_fixtures import _item, _write_manifest


def probe(
    candidate_source: Path, *, select_worktree: bool, evidence_flag: str, initialized: bool
) -> dict:
    with TemporaryDirectory(prefix="evidence-actual-") as temp:
        root = Path(temp)
        home = root / "home"
        home.mkdir()
        worktree = root / "checkout"
        worktree.mkdir()
        subprocess.run(["git", "init", "--quiet", str(worktree)], check=True, timeout=10)
        if initialized:
            (worktree / ".kedacode.toml").write_text(
                '[agent_runner.repository]\nid = "evidence-fixture"\n'
                '[agent_runner.validation]\nevidence_dir = "tasks/evidence"\nreexecute_timeout_seconds = 300\n',
                encoding="utf-8",
            )
        environment = {
            key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ
        }
        environment.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "KEDACODE_NO_UPDATE_CHECK": "1",
                "PYTHONPATH": str(candidate_source / "src"),
            }
        )

        project = tomllib.loads((candidate_source / "pyproject.toml").read_text(encoding="utf-8"))
        module, entrypoint = project["project"]["scripts"]["kc"].split(":")
        entry_code = f"from {module} import {entrypoint}; raise SystemExit({entrypoint}())"

        def invoke(*arguments: str):
            command = [sys.executable, "-c", entry_code, "evidence", "check"]
            if select_worktree:
                command.extend(["--worktree", str(worktree)])
            return subprocess.run(
                [*command, *arguments],
                cwd=worktree,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=18,
            )

        directories = worktree / "tasks" / "evidence"
        explicit = directories / "explicit-demo"
        marker = worktree / "invalid-item-executed"
        item = _item
        write = _write_manifest
        write(
            explicit,
            [
                item(1, "printf 'POSITIVE-A\\n'", assertions=[{"pattern": "POSITIVE-A"}]),
                item(2, "printf 'POSITIVE-B\\n'", assertions=[{"pattern": "POSITIVE-B"}]),
            ],
        )
        unique = invoke("--json")
        write(directories / "alternate-demo", [item(1, "exit 7")])
        ambiguous = invoke("--json")
        explicit_pass = invoke(evidence_flag, str(explicit), "--json")
        above_limit = invoke(evidence_flag, str(explicit), "--timeout", "301", "--json")

        write(
            explicit,
            [
                item(1, "printf 'ACTUAL-A closest\\n'", assertions=[{"pattern": "EXPECTED-A"}]),
                item(2, "printf 'ACTUAL-B closest\\n'", assertions=[{"pattern": "EXPECTED-B"}]),
            ],
        )
        assertions = invoke(evidence_flag, str(explicit), "--json")
        write(
            explicit,
            [
                item(1, "sleep 4"),
                item(2, "printf 'FAST-CHECK\\n'", assertions=[{"pattern": "FAST-CHECK"}]),
                item(3, "printf 'ACTUAL-LAST\\n'", assertions=[{"pattern": "EXPECTED-LAST"}]),
            ],
        )
        continued = invoke(evidence_flag, str(explicit), "--timeout", "1", "--json")
        invalid = item(2, "printf 'should not run'")
        invalid.pop("risks")
        write(explicit, [item(1, f'touch "{marker}"'), invalid])
        invalid_manifest = invoke(evidence_flag, str(explicit), "--json")
        write(explicit, [item(1, "printf 'legacy\\n'")])
        legacy_success = invoke(evidence_flag, str(explicit), "--json")
        write(explicit, [item(1, "exit 17")])
        legacy_failure = invoke(evidence_flag, str(explicit), "--json")

        def logged(result):
            return result.stdout + result.stderr

        def has_json(result):
            for stream in (result.stdout, result.stderr):
                try:
                    if isinstance(json.loads(stream), dict):
                        return True
                except json.JSONDecodeError:
                    continue
            return False

        checks = {
            "default_unique_manifest": unique.returncode == 0 and has_json(unique),
            "ambiguous_default": ambiguous.returncode == 2
            and has_json(ambiguous)
            and all(name in logged(ambiguous) for name in ("explicit-demo", "alternate-demo")),
            "explicit_manifest_wins": explicit_pass.returncode == 0 and has_json(explicit_pass),
            "timeout_above_gate_limit": above_limit.returncode == 2
            and has_json(above_limit)
            and "300" in logged(above_limit),
            "all_assertions_and_closest_lines": assertions.returncode == 1
            and has_json(assertions)
            and all(
                value in logged(assertions)
                for value in (
                    "EXPECTED-A",
                    "EXPECTED-B",
                    "ACTUAL-A closest",
                    "ACTUAL-B closest",
                )
            ),
            "timeout_reports_and_continues": continued.returncode == 1
            and has_json(continued)
            and "EXPECTED-LAST" in logged(continued)
            and any(
                value in logged(continued).lower()
                for value in (
                    "timed_out",
                    "timed out",
                    "timeout",
                )
            ),
            "invalid_manifest_runs_nothing": invalid_manifest.returncode == 1
            and has_json(invalid_manifest)
            and "risks" in logged(invalid_manifest)
            and not marker.exists(),
            "legacy_exit_code": legacy_success.returncode == 0
            and legacy_failure.returncode == 1
            and has_json(legacy_success)
            and has_json(legacy_failure),
        }
        results = (
            unique,
            ambiguous,
            explicit_pass,
            above_limit,
            assertions,
            continued,
            invalid_manifest,
            legacy_success,
            legacy_failure,
        )
        return {
            "source": str(candidate_source),
            "initialized": initialized,
            "flags": {"explicit_evidence": evidence_flag, "worktree_option": select_worktree},
            "passed": sum(checks.values()),
            "total": len(checks),
            "checks": checks,
            "commands": [
                {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
                for result in results
            ],
        }


@pytest.fixture(scope="module")
def behavior_results():
    source = Path.cwd()
    executable = Path(sys.executable).with_name("kc.exe" if os.name == "nt" else "kc")
    help_process = subprocess.run(
        [str(executable), "evidence", "check", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    help_text = help_process.stdout + help_process.stderr
    evidence_flag = "--evidence-dir" if "--evidence-dir" in help_text else "--evidence"
    return probe(source, select_worktree=False, evidence_flag=evidence_flag, initialized=True)


@pytest.mark.parametrize(
    "behavior",
    [
        "default_unique_manifest",
        "ambiguous_default",
        "explicit_manifest_wins",
        "timeout_above_gate_limit",
        "all_assertions_and_closest_lines",
        "timeout_reports_and_continues",
        "invalid_manifest_runs_nothing",
        "legacy_exit_code",
    ],
)
def test_evidence_cli_behavior(behavior_results, behavior):
    assert behavior_results["checks"][behavior], behavior_results["commands"]
