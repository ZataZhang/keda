"""Regression tests for the preview stack deploy helper.

这些用例用一个假的 `docker` 可执行文件顶替真实 docker：脚本本身只负责编排，
真正需要守住的是"失败时到底有没有把 backend 日志打出来"。历史上诊断分支写在
`up -d` 之后，而 `set -euo pipefail` 会让 `up -d` 一失败就退出，导致那段日志
转储在最需要它的路径上根本执行不到。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT_PATH = REPO_ROOT / "deploy" / "vps-traefik" / "deploy-preview.sh"

# 假 docker：把每次调用的参数追加到 $DOCKER_CALL_LOG，并按 $DOCKER_FAIL_ON
# 决定哪个子命令返回失败。`up` 与 `logs` 的判定都基于完整参数串。
FAKE_DOCKER_SCRIPT = """\
#!/usr/bin/env bash
echo "$*" >> "${DOCKER_CALL_LOG}"
if [ -n "${DOCKER_FAIL_ON:-}" ] && [[ "$*" == *"${DOCKER_FAIL_ON}"* ]]; then
  echo "fake docker: failing on ${DOCKER_FAIL_ON}" >&2
  exit 1
fi
exit 0
"""


def _make_fake_docker(tmp_path: Path) -> Path:
    """Create a fake `docker` executable and return the directory holding it."""
    fake_bin_dir = tmp_path / "fake-bin"
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    fake_docker_path = fake_bin_dir / "docker"
    fake_docker_path.write_text(FAKE_DOCKER_SCRIPT, encoding="utf-8")
    fake_docker_path.chmod(0o755)
    return fake_bin_dir


def _run_deploy_script(
    tmp_path: Path,
    *,
    fail_on: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run `deploy-preview.sh up` against a fake docker.

    Args:
        tmp_path: pytest 提供的临时目录，用作 APP_DIR 与假 bin 目录的父目录。
        fail_on: 出现在 docker 参数串里即触发失败的子串，None 表示全部成功。

    Returns:
        子进程结果，以及假 docker 记录下来的调用日志全文。
    """
    fake_bin_dir = _make_fake_docker(tmp_path)
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    docker_call_log = tmp_path / "docker-calls.log"
    docker_call_log.write_text("", encoding="utf-8")

    script_env = os.environ.copy()
    script_env.update(
        {
            "PATH": f"{fake_bin_dir}{os.pathsep}{script_env['PATH']}",
            "DOCKER_CALL_LOG": str(docker_call_log),
            "APP_DIR": str(app_dir),
            "COMPOSE_PROJECT_NAME": "keda-pr-1",
            "PREVIEW_DOMAIN": "pr-1.preview.example.com",
            "PREVIEW_URL_SCHEME": "https",
            "BACKEND_IMAGE": "ghcr.io/example-owner/keda-backend:deadbeef",
            "FRONTEND_IMAGE": "ghcr.io/example-owner/keda-frontend:deadbeef",
            "REGISTRY_HOST": "ghcr.io",
            "REGISTRY_NAMESPACE": "example-owner",
            "TRAEFIK_NETWORK": "traefik",
            "TRAEFIK_ROUTER_NAME": "keda-pr-1",
            "TRAEFIK_SERVICE_NAME": "keda-pr-1",
            "POSTGRES_USER": "keda",
            "POSTGRES_PASSWORD": "unused-in-test",
            "POSTGRES_DB": "keda_preview",
            "DATABASE_URL": "postgresql+psycopg2://keda:unused-in-test@db:5432/keda_preview",
            # 默认 30 × 2s 会让"探测始终失败"的用例跑满 60 秒。
            "PREVIEW_HEALTH_RETRIES": "2",
            "PREVIEW_HEALTH_INTERVAL_SECONDS": "0",
        }
    )
    if fail_on is not None:
        script_env["DOCKER_FAIL_ON"] = fail_on

    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT_PATH), "up"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=script_env,
        # 无 timeout 的 capture_output 子进程一旦阻塞就永久挂起。
        timeout=60,
    )
    return result, docker_call_log.read_text(encoding="utf-8")


def test_up_dumps_backend_logs_when_compose_up_fails(tmp_path: Path):
    """`up -d` 失败时必须转储 backend 日志，而不是被 set -e 直接带走。"""
    result, docker_calls = _run_deploy_script(tmp_path, fail_on="up -d")

    assert result.returncode == 1
    assert "logs --tail=100 backend" in docker_calls, (
        "compose up 失败后没有执行日志转储，诊断分支再次变成不可达：\n"
        f"docker 调用记录:\n{docker_calls}"
    )
    assert "ps -a" in docker_calls
    assert "backend never became healthy" in result.stderr


def test_up_dumps_backend_logs_when_health_probe_never_passes(tmp_path: Path):
    """健康探测始终失败时，同样必须转储日志。"""
    result, docker_calls = _run_deploy_script(tmp_path, fail_on="curl")

    assert result.returncode == 1
    assert "logs --tail=100 backend" in docker_calls
    assert "Backend failed to become healthy" in result.stderr


def test_up_succeeds_without_dumping_logs(tmp_path: Path):
    """一切正常时不应打印诊断信息，避免在绿色部署里刷无关日志。"""
    result, docker_calls = _run_deploy_script(tmp_path)

    assert result.returncode == 0
    assert "Backend is healthy." in result.stdout
    assert "logs --tail=100 backend" not in docker_calls


@pytest.mark.parametrize("missing_variable", ["APP_DIR", "COMPOSE_PROJECT_NAME"])
def test_up_requires_app_dir_and_project_name(tmp_path: Path, missing_variable: str):
    """缺少必需变量时必须立刻失败，而不是拿空路径去操作 docker。"""
    fake_bin_dir = _make_fake_docker(tmp_path)
    script_env = os.environ.copy()
    script_env.update(
        {
            "PATH": f"{fake_bin_dir}{os.pathsep}{script_env['PATH']}",
            "DOCKER_CALL_LOG": str(tmp_path / "docker-calls.log"),
            "APP_DIR": str(tmp_path / "app"),
            "COMPOSE_PROJECT_NAME": "keda-pr-1",
        }
    )
    del script_env[missing_variable]

    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT_PATH), "up"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=script_env,
        timeout=60,
    )

    assert result.returncode == 1
    assert "APP_DIR and COMPOSE_PROJECT_NAME must be set" in result.stderr
