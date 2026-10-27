"""Tests for ``backend.core.use_cases.agent_runner_container``.

覆盖：
- ``start_runner_container``：daemon lock 互斥、env 注入、目标仓库缺失报错
- ``start_runner_container --dry-run`` 不调 docker runner
- ``stop_runner_container`` / ``stream_runner_container_logs`` 透传到 engines
- core facade 不直接 import engines（架构守门）
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from backend.core.shared.interfaces.container_runner import ContainerGcRequest
from backend.core.shared.models import product_identity
from backend.core.use_cases.agent_runner_container import (
    StartRunnerContainerOptions,
    import_container_auth,
    start_runner_container,
    stop_runner_container,
    stream_runner_container_logs,
)
from backend.core.use_cases.daemon_single_instance import DaemonAlreadyRunningError
from backend.engines.agent_runner.container_auth import (
    AgentImportSpec,
    ContainerAuthController,
)
from backend.engines.agent_runner.container_gc import run_container_gc
from backend.engines.agent_runner.container_ops import ContainerOpsController


@pytest.fixture
def fake_global_kc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``Path.home()`` 指向临时目录，避免污染本机 ``~/.kedacode``。"""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def test_container_ops_controller_resolves_assets() -> None:
    """``ContainerOpsController`` 能定位包内 compose 文件。"""
    controller = ContainerOpsController()
    assets = controller.resolve_packaged_runner_assets()
    assert assets.compose_file.is_absolute()
    assert assets.compose_file.name == "docker-compose.runner.yml"


def test_runner_compose_is_single_service_without_public_port_or_socket() -> None:
    """runner Compose 保持单服务、无发布端口与 Docker socket，且日志有界。"""
    compose_path = ContainerOpsController().resolve_packaged_runner_assets().compose_file
    compose_settings = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    service_map = compose_settings["services"]
    runner_service = service_map["iar-runner"]
    mounted_volumes = runner_service.get("volumes", [])

    assert set(service_map) == {"iar-runner"}
    assert "ports" not in runner_service
    assert runner_service.get("expose", []) == []
    assert all("docker.sock" not in str(volume) for volume in mounted_volumes)
    assert runner_service["logging"] == {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }


def test_hosted_runner_compose_removes_secret_env_and_mounts_tmpfs_auth() -> None:
    """托管 Compose 变体：environment 不含 GH_TOKEN，凭据走 tmpfs runner-auth 挂载。"""
    compose_path = ContainerOpsController().resolve_packaged_runner_assets(hosted=True).compose_file
    compose_settings = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    service_map = compose_settings["services"]
    runner_service = service_map["kedacode-runner"]
    environment = runner_service.get("environment", {})
    mounted_volumes = [str(volume) for volume in runner_service.get("volumes", [])]

    # 与本地变体同样的资源卫生边界
    assert set(service_map) == {"kedacode-runner"}
    assert "ports" not in runner_service
    assert runner_service.get("expose", []) == []
    assert all("docker.sock" not in volume for volume in mounted_volumes)
    assert runner_service["logging"] == {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }

    # secret 边界：不设 GH_TOKEN，改设 GH_CONFIG_DIR 路径 + tmpfs 原生认证挂载
    assert "GH_TOKEN" not in environment
    assert environment.get("GH_CONFIG_DIR") == "/home/runner/.config/gh"
    assert any("KEDACODE_RUNNER_AUTH_DIR" in volume for volume in mounted_volumes)
    assert any("/home/runner/.config/gh" in volume for volume in mounted_volumes)
    assert all("KEDACODE_HOST_STATE_HOME" not in volume for volume in mounted_volumes)
    assert "runner_state:/home/runner/.kedacode" in mounted_volumes
    assert compose_settings["volumes"]["runner_state"]["name"] == (
        "kedacode-hosted-${KEDACODE_REPO_ID:?set by the hosted runner manager}-state"
    )


def test_start_runner_container_hosted_uses_hosted_compose_without_secret_env(
    tmp_path: Path, fake_global_kc: Path
) -> None:
    """``hosted=True`` 编排选托管 compose、传 tmpfs 目录、env 不含 GH_TOKEN。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    captured: list[dict] = []

    def fake_runner(argv, *, env, cwd, check):  # noqa: ARG001
        captured.append(dict(env))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="keda",
        gh_token="ghp_must_not_leak",
        uid=501,
        gid=20,
        process_registry_path=str(fake_global_kc / ".kedacode" / "processes.json"),
        dry_run=True,
        hosted=True,
        runner_auth_dir="/run/kedacode/runner-auth/keda",
    )
    controller = ContainerOpsController()
    result = start_runner_container(controller, options, runner=fake_runner)

    assert result.assets.compose_file.name == "docker-compose.runner.hosted.yml"
    assert "GH_TOKEN" not in result.plan.env
    assert result.plan.env["KEDACODE_RUNNER_AUTH_DIR"] == "/run/kedacode/runner-auth/keda"
    # dry_run 不调 runner
    assert captured == []


def test_start_runner_container_hosted_requires_auth_dir(
    tmp_path: Path, fake_global_kc: Path
) -> None:
    """``hosted=True`` 缺少 runner_auth_dir 时 fail-closed 抛 ValueError。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="keda",
        process_registry_path=str(fake_global_kc / ".kedacode" / "processes.json"),
        dry_run=True,
        hosted=True,
    )
    controller = ContainerOpsController()
    with pytest.raises(ValueError, match="runner_auth_dir"):
        start_runner_container(controller, options)


def test_container_gc_preview_preserves_referenced_and_recent_resources() -> None:
    """GC 预览只标记未引用悬空镜像及过期、可回收 cache。"""
    now = datetime(2026, 10, 10, tzinfo=UTC)
    stale_image_id = "sha256:" + "a" * 64
    referenced_image_id = "sha256:" + "b" * 64
    stale_cache_id = "cache_stale_1"
    recent_cache_id = "cache_recent_2"
    active_cache_id = "cache_active_3"
    old_time = (now - timedelta(days=100)).isoformat()
    recent_time = (now - timedelta(days=12)).isoformat()
    buildx_json_lines = "\n".join(
        json.dumps(cache_record)
        for cache_record in (
            {"ID": stale_cache_id, "LastUsedAt": old_time, "Reclaimable": True},
            {"ID": recent_cache_id, "LastUsedAt": recent_time, "Reclaimable": True},
            {"ID": active_cache_id, "LastUsedAt": old_time, "Reclaimable": False},
        )
    )
    called_argv: list[list[str]] = []

    def fake_runner(argv, *, env, cwd, check):  # noqa: ARG001
        called_argv.append(argv)
        if argv[1:3] == ["image", "ls"]:
            stdout = f"{stale_image_id}\n{referenced_image_id}\n"
        elif argv[1:3] == ["container", "ls"]:
            stdout = "container-id\n" if referenced_image_id in argv[-1] else ""
        elif argv[1:4] == ["buildx", "du", "--format=json"]:
            stdout = buildx_json_lines
        else:
            raise AssertionError(f"Preview unexpectedly attempted mutation: {argv}")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr="")

    gc_result = run_container_gc(
        ContainerGcRequest(cache_retention_days=90),
        runner=fake_runner,
        now=now,
    )

    status_by_resource = {
        (entry.resource_type, entry.resource_id): entry.status for entry in gc_result.entries
    }
    assert gc_result.scanned_count == 5
    assert gc_result.eligible_count == 2
    assert gc_result.deleted_count == 0
    assert gc_result.skipped_count == 3
    assert gc_result.failed_count == 0
    assert status_by_resource[("image", stale_image_id)] == "eligible"
    assert status_by_resource[("image", referenced_image_id)] == "skipped"
    assert status_by_resource[("build-cache", stale_cache_id)] == "eligible"
    assert status_by_resource[("build-cache", recent_cache_id)] == "skipped"
    assert status_by_resource[("build-cache", active_cache_id)] == "skipped"
    assert all("volume" not in " ".join(argv) for argv in called_argv)


def test_container_gc_apply_deletes_only_scanned_safe_candidates() -> None:
    """apply 对扫描得到的精确候选执行非强制镜像删除和精确 cache prune。"""
    now = datetime(2026, 10, 10, tzinfo=UTC)
    dangling_image_id = "sha256:" + "c" * 64
    reclaimable_cache_id = "cache_old_4"
    old_time = (now - timedelta(days=100)).isoformat()
    called_argv: list[list[str]] = []

    def fake_runner(argv, *, env, cwd, check):  # noqa: ARG001
        called_argv.append(argv)
        if argv[1:3] == ["image", "ls"]:
            stdout = f"{dangling_image_id}\n"
        elif argv[1:3] == ["container", "ls"]:
            stdout = ""
        elif argv[1:4] == ["buildx", "du", "--format=json"]:
            stdout = json.dumps(
                {"ID": reclaimable_cache_id, "LastUsedAt": old_time, "Reclaimable": True}
            )
        else:
            stdout = "removed"
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr="")

    gc_result = run_container_gc(
        ContainerGcRequest(apply=True, cache_retention_days=90),
        runner=fake_runner,
        now=now,
    )

    assert gc_result.eligible_count == 2
    assert gc_result.deleted_count == 2
    assert gc_result.failed_count == 0
    assert ["docker", "image", "rm", dangling_image_id] in called_argv
    assert not any("--force" in argv for argv in called_argv if argv[1:3] == ["image", "rm"])
    assert [
        "docker",
        "buildx",
        "prune",
        "--filter",
        f"id={reclaimable_cache_id}",
        "--filter",
        "inuse=false",
        "--force",
    ] in called_argv


def test_container_gc_conservatively_handles_relative_buildx_ages() -> None:
    """相对时间接近保留期时保留；明确超过窗口的 cache 才能回收。"""
    now = datetime(2026, 10, 10, tzinfo=UTC)
    buildx_json_lines = "\n".join(
        json.dumps(cache_record)
        for cache_record in (
            {"ID": "cache_three_months", "LastUsedAt": "3 months ago", "Reclaimable": True},
            {"ID": "cache_four_months", "LastUsedAt": "4 months ago", "Reclaimable": True},
        )
    )

    def fake_runner(argv, *, env, cwd, check):  # noqa: ARG001
        stdout = buildx_json_lines if argv[1:3] == ["buildx", "du"] else ""
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr="")

    gc_result = run_container_gc(
        ContainerGcRequest(cache_retention_days=90),
        runner=fake_runner,
        now=now,
    )
    status_by_cache_id = {entry.resource_id: entry.status for entry in gc_result.entries}

    assert status_by_cache_id["cache_three_months"] == "skipped"
    assert status_by_cache_id["cache_four_months"] == "eligible"


def test_container_auth_controller_imports_through_facade(
    fake_global_kc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``import_container_auth`` 经 facade 调用 engines 实现并返回结果。"""
    # 用临时 missing specs 让所有 agent 都 skipped
    from backend.engines.agent_runner import container_auth as ca_module

    missing_specs = tuple(
        AgentImportSpec(
            agent_name=spec.agent_name,
            source_dir=fake_global_kc / "missing",
            target_subdir=spec.target_subdir,
            include_top_level=spec.include_top_level,
            exclude_subpaths=spec.exclude_subpaths,
        )
        for spec in ca_module.SUPPORTED_AGENT_SPECS
    )
    monkeypatch.setattr(ca_module, "SUPPORTED_AGENT_SPECS", missing_specs)

    importer = ContainerAuthController()
    result = import_container_auth(importer)
    assert result.container_auth_dir == (
        fake_global_kc / product_identity.STATE_DIR_NAME / "container-auth"
    )
    # 由于 SUPPORTED_AGENT_SPECS 被 monkeypatched 但 default 参数在函数定义时已绑定，
    # 这里直接用底层函数验证：
    direct_result = ca_module.import_container_auth(specs=missing_specs)
    assert all(r.skipped for r in direct_result.agent_results)


def test_start_runner_container_missing_repo_path(tmp_path: Path, fake_global_kc: Path) -> None:
    """``start_runner_container`` 在目标仓库路径不存在时抛 ``FileNotFoundError``。"""
    options = StartRunnerContainerOptions(
        repo_path=tmp_path / "does-not-exist",
        repo_id="keda",
        process_registry_path=str(fake_global_kc / ".iar" / "processes.json"),
    )
    controller = ContainerOpsController()
    with pytest.raises(FileNotFoundError):
        start_runner_container(controller, options)


def test_start_runner_container_refuses_when_host_daemon_lock_live(
    tmp_path: Path, fake_global_kc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """当目标 repo_id 已有活 PID daemon lock 时拒绝启动，且不调 docker runner。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    lock_dir = fake_global_kc / ".iar" / "daemon-locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "keda.lock").write_text("99999\n", encoding="utf-8")

    from backend.core.use_cases import agent_runner_container as facade_module

    monkeypatch.setattr(facade_module, "_is_pid_alive", lambda _pid: True)

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="keda",
        process_registry_path=str(fake_global_kc / ".iar" / "processes.json"),
        dry_run=True,
    )

    called: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        called.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    controller = ContainerOpsController()
    with pytest.raises(DaemonAlreadyRunningError):
        start_runner_container(controller, options, runner=fake_runner)
    assert called == []


def test_start_runner_container_passes_env_to_runner(tmp_path: Path, fake_global_kc: Path) -> None:
    """``start_runner_container`` 把 env 传给注入的 docker runner。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    captured: list[dict] = []

    def fake_runner(argv, *, env, cwd, check):
        captured.append(dict(env))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="keda",
        gh_token="ghp_test",
        uid=501,
        gid=20,
        process_registry_path=str(fake_global_kc / ".iar" / "processes.json"),
    )
    controller = ContainerOpsController()
    result = start_runner_container(controller, options, runner=fake_runner)

    assert len(captured) == 1
    env = captured[0]
    assert env["REPO_PATH"] == str(repo_path)
    assert env["RUNNER_UID"] == "501"
    assert env["RUNNER_GID"] == "20"
    assert env["GH_TOKEN"] == "ghp_test"
    assert env["KEDACODE_REPO_ID"] == "keda"

    assert result.repo_id == "keda"
    assert result.daemon_lock_check_skipped is False
    assert "up" in result.plan.argv


def test_start_runner_container_dry_run_skips_runner(tmp_path: Path, fake_global_kc: Path) -> None:
    """``dry_run=True`` 时不调 docker runner。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    called: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        called.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="",
        process_registry_path=str(fake_global_kc / ".iar" / "processes.json"),
        dry_run=True,
    )
    controller = ContainerOpsController()
    result = start_runner_container(controller, options, runner=fake_runner)

    assert called == []
    assert "up" in result.plan.argv
    assert result.daemon_lock_check_skipped is True


def test_start_runner_container_no_repo_id_skips_lock_check(
    tmp_path: Path, fake_global_kc: Path
) -> None:
    """未传 ``repo_id`` 时跳过 daemon lock 检查（标 ``daemon_lock_check_skipped=True``）。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    called: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        called.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="",
        process_registry_path=str(fake_global_kc / ".iar" / "processes.json"),
    )
    controller = ContainerOpsController()
    result = start_runner_container(controller, options, runner=fake_runner)

    assert result.daemon_lock_check_skipped is True
    assert len(called) == 1


def test_start_runner_container_stale_daemon_lock_is_not_blocking(
    tmp_path: Path, fake_global_kc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目标 repo_id 的 daemon lock 是 stale PID（已死）时不应阻止启动。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    stale_pid = 2_147_483_647
    lock_dir = fake_global_kc / ".iar" / "daemon-locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "keda.lock").write_text(f"{stale_pid}\n", encoding="utf-8")

    from backend.core.use_cases import agent_runner_container as facade_module

    monkeypatch.setattr(facade_module, "_is_pid_alive", lambda pid: pid != stale_pid)

    called: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        called.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    options = StartRunnerContainerOptions(
        repo_path=repo_path,
        repo_id="keda",
        process_registry_path=str(fake_global_kc / ".iar" / "processes.json"),
    )
    controller = ContainerOpsController()
    start_runner_container(controller, options, runner=fake_runner)
    assert len(called) == 1


def test_stop_runner_container_forwards_to_engines(tmp_path: Path) -> None:
    """``stop_runner_container`` 透传到 engines 实现。"""
    compose = tmp_path / "docker-compose.runner.yml"
    compose.write_text("", encoding="utf-8")

    captured: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        captured.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    controller = ContainerOpsController()
    argv = stop_runner_container(controller, compose, runner=fake_runner)
    assert captured[0] == argv
    assert "down" in argv


def test_stream_runner_container_logs_forwards_to_engines(tmp_path: Path) -> None:
    """``stream_runner_container_logs`` 透传到 engines 实现。"""
    compose = tmp_path / "docker-compose.runner.yml"
    compose.write_text("", encoding="utf-8")

    captured: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        captured.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    controller = ContainerOpsController()
    argv = stream_runner_container_logs(controller, compose, runner=fake_runner)
    assert "logs" in argv
    assert "--follow" in argv
    assert captured[0] == argv


def test_stream_runner_container_logs_no_follow(tmp_path: Path) -> None:
    """``follow=False`` 时不含 ``--follow`` 跟随标志。"""
    compose = tmp_path / "docker-compose.runner.yml"
    compose.write_text("", encoding="utf-8")

    captured: list[tuple] = []

    def fake_runner(argv, *, env, cwd, check):
        captured.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    controller = ContainerOpsController()
    argv = stream_runner_container_logs(controller, compose, follow=False, runner=fake_runner)
    assert "--follow" not in argv


@pytest.mark.parametrize("operation", ["down", "logs"])
def test_container_operations_inject_effective_state_home(
    fake_global_kc: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """停止与日志命令无需继承 up 的临时环境，也能解析生效状态挂载。"""
    expected_state_home = fake_global_kc / ".kedacode"
    expected_state_home.mkdir()
    (fake_global_kc / ".iar").mkdir()
    monkeypatch.delenv("KEDACODE_HOST_STATE_HOME", raising=False)
    controller = ContainerOpsController()
    compose_file = controller.resolve_packaged_runner_assets().compose_file
    captured_envs: list[dict[str, str]] = []

    def capture_runner(argv, *, env, cwd, check):
        captured_envs.append(env)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    if operation == "down":
        stop_runner_container(controller, compose_file, runner=capture_runner)
    else:
        stream_runner_container_logs(controller, compose_file, runner=capture_runner)

    assert captured_envs[0]["KEDACODE_HOST_STATE_HOME"] == str(expected_state_home)


def test_core_facade_does_not_import_engines() -> None:
    """架构守门：core facade 不直接 import ``backend.engines.*``。"""
    facade_path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "backend"
        / "core"
        / "use_cases"
        / "agent_runner_container.py"
    )
    source = facade_path.read_text(encoding="utf-8")
    forbidden = re.compile(r"^\s*from\s+backend\.engines", flags=re.MULTILINE)
    assert forbidden.search(source) is None, (
        "agent_runner_container.py (core) must not directly import backend.engines.* — "
        "route through IContainerRunnerController / IContainerAuthImporter ports."
    )
