"""容器生命周期管理引擎模块 —— 封装 ``docker compose`` 子进程调用。

本模块负责 ``kc container up / down / logs`` 的实现部分。不引入 docker SDK，
仅通过 :func:`subprocess.run` 调 ``docker compose`` CLI；调用方提供 compose 文件
路径、运行时环境变量，本模块负责构建正确的命令与 env。

设计约束：

- **端口实现**：本模块的 :class:`ContainerOpsController` 实现 core 层定义的
  :class:`backend.core.shared.interfaces.container_runner.IContainerRunnerController`
  端口；``api`` 层在 dispatch 时把 ``ContainerOpsController()`` 注入到 core
  facade，core 不再直接 import 本模块。
- **可注入的 runner**：默认走 :mod:`subprocess`；测试可注入自定义 ``runner``
  callable ``(argv, env, cwd, check) -> CompletedProcess`` 捕获参数，无需真
  起容器。
- **环境变量合并**：传入 ``env_overrides`` 在 denylist 净化后的系统环境
  之上覆盖（会话私有变量不透传进容器，见 Issue #230），
  显式 ``RUNNER_UID``/``RUNNER_GID`` 从 ``os.getuid()``/``os.getgid()`` 推导。
- **失败语义**：默认 ``check=True``，docker compose 失败抛
  :class:`CalledProcessError`，调用方决定如何向上汇报。``dry_run=True`` 时不真
  调 docker，只打印计划。
- **不持久化 compose 文件**：compose 文件由 keda package data 提供（见
  ``templates/runner_container/``），本模块只定位路径并把它作为参数传入。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from backend.core.shared.models import product_identity
from backend.core.shared.interfaces.container_runner import (
    ContainerCommandPlan,
    ContainerGcRequest,
    ContainerGcResult,
    ContainerUpRequest,
    DockerRunnerCallable,
    IContainerRunnerController,
    RunnerContainerAssets,
)
from backend.infrastructure.child_env import build_sanitized_child_env
from backend.engines.agent_runner.container_gc import run_container_gc

TEMPLATE_PACKAGE_NAME = "backend.engines.agent_runner.templates"
RUNNER_CONTAINER_TEMPLATE_NAME = "runner_container"

# compose 文件内的持久化 service 名保持兼容，避免新版本无法停止已有容器。
RUNNER_SERVICE_NAME = "iar-runner"

# compose 文件名 + 示例 env 文件名。本地变体经 ``GH_TOKEN`` 环境变量认证（个人
# 电脑 keychain 容器读不到的权宜之计）；托管变体把 secret 边界改成 tmpfs 原生
# 认证文件挂载，environment 里**不含任何 key 值**（Issue #265 FR-5）。
COMPOSE_FILE_NAME = "docker-compose.runner.yml"
COMPOSE_HOSTED_FILE_NAME = "docker-compose.runner.hosted.yml"
COMPOSE_ENV_EXAMPLE_NAME = ".env.example"

RUNNER_AUTH_DIR_ENV_KEY = "KEDACODE_RUNNER_AUTH_DIR"


class _DockerRunner(Protocol):
    """docker compose 子进程运行协议，便于测试注入 fake。"""

    def __call__(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _default_docker_runner(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    """默认 docker compose 调用：直接走 :mod:`subprocess`。

    捕获 stdout/stderr 供短命令（``up -d`` / ``down``）抑制 docker 噪声；
    长命令（``logs``）用 :func:`_streaming_docker_runner` 走终端直通。
    """
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _streaming_docker_runner(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    """streaming docker compose 调用：stdout/stderr 直通终端，不捕获。

    供 ``logs``（尤其 ``--follow``）使用--``capture_output=True`` 会把流式
    日志全部缓存到内存且不回显，用户看到空输出直到 SIGINT。这里让
    :mod:`subprocess` 继承父进程 stdout/stderr，实时打印。
    """
    return subprocess.run(argv, cwd=cwd, env=env, check=check)


def resolve_packaged_runner_assets(*, hosted: bool = False) -> RunnerContainerAssets:
    """从 keda package data 定位 runner 容器资产路径。

    Args:
        hosted: True 时返回托管 compose 变体（``docker-compose.runner.hosted.yml``，
            environment 不含 secret），False 时返回本地变体（``GH_TOKEN`` 环境变量）。

    Returns:
        已解析为绝对 Path 的资产集合，``compose_file`` 指向所选变体。

    Raises:
        FileNotFoundError: 当所选 compose 文件不在 package data 中（典型：开发期
            keda 改动未安装，wheel 不含此目录）。
    """
    template_root = files(TEMPLATE_PACKAGE_NAME).joinpath(RUNNER_CONTAINER_TEMPLATE_NAME)
    selected_compose_name = COMPOSE_HOSTED_FILE_NAME if hosted else COMPOSE_FILE_NAME
    compose_resource = template_root.joinpath(selected_compose_name)
    env_example_resource = template_root.joinpath(COMPOSE_ENV_EXAMPLE_NAME)

    if not compose_resource.is_file():
        raise FileNotFoundError(
            f"runner container compose file not found in package data: "
            f"{TEMPLATE_PACKAGE_NAME}/{RUNNER_CONTAINER_TEMPLATE_NAME}/{selected_compose_name}"
        )

    return RunnerContainerAssets(
        template_dir=Path(str(template_root)),
        compose_file=Path(str(compose_resource)),
        env_example=Path(str(env_example_resource)) if env_example_resource.is_file() else Path(),
    )


def _build_compose_argv(compose_file: Path, action: str) -> list[str]:
    """构造 ``docker compose -f <file> <action> ...`` 的 argv 前缀。"""
    if not shutil.which("docker"):
        raise FileNotFoundError(
            "docker CLI not found on PATH. Install Docker Desktop or Docker Engine first."
        )
    return ["docker", "compose", "-f", str(compose_file), action]


def _resolve_uid_gid(request: ContainerUpRequest) -> tuple[int, int]:
    """解析容器内 UID/GID：``None`` 时回退到宿主当前用户。"""
    resolved_uid = request.uid if request.uid is not None else os.getuid()
    resolved_gid = request.gid if request.gid is not None else os.getgid()
    return resolved_uid, resolved_gid


def build_container_up_env(request: ContainerUpRequest) -> dict[str, str]:
    """构造传给 ``docker compose`` 的环境变量集合。

    ``REPO_PATH``、``RUNNER_UID``/``RUNNER_GID`` 是核心契约。本地模式（``hosted``
    为假）额外透传 ``GH_TOKEN``；**托管模式绝不注入任何 secret 值**，只传
    ``KEDACODE_RUNNER_AUTH_DIR``（该仓库 tmpfs 认证目录的宿主路径，不含 key），
    对应 compose 用 bind mount 把原生认证文件挂进容器（Issue #265 FR-5）。

    ``KEDACODE_HOST_STATE_HOME`` 取当前生效状态目录的绝对路径，供 compose 挂载
    宿主侧状态目录（本地模式还含其 ``container-auth`` 子目录）；直接跑 compose 时
    未设置会由 ``${KEDACODE_HOST_STATE_HOME:?...}`` 立即报错而非挂错目录。
    ``extra_env`` 覆盖默认项，便于调用方在不重写本函数的前提下补字段。

    Raises:
        ValueError: 托管模式下未提供 ``runner_auth_dir``（会导致 tmpfs 挂载缺失、
            runner 无凭据可读），fail-closed 拒绝而非静默启动无认证容器。
    """
    resolved_uid, resolved_gid = _resolve_uid_gid(request)
    base_env: dict[str, str] = {
        "REPO_PATH": str(request.repo_path),
        "RUNNER_UID": str(resolved_uid),
        "RUNNER_GID": str(resolved_gid),
    }
    if request.hosted:
        if not request.runner_auth_dir:
            raise ValueError(
                "hosted runner start requires runner_auth_dir (this repo's tmpfs "
                "credential directory); refusing to launch an unauthenticated container."
            )
        base_env[RUNNER_AUTH_DIR_ENV_KEY] = request.runner_auth_dir
    elif request.gh_token:
        base_env["GH_TOKEN"] = request.gh_token
    if request.repo_id:
        base_env.update(product_identity.build_child_env({"REPO_ID": request.repo_id}))
    base_env.update(_build_host_state_env())
    base_env.update(request.extra_env)
    return base_env


def build_container_up_argv(compose_file: Path, request: ContainerUpRequest) -> list[str]:
    """构造 ``docker compose up`` 的 argv。"""
    argv = _build_compose_argv(compose_file, "up")
    flags: list[str] = ["-d"]
    if request.build:
        flags.append("--build")
    argv.extend(flags)
    return argv


def build_container_down_argv(compose_file: Path) -> list[str]:
    """构造 ``docker compose down`` 的 argv。"""
    return _build_compose_argv(compose_file, "down")


def build_container_logs_argv(compose_file: Path, *, follow: bool = True) -> list[str]:
    """构造 ``docker compose logs`` 的 argv。

    ``follow=True`` 时附加 ``--follow``，使命令阻塞 streaming 容器日志。
    使用 ``--follow`` 长选项避免与 ``docker compose -f <file>`` 的短选项冲突。
    """
    argv = _build_compose_argv(compose_file, "logs")
    if follow:
        argv.append("--follow")
    return argv


def plan_container_up(compose_file: Path, request: ContainerUpRequest) -> ContainerCommandPlan:
    """生成 ``up`` 的命令计划而不执行。"""
    argv = build_container_up_argv(compose_file, request)
    env = build_container_up_env(request)
    return ContainerCommandPlan(
        argv=tuple(argv),
        env=env,
        compose_file=compose_file,
    )


def _build_host_state_env() -> dict[str, str]:
    """所有 compose 操作使用同一生效状态目录，避免 down/logs 插值失败。"""
    return {f"{product_identity.ENV_PREFIX}HOST_STATE_HOME": str(product_identity.state_home())}


def _build_compose_env(env_overrides: dict[str, str]) -> dict[str, str]:
    """在净化后的系统环境之上叠加 ``env_overrides``，得到传给 compose 的最终 env。

    基底用 denylist 净化档而非全量 ``os.environ``：compose 会把宿主环境
    内插/透传进容器内的 runner，会话私有变量（SERVER__PORT 等）不应
    沿这条谱系下传（Issue #230）。
    """
    merged_env = build_sanitized_child_env()
    merged_env.update(_build_host_state_env())
    merged_env.update(env_overrides)
    return merged_env


def run_container_up(
    compose_file: Path,
    request: ContainerUpRequest,
    *,
    runner: DockerRunnerCallable | None = None,
) -> ContainerCommandPlan:
    """执行 ``docker compose up``，可选 ``dry_run`` 走纯计划路径。"""
    plan = plan_container_up(compose_file, request)
    if request.dry_run:
        return plan
    effective_runner = runner if runner is not None else _default_docker_runner
    effective_runner(
        list(plan.argv),
        env=_build_compose_env(plan.env),
        cwd=compose_file.parent,
        check=True,
    )
    return plan


def run_container_down(
    compose_file: Path,
    *,
    env_overrides: dict[str, str] | None = None,
    runner: DockerRunnerCallable | None = None,
) -> list[str]:
    """执行 ``docker compose down``。"""
    argv = build_container_down_argv(compose_file)
    effective_runner = runner if runner is not None else _default_docker_runner
    effective_runner(
        list(argv),
        env=_build_compose_env(env_overrides or {}),
        cwd=compose_file.parent,
        check=True,
    )
    return argv


def run_container_logs(
    compose_file: Path,
    *,
    follow: bool = True,
    runner: DockerRunnerCallable | None = None,
) -> list[str]:
    """执行 ``docker compose logs``（默认 streaming）。

    默认走 :func:`_streaming_docker_runner` 让日志直通终端；``logs --follow``
    是长命令，不能用捕获式 runner（否则用户看到空输出直到 SIGINT）。测试可
    注入 ``runner`` 捕获 argv。
    """
    argv = build_container_logs_argv(compose_file, follow=follow)
    effective_runner = runner if runner is not None else _streaming_docker_runner
    effective_runner(
        list(argv),
        env=_build_compose_env({}),
        cwd=compose_file.parent,
        check=True,
    )
    return argv


class ContainerOpsController(IContainerRunnerController):
    """``IContainerRunnerController`` 的 engines 实现。

    由 ``api`` 层在 dispatch 时构造并注入到 core facade；本身无状态，
    所有方法都委托给模块级纯函数。
    """

    def resolve_packaged_runner_assets(self, *, hosted: bool = False) -> RunnerContainerAssets:
        """从 keda package data 定位 runner 容器资产路径（hosted 选择托管变体）。"""
        return resolve_packaged_runner_assets(hosted=hosted)

    def run_container_up(
        self,
        compose_file: Path,
        request: ContainerUpRequest,
        *,
        runner: DockerRunnerCallable | None = None,
    ) -> ContainerCommandPlan:
        """执行 ``docker compose up``；``dry_run=True`` 时不真调 docker。"""
        return run_container_up(compose_file, request, runner=runner)

    def run_container_down(
        self,
        compose_file: Path,
        *,
        env_overrides: dict[str, str] | None = None,
        runner: DockerRunnerCallable | None = None,
    ) -> list[str]:
        """执行 ``docker compose down``，可为托管 Compose 提供 repo 插值变量。"""
        return run_container_down(compose_file, env_overrides=env_overrides, runner=runner)

    def run_container_logs(
        self,
        compose_file: Path,
        *,
        follow: bool = True,
        runner: DockerRunnerCallable | None = None,
    ) -> list[str]:
        """执行 ``docker compose logs``（默认 streaming）。"""
        return run_container_logs(compose_file, follow=follow, runner=runner)

    def run_container_gc(
        self,
        request: ContainerGcRequest,
        *,
        runner: DockerRunnerCallable | None = None,
    ) -> ContainerGcResult:
        """扫描或清理安全的宿主 Docker 缓存。"""
        return run_container_gc(request, runner=runner)


__all__ = [
    "COMPOSE_ENV_EXAMPLE_NAME",
    "COMPOSE_FILE_NAME",
    "COMPOSE_HOSTED_FILE_NAME",
    "ContainerCommandPlan",
    "ContainerGcRequest",
    "ContainerGcResult",
    "ContainerOpsController",
    "ContainerUpRequest",
    "RUNNER_AUTH_DIR_ENV_KEY",
    "RUNNER_CONTAINER_TEMPLATE_NAME",
    "RUNNER_SERVICE_NAME",
    "RunnerContainerAssets",
    "build_container_down_argv",
    "build_container_logs_argv",
    "build_container_up_argv",
    "build_container_up_env",
    "plan_container_up",
    "resolve_packaged_runner_assets",
    "run_container_down",
    "run_container_gc",
    "run_container_logs",
    "run_container_up",
]
