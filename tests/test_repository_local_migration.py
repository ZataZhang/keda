"""Tests for migrating scaffold-pinned generated_content out of ``.iar.toml``."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig, GeneratedContentConfig
from backend.engines.agent_runner import repository_local_migration as migration_module
from backend.engines.agent_runner.factory import merge_repository_config
from backend.engines.agent_runner.repository_local import IARRepositoryNotInitializedError
from backend.engines.agent_runner.repository_local_migration import (
    ConfigMigrationError,
    ConfigMigrationResult,
    migrate_repository_local_config,
)
from backend.infrastructure.config.agent_runner_settings import (
    GENERATED_CONTENT_TARGET_NAMES,
    AgentRunnerGeneratedContentSettings,
    AgentRunnerGeneratedContentTargetSettings,
    load_agent_runner_local_settings,
)

CONFIG_BEFORE_GENERATED_CONTENT = """\
[agent_runner.repository]
id = "target-local"

# 校验闸门
[agent_runner.validation]
# 与 generated_content 同名的键不能被误删
enabled = false
"""

# 与旧脚手架里出现过的同名键：`enabled` / `default_agent` 也存在于 generated_content 之外。
CONFIG_AFTER_GENERATED_CONTENT = """\
# 交互式决策（iar ask）配置
[agent_runner.interactive_decision]
# 是否启用 iar ask 交互式决策
enabled = true
# iar ask 默认使用的 agent
default_agent = "auto"
"""

LEGACY_SECTION_TABLE = """\
# GitHub Issue / PR 内容生成（面向人类阅读，不影响实现 Agent）
[agent_runner.generated_content]
# 是否启用 AI 生成 Issue / PR 正文
enabled = true
# 生成失败时的回退方式（当前仅支持 template）
fallback = "template"
# 生成 prompt 的最大字符数
max_input_chars = 20000
# 执行生成的默认 agent：auto / claude / codex / kimi
default_agent = "auto"
"""

LEGACY_TARGET_TABLE = """\
# {description}
[agent_runner.generated_content.{target}]
# 是否生成
enabled = true
# 生成模式：template（模板渲染）或 agent（调用 AI）
mode = "{mode}"
# 输出格式：json / markdown
output = "json"
# 标题模板
title_template = ""
# 正文模板，支持字符串或字符串列表
body_template = ""
# 执行生成的 agent
agent = "auto"
# 生成超时秒数
timeout_seconds = 120
# agent 模式使用的 prompt
prompt = ""
# PR 生成时是否包含 commit log
include_commit_log = true
# PR 生成时是否包含 diff stat
include_diff_stat = true
"""

# 旧 `iar init` 写入的完整 generated_content 区块：各 target 的 mode 取当时的默认值。
LEGACY_GENERATED_CONTENT = "\n".join(
    [
        LEGACY_SECTION_TABLE,
        LEGACY_TARGET_TABLE.format(
            description="从 PRD 生成 GitHub Issue 的模板",
            target="issue_from_prd",
            mode="template",
        ),
        LEGACY_TARGET_TABLE.format(
            description="从 commit 信息生成 Draft PR 的模板", target="draft_pr", mode="template"
        ),
        LEGACY_TARGET_TABLE.format(
            description="从 GitHub Issue 生成 / 重写 PRD", target="prd_from_issue", mode="agent"
        ),
    ]
)


# 最早的脚手架（2026-06-12）在 section 与各 target 上写的都是 `enabled = false`。
OLDEST_SCAFFOLD_DISABLED_GENERATED_CONTENT = """\
[agent_runner.generated_content]
enabled = false
fallback = "template"

[agent_runner.generated_content.issue_from_prd]
enabled = false
mode = "template"
output = "json"

[agent_runner.generated_content.draft_pr]
enabled = false
mode = "template"
output = "json"
"""


def _write_config(repo_root_path: Path, *config_parts: str) -> Path:
    """把若干段配置文本按空行拼接写成 ``.iar.toml``。"""
    config_path = repo_root_path / ".iar.toml"
    config_path.write_text("\n".join(config_parts), encoding="utf-8")
    return config_path


def _parsed_config(config_text: str) -> dict[str, object]:
    return tomllib.loads(config_text)


def _effective_generated_content(repo_root_path: Path) -> GeneratedContentConfig:
    """按 daemon 的合并规则，算出代码默认 + 该仓库 ``.iar.toml`` 后的 generated_content。

    全局层用 ``AppConfig()`` 的代码默认值，不读开发机的 ``config.toml``，结果才稳定。
    """
    repository_settings = load_agent_runner_local_settings(repo_root_path)
    assert repository_settings is not None
    return merge_repository_config(AppConfig(), repository_settings).generated_content


def _mode_and_output_by_target(
    generated_content: GeneratedContentConfig,
) -> dict[str, tuple[str, str]]:
    return {
        target_name: (
            getattr(generated_content, target_name).mode,
            getattr(generated_content, target_name).output,
        )
        for target_name in GENERATED_CONTENT_TARGET_NAMES
    }


def _kept_pin_keys(migration_result: ConfigMigrationResult) -> list[tuple[str, str]]:
    return [(pin.table_name, pin.key_name) for pin in migration_result.kept_pins]


def test_migration_removes_the_whole_legacy_scaffold_block(tmp_path: Path) -> None:
    """全部是旧脚手架钉子时：区块连同表头与注释整段消失，其余内容逐字节不变。"""
    config_path = _write_config(
        tmp_path,
        CONFIG_BEFORE_GENERATED_CONTENT,
        LEGACY_GENERATED_CONTENT,
        CONFIG_AFTER_GENERATED_CONTENT,
    )
    original_config = _parsed_config(config_path.read_text(encoding="utf-8"))

    migration_result = migrate_repository_local_config(tmp_path)

    assert migration_result.wrote_file
    assert len(migration_result.removed_pins) == 34
    assert migration_result.kept_pins == ()
    migrated_text = config_path.read_text(encoding="utf-8")
    assert migrated_text == "\n".join(
        [CONFIG_BEFORE_GENERATED_CONTENT, CONFIG_AFTER_GENERATED_CONTENT]
    )
    expected_config = dict(original_config)
    expected_config["agent_runner"] = {
        table_name: table_values
        for table_name, table_values in original_config["agent_runner"].items()
        if table_name != "generated_content"
    }
    assert _parsed_config(migrated_text) == expected_config


def test_migration_keeps_values_the_repository_customized(tmp_path: Path) -> None:
    """值与旧脚手架不同 = 仓库主动改过：原样保留（含各自的注释），也不出现在报告里。"""
    config_text = """\
[agent_runner.repository]
id = "target-local"

# GitHub Issue / PR 内容生成
[agent_runner.generated_content]
# 是否启用 AI 生成 Issue / PR 正文
enabled = true
# 生成 prompt 的最大字符数
max_input_chars = 40000
# 执行生成的默认 agent
default_agent = "claude"

# 从 commit 信息生成 Draft PR 的模板
[agent_runner.generated_content.draft_pr]
# 是否生成
enabled = true
# 输出格式：json / markdown
output = "markdown"
# 执行生成的 agent
agent = "codex"
# 生成超时秒数
timeout_seconds = 300
# PR 生成时是否包含 commit log
include_commit_log = false
# PR 生成时是否包含 diff stat
include_diff_stat = true
"""
    config_path = _write_config(tmp_path, config_text)

    migration_result = migrate_repository_local_config(tmp_path)

    assert [pin.key_name for pin in migration_result.removed_pins] == [
        "enabled",
        "enabled",
        "include_diff_stat",
    ]
    assert migration_result.kept_pins == ()
    expected_config_text = """\
[agent_runner.repository]
id = "target-local"

# GitHub Issue / PR 内容生成
[agent_runner.generated_content]
# 生成 prompt 的最大字符数
max_input_chars = 40000
# 执行生成的默认 agent
default_agent = "claude"

# 从 commit 信息生成 Draft PR 的模板
[agent_runner.generated_content.draft_pr]
# 输出格式：json / markdown
output = "markdown"
# 执行生成的 agent
agent = "codex"
# 生成超时秒数
timeout_seconds = 300
# PR 生成时是否包含 commit log
include_commit_log = false
"""
    assert config_path.read_text(encoding="utf-8") == expected_config_text


def test_migration_keeps_template_mode_when_repository_has_custom_templates(
    tmp_path: Path,
) -> None:
    """自定义了标题 / 正文模板的 template 钉子是有意为之：保留并说明原因，行为不变。"""
    _write_config(
        tmp_path,
        """\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content.draft_pr]
mode = "template"
title_template = "custom: {title}"
output = "json"
""",
    )

    migration_result = migrate_repository_local_config(tmp_path)

    assert _kept_pin_keys(migration_result) == [("agent_runner.generated_content.draft_pr", "mode")]
    assert "custom title_template" in migration_result.kept_pins[0].reason
    migrated_text = (tmp_path / ".iar.toml").read_text(encoding="utf-8")
    assert 'mode = "template"' in migrated_text
    assert "output" not in migrated_text
    effective_draft_pr = _effective_generated_content(tmp_path).draft_pr
    assert effective_draft_pr.mode == "template"
    assert effective_draft_pr.title_template == "custom: {title}"


def test_migration_keeps_output_pin_only_when_it_would_change_behavior(tmp_path: Path) -> None:
    """自定义 prompt 决定回复格式：会改变默认输出的 output 钉子要留，等于默认的可以清。"""
    _write_config(
        tmp_path,
        """\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content.draft_pr]
output = "json"
prompt = "reply with a JSON object"

[agent_runner.generated_content.issue_from_prd]
output = "json"
prompt = "reply with a JSON object"
""",
    )

    migration_result = migrate_repository_local_config(tmp_path)

    assert _kept_pin_keys(migration_result) == [
        ("agent_runner.generated_content.draft_pr", "output")
    ]
    assert "custom prompt" in migration_result.kept_pins[0].reason
    assert [(pin.table_name, pin.key_name) for pin in migration_result.removed_pins] == [
        ("agent_runner.generated_content.issue_from_prd", "output")
    ]
    generated_content = _effective_generated_content(tmp_path)
    assert generated_content.draft_pr.output == "json"
    assert generated_content.issue_from_prd.output == "json"


def test_migration_reports_but_keeps_disabled_generation(tmp_path: Path) -> None:
    """最早脚手架的 `enabled = false` 只报告不清：清掉会让仓库开始调用 AI，行为必须不变。"""
    config_path = _write_config(
        tmp_path,
        '[agent_runner.repository]\nid = "target-local"\n',
        OLDEST_SCAFFOLD_DISABLED_GENERATED_CONTENT,
    )

    migration_result = migrate_repository_local_config(tmp_path)

    assert _kept_pin_keys(migration_result) == [
        ("agent_runner.generated_content", "enabled"),
        ("agent_runner.generated_content.issue_from_prd", "enabled"),
        ("agent_runner.generated_content.draft_pr", "enabled"),
    ]
    assert all("generation stays off" in pin.reason for pin in migration_result.kept_pins)
    expected_config_text = """\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content]
enabled = false

[agent_runner.generated_content.issue_from_prd]
enabled = false

[agent_runner.generated_content.draft_pr]
enabled = false
"""
    assert config_path.read_text(encoding="utf-8") == expected_config_text
    generated_content = _effective_generated_content(tmp_path)
    assert not generated_content.enabled
    assert not generated_content.issue_from_prd.enabled
    assert not generated_content.draft_pr.enabled

    second_result = migrate_repository_local_config(tmp_path)

    assert not second_result.changed
    assert len(second_result.kept_pins) == 3


def test_migrated_scaffold_follows_the_current_defaults(tmp_path: Path) -> None:
    """迁移前钉在 template / json；迁移后各 target 回到当前默认（agent / markdown）。"""
    _write_config(
        tmp_path,
        CONFIG_BEFORE_GENERATED_CONTENT,
        LEGACY_GENERATED_CONTENT,
        CONFIG_AFTER_GENERATED_CONTENT,
    )
    assert _mode_and_output_by_target(_effective_generated_content(tmp_path)) == {
        "issue_from_prd": ("template", "json"),
        # issue_from_prompt 在旧脚手架里根本没有这一段，迁移前后都取打包默认值。
        "issue_from_prompt": ("agent", "json"),
        "draft_pr": ("template", "json"),
        "prd_from_issue": ("agent", "json"),
    }

    migrate_repository_local_config(tmp_path)

    assert _mode_and_output_by_target(_effective_generated_content(tmp_path)) == {
        "issue_from_prd": ("agent", "json"),
        "issue_from_prompt": ("agent", "json"),
        "draft_pr": ("agent", "markdown"),
        "prd_from_issue": ("agent", "markdown"),
    }


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """再跑一遍什么都不做：没有可清的钉子，也不改文件。"""
    config_path = _write_config(tmp_path, CONFIG_BEFORE_GENERATED_CONTENT, LEGACY_GENERATED_CONTENT)
    migrate_repository_local_config(tmp_path)
    migrated_bytes = config_path.read_bytes()

    second_result = migrate_repository_local_config(tmp_path)

    assert not second_result.changed
    assert not second_result.wrote_file
    assert second_result.removed_pins == ()
    assert config_path.read_bytes() == migrated_bytes


def test_dry_run_reports_the_plan_without_writing(tmp_path: Path) -> None:
    """dry-run 给出与真实迁移完全一致的结果，但不碰文件，也不留临时文件。"""
    config_path = _write_config(tmp_path, CONFIG_BEFORE_GENERATED_CONTENT, LEGACY_GENERATED_CONTENT)
    original_bytes = config_path.read_bytes()

    dry_run_result = migrate_repository_local_config(tmp_path, dry_run=True)

    assert dry_run_result.changed
    assert not dry_run_result.wrote_file
    assert len(dry_run_result.removed_pins) == 34
    assert config_path.read_bytes() == original_bytes
    assert sorted(path.name for path in tmp_path.iterdir()) == [".iar.toml"]

    real_result = migrate_repository_local_config(tmp_path)

    assert real_result.wrote_file
    assert config_path.read_text(encoding="utf-8") == dry_run_result.migrated_text


def test_migration_skips_over_multiline_values_and_quoted_lookalikes(tmp_path: Path) -> None:
    """多行字符串 / 数组里的 ``[表头]``、``key = value``、``#`` 都不是语句，也不能被误删。"""
    config_text = '''\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content.draft_pr]
enabled = true
title_template = "fix: {title} # [x] enabled = true"
body_template = [
  "## Summary",  # ] a bracket in a trailing comment
  "",
  "closes #{issue_number}",
]
agent = "auto"
prompt = """
# a heading inside a string, not a comment
[not.a.header]
enabled = true
mode = "template"
"""
include_diff_stat = true
'''
    config_path = _write_config(tmp_path, config_text)
    original_config = _parsed_config(config_text)

    migration_result = migrate_repository_local_config(tmp_path)

    assert [pin.key_name for pin in migration_result.removed_pins] == [
        "enabled",
        "agent",
        "include_diff_stat",
    ]
    migrated_text = config_path.read_text(encoding="utf-8")
    expected_migrated_text = '''\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content.draft_pr]
title_template = "fix: {title} # [x] enabled = true"
body_template = [
  "## Summary",  # ] a bracket in a trailing comment
  "",
  "closes #{issue_number}",
]
prompt = """
# a heading inside a string, not a comment
[not.a.header]
enabled = true
mode = "template"
"""
'''
    assert migrated_text == expected_migrated_text
    migrated_draft_pr = _parsed_config(migrated_text)["agent_runner"]["generated_content"][
        "draft_pr"
    ]
    original_draft_pr = original_config["agent_runner"]["generated_content"]["draft_pr"]
    assert migrated_draft_pr == {
        key_name: key_value
        for key_name, key_value in original_draft_pr.items()
        if key_name not in {"enabled", "agent", "include_diff_stat"}
    }


def test_migration_reports_pins_it_cannot_locate_instead_of_editing_them(
    tmp_path: Path,
) -> None:
    """点分键 / 内联表写法不是逐行可删的语句：只报告，让用户手动处理，文件不动。"""
    config_text = """\
[agent_runner.repository]
id = "target-local"

[agent_runner.generated_content]
draft_pr = { mode = "template", output = "json" }
issue_from_prd.timeout_seconds = 120
"""
    config_path = _write_config(tmp_path, config_text)

    migration_result = migrate_repository_local_config(tmp_path)

    assert not migration_result.changed
    assert not migration_result.wrote_file
    assert config_path.read_text(encoding="utf-8") == config_text
    assert _kept_pin_keys(migration_result) == [
        ("agent_runner.generated_content.issue_from_prd", "timeout_seconds"),
        ("agent_runner.generated_content.draft_pr", "mode"),
        ("agent_runner.generated_content.draft_pr", "output"),
    ]
    assert all("by hand" in pin.reason for pin in migration_result.kept_pins)


def test_migration_only_touches_generated_content_tables(tmp_path: Path) -> None:
    """同名键（``enabled`` / ``default_agent``）出现在别的表里时一律不动。"""
    config_path = _write_config(
        tmp_path, CONFIG_BEFORE_GENERATED_CONTENT, CONFIG_AFTER_GENERATED_CONTENT
    )
    original_text = config_path.read_text(encoding="utf-8")

    migration_result = migrate_repository_local_config(tmp_path)

    assert not migration_result.changed
    assert migration_result.removed_pins == ()
    assert migration_result.kept_pins == ()
    assert config_path.read_text(encoding="utf-8") == original_text


def test_migration_preserves_crlf_line_endings(tmp_path: Path) -> None:
    """CRLF 文件迁移后每个换行仍是 CRLF，不会混入裸 LF。"""
    config_path = _write_config(
        tmp_path,
        CONFIG_BEFORE_GENERATED_CONTENT,
        LEGACY_GENERATED_CONTENT,
        CONFIG_AFTER_GENERATED_CONTENT,
    )
    crlf_bytes = config_path.read_bytes().replace(b"\n", b"\r\n")
    config_path.write_bytes(crlf_bytes)

    migration_result = migrate_repository_local_config(tmp_path)

    assert migration_result.wrote_file
    migrated_bytes = config_path.read_bytes()
    assert b"\r\n" in migrated_bytes
    assert b"\n" not in migrated_bytes.replace(b"\r\n", b"")
    assert b"[agent_runner.generated_content" not in migrated_bytes


def test_migration_writes_through_symlinks_and_keeps_permissions(tmp_path: Path) -> None:
    """``.iar.toml`` 是符号链接时改的是链接目标，链接本身与文件权限都保持不变。"""
    shared_config_path = tmp_path / "shared" / "iar.toml"
    shared_config_path.parent.mkdir()
    shared_config_path.write_text(
        "\n".join([CONFIG_BEFORE_GENERATED_CONTENT, LEGACY_GENERATED_CONTENT]), encoding="utf-8"
    )
    shared_config_path.chmod(0o600)
    repo_root_path = tmp_path / "repo"
    repo_root_path.mkdir()
    (repo_root_path / ".iar.toml").symlink_to(shared_config_path)

    migration_result = migrate_repository_local_config(repo_root_path)

    assert migration_result.wrote_file
    assert (repo_root_path / ".iar.toml").is_symlink()
    assert "[agent_runner.generated_content" not in shared_config_path.read_text(encoding="utf-8")
    assert shared_config_path.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in shared_config_path.parent.iterdir()) == ["iar.toml"]


def test_migration_rejects_invalid_toml_and_leaves_it_alone(tmp_path: Path) -> None:
    """不是合法 TOML 时报错而不是猜着改。"""
    config_path = tmp_path / ".iar.toml"
    config_path.write_text("[agent_runner\nbroken = ", encoding="utf-8")

    with pytest.raises(ConfigMigrationError, match="not valid TOML"):
        migrate_repository_local_config(tmp_path)

    assert config_path.read_text(encoding="utf-8") == "[agent_runner\nbroken = "


def test_migration_requires_an_initialized_repository(tmp_path: Path) -> None:
    """没有 ``.iar.toml`` 时抛统一的“未初始化”错误，由 CLI 统一提示 ``iar init``。"""
    with pytest.raises(IARRepositoryNotInitializedError) as exc_info:
        migrate_repository_local_config(tmp_path)

    assert exc_info.value.config_path == tmp_path / ".iar.toml"


def test_migration_refuses_to_write_when_the_result_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写回前的 tomllib 校验兜底：重建结果与预期不一致时拒绝写入，原文件与目录都不变。"""
    config_path = _write_config(tmp_path, CONFIG_BEFORE_GENERATED_CONTENT, LEGACY_GENERATED_CONTENT)
    original_bytes = config_path.read_bytes()
    rebuild_text = migration_module._rebuild_text
    monkeypatch.setattr(
        migration_module,
        "_rebuild_text",
        lambda toml_lines, deleted_line_indexes: (
            rebuild_text(toml_lines, deleted_line_indexes) + "stowaway = true\n"
        ),
    )

    with pytest.raises(ConfigMigrationError, match="nothing was written"):
        migrate_repository_local_config(tmp_path)

    assert config_path.read_bytes() == original_bytes
    assert sorted(path.name for path in tmp_path.iterdir()) == [".iar.toml"]


def test_legacy_pin_tables_only_name_real_generated_content_fields() -> None:
    """冻结的历史值表只能引用真实存在的字段与合法取值，防止字段改名后迁移静默失效。"""
    section_field_names = set(AgentRunnerGeneratedContentSettings.model_fields) - set(
        GENERATED_CONTENT_TARGET_NAMES
    )
    target_field_names = set(AgentRunnerGeneratedContentTargetSettings.model_fields)

    assert set(migration_module._LEGACY_SECTION_PIN_VALUES) <= section_field_names
    assert set(migration_module._LEGACY_TARGET_PIN_VALUES) <= target_field_names
    for field_name, legacy_values in migration_module._LEGACY_SECTION_PIN_VALUES.items():
        for legacy_value in legacy_values:
            AgentRunnerGeneratedContentSettings(**{field_name: legacy_value})
    for field_name, legacy_values in migration_module._LEGACY_TARGET_PIN_VALUES.items():
        for legacy_value in legacy_values:
            AgentRunnerGeneratedContentTargetSettings(**{field_name: legacy_value})


def test_migration_handles_spaced_headers_and_reports_quoted_ones(tmp_path: Path) -> None:
    """表头带空格 / 行尾注释照常处理；表名带引号的写法定位不到，只报告不编辑。"""
    config_path = _write_config(
        tmp_path,
        """\
[agent_runner.repository]
id = "target-local"

[ agent_runner . generated_content ]  # 区块
enabled = true

[agent_runner."generated_content".draft_pr]
mode = "template"
""",
    )

    migration_result = migrate_repository_local_config(tmp_path)

    assert [(pin.table_name, pin.key_name) for pin in migration_result.removed_pins] == [
        ("agent_runner.generated_content", "enabled")
    ]
    assert _kept_pin_keys(migration_result) == [("agent_runner.generated_content.draft_pr", "mode")]
    expected_config_text = """\
[agent_runner.repository]
id = "target-local"

[agent_runner."generated_content".draft_pr]
mode = "template"
"""
    assert config_path.read_text(encoding="utf-8") == expected_config_text


def test_migration_keeps_comments_detached_from_the_removed_block(tmp_path: Path) -> None:
    """与被删钉子之间隔着空行的注释不属于它：保留；文件末尾不留悬空空行。"""
    config_path = _write_config(
        tmp_path,
        """\
[agent_runner.repository]
id = "target-local"

# ---- 下面是内容生成区块（此注释与区块之间有空行）----

[agent_runner.generated_content]
enabled = true
""",
    )

    migrate_repository_local_config(tmp_path)

    expected_config_text = """\
[agent_runner.repository]
id = "target-local"

# ---- 下面是内容生成区块（此注释与区块之间有空行）----
"""
    assert config_path.read_text(encoding="utf-8") == expected_config_text
