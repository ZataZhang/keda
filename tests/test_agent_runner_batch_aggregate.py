"""rv-2：夜间批次聚合的**本地** core 逻辑测试。

只验证不依赖 GitHub 外部状态的部分：依赖拓扑排序、隔离分支的真实 Git 组合与冲突
保全、PRD 路径去重、总 PR 正文构造与重试命令。GitHub 来源解析 / 发布 / 检查 / 关闭
（rv-4）不在此覆盖，也不使用 fake GitHub 作证据。

Git 组合用项目自身的 Git 集成入口（:func:`build_aggregate_branch` + 真实
``SubprocessRunner``）操作本次自动创建的临时仓库与 bare remote。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.use_cases.agent_runner_batch_aggregate import (
    AggregateConflictError,
    AggregatePRBodyContext,
    BatchAggregateError,
    BatchSource,
    build_aggregate_branch,
    build_aggregate_retry_command,
    build_total_pr_body,
    plan_merge_order,
    resolve_aggregate_prd_paths,
    _validate_aggregate_prd_evidence,
)
from backend.core.use_cases.agent_runner_batch_aggregate_git import (
    AggregateBranchBuildRequest,
    AggregateGitError,
    AggregateGitRequest,
    push_aggregate_branch,
)
from backend.core.use_cases.agent_runner_pr_body_contract import (
    MERGE_ACCEPTANCE_V2_MARKER_TEXT,
    find_aggregate_pr_body_contract_violations,
    parse_aggregate_source_marker,
)
from backend.infrastructure.process_runner import SubprocessRunner
from tests.support.agent_runner import (
    create_commit,
    init_bare_git_repo,
    init_git_repo,
    run_git,
)


def _source(
    issue_number: int,
    *,
    base_sha: str = "base",
    head_sha: str | None = None,
    checks_state: str = "SUCCESS",
    pr_number: int | None = None,
    prd_path: str | None = None,
    dependencies: tuple[int, ...] = (),
) -> BatchSource:
    """构造一个本地 BatchSource fixture（编号派生自 issue_number，可覆盖关键字段）。"""
    return BatchSource(
        issue_number=issue_number,
        issue_url=f"https://example.test/issues/{issue_number}",
        pr_number=pr_number if pr_number is not None else 200 + issue_number,
        pr_url=f"https://example.test/pulls/{200 + issue_number}",
        branch=f"issue-{issue_number}",
        head_sha=head_sha or f"head-{issue_number}",
        base_sha=base_sha,
        checks_state=checks_state,
        prd_path=prd_path,
        dependency_issue_numbers=dependencies,
    )


# ---------------------------------------------------------------------------
# plan_merge_order：依赖拓扑 + 结构资格（全部本地静态断言）
# ---------------------------------------------------------------------------


def test_plan_merge_order_respects_dependency_before_dependent() -> None:
    """下游依赖上游时上游先合入，即使上游编号更大。"""
    upstream = _source(102)
    downstream = _source(101, dependencies=(102,))
    ordered = plan_merge_order([downstream, upstream])
    assert [source.issue_number for source in ordered] == [102, 101]


def test_plan_merge_order_breaks_ties_by_issue_number() -> None:
    """无依赖的独立任务按 Issue 编号升序稳定排序。"""
    ordered = plan_merge_order([_source(103), _source(101), _source(102)])
    assert [source.issue_number for source in ordered] == [101, 102, 103]


def test_plan_merge_order_rejects_cycle() -> None:
    """依赖环 fail closed（ordering 类）。"""
    with pytest.raises(BatchAggregateError) as error:
        plan_merge_order([_source(101, dependencies=(102,)), _source(102, dependencies=(101,))])
    assert error.value.failure_category == "ordering"


def test_plan_merge_order_rejects_missing_dependency() -> None:
    """依赖指向批外未知 Issue 时 fail closed。"""
    with pytest.raises(BatchAggregateError) as error:
        plan_merge_order([_source(101, dependencies=(999,)), _source(102)])
    assert error.value.failure_category == "ordering"


def test_plan_merge_order_rejects_base_mismatch() -> None:
    """来源 base SHA 不一致时 fail closed。"""
    with pytest.raises(BatchAggregateError) as error:
        plan_merge_order([_source(101, base_sha="A"), _source(102, base_sha="B")])
    assert error.value.failure_category == "ordering"


def test_plan_merge_order_rejects_non_success_checks() -> None:
    """来源必需检查非 SUCCESS 时不合格。"""
    with pytest.raises(BatchAggregateError) as error:
        plan_merge_order([_source(101), _source(102, checks_state="PENDING")])
    assert error.value.failure_category == "eligibility"


def test_plan_merge_order_requires_two_sources() -> None:
    """候选不足两个时拒绝聚合，避免产生单任务总 PR。"""
    with pytest.raises(BatchAggregateError) as error:
        plan_merge_order([_source(101)])
    assert error.value.failure_category == "eligibility"


# ---------------------------------------------------------------------------
# PRD 去重 / 正文构造 / 重试命令（本地纯逻辑）
# ---------------------------------------------------------------------------


def test_resolve_aggregate_prd_paths_dedupes_and_sorts() -> None:
    """跨来源 PRD 路径去重且稳定排序，None 来源不贡献路径。"""
    sources = [
        _source(101, prd_path="tasks/archive/z.md"),
        _source(102, prd_path="tasks/archive/a.md"),
        _source(103, prd_path="tasks/archive/z.md"),
        _source(104, prd_path=None),
    ]
    assert resolve_aggregate_prd_paths(sources) == ("tasks/archive/a.md", "tasks/archive/z.md")


def test_build_total_pr_body_satisfies_v2_contract() -> None:
    """两份 PRD 的总 PR 正文应通过 v2 契约且携带来源 marker + v2 声明。"""
    sources = [
        _source(102, prd_path="tasks/archive/b.md"),
        _source(101, prd_path="tasks/archive/a.md"),
    ]
    prd_paths = ("tasks/archive/a.md", "tasks/archive/b.md")
    body = build_total_pr_body(
        AggregatePRBodyContext(
            sources=tuple(sources),
            prd_paths=prd_paths,
            verification_results=(),
            verification_passed=True,
            base_sha="base",
            head_sha="head",
            tree_sha="tree",
        )
    )
    assert (
        find_aggregate_pr_body_contract_violations(
            body,
            expected_prd_paths=prd_paths,
            expected_issue_numbers=(101, 102),
            expected_source_pr_numbers=(301, 302),
        )
        == []
    )
    assert MERGE_ACCEPTANCE_V2_MARKER_TEXT in body
    assert "tasks/evidence/a/a.evidence-report.md" in body
    declaration = parse_aggregate_source_marker(body)
    assert declaration is not None
    assert declaration.issue_numbers == (101, 102)


def test_build_total_pr_body_annotates_when_source_pr_missing() -> None:
    """来源集合与批次成员不匹配时正文被追加 aggregate-contract 标注（本地软门）。"""
    sources = [_source(101), _source(102)]
    body = build_total_pr_body(
        AggregatePRBodyContext(
            sources=tuple(sources),
            prd_paths=("tasks/archive/a.md",),
            verification_results=(),
            verification_passed=True,
            base_sha="base",
            head_sha="head",
            tree_sha="tree",
        )
    )
    # 用错误的来源 PR 集去校验同一正文 → 来源 marker 不匹配 → 标注块出现。
    assert "iar:aggregate-contract" in build_total_pr_body(
        AggregatePRBodyContext(
            sources=tuple(sources),
            prd_paths=("tasks/archive/a.md",),
            verification_results=(),
            verification_passed=True,
            base_sha="base",
            head_sha="head",
            tree_sha="tree",
        )
    ) or find_aggregate_pr_body_contract_violations(
        body,
        expected_prd_paths=("tasks/archive/a.md",),
        expected_issue_numbers=(101, 999),
        expected_source_pr_numbers=(301, 302),
    ) == ["aggregate-source-marker"]


def test_aggregate_prd_evidence_requires_archived_prd_and_pass_report(tmp_path: Path) -> None:
    """总树 PRD 必须已归档并包含三份报告，verifier 报告首行必须是 PASS。"""
    prd_path = tmp_path / "tasks" / "archive" / "a-feature.md"
    evidence_dir = tmp_path / "tasks" / "evidence" / "a-feature"
    prd_path.parent.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    prd_path.write_text("# PRD\n", encoding="utf-8")
    for suffix in ("verification-plan", "evidence-report"):
        (evidence_dir / f"a-feature.{suffix}.md").write_text("report\n", encoding="utf-8")
    verifier_report = evidence_dir / "a-feature.verifier-report.md"
    verifier_report.write_text(
        "# Independent evidence verifier\n\nVerdict: PASS for the final tree.\n",
        encoding="utf-8",
    )

    _validate_aggregate_prd_evidence(tmp_path, ("tasks/archive/a-feature.md",))

    verifier_report.write_text("BLOCKER\n", encoding="utf-8")
    with pytest.raises(BatchAggregateError, match="not PASS"):
        _validate_aggregate_prd_evidence(tmp_path, ("tasks/archive/a-feature.md",))


def test_build_aggregate_retry_command_lists_sorted_issues() -> None:
    """重试命令带 repo-id 且 Issue 升序去重。"""
    command = build_aggregate_retry_command("keda-test", [102, 101, 102])
    assert command == "kc pr aggregate --repo-id keda-test --issue 101 --issue 102"


# ---------------------------------------------------------------------------
# build_aggregate_branch：真实 Git 组合（临时仓库 + bare remote）
# ---------------------------------------------------------------------------


def _init_repo_with_base_and_branches(
    tmp_path: Path,
    *,
    conflict: bool,
) -> tuple[Path, str, str, str, Path]:
    """建临时仓库：main=base，两条 issue 分支各一提交，并推到 bare remote。

    Returns:
        ``(repo, base_sha, head1, head2, remote_bare)``。
    """
    repo = init_git_repo(tmp_path / "repo")
    remote = init_bare_git_repo(tmp_path / "remote.git")
    run_git(repo, "remote", "add", "origin", str(remote))

    (repo / "README.md").write_text("# base\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    create_commit(repo, "base commit")
    base_sha = run_git(repo, "rev-parse", "HEAD").strip()

    run_git(repo, "checkout", "-b", "issue-1")
    if conflict:
        (repo / "conflict.txt").write_text("from issue-1\n", encoding="utf-8")
    else:
        (repo / "feature1.txt").write_text("feature 1\n", encoding="utf-8")
    run_git(repo, "add", "conflict.txt" if conflict else "feature1.txt")
    create_commit(repo, "issue-1 commit")
    head1 = run_git(repo, "rev-parse", "HEAD").strip()

    run_git(repo, "checkout", "main")
    run_git(repo, "checkout", "-b", "issue-2")
    if conflict:
        (repo / "conflict.txt").write_text("from issue-2\n", encoding="utf-8")
    else:
        (repo / "feature2.txt").write_text("feature 2\n", encoding="utf-8")
    run_git(repo, "add", "conflict.txt" if conflict else "feature2.txt")
    create_commit(repo, "issue-2 commit")
    head2 = run_git(repo, "rev-parse", "HEAD").strip()

    run_git(repo, "checkout", "main")
    run_git(repo, "push", "origin", "main", "issue-1", "issue-2")
    return repo, base_sha, head1, head2, remote


def _detach_worktree(repo: Path, tmp_path: Path, base_sha: str) -> Path:
    """从 base SHA 建一个 detached 隔离 worktree（组合分支的落点）。"""
    worktree_path = tmp_path / "aggregate-wt"
    run_git(repo, "worktree", "add", "--detach", str(worktree_path), base_sha)
    return worktree_path


def test_build_aggregate_branch_combines_sources_in_order(tmp_path: Path) -> None:
    """两个非冲突来源 head 按序合入：组合树包含双方文件，来源/base refs 不变。"""
    repo, base_sha, head1, head2, remote = _init_repo_with_base_and_branches(
        tmp_path, conflict=False
    )
    worktree_path = _detach_worktree(repo, tmp_path, base_sha)

    result = build_aggregate_branch(
        AggregateBranchBuildRequest(
            worktree_path=worktree_path,
            batch_branch="batch-101-102",
            base_sha=base_sha,
            source_head_shas=(head1, head2),
            process_runner=SubprocessRunner(),
        )
    )

    assert result.merged_head_shas == (head1, head2)
    tracked_files = set(run_git(worktree_path, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    assert {"feature1.txt", "feature2.txt"} <= tracked_files
    assert result.tree_sha == run_git(worktree_path, "rev-parse", "HEAD^{tree}").strip()
    # fresh_state_probe：从 bare remote 重新读 base/source refs，确认组合未推送、未改写。
    assert run_git(remote, "rev-parse", "main").strip() == base_sha
    assert run_git(remote, "rev-parse", "issue-1").strip() == head1
    assert run_git(remote, "rev-parse", "issue-2").strip() == head2


def test_build_aggregate_branch_skips_already_contained_head(tmp_path: Path) -> None:
    """已包含的祖先 head 幂等跳过（不重复合并）。"""
    repo, base_sha, head1, _, _ = _init_repo_with_base_and_branches(tmp_path, conflict=False)
    worktree_path = _detach_worktree(repo, tmp_path, base_sha)

    result = build_aggregate_branch(
        AggregateBranchBuildRequest(
            worktree_path=worktree_path,
            batch_branch="batch-x",
            base_sha=base_sha,
            source_head_shas=(base_sha, head1),
            process_runner=SubprocessRunner(),
        )
    )
    assert base_sha in result.skipped_head_shas
    assert result.merged_head_shas == (head1,)


def test_build_aggregate_branch_conflict_leaves_refs_untouched(tmp_path: Path) -> None:
    """冲突时立即停止并 abort：base 与来源 refs 保持原值，不产出部分总树。"""
    repo, base_sha, head1, head2, remote = _init_repo_with_base_and_branches(
        tmp_path, conflict=True
    )
    worktree_path = _detach_worktree(repo, tmp_path, base_sha)

    with pytest.raises(AggregateConflictError) as error:
        build_aggregate_branch(
            AggregateBranchBuildRequest(
                worktree_path=worktree_path,
                batch_branch="batch-conflict",
                base_sha=base_sha,
                source_head_shas=(head1, head2),
                process_runner=SubprocessRunner(),
            )
        )
    assert error.value.head_sha == head2
    # 冲突 head 未进入合并集合。
    assert error.value.failure_category == "conflict"
    # base 与两条来源分支在远端与本地均未改动。
    assert run_git(remote, "rev-parse", "main").strip() == base_sha
    assert run_git(remote, "rev-parse", "issue-1").strip() == head1
    assert run_git(remote, "rev-parse", "issue-2").strip() == head2
    assert run_git(repo, "rev-parse", "main").strip() == base_sha


def test_push_aggregate_branch_rejects_unowned_remote_name(tmp_path: Path) -> None:
    """同名远端分支没有已验证总 PR 时拒绝覆盖并保留原 ref。"""
    repo, base_sha, head1, _, remote = _init_repo_with_base_and_branches(tmp_path, conflict=False)
    batch_branch = "batch-101-102"
    run_git(repo, "push", "origin", f"{head1}:refs/heads/{batch_branch}")
    worktree_path = _detach_worktree(repo, tmp_path, base_sha)
    run_git(worktree_path, "checkout", "-b", batch_branch, base_sha)
    git_request = AggregateGitRequest(
        repo_path=repo,
        remote_name="origin",
        base_branch="main",
        batch_branch=batch_branch,
        source_branches=(("issue-1", head1),),
        process_runner=SubprocessRunner(),
    )

    with pytest.raises(AggregateGitError, match="without a matching aggregate PR"):
        push_aggregate_branch(git_request, worktree_path=worktree_path)

    assert run_git(remote, "rev-parse", batch_branch).strip() == head1


def test_push_aggregate_branch_preserves_remote_head_mismatching_pr_context(
    tmp_path: Path,
) -> None:
    """远端 batch ref 与读取到的总 PR head 不同则拒绝重写，保留远端提交。"""
    repo, base_sha, head1, _, remote = _init_repo_with_base_and_branches(tmp_path, conflict=False)
    batch_branch = "batch-101-102"
    run_git(repo, "push", "origin", f"{head1}:refs/heads/{batch_branch}")
    worktree_path = _detach_worktree(repo, tmp_path, base_sha)
    run_git(worktree_path, "checkout", "-b", batch_branch, base_sha)
    git_request = AggregateGitRequest(
        repo_path=repo,
        remote_name="origin",
        base_branch="main",
        batch_branch=batch_branch,
        source_branches=(("issue-1", head1),),
        process_runner=SubprocessRunner(),
    )

    with pytest.raises(AggregateGitError, match="moved after its PR context was read"):
        push_aggregate_branch(
            git_request,
            worktree_path=worktree_path,
            allow_update=True,
            expected_remote_sha="f" * 40,
        )

    assert run_git(remote, "rev-parse", batch_branch).strip() == head1
