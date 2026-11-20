"""仓库代码托管平台的共享词表与未实现状态。"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DEFAULT_REPOSITORY_PLATFORM",
    "GITEA_PLATFORM",
    "GITHUB_PLATFORM",
    "SUPPORTED_REPOSITORY_PLATFORMS",
    "RepositoryPlatformNotImplementedError",
    "validate_repository_platform",
]

GITHUB_PLATFORM = "github"
"""当前已实现的平台名称。"""

GITEA_PLATFORM = "gitea"
"""已登记但尚未实现的平台名称。"""

DEFAULT_REPOSITORY_PLATFORM = GITHUB_PLATFORM
"""仓库配置未声明平台时使用的默认值。"""

SUPPORTED_REPOSITORY_PLATFORMS = (GITHUB_PLATFORM, GITEA_PLATFORM)
"""仓库配置允许声明的平台名称。"""


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


class RepositoryPlatformNotImplementedError(RuntimeError):
    """仓库声明的平台尚无客户端实现。"""

    def __init__(self, *, platform_name: str, config_path: Path) -> None:
        """初始化带有平台与配置文件定位信息的错误。

        Args:
            platform_name: 仓库声明的平台名称。
            config_path: 声明平台的仓库配置文件路径。
        """
        self.platform_name = platform_name
        self.config_path = config_path
        super().__init__(
            f"The repository platform {platform_name!r} is not implemented. "
            f"Only {GITHUB_PLATFORM!r} is currently supported. "
            f"To switch back to GitHub, edit {config_path} and set "
            f"platform = {GITHUB_PLATFORM!r}."
        )
