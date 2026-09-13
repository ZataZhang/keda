"""守护 Worktree 专用数据库派生与建库脚本契约的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# 仓库根推导而非 CWD 相对路径：守卫必须与 pytest 的启动目录解耦。
_PROJECT_ROOT_PATH = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT_PATH / "scripts" / "shared" / "template" / "setup_copied_database.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "setup_copied_database",
    _SCRIPT_PATH,
)
assert _SCRIPT_SPEC is not None
assert _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)


def test_derive_database_name_preserves_digest_for_long_identifier() -> None:
    """超长且前缀相同的标识仍派生不同的合法数据库名。"""
    shared_prefix = "feature-" + "x" * 70
    first_database_name = _SCRIPT_MODULE.derive_database_name(f"{shared_prefix}-one")
    second_database_name = _SCRIPT_MODULE.derive_database_name(f"{shared_prefix}-two")

    assert len(first_database_name) <= 63
    assert len(second_database_name) <= 63
    assert first_database_name != second_database_name


def test_derive_database_name_prefixes_leading_digit() -> None:
    """派生名称始终可作为未引用的 PostgreSQL 标识符使用。"""
    assert _SCRIPT_MODULE.derive_database_name("2026-feature") == "app_2026_feature"


def test_update_database_url_rewrites_only_postgres_database_name(tmp_path: Path) -> None:
    """更新 Worktree 配置时保留连接凭据和其他环境变量。"""
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "LOG_LEVEL=INFO\n" "DATABASE_URL=postgresql+psycopg2://app:secret@localhost:5432/main_db\n",
        encoding="utf-8",
    )

    updated_database_url, derived_database_name = _SCRIPT_MODULE.update_database_url(
        env_local_path,
        "template-wt-feature-login-a1b2c3d4",
    )

    assert derived_database_name == "template_wt_feature_login_a1b2c3d4"
    assert updated_database_url is not None
    assert updated_database_url.endswith(f"/{derived_database_name}")
    assert env_local_path.read_text(encoding="utf-8") == (
        "LOG_LEVEL=INFO\nDATABASE_URL="
        f"postgresql+psycopg2://app:secret@localhost:5432/{derived_database_name}\n"
    )


def test_strict_mode_rejects_missing_postgres_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Worktree 严格模式不会静默回退到共享数据库。"""
    monkeypatch.setattr(
        _SCRIPT_MODULE.sys,
        "argv",
        ["setup_copied_database.py", "worktree-db", str(tmp_path), "--strict"],
    )

    assert _SCRIPT_MODULE.main() == 1


def test_database_setup_does_not_print_connection_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自动建库日志只输出数据库名，不输出连接凭据。"""
    secret_database_url = "postgresql://app:top-secret@localhost:5432/worktree_db"
    monkeypatch.setattr(
        _SCRIPT_MODULE.sys,
        "argv",
        ["setup_copied_database.py", "worktree-db", str(tmp_path), "--strict"],
    )
    monkeypatch.setattr(
        _SCRIPT_MODULE,
        "update_database_url",
        lambda *_: (secret_database_url, "worktree_db"),
    )
    monkeypatch.setattr(_SCRIPT_MODULE, "create_postgres_database", lambda *_, **__: True)

    assert _SCRIPT_MODULE.main() == 0

    captured_output = capsys.readouterr().out
    assert "worktree_db" in captured_output
    assert "top-secret" not in captured_output


def test_compose_mysql_create_database_sql_uses_safe_defaults() -> None:
    """MySQL 建库默认走 utf8mb4 / utf8mb4_unicode_ci，远离 MySQL 8 0900_ai_ci。"""
    sql = _SCRIPT_MODULE.compose_mysql_create_database_sql("wt_db")
    assert sql == ("CREATE DATABASE `wt_db` " "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    assert "utf8mb4_0900_ai_ci" not in sql


def test_compose_mysql_create_database_sql_honors_overrides() -> None:
    """下游项目可指定 charset / collation。"""
    sql = _SCRIPT_MODULE.compose_mysql_create_database_sql(
        "wt_db", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"
    )
    assert "COLLATE utf8mb4_0900_ai_ci" in sql


def test_compose_postgres_create_database_sql_uses_template0_by_default() -> None:
    """PostgreSQL 默认从 template0 复制，避免被 template1 自定义污染。"""
    sql = _SCRIPT_MODULE.compose_postgres_create_database_sql("wt_db")
    assert sql == "CREATE DATABASE wt_db TEMPLATE template0;"


def test_compose_postgres_create_database_sql_includes_locale_and_encoding() -> None:
    """显式给定 LOCALE / ENCODING 时追加到 CREATE DATABASE 中。"""
    sql = _SCRIPT_MODULE.compose_postgres_create_database_sql(
        "wt_db", locale="C.UTF-8", encoding="UTF8"
    )
    assert "ENCODING = 'UTF8'" in sql
    assert "LOCALE = 'C.UTF-8'" in sql
    assert "TEMPLATE template0" in sql


def test_read_database_options_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """目标 .env.local 不存在时返回空字典。"""
    assert _SCRIPT_MODULE.read_database_options(tmp_path / ".env.local") == {}


def test_read_database_options_parses_known_keys(tmp_path: Path) -> None:
    """只识别登记在 DATABASE_OPTION_KEYS 里的键，忽略其它 env var。"""
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "\n".join(
            [
                "# 与数据库建库无关的环境变量",
                "LOG_LEVEL=INFO",
                "DATABASE_CHARSET=utf8mb4",
                "DATABASE_COLLATION=utf8mb4_unicode_ci",
                "DATABASE_LOCALE=C.UTF-8",
                "DATABASE_ENCODING=UTF8",
                "OTHER_DB_OPTION=ignored",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    options = _SCRIPT_MODULE.read_database_options(env_local_path)
    assert options == {
        "DATABASE_CHARSET": "utf8mb4",
        "DATABASE_COLLATION": "utf8mb4_unicode_ci",
        "DATABASE_LOCALE": "C.UTF-8",
        "DATABASE_ENCODING": "UTF8",
    }
    assert "OTHER_DB_OPTION" not in options
    assert "LOG_LEVEL" not in options


def test_read_database_options_preserves_empty_values(tmp_path: Path) -> None:
    """空值保留——与"未设置"由调用方 ``or <default>`` 兜底。"""
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "DATABASE_CHARSET=\nDATABASE_LOCALE=C.UTF-8\n",
        encoding="utf-8",
    )

    options = _SCRIPT_MODULE.read_database_options(env_local_path)
    assert options["DATABASE_CHARSET"] == ""
    assert options["DATABASE_LOCALE"] == "C.UTF-8"


def test_main_passes_database_options_to_mysql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() 把 .env.local 里的 charset/collation 透给 create_mysql_database。"""
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "DATABASE_URL=mysql://app:secret@localhost:3306/main_db\n"
        "DATABASE_CHARSET=utf8mb4\n"
        "DATABASE_COLLATION=utf8mb4_0900_ai_ci\n",
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    def _fake_create_mysql(database_url: str, charset: str, collation: str) -> bool:
        captured["database_url"] = database_url
        captured["charset"] = charset
        captured["collation"] = collation
        return True

    monkeypatch.setattr(
        _SCRIPT_MODULE.sys, "argv", ["setup_copied_database.py", "wt", str(tmp_path)]
    )
    monkeypatch.setattr(_SCRIPT_MODULE, "create_mysql_database", _fake_create_mysql)

    assert _SCRIPT_MODULE.main() == 0
    assert captured["charset"] == "utf8mb4"
    assert captured["collation"] == "utf8mb4_0900_ai_ci"


def test_main_passes_database_options_to_postgres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() 把 .env.local 里的 locale/encoding 透给 create_postgres_database。"""
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "DATABASE_URL=postgresql://app:secret@localhost:5432/main_db\n"
        "DATABASE_LOCALE=C.UTF-8\n"
        "DATABASE_ENCODING=UTF8\n",
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    def _fake_create_postgres(database_url: str, locale: str, encoding: str) -> bool:
        captured["database_url"] = database_url
        captured["locale"] = locale
        captured["encoding"] = encoding
        return True

    monkeypatch.setattr(
        _SCRIPT_MODULE.sys, "argv", ["setup_copied_database.py", "wt", str(tmp_path)]
    )
    monkeypatch.setattr(_SCRIPT_MODULE, "create_postgres_database", _fake_create_postgres)

    assert _SCRIPT_MODULE.main() == 0
    assert captured["locale"] == "C.UTF-8"
    assert captured["encoding"] == "UTF8"
