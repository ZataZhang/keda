"""Preview deployment environment derivation logic.

Pure functions used by CI scripts to derive preview stack names,
domains and image references from non-sensitive project configuration.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PreviewSettings(Protocol):
    """Structural type for preview configuration consumed by this module."""

    enabled: bool
    base_domain: str
    project_slug: str
    app_dir_root: str
    registry_host: str
    registry_namespace: str
    traefik_network: str
    url_scheme: str
    subdomain_template: str
    compose_template: str


def resolve_registry_namespace(
    configured_namespace: str,
    repository_owner: str,
) -> str:
    """Resolve the container registry namespace used for preview images.

    优先使用配置里显式声明的 namespace；留空时回退到仓库 owner，这样仓库改名
    或 fork 之后不需要再手工同步一次用户名。两条路径都统一转小写：OCI 镜像名
    的路径段只允许小写，`ghcr.io/ZataZhang/...` 会被 registry 拒绝。

    Args:
        configured_namespace: `config.toml [preview] registry_namespace` 的值，
            留空表示按仓库 owner 推导。
        repository_owner: 仓库 owner，CI 中来自 `GITHUB_REPOSITORY_OWNER`。

    Returns:
        可直接拼进镜像名的小写 namespace。

    Raises:
        ValueError: 配置留空且未能拿到仓库 owner，此时无法推导出有效 namespace。
    """
    explicit_namespace = configured_namespace.strip()
    if explicit_namespace:
        return explicit_namespace.lower()

    derived_namespace = repository_owner.strip()
    if derived_namespace:
        return derived_namespace.lower()

    raise ValueError(
        "无法确定 preview 镜像的 registry namespace："
        "config.toml [preview] registry_namespace 为空，"
        "且环境变量 GITHUB_REPOSITORY_OWNER / GITHUB_REPOSITORY 均未提供仓库 owner。"
    )


def render_preview_env(
    preview: PreviewSettings,
    pr_number: int,
    commit_sha: str,
    repository_owner: str = "",
) -> dict[str, str]:
    """Derive non-sensitive preview environment values from settings.

    Args:
        preview: Project preview configuration.
        pr_number: Pull request number.
        commit_sha: Head commit SHA (shortened for image tags).
        repository_owner: 仓库 owner，仅在 `preview.registry_namespace` 留空时
            用于推导 namespace。

    Returns:
        Dictionary of environment key/value pairs consumed by the preview
        Docker Compose stack and the GitHub Actions workflow.

    Raises:
        ValueError: namespace 既未配置也无法从 owner 推导。
    """
    short_sha = _shorten_sha(commit_sha)
    registry_namespace = resolve_registry_namespace(
        configured_namespace=preview.registry_namespace,
        repository_owner=repository_owner,
    )
    subdomain = preview.subdomain_template.format(
        pr_number=pr_number,
        base_domain=preview.base_domain,
    )
    compose_project_name = preview.compose_template.format(
        project_slug=preview.project_slug,
        pr_number=pr_number,
    )
    preview_domain = f"{subdomain}"
    app_dir = f"{preview.app_dir_root}/{compose_project_name}"
    backend_image = (
        f"{preview.registry_host}/{registry_namespace}/"
        f"{preview.project_slug}-backend:{short_sha}"
    )
    frontend_image = (
        f"{preview.registry_host}/{registry_namespace}/"
        f"{preview.project_slug}-frontend:{short_sha}"
    )

    return {
        "PREVIEW_DOMAIN": preview_domain,
        "COMPOSE_PROJECT_NAME": compose_project_name,
        "APP_DIR": app_dir,
        "BACKEND_IMAGE": backend_image,
        "FRONTEND_IMAGE": frontend_image,
        "REGISTRY_HOST": preview.registry_host,
        "REGISTRY_NAMESPACE": registry_namespace,
        "TRAEFIK_NETWORK": preview.traefik_network,
        "TRAEFIK_ROUTER_NAME": compose_project_name,
        "TRAEFIK_SERVICE_NAME": compose_project_name,
        "PREVIEW_URL_SCHEME": preview.url_scheme,
    }


def _shorten_sha(commit_sha: str) -> str:
    """Return a stable short SHA string.

    Args:
        commit_sha: Full or short commit SHA.

    Returns:
        First 8 characters of the provided SHA, or the original value if
        shorter than 8 characters.
    """
    return commit_sha[:8] if len(commit_sha) > 8 else commit_sha
