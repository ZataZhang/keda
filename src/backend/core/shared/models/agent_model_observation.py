"""工具默认模型的调用时配置快照，不等同于执行器自报的实际模型。"""

from dataclasses import dataclass


AGENT_MODEL_CONFIG_SNAPSHOT_ATTR = "agent_model_config_snapshot"


def is_concrete_configured_model(model_id: str) -> bool:
    """判断配置模型是否足够具体，可作为明确标注来源的估价依据。

    Args:
        model_id: 工具配置中的模型标识。

    Returns:
        含版本数字且不是已知动态别名时为 True；未知无版本名称保守留待确认。
    """
    normalized_model = model_id.lower().split("[", 1)[0]
    return (
        any(character.isdigit() for character in normalized_model)
        and normalized_model not in {"auto", "default", "sonnet", "opus", "haiku", "opusplan"}
        and "latest" not in normalized_model
        and not normalized_model.startswith("qmodel_")
    )


@dataclass(frozen=True)
class AgentModelConfigSnapshot:
    """启动前读取的模型配置摘要，只保留模型名与非敏感来源。

    Attributes:
        model_id: 配置中的模型标识或别名。
        source: 来源名称，不包含配置文件原文、密钥或完整命令。
        is_alias: 别名或未确认的名称，为 True 时不用于模型费用归属。
    """

    model_id: str
    source: str
    is_alias: bool
