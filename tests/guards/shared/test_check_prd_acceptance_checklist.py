"""守护 PRD 验收清单 pre-commit hook 的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``hooks/shared/check_prd_acceptance_checklist.py``。核心不变量：

1. **归档只代表执行侧交付完成：``Human-Confirmed`` 分组内的空框不拦提交，其余空框
   照旧拦截。** 人的确认是归档**之后**的验收记录；hook 若仍把人属空框当作未完成项，
   执行侧就永远无法与代码同批归档，或者被迫替人代勾——两种结果都是伪造验收。
2. **人属分组的作用范围按"包含它的任一层分组是否人属"判定。** 嵌套子标题不会把人属项
   踢出去，同级或更高级的标题才关闭分组；标签前缀匹配且大小写不敏感；整行加粗的标签
   按三级分组处理；围栏代码块里的内容既不算空框也不改变分组；``[~]`` 是 runner 门禁，
   不算未勾。判定用例表与 skill 的契约解析共用一份（``prd_checklist_scope_cases.py``）。
3. **缺验收清单章节要显式报告**，不能因为"没有空框"而静默通过。
4. **检查范围不变：** 仓库根 ``tasks/`` 下的活跃 PRD，加上本次提交暂存的新入
   ``tasks/archive/`` 的 PRD；``tasks/pending/``（执行中的工作区）与未暂存的归档文件
   不在 pre-commit 范围内。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK_PATH = REPO_ROOT / "hooks" / "shared" / "check_prd_acceptance_checklist.py"

# 用例表所在目录由 pytest 的 rootdir 机制通常已在 sys.path 上；显式放入以免依赖导入模式。
_SHARED_GUARDS_PATH = Path(__file__).resolve().parent
if str(_SHARED_GUARDS_PATH) not in sys.path:
    sys.path.insert(0, str(_SHARED_GUARDS_PATH))

from prd_checklist_scope_cases import (  # noqa: E402
    CHECKLIST_SCOPE_PARAMS,
    open_item_labels,
    prd_with_checklist_body,
)

_ACTIVE_PRD_PATH = "tasks/P2-FEAT-20260101-000000-sample.md"
_PENDING_PRD_PATH = "tasks/pending/P2-FEAT-20260101-000000-sample.md"
_ARCHIVED_PRD_PATH = "tasks/archive/P2-FEAT-20260101-000000-sample.md"

_ONLY_HUMAN_ITEM_OPEN_BODY = "- [x] exec-1\n\n### Human-Confirmed\n\n- [ ] human-1\n"
_EXECUTION_ITEM_OPEN_BODY = "- [ ] exec-1\n\n### Human-Confirmed\n\n- [ ] human-1\n"


def load_hook_module() -> ModuleType:
    """按文件路径加载 hook 模块。

    Returns:
        ModuleType: 加载好的 hook 模块。
    """

    module_spec = importlib.util.spec_from_file_location("prd_acceptance_hook", HOOK_PATH)
    assert module_spec is not None
    assert module_spec.loader is not None
    hook_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(hook_module)
    return hook_module


HOOK = load_hook_module()


def _init_git_repository(repo_path: Path) -> None:
    """在临时目录里初始化 git 仓库（hook 用 ``git diff --cached`` 找暂存的归档）。"""

    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _stage_file(repo_path: Path, relative_path: str) -> None:
    """把文件加入暂存区。"""

    subprocess.run(
        ["git", "add", relative_path],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _write_prd(repo_path: Path, relative_path: str, checklist_body: str) -> None:
    """按仓库内相对路径写一份只有验收清单可变的 PRD（按需建目录）。"""

    prd_path = repo_path / relative_path
    prd_path.parent.mkdir(parents=True, exist_ok=True)
    prd_path.write_text(prd_with_checklist_body(checklist_body), encoding="utf-8")


def _run_hook_main(monkeypatch: pytest.MonkeyPatch, repo_path: Path, *relative_paths: str) -> int:
    """把 hook 的仓库根指向临时仓库，以给定的文件参数运行 ``main()``。"""

    monkeypatch.setattr(HOOK, "_repo_root", lambda: repo_path)
    monkeypatch.setattr(sys, "argv", ["check_prd_acceptance_checklist.py", *relative_paths])
    return HOOK.main()


@pytest.mark.parametrize(("checklist_body", "expected_open_labels"), CHECKLIST_SCOPE_PARAMS)
def test_only_executor_owed_open_items_are_reported(
    checklist_body: str, expected_open_labels: list[str]
) -> None:
    """人属分组内的空框不报，执行侧欠的空框照报；作用范围的判定逐形态钉死。"""

    prd_text = prd_with_checklist_body(checklist_body)

    reported_items = HOOK._unchecked_items_in_acceptance_section(prd_text)

    assert open_item_labels(reported_items) == expected_open_labels


def test_reported_line_number_points_at_the_open_item_in_the_file() -> None:
    """报告里的行号是 1-based，且指向原文里那一行空框，作者能直接定位。"""

    prd_text = prd_with_checklist_body(
        "### Human-Confirmed\n\n- [ ] human-1\n\n### Execution\n\n- [ ] exec-1\n"
    )
    open_item_line_number = prd_text.splitlines().index("- [ ] exec-1") + 1

    assert HOOK._unchecked_items_in_acceptance_section(prd_text) == [
        (open_item_line_number, "- [ ] exec-1")
    ]


def test_missing_checklist_section_is_reported_instead_of_passing_silently() -> None:
    """没有验收清单章节时要点名报告，不能因为没有空框而放行。"""

    prd_text = "# PRD: x\n\n## 8. Delivery Dependencies\n\n- [ ] not-a-checklist\n"

    assert HOOK._unchecked_items_in_acceptance_section(prd_text) == [
        (-1, "Missing Acceptance Checklist section")
    ]


def test_candidates_are_root_level_active_prds_plus_staged_archive_entries(
    tmp_path: Path,
) -> None:
    """检查范围：根目录活跃 PRD + 暂存的新入归档；pending、未暂存归档与想法收件箱都不在内。"""

    staged_archive_name = "P2-FEAT-20260101-000001-staged.md"
    unstaged_archive_name = "P2-FEAT-20260101-000002-unstaged.md"
    for relative_path in (
        _ACTIVE_PRD_PATH,
        _PENDING_PRD_PATH,
        f"tasks/archive/{staged_archive_name}",
        f"tasks/archive/{unstaged_archive_name}",
        "tasks/inbox/ideas.md",
    ):
        _write_prd(tmp_path, relative_path, _ONLY_HUMAN_ITEM_OPEN_BODY)
    staged_archive_paths = {Path(f"tasks/archive/{staged_archive_name}")}
    expected_relative_paths = [_ACTIVE_PRD_PATH, f"tasks/archive/{staged_archive_name}"]

    discovered_paths = HOOK._candidate_prd_paths(
        tmp_path, [], staged_archive_prd_paths=staged_archive_paths
    )
    provided_paths = HOOK._candidate_prd_paths(
        tmp_path,
        [
            tmp_path / relative_path
            for relative_path in (
                _ACTIVE_PRD_PATH,
                _PENDING_PRD_PATH,
                f"tasks/archive/{staged_archive_name}",
                f"tasks/archive/{unstaged_archive_name}",
            )
        ],
        staged_archive_prd_paths=staged_archive_paths,
    )

    assert [path.relative_to(tmp_path).as_posix() for path in discovered_paths] == (
        expected_relative_paths
    )
    assert [path.relative_to(tmp_path).as_posix() for path in provided_paths] == (
        expected_relative_paths
    )


def test_main_lets_a_prd_through_when_only_human_confirmed_items_are_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """归档语义变更的核心：只剩人属空框的 PRD 不拦提交。"""

    _init_git_repository(tmp_path)
    _write_prd(tmp_path, _ACTIVE_PRD_PATH, _ONLY_HUMAN_ITEM_OPEN_BODY)

    exit_code = _run_hook_main(monkeypatch, tmp_path, _ACTIVE_PRD_PATH)

    assert exit_code == 0
    assert "human-1" not in capsys.readouterr().out


def test_main_blocks_a_prd_with_open_execution_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """执行侧欠的空框照旧拦截；同一份 PRD 里的人属空框不跟着被报。"""

    _init_git_repository(tmp_path)
    _write_prd(tmp_path, _ACTIVE_PRD_PATH, _EXECUTION_ITEM_OPEN_BODY)

    exit_code = _run_hook_main(monkeypatch, tmp_path, _ACTIVE_PRD_PATH)

    printed_output = capsys.readouterr().out
    assert exit_code == 1
    assert "exec-1" in printed_output
    assert "human-1" not in printed_output


def test_main_checks_an_archived_prd_only_once_it_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """归档文件只在本次提交把它暂存进来时才检查：存量归档不会被反复翻出来重判。"""

    _init_git_repository(tmp_path)
    _write_prd(tmp_path, _ARCHIVED_PRD_PATH, _EXECUTION_ITEM_OPEN_BODY)

    assert _run_hook_main(monkeypatch, tmp_path, _ARCHIVED_PRD_PATH) == 0

    _stage_file(tmp_path, _ARCHIVED_PRD_PATH)
    assert _run_hook_main(monkeypatch, tmp_path, _ARCHIVED_PRD_PATH) == 1


def test_main_does_not_check_pending_prds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``tasks/pending/`` 是执行中的工作区：清单没勾完是常态，pre-commit 不拦。"""

    _init_git_repository(tmp_path)
    _write_prd(tmp_path, _PENDING_PRD_PATH, _EXECUTION_ITEM_OPEN_BODY)

    exit_code = _run_hook_main(monkeypatch, tmp_path, _PENDING_PRD_PATH)

    assert exit_code == 0
    assert capsys.readouterr().out == ""
