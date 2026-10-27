"""保留式 TOML 段编辑器的落盘语义测试。

事实源是**磁盘文本**：断言"只动点名的键 / 段"、no-op 写回不产生文件抖动、
数组表原地改写保住位置与注释。生命周期预设 / 绑定 / 回退候选的写回都走本模块。
"""

from __future__ import annotations

import fcntl
import os
import stat
import time
from pathlib import Path

import pytest
import tomlkit

from backend.core.shared.models.config_edit import (
    ConfigDocumentInvalidError,
    ConfigKeyMissingError,
    ConfigLockBusyError,
)
from backend.infrastructure.config import toml_section_editor
from backend.infrastructure.config.toml_section_editor import (
    ArrayTableUpdate,
    TableKeyUpdate,
    apply_toml_updates,
    config_write_lock,
    mutate_toml_document,
    update_toml_array_of_tables,
    update_toml_table_keys,
)

_RUNNER_TABLE = ("agent_runner", "runner")
_CANDIDATES_KEY = "agent_fallback_candidates"
_LIFECYCLE_PRESETS_TABLE = ("agent_runner", "lifecycle_presets")
_GHOST_PRESET_TABLE = ("agent_runner", "presets", "ghost")

#: 没有 fcntl/msvcrt 的平台上跨进程建议锁不成立：相关断言跳过，而不是"静默通过"。
requires_advisory_lock = pytest.mark.skipif(
    toml_section_editor._LOCK_BACKEND == "none",
    reason="本平台没有 fcntl / msvcrt，跨进程写锁不可验证",
)

_CONFIG_WITH_ANNOTATED_CANDIDATES = """\
[agent_runner]

[agent_runner.runner]
max_agent_switches = 2

# 段前手写注释：候选链原地改写须保住它
[[agent_runner.runner.agent_fallback_candidates]]
agent = "claude"
[[agent_runner.runner.agent_fallback_candidates]]
agent = "kimi"

[agent_runner.runner.unrelated]
keep = true
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """写一份带注释与后续子表的 config.toml，作为落盘断言的起点。"""
    path = tmp_path / "config.toml"
    path.write_text(_CONFIG_WITH_ANNOTATED_CANDIDATES, encoding="utf-8")
    return path


def test_same_value_write_is_no_op(config_path: Path) -> None:
    """同值写回不落盘：共写但没变的键不产生文件抖动。"""
    before = config_path.read_bytes()
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 2})
    assert config_path.read_bytes() == before

    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 1})
    assert b"max_agent_switches = 1" in config_path.read_bytes()


def test_delete_of_absent_key_never_creates_file(tmp_path: Path) -> None:
    """目标键本就不存在的纯删除不落盘，也不凭空建文件。"""
    missing_path = tmp_path / "absent.toml"
    update_toml_table_keys(missing_path, _RUNNER_TABLE, {"max_agent_switches": None})
    assert not missing_path.exists()


def test_failed_replace_leaves_file_and_directory_untouched(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """替换失败时原文件一字不动、目录不留临时文件。"""
    before = config_path.read_bytes()
    temp_names: list[str] = []

    def _capture_and_fail(temp_path: Path, _target: Path) -> None:
        temp_names.append(Path(temp_path).name)
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _capture_and_fail)
    with pytest.raises(OSError, match="simulated replace failure"):
        update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 9})

    assert config_path.read_bytes() == before
    assert temp_names, "写回未经过临时文件"
    assert not [p for p in config_path.parent.iterdir() if p.name.endswith(".tmp")]


def test_successive_writes_use_distinct_temp_names(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每次落盘的临时文件名互不相同——共用固定名会让并发写互相截断。"""
    observed: list[str] = []
    real_replace = os.replace

    def _observe(temp_path: Path, target: Path) -> None:
        observed.append(Path(temp_path).name)
        real_replace(temp_path, target)

    monkeypatch.setattr(os, "replace", _observe)
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 3})
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 4})

    assert len(observed) == 2
    assert observed[0] != observed[1]
    assert b"max_agent_switches = 4" in config_path.read_bytes()


def test_array_table_rewrite_keeps_position_and_comments(config_path: Path) -> None:
    """数组表原地改写：保住段前注释与其后的子表顺序，重复提交同值不落盘。"""
    before = config_path.read_bytes()
    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [{"agent": "claude", "preset": None}, {"agent": "kimi", "preset": None}],
    )
    assert config_path.read_bytes() == before

    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [{"agent": "codex", "preset": "fast"}, {"agent": "kimi", "preset": None}],
    )
    written = config_path.read_text(encoding="utf-8")
    assert "# 段前手写注释：候选链原地改写须保住它" in written
    assert written.index("[[agent_runner.runner.agent_fallback_candidates]]") < written.index(
        "[agent_runner.runner.unrelated]"
    )
    assert 'agent = "codex"' in written
    assert 'preset = "fast"' in written


def test_array_table_grows_and_shrinks_in_order(config_path: Path) -> None:
    """候选数量变化按期望顺序收口：追加补条目、多余弹出、None 字段删键。"""
    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [
            {"agent": "claude", "preset": None},
            {"agent": "kimi", "preset": None},
            {"agent": "codex", "preset": "slow"},
        ],
    )
    grown = config_path.read_text(encoding="utf-8")
    assert grown.count("[[agent_runner.runner.agent_fallback_candidates]]") == 3
    assert 'agent = "codex"' in grown

    update_toml_array_of_tables(
        config_path,
        _RUNNER_TABLE,
        _CANDIDATES_KEY,
        [{"agent": "claude", "preset": None}],
    )
    shrunk = config_path.read_text(encoding="utf-8")
    assert shrunk.count("[[agent_runner.runner.agent_fallback_candidates]]") == 1
    assert 'agent = "kimi"' not in shrunk
    assert 'agent = "codex"' not in shrunk
    assert "preset" not in shrunk.split("[[agent_runner")[1]


def test_empty_entries_removes_the_array_table_key(config_path: Path) -> None:
    """空列表清空该键，其余内容（含注释与后续子表）不动。"""
    update_toml_array_of_tables(config_path, _RUNNER_TABLE, _CANDIDATES_KEY, [])
    written = config_path.read_text(encoding="utf-8")
    assert "[[agent_runner.runner.agent_fallback_candidates]]" not in written
    assert 'agent = "claude"' not in written
    assert 'agent = "kimi"' not in written
    assert "[agent_runner.runner.unrelated]" in written


# ─────────────────────────────────────────────────────────────────────────────
# 落盘权限、跨进程锁、单事务与"删不掉就失败"
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def bound_presets_path(tmp_path: Path) -> Path:
    """写一份带 ``[agent_runner.lifecycle_presets]`` 与一个已声明预设的配置。"""
    path = tmp_path / "bound.toml"
    path.write_text(
        """\
[agent_runner]
[agent_runner.lifecycle_presets]
review = "hot"

[agent_runner.presets.hot]
agent = "claude"
model = "sonnet"
""",
        encoding="utf-8",
    )
    return path


def test_new_lock_file_without_fchmod_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 ``os.fchmod`` 的平台仍能打开配置锁文件。"""
    if hasattr(os, "fchmod"):
        monkeypatch.delattr(os, "fchmod")

    lock_path = tmp_path / "config.toml.lock"
    file_descriptor = toml_section_editor._open_lock_file(lock_path)
    try:
        os.write(file_descriptor, b"lock")
    finally:
        os.close(file_descriptor)

    assert lock_path.read_bytes() == b"lock"


def test_write_keeps_existing_file_mode(config_path: Path) -> None:
    """原子替换沿用目标权限位：一次写回不把 0644 降级成 mkstemp 的 0600。

    ``tempfile.mkstemp`` 建出的临时文件是 0600，``os.replace`` 会把该 mode 带到目标；
    机器级 ``config.toml`` 因此会在每次保存后变成只有属主可读，其他读取者静默失效。
    """
    os.chmod(config_path, 0o644)
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 5})
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o644


def test_write_keeps_tighter_current_mode(config_path: Path) -> None:
    """0600 是现状时同样沿用——写回只保持权限，不擅自放宽或收紧。"""
    os.chmod(config_path, 0o600)
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 5})
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_created_config_file_is_readable_by_default(tmp_path: Path) -> None:
    """新建配置文件用 0644，而不是临时文件的 0600。"""
    created = tmp_path / "fresh.toml"
    update_toml_table_keys(created, _RUNNER_TABLE, {"max_agent_switches": 1})
    assert created.is_file()
    assert stat.S_IMODE(created.stat().st_mode) == 0o644


def test_batched_update_lands_in_a_single_replace(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选链 + 预算收敛成一次读-改-写：只发生一次 ``os.replace``。"""
    real_replace = os.replace
    replaced: list[Path] = []

    def _observe(temp_path: Path, target: Path) -> None:
        replaced.append(Path(target))
        real_replace(temp_path, target)

    monkeypatch.setattr(os, "replace", _observe)
    apply_toml_updates(
        config_path,
        [
            ArrayTableUpdate(_RUNNER_TABLE, _CANDIDATES_KEY, [{"agent": "codex", "preset": None}]),
            TableKeyUpdate(_RUNNER_TABLE, {"max_agent_switches": 3}),
        ],
    )

    assert replaced == [config_path]
    written = config_path.read_text(encoding="utf-8")
    assert 'agent = "codex"' in written
    assert "max_agent_switches = 3" in written


def test_batched_update_aborts_entire_batch_on_invalid_delete(
    config_path: Path,
) -> None:
    """一批改动里任一段校验不过 → 整批不落盘，已能写的那半也不会先进文件。

    这是"失败时配置文件不发生部分改动"的判别口径：去掉写前的存在性检查，
    ``max_agent_switches`` 就会先落盘、随后才抛错。
    """
    before = config_path.read_bytes()
    with pytest.raises(ConfigKeyMissingError):
        apply_toml_updates(
            config_path,
            [
                TableKeyUpdate(_RUNNER_TABLE, {"max_agent_switches": 9}),
                TableKeyUpdate(_GHOST_PRESET_TABLE, {"agent": None}, presence="table"),
            ],
        )

    assert config_path.read_bytes() == before


def test_delete_of_preset_absent_in_target_layer_fails(bound_presets_path: Path) -> None:
    """删一个本层根本没声明的预设必须失败：这类删除不会改动任何配置，不能报成功。"""
    before = bound_presets_path.read_bytes()
    with pytest.raises(ConfigKeyMissingError, match="agent_runner.presets.ghost"):
        apply_toml_updates(
            bound_presets_path,
            [TableKeyUpdate(_GHOST_PRESET_TABLE, {"agent": None}, presence="table")],
        )
    assert bound_presets_path.read_bytes() == before


def test_unset_of_unbound_stage_fails_but_bound_stage_succeeds(bound_presets_path: Path) -> None:
    """解绑要求该阶段在本层确有绑定；本层绑定的阶段仍能正常解绑。"""
    before = bound_presets_path.read_bytes()
    with pytest.raises(ConfigKeyMissingError, match="planner"):
        apply_toml_updates(
            bound_presets_path,
            [TableKeyUpdate(_LIFECYCLE_PRESETS_TABLE, {"planner": None}, presence="keys")],
        )
    assert bound_presets_path.read_bytes() == before

    apply_toml_updates(
        bound_presets_path,
        [TableKeyUpdate(_LIFECYCLE_PRESETS_TABLE, {"review": None}, presence="keys")],
    )
    assert 'review = "hot"' not in bound_presets_path.read_text(encoding="utf-8")


def test_lenient_delete_of_absent_key_stays_a_no_op(bound_presets_path: Path) -> None:
    """默认 ``presence="none"`` 保留旧语义：删不存在的键仍是不落盘的 no-op（矩阵 PUT 依赖）。"""
    before = bound_presets_path.read_bytes()
    apply_toml_updates(
        bound_presets_path,
        [TableKeyUpdate(("agent_runner", "lifecycle_agents"), {"planner": None})],
    )
    assert bound_presets_path.read_bytes() == before


def test_invalid_toml_is_not_overwritten(config_path: Path) -> None:
    """现有文件不是合法 TOML 时抛可读错误、原字节一字不动，而非静默重写成"能解析"的新文件。"""
    corrupted = "[agent_runner.runner\nmax_agent_switches = 2\n"
    config_path.write_text(corrupted, encoding="utf-8")

    with pytest.raises(ConfigDocumentInvalidError, match="不是合法的 TOML"):
        update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 7})

    assert config_path.read_text(encoding="utf-8") == corrupted


def test_read_modify_write_holds_exclusive_lock(config_path: Path) -> None:
    """读-改-写全程持有同目录写锁：第二个写入者拿不到锁，因此不会插进来丢键。"""
    lock_path = config_path.with_name(config_path.name + ".lock")
    lock_held_during_write: list[bool] = []

    def _probe(document: tomlkit.TOMLDocument) -> None:
        with open(lock_path, "a", encoding="utf-8") as probe_handle:
            try:
                fcntl.flock(probe_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_held_during_write.append(True)
                return
            fcntl.flock(probe_handle.fileno(), fcntl.LOCK_UN)
        lock_held_during_write.append(False)
        document["agent_runner"]["runner"]["max_agent_switches"] = 7

    mutate_toml_document(config_path, _probe)

    assert lock_held_during_write == [True]


@requires_advisory_lock
def test_busy_lock_fails_fast_instead_of_blocking_forever(config_path: Path, monkeypatch) -> None:
    """别人持锁时：**有界**等待后报错退出，而不是把 CLI 挂死或长期占住 worker。

    锁被占用是本模块对外承诺的失败之一，必须"配置一字未动 + 可诊断错误"，
    超时上限就是这条承诺的代价：等满就失败，绝不无限等。
    """
    lock_path = config_path.with_name(config_path.name + ".lock")
    with open(lock_path, "a", encoding="utf-8") as holder_handle:
        fcntl.flock(holder_handle.fileno(), fcntl.LOCK_EX)
        monkeypatch.setattr(toml_section_editor, "_LOCK_WAIT_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(toml_section_editor, "_LOCK_RETRY_INTERVAL_SECONDS", 0.02)
        before = config_path.read_bytes()
        started_at = time.monotonic()

        with pytest.raises(ConfigLockBusyError, match="写锁被另一个写入者持有"):
            update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 99})

        assert time.monotonic() - started_at < 5
        fcntl.flock(holder_handle.fileno(), fcntl.LOCK_UN)

    assert config_path.read_bytes() == before


@requires_advisory_lock
def test_lock_is_reentrant_within_one_thread(config_path: Path) -> None:
    """同线程嵌套取同一把锁必须放行：flock 记在 fd 上，不自救就是自死锁。

    两条真实嵌套都要过：把 :func:`apply_toml_updates` 写进 ``mutate`` 回调，以及
    调用方先经 :func:`config_write_lock` 圈住"读基线 + 校验"再落盘。
    """
    nested_marker = {"applied": False}

    def _mutate(document: tomlkit.TOMLDocument) -> None:
        apply_toml_updates(
            config_path,
            [TableKeyUpdate(_RUNNER_TABLE, {"max_agent_switches": 5})],
        )
        nested_marker["applied"] = True
        document["agent_runner"]["runner"]["max_agent_switches"] = 5

    mutate_toml_document(config_path, _mutate)
    assert nested_marker["applied"] is True

    with config_write_lock(config_path):
        apply_toml_updates(
            config_path,
            [TableKeyUpdate(_RUNNER_TABLE, {"max_agent_switches": 6})],
        )
    assert "max_agent_switches = 6" in config_path.read_text(encoding="utf-8")


@requires_advisory_lock
def test_public_write_lock_is_the_same_lock(config_path: Path) -> None:
    """对外暴露的锁与内部读-改-写用的是同一把：否则裸写 Text 的写入者仍能插进来。"""
    lock_path = config_path.with_name(config_path.name + ".lock")
    with config_write_lock(config_path):
        with open(lock_path, "a", encoding="utf-8") as probe_handle:
            with pytest.raises(OSError):
                fcntl.flock(probe_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_lock_file_is_owner_only(config_path: Path) -> None:
    """锁文件不含数据，权限必须是 0600，而不是 ``0666 & ~umask`` 的默认宽权限。"""
    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 3})
    lock_path = config_path.with_name(config_path.name + ".lock")
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_atomic_replace_carries_target_ownership(config_path: Path, monkeypatch) -> None:
    """原子替换把目标原本的 uid/gid 搬到临时文件，写后属主不被换成写入进程。"""
    chown_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        toml_section_editor.os,
        "chown",
        lambda path, uid, gid: chown_calls.append((uid, gid)),
    )

    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 3})

    target_stat = config_path.stat()
    assert chown_calls == [(target_stat.st_uid, target_stat.st_gid)]


def test_unprivileged_chown_does_not_abort_the_write(config_path: Path, monkeypatch) -> None:
    """非特权进程 chown 不动属主时**继续写完**：属主保不住不该把一次合法保存改成失败。"""

    def _deny(_path, _uid, _gid):
        raise PermissionError("not owner")

    monkeypatch.setattr(toml_section_editor.os, "chown", _deny)

    update_toml_table_keys(config_path, _RUNNER_TABLE, {"max_agent_switches": 8})

    assert "max_agent_switches = 8" in config_path.read_text(encoding="utf-8")


def test_strict_reader_refuses_missing_config_without_creating_it(tmp_path: Path) -> None:
    """``create_if_missing=False`` 的读-改-写沿用"文件必须已存在"：缺失即 OSError，不凭空建文件。"""
    missing = tmp_path / "registry.toml"

    def _noop(_document: tomlkit.TOMLDocument) -> None:
        return None

    with pytest.raises(OSError):
        mutate_toml_document(missing, _noop)
    assert not missing.exists()
