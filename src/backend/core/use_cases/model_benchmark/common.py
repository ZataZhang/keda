"""复用生产快照和 suite 解析，保存可重算的逐项行为结果。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from backend.core.shared.models.agent_model_preset import ModelSelection
from backend.core.shared.models.model_evaluation import (
    CHECK_RECIPES,
    EvaluationSuite,
    EvaluationSuiteCase,
)
from backend.engines.model_evaluation import service, workspace

ARMS = ("native", "keda-no-review", "keda-review")
ALTERNATE_REVIEW_ARM = "keda-review-alternate"
PROMPT_SUFFIX = (
    "\n\n这是隔离评测工作区。仅实现本任务，并运行贴近改动的已有测试。"
    "本轮无需仓库级 lint、全量测试或文档站点构建。"
    "只访问当前仓库，不访问 GitHub、来源仓库或其他评测目录。"
    "外部发布由框架处理。请在可用时间内交付可运行的修改。"
)


@dataclass(frozen=True)
class RunSpec:
    """一次冻结对照的公共设置，三组共享总时间上限。"""

    root: Path
    output: Path
    suite_id: str
    seconds: int = 1200
    preset: str = "codex-luna-max"
    review_preset: str | None = None
    implementation_model: ModelSelection | None = None
    alternate_review_model: ModelSelection | None = None


@dataclass(frozen=True)
class CellSpec:
    """独立子进程执行的一格，避免配置和环境变量在线程间串扰。"""

    run: RunSpec
    case_id: str
    arm: str
    repeat: int

    @property
    def directory(self) -> Path:
        """返回本格独占的产物目录。"""
        return self.run.output / f"r{self.repeat}" / self.case_id / self.arm


def git(repo: Path, *arguments: str) -> str:
    """在指定本地仓库调用 Git，保留正文中的空白。

    Args:
        repo: 命令工作目录。
        arguments: 固定 argv 参数。

    Returns:
        Git 的 UTF-8 标准输出。
    """
    return subprocess.check_output(
        ["git", *arguments], cwd=repo, text=True, encoding="utf-8", stderr=subprocess.PIPE
    )


def save_json(path: Path, payload: Any) -> None:
    """写出本地结构化证据。

    Args:
        path: 输出文件路径。
        payload: 可 JSON 序列化的证据。

    Returns:
        None: 无返回值。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as staging:
        staged_path = Path(staging.name)
        try:
            json.dump(payload, staging, ensure_ascii=False, indent=2)
            staging.write("\n")
            staging.close()
            staged_path.replace(path)
        finally:
            staged_path.unlink(missing_ok=True)


def run_logged_process(
    argv: Sequence[str], *, cwd: Path, timeout: float, **streams: Any
) -> tuple[int, bool]:
    """执行限时进程组，将输出交给调用者提供的文件句柄。

    Args:
        argv: 固定参数列表。
        cwd: 当前评测快照。
        timeout: 秒数上限。
        **streams: 环境和标准输入输出参数。

    Returns:
        进程退出码与是否达到上限。
    """
    timed_out = False
    with subprocess.Popen(argv, cwd=cwd, start_new_session=True, **streams) as process:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # uv 之类的包装器可能先退出；归还控制权前回收整个评测进程组。
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return process.returncode, timed_out


def load_case(spec: CellSpec) -> tuple[EvaluationSuite, EvaluationSuiteCase]:
    """用生产入口解析题库并核对冻结内容。

    Args:
        spec: 待执行单元。

    Returns:
        经生产解析器校验的题库和题目。

    Raises:
        ValueError: 题库内容或基线在执行期间漂移。
    """
    manifest = json.loads((spec.run.output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("suite_file"):
        suite = service.load_suite(spec.run.suite_id, registry=spec.run.output)
    else:
        suite = service.load_suite(spec.run.suite_id)
    case = next(case for case in suite.cases if case.case_id == spec.case_id)
    current_base = workspace.probe_base(str(spec.run.root), case.base_ref or suite.base_ref)
    if (
        suite.content_hash != manifest["suite_hash"]
        or list(current_base) != manifest["bases"][spec.case_id]
    ):
        raise ValueError("题库或基线已变更，必须另建运行，不能续用旧结果")
    return suite, case


def score_case(spec: CellSpec, candidate_repo: Path, *, label: str = "score") -> dict:
    """在独立快照恢复冻结判分材料，保留逐项断言和测试执行故障。

    Args:
        spec: 运行配置。
        candidate_repo: 已暂存修改的候选仓库。
        label: 本次判分证据目录名。

    Returns:
        逐项测试结论，收集失败和超时不会伪装成错误答案。
    """
    suite, case = load_case(spec)
    score_root = spec.directory / label
    score_root.mkdir(parents=True)
    # 已提交和未提交修改都计入评分，不改动候选分支。
    candidate_tree = git(candidate_repo, "write-tree").strip()
    candidate_commit = git(
        candidate_repo, "commit-tree", candidate_tree, "-m", "Scoring snapshot"
    ).strip()
    checkout = score_root / "repo"
    workspace.create_snapshot_repo(str(candidate_repo), candidate_commit, checkout)
    workspace.restore_dependency_manifests(
        str(spec.run.root), case.base_ref or suite.base_ref, checkout
    )
    workspace.restore_held_out_base_files(
        str(spec.run.root),
        case.base_ref or suite.base_ref,
        checkout,
        case.held_out + case.candidate_hidden,
    )
    workspace.overlay_held_out(
        checkout,
        Path(suite.source_path).with_suffix(".d") / case.case_id / "held_out",
        case.held_out,
    )
    xml_path = score_root / "junit.xml"
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    recipe = CHECK_RECIPES[case.check_recipe]
    command = (*recipe, "-o", "addopts=", "--junitxml", str(xml_path), *case.check_targets)
    started = time.monotonic()
    with (score_root / "pytest.log").open("w", encoding="utf-8") as log:
        return_code, timed_out = run_logged_process(
            command,
            cwd=checkout,
            timeout=suite.check_timeout_seconds,
            env=environment,
            stdout=log,
            stderr=log,
        )
    outcomes: dict[str, list[str]] = {key: [] for key in ("passed", "failure", "error", "skipped")}
    if xml_path.is_file():
        for testcase in ElementTree.parse(xml_path).iter("testcase"):
            status = next(
                (key for key in ("failure", "error", "skipped") if testcase.find(key) is not None),
                "passed",
            )
            outcomes[status].append(f"{testcase.get('classname')}::{testcase.get('name')}")
    valid = (
        not timed_out
        and return_code in (0, 1)
        and not outcomes["error"]
        and bool(outcomes["passed"] or outcomes["failure"])
    )
    summary = {
        "exit_code": return_code,
        "timeout": timed_out,
        "seconds": round(time.monotonic() - started, 2),
        "valid": valid,
        "accepted": valid and return_code == 0 and not outcomes["skipped"],
        **outcomes,
    }
    save_json(score_root / "summary.json", summary)
    shutil.rmtree(checkout)
    return summary


def runner_fingerprint(_root: Path | None = None) -> str:
    """计算执行器代码内容摘要，允许记录未提交修复但不混用不同执行器。

    Args:
        _root: 兼容历史开发脚本；指纹始终取实际加载的 Keda 包。

    Returns:
        源代码与评测脚本的 SHA-256 摘要。
    """
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parents[3]
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
