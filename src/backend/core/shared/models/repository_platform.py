"""仓库代码托管平台的共享词表与未实现状态。"""

from __future__ import annotations

__all__ = [
    "DEFAULT_REPOSITORY_PLATFORM",
    "GITEA_PLATFORM",
    "GITHUB_PLATFORM",
    "RepositoryPlatformConfigurationError",
    "SUPPORTED_REPOSITORY_PLATFORMS",
    "validate_repository_platform",
]

GITHUB_PLATFORM = "github"
"""当前已实现的平台名称。"""

GITEA_PLATFORM = "gitea"
"""Gitea 平台名称。"""

DEFAULT_REPOSITORY_PLATFORM = GITHUB_PLATFORM
"""仓库配置未声明平台时使用的默认值。"""

SUPPORTED_REPOSITORY_PLATFORMS = (GITHUB_PLATFORM, GITEA_PLATFORM)
"""仓库配置允许声明的平台名称。"""


class RepositoryPlatformConfigurationError(ValueError):
    """代码平台客户端无法根据本机与仓库配置安全完成装配。"""


def validate_repository_platform(platform_value: object) -> str:
    """校验仓库平台名称并原样返回有效值。

    Args:
        platform_value: 仓库配置中的原始平台值。

    Returns:
        与输入完全一致的平台名称。

    Raises:
        ValueError: 平台值不是允许的字符串之一。
    """
    if isinstance(platform_value, str) and platform_value in SUPPORTED_REPOSITORY_PLATFORMS:
        return platform_value

    allowed_platforms = ", ".join(repr(value) for value in SUPPORTED_REPOSITORY_PLATFORMS)
    raise ValueError(
        f"Invalid repository.platform value {platform_value!r}; "
        f"expected one of: {allowed_platforms}."
    )
