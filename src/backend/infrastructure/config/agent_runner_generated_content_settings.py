"""生成内容目标的 Agent Runner 设置模型。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AgentRunnerGeneratedContentTargetSettings(BaseModel):
    """生成内容目标配置，支持 TOML 字符串列表语法。

    用于 ``issue_from_prd``、``issue_from_prompt``、``draft_pr``、
    ``prd_from_issue`` 四类生成目标，
    分别控制 Issue、无 PRD Issue、PR、PRD 的标题/正文生成方式。
    """

    enabled: bool = True
    # 仅接受 template / agent；非法值（如手误 "agnet"）在配置加载期直接报错，
    # 而不是静默退回 fallback。默认 agent；agent 缺 prompt、超时、不可用或输出
    # 不合格时按 ``fallback`` 退回 template 渲染，所以未配置的机器同样能产出内容。
    # 显式配成 template 已废弃（仍被接受，加载仓库本地配置时会告警）：模板渲染的
    # 长期用途是上面这条失败兜底。
    mode: Literal["template", "agent"] = "agent"
    output: str = "json"
    title_template: str | list[str] = ""
    body_template: str | list[str] = ""
    agent: str = "auto"
    timeout_seconds: int = 120
    prompt: str | list[str] = ""
    include_commit_log: bool = True
    include_diff_stat: bool = True

    @model_validator(mode="after")
    def _join_list_templates(self) -> "AgentRunnerGeneratedContentTargetSettings":
        """将 list[str] 类型的模板字段合并为单个字符串。

        TOML 中多行模板通常以字符串列表书写，便于版本控制审阅；
        加载后需要拼接成完整模板字符串供 ``str.format()`` 渲染。
        """
        for field_name in ("title_template", "body_template", "prompt"):
            value = getattr(self, field_name)
            if isinstance(value, list):
                setattr(self, field_name, "\n".join(value))
        return self


class AgentRunnerGeneratedContentSettings(BaseModel):
    """GitHub Issue 与 PR 的生成内容全局配置。

    聚合各类生成目标（PRD→Issue、一句话→Issue、PR、PRD）的共享参数，如是否启用、
    失败回退策略、最大输入长度以及默认 agent。
    """

    enabled: bool = True
    fallback: str = "template"
    max_input_chars: int = 20000
    default_agent: str = "auto"
    # 各 target 默认都是 agent 模式；``output`` 必须与各自提示词的回复格式一致：
    # issue_from_prd 的提示词回 JSON（沿用类默认），draft_pr / prd_from_issue 回 Markdown。
    # 与 core 的 ``GeneratedContentConfig`` 默认值保持一致（有测试守卫）。
    issue_from_prd: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=AgentRunnerGeneratedContentTargetSettings
    )
    # 一句话需求 → Issue 正文（``kc issue create --from-prompt``）。产物不含 PRD 锚点，
    # 因此提示词与回退模板都不能写出 ``- PRD path:``。
    issue_from_prompt: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=AgentRunnerGeneratedContentTargetSettings
    )
    draft_pr: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=lambda: AgentRunnerGeneratedContentTargetSettings(
            enabled=False, output="markdown"
        )
    )
    prd_from_issue: AgentRunnerGeneratedContentTargetSettings = Field(
        default_factory=lambda: AgentRunnerGeneratedContentTargetSettings(output="markdown")
    )


# ``generated_content`` 下的生成 target 名（TOML 子表名，同时是 settings / core 配置的
# 字段名），直接取自模型字段，不另写一份清单。
GENERATED_CONTENT_TARGET_NAMES: tuple[str, ...] = tuple(
    field_name
    for field_name, field_info in AgentRunnerGeneratedContentSettings.model_fields.items()
    if field_info.annotation is AgentRunnerGeneratedContentTargetSettings
)


__all__ = (
    "AgentRunnerGeneratedContentTargetSettings",
    "AgentRunnerGeneratedContentSettings",
    "GENERATED_CONTENT_TARGET_NAMES",
)
