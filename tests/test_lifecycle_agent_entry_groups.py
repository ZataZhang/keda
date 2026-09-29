"""生命周期触发入口分组导入期校验（守卫回归测试）。

`_validate_entry_groups` 在导入 ``lifecycle_agent`` 模块时即执行，本文件用
monkeypatch 替换模块级常量后手动调用校验函数，断言两类文档化契约的漂移
（组内键顺序违例、触发时机缺失/为空）都在导入期转红，而不是漏到视图构建期
才以 ``KeyError`` 或空文案的形式暴露。
"""

import pytest

from backend.core.shared.models import lifecycle_agent
from backend.core.shared.models.lifecycle_agent import (
    LIFECYCLE_AGENT_ENTRY_GROUPS,
    LifecycleAgentEntryGroup,
)


def test_entry_group_validation_rejects_in_group_order_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组内键顺序违反 LIFECYCLE_AGENT_KEYS 相对顺序时导入期报错。"""
    original_first_group = LIFECYCLE_AGENT_ENTRY_GROUPS[0]
    reversed_first_group = LifecycleAgentEntryGroup(
        entry=original_first_group.entry,
        label=original_first_group.label,
        summary=original_first_group.summary,
        keys=tuple(reversed(original_first_group.keys)),
    )
    monkeypatch.setattr(
        lifecycle_agent,
        "LIFECYCLE_AGENT_ENTRY_GROUPS",
        (reversed_first_group,) + LIFECYCLE_AGENT_ENTRY_GROUPS[1:],
    )
    with pytest.raises(ValueError, match="组内键顺序"):
        lifecycle_agent._validate_entry_groups()


def test_entry_group_validation_rejects_missing_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """某键缺触发时机文案时导入期报错，而不是等只读视图构建期才 KeyError。"""
    triggers_without_implementation = {
        lifecycle_key: trigger_text
        for lifecycle_key, trigger_text in lifecycle_agent.LIFECYCLE_AGENT_TRIGGERS.items()
        if lifecycle_key != "implementation"
    }
    monkeypatch.setattr(
        lifecycle_agent, "LIFECYCLE_AGENT_TRIGGERS", triggers_without_implementation
    )
    with pytest.raises(ValueError, match="非空触发时机"):
        lifecycle_agent._validate_entry_groups()


def test_entry_group_validation_rejects_empty_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """触发时机文案为空串同样导入期报错（只读视图要求每行非空 trigger）。"""
    triggers_with_empty_text = {
        **lifecycle_agent.LIFECYCLE_AGENT_TRIGGERS,
        "implementation": "",
    }
    monkeypatch.setattr(lifecycle_agent, "LIFECYCLE_AGENT_TRIGGERS", triggers_with_empty_text)
    with pytest.raises(ValueError, match="非空触发时机"):
        lifecycle_agent._validate_entry_groups()
