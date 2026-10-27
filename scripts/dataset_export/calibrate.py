"""校准候选题的判分器：验证"用原始修复带来的测试判分"这件事本身是对的，不调用任何模型。

一道评测题能成立，要求判分器本身可靠。这里借用软件工程评测里常见的"失败转通过"检验：

- **gold（应用了原始修复）**：改动后的代码 + 改动后的测试，测试应该通过；
- **noop（什么都不改）**：改动前的代码 + 改动后的测试，应该有测试失败。noop 下失败、gold 下通过的测试叫
  ``fail_to_pass``（做对了才会变绿）；两边都通过的叫 ``pass_to_pass``（不能被改坏）；
- **黑盒的 fail_to_pass**：noop 下失败的**原因**必须是断言（行为不对），而不是"找不到这次新增的内部名字"
  （``ImportError``、``AttributeError``…）。后者说明测试和实现细节耦合，任务描述又不能写出那些名字，
  用它判分对做题的人不公平。只有黑盒的 fail_to_pass 才算一把合格的判分器。

结论分级见 ``STATUS_*``。校准只记录**运行事实**（测试怎么过、怎么挂），不是人的判断，所以单独放在数据集仓库的
``calibration/`` 里，不混进补全层。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from scripts.dataset_export.archive import safe_name

CALIBRATION_SCHEMA_VERSION = 1
DEFAULT_TEST_COMMAND = ("uv", "run", "python", "-m", "pytest")
DEFAULT_TIMEOUT_SECONDS = 900
MAX_MESSAGE_CHARS = 200
MAX_DETAIL_ENTRIES = 40
STATUS_CALIBRATED = "calibrated"
STATUS_NO_TESTS = "no_tests"
STATUS_GOLD_FAILED = "gold_failed"
STATUS_NO_FAIL_TO_PASS = "no_fail_to_pass"
STATUS_COUPLED = "coupled_to_new_symbols"
STATUS_ERROR = "error"
FAILURE_MISSING_SYMBOL = "missing_symbol"
FAILURE_BEHAVIORAL = "behavioral"
OUTCOME_PASSED = "passed"
OUTCOME_FAILED = "failed"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED = "skipped"
# noop 下因为找不到这次新增的内部名字而失败的典型信息。
_MISSING_SYMBOL_PATTERN = re.compile(
    r"ImportError|ModuleNotFoundError|cannot import name|has no attribute|NameError|"
    r"unexpected keyword argument|got an unexpected|takes \d+ positional argument|"
    r"fixture '[^']+' not found"
)
TEST_FILE_PATTERN = re.compile(r"(^|/)test_[^/]*\.py$")
TESTS_DIR_PATTERN = re.compile(r"(^|/)tests?/")


@dataclass(frozen=True)
class TestOutcome:
    """一个测试在某次运行里的结果。"""

    # 不是 pytest 用例；避免被 pytest 当成测试类收集。
    __test__ = False

    outcome: str
    failure_kind: str | None = None
    # 失败信息的第一行（截断），给人复核"为什么说它是行为失败 / 找不到名字"用。
    message: str | None = None


class UnrunnableBeforeChangeError(Exception):
    """改动前的代码上，连测试都加载不起来（例如 conftest 要导入这次新增的名字）。"""


@dataclass
class CalibrationPlan:
    """从两个提交的差异里算出的校准计划。"""

    test_files: list[str] = field(default_factory=list)
    # (状态, 路径)：改动前后 tests/ 下发生变化的全部文件，状态为 A 新增 / M 修改 / D 删除。
    support_changes: list[tuple[str, str]] = field(default_factory=list)


def plan_calibration(*, repo_path: Path, base_sha: str, final_sha: str) -> CalibrationPlan:
    """找出需要放到改动前代码上的测试文件，以及 tests/ 下需要同步的全部文件。"""
    listing = _git(repo_path, "diff", "--name-status", "-M", base_sha, final_sha).decode("utf-8")
    plan = CalibrationPlan()
    for line in listing.splitlines():
        columns = line.split("\t")
        status = columns[0][0]
        paths = columns[1:]
        # 重命名：旧路径视为删除，新路径视为新增。
        changes = [("D", paths[0]), ("A", paths[1])] if status == "R" else [(status, paths[0])]
        for change_status, path in changes:
            if TESTS_DIR_PATTERN.search(path) and change_status in ("A", "M", "D"):
                plan.support_changes.append((change_status, path))
                if change_status != "D" and TEST_FILE_PATTERN.search(path) and path.endswith(".py"):
                    plan.test_files.append(path)
    return plan


def _git(repo_path: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo_path), *args], capture_output=True, check=True
    )
    return completed.stdout


def materialize_tree(repo_path: Path, sha: str, destination: Path) -> None:
    """把某个提交的文件树导出到目录里，不碰源仓库的任何状态（不建 worktree、不改引用）。"""
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(
        ["git", "-C", str(repo_path), "archive", sha], stdout=subprocess.PIPE
    )
    subprocess.run(["tar", "-x", "-C", str(destination)], stdin=archive.stdout, check=True)
    if archive.stdout is not None:
        archive.stdout.close()
    if archive.wait() != 0:
        raise RuntimeError(f"git archive {sha} failed")


def overlay_tests(
    repo_path: Path, final_sha: str, destination: Path, support_changes: list[tuple[str, str]]
) -> None:
    """把改动后的 tests/ 变更（含辅助文件）覆盖到改动前的树上；被删除的文件也删掉。"""
    for status, path in support_changes:
        target = destination / path
        if status == "D":
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git(repo_path, "show", f"{final_sha}:{path}"))


def _headline(text: str) -> str:
    """从失败信息里挑最有信息量的一行：优先 pytest 用 ``E `` 标出的错误行，否则取第一个非空行。

    收集阶段的失败，junit 的 ``message`` 只是 ``collection failure``，真正的原因（``ImportError: …``）在正文里。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("E "):
            return line[1:].strip()
    return lines[0] if lines else ""


def parse_junit(xml_path: Path) -> dict[str, TestOutcome]:
    """解析 pytest 的 junit xml：测试标识 -> 结果；收集阶段的错误以文件为键记录。"""
    outcomes: dict[str, TestOutcome] = {}
    for case in ElementTree.parse(xml_path).getroot().iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        identifier = f"{classname}::{name}" if classname else name
        problem = case.find("failure")
        kind_tag = "failed"
        if problem is None:
            problem = case.find("error")
            kind_tag = "error"
        if problem is not None:
            text = f"{problem.get('message') or ''}\n{problem.text or ''}"
            first_line = _headline(text)
            kind = (
                FAILURE_MISSING_SYMBOL
                if _MISSING_SYMBOL_PATTERN.search(text)
                else FAILURE_BEHAVIORAL
            )
            outcomes[identifier] = TestOutcome(
                OUTCOME_FAILED if kind_tag == "failed" else OUTCOME_ERROR,
                kind,
                first_line[:MAX_MESSAGE_CHARS],
            )
        elif case.find("skipped") is not None:
            outcomes[identifier] = TestOutcome(OUTCOME_SKIPPED)
        else:
            outcomes[identifier] = TestOutcome(OUTCOME_PASSED)
    return outcomes


def run_pytest(
    *,
    tree: Path,
    test_files: Sequence[str],
    command: Sequence[str],
    timeout_seconds: int,
    home_dir: Path,
) -> dict[str, TestOutcome]:
    """在给定的树里跑测试文件，返回逐个测试的结果。

    ``HOME`` 指到临时目录：很早的提交里的测试可能写 ``~/.kedacode`` 之类的真实本机状态，不能让校准弄脏它；
    uv 的缓存与 Python 安装位置仍指向真实位置，避免重复下载。
    """
    xml_path = tree / ".calibration-junit.xml"
    real_home = Path.home()
    environment = {
        **os.environ,
        "HOME": str(home_dir),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(real_home / ".cache" / "uv")),
        "UV_PYTHON_INSTALL_DIR": os.environ.get(
            "UV_PYTHON_INSTALL_DIR", str(real_home / ".local" / "share" / "uv" / "python")
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    completed = subprocess.run(
        [
            *command,
            *test_files,
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            # 改动前有的测试文件会因为找不到新增的名字而无法导入；不能让它中止整次运行，
            # 否则同一次改动里其他文件的行为测试也跑不出来。
            "--continue-on-collection-errors",
            f"--junitxml={xml_path}",
        ],
        cwd=tree,
        env=environment,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if not xml_path.is_file():
        output = (completed.stdout or b"").decode("utf-8", "replace") + (
            completed.stderr or b""
        ).decode("utf-8", "replace")
        if _MISSING_SYMBOL_PATTERN.search(output) and "conftest" in output:
            # conftest 在加载阶段就导入失败：pytest 没有跑任何用例，也就不会出报告。
            raise UnrunnableBeforeChangeError(
                "tests/conftest.py 在改动前的代码上就无法加载（它依赖这次改动新增的名字）"
            )
        raise RuntimeError(
            "pytest produced no junit report (crashed or timed out before reporting)"
        )
    return parse_junit(xml_path)


def _file_of(identifier: str) -> str:
    """测试标识里的模块路径对应的文件（``tests.test_x::test_y`` -> ``tests/test_x.py``，类名部分忽略）。"""
    module = identifier.split("::", 1)[0]
    parts = [part for part in module.split(".") if part]
    return "/".join(parts) + ".py"


def _noop_outcome(
    identifier: str, noop: dict[str, TestOutcome], test_files: Sequence[str]
) -> TestOutcome | None:
    """gold 里存在的测试，在 noop 里的结果；整文件收集失败时取文件级错误。"""
    if identifier in noop:
        return noop[identifier]
    module_file = _file_of(identifier)
    for noop_identifier, outcome in noop.items():
        if "::" not in noop_identifier and (
            noop_identifier == module_file
            or noop_identifier.replace(".", "/") + ".py" == module_file
        ):
            return outcome
    # 文件名可能带类前缀；按已知的测试文件做一次前缀匹配。
    for test_file in test_files:
        stem = test_file[:-3].replace("/", ".")
        if identifier.startswith(stem):
            for noop_identifier, outcome in noop.items():
                if "::" not in noop_identifier and test_file in noop_identifier:
                    return outcome
    return None


def compare_runs(
    gold: dict[str, TestOutcome], noop: dict[str, TestOutcome], test_files: Sequence[str]
) -> dict[str, Any]:
    """比较 gold 与 noop，得出 fail_to_pass、pass_to_pass、黑盒 fail_to_pass 与 gold 里的失败。"""
    fail_to_pass: list[str] = []
    black_box: list[str] = []
    pass_to_pass: list[str] = []
    for identifier, outcome in sorted(gold.items()):
        if outcome.outcome != OUTCOME_PASSED:
            continue
        before = _noop_outcome(identifier, noop, test_files)
        if before is None or before.outcome in (OUTCOME_FAILED, OUTCOME_ERROR):
            fail_to_pass.append(identifier)
            # 在 noop 里"根本没被收集到"（before 为 None）也只可能是找不到新增的名字。
            if before is not None and before.failure_kind == FAILURE_BEHAVIORAL:
                black_box.append(identifier)
        elif before.outcome == OUTCOME_PASSED:
            pass_to_pass.append(identifier)
    detail = {
        identifier: {
            "kind": before.failure_kind if before is not None else FAILURE_MISSING_SYMBOL,
            "noop_message": before.message if before is not None else "测试在改动前没有被收集到",
        }
        for identifier in fail_to_pass[:MAX_DETAIL_ENTRIES]
        for before in [_noop_outcome(identifier, noop, test_files)]
    }
    return {
        "fail_to_pass_detail": detail,
        "fail_to_pass": fail_to_pass,
        "black_box_fail_to_pass": black_box,
        "pass_to_pass": pass_to_pass,
        "gold_failures": sorted(
            identifier
            for identifier, outcome in gold.items()
            if outcome.outcome in (OUTCOME_FAILED, OUTCOME_ERROR)
        ),
    }


def classify(
    plan: CalibrationPlan, comparison: dict[str, Any], gold: dict[str, TestOutcome]
) -> str:
    """把比较结果归成一个结论。"""
    if not any(outcome.outcome == OUTCOME_PASSED for outcome in gold.values()):
        return STATUS_GOLD_FAILED
    if not comparison["fail_to_pass"]:
        return STATUS_NO_FAIL_TO_PASS
    if not comparison["black_box_fail_to_pass"]:
        return STATUS_COUPLED
    return STATUS_CALIBRATED


def calibrate_sample(
    sample: dict[str, Any],
    *,
    repo_path: Path,
    command: Sequence[str] = DEFAULT_TEST_COMMAND,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """校准一个样本，返回要写进 ``calibration/`` 的记录。任何环境问题都归为 ``error``，不抛出。"""
    code = sample.get("code") or {}
    base_sha, final_sha = code.get("base_sha"), code.get("final_sha")
    record: dict[str, Any] = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "sample_id": sample["sample_id"],
        "calibrated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_sha": base_sha,
        "final_sha": final_sha,
        "test_files": [],
        "status": STATUS_ERROR,
        "reasons": [],
    }
    if not base_sha or not final_sha:
        record["reasons"].append("样本缺少 base_sha 或 final_sha")
        return record
    try:
        plan = plan_calibration(repo_path=repo_path, base_sha=base_sha, final_sha=final_sha)
        record["test_files"] = plan.test_files
        if not plan.test_files:
            record["status"] = STATUS_NO_TESTS
            record["reasons"].append("这次改动没有新增或修改任何 Python 测试文件")
            return record
        with tempfile.TemporaryDirectory(prefix="calibration-") as work:
            work_dir = Path(work)
            gold_tree, noop_tree, home_dir = work_dir / "gold", work_dir / "noop", work_dir / "home"
            home_dir.mkdir()
            materialize_tree(repo_path, final_sha, gold_tree)
            materialize_tree(repo_path, base_sha, noop_tree)
            overlay_tests(repo_path, final_sha, noop_tree, plan.support_changes)
            gold = run_pytest(
                tree=gold_tree,
                test_files=plan.test_files,
                command=command,
                timeout_seconds=timeout_seconds,
                home_dir=home_dir,
            )
            try:
                noop = run_pytest(
                    tree=noop_tree,
                    test_files=plan.test_files,
                    command=command,
                    timeout_seconds=timeout_seconds,
                    home_dir=home_dir,
                )
            except UnrunnableBeforeChangeError as unrunnable:
                # 改动前连测试都加载不起来：gold 下通过的测试在 noop 下都"不存在"，且完全与新增名字耦合。
                passed = sorted(i for i, o in gold.items() if o.outcome == OUTCOME_PASSED)
                record.update(
                    status=STATUS_COUPLED,
                    fail_to_pass=passed,
                    black_box_fail_to_pass=[],
                    pass_to_pass=[],
                    gold_failures=sorted(i for i, o in gold.items() if o.outcome != OUTCOME_PASSED),
                    counts={"gold": _count_outcomes(gold), "noop": {}},
                )
                record["reasons"].append(str(unrunnable))
                return record
        comparison = compare_runs(gold, noop, plan.test_files)
        record.update(comparison)
        record["status"] = classify(plan, comparison, gold)
        record["counts"] = {
            "gold": _count_outcomes(gold),
            "noop": _count_outcomes(noop),
        }
        if record["status"] == STATUS_COUPLED:
            record["reasons"].append(
                "noop 下的失败全部来自找不到新增的内部名字，测试与实现细节耦合"
            )
        elif record["status"] == STATUS_NO_FAIL_TO_PASS:
            record["reasons"].append("改动前的代码也能通过这些测试，它们区分不出做没做对")
        elif record["status"] == STATUS_GOLD_FAILED:
            record["reasons"].append("应用了原始修复后测试也没有通过，判分器本身不可用")
    except (RuntimeError, subprocess.SubprocessError, OSError, ElementTree.ParseError) as error:
        record["status"] = STATUS_ERROR
        record["reasons"].append(f"{type(error).__name__}: {error}"[:300])
    return record


def _count_outcomes(outcomes: dict[str, TestOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in outcomes.values():
        counts[outcome.outcome] = counts.get(outcome.outcome, 0) + 1
    return counts


def load_candidates(
    raw_dir: Path, *, only_eval_ready: bool, sample_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """从归档的 ``raw/`` 里读出要校准的样本。"""
    samples = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(raw_dir.rglob("*.json"))
    ]
    if sample_ids:
        return [sample for sample in samples if sample["sample_id"] in set(sample_ids)]
    if only_eval_ready:
        return [sample for sample in samples if sample["labels"].get("eval_ready")]
    return samples


def calibration_path(out_dir: Path, sample: dict[str, Any]) -> Path:
    """某个样本的校准记录位置：``calibration/<样本 id>.json``。"""
    return out_dir / f"{safe_name(sample['sample_id'])}.json"


def is_up_to_date(path: Path, sample: dict[str, Any]) -> bool:
    """已有校准记录且针对的是同一对提交、并且不是环境错误时，不必重跑。"""
    if not path.is_file():
        return False
    existing = json.loads(path.read_text(encoding="utf-8"))
    code = sample.get("code") or {}
    return (
        existing.get("base_sha") == code.get("base_sha")
        and existing.get("final_sha") == code.get("final_sha")
        and existing.get("status") != STATUS_ERROR
    )


def calibrate_all(
    samples: list[dict[str, Any]],
    *,
    repo_paths: dict[str, Path],
    out_dir: Path,
    command: Sequence[str] = DEFAULT_TEST_COMMAND,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    jobs: int = 2,
    force: bool = False,
) -> dict[str, int]:
    """校准一批样本并把记录写进 ``out_dir``；返回各结论的数量。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[dict[str, Any], Path]] = []
    status_counts: dict[str, int] = {}
    for sample in samples:
        repo_path = repo_paths.get(sample["repo_id"])
        target = calibration_path(out_dir, sample)
        if repo_path is None:
            record = {
                "schema_version": CALIBRATION_SCHEMA_VERSION,
                "sample_id": sample["sample_id"],
                "status": STATUS_ERROR,
                "reasons": [f"没有给仓库 {sample['repo_id']} 指定本地路径"],
            }
            status_counts[STATUS_ERROR] = status_counts.get(STATUS_ERROR, 0) + 1
            print(f"[{sample['sample_id']}] {record['status']}: {record['reasons'][0]}", flush=True)
            continue
        if not force and is_up_to_date(target, sample):
            status = json.loads(target.read_text(encoding="utf-8"))["status"]
            status_counts[status] = status_counts.get(status, 0) + 1
            print(f"[{sample['sample_id']}] {status} (已有记录，跳过)", flush=True)
            continue
        pending.append((sample, repo_path))

    def work(item: tuple[dict[str, Any], Path]) -> dict[str, Any]:
        sample, repo_path = item
        record = calibrate_sample(
            sample, repo_path=repo_path, command=command, timeout_seconds=timeout_seconds
        )
        calibration_path(out_dir, sample).write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"[{sample['sample_id']}] {record['status']}"
            f" f2p={len(record.get('fail_to_pass', []))}"
            f" 黑盒f2p={len(record.get('black_box_fail_to_pass', []))}"
            f" p2p={len(record.get('pass_to_pass', []))}"
            + (f" | {record['reasons'][0]}" if record["reasons"] else ""),
            flush=True,
        )
        return record

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for record in pool.map(work, pending):
            status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1
    return status_counts


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：校准归档里的候选题，记录写进 ``--out-dir``。"""
    import argparse

    parser = argparse.ArgumentParser(description="校准候选题的判分器（失败转通过检验，不调用模型）")
    parser.add_argument("--raw-dir", type=Path, required=True, help="归档里的 raw/ 目录")
    parser.add_argument(
        "--out-dir", type=Path, required=True, help="校准记录目录（数据集仓库的 calibration/）"
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="REPO_ID=PATH",
        help="样本的 repo_id 对应的本地仓库路径，可重复；keda 运行样本的 repo_id 是 keda、keda-main 等",
    )
    parser.add_argument("--sample-id", action="append", default=[], help="只校准这些样本，可重复")
    parser.add_argument(
        "--all", action="store_true", help="校准全部样本，而不只是机器判定的候选题（eval_ready）"
    )
    parser.add_argument("--jobs", type=int, default=2, help="并行校准的样本数")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="每次 pytest 运行的超时（秒）"
    )
    parser.add_argument("--force", action="store_true", help="已有记录也重跑")
    parser.add_argument(
        "--command",
        default=" ".join(DEFAULT_TEST_COMMAND),
        help='运行 pytest 的命令前缀（一个字符串），默认 "uv run python -m pytest"',
    )
    namespace = parser.parse_args(argv)
    repo_paths = {}
    for item in namespace.repo:
        repo_id, _, path = item.partition("=")
        repo_paths[repo_id] = Path(path).expanduser()
    samples = load_candidates(
        namespace.raw_dir, only_eval_ready=not namespace.all, sample_ids=namespace.sample_id
    )
    print(f"待校准样本 {len(samples)} 个", flush=True)
    counts = calibrate_all(
        samples,
        repo_paths=repo_paths,
        out_dir=namespace.out_dir,
        command=shlex.split(namespace.command),
        timeout_seconds=namespace.timeout,
        jobs=namespace.jobs,
        force=namespace.force,
    )
    print(f"校准结论: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
