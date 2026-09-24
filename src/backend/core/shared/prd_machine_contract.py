"""prd skill Machine Contract 的版本契约与 prompt 指针（单一出处）。

PRD 格式约定（Change Log 条目结构、验收复选框语法、rv-id 证据命名、证据目录
布局）的权威文本由 prd skill 的 ``## Machine Contract (v3)`` 章节承载；iar 的
各 prompt 只引用 :data:`PRD_MACHINE_CONTRACT_POINTER` 这一行指针，不抄录教学
内容。daemon 起执行循环前用本模块的版本解析做预检：skill 缺失或契约主版本与
:data:`SUPPORTED_MACHINE_CONTRACT_VERSION` 不一致即 fail fast。

本模块只允许依赖标准库（架构验收：模块内不得出现任何 backend 包导入）。
"""

from __future__ import annotations

import re

SUPPORTED_MACHINE_CONTRACT_VERSION = 3
"""iar 当前支持的 prd skill Machine Contract 主版本号。"""

MACHINE_CONTRACT_VERSION_PATTERN = re.compile(r"Machine-Contract-Version:\s*(\d+)")
"""SKILL.md 中机器可读版本标记的解析正则（独立一行 ``Machine-Contract-Version: <n>``）。"""

PRD_MACHINE_CONTRACT_POINTER = (
    "PRD format conventions (Change Log entry structure, Acceptance Checklist "
    "syntax, rv-id evidence naming, evidence directory layout) are defined by "
    f"the prd skill's Machine Contract v{SUPPORTED_MACHINE_CONTRACT_VERSION} — "
    "read and follow the prd skill."
)
"""注入各 prompt 的契约指针行；格式教学的唯一去处是 prd skill，此处只做指针。"""


class PrdSkillPreflightError(RuntimeError):
    """prd skill 缺失或 Machine Contract 主版本不匹配时由启动预检抛出。"""


def parse_machine_contract_version(skill_text: str) -> int | None:
    """从 prd skill 文本中解析 Machine Contract 版本号。

    Args:
        skill_text: ``SKILL.md`` 全文。

    Returns:
        版本号整数；未找到版本标记时返回 ``None``。
    """
    version_match = MACHINE_CONTRACT_VERSION_PATTERN.search(skill_text)
    if version_match is None:
        return None
    return int(version_match.group(1))
