"""守护 PRD 人工审查清单打开器的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_review.py``（judge：``just prd review``）。
核心不变量：

1. **人工审查清单按分支副本优先解析。** 审查发生在 PRD 合并回主线之前，证据
   目录此时只存在于 worktree 里；按主仓库路径找会扑空或读到旧副本（与看板
   EVIDENCE 列同一理由）。worktree 按 PRD slug 与分支名匹配，解析必须与
   ``prd_status`` 共用同一套规则，两处不得分叉。
2. **清单槽位优先于证据报告，且两个槽位独立兜底。** 清单缺失不应连带丢弃
   分支上已有的报告，反之亦然；命中顺序必须是清单 → 报告。
3. **清单槽位内交互 HTML 优先于静态 Markdown。** 两者内容一致，HTML（逐步
   按钮作答、内嵌截图）是人审会话的首选呈现面；扩展名优先级由调用方传入
   ``prd_status.find_named_evidence_file``，命名模式仍由后者单一事实源维护，
   两处不得分叉。
4. **没有可打开文件时必须非零退出。** 静默成功（退出 0 却没打开任何东西）会
   让人以为"没有待人工审查的事"，而实际可能只是证据目录还没建——本地审查
   入口的失败必须显式。
5. **``--print`` 只打印路径、绝不调用系统打开器。** 测试、CI 与脚本消费都
   依赖这条；破坏它会让无 GUI 环境下的调用挂起或弹窗。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# prd_review.py / prd_status.py / prd_lock.py 不是包的一部分，import 前需把
# 它们所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import prd_review  # noqa: E402

_PRD_FILENAME = "P1-FEAT-20260916-212206-demo-feature.md"
_PRD_STEM = "P1-FEAT-20260916-212206-demo-feature"
_BRANCH_NAME = "demo-feature"


def _build_fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    """构造可见的最小仓库骨架：repo/tasks/pending/<prd>。"""
    repo_root_path = tmp_path / "repo"
    pending_dir_path = repo_root_path / "tasks" / "pending"
    pending_dir_path.mkdir(parents=True)
    prd_path = pending_dir_path / _PRD_FILENAME
    prd_path.write_text("# Demo PRD\n", encoding="utf-8")
    return repo_root_path, prd_path


def _write_evidence_file(base_root: Path, filename: str, content: str = "# file\n") -> Path:
    """在 base_root/tasks/evidence/<prd-stem>/ 下写一个证据文件。"""
    evidence_dir_path = base_root / "tasks" / "evidence" / _PRD_STEM
    evidence_dir_path.mkdir(parents=True, exist_ok=True)
    target_path = evidence_dir_path / filename
    target_path.write_text(content, encoding="utf-8")
    return target_path


def test_checklist_resolves_from_worktree_branch_first(tmp_path: Path) -> None:
    """分支副本优先：worktree 与主仓库都有清单时，命中 worktree 的那份。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)
    _write_evidence_file(repo_root_path, "human-review-checklist.md", "# 主仓库旧副本\n")
    worktree_root_path = tmp_path / "worktrees" / _BRANCH_NAME
    branch_checklist_path = _write_evidence_file(
        worktree_root_path, "human-review-checklist.md", "# 分支新副本\n"
    )

    resolved_target_path = prd_review.resolve_review_target(
        prd_path, repo_root_path, [(_BRANCH_NAME, worktree_root_path)]
    )

    assert resolved_target_path == branch_checklist_path
    candidate_dirs_list = prd_review.resolve_evidence_candidate_dirs(
        prd_path, repo_root_path, [(_BRANCH_NAME, worktree_root_path)]
    )
    assert candidate_dirs_list == [
        worktree_root_path / "tasks" / "evidence" / _PRD_STEM,
        repo_root_path / "tasks" / "evidence" / _PRD_STEM,
    ]


def test_checklist_falls_back_to_main_repo_without_worktree_match(tmp_path: Path) -> None:
    """无分支名匹配的 worktree 时，清单从主仓库解析。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)
    main_checklist_path = _write_evidence_file(repo_root_path, "human-review-checklist.md")

    resolved_target_path = prd_review.resolve_review_target(prd_path, repo_root_path, [])

    assert resolved_target_path == main_checklist_path


def test_worktree_without_evidence_dir_falls_back_to_main_repo(tmp_path: Path) -> None:
    """worktree 匹配但分支上还没有证据目录时，回退主仓库副本。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)
    main_checklist_path = _write_evidence_file(repo_root_path, "human-review-checklist.md")
    worktree_root_path = tmp_path / "worktrees" / _BRANCH_NAME
    worktree_root_path.mkdir(parents=True)

    resolved_target_path = prd_review.resolve_review_target(
        prd_path, repo_root_path, [(_BRANCH_NAME, worktree_root_path)]
    )

    assert resolved_target_path == main_checklist_path


def test_review_target_prefers_checklist_and_falls_back_to_report(tmp_path: Path) -> None:
    """槽位独立兜底：清单优先；清单缺失时用分支上的报告，反之亦然。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)
    main_checklist_path = _write_evidence_file(repo_root_path, "human-review-checklist.md")
    worktree_root_path = tmp_path / "worktrees" / _BRANCH_NAME
    branch_report_path = _write_evidence_file(worktree_root_path, "evidence-report.md")
    worktree_branches_list = [(_BRANCH_NAME, worktree_root_path)]

    # 清单在主仓库、报告在分支：清单优先，槽位各自命中不同目录。
    assert (
        prd_review.resolve_review_target(prd_path, repo_root_path, worktree_branches_list)
        == main_checklist_path
    )

    main_checklist_path.unlink()
    assert (
        prd_review.resolve_review_target(prd_path, repo_root_path, worktree_branches_list)
        == branch_report_path
    )


def test_review_target_prefers_interactive_html_over_markdown(tmp_path: Path) -> None:
    """清单槽位内交互 HTML 优先于静态 Markdown：并存时命中 html，缺失时回退 md。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)
    markdown_checklist_path = _write_evidence_file(
        repo_root_path, "human-review-checklist.md", "# 静态底稿\n"
    )
    html_checklist_path = _write_evidence_file(
        repo_root_path, "human-review-checklist.html", "<!doctype html>\n"
    )

    resolved_target_path = prd_review.resolve_review_target(prd_path, repo_root_path, [])
    assert resolved_target_path == html_checklist_path

    html_checklist_path.unlink()
    assert prd_review.resolve_review_target(prd_path, repo_root_path, []) == markdown_checklist_path


def test_review_target_is_none_when_nothing_exists(tmp_path: Path) -> None:
    """证据目录里既没有清单也没有报告时，解析结果为空。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)

    assert prd_review.resolve_review_target(prd_path, repo_root_path, []) is None


def test_resolve_prd_path_accepts_repo_relative_path(tmp_path: Path) -> None:
    """PRD 参数支持仓库根相对路径，不要求 cwd 恰好是仓库根。"""
    repo_root_path, prd_path = _build_fake_repo(tmp_path)

    resolved_prd_path = prd_review.resolve_prd_path(
        f"tasks/pending/{_PRD_FILENAME}", repo_root_path
    )
    assert resolved_prd_path == prd_path
    assert prd_review.resolve_prd_path("tasks/pending/missing.md", repo_root_path) is None


def test_main_print_mode_never_calls_system_opener(tmp_path: Path, monkeypatch, capsys) -> None:
    """--print 打印解析路径并返回 0，绝不调用系统打开器。"""
    repo_root_path, _prd_path = _build_fake_repo(tmp_path)
    checklist_path = _write_evidence_file(repo_root_path, "human-review-checklist.md")

    def _forbidden_opener(target_path: Path) -> bool:
        raise AssertionError(f"--print 不得调用系统打开器：{target_path}")

    monkeypatch.chdir(repo_root_path)
    monkeypatch.setattr(prd_review, "open_in_system_viewer", _forbidden_opener)

    exit_code = prd_review.main([f"tasks/pending/{_PRD_FILENAME}", "--print"])

    assert exit_code == 0
    assert str(checklist_path) in capsys.readouterr().out


def test_main_exits_nonzero_when_prd_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    """PRD 文件不存在时非零退出并给出用法提示。"""
    repo_root_path, _prd_path = _build_fake_repo(tmp_path)
    monkeypatch.chdir(repo_root_path)

    exit_code = prd_review.main(["tasks/pending/missing.md"])

    assert exit_code == 1
    assert "PRD 文件不存在" in capsys.readouterr().out


def test_main_exits_nonzero_when_nothing_to_open(tmp_path: Path, monkeypatch, capsys) -> None:
    """没有清单也没有报告时非零退出，并列出证据目录现状。"""
    repo_root_path, _prd_path = _build_fake_repo(tmp_path)
    _write_evidence_file(repo_root_path, "verification-plan.md")
    monkeypatch.chdir(repo_root_path)

    exit_code = prd_review.main([f"tasks/pending/{_PRD_FILENAME}"])

    assert exit_code == 1
    captured_output_text = capsys.readouterr().out
    assert "还没有人工审查清单或证据报告" in captured_output_text
    assert "verification-plan.md" in captured_output_text


def test_script_runs_standalone_via_subprocess(tmp_path: Path) -> None:
    """脚本以内联方式执行时同目录 import 可用（不依赖测试的 sys.path 注入）。"""
    repo_root_path, _prd_path = _build_fake_repo(tmp_path)
    checklist_path = _write_evidence_file(repo_root_path, "human-review-checklist.md")
    script_path = _JUST_SCRIPTS_PATH / "prd_review.py"

    completed_process = subprocess.run(
        [sys.executable, str(script_path), f"tasks/pending/{_PRD_FILENAME}", "--print"],
        cwd=repo_root_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed_process.returncode == 0
    assert str(checklist_path) in completed_process.stdout
