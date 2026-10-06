"""Issue 创建时的标签装配，PRD 路径与 ``--from-prompt`` 路径共用。

就绪标记与 agent 路由标签的判定必须对两种输入方式完全一致（否则「同一条需求
换个入口建」会得到不同的可领取性），所以从
:mod:`backend.core.use_cases.create_issue_from_prd` 的 ``build_issue_labels``
里抽出来放在这里，而不是在第二条路径上重写一遍。
"""

from __future__ import annotations

from backend.core.shared.models.agent_runner import LabelConfig


def apply_routing_labels(
    labels: list[str],
    *,
    queue_ready: bool,
    ready_deferred_until_publish: bool,
    issue_agent: str,
    labels_config: LabelConfig,
) -> None:
    """就地向 ``labels`` 追加就绪标记与 agent 路由标签。

    ``queue_ready`` 为真且本次不发布 PRD 时才立即打 ``agent/ready``：发布路径会在
    push 成功*之后*补标记，避免在未发布的 PRD 上就开始工作。

    Args:
        labels: 待追加的标签列表（原地修改）。
        queue_ready: 是否请求把 Issue 排进 runner 队列。
        ready_deferred_until_publish: 就绪标记是否推迟到发布成功后再打。
        issue_agent: agent 路由键（``LabelConfig.agent_labels`` 的键，或
            ``"auto"`` / ``"none"``）。

    Raises:
        ValueError: 当 ``issue_agent`` 不是可识别的值时。
    """
    if queue_ready and not ready_deferred_until_publish:
        labels.append(labels_config.ready)
    if issue_agent in labels_config.agent_labels:
        labels.append(labels_config.agent_labels[issue_agent])
    elif issue_agent not in {"auto", "none"}:
        allowed = ", ".join([*labels_config.agent_labels.keys(), "auto", "none"])
        raise ValueError(f"issue_agent must be one of: {allowed}")
