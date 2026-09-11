"""Sync standard GitHub labels for agent-runner workflow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.core.shared.interfaces.agent_runner import IGitHubClient
from backend.core.shared.models.agent_runner import LabelConfig

if TYPE_CHECKING:
    from backend.core.shared.models.agent_spec import AgentSpec

_logger = logging.getLogger(__name__)


def sync_labels(
    *,
    labels_config: LabelConfig,
    github_client: IGitHubClient,
    agent_registry: dict[str, AgentSpec] | None = None,
) -> None:
    """Create or update standard labels in the target repository.

    Args:
        labels_config: Label names to use.
        github_client: Client for interacting with GitHub.
        agent_registry: agent 注册表（agent 名 -> spec），为 agent 路由
            标签提供颜色与描述；``None`` 时实现回落内置注册表。
    """
    github_client.sync_labels(labels_config, agent_registry)
    _logger.info("Labels synchronized.")
