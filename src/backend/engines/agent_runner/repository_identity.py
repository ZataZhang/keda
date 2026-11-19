"""仓库身份标识的格式化辅助函数。"""

import re


def normalize_repository_id(raw_repo_name: str) -> str:
    """把仓库名规范化为可用的仓库标识。

    Args:
        raw_repo_name: 原始仓库名（通常是目录名）。

    Returns:
        只含字母、数字与 ``_.-`` 的小写标识；规范化后为空时返回 ``repository``。
    """
    normalized_repo_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_repo_name)
    stripped_repo_name = normalized_repo_name.strip("-_.").lower()
    return stripped_repo_name or "repository"
