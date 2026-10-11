"""统一生效并发上限（execution ceiling）解析的单元测试。

对应 PRD P1-FEAT-20261010-011714：Backlog「并发」策略与 runner 容量收敛为
一个生效值 ``min(policy, capacity)``；从未设置的策略 = ``None`` 表示继承容量。
存储层「从未设置」= 没有设置行，「恢复继承」= 删除设置行。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.backlog import BacklogSettingsEntry
from backend.core.use_cases.backlog_concurrency import (
    CEILING_SOURCE_CAPPED_BY_CAPACITY,
    CEILING_SOURCE_INHERITED,
    CEILING_SOURCE_POLICY,
    describe_ceiling_source,
    read_policy_max_parallel,
    resolve_execution_ceiling,
)
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from tests.conftest import FakeBacklogStore

REPO_ID = "keda-test"


# --- resolve_execution_ceiling ----------------------------------------------


def test_resolve_ceiling_inherits_capacity_when_policy_unset() -> None:
    """策略为 ``None``（从未设置）时生效值 = runner 容量。"""
    assert resolve_execution_ceiling(None, 4) == 4


def test_resolve_ceiling_takes_policy_when_below_capacity() -> None:
    """策略低于容量时生效值 = 策略值。"""
    assert resolve_execution_ceiling(2, 5) == 2


def test_resolve_ceiling_caps_policy_at_capacity() -> None:
    """策略高于容量时生效值 = 容量（页面数字被 runner 压回）。"""
    assert resolve_execution_ceiling(6, 3) == 3


def test_resolve_ceiling_equal_values_take_either_side() -> None:
    """策略恰等于容量时两口径一致。"""
    assert resolve_execution_ceiling(3, 3) == 3


def test_resolve_ceiling_rejects_non_positive_capacity() -> None:
    """容量 < 1 是配置错误，必须显式报错而不是静默放行 0 并发。"""
    with pytest.raises(ValueError, match="runner_capacity"):
        resolve_execution_ceiling(None, 0)


def test_resolve_ceiling_rejects_non_positive_policy() -> None:
    """策略 < 1 非法（哨兵值曾被用作「未设置」的伪装，现在禁止）。"""
    with pytest.raises(ValueError, match="policy_max_parallel"):
        resolve_execution_ceiling(0, 4)


# --- describe_ceiling_source ------------------------------------------------


def test_describe_ceiling_source_tristate() -> None:
    """来源三态：未设置→继承；策略即生效值；策略高于生效值→被容量压低。"""
    assert describe_ceiling_source(None, 4) == CEILING_SOURCE_INHERITED
    assert describe_ceiling_source(2, 2) == CEILING_SOURCE_POLICY
    assert describe_ceiling_source(2, 4) == CEILING_SOURCE_POLICY
    assert describe_ceiling_source(6, 3) == CEILING_SOURCE_CAPPED_BY_CAPACITY


# --- read_policy_max_parallel ------------------------------------------------


def test_read_policy_returns_none_when_settings_row_missing() -> None:
    """「从未设置」= 没有设置行；只读绝不为了读而写行。"""
    store = FakeBacklogStore(repo_id=REPO_ID, max_parallel=None)
    assert read_policy_max_parallel(store, REPO_ID) is None
    assert store.settings is None


def test_read_policy_returns_saved_value() -> None:
    """已落库的策略值原样读回。"""
    store = FakeBacklogStore(repo_id=REPO_ID, max_parallel=3)
    assert read_policy_max_parallel(store, REPO_ID) == 3


def test_read_policy_treats_legacy_non_positive_row_as_unset() -> None:
    """历史库里的非法值（<1）按未设置回退，不炸解析函数。"""
    store = FakeBacklogStore(repo_id=REPO_ID, max_parallel=None)
    store.settings = BacklogSettingsEntry(
        repo_id=REPO_ID, max_parallel=0, default_view="list", updated_at=""
    )
    assert read_policy_max_parallel(store, REPO_ID) is None


def test_read_policy_returns_none_for_unknown_repo() -> None:
    """未登记仓库没有设置行，读作未设置。"""
    store = FakeBacklogStore(repo_id=REPO_ID, max_parallel=3)
    assert read_policy_max_parallel(store, "other-repo") is None


# --- 恢复继承 = 删行（SQLite 真实存储） --------------------------------------


def test_delete_backlog_settings_restores_inheritance(tmp_path: Path) -> None:
    """删行后策略读回 ``None``（继承容量）；重复删除幂等静默成功。"""
    db_path = tmp_path / "console.db"
    store = SqliteConsoleStore(db_path)
    store.save_backlog_settings(
        BacklogSettingsEntry(
            repo_id=REPO_ID, max_parallel=2, default_view="timeline", updated_at="t0"
        )
    )
    assert read_policy_max_parallel(store, REPO_ID) == 2

    store.delete_backlog_settings(REPO_ID)
    assert store.get_backlog_settings(REPO_ID) is None
    assert read_policy_max_parallel(store, REPO_ID) is None

    store.delete_backlog_settings(REPO_ID)
    assert store.get_backlog_settings(REPO_ID) is None


def test_sqlite_read_policy_never_creates_row(tmp_path: Path) -> None:
    """只读解析在无设置行的库上返回 ``None`` 且不落新行（替换 get_or_create）。"""
    store = SqliteConsoleStore(tmp_path / "console.db")
    assert read_policy_max_parallel(store, REPO_ID) is None
    assert store.get_backlog_settings(REPO_ID) is None
