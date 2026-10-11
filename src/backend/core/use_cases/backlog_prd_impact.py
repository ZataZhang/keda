"""§7.2 Change Impact Tree 的 backend 移植：解析 + 分支触达测量（只读）。

本模块把命令行看板 :mod:`scripts.shared.just.prd_impact_tree` 的解析语义**正向移植**
到 backend——console 跨仓库、各仓 scripts 副本版本不同步，运行期 import 被服务仓库的
工具目录不可接受。为防漂移，测试用 importlib 加载 CLI 实现做逐字段一致性对照（rv-3）。

判据与命令行完全一致：节点是否「在本次分支上被改动过」，而不是「文件在磁盘上存在没有」。
无法判定的节点（跨仓库路径、``{a,b}.py``、通配符、占位符）不进分母、单独计数披露，
绝不编造一个假分母换取好看的百分比。git 调用一律经 :class:`IProcessRunner` 端口。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from backend.core.shared.interfaces.agent_runner import IProcessRunner

# ── 以下解析常量与函数逐字移植自 prd_impact_tree.py，保持与 CLI 同口径 ──────────

IMPACT_TREE_HEADING_PATTERN = re.compile(
    r"^#{2,4}\s+(?:[^\n]*?[/／]\s*)?(?:\d+(?:\.\d+)*[.、]?\s*)?"
    r"(?:Change Impact Tree|变更影响树)\s*$"
)
TREE_NODE_PATTERN = re.compile(r"(?:├──|└──)\s*(?P<name>.+?)\s*$")
TREE_NODE_INDENT_WIDTH = 4
NODE_ACTION_PATTERN = re.compile(r"^[\s│├└─|]*\[(?P<action>新增|修改|删除)\]")
INLINE_ACTION_SUFFIX_PATTERN = re.compile(r"\s*\[(?P<action>新增|修改|删除)\]\s*$")
TRAILING_ANNOTATION_PATTERN = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")
MULTI_PATH_SEPARATOR_PATTERN = re.compile(r"\s+[/+]\s+|\s*、\s*")
AMBIGUOUS_PATH_PATTERN = re.compile(r"[{}*<>]|XXXXXX")
TREE_GLYPH_CHARS = " │|\t"
BASE_BRANCH_CANDIDATES = ("main", "master", "origin/main", "origin/master")


@dataclass(frozen=True)
class ImpactNode:
    """影响树里的一个文件节点（与 CLI 的 ``ImpactNode`` 同结构）。"""

    candidate_paths: tuple[str, ...]
    action: str


@dataclass(frozen=True)
class RepoPathIndex:
    """仓库相对路径的查找索引：文件全集 + 由其推出的目录全集。"""

    file_paths: frozenset[str]
    directory_paths: frozenset[str]

    @classmethod
    def from_file_paths(cls, file_paths_set: frozenset[str]) -> RepoPathIndex:
        """由文件路径集合构建索引，一次性展开全部祖先目录。"""
        directory_paths_set: set[str] = set()
        for file_path_text in file_paths_set:
            path_segments_list = file_path_text.split("/")
            for segment_count in range(1, len(path_segments_list)):
                directory_paths_set.add("/".join(path_segments_list[:segment_count]))
        return cls(file_paths=file_paths_set, directory_paths=frozenset(directory_paths_set))

    def has_file(self, candidate_path_text: str) -> bool:
        """候选路径是否命中一个文件。"""
        return candidate_path_text in self.file_paths

    def has_directory(self, candidate_path_text: str) -> bool:
        """候选路径是否命中一个目录（其下确实有文件）。"""
        return candidate_path_text in self.directory_paths

    def is_plausible_path(self, candidate_path_text: str) -> bool:
        """候选路径在本仓库里是否讲得通（用于把候选拼法收敛到一种）。"""
        if self.has_file(candidate_path_text) or self.has_directory(candidate_path_text):
            return True
        if "/" not in candidate_path_text:
            return True
        return self.has_directory(candidate_path_text.rsplit("/", 1)[0])


@dataclass(frozen=True)
class ImpactProgress:
    """影响树的分支触达进度（与 CLI 的 ``ImpactProgress`` 同结构）。"""

    judgeable_total: int
    touched_total: int
    unresolvable_total: int


def extract_impact_tree_lines(prd_text: str) -> tuple[str, ...]:
    """截取 ``Change Impact Tree`` 标题后的第一个围栏代码块（移植自 CLI）。"""
    raw_lines_list = prd_text.splitlines()
    heading_line_index = -1
    for line_index, raw_line_text in enumerate(raw_lines_list):
        if IMPACT_TREE_HEADING_PATTERN.match(raw_line_text):
            heading_line_index = line_index
            break
    if heading_line_index < 0:
        return ()

    block_lines_list: list[str] = []
    is_inside_fence = False
    for raw_line_text in raw_lines_list[heading_line_index + 1 :]:
        if raw_line_text.lstrip().startswith("```"):
            if is_inside_fence:
                break
            is_inside_fence = True
            continue
        if not is_inside_fence and raw_line_text.startswith("#"):
            return ()
        if is_inside_fence:
            block_lines_list.append(raw_line_text)

    return tuple(block_lines_list)


def _strip_node_annotations(raw_node_name: str) -> str:
    """剥掉节点名尾部的注解与目录斜杠，留下路径本身。"""
    stripped_name_text = raw_node_name.strip()
    while True:
        shortened_name_text = TRAILING_ANNOTATION_PATTERN.sub("", stripped_name_text)
        if shortened_name_text == stripped_name_text:
            break
        stripped_name_text = shortened_name_text
    return stripped_name_text.rstrip("/").strip()


def _find_following_content_line(block_lines_list: list[str], node_line_index: int) -> str:
    """找到节点行之后第一行有实质内容的行，用于判断该节点是文件还是目录。"""
    for raw_line_text in block_lines_list[node_line_index + 1 :]:
        if raw_line_text.strip(TREE_GLYPH_CHARS):
            return raw_line_text
    return ""


def _build_candidate_paths(
    ancestor_segments_tuple: tuple[str, ...], node_path_text: str
) -> tuple[str, ...]:
    """给出一个文件节点可能对应的仓库相对路径（祖先前缀逐层展开、去重）。"""
    if not node_path_text or AMBIGUOUS_PATH_PATTERN.search(node_path_text):
        return ()

    candidate_paths_list: list[str] = []
    for start_index in range(len(ancestor_segments_tuple)):
        prefix_text = "/".join(ancestor_segments_tuple[start_index:])
        candidate_paths_list.append(f"{prefix_text}/{node_path_text}")
    if "/" in node_path_text or not ancestor_segments_tuple:
        candidate_paths_list.append(node_path_text)

    return tuple(dict.fromkeys(candidate_paths_list))


def parse_impact_tree(prd_text: str) -> tuple[ImpactNode, ...]:
    """把 PRD 的 Change Impact Tree 解析成文件节点列表（移植自 CLI）。"""
    block_lines_list = list(extract_impact_tree_lines(prd_text))
    if not block_lines_list:
        return ()

    prefix_by_depth_dict: dict[int, str] = {}
    impact_nodes_list: list[ImpactNode] = []
    for line_index, raw_line_text in enumerate(block_lines_list):
        node_match = TREE_NODE_PATTERN.search(raw_line_text)
        if node_match is None:
            continue

        node_depth = node_match.start() // TREE_NODE_INDENT_WIDTH + 1
        raw_node_name_text = node_match.group("name")

        inline_action_match = INLINE_ACTION_SUFFIX_PATTERN.search(raw_node_name_text)
        if inline_action_match is not None:
            raw_node_name_text = raw_node_name_text[: inline_action_match.start()]

        cleaned_node_text = _strip_node_annotations(raw_node_name_text)
        if not cleaned_node_text:
            continue

        action_match = inline_action_match
        if action_match is None:
            following_line_text = _find_following_content_line(block_lines_list, line_index)
            action_match = NODE_ACTION_PATTERN.match(following_line_text)
        if action_match is None:
            prefix_by_depth_dict = {
                depth: prefix_text
                for depth, prefix_text in prefix_by_depth_dict.items()
                if depth < node_depth
            }
            prefix_by_depth_dict[node_depth] = cleaned_node_text
            continue

        ancestor_segments_tuple = tuple(
            prefix_by_depth_dict[depth]
            for depth in sorted(prefix_by_depth_dict)
            if depth < node_depth and prefix_by_depth_dict[depth] != "."
        )
        sibling_directory_text = ""
        for part_index, raw_part_text in enumerate(
            MULTI_PATH_SEPARATOR_PATTERN.split(cleaned_node_text)
        ):
            single_path_text = raw_part_text.strip()
            effective_segments_tuple = ancestor_segments_tuple
            if part_index > 0 and sibling_directory_text and "/" not in single_path_text:
                effective_segments_tuple = (sibling_directory_text,)
            elif part_index == 0 and "/" in single_path_text:
                sibling_directory_text = single_path_text.rsplit("/", 1)[0]

            impact_nodes_list.append(
                ImpactNode(
                    candidate_paths=_build_candidate_paths(
                        effective_segments_tuple, single_path_text
                    ),
                    action=action_match.group("action"),
                )
            )

    return tuple(impact_nodes_list)


def measure_impact_progress(
    impact_nodes_tuple: tuple[ImpactNode, ...],
    touched_path_index: RepoPathIndex,
    repo_path_index: RepoPathIndex,
) -> ImpactProgress:
    """把影响树节点与分支触达集求交（移植自 CLI；目录节点也算触达）。"""
    judgeable_node_count = 0
    touched_node_count = 0
    unresolvable_node_count = 0
    for impact_node in impact_nodes_tuple:
        if not impact_node.candidate_paths:
            unresolvable_node_count += 1
            continue

        is_touched = any(
            touched_path_index.has_file(candidate_path_text)
            or touched_path_index.has_directory(candidate_path_text)
            for candidate_path_text in impact_node.candidate_paths
        )
        if is_touched:
            judgeable_node_count += 1
            touched_node_count += 1
            continue

        if any(
            repo_path_index.is_plausible_path(candidate_path_text)
            for candidate_path_text in impact_node.candidate_paths
        ):
            judgeable_node_count += 1
            continue

        unresolvable_node_count += 1

    return ImpactProgress(
        judgeable_total=judgeable_node_count,
        touched_total=touched_node_count,
        unresolvable_total=unresolvable_node_count,
    )


# ── git 采集：CLI 用 subprocess，本模块改为经 IProcessRunner 端口 ───────────────


class ImpactMeasureStatus(str, Enum):
    """分支触达测量这一步的可判别结果。"""

    AVAILABLE = "available"
    NO_BASE = "no_base"
    UNAVAILABLE = "unavailable"


def _git_probe(
    process_runner: IProcessRunner, worktree_path: Path, *git_arguments: str
) -> tuple[int | None, tuple[str, ...]]:
    """在 worktree 内跑只读 git 命令，返回 ``(退出码或 None, 非空输出行)``。

    ``None`` 表示命令根本没跑起来（git 不可用 / 目录不可读 / 端口抛异常），调用方据此
    区分「执行失败」与「命令成功但目标不存在」——前者不可折算为零触达。
    """
    command = ["git", "-C", str(worktree_path), *git_arguments]
    try:
        probe_result = process_runner.run(command, cwd=worktree_path, check=False)
    except Exception:  # noqa: BLE001 - 端口执行异常一律视为「跑不起来」，交给调用方标不可用
        return None, ()
    stripped_lines = tuple(
        line_text.strip() for line_text in probe_result.stdout.splitlines() if line_text.strip()
    )
    return probe_result.return_code, stripped_lines


def _resolve_base_commit(
    process_runner: IProcessRunner, worktree_path: Path
) -> tuple[str | None, bool]:
    """依次对 ``main``/``master``/``origin/*`` 求 merge-base，返回 ``(基准或 None, 是否成功跑过 git)``。

    没有任何候选基准能跑成功（全部「跑不起来」）→ 第二个值为假，代表 git 不可用；
    跑成功但四个候选都没有分叉点 → ``(None, True)``，代表「确认无可比较基准」。
    """
    any_command_ran = False
    for base_branch_name in BASE_BRANCH_CANDIDATES:
        return_code, base_lines = _git_probe(
            process_runner, worktree_path, "merge-base", "HEAD", base_branch_name
        )
        if return_code is None:
            continue
        any_command_ran = True
        if return_code == 0 and base_lines:
            return base_lines[0], True
    return None, any_command_ran


def _collect_lines(
    process_runner: IProcessRunner, worktree_path: Path, *git_arguments: str
) -> tuple[frozenset[str], bool]:
    """采集一组仓库相对路径；命令没跑起来或非零退出时返回 ``(空集合, False)``。"""
    return_code, collected_lines = _git_probe(process_runner, worktree_path, *git_arguments)
    if return_code is None or return_code != 0:
        return frozenset(), False
    return frozenset(collected_lines), True


def measure_branch_impact(
    process_runner: IProcessRunner,
    worktree_path: Path,
    impact_nodes_tuple: tuple[ImpactNode, ...],
) -> tuple[ImpactMeasureStatus, ImpactProgress | None]:
    """在有效 merge-base 下测量分支触达进度，并把「无基准」与「执行失败」分开报告。

    覆盖已提交 / 未提交 / 未跟踪三类改动（与 CLI ``collect_branch_touched_paths`` 同集合）。
    git 子命令真正跑不起来时返回 ``unavailable``，绝不以空触达集合折算 ``0/y``。

    Args:
        process_runner: 只读命令端口。
        worktree_path: 分支所在的 worktree 目录。
        impact_nodes_tuple: :func:`parse_impact_tree` 的结果。

    Returns:
        ``(status, progress)``：``available`` 带进度；``no_base`` / ``unavailable`` 进度为 ``None``。
    """
    base_commit_text, git_ran = _resolve_base_commit(process_runner, worktree_path)
    if not git_ran:
        return ImpactMeasureStatus.UNAVAILABLE, None
    if base_commit_text is None:
        return ImpactMeasureStatus.NO_BASE, None

    tracked_changed_set, diff_ok = _collect_lines(
        process_runner, worktree_path, "diff", "--name-only", base_commit_text
    )
    untracked_set, untracked_ok = _collect_lines(
        process_runner, worktree_path, "ls-files", "--others", "--exclude-standard"
    )
    tracked_set, tracked_ok = _collect_lines(process_runner, worktree_path, "ls-files")
    if not (diff_ok and untracked_ok and tracked_ok):
        return ImpactMeasureStatus.UNAVAILABLE, None

    touched_set = tracked_changed_set | untracked_set
    repo_file_set = tracked_set | untracked_set
    return (
        ImpactMeasureStatus.AVAILABLE,
        measure_impact_progress(
            impact_nodes_tuple,
            RepoPathIndex.from_file_paths(touched_set),
            RepoPathIndex.from_file_paths(repo_file_set),
        ),
    )
