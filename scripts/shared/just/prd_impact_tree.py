#!/usr/bin/env python3
"""Change Impact Tree 解析与分支触达进度测量。

PRD 的 ``Change Impact Tree`` 列出了本次改动预计触及的文件，是 PRD 里唯一一份
**可数、且执行途中就能观测**的清单——验收清单要到收尾才勾，从开工到验收之间
看板没有任何进度粒度。本模块把那棵树解析成文件节点，再与分支上**实际被改动过
的文件集**求交，得出 ``FILES`` 列的触达进度。

判据是"这个文件在本次分支上被碰过没有"，不是"文件在磁盘上存在没有"：模板仓库
历史 PRD 的影响树里 57% 的节点是 ``[修改]``，那些文件开工前就在磁盘上，用存在性
判定会让一条尚未动工的 PRD 直接显示过半完成。

**这是弱信号，不是验收信号。** 影响树自己声明"以上为起点而非穷尽清单"，而且
"文件被碰过"不等于"改对了"——executor 换一条更合理的实现路径，触达率反而会掉。
它与 ACTIVITY 列的 mtime 启发式同级，绝不能与 CHECKLIST / EVIDENCE / verifier
那条强信号链混为一谈。

无法判定的节点（跨仓库路径、``{a,b}.py`` 花括号展开、通配符、占位符文件名）不进
分母，而是单独计数由调用方显式披露，避免用一个假的分母换来好看的百分比。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 标题允许小节编号（``### 7.2 Change Impact Tree``）与前置限定语
# （``### Core Logic / Change Impact Tree``）；限定语只能出现在 ``/`` 之前，且整行
# 必须以树名结尾，不会误吞正文。
IMPACT_TREE_HEADING_PATTERN = re.compile(
    r"^#{2,4}\s+(?:[^\n]*?[/／]\s*)?(?:\d+(?:\.\d+)*[.、]?\s*)?"
    r"(?:Change Impact Tree|变更影响树)\s*$"
)

# 树枝节点：``├── name`` / ``└── name``。缩进层级按分支符号出现的列号推算。
TREE_NODE_PATTERN = re.compile(r"(?:├──|└──)\s*(?P<name>.+?)\s*$")
TREE_NODE_INDENT_WIDTH = 4

# 动作标记可能独占一行，也可能与【总结】同行（``[新增]【总结】……``），故不锚定行尾。
NODE_ACTION_PATTERN = re.compile(r"^[\s│├└─|]*\[(?P<action>新增|修改|删除)\]")

# 动作标记也可能直接跟在路径后面（``path/to/x.py [新增]``，下一行才是【总结】）。
# 这种写法在派生项目里同样常见，漏掉会把整个文件节点误判成目录。
INLINE_ACTION_SUFFIX_PATTERN = re.compile(r"\s*\[(?P<action>新增|修改|删除)\]\s*$")

# 行尾注解：``(新文件)``、``（如存在）``、``（或实际入口文件）`` 等，不属于路径。
TRAILING_ANNOTATION_PATTERN = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")

# 一行写多个文件时的分隔符：``a.bash / b.zsh``、``a.ts + b.ts``、``zh.json、en.json``。
# 顿号在中文 PRD 里最常见，且惯例不带空格，因此单独一支。
MULTI_PATH_SEPARATOR_PATTERN = re.compile(r"\s+[/+]\s+|\s*、\s*")

# 花括号展开、通配符、``<由 … 生成>`` 占位段：本地无法唯一定位，不进分母。
AMBIGUOUS_PATH_PATTERN = re.compile(r"[{}*<>]|XXXXXX")

# 树里的装饰字符，判断"下一行是否有实质内容"时需要先剥掉。
TREE_GLYPH_CHARS = " │|\t"

BASE_BRANCH_CANDIDATES = ("main", "master", "origin/main", "origin/master")


@dataclass(frozen=True)
class ImpactNode:
    """影响树里的一个文件节点。

    Attributes:
        candidate_paths (tuple[str, ...]): 该节点可能对应的仓库相对路径。树里既有
            ``scripts/shared/just/`` + ``prd_lock.py`` 这种"目录节点 + 裸文件名"，
            也有 ``Database`` + ``src/backend/.../models/`` + ``x.py`` 这种"层级标签 +
            真实目录 + 裸文件名"，单看一行无法区分标签与目录，因此祖先链的每个非空
            后缀都作为候选给出，由测量阶段用真实仓库状态择一。歧义节点为空元组。
        action (str): 动作标记，``新增`` / ``修改`` / ``删除``。
    """

    candidate_paths: tuple[str, ...]
    action: str


@dataclass(frozen=True)
class RepoPathIndex:
    """一组仓库相对路径的查找索引：文件全集 + 由它们推出的目录全集。

    路径全部来自 ``git ls-files`` / ``git diff``，**不做文件系统探测**。
    ``Path.is_dir()`` 在 macOS 与 Windows 上大小写不敏感：影响树里的层级标签
    ``Docs`` 会命中真实目录 ``docs/``，于是 ``Docs/mkdocs.yml`` 被判成"讲得通"进了
    分母，却永远匹配不上 git 报出的真实路径 ``mkdocs.yml``——而同一份 PRD 在
    Linux/CI 上会被判成"无法判定"。分母不该随操作系统变。

    Attributes:
        file_paths (frozenset[str]): 文件路径全集。
        directory_paths (frozenset[str]): 上述文件的所有祖先目录路径。
    """

    file_paths: frozenset[str]
    directory_paths: frozenset[str]

    @classmethod
    def from_file_paths(cls, file_paths_set: frozenset[str]) -> RepoPathIndex:
        """由文件路径集合构建索引，一次性展开全部祖先目录。

        Args:
            file_paths_set (frozenset[str]): 仓库相对文件路径集合。

        Returns:
            RepoPathIndex: 构建好的索引。
        """
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
        """候选路径是否命中一个目录（即其下确实有文件）。"""
        return candidate_path_text in self.directory_paths

    def is_plausible_path(self, candidate_path_text: str) -> bool:
        """候选路径在这个仓库里是否讲得通，用于把候选拼法收敛到一种。

        已存在的文件或目录对应 ``[修改]`` / ``[删除]``；父目录已存在的对应还没落盘的
        ``[新增]``；仓库根下的新文件同样讲得通。都不成立时多半是跨仓库路径或解析
        残渣，不该进分母。

        Args:
            candidate_path_text (str): 候选仓库相对路径。

        Returns:
            bool: 该路径是否是这个仓库里讲得通的目标。
        """
        if self.has_file(candidate_path_text) or self.has_directory(candidate_path_text):
            return True
        if "/" not in candidate_path_text:
            # 仓库根下的新文件；候选生成阶段已禁止裸文件名退回仓库根解释。
            return True
        return self.has_directory(candidate_path_text.rsplit("/", 1)[0])


@dataclass(frozen=True)
class ImpactProgress:
    """影响树的分支触达进度。

    Attributes:
        judgeable_total (int): 能够判定的节点数，即分母。
        touched_total (int): 其中在本次分支上确实被改动过的节点数。
        unresolvable_total (int): 本地无法判定因而被排除在分母之外的节点数。
    """

    judgeable_total: int
    touched_total: int
    unresolvable_total: int


def extract_impact_tree_lines(prd_text: str) -> tuple[str, ...]:
    """截取 ``Change Impact Tree`` 标题后的第一个围栏代码块。

    标题级别与编号都不固定（``### Change Impact Tree`` 与 ``### 7.2 Change Impact
    Tree`` 都出现过），因此标题匹配放宽到 2–4 级并允许小节编号。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        tuple[str, ...]: 代码块内的原始行；无影响树时返回空元组。
    """
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
        # 标题与围栏之间只允许说明性文字；碰到下一个标题说明这份 PRD 没写代码块。
        if not is_inside_fence and raw_line_text.startswith("#"):
            return ()
        if is_inside_fence:
            block_lines_list.append(raw_line_text)

    return tuple(block_lines_list)


def _strip_node_annotations(raw_node_name: str) -> str:
    """剥掉节点名尾部的注解与目录斜杠，留下路径本身。

    Args:
        raw_node_name (str): 树里读到的原始节点名。

    Returns:
        str: 去掉行尾括号注解与结尾 ``/`` 的节点名。
    """
    stripped_name_text = raw_node_name.strip()
    while True:
        shortened_name_text = TRAILING_ANNOTATION_PATTERN.sub("", stripped_name_text)
        if shortened_name_text == stripped_name_text:
            break
        stripped_name_text = shortened_name_text
    return stripped_name_text.rstrip("/").strip()


def _find_following_content_line(block_lines_list: list[str], node_line_index: int) -> str:
    """找到节点行之后第一行有实质内容的行，用于判断该节点是文件还是目录。

    树里的 ``│   │`` 只是竖线占位，不算内容。文件节点的下一行必定是动作标记，
    目录节点的下一行必定是另一个树枝节点——这个判据比"名字有没有结尾斜杠"稳，
    因为层级标签（``Infrastructure``）作为目录节点时并不带斜杠。

    Args:
        block_lines_list (list[str]): 影响树代码块的全部行。
        node_line_index (int): 当前节点所在行的下标。

    Returns:
        str: 下一行有内容的原文；没有时返回空串。
    """
    for raw_line_text in block_lines_list[node_line_index + 1 :]:
        if raw_line_text.strip(TREE_GLYPH_CHARS):
            return raw_line_text
    return ""


def _build_candidate_paths(
    ancestor_segments_tuple: tuple[str, ...], node_path_text: str
) -> tuple[str, ...]:
    """给出一个文件节点可能对应的仓库相对路径。

    祖先链里可能混着**层级标签**（``Database``、``Infrastructure``）和**真实目录**
    （``src/backend/.../models/``），单看一行分不出哪个是哪个，因此把祖先链的每一个
    非空后缀都作为候选：``Database`` + ``src/.../models`` + ``x.py`` 会同时给出
    ``Database/src/.../models/x.py`` 与 ``src/.../models/x.py``，由测量阶段用真实仓库
    状态择一。

    后缀刻意不含空串——裸文件名不能退回仓库根解释：跨仓库的 ``zata-ops/`` +
    ``pyproject.toml`` 会撞上本仓库根的同名文件，把一个本该报"无法判定"的节点算成
    已判定。只有名字自带 ``/`` 的节点才可能本身就是完整的仓库相对路径。

    多给候选只会让"可判定"判得更宽（分母变大、进度显得更保守），不会让"已触达"判错：
    触达要求候选与 git 报出的路径**精确相等**。

    Args:
        ancestor_segments_tuple (tuple[str, ...]): 祖先目录节点，由浅到深。
        node_path_text (str): 已清洗的节点名。

    Returns:
        tuple[str, ...]: 候选路径，按"前缀最长优先"排列并去重；歧义节点返回空元组。
    """
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
    """把 PRD 的 Change Impact Tree 解析成文件节点列表。

    Args:
        prd_text (str): PRD 文件全文。

    Returns:
        tuple[ImpactNode, ...]: 文件节点；无影响树或树内无文件节点时返回空元组。
    """
    block_lines_list = list(extract_impact_tree_lines(prd_text))
    if not block_lines_list:
        return ()

    # 代码块首行是仓库根（``.`` 或仓库名），不带树枝符号，天然不会进入这张前缀表。
    prefix_by_depth_dict: dict[int, str] = {}
    impact_nodes_list: list[ImpactNode] = []
    for line_index, raw_line_text in enumerate(block_lines_list):
        node_match = TREE_NODE_PATTERN.search(raw_line_text)
        if node_match is None:
            continue

        node_depth = node_match.start() // TREE_NODE_INDENT_WIDTH + 1
        raw_node_name_text = node_match.group("name")

        # 两种动作标记写法都要认：跟在路径后面（``x.py [新增]``）与独占下一行。
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
        # 一行多文件时，后续文件继承第一个文件所在的目录：``a/b/c.bash / c.zsh``
        # 的第二个文件写的是裸名，按祖先前缀拼会丢掉 ``a/b``。
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


def _run_git_lines(worktree_path: Path, *git_arguments: str) -> tuple[str, ...]:
    """在指定 worktree 内执行 git 命令并按行返回标准输出。

    Args:
        worktree_path (Path): 执行目录。
        *git_arguments (str): git 子命令与参数。

    Returns:
        tuple[str, ...]: 非空输出行；命令失败或 git 不可用时返回空元组。
    """
    try:
        completed_git_process = subprocess.run(
            ["git", "-C", str(worktree_path), *git_arguments],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except OSError:
        return ()
    if completed_git_process.returncode != 0:
        return ()
    return tuple(
        stripped_line_text
        for stripped_line_text in (
            raw_line_text.strip() for raw_line_text in completed_git_process.stdout.splitlines()
        )
        if stripped_line_text
    )


def resolve_base_commit(worktree_path: Path) -> str | None:
    """求本次分支与主线的分叉点，作为"改动从哪里开始"的基准。

    Args:
        worktree_path (Path): 分支所在的 worktree 目录。

    Returns:
        str | None: 分叉点 commit；没有任何主线候选分支时返回 ``None``。
    """
    for base_branch_name in BASE_BRANCH_CANDIDATES:
        merge_base_lines_tuple = _run_git_lines(
            worktree_path, "merge-base", "HEAD", base_branch_name
        )
        if merge_base_lines_tuple:
            return merge_base_lines_tuple[0]
    return None


def collect_branch_touched_paths(worktree_path: Path) -> frozenset[str]:
    """收集本次分支上实际被改动过的文件路径。

    覆盖三类改动：已提交的、已改但未提交的、以及尚未 ``git add`` 的新文件。执行
    中途的 executor 往往还没提交，只看 commit 会让进度长期停在 0。

    Args:
        worktree_path (Path): 分支所在的 worktree 目录。

    Returns:
        frozenset[str]: 仓库相对路径集合；无法确定基准时返回空集合。
    """
    base_commit_text = resolve_base_commit(worktree_path)
    if base_commit_text is None:
        return frozenset()

    # ``git diff <base>`` 比较基准与**工作区**，因此已提交与未提交的改动一并覆盖。
    tracked_changed_paths_tuple = _run_git_lines(
        worktree_path, "diff", "--name-only", base_commit_text
    )
    untracked_paths_tuple = _run_git_lines(
        worktree_path, "ls-files", "--others", "--exclude-standard"
    )
    return frozenset(tracked_changed_paths_tuple) | frozenset(untracked_paths_tuple)


def collect_repo_file_paths(worktree_path: Path) -> frozenset[str]:
    """收集 git 视野里这个仓库的全部文件路径（已跟踪 + 未跟踪、不含忽略项）。

    Args:
        worktree_path (Path): 分支所在的 worktree 目录。

    Returns:
        frozenset[str]: 仓库相对路径集合。
    """
    return frozenset(_run_git_lines(worktree_path, "ls-files")) | frozenset(
        _run_git_lines(worktree_path, "ls-files", "--others", "--exclude-standard")
    )


def measure_impact_progress(
    impact_nodes_tuple: tuple[ImpactNode, ...],
    touched_path_index: RepoPathIndex,
    repo_path_index: RepoPathIndex,
) -> ImpactProgress:
    """把影响树节点与分支触达集求交，得出触达进度。

    Args:
        impact_nodes_tuple (tuple[ImpactNode, ...]): ``parse_impact_tree`` 的结果。
        touched_path_index (RepoPathIndex): 本次分支改动过的路径索引。
        repo_path_index (RepoPathIndex): 仓库全部路径索引，用于判定候选是否讲得通。

    Returns:
        ImpactProgress: 触达进度；无法判定的节点只计数、不进分母。
    """
    judgeable_node_count = 0
    touched_node_count = 0
    unresolvable_node_count = 0
    for impact_node in impact_nodes_tuple:
        if not impact_node.candidate_paths:
            unresolvable_node_count += 1
            continue

        # 目录节点（``src/backend/core/fcl_sync`` [新增]）也要认：树里常常只点到目录，
        # 只做文件级精确比较会让它永远判不出触达，哪怕目录下十几个文件都改了。
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


def measure_branch_impact_progress(prd_text: str, worktree_path: Path) -> ImpactProgress | None:
    """解析影响树并测量该分支的触达进度，供看板直接调用。

    Args:
        prd_text (str): PRD 文件全文（应取分支副本）。
        worktree_path (Path): 分支所在的 worktree 目录。

    Returns:
        ImpactProgress | None: 触达进度；PRD 没有影响树或树内没有文件节点时
        返回 ``None``，由调用方渲染成"无此信号"。
    """
    impact_nodes_tuple = parse_impact_tree(prd_text)
    if not impact_nodes_tuple:
        return None

    return measure_impact_progress(
        impact_nodes_tuple,
        RepoPathIndex.from_file_paths(collect_branch_touched_paths(worktree_path)),
        RepoPathIndex.from_file_paths(collect_repo_file_paths(worktree_path)),
    )
