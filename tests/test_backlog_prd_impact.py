"""影响树 backend 移植（backlog_prd_impact）单测 + 与命令行的 rv-3 一致性对照。

判据是「分支碰没碰」，且「无 merge-base」「git 跑不起来」必须与「零触达」分开。
移植的解析/求交语义用 importlib 直接加载 CLI 实现（`scripts/shared/just/prd_impact_tree.py`）
在**真实 git 分支**上逐字段对照，防止 backend 与看板漂移；一致性只要求有效基准样例。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import CommandResult
from backend.core.use_cases.backlog_prd_impact import (
    ImpactMeasureStatus,
    RepoPathIndex,
    extract_impact_tree_lines,
    measure_branch_impact,
    measure_impact_progress,
    parse_impact_tree,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
IMPACT_TREE_TEXT = """# PRD: Demo

## 7.2 Change Impact Tree

```text
repo-root
├── src
│   └── backend
│       ├── module_a.py [修改]
│       └── untouched.py [新增]
├── new_file.py [新增]
├── untracked.py [新增]
├── {a,b}.py [修改]
└── foreign
    └── other_repo.py [新增]
```
"""


class _SubprocessRunner:
    """把只读 git 命令交给真实 subprocess 执行，包装成 CommandResult。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, command, *, cwd, check=True, **_kwargs):  # noqa: ANN001, D102
        self.calls.append(list(command))
        completed = subprocess.run(  # noqa: S603
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(
            command=tuple(command),
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _load_cli_module():
    script_dir = SOURCE_ROOT / "scripts/shared/just"
    sys.path.insert(0, str(script_dir))
    module_spec = importlib.util.spec_from_file_location(
        "prd_impact_tree_cli", script_dir / "prd_impact_tree.py"
    )
    assert module_spec is not None and module_spec.loader is not None
    cli_module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = cli_module
    module_spec.loader.exec_module(cli_module)
    return cli_module


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=str(repo_path), check=True, capture_output=True, text=True
    )


def _write(repo_path: Path, relpath: str, content: str) -> None:
    target = repo_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


@pytest.fixture
def impact_repo(tmp_path: Path) -> Path:
    """真实 git 仓库：main 基线上有 module_a.py 与 src/backend；feature 分支改动部分节点。"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _git(repo_path, "init", "-q", "-b", "main")
    _git(repo_path, "config", "user.email", "t@example.com")
    _git(repo_path, "config", "user.name", "Tester")
    _write(repo_path, "src/backend/module_a.py", "orig\n")
    _write(repo_path, "README.md", "readme\n")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-qm", "base")
    _git(repo_path, "checkout", "-qb", "feature")
    # 触碰：修改已跟踪 + 新增已 add + 未跟踪新文件
    _write(repo_path, "src/backend/module_a.py", "changed\n")
    _write(repo_path, "new_file.py", "brand new\n")
    _git(repo_path, "add", "new_file.py")
    _write(repo_path, "untracked.py", "not added\n")
    return repo_path


def test_parse_impact_tree_parity_with_cli() -> None:
    """backend 解析出的节点候选与动作与 CLI 逐字段一致。"""
    cli_module = _load_cli_module()
    backend_nodes = parse_impact_tree(IMPACT_TREE_TEXT)
    cli_nodes = cli_module.parse_impact_tree(IMPACT_TREE_TEXT)
    assert [(node.candidate_paths, node.action) for node in backend_nodes] == [
        (node.candidate_paths, node.action) for node in cli_nodes
    ]


def test_extract_impact_tree_lines_parity_with_cli() -> None:
    """围栏块抽取口径一致（同一 PRD 文本）。"""
    cli_module = _load_cli_module()
    assert extract_impact_tree_lines(IMPACT_TREE_TEXT) == cli_module.extract_impact_tree_lines(
        IMPACT_TREE_TEXT
    )


def test_measure_branch_impact_matches_cli_on_real_git(impact_repo: Path) -> None:
    """有效 merge-base 的真实分支：backend 与 CLI 的 touched/judgeable/unresolvable 完全一致。"""
    cli_module = _load_cli_module()
    cli_progress = cli_module.measure_branch_impact_progress(IMPACT_TREE_TEXT, impact_repo)
    assert cli_progress is not None

    backend_nodes = parse_impact_tree(IMPACT_TREE_TEXT)
    status, backend_progress = measure_branch_impact(
        _SubprocessRunner(), impact_repo, backend_nodes
    )

    assert status is ImpactMeasureStatus.AVAILABLE
    assert backend_progress is not None
    assert backend_progress.touched_total == cli_progress.touched_total
    assert backend_progress.judgeable_total == cli_progress.judgeable_total
    assert backend_progress.unresolvable_total == cli_progress.unresolvable_total


def test_touched_nodes_are_picked_up(impact_repo: Path) -> None:
    """已提交改动、已 add 新文件、未跟踪新文件都算触达。"""
    backend_nodes = parse_impact_tree(IMPACT_TREE_TEXT)
    _status, progress = measure_branch_impact(_SubprocessRunner(), impact_repo, backend_nodes)
    assert progress is not None
    assert progress.touched_total == 3
    touched = _touched_set(impact_repo)
    assert {"src/backend/module_a.py", "new_file.py", "untracked.py"} <= touched


def _touched_set(repo_path: Path) -> set[str]:
    base = subprocess.run(  # noqa: S603
        ["git", "merge-base", "HEAD", "main"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    changed = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", base],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    untracked = subprocess.run(  # noqa: S603
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {*changed, *untracked}


def test_unresolvable_nodes_excluded_from_denominator(impact_repo: Path) -> None:
    """花括号展开等无法判定节点只计数、不进 judgeable 分母。"""
    backend_nodes = parse_impact_tree(IMPACT_TREE_TEXT)
    _status, progress = measure_branch_impact(_SubprocessRunner(), impact_repo, backend_nodes)
    assert progress is not None
    # {a,b}.py（歧义）与 foreign/other_repo.py（跨仓库、讲不通）都算 unresolvable
    assert progress.unresolvable_total == 2
    assert progress.judgeable_total == progress.touched_total + 1  # 仅 untouched.py 可判未触达


def _scripted_runner(responses: dict[tuple[str, ...], CommandResult]):
    class _Scripted:
        def run(self, command, *, cwd, check=True, **_kwargs):  # noqa: ANN001
            key = tuple(command[3:])  # 去掉 ["git", "-C", path]
            if key in responses:
                return responses[key]
            return CommandResult(command=tuple(command), return_code=0, stdout="", stderr="")

    return _Scripted()


def test_no_base_reports_no_base_not_zero(tmp_path: Path) -> None:
    """git 能跑但没有任何 merge-base 候选 → no_base（不可比较），不是零触达。"""
    responses = {
        ("merge-base", "HEAD", base): CommandResult(
            command=("git",), return_code=1, stdout="", stderr="no such branch"
        )
        for base in ("main", "master", "origin/main", "origin/master")
    }
    runner = _scripted_runner(responses)
    nodes = parse_impact_tree(IMPACT_TREE_TEXT)
    status, progress = measure_branch_impact(runner, tmp_path, nodes)
    assert status is ImpactMeasureStatus.NO_BASE
    assert progress is None


def test_git_failure_reports_unavailable_not_zero(tmp_path: Path) -> None:
    """git 根本没跑起来（全部命令异常）→ unavailable，绝不折算 0 触达。"""

    class _Boom:
        def run(self, command, *, cwd, check=True, **_kwargs):  # noqa: ANN001
            raise OSError("git missing")

    nodes = parse_impact_tree(IMPACT_TREE_TEXT)
    status, progress = measure_branch_impact(_Boom(), tmp_path, nodes)
    assert status is ImpactMeasureStatus.UNAVAILABLE
    assert progress is None


def test_measure_impact_progress_directory_node_counts_as_touched() -> None:
    """目录节点：其下有改动文件即算触达（照 CLI 语义）。"""
    tree_text = "## 7.2 Change Impact Tree\n\n```text\nroot\n" "└── src/backend [新增]\n```\n"
    nodes = parse_impact_tree(tree_text)
    touched = RepoPathIndex.from_file_paths(frozenset({"src/backend/x.py"}))
    repo_index = RepoPathIndex.from_file_paths(frozenset({"src/backend/x.py"}))
    progress = measure_impact_progress(nodes, touched, repo_index)
    assert progress.touched_total == 1
    assert progress.judgeable_total == 1


def test_cli_surface_unchanged() -> None:
    """本功能只移植、不改 CLI：命令行模块仍可独立加载并工作。"""
    cli_module = _load_cli_module()
    assert callable(cli_module.measure_branch_impact_progress)
    assert cli_module.BASE_BRANCH_CANDIDATES == ("main", "master", "origin/main", "origin/master")
