"""PRD 验收清单"作用域"用例表（测试辅助数据，不含测试用例，不会被 pytest 收集）。

``Human-Confirmed`` 分组作用范围的判定（嵌套、同级关闭、前缀匹配、围栏代码块……）
是本仓库里一条不能走样的语义：归档只代表执行侧交付完成，人属分组内的空框不拦提交，
其余空框照旧拦截。读这条语义的实现有不止一份——随模板同步的 hook，以及 prd skill
内的契约解析——它们不能互相 import，只能各带一份。用例表因此只在这里写一份，由两处共用：

- ``test_check_prd_acceptance_checklist.py`` 钉死 hook 的读法（随 sync 分发到下游）；
- 模板仓库内另有一份守卫测试，用同一张表钉死 skill 的契约解析，并校验两份实现逐例等价。

改动用例即改动约定：必须同步更新 ``docs/ai-standards/tooling.md`` 的 PRD Workflow
Hooks 小节与各实现，并保持各处读法一致。
"""

from __future__ import annotations

import pytest


def prd_with_checklist_body(checklist_body: str) -> str:
    """把一段清单正文放进 §9，并在其后放一个必须被忽略的下一章空框。

    Args:
        checklist_body: 放进 ``## 9. Acceptance Checklist`` 的正文。
    """

    return (
        "# PRD: x\n\n"
        "## 9. Acceptance Checklist\n\n"
        f"{checklist_body}\n"
        "## 10. Functional Requirements\n\n"
        "- [ ] after-section\n"
    )


def open_item_labels(unchecked_items: list[tuple[int, str]]) -> list[str]:
    """取空框原文里 ``] `` 之后的标签，便于直接断言"报了哪些条目"。

    Args:
        unchecked_items: ``(行号, 原文)`` 列表。
    """

    return [item_text.split("] ", 1)[1].strip() for _, item_text in unchecked_items]


# 每条用例是 §9 正文 + 期望被报告的空框标签（id 即用例名）。标签里的 exec-* 是执行侧欠的
# 活，human-* 位于 Human-Confirmed 作用范围内，done-* 已勾，fenced-* 在围栏代码块内。
CHECKLIST_SCOPE_PARAMS = [
    pytest.param(
        "- [ ] exec-1\n- [x] done-1\n",
        ["exec-1"],
        id="no-groups-so-every-open-box-is-execution-owed",
    ),
    pytest.param(
        "### Execution\n\n- [ ] exec-1\n\n### Human-Confirmed\n\n- [ ] human-1\n",
        ["exec-1"],
        id="human-heading-exempts-only-its-own-group",
    ),
    pytest.param(
        "### Human-Confirmed\n\n- [ ] human-1\n\n### Execution\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="sibling-heading-closes-the-human-group",
    ),
    pytest.param(
        "### Human-Confirmed\n\n#### Sub A\n\n- [ ] human-1\n\n#### Sub B\n\n- [ ] human-2\n\n"
        "### Execution\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="nested-subgroups-stay-human",
    ),
    pytest.param(
        "#### Human-Confirmed\n\n- [ ] human-1\n\n### Execution\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="shallower-heading-closes-a-deeper-human-group",
    ),
    pytest.param(
        "### Execution\n\n#### Human-Confirmed\n\n- [ ] human-1\n\n#### Probes\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="human-subgroup-closes-at-its-next-sibling",
    ),
    pytest.param(
        "**Human-Confirmed**\n\n- [ ] human-1\n\n**Execution**\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="whole-line-bold-labels-are-accepted-as-groups",
    ),
    pytest.param(
        "#### Human-Confirmed\n\n- [ ] human-1\n\n**Execution**\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="bold-label-counts-as-level-three-and-closes-a-deeper-human-group",
    ),
    pytest.param(
        "### Human-Confirmed\n\n**说明：** 行内强调不是分组\n\n- [ ] human-1\n",
        [],
        id="inline-emphasis-is-not-a-group-label",
    ),
    pytest.param(
        "### Human-Confirmed (来自 Part A 风险地图)\n\n- [ ] human-1\n\n"
        "### HUMAN-CONFIRMED\n\n- [ ] human-2\n",
        [],
        id="prefix-match-is-case-blind-and-allows-a-suffix",
    ),
    pytest.param(
        "### Not Human-Confirmed\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="the-label-must-start-with-human-confirmed",
    ),
    pytest.param(
        "```\n- [ ] fenced-1\n```\n\n~~~\n- [ ] fenced-2\n~~~\n\n- [ ] exec-1\n",
        ["exec-1"],
        id="fenced-code-is-ignored",
    ),
    pytest.param(
        "### Human-Confirmed\n\n```\n### Execution\n```\n\n- [ ] human-1\n",
        [],
        id="a-heading-inside-a-code-fence-does-not-close-the-group",
    ),
    pytest.param(
        "- [~] gated-1 — runner-owned gate: probe\n- [ ] exec-1\n",
        ["exec-1"],
        id="runner-owned-gate-is-not-an-open-box",
    ),
    pytest.param(
        "* [ ] exec-1\n+ [ ] exec-2\n  - [ ] exec-3\n- [X] done-1\n",
        ["exec-1", "exec-2", "exec-3"],
        id="bullet-markers-and-indentation",
    ),
]
