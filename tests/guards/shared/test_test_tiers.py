"""守护本地测试分层选集逻辑的守卫测试（guard test；被测对象：
``scripts/shared/testing/test_tiers.py``）。

``just test`` 的本地档 = 名单 ∪ 改动的测试 ∪ src 映射补跑 ∪ core 标记。后三项里
"什么算改动、改动映射到哪些测试、预算怎么切"全由这个脚本决定，而 ``check-test-flag``
把通过标记绑在整份有效树上：这里任何一处回归都不会让别的测试变红，只会让门禁给一批
从未执行过的用例盖章。因此逻辑本身要有行为测试，不能只靠守卫去 grep 接线（接线由
``tests/guards/test_local_core_tier_budget.py`` 守护）。

测试把脚本的仓库根重定向到 ``tmp_path`` 里的假仓库，git 采集换成脚本化输出；文件系统
遍历、名单与基线读取、源码里的 core 标记与测试函数计数都走真实实现。

本文件位于 ``tests/guards/shared/``，失败意味着共享脚本违反了它的契约。正确做法是修复
脚本，而不是修改本文件让测试通过；仅当约定本身需要变更时才改本文件，并同步更新
``docs/ai-standards/testing.md`` 的「本地核心档」「全量档成本棘轮」小节。详见该文档的
Guard Tests 小节。
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

# 仓库根推导而非 CWD 相对路径：守卫必须与 pytest 的启动目录解耦。
_PROJECT_ROOT_PATH = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT_PATH / "scripts" / "shared" / "testing" / "test_tiers.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("test_tiers_under_guard", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None
assert _SCRIPT_SPEC.loader is not None
tiers = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(tiers)

# 守卫与 conftest 都是按源码文本找 core 标记字面量的，整串写在本文件里会让本模块被当成
# 显式 core 模块；所以夹具源码里的标记在运行期由这个名字拼出来。
_CORE_MARKER_NAME = "core"
_CORE_MARKED_SOURCE = f"import pytest\n\npytestmark = pytest.mark.{_CORE_MARKER_NAME}\n"

_ADMIN_ROUTER_SOURCE = "src/backend/api/admin_user_router.py"
_SETTINGS_TEST = "tests/backend/test_admin_user_settings.py"  # 便宜，有基线
_MANAGEMENT_TEST = "tests/backend/test_admin_user_management.py"  # 贵，有基线
_QUOTA_TEST = "tests/backend/test_admin_user_quota.py"  # 已在名单里
_FLAGS_TEST = "tests/backend/test_admin_user_flags.py"  # 带 core 标记
_NEW_TEST = "tests/backend/test_brand_new_feature.py"  # 刚写的、还没提交
_PINNED_TEST = "tests/backend/test_pinned_slow.py"  # 带 core 标记，又贵又慢


def _write_repo_files(repo_root: Path, file_sources: dict[str, str]) -> None:
    """按 ``{相对路径: 源码}`` 在假仓库里写文件，自动创建父目录。"""
    for relative_path, source_text in file_sources.items():
        target_path = repo_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(source_text, encoding="utf-8")


def _test_module_source(test_function_count: int) -> str:
    """生成含指定个数 ``def test_`` 的测试模块源码。"""
    return "".join(
        f"def test_case_{case_index}() -> None:\n    pass\n\n"
        for case_index in range(test_function_count)
    )


def _cost(*, seconds: float, avg: float) -> dict[str, float]:
    """构造一条文件级耗时记录，字段与 ``load_baseline`` 的返回一致。"""
    return {"seconds": seconds, "entries": 10.0, "avg": avg}


def _write_durations_report(repo_root: Path, seconds_by_test_file: dict[str, float]) -> Path:
    """按 ``pytest --durations=0`` 的行格式写一份耗时报告，每个文件只记一条 call 用例。"""
    report_lines = [
        f"{call_seconds:.2f}s call     {test_file}::test_case_0"
        for test_file, call_seconds in seconds_by_test_file.items()
    ]
    durations_report_path = repo_root / "durations.txt"
    durations_report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return durations_report_path


def _script_git_changes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    diff_against_head: Sequence[str] = (),
    staged: Sequence[str] = (),
    untracked: Sequence[str] = (),
) -> None:
    """让 ``_run_git`` 只回放这三类变更，与 ``changed_working_paths`` 发起的命令一一对应。"""
    scripted_outputs = {
        ("diff", "--name-only", "HEAD"): diff_against_head,
        ("diff", "--name-only", "--cached"): staged,
        ("ls-files", "--others", "--exclude-standard"): untracked,
    }

    def _replay_git(*git_arguments: str) -> str:
        return "\n".join(scripted_outputs.get(git_arguments, ()))

    monkeypatch.setattr(tiers, "_run_git", _replay_git)


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把脚本的仓库根、名单与基线路径重定向到 ``tmp_path``，返回假仓库根目录。"""
    repo_root = tmp_path.resolve()
    monkeypatch.setattr(tiers, "PROJECT_ROOT", repo_root)
    monkeypatch.setattr(tiers, "CORE_SET_PATH", repo_root / "tests" / "core_set.txt")
    monkeypatch.setattr(tiers, "BASELINE_PATH", repo_root / ".test_durations.json")
    return repo_root


@pytest.fixture
def admin_router_repo(fake_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """典型场景：改了 admin 路由，同名词根映射到四个测试，另有一个新写的测试。"""
    _write_repo_files(
        fake_repo,
        {
            _ADMIN_ROUTER_SOURCE: "",
            _SETTINGS_TEST: _test_module_source(2),
            _MANAGEMENT_TEST: _test_module_source(3),
            _QUOTA_TEST: _test_module_source(1),
            _FLAGS_TEST: _CORE_MARKED_SOURCE + _test_module_source(1),
            _NEW_TEST: _test_module_source(1),
            "tests/core_set.txt": f"# 生成的名单\n\n{_QUOTA_TEST}\n",
        },
    )
    baseline_costs = {
        _SETTINGS_TEST: _cost(seconds=2.0, avg=0.1),
        _MANAGEMENT_TEST: _cost(seconds=9.0, avg=0.3),
        _QUOTA_TEST: _cost(seconds=1.0, avg=0.1),
    }
    (fake_repo / ".test_durations.json").write_text(
        json.dumps({"files": baseline_costs}), encoding="utf-8"
    )
    _script_git_changes(
        monkeypatch, diff_against_head=[_ADMIN_ROUTER_SOURCE], untracked=[_NEW_TEST]
    )
    return fake_repo


def test_changed_working_paths_unions_git_sources_and_drops_docs_and_deleted_files(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三路 git 采集取并集去重；文档类文件与已不在磁盘上的文件不算代码变更。"""
    _write_repo_files(
        fake_repo,
        {
            "src/backend/api/foo_router.py": "",
            "tests/backend/test_foo.py": "",
            "docs/guide.md": "",
        },
    )
    _script_git_changes(
        monkeypatch,
        diff_against_head=[
            "src/backend/api/foo_router.py",
            "docs/guide.md",
            "src/backend/core/already_deleted.py",
        ],
        staged=["src/backend/api/foo_router.py"],
        untracked=["tests/backend/test_foo.py"],
    )

    assert tiers.changed_working_paths() == [
        "src/backend/api/foo_router.py",
        "tests/backend/test_foo.py",
    ]


def test_changed_test_paths_keeps_only_existing_test_modules(fake_repo: Path) -> None:
    """``conftest.py``、``*_test_support.py`` 没有自己的用例，已删除的测试也不能交给 pytest。"""
    _write_repo_files(
        fake_repo,
        {
            "tests/backend/test_alpha.py": "",
            "tests/conftest.py": "",
            "tests/backend/alpha_test_support.py": "",
            "src/backend/api/alpha_router.py": "",
        },
    )

    changed_tests = tiers.changed_test_paths(
        [
            "tests/backend/test_alpha.py",
            "tests/conftest.py",
            "tests/backend/alpha_test_support.py",
            "tests/backend/test_deleted.py",
            "src/backend/api/alpha_router.py",
        ]
    )

    assert changed_tests == ["tests/backend/test_alpha.py"]


def test_global_impact_change_still_maps_src_changes_to_tests(fake_repo: Path) -> None:
    """ "影响面无法收窄"与"能映射到哪些测试"是两个独立信号，不能互相短路。

    早先版本一命中全局影响面就直接返回空列表，于是任何改到 ``tests/conftest.py`` 的工作
    里，src → 测试的并集都静默失效。
    """
    _write_repo_files(
        fake_repo,
        {
            "tests/conftest.py": "",
            _ADMIN_ROUTER_SOURCE: "",
            _MANAGEMENT_TEST: _test_module_source(1),
            "tests/backend/test_billing_export.py": _test_module_source(1),
        },
    )

    selected_tests, needs_full_run, reasons = tiers.select_changed_tests(
        ["tests/conftest.py", _ADMIN_ROUTER_SOURCE]
    )

    assert selected_tests == [_MANAGEMENT_TEST]
    assert needs_full_run is True
    assert "全局影响面" in reasons[0]


@pytest.mark.parametrize(
    "global_change_path",
    [
        "tests/conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "uv.lock",
        ".pre-commit-config.yaml",
        "src/backend/shared/helpers.py",
        "tests/fixtures/sample.json",
    ],
)
def test_global_impact_paths_ask_for_a_full_run(fake_repo: Path, global_change_path: str) -> None:
    """改到这些文件时本地档只能给局部信号，必须把"需要全量复核"交给调用方。"""
    _selected_tests, needs_full_run, reasons = tiers.select_changed_tests([global_change_path])

    assert needs_full_run is True
    assert any("全局影响面" in reason for reason in reasons)


def test_narrow_src_change_does_not_ask_for_a_full_run(fake_repo: Path) -> None:
    """只改一个路由时影响面可以收窄：映射出相关测试，且不触发全量复核提示。"""
    _write_repo_files(
        fake_repo,
        {_ADMIN_ROUTER_SOURCE: "", _MANAGEMENT_TEST: _test_module_source(1)},
    )

    selected_tests, needs_full_run, reasons = tiers.select_changed_tests([_ADMIN_ROUTER_SOURCE])

    assert selected_tests == [_MANAGEMENT_TEST]
    assert needs_full_run is False
    assert not any("全局影响面" in reason for reason in reasons)


def test_src_change_maps_only_into_the_test_dirs_of_its_layer(fake_repo: Path) -> None:
    """engines 层的改动只往 ``tests/engines`` 与 ``tests/backend`` 映射，不外溢到别的层。"""
    _write_repo_files(
        fake_repo,
        {
            "src/backend/engines/sandbox_engine.py": "",
            "tests/engines/test_sandbox_engine.py": _test_module_source(1),
            "tests/infrastructure/test_sandbox_storage.py": _test_module_source(1),
        },
    )

    selected_tests, _needs_full_run, _reasons = tiers.select_changed_tests(
        ["src/backend/engines/sandbox_engine.py"]
    )

    assert selected_tests == ["tests/engines/test_sandbox_engine.py"]


@pytest.mark.parametrize(
    "changed_source",
    ["src/backend/api/main.py", "src/backend/api/db.py", "src/backend/api/utils.py"],
)
def test_generic_or_short_file_names_do_not_widen_the_mapping(
    fake_repo: Path, changed_source: str
) -> None:
    """停用词（main、utils）与短于 4 个字符的词根（db）不能参与映射，否则会圈进一大片。"""
    _write_repo_files(
        fake_repo,
        {
            changed_source: "",
            "tests/backend/test_main_flow.py": _test_module_source(1),
            "tests/backend/test_db_session.py": _test_module_source(1),
            "tests/backend/test_utils_helpers.py": _test_module_source(1),
        },
    )

    selected_tests, _needs_full_run, _reasons = tiers.select_changed_tests([changed_source])

    assert selected_tests == []


def test_changed_test_module_selects_itself_but_support_files_select_nothing(
    fake_repo: Path,
) -> None:
    """改动的测试模块入选自己；``*_test_support.py`` 交给 pytest 只会收集到 0 条。"""
    _write_repo_files(
        fake_repo,
        {
            "tests/backend/test_alpha_flow.py": _test_module_source(1),
            "tests/backend/alpha_flow_test_support.py": "",
        },
    )

    selected_tests, needs_full_run, _reasons = tiers.select_changed_tests(
        ["tests/backend/test_alpha_flow.py", "tests/backend/alpha_flow_test_support.py"]
    )

    assert selected_tests == ["tests/backend/test_alpha_flow.py"]
    assert needs_full_run is False


def test_non_python_change_selects_nothing(fake_repo: Path) -> None:
    """配置类改动既没有可映射的测试，也不属于全局影响面。"""
    assert tiers.select_changed_tests(["config.toml"]) == ([], False, [])


def test_split_by_budget_fills_budget_cheapest_average_first() -> None:
    """按平均耗时升序累加串行秒数；放不进预算的文件被挪到预算外。"""
    cheap_path = "tests/backend/test_cheap.py"
    middle_path = "tests/backend/test_middle.py"
    pricey_path = "tests/backend/test_pricey.py"
    file_costs = {
        cheap_path: _cost(seconds=3.0, avg=0.1),
        middle_path: _cost(seconds=4.0, avg=0.2),
        pricey_path: _cost(seconds=6.0, avg=0.3),
    }

    # 故意倒序传入：结果只取决于成本，不取决于入参顺序。
    within_budget, over_budget = tiers.split_by_budget(list(reversed(file_costs)), file_costs, 8.0)

    assert within_budget == [cheap_path, middle_path]
    assert over_budget == [pricey_path]


def test_split_by_budget_skips_a_file_that_does_not_fit_and_keeps_trying() -> None:
    """一个文件放不进剩余预算不等于后面的都放不进：更小的仍有机会入选。"""
    first_path = "tests/backend/test_first.py"
    too_big_path = "tests/backend/test_too_big.py"
    small_path = "tests/backend/test_small.py"
    file_costs = {
        first_path: _cost(seconds=6.0, avg=0.1),
        too_big_path: _cost(seconds=5.0, avg=0.2),
        small_path: _cost(seconds=3.0, avg=0.3),
    }

    within_budget, over_budget = tiers.split_by_budget(list(file_costs), file_costs, 10.0)

    assert within_budget == [first_path, small_path]
    assert over_budget == [too_big_path]


def test_split_by_budget_ranks_unmeasured_files_after_all_measured_ones(fake_repo: Path) -> None:
    """没有基线的新文件排在所有有基线的文件之后——宁可让它进 CI，也不赌它便宜。"""
    measured_path = "tests/backend/test_measured_heavy.py"
    unmeasured_path = "tests/backend/test_unmeasured_new.py"
    _write_repo_files(fake_repo, {unmeasured_path: _test_module_source(1)})
    file_costs = {measured_path: _cost(seconds=4.0, avg=2.0)}

    # 没基线的只有 1 条，估算 0.7s，比有基线那个的平均耗时 2.0s 还低：只按成本排序的话它会
    # 插到前面。预算 4.5s 恰好容不下两者之和 4.7s，先排到的那个把另一个挤出去。
    roomy_split = tiers.split_by_budget([unmeasured_path, measured_path], file_costs, 10.0)
    tight_split = tiers.split_by_budget([unmeasured_path, measured_path], file_costs, 4.5)

    assert roomy_split == ([measured_path, unmeasured_path], [])
    assert tight_split == ([measured_path], [unmeasured_path])


def test_split_by_budget_scales_unmeasured_cost_with_test_function_count(fake_repo: Path) -> None:
    """没有基线的文件按测试函数个数估成本：大文件不能被当成只花 0.7s 就放进预算。"""
    small_path = "tests/backend/test_small_new.py"
    large_path = "tests/backend/test_large_new.py"
    _write_repo_files(
        fake_repo,
        {small_path: _test_module_source(2), large_path: _test_module_source(20)},
    )

    within_budget, over_budget = tiers.split_by_budget([large_path, small_path], {}, 10.0)

    assert within_budget == [small_path]
    assert over_budget == [large_path]


def test_unmeasured_estimate_counts_methods_and_async_but_not_commented_out_tests(
    fake_repo: Path,
) -> None:
    """函数、类方法与 async 函数都算一条；被注释掉的 ``def test_`` 不算。"""
    shapes_path = "tests/backend/test_shapes.py"
    _write_repo_files(
        fake_repo,
        {
            shapes_path: (
                "def test_plain() -> None:\n    pass\n\n"
                "async def test_async() -> None:\n    pass\n\n"
                "class TestGroup:\n"
                "    def test_method(self) -> None:\n        pass\n\n"
                "    def helper(self) -> None:\n        pass\n\n"
                "# def test_commented_out() -> None:\n"
            )
        },
    )

    # 正确数法是 3 条 = 2.1s。少数（漏掉方法或 async）会落在 2.0s 以内，多数（把注释里那条
    # 也算上）是 2.8s 而超出 2.5s：两个预算夹住 (2.0, 2.5] 才能同时挡住两个方向的错数。
    under_counting_probe = tiers.split_by_budget([shapes_path], {}, 2.0)
    over_counting_probe = tiers.split_by_budget([shapes_path], {}, 2.5)

    assert under_counting_probe == ([], [shapes_path])
    assert over_counting_probe == ([shapes_path], [])


def test_unmeasured_file_that_cannot_be_read_still_costs_one_test(fake_repo: Path) -> None:
    """读不出源码的文件按 1 条估，而不是 0：缺失的数据不能被当成"免费"。"""
    missing_path = "tests/backend/test_missing.py"

    assert tiers.split_by_budget([missing_path], {}, 0.5) == ([], [missing_path])
    assert tiers.split_by_budget([missing_path], {}, 1.0) == ([missing_path], [])


def test_split_by_budget_with_nothing_to_run() -> None:
    """没有候选文件时两份都为空。"""
    assert tiers.split_by_budget([], {}, 10.0) == ([], [])


def test_plan_charges_budget_only_for_tests_that_would_not_otherwise_run(
    admin_router_repo: Path,
) -> None:
    """已在名单里或带 core 标记的映射项本来就会跑，不占补跑预算，也不能被报成"没跑"。"""
    changed_tests, within_budget, over_budget, needs_full_run = tiers.working_tree_test_plan(10.0)

    assert changed_tests == [_NEW_TEST]
    # 便宜的 settings（2s）进预算；贵的 management（9s）再加 2s 就是 11s，超出 10s 被点名。
    assert within_budget == [_SETTINGS_TEST]
    assert over_budget == [_MANAGEMENT_TEST]
    assert _QUOTA_TEST not in within_budget + over_budget
    assert _FLAGS_TEST not in within_budget + over_budget
    assert needs_full_run is False


def test_plan_with_a_roomy_budget_runs_every_mapped_test(admin_router_repo: Path) -> None:
    """预算够大时没有任何映射项被点名。"""
    _changed_tests, within_budget, over_budget, _needs_full_run = tiers.working_tree_test_plan(
        100.0
    )

    assert within_budget == [_SETTINGS_TEST, _MANAGEMENT_TEST]
    assert over_budget == []


def test_plan_keeps_mapping_when_the_change_also_hits_global_impact(
    admin_router_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改了 conftest 的同时改了路由：映射照旧给出，同时要求全量复核。"""
    _write_repo_files(admin_router_repo, {"tests/conftest.py": ""})
    _script_git_changes(monkeypatch, diff_against_head=[_ADMIN_ROUTER_SOURCE, "tests/conftest.py"])

    _changed_tests, within_budget, _over_budget, needs_full_run = tiers.working_tree_test_plan(10.0)

    assert within_budget == [_SETTINGS_TEST]
    assert needs_full_run is True


def test_plan_never_charges_a_changed_test_against_the_mapped_budget(
    admin_router_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """刚改的测试一定要跑，不设预算：哪怕它很贵，也不进"超预算"清单。"""
    _script_git_changes(monkeypatch, diff_against_head=[_ADMIN_ROUTER_SOURCE, _MANAGEMENT_TEST])

    changed_tests, within_budget, over_budget, _needs_full_run = tiers.working_tree_test_plan(1.0)

    assert changed_tests == [_MANAGEMENT_TEST]
    assert within_budget == []
    assert over_budget == [_SETTINGS_TEST]


@pytest.mark.parametrize(
    ("cli_flags", "expected_header_rules"),
    [
        pytest.param(
            [],
            (
                f"单条平均 <= {tiers.DEFAULT_AVG_CAP_SECONDS}s",
                f"取满 {tiers.DEFAULT_SERIAL_BUDGET_SECONDS}s 串行预算",
            ),
            id="defaults",
        ),
        pytest.param(
            ["--budget", "13.5", "--avg-cap", "0.35"],
            ("单条平均 <= 0.35s", "取满 13.5s 串行预算"),
            id="flags-override-defaults",
        ),
    ],
)
def test_refresh_header_records_the_budget_and_cap_the_run_actually_used(
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_flags: list[str],
    expected_header_rules: tuple[str, str],
) -> None:
    """名单头部写本次刷新真正用的预算与均值上限，而不是模块默认值。

    ``--budget`` / ``--avg-cap`` 只改这一次选择：头部若仍印默认值，评审者读到的"规则"
    与名单的实际生成方式就对不上（``--budget 12`` 生成的名单头部却写着取满 16s）。
    """
    _script_git_changes(monkeypatch)
    cheap_test_path = "tests/backend/test_cheap.py"
    _write_repo_files(fake_repo, {cheap_test_path: _test_module_source(1)})
    durations_report_path = _write_durations_report(fake_repo, {cheap_test_path: 0.10})

    exit_code = tiers.main(
        ["refresh", "--durations-output", str(durations_report_path), *cli_flags]
    )

    core_set_header = tiers.CORE_SET_PATH.read_text(encoding="utf-8")
    assert exit_code == 0
    for expected_rule in expected_header_rules:
        assert expected_rule in core_set_header


def test_pinned_file_is_charged_to_the_budget_before_the_cost_fill(fake_repo: Path) -> None:
    """钉住的文件先占预算，剩下的预算才轮到便宜优先的填充，合计不得越过预算。

    钉住不是免费通道：6s 的钉住文件（平均 0.9s，本该被均值上限挡掉）先占去 10s 里的 6s，
    只剩 4s 给三个各 3s 的便宜文件，所以只放得进一个，总计 9s。若先填充、再把钉住的文件
    追加进来，三个便宜文件先占 9s，总数就是 15s，早已越过预算。
    """
    cheap_test_paths = [f"tests/backend/test_cheap_{suffix}.py" for suffix in "abc"]
    _write_repo_files(
        fake_repo,
        {
            _PINNED_TEST: _CORE_MARKED_SOURCE + _test_module_source(1),
            **{cheap_test_path: _test_module_source(1) for cheap_test_path in cheap_test_paths},
        },
    )
    file_costs = {
        _PINNED_TEST: _cost(seconds=6.0, avg=0.9),
        **{cheap_test_path: _cost(seconds=3.0, avg=0.1) for cheap_test_path in cheap_test_paths},
    }

    selected_files, core_stats = tiers.select_core_files(
        file_costs, serial_budget_seconds=10.0, avg_cap_seconds=0.5
    )

    assert selected_files == sorted([_PINNED_TEST, cheap_test_paths[0]])
    assert core_stats["serial_seconds"] == 9.0
    assert core_stats["pinned_seconds"] == 6.0
    assert core_stats["demoted_over_budget"] == cheap_test_paths[1:]
    # 钉住的文件不是被"降级"的对象，哪怕它的平均耗时本来越过了上限。
    assert core_stats["demoted_over_avg"] == []


def test_pinned_files_may_fill_the_whole_budget_but_nothing_more(fake_repo: Path) -> None:
    """钉住的合计恰好等于预算是允许的，只有超出才算错；此时再没有便宜文件挤得进来。"""
    cheap_test_path = "tests/backend/test_cheap.py"
    _write_repo_files(
        fake_repo,
        {
            _PINNED_TEST: _CORE_MARKED_SOURCE + _test_module_source(1),
            cheap_test_path: _test_module_source(1),
        },
    )
    file_costs = {
        _PINNED_TEST: _cost(seconds=5.0, avg=0.9),
        cheap_test_path: _cost(seconds=0.1, avg=0.1),
    }

    selected_files, core_stats = tiers.select_core_files(
        file_costs, serial_budget_seconds=5.0, avg_cap_seconds=0.5
    )

    assert selected_files == [_PINNED_TEST]
    assert core_stats["demoted_over_budget"] == [cheap_test_path]


@pytest.mark.parametrize(
    "extra_cli_flags",
    [pytest.param([], id="write"), pytest.param(["--dry-run"], id="dry-run")],
)
def test_refresh_aborts_when_pinned_files_alone_exceed_the_budget(
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_cli_flags: list[str],
) -> None:
    """钉住的文件合计就超预算时，刷新当场报错并点名，不写名单也不写基线。

    否则这份超预算的名单会被写进仓库，直到守卫 ``test_core_set_stays_inside_serial_budget``
    在之后的某次提交才变红——那时人已经离开了做出"钉住"这个决定的上下文。预览（dry-run）
    也要报同一个错，不然预览看着没事、真刷新才炸。
    """
    _script_git_changes(monkeypatch)
    _write_repo_files(fake_repo, {_PINNED_TEST: _CORE_MARKED_SOURCE + _test_module_source(1)})
    durations_report_path = _write_durations_report(fake_repo, {_PINNED_TEST: 6.0})

    exit_code = tiers.main(
        [
            "refresh",
            "--durations-output",
            str(durations_report_path),
            "--budget",
            "5",
            *extra_cli_flags,
        ]
    )

    captured_stderr = capsys.readouterr().err
    assert exit_code == 1
    assert _PINNED_TEST in captured_stderr
    assert "6.00s" in captured_stderr
    assert str(durations_report_path) in captured_stderr
    assert not tiers.CORE_SET_PATH.exists()
    assert not tiers.BASELINE_PATH.exists()
