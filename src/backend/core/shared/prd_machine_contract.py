"""prd skill Machine Contract 的版本契约与 prompt 指针（单一出处）。

PRD 格式约定（Change Log 条目结构、验收复选框语法、rv-id 证据命名、证据目录
布局）的权威文本由 prd skill 的 ``## Machine Contract`` 章节承载；iar 的各
prompt 只引用 :data:`PRD_MACHINE_CONTRACT_POINTER` 这一行指针，不抄录教学
内容。daemon 起执行循环前用本模块的版本解析做预检：skill 缺失、无版本标记，
或主版本不在 :data:`SUPPORTED_MACHINE_CONTRACT_VERSIONS` 内即 fail fast。

本模块只允许依赖标准库（架构验收：模块内不得出现任何 backend 包导入）。
"""

from __future__ import annotations

import re

SUPPORTED_MACHINE_CONTRACT_VERSIONS: tuple[int, ...] = (3, 4, 5)
"""iar 能读懂的 prd skill Machine Contract 主版本号（升序）。

契约自身规定「改这一节的内容必须 bump 版本」，而两侧发版无法原子完成：skill
先 bump、keda 后跟进的那段时间里，硬相等 pin 会让**所有** iar 在启动预检直接
fail fast。因此这里表达「能读懂的版本集合」，配套的发版纪律是：

1. 先把新版本加进集合（keda 侧先发）；
2. skill 侧再 bump 契约版本；
3. 确认没有在用之后，才从集合里移除旧版本。
"""

MACHINE_CONTRACT_VERSION_PATTERN = re.compile(r"Machine-Contract-Version:\s*(\d+)")
"""SKILL.md 中机器可读版本标记的解析正则（独立一行 ``Machine-Contract-Version: <n>``）。"""

PRD_MACHINE_CONTRACT_POINTER = (
    "PRD format conventions (Change Log entry structure, Acceptance Checklist "
    "syntax, rv-id evidence naming, evidence directory layout) are defined by "
    "the prd skill's Machine Contract — read and follow the prd skill."
)
"""注入各 prompt 的契约指针行；格式教学的唯一去处是 prd skill，此处只做指针。

刻意**不写死版本号**：这里没有 skill 的读取上下文，写死只会在过渡期指向一个
当前未安装的版本。运行期究竟装的哪一版由启动预检负责判定。
"""


class PrdSkillPreflightError(RuntimeError):
    """prd skill 缺失或 Machine Contract 主版本不受支持时由启动预检抛出。"""


def is_supported_machine_contract_version(version: int | None) -> bool:
    """判断解析出的契约主版本是否受支持。

    Args:
        version: :func:`parse_machine_contract_version` 的结果；``None`` 表示
            skill 里没有版本标记。

    Returns:
        版本非 ``None`` 且落在 :data:`SUPPORTED_MACHINE_CONTRACT_VERSIONS` 内时为 True。
    """
    return version is not None and version in SUPPORTED_MACHINE_CONTRACT_VERSIONS


def format_supported_machine_contract_versions() -> str:
    """把受支持的主版本渲染成 ``v3 / v4`` 形式，供报错文案使用。"""
    return " / ".join(f"v{version}" for version in SUPPORTED_MACHINE_CONTRACT_VERSIONS)


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
