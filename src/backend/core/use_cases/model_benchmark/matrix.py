"""先校准再执行三组等时限对照，保留失败、超时和不完整矩阵。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from unittest.mock import patch

from backend.core.use_cases.agent_invocation import build_agent_invocation
from backend.core.shared.models.platform_support import require_posix_process_groups
from backend.engines.model_evaluation import service, workspace
from backend.core.use_cases.model_benchmark.common import (
    ALTERNATE_REVIEW_ARM,
    ARMS,
    PROMPT_SUFFIX,
    CellSpec,
    RunSpec,
    git,
    load_case,
    runner_fingerprint,
    save_json,
    score_case,
)
from backend.core.use_cases.model_benchmark.workflow import (
    benchmark_model,
    build_config,
    run_keda,
    run_native,
)

DEFAULT_SUITE = "keda-behavior-v2"
DEFAULT_PRESET = "codex-luna-max"
BenchmarkInputError = service.EvaluationActionError


@dataclass(frozen=True)
class BenchmarkRequest:
    """评测矩阵请求；校准和实跑使用统一的题目、预算和产物约定。"""

    mode: str
    repo: Path
    output: Path
    suite: str = DEFAULT_SUITE
    cases: tuple[str, ...] = ()
    seconds: int = 1200
    concurrency: int = 3
    repeats: int = 1
    calibration: Path | None = None
    live: bool = False
    preset: str = DEFAULT_PRESET
    review_preset: str | None = None


def _repository_root(workdir: Path) -> Path:
    try:
        return Path(git(workdir.expanduser(), "rev-parse", "--show-toplevel").strip())
    except subprocess.CalledProcessError as failure:
        raise ValueError(f"不是 Git 工作区: {workdir}") from failure


def list_benchmark_suites(workdir: Path) -> list[dict]:
    """在指定检出的评测目录列出题库，不依赖 Console 的数据库。

    Args:
        workdir: 仓库下任一目录。

    Returns:
        各题库和可读校验状态。
    """
    registry = _repository_root(workdir) / service.SUITE_SUBDIR
    if not registry.is_dir():
        raise FileNotFoundError(f"未找到评测集目录: {registry}")
    return service.list_suites(registry=registry, include_calibration=False)


def execute_cell(spec: CellSpec) -> dict:
    """执行单格并对包括超时残留在内的候选统一评分。

    Args:
        spec: 已冻结的单格配置。

    Returns:
        包含真实执行、候选判分和协议故障的记录。
    """
    spec.directory.mkdir(parents=True, exist_ok=False)
    record = {"arm": spec.arm, "case": spec.case_id, "repeat": spec.repeat}
    with (
        (spec.directory / "execution.log").open("w", encoding="utf-8") as execution_log,
        redirect_stdout(execution_log),
        redirect_stderr(execution_log),
    ):
        logging.basicConfig(level=logging.INFO, force=True)
        try:
            suite, case = load_case(spec)
            manifest = json.loads((spec.run.output / "manifest.json").read_text(encoding="utf-8"))
            if runner_fingerprint() != manifest["runner_fingerprint"]:
                raise ValueError("执行器代码已变化，必须另建运行")
            repo = spec.directory / "repo"
            hidden = case.held_out + case.candidate_hidden
            source_commit, source_tree = manifest["bases"][spec.case_id]
            workspace.create_snapshot_repo(
                str(spec.run.root), source_commit, repo, excluded_paths=hidden
            )
            snapshot_commit = git(repo, "rev-parse", "HEAD").strip()
            record.update(
                base_commit=source_commit,
                source_tree=source_tree,
                snapshot_tree=workspace.worktree_tree_hash(repo),
                suite_hash=suite.content_hash,
            )
            git(repo, "config", "core.hooksPath", "/dev/null")
            with (repo / ".git/info/exclude").open("a", encoding="utf-8") as excludes:
                excludes.write("\n/.uv-cache/\n/.kedacode/\n")
            prompt = (
                case.prompt.rstrip()
                + PROMPT_SUFFIX
                + f"\n本轮总执行时间上限为 {spec.run.seconds} 秒。\n"
            )
            record["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            (spec.directory / "prompt.txt").write_text(prompt, encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "UV_CACHE_DIR": str(repo / ".uv-cache"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "GIT_ALLOW_PROTOCOL": "file",
                },
            ):
                if spec.arm in ("noop", "gold"):
                    if spec.arm == "gold":
                        workspace.apply_gold_patch(
                            repo,
                            Path(suite.source_path).with_suffix(".d")
                            / case.case_id
                            / str(case.gold_patch),
                        )
                    record["execution"] = {"delivered": True, "seconds": 0, "agent_calls": []}
                elif spec.arm == "native":
                    record["execution"] = run_native(spec, prompt)
                else:
                    record["execution"] = run_keda(spec, prompt)
            workspace.remove_held_out_files(repo, hidden)
            candidate_patch = workspace.collect_diff(repo, snapshot_commit)
            (spec.directory / "solution.patch").write_text(candidate_patch, encoding="utf-8")
            record["patch_sha256"] = hashlib.sha256(candidate_patch.encode("utf-8")).hexdigest()
            record["changed_files"] = git(repo, "diff", "--name-only", snapshot_commit).splitlines()
            record["score"] = score_case(spec, repo)
            if spec.arm not in ("noop", "gold") and not record["execution"]["agent_calls"]:
                record["harness_error"] = (
                    record["execution"].get("execution_error") or "Keda 未启动模型调用"
                )
            before_review = record["execution"].get("before_review")
            if before_review:
                checkpoint_repo = spec.directory / "before-review-repo"
                workspace.create_snapshot_repo(str(repo), before_review, checkpoint_repo)
                record["before_review_score"] = score_case(
                    spec, checkpoint_repo, label="before-review-score"
                )
                shutil.rmtree(checkpoint_repo)
            record["manual_candidate_edits"] = 0
        except Exception as error:  # noqa: BLE001 - 矩阵边界保留单格故障，不遗漏失败样本
            logging.exception("评测单格失败")
            record["harness_error"] = f"{type(error).__name__}: {error}"
    save_json(spec.directory / "result.json", record)
    if spec.arm in ("noop", "gold") and (spec.directory / "repo").is_dir():
        shutil.rmtree(spec.directory / "repo")
    return record


def is_calibrated(records: list[dict], selected: list[str]) -> bool:
    """要求每道题三次行为负控和三次无故障正控，不能用导入失败充当负控。

    Args:
        records: 校准逐格结果。
        selected: 本轮需要的题号。

    Returns:
        每一组控制都满足要求时为真。
    """
    for case_id in selected:
        for arm in ("noop", "gold"):
            matches = [row for row in records if row["case"] == case_id and row["arm"] == arm]
            if {row["repeat"] for row in matches} != {1, 2, 3} or len(matches) != 3:
                return False
            for row in matches:
                score = row.get("score", {})
                if row.get("harness_error") or not score.get("valid") or score.get("skipped"):
                    return False
                if arm == "gold" and not score.get("accepted"):
                    return False
                if arm == "noop" and not score.get("failure"):
                    return False
    return True


def summarize(output: Path, *, write: bool = True) -> dict:
    """按独立任务列出三组结果，重复次数不会扩充独立样本数。

    Args:
        output: 运行产物目录。
        write: 是否将 JSON 和 Markdown 汇总回写到产物目录。

    Returns:
        可重新从逐格记录计算的汇总。
    """
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(output.glob("r*/*/*/result.json"))
    ]
    aggregates = {}
    for arm in manifest["arms"]:
        rows = [row for row in records if row["arm"] == arm]
        valid = [
            row
            for row in rows
            if row.get("score", {}).get("valid") and not row.get("harness_error")
        ]
        elapsed = [row["execution"]["seconds"] for row in rows if "execution" in row]
        aggregates[arm] = {
            "completed_cells": len(rows),
            "valid_scores": len(valid),
            "accepted_patches": sum(row["score"]["accepted"] for row in valid),
            "delivered_and_accepted": sum(
                row["score"]["accepted"] and row["execution"]["delivered"] for row in valid
            ),
            "invalid_or_unscored": len(rows) - len(valid),
            "median_seconds": median(elapsed) if elapsed else None,
        }
    summary = {
        "unique_tasks": len(manifest["cases"]),
        "planned_cells": len(manifest["schedule"]),
        "completed_cells": len(records),
        "complete": len(records) == len(manifest["schedule"]),
        "arms": aggregates,
        "records": records,
    }
    if not write:
        return summary
    save_json(output / "summary.json", summary)
    calibration = set(manifest["arms"]) == {"noop", "gold"}
    lines = [
        "# 题库校准" if calibration else "# 等时限评测",
        "",
        f"独立任务 {summary['unique_tasks']} 道；完成 {len(records)}/{summary['planned_cells']} 格。",
        "",
        "重复和源任务相关性另行保留，不作为独立新题。成本未核价，不从 token 猜测金额。",
        "",
        "| 题目 | 轮次 | 组别 | 行为通过项 | 完整通过 | 正常交付 | 秒 |",
        "|---|---:|---|---:|---|---|---:|",
    ]
    for row in records:
        score = row.get("score", {})
        execution = row.get("execution", {})
        count = (
            f"{len(score.get('passed', []))}/{len(score.get('passed', [])) + len(score.get('failure', []))}"
            if score.get("valid") and not row.get("harness_error")
            else "判分故障/未评分"
        )
        lines.append(
            f"| {row['case']} | {row['repeat']} | {row['arm']} | {count} | {score.get('accepted', False)} | {execution.get('delivered', False)} | {execution.get('seconds', '—')} |"
        )
    if calibration:
        lines += ["", "校准只运行空补丁和参考补丁，不调用模型。"]
    else:
        lines += [
            "",
            "GitHub 操作和 Git 发布写入本地夹具，模型调用真实。",
            "逐格记录保留输入摘要、审查前提交和独立评分。",
        ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def run_benchmark(
    request: BenchmarkRequest, *, progress: Callable[[dict], None] | None = None
) -> tuple[dict, bool]:
    """冻结、执行并汇总一个本地校准或对照矩阵。

    Args:
        request: 评测输入、时限、模型预设和显式实跑开关。
        progress: 每格完成后的可选进度回调。

    Returns:
        完整报告数据和操作成功标记；模型超时与错误答案仍保留在报告内。

    Raises:
        ValueError: 参数、目标平台或用于真实调用的校准不符合要求。
    """
    calibrating = request.mode == "calibrate"
    if request.mode not in ("calibrate", "run"):
        raise ValueError(f"未知评测模式: {request.mode}")
    if not calibrating and (not request.live or request.calibration is None):
        raise ValueError("真实评测要求 --live 和 --calibration")
    require_posix_process_groups("kc eval")
    if request.seconds <= 0 or not 1 <= request.concurrency <= 4 or not 1 <= request.repeats <= 3:
        raise ValueError("秒数必须为正，并发须为 1–4，重复次数须为 1–3")

    checkout = _repository_root(request.repo)
    suite = service.load_suite(request.suite, registry=checkout / service.SUITE_SUBDIR)
    output = request.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"评测输出目录已存在: {output}")
    selected = list(request.cases) or [
        case.case_id for case in suite.cases if calibrating or case.split != "holdout"
    ]
    if not selected or set(selected) - set(suite.case_ids) or len(set(selected)) != len(selected):
        raise ValueError("题号为空、不存在或重复")
    selected_cases = [case for case in suite.cases if case.case_id in selected]
    if any(case.check_recipe not in ("pytest", "uv-pytest") for case in selected_cases):
        raise ValueError("等时限对照只支持 pytest 或 uv-pytest 的逐项判分")
    if calibrating and any(not case.gold_patch for case in selected_cases):
        raise ValueError("待校准的题目必须提供 gold_patch")
    source = Path(suite.repo_path).expanduser()
    repo = _repository_root(source if source.is_absolute() else checkout / source)
    spec = RunSpec(
        root=repo,
        output=output,
        suite_id=suite.suite_id,
        seconds=request.seconds,
        preset=request.preset,
        review_preset=request.review_preset,
    )
    bases = {
        case.case_id: list(workspace.probe_base(str(repo), case.base_ref or suite.base_ref))
        for case in selected_cases
    }
    arms = ("noop", "gold") if calibrating else ARMS
    if not calibrating:
        assert request.calibration is not None
        approved = request.calibration.expanduser().resolve()
        calibration_manifest = json.loads((approved / "manifest.json").read_text(encoding="utf-8"))
        if (
            calibration_manifest["suite_hash"] != suite.content_hash
            or any(calibration_manifest["bases"].get(key) != value for key, value in bases.items())
            or not is_calibrated(summarize(approved, write=False)["records"], selected)
        ):
            raise ValueError("缺少与当前题目内容和基线相符的三次有效正负校准")
        spec = _freeze_models(spec, selected[0])
        if spec.alternate_review_model is not None:
            arms = (*arms, ALTERNATE_REVIEW_ARM)

    suite_file = Path(suite.source_path)
    if output.is_relative_to(suite_file.parent):
        raise ValueError("评测输出目录必须位于题库目录之外")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(suite_file, output / suite_file.name)
    materials = suite_file.with_suffix(".d")
    if materials.is_dir():
        shutil.copytree(materials, output / materials.name)
    repeats = 3 if calibrating else request.repeats
    schedule = [
        CellSpec(spec, case_id, arm, repeat)
        for repeat in range(1, repeats + 1)
        for case_id in selected
        for arm in arms
    ]
    schedule_seed = 20261122
    random.Random(schedule_seed).shuffle(schedule)
    save_json(
        output / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": request.mode,
            "suite_id": suite.suite_id,
            "suite_file": suite_file.name,
            "suite_hash": suite.content_hash,
            "suite_version": suite.version,
            "bases": bases,
            "runner_fingerprint": runner_fingerprint(),
            "source_head": git(repo, "rev-parse", "HEAD").strip(),
            "cases": selected,
            "arms": arms,
            "seconds": spec.seconds,
            "implementation_model": asdict(spec.implementation_model)
            if spec.implementation_model
            else None,
            "alternate_review_model": asdict(spec.alternate_review_model)
            if spec.alternate_review_model
            else None,
            "schedule_seed": schedule_seed,
            "schedule": [
                {"case": cell.case_id, "arm": cell.arm, "repeat": cell.repeat} for cell in schedule
            ],
        },
    )
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=request.concurrency) as pool:
        for future in as_completed([pool.submit(execute_cell, cell) for cell in schedule]):
            row = future.result()
            if progress is not None:
                progress(
                    {
                        "case": row["case"],
                        "arm": row["arm"],
                        "repeat": row["repeat"],
                        "accepted": row.get("score", {}).get("accepted"),
                        "error": row.get("harness_error"),
                        "elapsed_seconds": round(time.monotonic() - started),
                    }
                )
    summary = summarize(output)
    success = summary["complete"] and (
        is_calibrated(summary["records"], selected)
        if calibrating
        else all(
            row.get("score", {}).get("valid") and not row.get("harness_error")
            for row in summary["records"]
        )
    )
    return summary, success


def _freeze_models(spec: RunSpec, case_id: str) -> RunSpec:
    source_config = build_config(CellSpec(spec, case_id, "keda-review", 1))
    implementation = benchmark_model(spec.preset, source_config)
    alternative = benchmark_model(spec.review_preset, source_config) if spec.review_preset else None
    if alternative and (alternative.model, alternative.reasoning_effort) == (
        implementation.model,
        implementation.reasoning_effort,
    ):
        raise ValueError("替代审查预设必须改变模型或推理档")
    if shutil.which(source_config.agents["codex"].bin) is None:
        raise FileNotFoundError("未找到 Codex CLI，请检查 kc agent doctor")
    for selection in filter(None, (implementation, alternative)):
        invocation = build_agent_invocation(
            "codex", "run", "评测预检", spec.root, source_config, selection
        )
        if (
            Path(invocation.argv[0]).name != "codex"
            or "--model" not in invocation.argv
            or f"model_reasoning_effort={selection.reasoning_effort}" not in invocation.argv
        ):
            raise ValueError("Codex CLI 配置必须传递 --model 和 model_reasoning_effort")
    return replace(spec, implementation_model=implementation, alternate_review_model=alternative)
