#!/usr/bin/env python3
"""测试分层：维护本地核心档名单、耗时基线与「变更相关用例」选择。

用法：
    uv run python scripts/shared/testing/test_tiers.py list
    uv run python scripts/shared/testing/test_tiers.py refresh --durations-output /tmp/dur.txt
    uv run python scripts/shared/testing/test_tiers.py changed
    uv run python scripts/shared/testing/test_tiers.py report --top 15

设计要点：
- 本地档必须恒定 <30s，而全量档 1300+ 条串行约 700s，靠人手挑名单一定会腐烂，
  所以核心档成员由「实测耗时」自动决定：平均越便宜越先进本地档，直到填满串行预算。
  用例只会随时间变慢，因此这套规则天然就是「旧测试定期退出本地档」的机制——
  退出只是移出本地循环，CI 的全量档仍然跑它，不存在关掉不测。
- `tests/core_set.txt` 是本地档唯一的运行时名单，由 `refresh` 生成、可被 Git 审校；
  `just test` 只读它，不在运行时解析耗时，保证选取结果确定、可复现。
- 显式写了 `pytest.mark.core` 的文件（「钉住」）先进本地档并先占串行预算，用于覆盖
  自动规则漏掉的「很贵但必须每次跑」的主干用例，这是唯一的手工例外：它不受平均耗时
  上限与便宜优先顺序约束，但不是免费通道——钉住的合计超出预算时 `refresh` 直接报错。
- 名单之外，本地档还会并上工作区改动/新增的测试，以及改动 src 映射到的测试（有补跑
  预算，超出的点名而不静默丢弃）。这两项由 `working_tree_test_plan` 一次算出，
  `tests/conftest.py` 直接加载本脚本取结果——「什么算改动」只有这一份实现。
- 耗时基线来自 `pytest --durations=0` 的文本输出，不依赖 pytest 9 已移除的
  `--store-durations`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORE_SET_PATH = PROJECT_ROOT / "tests" / "core_set.txt"
BASELINE_PATH = PROJECT_ROOT / ".test_durations.json"

# 串行秒数预算：墙钟 ≈ 固定开销 + workers×1.1 + 串行秒数/workers。按 -n4 与 7s 固定
# 开销反推，16s 串行约等于 15s pytest 墙钟；但 `just test` 在 pytest 之前还可能跑
# `just lint --full`（实测 8-14s），两段是同一个 30s 预算里的加法，所以这里必须给
# lint 留出余量——22s 实测会让 bench-test 的 warm 场景在 30.7s 上下抖动越线。
DEFAULT_SERIAL_BUDGET_SECONDS = float(os.environ.get("JUST_CORE_BUDGET_SECONDS", "16"))
# 单条用例的平均耗时上限：超过它就认为不值得放进每次提交的本地循环。
DEFAULT_AVG_CAP_SECONDS = float(os.environ.get("JUST_CORE_MAX_AVG_SECONDS", "0.5"))
# 改动 src 后临时补跑那些映射到的测试的串行预算；词根映射宁可多圈，不设上限会把
# 本地档从 16s 拉到 100s+，摧毁 30 秒契约。超出的部分点名提示、由 CI 全量档兜。
DEFAULT_MAPPED_BUDGET_SECONDS = float(os.environ.get("JUST_MAPPED_BUDGET_SECONDS", "10"))
# 没有耗时基线的测试（刷新名单之后才提交的新文件）每条按多少秒估：略高于全套件平均
# （约 0.55s/条），宁可高估也不让一个没人量过的文件悄悄撑破预算。
UNMEASURED_TEST_SECONDS = 0.7

# 与 scripts/shared/hooks/quality_flag.sh 的 QUALITY_TEST_EXCLUDED_FILE_PATTERN 对齐，
# 否则「改了个文档」会被算成代码变更并触发全量选择。
TEST_EXCLUDED_SUFFIX_PATTERN = re.compile(
    r"\.(md|rst|txt|log|png|jpg|jpeg|gif|svg|ico|drawio|pdf|docx|xlsx|zip|tar|gz|bz2|example)$"
)
DURATION_LINE_PATTERN = re.compile(
    r"^(?P<seconds>[0-9]+\.[0-9]+)s\s+(?P<phase>call|setup|teardown)\s+(?P<nodeid>\S+)$"
)
CORE_MARKER_PATTERN = re.compile(r"pytest\.mark\.core\b")
TEST_FUNCTION_PATTERN = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)

# 这些文件一动，理论上全套都可能受影响，本地档无法给出可信的窄集合。
GLOBAL_IMPACT_PATHS = (
    "tests/conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "uv.lock",
    ".pre-commit-config.yaml",
)
GLOBAL_IMPACT_PREFIXES = ("src/backend/shared/", "tests/fixtures/")

STOPWORD_TOKENS = frozenset(
    {
        "test",
        "tests",
        "py",
        "api",
        "app",
        "base",
        "common",
        "data",
        "impl",
        "main",
        "support",
        "utils",
    }
)

# src/backend/<layer> -> 需要跟着跑的测试目录，按四层依赖方向向外扩一层。
LAYER_TEST_DIRS: dict[str, tuple[str, ...]] = {
    "api": ("tests/backend", "tests/composition", "tests/rv-api"),
    "core": ("tests/backend", "tests/composition", "tests/engines"),
    "engines": ("tests/engines", "tests/backend"),
    "infrastructure": ("tests/infrastructure", "tests/backend", "tests/engines"),
    "composition": ("tests/composition", "tests/backend"),
    "scripts": ("tests/guards", "tests/engines"),
}
DEFAULT_TEST_DIRS: tuple[str, ...] = ("tests/backend", "tests/guards")


def _relative_display(path: Path) -> str:
    """把绝对路径转成相对仓库根的展示形式。

    Args:
        path: 待展示的绝对或相对路径。

    Returns:
        相对仓库根的路径字符串。
    """
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _run_git(*arguments: str) -> str:
    """执行 git 命令并返回 stdout，失败时返回空串。

    Args:
        *arguments: 传给 ``git`` 的参数序列。

    Returns:
        命令标准输出；仓库不可用或命令非零退出时为空串。
    """
    completed = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else ""


def parse_durations_output(durations_text: str) -> dict[str, float]:
    """解析 ``pytest --durations=0`` 文本，聚合出每个测试文件的串行耗时。

    Args:
        durations_text: pytest 报告全文，包含 ``X.XXs call nodeid`` 形式的行。

    Returns:
        ``{测试文件相对路径: [串行秒数, 计时条目数]}``。
    """
    file_costs: dict[str, list[float]] = {}
    for raw_line in durations_text.splitlines():
        match = DURATION_LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue
        nodeid = match.group("nodeid")
        file_part = nodeid.split("::", 1)[0]
        if not file_part.endswith(".py"):
            continue
        bucket = file_costs.setdefault(file_part, [0.0, 0])
        bucket[0] += float(match.group("seconds"))
        bucket[1] += 1
    return {file_part: [cost, count] for file_part, (cost, count) in file_costs.items()}


_SKIPPED_TEST_DIRS = frozenset({"node_modules", "__pycache__", "playwright-e2e", ".venv"})


def _existing_test_files() -> list[str]:
    """列出当前磁盘上的测试模块。

    以文件系统为准而不是 ``git ls-files``：前者才反映"现在有什么能跑"。用
    ``git ls-files`` 会同时犯两种错——刚 ``rm`` 还没 staged 的删除仍被列出（旧耗时
    报告里的幽灵文件因此复活进名单），新建还没 ``git add`` 的测试则完全看不到
    （连 ``@pytest.mark.core`` 的例外通道都失效）。

    Returns:
        相对仓库根的 ``test_*.py`` 路径列表；辅助模块（``conftest.py``、
        ``*_test_support.py``）不算测试模块。
    """
    tests_dir = PROJECT_ROOT / "tests"
    discovered: list[str] = []
    for module_path in sorted(tests_dir.rglob("test_*.py")):
        if _SKIPPED_TEST_DIRS & set(module_path.parent.parts):
            continue
        discovered.append(_relative_display(module_path))
    return discovered


def _read_test_source(relative_path: str) -> str:
    """读取测试文件源码；文件缺失或无法按 UTF-8 解码时返回空串。

    Args:
        relative_path: 相对仓库根的测试文件路径。

    Returns:
        文件全文；读不出来时为空串，调用方按"文件里没有任何声明"处理。
    """
    try:
        return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _has_explicit_core_marker(relative_path: str) -> bool:
    """判断测试文件是否显式声明了 ``core`` 标记。

    Args:
        relative_path: 相对仓库根的测试文件路径。

    Returns:
        文件源码中出现 ``pytest.mark.core`` 时为 True。
    """
    return bool(CORE_MARKER_PATTERN.search(_read_test_source(relative_path)))


def build_file_costs(durations_text: str) -> dict[str, dict[str, float]]:
    """把解析结果整理成带平均耗时的文件级成本表。

    Args:
        durations_text: ``pytest --durations=0`` 的报告全文。

    Returns:
        ``{文件路径: {"seconds": 串行秒数, "entries": 计时条目数, "avg": 平均秒数}}``。
    """
    parsed = parse_durations_output(durations_text)
    file_costs: dict[str, dict[str, float]] = {}
    for file_part, (cost, count) in parsed.items():
        entries = max(count, 1)
        file_costs[file_part] = {
            "seconds": round(cost, 3),
            "entries": float(entries),
            "avg": round(cost / entries, 4),
        }
    return file_costs


class PinnedOverBudgetError(ValueError):
    """被 ``pytest.mark.core`` 钉住的文件自己就超出了串行预算，选不出合法名单。"""


def select_core_files(
    file_costs: dict[str, dict[str, float]],
    *,
    serial_budget_seconds: float,
    avg_cap_seconds: float,
    unmeasured_files: list[str] | None = None,
) -> tuple[list[str], dict[str, object]]:
    """先让钉住的文件占预算，再按「平均耗时升序 + 串行预算截断」填满本地核心档。

    显式写了 ``pytest.mark.core`` 的文件（钉住）先入选、先占预算：不受平均耗时上限和
    便宜优先顺序约束，但和其余文件共用同一份串行预算。钉住不是免费通道——它让主干用例
    先吃掉预算，剩下的才轮到自动填充；合计超出预算就拒绝出名单，而不是让一份超预算的
    名单被写进仓库、等守卫事后才变红。

    Args:
        file_costs: 文件级成本表，来自 ``build_file_costs``。
        serial_budget_seconds: 核心档允许的串行秒数上限，钉住的文件也计入。
        avg_cap_seconds: 单条用例平均耗时上限，超过即不入选（钉住的文件除外）。
        unmeasured_files: 没有计时数据的测试文件。pytest 的 ``--durations-min``
            默认只记录 >=5ms 的用例，所以缺数据等价于"快到不值得记录"，按零成本入选。
            纯由 slow 等 marker 排除的文件也会落在这里，运行时仍会被 addopts 过滤掉。

    Returns:
        ``(入选文件列表, 统计信息)``；统计信息含本次实际用的预算与均值上限、钉住的文件
        个数与耗时，以及被降级原因，便于人审。钉住的文件不会出现在降级清单里。

    Raises:
        PinnedOverBudgetError: 钉住的文件合计已超出 ``serial_budget_seconds``。
    """
    unmeasured_file_set = set(unmeasured_files or [])
    pinned_file_set = {
        file_part
        for file_part in file_costs.keys() | unmeasured_file_set
        if _has_explicit_core_marker(file_part)
    }
    pinned_seconds_by_file = {
        file_part: file_costs[file_part]["seconds"]
        for file_part in pinned_file_set
        if file_part in file_costs
    }
    pinned_seconds = sum(pinned_seconds_by_file.values())
    if pinned_seconds > serial_budget_seconds:
        pinned_listing = "\n".join(
            f"  {pinned_file_seconds:.2f}s  {file_part}"
            for file_part, pinned_file_seconds in sorted(
                pinned_seconds_by_file.items(), key=lambda entry: (-entry[1], entry[0])
            )
        )
        raise PinnedOverBudgetError(
            f"钉住（pytest.mark.core）的文件合计 {pinned_seconds:.2f}s，"
            f"已超出 {serial_budget_seconds}s 串行预算：\n{pinned_listing}"
        )

    selected = unmeasured_file_set | pinned_file_set
    spent = pinned_seconds
    over_budget: list[str] = []
    over_avg: list[str] = []
    measured_cheapest_first = sorted(
        file_costs.items(), key=lambda entry: (entry[1]["avg"], entry[0])
    )
    for file_part, cost in measured_cheapest_first:
        if file_part in pinned_file_set:
            continue
        if cost["avg"] > avg_cap_seconds:
            over_avg.append(file_part)
            continue
        if spent + cost["seconds"] > serial_budget_seconds:
            over_budget.append(file_part)
            continue
        spent += cost["seconds"]
        selected.add(file_part)

    stats: dict[str, object] = {
        "serial_budget_seconds": serial_budget_seconds,
        "avg_cap_seconds": avg_cap_seconds,
        "serial_seconds": round(spent, 2),
        "selected_count": len(selected),
        "unmeasured_count": len(unmeasured_file_set & selected),
        "pinned_count": len(pinned_file_set),
        "pinned_seconds": round(pinned_seconds, 2),
        "demoted_over_avg": sorted(over_avg),
        "demoted_over_budget": sorted(over_budget),
    }
    return sorted(selected), stats


def write_core_set(files: list[str], stats: dict[str, object]) -> None:
    """把核心档名单写入 ``tests/core_set.txt``。

    Args:
        files: 入选的测试文件路径列表。
        stats: ``select_core_files`` 返回的统计信息（含本次实际用的预算与均值上限），
            写进文件头供审校。
    """
    header = "\n".join(
        [
            "# 本地档（`just test`）名单：由 `just test-core-refresh` 依据实测耗时生成。",
            (
                f"# 规则：单条平均 <= {stats['avg_cap_seconds']}s 的文件按便宜优先取满 "
                f"{stats['serial_budget_seconds']}s 串行预算；"
            ),
            (
                f"# 显式写了 pytest.mark.core 的文件（本次 {stats['pinned_count']} 个，"
                f"占 {stats['pinned_seconds']}s）先入选并先占这份预算，"
            ),
            "# 不看均值上限，但合计不能超出预算（超出时刷新报错，不写盘）。",
            "# 不要手工删改后忘记同步——CI 的全量档始终覆盖 tests/，这里只决定本地循环跑什么。",
            "# 本文件只是名单：改动过的测试与打了 pytest.mark.core 的模块由 conftest",
            "# 直接并进本地档，所以新写的用例不需要先跑 refresh 也会被执行。",
            f"# 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"# 串行耗时：{stats['serial_seconds']}s / 文件数：{stats['selected_count']}"
            f"（含 {stats['unmeasured_count']} 个快到没有计时记录的文件）",
            "# 因平均耗时过高退出本地档：",
            *(f"#   {item}" for item in (stats["demoted_over_avg"] or ["(无)"])),
            "# 因超出串行预算退出本地档：",
            *(f"#   {item}" for item in (stats["demoted_over_budget"] or ["(无)"])),
        ]
    )
    body = "\n".join(files)
    CORE_SET_PATH.write_text(f"{header}\n\n{body}\n", encoding="utf-8")


def load_core_set() -> list[str]:
    """读取本地核心档名单。

    Returns:
        测试文件路径列表；名单文件缺失时返回空列表。
    """
    if not CORE_SET_PATH.is_file():
        return []
    files: list[str] = []
    for line in CORE_SET_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        files.append(stripped)
    return files


def write_baseline(file_costs: dict[str, dict[str, float]]) -> None:
    """落盘耗时基线，供 ``changed`` 预估代价与 ``report`` 对比趋势。

    Args:
        file_costs: 文件级成本表。
    """
    head_hash = _run_git("rev-parse", "HEAD").strip()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "head": head_hash,
        "files": file_costs,
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_baseline() -> dict[str, dict[str, float]]:
    """读取耗时基线文件。

    Returns:
        文件级成本表；基线缺失或损坏时返回空字典。
    """
    if not BASELINE_PATH.is_file():
        return {}
    try:
        payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = payload.get("files", {}) if isinstance(payload, dict) else {}
    return {
        str(file_part): {
            "seconds": float(cost.get("seconds", 0.0)),
            "entries": float(cost.get("entries", 1.0)),
            "avg": float(cost.get("avg", 0.0)),
        }
        for file_part, cost in files.items()
        if isinstance(cost, dict)
    }


def _tokenize(path_stem: str) -> set[str]:
    """把文件名拆成用于匹配的语义词。

    Args:
        path_stem: 不含目录与扩展名的文件名。

    Returns:
        小写、长度 >=4 且非停用词的语义词集合。
    """
    pieces = re.split(r"[_\-.]+", path_stem)
    words: set[str] = set()
    for piece in pieces:
        for word in re.findall(r"[A-Za-z]+|[0-9]+", piece):
            lowered = word.lower()
            if len(lowered) >= 4 and lowered not in STOPWORD_TOKENS:
                words.add(lowered)
    return words


def changed_working_paths() -> list[str]:
    """收集当前工作区里所有算作代码变更的文件。

    本函数是"什么算改动"的唯一实现：``tests/conftest.py`` 的本地档并集、
    ``just test-changed`` 的映射、以及提交门禁的排除口径都必须走同一份判断，否则
    三处对"改了什么"的理解会各自漂移——而仓库的重复检测 hook 也会把复制的那份挑出来。

    Returns:
        相对仓库根的路径列表，已剔除纯文档/图片类文件与不存在的文件。
    """
    raw_paths = set()
    for diff_args in (
        ("diff", "--name-only", "HEAD"),
        ("diff", "--name-only", "--cached"),
    ):
        raw_paths.update(filter(None, _run_git(*diff_args).splitlines()))
    raw_paths.update(
        filter(None, _run_git("ls-files", "--others", "--exclude-standard").splitlines())
    )
    change_paths: list[str] = []
    for candidate in sorted(raw_paths):
        if TEST_EXCLUDED_SUFFIX_PATTERN.search(candidate):
            continue
        if not (PROJECT_ROOT / candidate).is_file():
            continue
        change_paths.append(candidate)
    return change_paths


def changed_test_paths(change_paths: list[str] | None = None) -> list[str]:
    """挑出工作区里改动或新增的测试模块。

    Args:
        change_paths: 变更路径列表，默认现场采集。

    Returns:
        存在磁盘上的 ``tests/**/test_*.py`` 相对路径列表；``conftest.py`` 与
        ``*_test_support.py`` 没有自己的用例，不算测试模块。
    """
    source_paths = changed_working_paths() if change_paths is None else change_paths
    existing_test_files = set(_existing_test_files())
    return sorted(
        change_path
        for change_path in source_paths
        if change_path.startswith("tests/")
        and change_path in existing_test_files
        and Path(change_path).name.startswith("test_")
    )


def _is_global_impact(change_path: str) -> bool:
    """判断单个变更路径是否属于「影响面无法收窄」的类别。

    Args:
        change_path: 相对仓库根的变更路径。

    Returns:
        命中全局影响清单时为 True。
    """
    return change_path in GLOBAL_IMPACT_PATHS or change_path.startswith(GLOBAL_IMPACT_PREFIXES)


def select_changed_tests(change_paths: list[str]) -> tuple[list[str], bool, list[str]]:
    """由变更路径推导出应当补跑的测试路径。

    Args:
        change_paths: 相对仓库根的变更文件列表。

    Returns:
        ``(测试路径列表, 影响面是否无法收窄, 命中的原因说明列表)``。测试路径为空表示
        没有可映射的测试，此时调用方应回退到核心档。

        "影响面无法收窄"（改了 conftest / pytest.ini / shared / 依赖锁）与"能不能映射"
        是两件事，不能互相短路：早先版本在这里直接返回空列表，结果任何改到
        ``tests/conftest.py`` 的工作里 src→测试的并集都静默失效，本地档连最该跑的测试
        都不跑了。现在两者并存——照旧给出能映射到的测试，同时把"需要全量复核"这个信号
        交给调用方。
    """
    changed_test_files = set(changed_test_paths(change_paths))
    test_files = _existing_test_files()
    selected: set[str] = set()
    reasons: list[str] = []
    needs_full_run = any(_is_global_impact(change_path) for change_path in change_paths)
    if needs_full_run:
        reasons.append(
            "变更命中全局影响面（conftest / pytest.ini / shared / 依赖锁），本地档需配全量复核"
        )
    for change_path in change_paths:
        if change_path.startswith("tests/"):
            # tests/ 下的 conftest.py 与 *_test_support.py 没有自己的用例，交给 pytest
            # 只会收集到 0 条；它们的影响面由全局影响清单另行处理。
            if change_path in changed_test_files:
                selected.add(change_path)
                reasons.append(f"{change_path} 本身是测试")
            continue
        if not change_path.endswith(".py"):
            continue
        layer = change_path.split("/")[2] if change_path.startswith("src/backend/") else ""
        test_dirs = LAYER_TEST_DIRS.get(layer, DEFAULT_TEST_DIRS)
        for test_dir in test_dirs:
            if not any(test_file.startswith(test_dir + "/") for test_file in test_files):
                continue
            selected.update(
                test_file
                for test_file in test_files
                if test_file.startswith(test_dir + "/")
                and _tokenize(Path(test_file).stem) & _tokenize(Path(change_path).stem)
            )
        reasons.append(f"{change_path} -> {'/'.join(test_dirs)} 同名词根")

    return sorted(selected), needs_full_run, reasons


def split_by_budget(
    paths: list[str],
    file_costs: dict[str, dict[str, float]],
    budget_seconds: float,
) -> tuple[list[str], list[str]]:
    """按"便宜优先"把一组测试切成预算内与预算外两份。

    Args:
        paths: 候选测试路径。
        file_costs: 文件级耗时表，来自 ``load_baseline``。
        budget_seconds: 允许的串行秒数上限。

    Returns:
        ``(预算内路径, 预算外路径)``。有基线的文件按平均耗时升序；没有基线的文件
        （刷新名单之后才提交的新文件）排在**所有**有基线的文件之后，成本按静态数出的
        ``def test_`` 个数（至少 1）× ``UNMEASURED_TEST_SECONDS`` 估算——参数化会让真实
        条数更多，所以这是下限，但仍比"整个文件算 0.7s"诚实。宁可让它进 CI，也不赌它便宜。
        一个文件放不进剩余预算时只把它挪到预算外，后面更便宜的仍有机会入选。
    """
    # 元组布局：(是否无基线, 排序用秒数, 串行总秒数, 路径)，直接按元组排序即得上述顺序。
    costed_paths: list[tuple[int, float, float, str]] = []
    for path in paths:
        measured_cost = file_costs.get(path)
        if measured_cost is not None:
            costed_paths.append(
                (0, float(measured_cost["avg"]), float(measured_cost["seconds"]), path)
            )
            continue
        test_function_count = max(len(TEST_FUNCTION_PATTERN.findall(_read_test_source(path))), 1)
        estimated_seconds = test_function_count * UNMEASURED_TEST_SECONDS
        costed_paths.append((1, estimated_seconds, estimated_seconds, path))

    within_budget: list[str] = []
    over_budget: list[str] = []
    spent = 0.0
    for _is_unmeasured, _order_seconds, serial_seconds, path in sorted(costed_paths):
        if spent + serial_seconds > budget_seconds:
            over_budget.append(path)
            continue
        spent += serial_seconds
        within_budget.append(path)
    return within_budget, over_budget


def working_tree_test_plan(
    mapped_budget_seconds: float = DEFAULT_MAPPED_BUDGET_SECONDS,
) -> tuple[list[str], list[str], list[str], bool]:
    """给 ``tests/conftest.py`` 用的单一入口：本地档这一次应当覆盖哪些测试。

    把"改了 src 就把覆盖它的测试临时放开"做成机制，而不是写进提示词指望 agent 记得：
    最该验证的那批用例往往正是被耗时规则请出本地档的贵文件，回归最容易漏的就是这里。
    三个来源一次 git 采集算完，避免 conftest 与脚本各写一份"什么算改动"的判断。

    src 映射要过预算：词根映射刻意宁可多圈，实测改一个 admin 路由会圈进 14 个文件、
    本地档从 16s 涨到 102s，直接摧毁 30 秒契约。所以映射项按便宜优先取满预算，
    剩下的照跑与否交给 CI 全量档，并在汇总里点名，不静默丢弃。

    预算只记"补跑"：名单里已有、打了 ``core`` 标记、或本身就是改动的测试，不管有没有
    这次映射本来就会跑，不能占预算，更不能被报成"这次没跑"。

    Args:
        mapped_budget_seconds: src 映射测试的补跑预算（串行秒数）。

    Returns:
        ``(改动或新增的测试, 预算内的 src 映射测试, 超预算的 src 映射测试,
        影响面是否无法收窄)``。改动/新增的测试不设预算：刚改的那条一定要跑。
    """
    change_paths = changed_working_paths()
    changed_tests = changed_test_paths(change_paths)
    all_selected, prefer_full, _reasons = select_changed_tests(change_paths)
    already_running = set(changed_tests) | set(load_core_set())
    additional_tests = [
        path
        for path in all_selected
        if path not in already_running and not _has_explicit_core_marker(path)
    ]
    within_budget, over_budget = split_by_budget(
        additional_tests, load_baseline(), mapped_budget_seconds
    )
    return changed_tests, within_budget, over_budget, prefer_full


def estimate_serial_seconds(file_costs: dict[str, dict[str, float]], paths: list[str]) -> float:
    """按基线估算一组测试路径的串行耗时。

    Args:
        file_costs: 文件级成本表。
        paths: 待估算的测试路径（文件或 nodeid）。

    Returns:
        估算的串行秒数；基线未覆盖的条目按每条 ``UNMEASURED_TEST_SECONDS`` 计入。
    """
    total = 0.0
    for test_path in paths:
        file_part = test_path.split("::", 1)[0]
        cost = file_costs.get(file_part)
        if cost is None:
            total += UNMEASURED_TEST_SECONDS
        else:
            total += cost["seconds"] if "::" not in test_path else cost["avg"]
    return round(total, 2)


def _command_list(paths: list[str]) -> str:
    """把路径列表拼成可复制的 shell 参数。

    Args:
        paths: 测试路径列表。

    Returns:
        空格分隔的路径字符串。
    """
    return " ".join(paths)


def _handle_list(_args: argparse.Namespace) -> int:
    """处理 ``list``：打印本地核心档名单。

    Args:
        _args: argparse 解析结果，未使用。

    Returns:
        进程退出码。
    """
    files = load_core_set()
    if not files:
        print(
            "core set 为空或不存在，请先运行 `just test-core-refresh`。",
            file=sys.stderr,
        )
        return 1
    print("\n".join(files))
    return 0


def _handle_refresh(args: argparse.Namespace) -> int:
    """处理 ``refresh``：由耗时报告重算核心档名单与基线。

    Args:
        args: 需含 ``durations_output``，可选 ``dry_run``。

    Returns:
        进程退出码。
    """
    durations_path = Path(args.durations_output)
    if not durations_path.is_absolute():
        durations_path = PROJECT_ROOT / durations_path
    if not durations_path.is_file():
        print(f"耗时报告不存在：{durations_path}", file=sys.stderr)
        return 2
    file_costs = build_file_costs(durations_path.read_text(encoding="utf-8"))
    if not file_costs:
        print("耗时报告里没解析出任何计时行，检查 --durations=0 是否生效。", file=sys.stderr)
        return 2
    # 耗时报告可能是几小时前采的：先按磁盘现状剪掉已经不存在的文件，否则用旧报告
    # refresh 会把删掉的测试复活进名单，而 test_core_set_files_exist_on_disk 只能
    # 事后报错，不能阻止名单里出现幽灵条目。
    existing_files = set(_existing_test_files())
    ghost_files = sorted(set(file_costs) - existing_files)
    file_costs = {name: cost for name, cost in file_costs.items() if name in existing_files}
    if ghost_files:
        print(f"忽略报告里已不存在的 {len(ghost_files)} 个文件：{', '.join(ghost_files)}")
    unmeasured_files = sorted(existing_files - set(file_costs))
    try:
        files, stats = select_core_files(
            file_costs,
            serial_budget_seconds=args.budget,
            avg_cap_seconds=args.avg_cap,
            unmeasured_files=unmeasured_files,
        )
    except PinnedOverBudgetError as pinned_over_budget_error:
        print(
            f"{pinned_over_budget_error}\n"
            "刷新没有写盘。撤掉不必每次都钉的 pytest.mark.core 后，可复用这份耗时报告重算：\n"
            "  uv run python scripts/shared/testing/test_tiers.py refresh "
            f"--durations-output {durations_path}",
            file=sys.stderr,
        )
        return 1
    previous = set(load_core_set())
    print(
        f"核心档：{stats['selected_count']} 个文件 / 串行 {stats['serial_seconds']}s "
        f"（显式 core 标记 {stats['pinned_count']} 个占 {stats['pinned_seconds']}s，"
        f"零计时文件 {stats['unmeasured_count']} 个）"
    )
    print(f"新入选：{_command_list(sorted(set(files) - previous)) or '(无)'}")
    print(f"退出本地档：{_command_list(sorted(previous - set(files))) or '(无)'}")
    if args.dry_run:
        print("(dry-run，未写盘)")
        return 0
    write_core_set(files, stats)
    write_baseline(file_costs)
    print(f"已写入 {_relative_display(CORE_SET_PATH)} 与 {_relative_display(BASELINE_PATH)}")
    return 0


def _handle_changed(args: argparse.Namespace) -> int:
    """处理 ``changed``：打印与当前工作区变更相关的测试路径。

    Args:
        args: 需含 ``budget``。

    Returns:
        进程退出码。2 表示建议改跑全量档。
    """
    change_paths = changed_working_paths()
    if not change_paths:
        files = load_core_set()
        print(_command_list(files))
        print(f"工作区无代码变更，回退核心档（{len(files)} 个文件）", file=sys.stderr)
        return 0
    paths, prefer_full, reasons = select_changed_tests(change_paths)
    for reason in reasons[:12]:
        print(f"  · {reason}", file=sys.stderr)
    if prefer_full and args.fallback_core:
        merged_paths = sorted(set(load_core_set()) | set(paths))
        print(_command_list(merged_paths))
        print(
            f"影响面无法收窄：已给出核心档 + 可映射到的测试（{len(merged_paths)} 个），"
            "收尾请跑 `just test all`。",
            file=sys.stderr,
        )
        return 0
    if not paths:
        print(_command_list(load_core_set()))
        print("没有映射到相关测试：已回退核心档。", file=sys.stderr)
        return 0
    estimate = estimate_serial_seconds(load_baseline(), paths)
    print(_command_list(paths))
    print(f"选中 {len(paths)} 个测试，基线估算串行 {estimate}s", file=sys.stderr)
    return 2 if prefer_full else 0


def _handle_report(args: argparse.Namespace) -> int:
    """处理 ``report``：打印最慢文件与本地档成员，辅助淘汰决策。

    Args:
        args: 需含 ``top``。

    Returns:
        进程退出码。
    """
    file_costs = load_baseline()
    if not file_costs:
        print("基线不存在，先运行 `just test-core-refresh`。", file=sys.stderr)
        return 1
    core = set(load_core_set())
    ranked = sorted(file_costs.items(), key=lambda kv: kv[1]["seconds"], reverse=True)
    print(f"{'串行秒数':>8} {'条目':>5} {'平均':>7}  {'本地档':<6} 文件")
    for file_part, cost in ranked[: args.top]:
        flag = "core" if file_part in core else "-"
        cost_display = f"{cost['seconds']:>8.1f} {cost['entries']:>5.0f} {cost['avg']:>7.2f}"
        print(f"{cost_display}  {flag:<6} {file_part}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。

    Returns:
        配置好子命令的 ``ArgumentParser``。
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="打印本地核心档名单")
    list_parser.set_defaults(handler=_handle_list)

    refresh_parser = subparsers.add_parser("refresh", help="按耗时报告重算核心档")
    refresh_parser.add_argument(
        "--durations-output", required=True, help="pytest --durations=0 输出文件"
    )
    refresh_parser.add_argument("--budget", type=float, default=DEFAULT_SERIAL_BUDGET_SECONDS)
    refresh_parser.add_argument("--avg-cap", type=float, default=DEFAULT_AVG_CAP_SECONDS)
    refresh_parser.add_argument("--dry-run", action="store_true")
    refresh_parser.set_defaults(handler=_handle_refresh)

    changed_parser = subparsers.add_parser("changed", help="打印与当前变更相关的测试路径")
    changed_parser.add_argument("--budget", type=float, default=DEFAULT_SERIAL_BUDGET_SECONDS)
    changed_parser.add_argument(
        "--fallback-core",
        action="store_true",
        help="影响面无法收窄时回退核心档而非退出",
    )
    changed_parser.set_defaults(handler=_handle_changed)

    report_parser = subparsers.add_parser("report", help="打印最慢文件与本地档成员")
    report_parser.add_argument("--top", type=int, default=15)
    report_parser.set_defaults(handler=_handle_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    """入口函数。

    Args:
        argv: 命令行参数，默认取 ``sys.argv[1:]``。

    Returns:
        子命令处理函数返回的退出码。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
