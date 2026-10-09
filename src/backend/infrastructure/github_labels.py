"""Label syncing for the GitHub CLI client.

Creates or updates each standard label through ``gh label create``. The label
set itself is defined once in
:mod:`backend.core.shared.models.agent_labels` so that the console's label
write validation and ``kc labels sync`` can never drift apart; this module only
holds the ``gh`` side of that contract.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from backend.core.shared.models.agent_labels import standard_label_specs
from backend.core.shared.models.agent_spec import AgentSpec
from backend.infrastructure.github_models import LabelConfig

_logger = logging.getLogger(__name__)


class _ClientProtocol(Protocol):
    """Duck-typed interface expected by :func:`sync_labels`."""

    repo_path: object

    def _run_with_retry(self, command: Sequence[str], *, cwd: object) -> object: ...


def sync_labels(
    client: _ClientProtocol,
    labels: LabelConfig,
    agent_registry: dict[str, AgentSpec] | None = None,
) -> None:
    """Create or update standard labels."""
    for label_spec in standard_label_specs(labels, agent_registry):
        client._run_with_retry(
            [
                "gh",
                "label",
                "create",
                label_spec.effective_name,
                "--color",
                label_spec.color,
                "--description",
                label_spec.description,
                "--force",
            ],
            cwd=client.repo_path,
        )


__all__ = ["sync_labels"]
