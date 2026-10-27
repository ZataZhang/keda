"""监控入口的读取复用；不影响 Agent 执行侧的权威读取。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from backend.api.response_cache import TTLResponseCache
from backend.core.shared.interfaces.agent_runner import IGitHubClient, IProcessRunner
from backend.core.shared.models.agent_runner import PullRequestContext

HEALTH_CACHE = TTLResponseCache(ttl_seconds=300)


class MonitoringGitHubReads:
    """仅在一个仓库的一轮扫描内复用同分支 PR 读取结果。"""

    def __init__(self, client: IGitHubClient) -> None:
        """绑定本轮扫描的 GitHub 客户端。"""
        self._client = client
        self._pr_cache = TTLResponseCache(ttl_seconds=float("inf"))

    def __getattr__(self, name: str) -> Any:
        """其余操作直接委托原客户端。"""
        return getattr(self._client, name)

    def get_pull_request_context(
        self, branch: str, *, require_success: bool = False
    ) -> PullRequestContext | None:
        """复用非严格 PR 查询；严格读取始终直达客户端。

        Args:
            branch: PR 的 head 分支。
            require_success: 是否要求权威查询。

        Returns:
            PR 上下文或 None。
        """
        if require_success:
            return self._client.get_pull_request_context(branch, require_success=True)
        return self._pr_cache.get_or_build(
            f"context:{branch}", lambda: self._client.get_pull_request_context(branch)
        )

    def find_open_pr_by_head(self, branch: str, *, require_success: bool = False) -> str | None:
        """复用本轮的 PR 回退查询，严格读取绕过缓存。

        Args:
            branch: PR 的 head 分支。
            require_success: 是否要求权威查询。

        Returns:
            PR 链接或 None。
        """
        if require_success:
            return self._client.find_open_pr_by_head(branch, require_success=True)
        return self._pr_cache.get_or_build(
            f"fallback:{branch}", lambda: self._client.find_open_pr_by_head(branch)
        )


class MonitoringProcessReads:
    """仅缓存监控健康命令，工作树命令每轮实时执行。"""

    def __init__(self, runner: IProcessRunner) -> None:
        """绑定原始命令执行器。"""
        self._runner = runner

    def __getattr__(self, name: str) -> Any:
        """其余能力直接委托原执行器。"""
        return getattr(self._runner, name)

    def run(self, command: Any, *, cwd: Path, **options: Any) -> Any:
        """对健康检查复用五分钟结果，异常与非零退出不缓存。

        Args:
            command: 监控用例请求执行的命令。
            cwd: 仓库路径。
            options: 原始执行选项。

        Returns:
            原始命令结果。
        """
        command_parts = tuple(command)
        if options != {"check": False} or not (
            command_parts == ("gh", "--version")
            or (len(command_parts) == 4 and command_parts[:3] == ("git", "remote", "get-url"))
        ):
            return self._runner.run(command, cwd=cwd, **options)
        # gh 可用性与仓库无关；remote 则按路径和名称隔离。
        probe_cwd = Path.cwd() if command_parts == ("gh", "--version") else cwd
        cache_key = repr((str(probe_cwd.resolve()), command_parts, os.environ.get("PATH")))

        def probe() -> Any:
            command_result = self._runner.run(command, cwd=probe_cwd, **options)
            if command_result.return_code != 0:
                raise RuntimeError(command_result.stderr or "健康检查失败")
            return command_result

        return HEALTH_CACHE.get_or_build(cache_key, probe)
