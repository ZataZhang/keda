"""托管 runner 的运行时凭据 tmpfs 交付（Issue #265，FR-5）。

实现 core 契约 ``RunnerCredentialHandoff``：宿主服务只在 runner 启动期间把
**该仓库自己的** GitHub/Agent 凭据解密写到 Linux tmpfs 的仓库专属目录
``<runner_auth_root>/<repo_id>/``（目录 ``0700``、文件 ``0600``），再以 bind
mount 交给对应容器；Docker 配置只含挂载目标路径，不含 key 值。runner 退出、
凭据更新或删除后由 ``erase`` 清空该仓库目录。

安全不变式（对应 Acceptance「GitHub/Agent 凭据按仓库隔离」）：

- 每个 ``repo_id`` 落在独立子目录，materialize/erase 只操作自己那一份；
  另一个仓库/客户的路径永远读不到本仓库文件。
- 目录名经校验，禁止 ``/``、``..``、空串，避免路径逃逸到别的仓库或宿主根。
- 明文只在该目录存在；``host_directory`` 返回值用于 compose 挂载，绝不含 key 值。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from backend.core.shared.models.hosted_console import RunnerGitHubCredential

_logger = logging.getLogger(__name__)

_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

_DIR_MODE = 0o700
_FILE_MODE = 0o600

#: 与三个受支持 CLI 的原生认证文件布局一一对应（tmpfs 交付目标）。
_GH_CONFIG_SUBDIR = "gh"
_CLAUDE_SUBDIR = "claude"
_CODEX_SUBDIR = "codex"
_KIMI_SUBDIR = "kimi-code"


def _safe_repo_dir(root: Path, repo_id: str) -> Path:
    """把 repo_id 解析成 root 下的专属目录；非法名或越界一律拒绝。"""
    if not repo_id or repo_id in {".", ".."} or not _REPO_ID_RE.match(repo_id):
        raise ValueError(f"invalid repository id for credential handoff: {repo_id!r}")
    resolved_root = root.resolve()
    unresolved_candidate = root / repo_id
    if unresolved_candidate.is_symlink():
        raise ValueError(f"repository credential path is a symlink: {repo_id!r}")
    candidate = unresolved_candidate.resolve()
    if candidate.parent != resolved_root or candidate.name != repo_id:
        raise ValueError(f"repository credential path escapes handoff root: {repo_id!r}")
    return candidate


def is_tmpfs_directory(directory_path: Path) -> bool:
    """确认目录所在挂载文件系统为 Linux tmpfs。"""
    findmnt_path = shutil.which("findmnt")
    if not findmnt_path or not directory_path.is_dir():
        return False
    try:
        mount_result = subprocess.run(
            [findmnt_path, "--target", str(directory_path), "--noheadings", "--output", "FSTYPE"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return mount_result.stdout.strip() == "tmpfs"


def _write_private_file(path: Path, content: str) -> None:
    """以 ``0600`` 写入明文文件（tmpfs 内），目录 ``0700``。"""
    path.parent.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    os.chmod(path.parent, _DIR_MODE)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(content)
    finally:
        os.chmod(path, _FILE_MODE)


def _shred_dir(directory: Path) -> None:
    """尽力擦除目录内容后删除；tmpfs 上 unlink 即可，覆盖写只是纵深防御。"""
    if not directory.exists():
        return
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        current_directory = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (current_directory / name).is_symlink()]
        for name in filenames:
            file_path = Path(dirpath) / name
            try:
                mode = file_path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    continue
                if mode & stat.S_IWUSR:
                    descriptor = os.open(file_path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
                    with os.fdopen(descriptor, "wb") as wipe:
                        wipe.write(b"\x00" * file_path.lstat().st_size)
            except OSError as wipe_error:
                _logger.warning("Failed to overwrite %s before erase: %s", file_path, wipe_error)
    shutil.rmtree(directory, ignore_errors=True)


class TmpfsRunnerCredentialHandoff:
    """实现 ``RunnerCredentialHandoff`` 的 Linux tmpfs 交付器。

    Args:
        runner_auth_root: ``[agent_runner.hosted].runner_auth_root``，应为 tmpfs 挂载点。
        require_tmpfs: 是否在构造与每次明文写入前验证挂载类型；生产 hosted service 必须开启。
    """

    def __init__(self, runner_auth_root: Path, *, require_tmpfs: bool = True) -> None:
        """解析并创建 handoff 根目录（``0700``）。"""
        self._root = Path(runner_auth_root)
        self._root.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
        os.chmod(self._root, _DIR_MODE)
        self._require_tmpfs = require_tmpfs
        self._ensure_tmpfs()

    def _ensure_tmpfs(self) -> None:
        """生产模式下不允许把凭据写入持久文件系统。"""
        if self._require_tmpfs and not is_tmpfs_directory(self._root):
            raise OSError("hosted runner credential root must be mounted as Linux tmpfs")

    def host_directory(self, repo_id: str) -> str:
        """返回该仓库 runner 的凭据目录（供 compose bind mount 的目标宿主路径）。"""
        return str(_safe_repo_dir(self._root, repo_id))

    def materialize(
        self,
        repo_id: str,
        github_credential: RunnerGitHubCredential | None,
        agent_provider: str | None,
        agent_api_key: str | None,
    ) -> list[str]:
        """先把旧目录清空，再仅写入本仓库所需的 GitHub/Agent 原生认证文件。

        Returns:
            实际写入的文件路径列表（不含任何 key 内容）。
        """
        self._ensure_tmpfs()
        repo_dir = _safe_repo_dir(self._root, repo_id)
        if repo_dir.exists():
            _shred_dir(repo_dir)
        repo_dir.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
        os.chmod(repo_dir, _DIR_MODE)

        written: list[str] = []
        if github_credential:
            written.append(self._write_gh_config(repo_dir, github_credential))
        if agent_provider and agent_api_key:
            written.append(self._write_agent_config(repo_dir, agent_provider, agent_api_key))
        return written

    @staticmethod
    def _write_gh_config(repo_dir: Path, github_credential: RunnerGitHubCredential) -> str:
        """写 ``GH_CONFIG_DIR/hosts.yml``（GitHub 走 hosts.yml，容器不设 GH_TOKEN）。"""
        gh_dir = repo_dir / _GH_CONFIG_SUBDIR
        hosts_path = gh_dir / "hosts.yml"
        hosts_content = (
            "github.com:\n"
            f"    user: {json.dumps(github_credential.login, ensure_ascii=False)}\n"
            "    oauth_token: "
            f"{json.dumps(github_credential.token, ensure_ascii=False)}\n"
            "    git_protocol: https\n"
        )
        _write_private_file(hosts_path, hosts_content)
        return str(hosts_path)

    def _write_agent_config(self, repo_dir: Path, provider: str, api_key: str) -> str:
        """按 provider 生成对应 CLI 原生认证文件（Claude/Codex/Kimi）。"""
        if provider == "claude":
            config_dir = repo_dir / _CLAUDE_SUBDIR
            path = config_dir / "settings.json"
            payload = {"env": {"ANTHROPIC_API_KEY": api_key}}
            _write_private_file(path, json.dumps(payload, ensure_ascii=False, indent=2))
            return str(path)
        if provider == "codex":
            config_dir = repo_dir / _CODEX_SUBDIR
            path = config_dir / "auth.json"
            payload = {"OPENAI_API_KEY": api_key}
            _write_private_file(path, json.dumps(payload, ensure_ascii=False, indent=2))
            return str(path)
        if provider == "kimi":
            config_dir = repo_dir / _KIMI_SUBDIR
            path = config_dir / "config.toml"
            content = '[providers.kimi]\napi_key = "%s"\n' % self._toml_escape(api_key)
            _write_private_file(path, content)
            return str(path)
        raise ValueError(f"unsupported agent provider for credential handoff: {provider!r}")

    @staticmethod
    def _toml_escape(value: str) -> str:
        """转义写入 TOML 基础字符串的反斜杠与引号。"""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def erase(self, repo_id: str) -> bool:
        """清除该仓库运行时明文目录；目录原本不存在时也视为清理成功。"""
        self._ensure_tmpfs()
        repo_dir = _safe_repo_dir(self._root, repo_id)
        if not repo_dir.exists():
            return True
        _shred_dir(repo_dir)
        return not repo_dir.exists()


__all__ = ["TmpfsRunnerCredentialHandoff", "is_tmpfs_directory"]
