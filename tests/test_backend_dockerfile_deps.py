"""Guard that the backend image installs what its entrypoint actually runs.

`src/backend/Dockerfile` 的 CMD 是 `sh backend/scripts/start.sh`，而该脚本第一件事
就是 `alembic upgrade head`；compose 栈又统一用 `postgresql+psycopg2://` 连库。
这两个包都只在 `[project.optional-dependencies] db` 里，所以一旦 Dockerfile 的
`uv sync` 丢掉 `--extra db`，镜像能构建成功、能推送、能启动容器，只会在运行期
以 `alembic: not found` 崩掉——构建阶段完全看不出来。

这里不去构建镜像（太重），而是用 Dockerfile 里真实写着的那组 sync flag 去做一次
`uv export`，断言解析结果确实包含入口脚本依赖的包。
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DOCKERFILE_PATH = REPO_ROOT / "src" / "backend" / "Dockerfile"
START_SCRIPT_PATH = REPO_ROOT / "src" / "backend" / "scripts" / "start.sh"

# 入口脚本调用的可执行文件 -> 提供它的发行包名（uv export 输出里的名字）。
ENTRYPOINT_COMMAND_PACKAGES = {
    "alembic": "alembic",
    "uvicorn": "uvicorn",
}

# compose 栈的 DATABASE_URL 驱动 -> 提供它的发行包名。
DATABASE_DRIVER_PACKAGE = "psycopg2-binary"


def _read_dockerfile_uv_sync_flags() -> list[str]:
    """Extract the `uv sync` flags from the backend Dockerfile.

    Returns:
        `uv sync` 后面的参数列表，例如 `["--frozen", "--no-dev", "--extra", "db"]`。

    Raises:
        AssertionError: Dockerfile 里找不到 `uv sync` 指令。
    """
    dockerfile_text = BACKEND_DOCKERFILE_PATH.read_text(encoding="utf-8")
    # Dockerfile 里的 RUN 可能带续行反斜杠，先把续行拼回单行再匹配。
    joined_text = dockerfile_text.replace("\\\n", " ")
    sync_match = re.search(r"uv sync([^\n]*)", joined_text)
    assert sync_match is not None, f"{BACKEND_DOCKERFILE_PATH} 里没有找到 `uv sync` 指令"
    return shlex.split(sync_match.group(1))


def _export_resolved_package_names(sync_flags: list[str]) -> set[str]:
    """Resolve the dependency set that the given `uv sync` flags would install."""
    export_flags = [flag for flag in sync_flags if flag != "--frozen"]
    result = subprocess.run(
        ["uv", "export", "--frozen", "--no-emit-project", "--no-hashes", *export_flags],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    package_names: set[str] = set()
    for line in result.stdout.splitlines():
        requirement_match = re.match(r"^([A-Za-z0-9._-]+)==", line.strip())
        if requirement_match:
            package_names.add(requirement_match.group(1).lower())
    return package_names


@pytest.fixture(scope="module")
def dockerfile_resolved_packages() -> set[str]:
    """Resolve the backend image dependency set once for the whole module."""
    return _export_resolved_package_names(_read_dockerfile_uv_sync_flags())


def test_start_script_commands_are_installed_by_dockerfile(dockerfile_resolved_packages):
    """start.sh 调用的每个可执行文件都必须由镜像的依赖集提供。"""
    start_script_text = START_SCRIPT_PATH.read_text(encoding="utf-8")

    for command_name, package_name in ENTRYPOINT_COMMAND_PACKAGES.items():
        if not re.search(rf"(^|\s){re.escape(command_name)}\s", start_script_text, re.MULTILINE):
            continue
        assert package_name.lower() in dockerfile_resolved_packages, (
            f"start.sh 调用了 `{command_name}`，但 Dockerfile 的 uv sync 没有安装 "
            f"`{package_name}`。镜像会构建成功、启动时才以 "
            f"`{command_name}: not found` 崩溃。"
        )


def test_dockerfile_installs_postgres_driver(dockerfile_resolved_packages):
    """compose 栈用 `postgresql+psycopg2://`，驱动必须在镜像里。"""
    assert DATABASE_DRIVER_PACKAGE.lower() in dockerfile_resolved_packages, (
        f"compose 的 DATABASE_URL 使用 psycopg2 驱动，但镜像未安装 " f"{DATABASE_DRIVER_PACKAGE}。"
    )


def test_dockerfile_still_excludes_dev_dependencies(dockerfile_resolved_packages):
    """生产镜像不应把开发依赖一并打进去。"""
    assert "pytest" not in dockerfile_resolved_packages
