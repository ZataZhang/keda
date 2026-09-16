"""Realistic Validation evidence gate for the agent runner.

本模块承载"验证证据门禁"的全部 core 逻辑：

1. **物化解析** — 从 PRD / Issue body 解析 ``Realistic Validation`` 清单与
   ``Validation Waiver`` 豁免声明。
2. **证据隔离** — 默认约定下证据落 ``tasks/evidence/<prd-stem>/``，由
   ``iar init`` provision 的 ``.gitignore`` 白名单保证只有 ``*.md`` 报告进
   版本库；legacy ``.iar/evidence`` 配置仍用 ``info/exclude`` 整目录排除。
   发布前拒绝混入代码 diff 的证据产物（双保险）。
3. **证据强制** — commit 前要求证据目录非空（``ValidationEvidenceError``
   进入既有 recovery 循环）。
4. **证据呈现** — 用 git plumbing（``hash-object``/``mktree``/``commit-tree``）
   构造无父提交并 force-push 到 orphan 证据分支，再在 PR 上发证据评论。
5. **软门禁** — daemon 轮询 ``agent/review`` Issue，按 PR body 勾选状态维护
   ``validation/pending`` / ``validation/passed`` label，head 漂移时重置勾选，
   Issue 关闭后清理证据分支。

所有 hidden marker 与 ``agent_runner_events.py`` 的 ``iar:event`` 同型
（``<!-- iar:... -->`` + 命名捕获组正则）。GitHub Issue / PR 仍是唯一状态源。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


from backend.core.shared.interfaces.agent_runner import (
    IProcessRunner,
)
from backend.core.shared.models.agent_runner import (
    DEFAULT_VALIDATION_EVIDENCE_DIR,
    AppConfig,
    CommandResult,
    DeliveryGateFailureKind,
    IssueSummary,
    evidence_dir_uses_task_subdirs,
)
from backend.core.use_cases.agent_runner_evidence_format import (
    EvidenceKindRule as EvidenceKindRule,
    VISUAL_EVIDENCE_SUFFIXES,
    collect_evidence_coverage_problems,
    demanded_evidence_kinds as demanded_evidence_kinds,
    extract_evidence_format_markers as extract_evidence_format_markers,
)
from backend.core.use_cases.agent_runner_feedback import (
    extract_prd_path,
)
from backend.core.use_cases.agent_runner_git import (
    expand_changed_path,
    has_changes,
    list_changed_paths,
)
from backend.core.use_cases.agent_runner_structured_evidence import (
    EvidenceBlock,
    StdoutAssertion,
    ValidationEvidenceError,
    has_structured_evidence_marker,
    load_evidence_manifest,
    validate_evidence_artifacts,
    validate_evidence_manifest,
)
from backend.core.use_cases.agent_runner_validation_checklist import (
    ValidationChecklistState as ValidationChecklistState,
    build_validation_checklist_block as build_validation_checklist_block,
    parse_validation_checklist_state as parse_validation_checklist_state,
    reset_validation_checklist as reset_validation_checklist,
)
from backend.core.use_cases.agent_runner_validation_parsing import (
    EVIDENCE_ORACLE_SUBDIR,
    build_issue_validation_section as build_issue_validation_section,
    build_validation_prompt_line as build_validation_prompt_line,
    evidence_format_check_required as evidence_format_check_required,
    extract_evidence_format_waiver_reason as extract_evidence_format_waiver_reason,
    extract_realistic_validation_items as extract_realistic_validation_items,
    extract_validation_waiver_reason as extract_validation_waiver_reason,
    format_evidence_format_waiver_marker as format_evidence_format_waiver_marker,
    format_validation_waiver_marker as format_validation_waiver_marker,
    has_evidence_format_waiver_marker as has_evidence_format_waiver_marker,
    has_validation_waiver_marker as has_validation_waiver_marker,
    validation_required as validation_required,
)
from backend.core.use_cases.agent_runner_validation_gate import (
    build_validation_passed_comment as build_validation_passed_comment,
    build_validation_reset_comment as build_validation_reset_comment,
    cleanup_closed_issue_evidence_branches as cleanup_closed_issue_evidence_branches,
    process_validation_gate as process_validation_gate,
)
from backend.core.use_cases.agent_runner_validation_publication import (
    EvidenceUpload as EvidenceUpload,
    build_evidence_comment as build_evidence_comment,
    evidence_branch_name as evidence_branch_name,
    parse_latest_evidence_marker as parse_latest_evidence_marker,
    parse_pr_number as parse_pr_number,
    publish_validation_evidence as publish_validation_evidence,
    publish_validation_evidence_best_effort as publish_validation_evidence_best_effort,
    upload_evidence_branch as upload_evidence_branch,
)

_logger = logging.getLogger(__name__)
_EVIDENCE_MARKER_PATTERN = re.compile(
    r"<!--\s*iar:validation-evidence\s+"
    r"version=(?P<version>\d+)\s+"
    r"head=(?P<head>[a-f0-9]+)\s+"
    r"branch=(?P<branch>[^\s>]+)\s+"
    r"count=(?P<count>\d+)"
    r"\s*-->"
)
_PR_URL_PATTERN = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)

_INLINE_TEXT_SUFFIXES = {".txt", ".log", ".md", ".out"}
_MAX_INLINE_EVIDENCE_CHARS = 3000
_MISPLACED_EVIDENCE_HELPER_PREFIXES = (
    "scripts_evidence/",
    "scripts/evidence/",
    "scripts/evidence_helpers/",
    "scripts/rv_evidence/",
)
# ``rv-``/``rv_`` 前缀几乎不会出现在产品交付物上,因此可以独立于目录名识别
# 错放的取证脚本——这是"换个新目录名规避"的兜底防线。
#
# 刻意不要求前缀后跟条目编号(曾用 ``^rv[-_]\d+[-_]``):keda 自身就攒下了
# ``rv_capture.sh``(自述 "RV capture script for Issue #115")、``rv_follow.py``、
# ``rv_setup_fixture.py`` 一类按用途而非按条目命名的取证脚本,带编号的规则一个
# 都拦不住。误伤面很窄——只作用于**新增**文件的 basename,而产品代码里以
# ``rv`` 开头的文件名本就罕见;真撞上时错误信息会直接给出正确去处。
_RV_ORACLE_NAME_PATTERN = re.compile(r"^rv[-_]", re.IGNORECASE)
# 命名规则只作用于**脚本**。证据产物(``rv-1-login.png``/``.txt``/``.webm``)是
# 另一回事:部分下游仓库按 ``docs/ai-standards/testing.md`` 的规定把证据归档到
# ``tasks/evidence/<prd-basename>/`` 并有 ``check_prd_evidence.sh`` 兜底,那是
# 有文档、有工具背书的既定做法。本门禁的错误信息写的是"RV scripts must never
# enter the code diff",拿它去拦一张 PNG 既不自洽,也会把那套流程直接打断。
# "证据产物该不该进版本库"与本规则正交,应单独决策。
_RV_SCRIPT_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".js", ".cjs", ".mjs", ".ts", ".rb", ".pl", ".ps1"}
)
_EVIDENCE_UPLOAD_SKIP_DIRS = frozenset({"__pycache__", "node_modules"})
_EVIDENCE_UPLOAD_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})


@dataclass(frozen=True)
class EvidenceMarker:
    """Parsed iar:validation-evidence hidden marker from a PR comment."""

    head_sha: str
    branch: str
    count: int


# ---------------------------------------------------------------------------
# 证据目录：隔离与强制
# ---------------------------------------------------------------------------


def evidence_dir_path(worktree_path: Path, config: AppConfig) -> Path:
    """Return the absolute evidence directory *root* inside the worktree.

    这是配置根目录（``config.validation.evidence_dir``）的解析。新约定下证据
    实际落在按任务分的子目录里，消费方应使用 :func:`resolve_issue_evidence_dir`
    或 :func:`resolve_evidence_dir`；本函数只作为它们的根解析与 legacy 兜底。
    """
    return worktree_path / config.validation.evidence_dir


def resolve_evidence_dir(
    worktree_path: Path,
    config: AppConfig,
    *,
    prd_stem: str | None = None,
    issue_number: int | None = None,
) -> Path:
    """证据目录的单一解析入口：配置根 + 任务子目录（仅新约定）。

    配置为默认 ``tasks/evidence`` 时按任务分目录：有 canonical PRD 用
    ``<root>/<prd-stem>``，无 PRD 时用 ``<root>/issue-<N>`` 兜底；两者都
    没有时退回配置根本身。显式配置的其它目录（legacy ``.iar/evidence``）
    保持扁平语义——那些仓库的行为逐字节不变。

    Args:
        worktree_path: worktree 根目录。
        config: 运行配置，提供证据目录根。
        prd_stem: canonical PRD 的文件名 stem（不含目录与扩展名）。
        issue_number: Issue 编号；无 PRD 时用于 ``issue-<N>`` 兜底子目录。

    Returns:
        证据目录的绝对路径。
    """
    root = evidence_dir_path(worktree_path, config)
    if not evidence_dir_uses_task_subdirs(config.validation.evidence_dir):
        return root
    if prd_stem:
        return root / prd_stem
    if issue_number is not None:
        return root / f"issue-{issue_number}"
    return root


def resolve_evidence_relpath(
    config: AppConfig,
    *,
    prd_stem: str | None = None,
    issue_number: int | None = None,
) -> str:
    """与 :func:`resolve_evidence_dir` 同规则的仓库相对路径（POSIX 分隔符）。

    供 prompt 插值与错误信息使用，避免各处各自拼 ``f"{root}/{stem}"``。
    """
    return resolve_evidence_dir(
        Path(""), config, prd_stem=prd_stem, issue_number=issue_number
    ).as_posix()


def resolve_issue_evidence_dir(worktree_path: Path, config: AppConfig, issue: IssueSummary) -> Path:
    """按 Issue 的 canonical PRD（或编号兜底）解析证据目录。"""
    prd_relative_path = extract_prd_path(issue.body)
    prd_stem = Path(prd_relative_path).stem if prd_relative_path else None
    return resolve_evidence_dir(worktree_path, config, prd_stem=prd_stem, issue_number=issue.number)


def resolve_issue_evidence_relpath(config: AppConfig, issue: IssueSummary) -> str:
    """按 Issue 解析证据目录的仓库相对路径（POSIX 分隔符）。"""
    prd_relative_path = extract_prd_path(issue.body)
    prd_stem = Path(prd_relative_path).stem if prd_relative_path else None
    return resolve_evidence_relpath(config, prd_stem=prd_stem, issue_number=issue.number)


def list_evidence_files(
    worktree_path: Path,
    config: AppConfig,
    issue: IssueSummary | None = None,
) -> list[Path]:
    """List first-level regular evidence *artifacts*, sorted by name.

    隐藏文件（``.`` 开头）与子目录被忽略——**这里的单层语义是刻意的,不要改成
    递归**。本函数喂给两处判定:``collect_evidence_coverage_problems`` 按
    ``rv-<n>-*`` 文件名对账清单覆盖,``ensure_frontend_visual_evidence`` 按后缀
    找截图/录屏。一旦递归,``{evidence_dir}/scripts/rv-1-oracle.py`` 会冒充
    rv-1 的证据文件、``scripts/`` 下的 ``.png`` 会冒充视觉证据,缺证据的清单项
    就能蒙混过关。需要连子目录一起取（仅上传场景）请用
    :func:`list_evidence_upload_files`。

    Args:
        worktree_path: worktree 根目录。
        config: 运行配置。
        issue: 当前 Issue；提供时按任务子目录解析，否则退回配置根目录。
    """
    evidence_dir = (
        resolve_issue_evidence_dir(worktree_path, config, issue)
        if issue is not None
        else evidence_dir_path(worktree_path, config)
    )
    if not evidence_dir.is_dir():
        return []
    return sorted(
        candidate_path
        for candidate_path in evidence_dir.iterdir()
        if candidate_path.is_file() and not candidate_path.name.startswith(".")
    )


def list_evidence_upload_files(
    worktree_path: Path,
    config: AppConfig,
    issue: IssueSummary | None = None,
) -> list[str]:
    """List every evidence file, recursively, as evidence-dir-relative POSIX paths.

    与 :func:`list_evidence_files` 的单层语义相对:上传到证据分支时要连同
    ``{evidence_dir}/scripts/`` 下的 oracle 源码一起带走,审阅者才能看到"产出
    这份证据的断言是怎么写的"。任何一级以 ``.`` 开头的文件或目录都跳过。

    Args:
        worktree_path: worktree 根目录。
        config: 运行配置。
        issue: 当前 Issue；提供时按任务子目录解析，否则退回配置根目录。

    Returns:
        相对证据目录的路径列表（POSIX 分隔符）,已排序;证据目录不存在时为空。
    """
    evidence_dir = (
        resolve_issue_evidence_dir(worktree_path, config, issue)
        if issue is not None
        else evidence_dir_path(worktree_path, config)
    )
    if not evidence_dir.is_dir():
        return []
    relative_paths: list[str] = []
    for candidate_path in evidence_dir.rglob("*"):
        if not candidate_path.is_file():
            continue
        relative_path = candidate_path.relative_to(evidence_dir)
        if any(part.startswith(".") for part in relative_path.parts):
            continue
        # Python 字节码缓存不是证据:oracle 一旦被 pytest 收集过,
        # ``__pycache__/*.pyc`` 就会出现在证据目录里,推到证据分支纯属噪音。
        if _EVIDENCE_UPLOAD_SKIP_DIRS.intersection(relative_path.parts):
            continue
        if relative_path.suffix in _EVIDENCE_UPLOAD_SKIP_SUFFIXES:
            continue
        relative_paths.append(relative_path.as_posix())
    return sorted(relative_paths)


def evidence_oracle_digest(
    worktree_path: Path,
    config: AppConfig,
    issue: IssueSummary | None = None,
) -> str:
    """Digest the RV oracle scripts so the re-execution cache tracks their content.

    证据目录下的 oracle 不参与 ``HEAD`` 的 tree SHA（新约定经 ``.gitignore``
    白名单排除非 ``.md`` 产物，legacy 配置经 ``info/exclude`` 整目录排除）。
    若缓存键只按 tree SHA + 命令构造,把 oracle 的断言删空
    也不会改变键,门禁会继续报"已通过、跳过复跑"。本摘要把 oracle 内容并回缓存
    键,让任何 oracle 改动都必然触发真实重跑。

    摘要按目录整体计算而非按命令解析脚本路径:代价是改 A 脚本会让 B 条目一并
    失效,换来的是不必解析 shell 命令行,方向上宁可过度失效也不放过。

    Args:
        worktree_path: worktree 根目录。
        config: 运行配置。
        issue: 当前 Issue；提供时按任务子目录解析，否则退回配置根目录。

    Returns:
        十六进制摘要;oracle 目录不存在或为空时返回固定的 ``"-"``,保证既有仓库
        行为稳定。
    """
    oracle_dir = (
        resolve_issue_evidence_dir(worktree_path, config, issue)
        if issue is not None
        else evidence_dir_path(worktree_path, config)
    ) / EVIDENCE_ORACLE_SUBDIR
    if not oracle_dir.is_dir():
        return "-"
    file_digests: list[str] = []
    for script_path in sorted(oracle_dir.rglob("*")):
        if not script_path.is_file():
            continue
        relative_path = script_path.relative_to(oracle_dir).as_posix()
        content_digest = hashlib.sha256(script_path.read_bytes()).hexdigest()
        file_digests.append(f"{relative_path}:{content_digest}")
    if not file_digests:
        return "-"
    return hashlib.sha256("\n".join(file_digests).encode("utf-8")).hexdigest()[:16]


def ensure_evidence_dir_excluded(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """Idempotently exclude the evidence dir (legacy only) and RV cache via git ``info/exclude``.

    新约定（默认 ``tasks/evidence``）下证据目录的 git 语义由 ``iar init``
    provision 的 ``.gitignore`` 白名单保证（只放行 ``*.md`` 报告），这里不再对
    它写整目录 info/exclude；显式配置了其它目录（legacy ``.iar/evidence``）的
    仓库保持原有的整目录排除行为，逐字节不变。

    无论哪种配置，都排除 RV 复跑缓存文件（:data:`_RV_REEXEC_CACHE_RELPATH`），
    避免它让工作区显示为脏或泄漏进代码 diff。

    使用 ``git rev-parse --git-path info/exclude`` 解析排除文件位置
    （worktree 下指向 commondir，规则对主仓与所有 worktree 共享生效）。
    该文件是本地配置，不进入版本库，因此不会像修改 ``.gitignore``
    那样产生需要合并的代码变更。
    """
    exclude_path_result = process_runner.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=worktree_path,
        check=False,
    )
    exclude_path_text = exclude_path_result.stdout.strip()
    if exclude_path_result.return_code != 0 or not exclude_path_text:
        # 拿不到排除文件路径时降级为警告：发布前的
        # ensure_no_evidence_paths_in_changes 仍会拦截证据泄漏。
        _logger.warning(
            "Could not resolve git info/exclude for %s; evidence exclusion "
            "falls back to the publish guard.",
            worktree_path,
        )
        return
    exclude_path = Path(exclude_path_text)
    if not exclude_path.is_absolute():
        exclude_path = worktree_path / exclude_path
    if exclude_path.is_dir():
        _logger.warning(
            "Resolved info/exclude path is a directory (%s); skipping evidence exclusion.",
            exclude_path,
        )
        return
    desired_lines = []
    if not evidence_dir_uses_task_subdirs(config.validation.evidence_dir):
        # legacy/自定义目录：整目录排除，行为与历史版本一致。
        desired_lines.append(f"/{config.validation.evidence_dir.strip('/')}/")
    desired_lines.append(f"/{_RV_REEXEC_CACHE_RELPATH}")
    existing_text = ""
    if exclude_path.exists():
        existing_text = exclude_path.read_text(encoding="utf-8")
    existing_lines = existing_text.splitlines()
    missing_lines = [line for line in desired_lines if line not in existing_lines]
    if not missing_lines:
        return
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    appended_text = existing_text
    if appended_text and not appended_text.endswith("\n"):
        appended_text += "\n"
    appended_text += "".join(f"{line}\n" for line in missing_lines)
    exclude_path.write_text(appended_text, encoding="utf-8")


def _path_touches_frontend(changed_path: str, frontend_paths: tuple[str, ...]) -> bool:
    """判断变更路径是否落在任一前端目录前缀下（按路径段匹配，非裸子串）。

    Args:
        changed_path (str): git status 报出的单个仓库相对路径。
        frontend_paths (tuple[str, ...]): 前端目录前缀列表。

    Returns:
        bool: 命中任一前端前缀返回 True。
    """
    normalized_path = changed_path.strip()
    for raw_prefix in frontend_paths:
        prefix = raw_prefix.strip().strip("/")
        if prefix and (normalized_path == prefix or normalized_path.startswith(prefix + "/")):
            return True
    return False


def ensure_frontend_visual_evidence(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner | None = None,
) -> None:
    """前端改动强制真实视觉证据的 fail-closed 门禁。

    当 worktree 的 git 变更命中 ``config.validation.frontend_paths`` 中任一
    目录前缀时，证据目录（第一层）必须至少有一个视觉证据文件（图片/视频，
    见 ``VISUAL_EVIDENCE_SUFFIXES``），否则抛 ``ValidationEvidenceError``，
    由既有 recovery 循环接管。

    判定依据是"改了什么"（git diff）而非清单文本关键字，因此覆盖"前端 RV
    条目文本不含'截图'导致逐项检查漏判"的盲区；本门禁独立于
    ``verifier_enabled``。``process_runner`` 为 ``None`` 时（旧调用方未接线）
    跳过，避免破坏兼容。

    Args:
        issue (IssueSummary): 当前处理的 Issue。
        worktree_path (Path): worktree 根目录。
        config (AppConfig): 运行配置。
        process_runner (IProcessRunner | None): 命令执行端口；None 时跳过。

    Raises:
        ValidationEvidenceError: 前端改动但证据目录缺少视觉证据文件。
    """
    if process_runner is None:
        return
    if not config.validation.frontend_visual_evidence_required:
        return
    if not validation_required(issue.body, config):
        return
    frontend_paths = tuple(config.validation.frontend_paths)
    if not frontend_paths:
        return
    changed_paths = list_changed_paths(worktree_path, process_runner)
    touched_frontend_paths = [
        changed_path
        for changed_path in changed_paths
        if _path_touches_frontend(changed_path, frontend_paths)
    ]
    if not touched_frontend_paths:
        return
    evidence_files = list_evidence_files(worktree_path, config, issue)
    if any(
        evidence_file.suffix.lower() in VISUAL_EVIDENCE_SUFFIXES for evidence_file in evidence_files
    ):
        return
    accepted_suffixes_text = "/".join(sorted(VISUAL_EVIDENCE_SUFFIXES))
    touched_preview = ", ".join(sorted(touched_frontend_paths)[:5])
    evidence_dir_text = resolve_issue_evidence_relpath(config, issue)
    raise ValidationEvidenceError(
        "Frontend changes were made but no visual evidence "
        f"({accepted_suffixes_text}) exists in "
        f"`{evidence_dir_text}/`. Changed frontend paths: "
        f"{touched_preview}. Run the target repo's UI/e2e entry point and save "
        "at least one real screenshot or screen recording into the evidence "
        "directory; a text log does not prove a UI change.",
        kind=DeliveryGateFailureKind.FRONTEND_VISUAL_EVIDENCE_MISSING,
    )


def ensure_validation_evidence_ready(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner | None = None,
) -> None:
    """Require per-item evidence when the Issue demands validation.

    除了证据目录非空，还逐项核对清单：每个条目都要有 ``rv-<n>-*`` 文件，
    条目点名的格式（截图、pdf、txt、word……）必须有对应后缀的证据。
    逐项对账可全局关（``validation.evidence_format_check = false``）或
    按任务关（Issue body 带 ``iar:evidence-format-waived`` marker），
    关闭后退化为仅要求证据目录非空。

    对于带 ``iar:structured-evidence`` marker 的 Issue，额外校验
    ``evidence.json`` manifest：字段完整性、item 覆盖、证据文件存在性与
    编号一致性、语言一致性。

    当 ``config.validation.artifact_health_enabled`` 为真且 ``process_runner``
    被提供时,额外对 manifest 声明的 ``expected_artifacts`` 跑硬层健全性卡点
    (mime/size/duration/mtime),FR-11a。

    Raises:
        ValidationEvidenceError: 要求验证但证据缺失或与清单不匹配。
    """
    if not validation_required(issue.body, config):
        return
    # 前端改动强制视觉证据（fail-closed，按 diff 判定，独立于 verifier）。
    ensure_frontend_visual_evidence(issue, worktree_path, config, process_runner)
    evidence_files = list_evidence_files(worktree_path, config, issue)
    evidence_dir_text = resolve_issue_evidence_relpath(config, issue)
    if not evidence_files:
        # 证据目录为空 = 验证根本没跑过，保持真失败分类（整轮重跑），不进收尾层。
        raise ValidationEvidenceError(
            "Realistic Validation evidence is required but "
            f"`{evidence_dir_text}/` is empty or missing. "
            "Actually execute the PRD's Realistic Validation Plan through "
            "real entry points and save evidence files (PNG screenshots for "
            "UI behavior, captured terminal output as .txt for CLI behavior) "
            "named like `rv-1-<slug>.png` into that directory."
        )
    if has_structured_evidence_marker(issue.body):
        checklist_items = extract_realistic_validation_items(issue.body)
        resolved_evidence_dir = resolve_issue_evidence_dir(worktree_path, config, issue)
        validate_evidence_manifest(
            issue_body=issue.body,
            checklist_items=checklist_items,
            worktree_path=worktree_path,
            config=config,
            evidence_dir=resolved_evidence_dir,
        )
        # FR-11a: artifact health hard layer (machine-checkable assertions).
        # Skip when process_runner is None (caller did not wire it) to keep the
        # legacy non-structured callers working.
        if config.validation.artifact_health_enabled and process_runner is not None:
            manifest = load_evidence_manifest(
                worktree_path, config, evidence_dir=resolved_evidence_dir
            )
            validate_evidence_artifacts(
                manifest,
                worktree_path,
                config,
                process_runner,
            )
        return
    if not evidence_format_check_required(issue.body, config):
        return
    coverage_problems = collect_evidence_coverage_problems(
        extract_realistic_validation_items(issue.body),
        evidence_files,
        issue_body=issue.body,
    )
    if coverage_problems:
        problems_text = "\n".join(f"- {coverage_problem}" for coverage_problem in coverage_problems)
        # 覆盖对不上 = 有条目没真跑过，保持真失败分类，不进收尾层。
        raise ValidationEvidenceError(
            "Realistic Validation evidence does not match the checklist:\n"
            f"{problems_text}\n"
            "Each checklist item needs its own evidence file numbered "
            "`rv-<item-number>-<slug>.<ext>`, in the file format the item "
            "names (screenshot → image, pdf → .pdf, txt → .txt, and so on). "
            "Execute every item through the real entry point it describes — "
            "fakes, mocks, or TestClient substitutes do not satisfy the item."
        )


_RV_REEXEC_CACHE_RELPATH = ".iar/rv_reexec_cache.json"
"""RV 复跑缓存的 worktree 相对路径（runner 私有状态）。

固定留在 ``.iar/``，与证据目录父目录解耦：RV 脚本每轮会清空自己产生的
``rv-*`` 证据，缓存放进证据目录会被一并抹掉；``.iar/`` 由 init 的
``.gitignore`` 托管块与 :func:`ensure_evidence_dir_excluded` 双重排除。
"""


def _rv_reexec_cache_path(worktree_path: Path) -> Path:
    """Absolute path of the RV re-execution cache inside ``worktree_path``."""
    return worktree_path / _RV_REEXEC_CACHE_RELPATH


def _clean_tree_fingerprint(worktree_path: Path, process_runner: IProcessRunner) -> str | None:
    """Return ``HEAD`` 的 tree SHA(工作区干净时),否则 ``None``。

    tree SHA 是已提交代码的纯内容指纹(不含提交时间/作者/message)。工作区
    一旦脏——有未提交的已跟踪改动,或非排除的 untracked 文件——返回 ``None``,
    让调用方照常复跑而非信任过期的通过结果;v1 只对完全已提交的状态做缓存。
    """
    if has_changes(worktree_path, process_runner):
        return None
    result = process_runner.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=worktree_path,
        check=False,
    )
    tree_sha = result.stdout.strip()
    if result.return_code != 0 or not tree_sha:
        return None
    return tree_sha


def _rv_reexec_cache_key(
    tree_fingerprint: str,
    item_number: int,
    command: str,
    oracle_digest: str,
) -> str:
    """Cache key 绑定"某命令在某代码树与某份 oracle 上、对某 item 已通过"。

    键里含命令的哈希:在(gitignore 的)manifest 里改命令不会改 tree SHA,
    但会改命令哈希 → 缓存未命中 → 照常复跑,不会用旧命令的结果蒙混。

    键里同样含 oracle 摘要(:func:`evidence_oracle_digest`):命令字符串不变、
    但它调用的脚本被改写时,tree SHA 与命令哈希都不变,只有摘要会变 → 缓存
    未命中 → 照常复跑,不会用旧 oracle 的结论蒙混。
    """
    command_digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]
    return f"{tree_fingerprint}|{item_number}|{command_digest}|{oracle_digest}"


def _load_rv_reexec_cache(cache_path: Path) -> dict[str, str]:
    """Load the RV re-exec cache entries; tolerate a missing/corrupt file."""
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return entries if isinstance(entries, dict) else {}


def _save_rv_reexec_cache(cache_path: Path, entries: dict[str, str]) -> None:
    """Persist the RV re-exec cache entries as json."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _assertion_target_text(assertion: StdoutAssertion, command_result: CommandResult) -> str:
    """Return the stream the assertion must be evaluated against."""
    return command_result.stderr if assertion.source == "stderr" else command_result.stdout


def _is_stdout_assertion_satisfied(
    assertion: StdoutAssertion, command_result: CommandResult
) -> bool:
    """判断一条输出断言是否被满足（``must_match`` 时要求出现，否则要求不出现）。"""
    target_text = _assertion_target_text(assertion, command_result)
    pattern_matched = assertion.pattern in target_text
    return pattern_matched if assertion.must_match else not pattern_matched


def _validate_stdout_assertions(block: EvidenceBlock, command_result: CommandResult) -> None:
    """对已 exit 0 的 RV 命令执行 manifest 声明的输出断言（补丁 1）。

    退出码只是"命令没崩"，不代表检查点成立：``[ -d x ] || true`` 这类兜底会让
    命令稳定 exit 0 而从未真正验证。这里按 manifest 的 ``stdout_assertions`` 对
    keda 自己复跑得到的 stdout/stderr 做子串断言，失败即抛
    ``ValidationEvidenceError`` 进入既有 recovery 循环——与 exit code 失败走同一条路。

    Raises:
        ValidationEvidenceError: 任一断言未被满足。错误消息指明断言序号、
            pattern、source，便于 agent 直接定位改哪条。
    """
    for assertion_index, assertion in enumerate(block.stdout_assertions, start=1):
        if _is_stdout_assertion_satisfied(assertion, command_result):
            continue
        expectation_text = (
            f"must contain {assertion.pattern!r}"
            if assertion.must_match
            else f"must not contain {assertion.pattern!r}"
        )
        raise ValidationEvidenceError(
            f"Realistic Validation item {block.item_number} exited 0 but its stdout "
            f"assertion #{assertion_index} failed: {expectation_text} in "
            f"{assertion.source} (severity={assertion.severity}, "
            f"command `{block.command}`). An exit code of 0 is not proof the "
            "checkpoint holds — remove `|| true` / `|| echo ok` style fallbacks and "
            "make the command actually assert, or correct the declared assertion."
        )


def ensure_validation_commands_pass(
    issue: IssueSummary,
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """Re-run each structured-evidence item's command and require it to pass.

    keda 以自己复跑的退出码为准,而不是只信 agent 写的证据文件——这样
    "测试通过但功能其实坏了 / agent 没真跑" 无法蒙混过关。仅对带
    ``iar:structured-evidence`` marker、要求验证、且开启 ``reexecute_commands``
    的 Issue 生效。命令经 ``bash -lc`` 在 worktree 内执行并带超时;非零退出
    或超时即判失败,抛 ``ValidationEvidenceError`` 进入既有 recovery 循环。

    当 ``reexecute_cache_enabled`` 开启且工作区干净时,按 ``HEAD`` 的 tree SHA
    指纹缓存"该 item 的该命令已通过":同一份已提交代码再次进入(如
    blocked-continue、换 agent、重新 claim)直接跳过复跑,避免重复跑 e2e。
    工作区一旦脏(有未提交改动)即不读不写缓存、照常复跑。证据文件是否齐全
    仍由 ``ensure_validation_evidence_ready`` 单独把关,缓存命中不绕过它。

    Raises:
        ValidationEvidenceError: 任一命令被 keda 复跑后未通过或超时。
    """
    if not config.validation.reexecute_commands:
        return
    if not validation_required(issue.body, config):
        return
    if not has_structured_evidence_marker(issue.body):
        return

    manifest = load_evidence_manifest(
        worktree_path,
        config,
        evidence_dir=resolve_issue_evidence_dir(worktree_path, config, issue),
    )
    timeout_seconds = config.validation.reexecute_timeout_seconds

    tree_fingerprint = (
        _clean_tree_fingerprint(worktree_path, process_runner)
        if config.validation.reexecute_cache_enabled
        else None
    )
    cache_path = _rv_reexec_cache_path(worktree_path)
    cache_entries = _load_rv_reexec_cache(cache_path) if tree_fingerprint else {}
    newly_passed: dict[str, str] = {}
    # oracle 摘要按次计算一次:同一轮内脚本内容不变,逐条重算既无必要也会让
    # 中途被改写的脚本产生前后不一致的键。
    oracle_digest = evidence_oracle_digest(worktree_path, config, issue)

    for block in manifest.items:
        cache_key = (
            _rv_reexec_cache_key(
                tree_fingerprint,
                block.item_number,
                block.command,
                oracle_digest,
            )
            if tree_fingerprint
            else None
        )
        if cache_key is not None and cache_key in cache_entries:
            _logger.info(
                "Realistic Validation item %s: skipping re-execution; command "
                "already passed at tree %s.",
                block.item_number,
                tree_fingerprint,
            )
            continue
        try:
            result = process_runner.run(
                ["bash", "-lc", block.command],
                cwd=worktree_path,
                check=False,
                capture_output=True,
                timeout=timeout_seconds,
                label=f"rv-reexec-{block.item_number}",
            )
        except subprocess.TimeoutExpired as timeout_error:
            # RV 命令复跑超时 / 失败 = 行为没做对，保持真失败分类（收尾层不得接手）。
            raise ValidationEvidenceError(
                f"Realistic Validation item {block.item_number} timed out when keda "
                f"re-ran its command (>{timeout_seconds}s): `{block.command}`. The "
                "reproducible command must be a self-terminating check that probes the "
                "real entry point and exits, not a long-running server. Set "
                "`validation.reexecute_commands=false` to opt out."
            ) from timeout_error
        if result.return_code != 0:
            raise ValidationEvidenceError(
                f"Realistic Validation item {block.item_number} failed when keda "
                f"re-ran its command: `{block.command}` exited {result.return_code}. "
                "keda re-executes RV commands to confirm they actually pass — the "
                "agent's evidence file alone is not trusted. Fix the behavior so the "
                "command passes (or correct the command). Set "
                "`validation.reexecute_commands=false` to opt out."
            )
        # exit 0 只是"命令没崩"：断言未过时不得写缓存，否则假绿灯会被缓存固化。
        _validate_stdout_assertions(block, result)
        # 逐条记耗时：attempt 历史里的 ``rv_reexec`` 只有一个合计值，慢在哪一条
        # 只能靠这条日志（例如一条 e2e 吃掉几百秒）。
        _logger.info(
            "Realistic Validation item %s re-executed in %.1fs.",
            block.item_number,
            result.duration_seconds,
        )
        if cache_key is not None:
            newly_passed[cache_key] = datetime.now(timezone.utc).isoformat()

    if newly_passed:
        cache_entries.update(newly_passed)
        _save_rv_reexec_cache(cache_path, cache_entries)


def format_validation_evidence_detail(message: str) -> str:
    """Build the recorded attempt detail for a validation-evidence failure.

    Keeps the specific failure ``message`` as the last line so the attempt
    history Detail column surfaces the real reason — the table summarizer
    (``_summarize_attempt_detail``) keeps the last informative line. The
    generic "run it for real" instruction belongs only in the recovery prompt
    (:func:`format_validation_evidence_failure`), never in the diagnostic
    record, where appending it as the last line would mask the actual cause.
    """
    return "\n".join(
        [
            "Realistic Validation evidence check failed.",
            message,
        ]
    )


def format_validation_evidence_failure(
    message: str,
    evidence_dir: str = DEFAULT_VALIDATION_EVIDENCE_DIR,
) -> str:
    """Build the failure section for an evidence recovery prompt.

    Args:
        message: 门禁失败的具体原因。
        evidence_dir: 当前 Issue 的证据目录仓库相对路径；调用方应传
            :func:`resolve_issue_evidence_relpath` 的结果，缺省为配置默认值。
    """
    return "\n".join(
        [
            format_validation_evidence_detail(message),
            "Run the validation plan for real and write the evidence files; "
            "do not fabricate evidence and do not capture secrets. Every RV script "
            "— evidence capture, temporary setup, and reproducible oracles referenced "
            f"by an `evidence.json` command alike — must live under "
            f"`{evidence_dir}/{EVIDENCE_ORACLE_SUBDIR}/` and stay out of the code diff. "
            "No RV script may be committed, whatever the PRD asks for.",
        ]
    )


def is_misplaced_evidence_helper(repo_relative_path: str, config: AppConfig) -> bool:
    """判定单个仓库相对路径是否为错放的 RV 辅助脚本。

    这是前瞻守卫(:func:`ensure_no_misplaced_evidence_helpers`)与存量告警
    (:func:`warn_legacy_evidence_helpers`)**共用的唯一判定源**,两处规则不得
    各写一份,否则必然漂移。

    判定顺序:

    1. 落在证据目录下 → 合法。证据目录是 RV 脚本的唯一归宿,其内部结构不受限。
    2. 命中历史上被反复误用的目录前缀 → 错放。
    3. 文件名以 ``rv-``/``rv_`` 开头**且**是脚本后缀(``rv-1-login.py``、
       ``rv_capture.sh`` 均算) → 错放。这条与目录名无关,换个新目录名规避不掉;
       但只管脚本——``rv-1-login.png`` 这类证据产物由别的规则管,见
       :data:`_RV_SCRIPT_SUFFIXES`。
    4. 其余 → 合法。产品脚本(如 ``scripts/migrate_users.py``)不受影响。

    Args:
        repo_relative_path: 仓库相对路径,POSIX 分隔符。
        config: 提供证据目录位置。

    Returns:
        错放为 ``True``。
    """
    normalized_path = repo_relative_path.strip("/")
    if not normalized_path:
        return False
    evidence_prefix = config.validation.evidence_dir.strip("/") + "/"
    if normalized_path.startswith(evidence_prefix):
        return False
    if normalized_path.startswith(_MISPLACED_EVIDENCE_HELPER_PREFIXES):
        return True
    candidate_name = Path(normalized_path)
    return bool(
        _RV_ORACLE_NAME_PATTERN.match(candidate_name.name)
        and candidate_name.suffix.lower() in _RV_SCRIPT_SUFFIXES
    )


def ensure_no_misplaced_evidence_helpers(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """拒绝把任何 RV 脚本放进代码变更。

    RV 脚本——取证的、临时 setup 的、被 ``evidence.json`` 命令引用的可复跑
    oracle——一律只能留在 worktree 本地的证据目录,没有例外。规则刻意不含任何
    需要执行器判断的条件:此前"PRD 要求的可复跑脚本可以提交到
    ``scripts/rv_evidence/``"这条豁免带着一个门禁从不校验的前提,执行器只要把
    一次性脚本起成合规名字就能进代码树(freshai issue-113 实证)。

    Args:
        worktree_path: 当前 Agent worktree 根目录。
        config: 提供证据目录位置。
        process_runner: 用于读取 worktree 变更的进程执行器。

    Raises:
        ValidationEvidenceError: 变更中存在错放的 RV 脚本时抛出。
    """
    misplaced_paths: list[str] = []
    for changed_path in list_changed_paths(worktree_path, process_runner):
        misplaced_paths.extend(
            expanded_path
            for expanded_path in expand_changed_path(worktree_path, changed_path)
            if is_misplaced_evidence_helper(expanded_path, config)
        )
    if not misplaced_paths:
        return
    misplaced_paths_text = ", ".join(sorted(set(misplaced_paths)))
    evidence_oracle_dir = f"{config.validation.evidence_dir.strip('/')}/{EVIDENCE_ORACLE_SUBDIR}/"
    # RV 脚本错放 = 取证脚本已进代码 diff，收尾层不得代为搬运，保持真失败分类。
    raise ValidationEvidenceError(
        f"RV scripts must never enter the code diff: {misplaced_paths_text}. "
        f"Move every one of them to `{evidence_oracle_dir}` — that is the only "
        "supported location, for evidence-capture helpers and for reproducible "
        "oracles referenced by an `evidence.json` command alike. Point the "
        "manifest commands at the moved paths and re-run them."
    )


def warn_legacy_evidence_helpers(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """对仓库中**已提交**的 RV 脚本发出告警,但不阻塞交付。

    :func:`ensure_no_misplaced_evidence_helpers` 只看本次变更,因此历史交付留在
    主干里的取证脚本永远不会被发现。本扫描让它们在日志里可见,清理可以另行
    安排——回溯硬失败会让存量违规的仓库下一个 PR 立刻被挡,代价不成比例。

    git 命令失败时静默跳过:这是告警路径,不应因此中断交付。
    """
    tracked_result = process_runner.run(
        ["git", "ls-files", "-z"],
        cwd=worktree_path,
        check=False,
    )
    if tracked_result.return_code != 0:
        return
    legacy_paths = sorted(
        tracked_path
        for tracked_path in tracked_result.stdout.split("\0")
        if tracked_path and is_misplaced_evidence_helper(tracked_path, config)
    )
    if not legacy_paths:
        return
    evidence_oracle_dir = f"{config.validation.evidence_dir.strip('/')}/{EVIDENCE_ORACLE_SUBDIR}/"
    _logger.warning(
        "%d RV helper script(s) are already committed in this repository and "
        "predate the evidence-directory rule: %s. They do not block this "
        "delivery, but they belong under `%s`; schedule a cleanup.",
        len(legacy_paths),
        ", ".join(legacy_paths),
        evidence_oracle_dir,
    )


def ensure_no_evidence_paths_in_changes(
    worktree_path: Path,
    config: AppConfig,
    process_runner: IProcessRunner,
) -> None:
    """Refuse to publish when evidence paths leak into the code diff.

    白名单语义随配置分两种：

    - 新约定（默认 ``tasks/evidence``）：``.gitignore`` 白名单只放行 ``*.md``
      文本报告进版本库，本守卫拦截其余证据产物（截图、录屏、JSON manifest、
      oracle 脚本等）被 ``git add -f`` 一类强制加入的情况，是发布前的双保险。
    - legacy/自定义目录：整目录排除语义不变，任何证据路径都拒绝（与历史
      版本逐字节一致）。
    """
    evidence_dir_prefix = config.validation.evidence_dir.strip("/") + "/"
    allow_markdown_reports = evidence_dir_uses_task_subdirs(config.validation.evidence_dir)
    leaked_paths = [
        changed_path
        for changed_path in list_changed_paths(worktree_path, process_runner)
        if changed_path.startswith(evidence_dir_prefix)
        and not (allow_markdown_reports and changed_path.lower().endswith(".md"))
    ]
    if leaked_paths:
        leaked_paths_text = ", ".join(sorted(set(leaked_paths)))
        raise RuntimeError(
            "Refusing to publish: validation evidence files must never enter "
            f"the code diff: {leaked_paths_text}"
        )
