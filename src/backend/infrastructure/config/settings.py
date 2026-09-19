"""配置文件 - 使用 pydantic-settings 集中管理所有配置。

支持三层配置源（优先级从高到低）：
1. 环境变量 / .env / .env.local
2. config.toml（非敏感配置）
3. 代码中的默认值
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import (
    Field,
    SecretStr,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


from backend.infrastructure.config.agent_runner_settings import (
    AgentRunnerAgentSettings,
    AgentRunnerAutopilotSettings,
    AgentRunnerConsoleSettings,
    AgentRunnerDaemonSettings,
    AgentRunnerDeliberationSettings,
    AgentRunnerGeneratedContentSettings,
    AgentRunnerGeneratedContentTargetSettings,
    AgentRunnerGitSettings,
    AgentRunnerInteractiveDecisionSettings,
    AgentRunnerLabelSettings,
    AgentRunnerLifecycleAgentsSettings,
    AgentRunnerLocalSettings,
    AgentRunnerMemorySettings,
    AgentRunnerPostPrSupervisorSettings,
    AgentRunnerPrePrReviewSettings,
    AgentRunnerPromptSettings,
    AgentRunnerReplSettings,
    AgentRunnerRepositoryMetadataSettings,
    AgentRunnerRepositorySettings,
    AgentRunnerRunnerSettings,
    AgentRunnerSafetySettings,
    AgentRunnerValidationSettings,
    AgentRunnerWorktreeSettings,
    load_agent_runner_local_settings,
)

# 冗余别名（``X as X``）是 PEP 484 的显式再导出手法：这些名字迁到了
# agent_runner_settings / settings_sources，但仍有消费者按
# ``backend.infrastructure.config.settings.X`` 取用，故按原路径再导出。
#
# 迁出后**已无消费者走 settings. 路径**的名字刻意不在此列，例如
# ``_load_toml_section_data`` / ``_load_registry_toml_section_data`` /
# ``resolve_config_toml_path`` / ``_default_runner_command``。保留这类死别名
# 会让 ``patch("...config.settings.X")`` 变成静默 no-op（真正的解析发生在
# settings_sources / agent_runner_settings 的命名空间里），而 AttributeError
# 至少是响亮的。替换它们请直接打实现所在模块。
from backend.infrastructure.config.agent_runner_settings import (
    AgentRunnerAgentProfileSettings as AgentRunnerAgentProfileSettings,
    AgentRunnerDeliberationProfileSettings as AgentRunnerDeliberationProfileSettings,
)

from backend.infrastructure.config.settings_sources import (
    IAR_REPOSITORY_CONFIG_FILENAME,
    _PROJECT_ROOT_PATH,
    _RegistryRepositoriesSource,
    _TomlSectionSource,
    _env_toml_init_sources,
)

from backend.infrastructure.config.settings_sources import (
    resolve_project_root_path as resolve_project_root_path,
    resolve_registry_config_toml_path as resolve_registry_config_toml_path,
)


class DatabaseSettings(BaseSettings):
    """数据库连接配置（非敏感部分）。"""

    model_config = SettingsConfigDict(env_prefix="DB_")

    backend: str = "postgresql"
    host: str = "localhost"
    port: int = 5432
    name: str = "app_database"
    driver: str = "psycopg2"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "database", env_settings, init_settings)


class ChatModelSettings(BaseSettings):
    """默认聊天模型配置。"""

    model_config = SettingsConfigDict(env_prefix="CHAT_MODEL_")

    name: str = "gpt-4"
    provider: str = "openai"
    temperature: float = 0.2

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "chat_model", env_settings, init_settings)


class MinioSettings(BaseSettings):
    """MinIO 对象存储配置（非敏感部分）。"""

    model_config = SettingsConfigDict(env_prefix="MINIO_")

    endpoint: str = "localhost:9000"
    secure: bool = False
    bucket_raw_documents: str = "default-bucket"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "minio", env_settings, init_settings)


class QdrantSettings(BaseSettings):
    """Qdrant 向量数据库配置。"""

    model_config = SettingsConfigDict(env_prefix="QDRANT_")

    host: str = "localhost"
    port: int = 6333
    collection_name: str = "default_collection"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "qdrant", env_settings, init_settings)


class EmbeddingSettings(BaseSettings):
    """Embedding 模型配置。"""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_")

    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dim: int = 384
    offline_mode: bool = True
    model_dir: str = "resources/models"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "embedding", env_settings, init_settings)


class ChunkingSettings(BaseSettings):
    """文档分块配置。"""

    model_config = SettingsConfigDict(env_prefix="CHUNK_")

    size: int = 512
    overlap: int = 50

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "chunking", env_settings, init_settings)


class TimeoutSettings(BaseSettings):
    """超时配置（秒）。"""

    model_config = SettingsConfigDict(env_prefix="TIMEOUT_")

    embedding_model_load_seconds: int = 300
    ingestion_document_seconds: int = 600
    ingestion_job_seconds: int = 7200
    minio_seconds: int = 60

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "timeouts", env_settings, init_settings)


class AgentRunnerSettings(BaseSettings):
    """Agent Runner configuration."""

    model_config = SettingsConfigDict(env_prefix="AGENT_RUNNER_")

    max_issues: int = 1
    default_agent: str = "auto"
    labels: AgentRunnerLabelSettings = Field(default_factory=AgentRunnerLabelSettings)
    git: AgentRunnerGitSettings = Field(default_factory=AgentRunnerGitSettings)
    worktree: AgentRunnerWorktreeSettings = Field(default_factory=AgentRunnerWorktreeSettings)
    runner: AgentRunnerRunnerSettings = Field(default_factory=AgentRunnerRunnerSettings)
    memory: AgentRunnerMemorySettings = Field(default_factory=AgentRunnerMemorySettings)
    safety: AgentRunnerSafetySettings = Field(default_factory=AgentRunnerSafetySettings)
    autopilot: AgentRunnerAutopilotSettings = Field(default_factory=AgentRunnerAutopilotSettings)
    validation: AgentRunnerValidationSettings = Field(default_factory=AgentRunnerValidationSettings)
    console: AgentRunnerConsoleSettings = Field(default_factory=AgentRunnerConsoleSettings)
    daemon: AgentRunnerDaemonSettings = Field(default_factory=AgentRunnerDaemonSettings)
    prompts: AgentRunnerPromptSettings = Field(default_factory=AgentRunnerPromptSettings)
    pre_pr_review: AgentRunnerPrePrReviewSettings = Field(
        default_factory=AgentRunnerPrePrReviewSettings
    )
    post_pr_supervisor: AgentRunnerPostPrSupervisorSettings = Field(
        default_factory=AgentRunnerPostPrSupervisorSettings
    )
    deliberation: AgentRunnerDeliberationSettings = Field(
        default_factory=AgentRunnerDeliberationSettings
    )
    generated_content: AgentRunnerGeneratedContentSettings = Field(
        default_factory=AgentRunnerGeneratedContentSettings
    )
    interactive_decision: AgentRunnerInteractiveDecisionSettings = Field(
        default_factory=AgentRunnerInteractiveDecisionSettings
    )
    repl: AgentRunnerReplSettings = Field(default_factory=AgentRunnerReplSettings)
    # 生命周期 Agent 矩阵（九键）：把"阶段 -> agent"从散落配置段收敛到一张表。
    # 未声明的键继续回落到既有散落配置键与内置默认（零配置行为不变）。
    lifecycle_agents: AgentRunnerLifecycleAgentsSettings = Field(
        default_factory=AgentRunnerLifecycleAgentsSettings
    )
    repositories: dict[str, AgentRunnerRepositorySettings] = Field(default_factory=dict)
    # agent 声明式注册表的配置覆盖层：[agent_runner.agents.<name>] 段。
    # 内置默认在 core.shared.models.agent_spec.BUILTIN_AGENT_SPECS；
    # 此处只存显式声明，逐字段覆盖内置值（见 factory_config_builder）。
    agents: dict[str, AgentRunnerAgentSettings] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_source = _TomlSectionSource(settings_cls, "agent_runner")
        return (
            env_settings,
            _RegistryRepositoriesSource(settings_cls),
            toml_source,
            init_settings,
        )


class PreviewSettings(BaseSettings):
    """Preview deployment configuration (non-sensitive structure only)."""

    model_config = SettingsConfigDict(env_prefix="PREVIEW_")

    enabled: bool = False
    base_domain: str = "preview.example.com"
    project_slug: str = "keda"
    app_dir_root: str = "/opt/preview"
    registry_host: str = "ghcr.io"
    registry_namespace: str = ""
    traefik_network: str = "traefik"
    url_scheme: str = "https"
    subdomain_template: str = "pr-{pr_number}.{base_domain}"
    compose_template: str = "{project_slug}-pr-{pr_number}"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return _env_toml_init_sources(settings_cls, "preview", env_settings, init_settings)


class AppSettings(BaseSettings):
    """应用主配置 - 聚合所有子配置。"""

    model_config = SettingsConfigDict(
        env_file=(_PROJECT_ROOT_PATH / ".env", _PROJECT_ROOT_PATH / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="app", validation_alias="NAME")
    log_level: str = Field(default="INFO")

    postgres_user: str = ""
    postgres_password: SecretStr = SecretStr("")
    database_url: str = ""
    minio_access_key: str = Field(default="minioadmin")
    minio_secret_key: SecretStr = SecretStr("minioadmin")
    minio_root_user: str = ""
    minio_root_password: SecretStr = SecretStr("")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    chat_model: ChatModelSettings = Field(default_factory=ChatModelSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    agent_runner: AgentRunnerSettings = Field(default_factory=AgentRunnerSettings)
    preview: PreviewSettings = Field(default_factory=PreviewSettings)

    base_dir: Path = _PROJECT_ROOT_PATH
    log_dir: Path = Field(default_factory=lambda: _PROJECT_ROOT_PATH / "logs")
    log_file: Path = Field(default_factory=lambda: _PROJECT_ROOT_PATH / "logs" / "app.log")

    @property
    def resolved_database_url(self) -> str:
        """解析最终 DATABASE_URL：env var > TOML + credentials > default。"""
        if self.database_url and self.database_url.strip():
            return self.database_url.strip()

        db_config: DatabaseSettings = self.database
        encoded_user: str = quote_plus(self.postgres_user) if self.postgres_user else ""
        raw_password: str = self.postgres_password.get_secret_value()
        encoded_password: str = quote_plus(raw_password) if raw_password else ""

        credentials_part: str = ""
        if encoded_user or encoded_password:
            credentials_part = f"{encoded_user}:{encoded_password}"

        netloc: str = f"{credentials_part}@{db_config.host}" if credentials_part else db_config.host

        resolved_url: str = (
            f"{db_config.backend}+{db_config.driver}://{netloc}:{db_config.port}/{db_config.name}"
        )
        return resolved_url

    @property
    def resolved_minio_access_key(self) -> str:
        """解析 MinIO access key。"""
        if self.minio_access_key != "minioadmin":
            return self.minio_access_key
        return self.minio_root_user or "minioadmin"

    @property
    def resolved_minio_secret_key(self) -> str:
        """解析 MinIO secret key。"""
        secret_value: str = self.minio_secret_key.get_secret_value()
        if secret_value != "minioadmin":
            return secret_value
        root_password: str = self.minio_root_password.get_secret_value()
        return root_password or "minioadmin"

    def ensure_log_directory(self) -> None:
        """确保日志目录存在。"""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_source: _TomlSectionSource = _TomlSectionSource(settings_cls, "app")
        return (
            env_settings,
            dotenv_settings,
            toml_source,
            init_settings,
        )


def _ensure_no_proxy_for_local_services() -> None:
    """确保本地服务（localhost/127.0.0.1）不经过系统 HTTP 代理。"""
    existing_no_proxy: str = os.getenv("NO_PROXY", "")
    local_hosts: set[str] = {"localhost", "127.0.0.1", "::1"}
    current_entries: set[str] = {
        entry.strip() for entry in existing_no_proxy.split(",") if entry.strip()
    }
    missing_entries: set[str] = local_hosts - current_entries

    if missing_entries:
        updated_no_proxy: str = ",".join(current_entries | local_hosts)
        os.environ["NO_PROXY"] = updated_no_proxy
        os.environ["no_proxy"] = updated_no_proxy


config: AppSettings = AppSettings()
config.ensure_log_directory()
_ensure_no_proxy_for_local_services()

__all__ = [
    "AgentRunnerAutopilotSettings",
    "AgentRunnerLocalSettings",
    "AgentRunnerRepositoryMetadataSettings",
    "AgentRunnerConsoleSettings",
    "AgentRunnerDaemonSettings",
    "AgentRunnerGeneratedContentSettings",
    "AgentRunnerGeneratedContentTargetSettings",
    "AgentRunnerGitSettings",
    "AgentRunnerLabelSettings",
    "AgentRunnerPromptSettings",
    "AgentRunnerReplSettings",
    "AgentRunnerLifecycleAgentsSettings",
    "AgentRunnerRepositorySettings",
    "AgentRunnerRunnerSettings",
    "AgentRunnerSafetySettings",
    "AgentRunnerSettings",
    "AppSettings",
    "ChatModelSettings",
    "ChunkingSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "IAR_REPOSITORY_CONFIG_FILENAME",
    "MinioSettings",
    "PreviewSettings",
    "QdrantSettings",
    "TimeoutSettings",
    "config",
    "load_agent_runner_local_settings",
]
