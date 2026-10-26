#!/usr/bin/env python3
"""本 PRD 的 CLI 真实入口验证 harness；原始输出逐项写入 evidence root。"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
EVIDENCE_ROOT = SCRIPT_PATH.parents[1]
PROJECT_ROOT = SCRIPT_PATH.parents[4]
SCENE_ROOT = SCRIPT_PATH.parent / "scenes"
KC_CLI_PATH = PROJECT_ROOT / ".venv" / "bin" / "kc"
FAKE_GH_SOURCE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
state_path = Path(os.environ["KC_RV_FAKE_GH_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
state.setdefault("calls", []).append(arguments)

def save_state():
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

if arguments[:2] == ["auth", "status"]:
    save_state()
    print("✓ Logged in to github.com as evidence-bot (GH_TOKEN fixture)")
    raise SystemExit(0)

if not arguments or arguments[0] != "api":
    print(f"unsupported fake gh argv: {arguments!r}", file=sys.stderr)
    raise SystemExit(97)

if arguments[1:] == ["rate_limit"]:
    save_state()
    print(json.dumps({"resources": {"core": {"remaining": 5000, "limit": 5000}}}))
    raise SystemExit(0)

if arguments[1:] == ["user"]:
    save_state()
    print(json.dumps({"login": "evidence-bot"}))
    raise SystemExit(0)

method = "GET"
for index, argument in enumerate(arguments):
    if argument == "-X":
        method = arguments[index + 1].upper()
        break

endpoint = next((argument for argument in arguments if argument.startswith("repos/")), None)
if endpoint is None:
    print(f"unsupported fake gh API endpoint: {arguments!r}", file=sys.stderr)
    raise SystemExit(97)

if method == "GET" and endpoint == "repos/rv-owner/rv-repo/issues":
    save_state()
    print(json.dumps([[state["issue"]]], ensure_ascii=False))
    raise SystemExit(0)

if method == "GET" and endpoint == "repos/rv-owner/rv-repo/issues/17":
    save_state()
    print(json.dumps(state["issue"], ensure_ascii=False))
    raise SystemExit(0)

if method == "GET" and endpoint == "repos/rv-owner/rv-repo/issues/17/comments":
    save_state()
    print(json.dumps([state["comments"]], ensure_ascii=False))
    raise SystemExit(0)

if method == "PATCH" and endpoint == "repos/rv-owner/rv-repo/issues/comments/17001":
    body_argument = next(
        (arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == "-F"),
        None,
    )
    if body_argument is None or not body_argument.startswith("body=@"):
        print(f"unsupported fake gh patch fields: {arguments!r}", file=sys.stderr)
        raise SystemExit(97)
    comment_body = Path(body_argument.removeprefix("body=@")).read_text(encoding="utf-8")
    state["comments"][0]["body"] = comment_body
    save_state()
    print(json.dumps({"id": 17001}))
    raise SystemExit(0)

print(f"unsupported fake gh argv: {arguments!r}", file=sys.stderr)
raise SystemExit(97)
'''


def _record_path(item_number: int, slug: str) -> Path:
    """返回该 RV 的单独原始输出文件。"""
    return EVIDENCE_ROOT / f"rv-{item_number}-{slug}.txt"


def _record_line(record_path: Path, text: str) -> None:
    """将一行输出追加到唯一所属 RV 文件。"""
    with record_path.open("a", encoding="utf-8") as output_file:
        output_file.write(text)
        output_file.write("\n")


def _run_process(
    record_path: Path,
    command: list[str],
    *,
    cwd_path: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """运行真实 CLI 子进程并保留其原始输出与退出码。"""
    _record_line(record_path, f"$ {' '.join(command)}")
    completed_process = subprocess.run(
        command,
        cwd=cwd_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    _record_line(record_path, f"exit_code={completed_process.returncode}")
    _record_line(record_path, "stdout:")
    _record_line(record_path, completed_process.stdout.rstrip())
    _record_line(record_path, "stderr:")
    _record_line(record_path, completed_process.stderr.rstrip())
    return completed_process


def _write_fake_gh(fake_bin_path: Path) -> None:
    """写入只接受本 harness 所需 argv 的 fail-loud gh shim。"""
    fake_bin_path.mkdir(parents=True, exist_ok=True)
    fake_gh_path = fake_bin_path / "gh"
    fake_gh_path.write_text(FAKE_GH_SOURCE, encoding="utf-8")
    fake_gh_path.chmod(0o755)


def _write_repository_config(repository_path: Path, repository_id: str) -> None:
    """创建最小初始化仓库，Git 元数据与 CLI 均真实。"""
    repository_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repository_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.email", "rv@example.invalid"],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.name", "RV Harness"],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (repository_path / "README.md").write_text("rv fixture\n", encoding="utf-8")
    (repository_path / ".kedacode.toml").write_text(
        "[agent_runner.repository]\n"
        f'id = "{repository_id}"\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "README.md", ".kedacode.toml"],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-m", "initial rv fixture"],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _worktree_scene(item_number: int, *, inject_legacy_default: bool) -> tuple[Path, dict[str, str]]:
    """为 rv-3 / rv-4 创建隔离仓库并返回环境。"""
    scene_path = SCENE_ROOT / f"rv-{item_number}"
    if scene_path.exists():
        shutil.rmtree(scene_path)
    repository_path = scene_path / "repo"
    home_path = scene_path / "home"
    config_path = scene_path / "config.toml"
    home_path.mkdir(parents=True)
    config_path.write_text("", encoding="utf-8")
    _write_repository_config(repository_path, f"rv-{item_number}")
    environment = os.environ.copy()
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    environment.pop("KEDACODE_CONFIG", None)
    environment["HOME"] = str(home_path)
    environment["KEDACODE_CONFIG"] = str(config_path)
    environment["UV_CACHE_DIR"] = str(PROJECT_ROOT / ".uv-cache")
    if inject_legacy_default:
        sitecustomize_path = SCRIPT_PATH.parent / f"negative-rv-{item_number}"
        sitecustomize_path.mkdir(parents=True, exist_ok=True)
        sitecustomize_source = (
            "from backend.core.shared.models import repository_paths\n"
            "repository_paths.DEFAULT_WORKTREE_DIR_NAME = '.iar-worktrees'\n"
        )
        (sitecustomize_path / "sitecustomize.py").write_text(
            sitecustomize_source, encoding="utf-8"
        )
        current_pythonpath = environment.get("PYTHONPATH", "")
        injection_paths = [str(sitecustomize_path), str(PROJECT_ROOT / "src")]
        if current_pythonpath:
            injection_paths.append(current_pythonpath)
        environment["PYTHONPATH"] = os.pathsep.join(injection_paths)
    return repository_path, environment


def _run_worktree_oracle(item_number: int, *, negative: bool) -> None:
    """通过安装后的 kc CLI 与真实 Git 验证新目录或旧目录复用。"""
    slug = "negative-control" if negative else ("clean-repository" if item_number == 3 else "legacy-directory")
    record_path = _record_path(item_number, slug)
    record_path.write_text("", encoding="utf-8")
    repository_path, environment = _worktree_scene(
        item_number,
        inject_legacy_default=negative and item_number == 3,
    )
    if item_number == 4 and not negative:
        (repository_path / ".iar-worktrees").mkdir()
    if item_number == 4 and negative:
        sitecustomize_path = SCRIPT_PATH.parent / "negative-rv-4"
        sitecustomize_path.mkdir(parents=True, exist_ok=True)
        (sitecustomize_path / "sitecustomize.py").write_text(
            "from pathlib import Path\n"
            "from backend.core.shared.models import repository_paths\n"
            "repository_paths.resolve_managed_worktree_root = (\n"
            "    lambda repository_root_path: Path(repository_root_path) / '.kc-worktrees'\n"
            ")\n",
            encoding="utf-8",
        )
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(sitecustomize_path), str(PROJECT_ROOT / "src")]
        )
        (repository_path / ".iar-worktrees").mkdir()

    _record_line(
        record_path,
        f"scene={'negative control' if negative else 'acceptance'}; repo={repository_path}",
    )
    if not negative:
        expected_root_name = ".kc-worktrees" if item_number == 3 else ".iar-worktrees"
        _record_line(
            record_path,
            f"precondition: {expected_root_name} exists before create = "
            f"{(repository_path / expected_root_name).exists()}",
        )

    branch_name = f"rv-{item_number}-negative" if negative else f"rv-{item_number}-issue"
    create_command = [
        str(KC_CLI_PATH),
        "worktree",
        "create",
        "--branch",
        branch_name,
        "--base-branch",
        "main",
    ]
    create_result = _run_process(
        record_path,
        create_command,
        cwd_path=repository_path,
        environment=environment,
    )
    if create_result.returncode != 0:
        raise RuntimeError(f"kc worktree create failed; inspect {record_path}")

    expected_root_path = repository_path / (".kc-worktrees" if item_number == 3 else ".iar-worktrees")
    wrong_root_path = repository_path / (".iar-worktrees" if item_number == 3 else ".kc-worktrees")
    expected_worktree_path = expected_root_path / branch_name
    git_list_command = ["git", "worktree", "list"]
    git_list_result = _run_process(
        record_path,
        git_list_command,
        cwd_path=repository_path,
        environment=environment,
    )
    ls_command = ["ls", "-la", str(repository_path)]
    ls_result = _run_process(
        record_path,
        ls_command,
        cwd_path=repository_path,
        environment=environment,
    )
    status_worktree_path = wrong_root_path / branch_name if negative else expected_worktree_path
    git_status_result = _run_process(
        record_path,
        ["git", "status", "--short"],
        cwd_path=status_worktree_path,
        environment=environment,
    )
    if (
        git_list_result.returncode != 0
        or ls_result.returncode != 0
        or git_status_result.returncode != 0
    ):
        raise RuntimeError(f"filesystem / Git inspection failed; inspect {record_path}")

    if negative:
        try:
            assert expected_worktree_path.is_dir(), (
                f"expected worktree under {expected_root_path}; actual worktree is "
                f"{wrong_root_path / branch_name}"
            )
            assert not (wrong_root_path / branch_name).exists(), (
                f"unexpected parallel directory {wrong_root_path / branch_name}"
            )
        except AssertionError as expected_assertion:
            _record_line(record_path, f"EXPECTED_FAIL: {expected_assertion}")
        else:
            raise RuntimeError("negative control unexpectedly satisfied the acceptance assertion")
        return

    assert expected_worktree_path.is_dir(), f"missing {expected_worktree_path}"
    assert not (wrong_root_path / branch_name).exists(), f"unexpected {wrong_root_path / branch_name}"
    assert str(expected_worktree_path) in git_list_result.stdout
    assert not git_status_result.stdout.strip(), f"new worktree is not clean: {git_status_result.stdout}"
    _record_line(record_path, f"PASS: worktree path={expected_worktree_path}")
    _record_line(record_path, "PASS: git worktree list contains the new worktree")
    _record_line(record_path, "PASS: git status --short is clean in the new worktree")
    _record_line(record_path, "PASS: no parallel directory was created")


def _marker_state_path(scene_path: Path) -> Path:
    """返回 fake GitHub 的持久化 JSON 状态。"""
    return scene_path / "fake-github.json"


def _read_marker_state(scene_path: Path) -> dict[str, object]:
    """读取 fake GitHub 状态文件。"""
    return json.loads(_marker_state_path(scene_path).read_text(encoding="utf-8"))


def _write_marker_state(scene_path: Path, state: dict[str, object]) -> None:
    """更新 fake GitHub 状态文件。"""
    _marker_state_path(scene_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _fake_github_scene() -> tuple[Path, dict[str, str]]:
    """创建包含真实 git 仓库、持久 fake gh 边界和隔离 HOME 的迁移场景。"""
    scene_path = SCENE_ROOT / "rv-2"
    if scene_path.exists():
        shutil.rmtree(scene_path)
    repository_path = scene_path / "repo"
    home_path = scene_path / "home"
    fake_bin_path = scene_path / "bin"
    config_path = scene_path / "config.toml"
    home_path.mkdir(parents=True)
    config_path.write_text("", encoding="utf-8")
    _write_repository_config(repository_path, "rv-2")
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/rv-owner/rv-repo.git"],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _write_fake_gh(fake_bin_path)
    host_name = socket.gethostname()
    old_claim_body = (
        "Claim owner text remains byte-identical.\n"
        f'<!-- iar:claim host="{host_name}" pid="{os.getpid()}" '
        f'started_at="{time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}" -->'
    )
    marker_state: dict[str, object] = {
        "issue": {
            "number": 17,
            "title": "Running claim fixture",
            "html_url": "https://github.com/rv-owner/rv-repo/issues/17",
            "body": "Fixture issue body.",
            "labels": [{"name": "agent/running"}],
            "state": "OPEN",
        },
        "comments": [{"id": 17001, "body": old_claim_body}],
        "calls": [],
    }
    _write_marker_state(scene_path, marker_state)
    environment = os.environ.copy()
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    environment.pop("KEDACODE_CONFIG", None)
    environment["HOME"] = str(home_path)
    environment["KEDACODE_CONFIG"] = str(config_path)
    environment["KC_RV_FAKE_GH_STATE"] = str(_marker_state_path(scene_path))
    environment["PATH"] = os.pathsep.join([str(fake_bin_path), environment.get("PATH", "")])
    environment["UV_CACHE_DIR"] = str(PROJECT_ROOT / ".uv-cache")
    return repository_path, environment


def _run_marker_migration() -> None:
    """保留旧 marker 负控，并如实记录进程扫描受限导致的 CLI 阻断。"""
    item_number = 2
    record_path = _record_path(item_number, "marker-migration")
    record_path.write_text("", encoding="utf-8")
    repository_path, environment = _fake_github_scene()
    scene_path = repository_path.parent

    negative_run = _run_process(
        record_path,
        [
            str(KC_CLI_PATH),
            "run",
            "--issue",
            "17",
            "--dry-run",
            "--repo",
            str(repository_path),
            "--output",
            "json",
        ],
        cwd_path=repository_path,
        environment=environment,
    )
    negative_text = negative_run.stdout + negative_run.stderr
    try:
        assert "actively claimed" in negative_text.lower(), (
            "acceptance assertion expected an unmigrated claim to remain claimed"
        )
    except AssertionError as expected_failure:
        _record_line(record_path, f"EXPECTED_FAIL: {expected_failure}")
    else:
        raise RuntimeError("negative control unexpectedly recognized an old claim marker")
    assert negative_run.returncode == 10, (
        "unmigrated claim did not reach the dry-run candidate path; "
        f"exit={negative_run.returncode}, output={negative_text}"
    )
    assert "actively claimed" not in negative_text.lower(), (
        "unmigrated legacy claim unexpectedly blocked the new runner"
    )
    before_preview_body = str(_read_marker_state(scene_path)["comments"][0]["body"])
    preview_result = _run_process(
        record_path,
        [str(KC_CLI_PATH), "config", "migrate", "--repo", str(repository_path)],
        cwd_path=repository_path,
        environment=environment,
    )
    preview_text = preview_result.stdout + preview_result.stderr
    assert "Issue/PR #17 · comment 17001 · iar:claim × 1 · would update" in preview_text
    assert str(_read_marker_state(scene_path)["comments"][0]["body"]) == before_preview_body
    _record_line(record_path, "PASS: preview left the fake GitHub comment unchanged")
    if preview_result.returncode != 0:
        assert "process scanner unavailable" in preview_text
        _record_line(
            record_path,
            "BLOCKED: preview listed the GitHub marker but returned non-zero because this sandbox "
            "denies psutil process enumeration.",
        )

    new_home_state_path = Path(environment["HOME"]) / ".kedacode"
    new_home_state_path.mkdir(parents=True, exist_ok=True)
    process_registry_path = new_home_state_path / "processes.json"
    active_runner = subprocess.Popen(
        ["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        process_registry_path.write_text(
            json.dumps({"rv-daemon": {"pid": active_runner.pid}}),
            encoding="utf-8",
        )
        apply_result = _run_process(
            record_path,
            [
                str(KC_CLI_PATH),
                "config",
                "migrate",
                "--repo",
                str(repository_path),
                "--apply",
            ],
            cwd_path=repository_path,
            environment=environment,
        )
        apply_text = apply_result.stdout + apply_result.stderr
        assert apply_result.returncode != 0
        assert "process scanner unavailable" in apply_text
        assert str(_read_marker_state(scene_path)["comments"][0]["body"]) == before_preview_body
        _record_line(
            record_path,
            "EXPECTED_FAIL: --apply could not migrate because psutil process enumeration is "
            "denied; the marker remained unchanged and no GitHub edit occurred.",
        )
        _record_line(
            record_path,
            "BLOCKED: no migration-success or post-migration claim evidence is claimed from this host.",
        )
    finally:
        active_runner.terminate()
        active_runner.wait(timeout=5)
        process_registry_path.unlink(missing_ok=True)
    raise RuntimeError(
        "RV-2 remains unresolved: realistic --apply is blocked by denied process enumeration."
    )


def main() -> int:
    """按单一 RV 编号执行隔离场景。"""
    if len(sys.argv) != 2:
        raise SystemExit("usage: rv_harness.py rv-2|rv-3|rv-3-negative|rv-4|rv-4-negative")
    scenario_name = sys.argv[1]
    if scenario_name == "rv-2":
        _run_marker_migration()
    elif scenario_name == "rv-3":
        _run_worktree_oracle(3, negative=False)
    elif scenario_name == "rv-3-negative":
        _run_worktree_oracle(3, negative=True)
    elif scenario_name == "rv-4":
        _run_worktree_oracle(4, negative=False)
    elif scenario_name == "rv-4-negative":
        _run_worktree_oracle(4, negative=True)
    else:
        raise SystemExit(f"unknown scenario: {scenario_name}")
    print(f"PASS: {scenario_name}; evidence={EVIDENCE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
