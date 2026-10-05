# 证据报告 — stack sequencing（P1-FEAT-20261005-161632）

本目录的证据全部可在本地复现，不依赖对真实 GitHub 仓库的写操作。

## 复现命令

```bash
cd <worktree>
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=src \
  .venv/bin/python tasks/evidence/P1-FEAT-20261005-161632-dependent-prd-sequencing-strategy/gen_evidence.py
```

`gen_evidence.py` 走真实代码入口：真实 PRD 解析（`parse_delivery_dependencies` /
`_resolve_dependencies`）、真实 marker 物化（`format_dependency_marker` /
`parse_dependency_marker`）、真实 `create_or_reuse_worktree` → 真实 `iar worktree
create/path` CLI + 真实 git 仓库（临时目录，非 mock）。

## oracle 映射

| oracle | 状态 | 证据 |
|---|---|---|
| rv-1 stack 下游基于上游分支 fork，祖先可证 | ✅ 通过 | 下方 transcript：`merge-base --is-ancestor` 成功，worktree HEAD 直接等于上游 tip |
| rv-2 上游合并后下游 PR retarget 到 main 并收敛 | ⚠️ 自动化等价覆盖 | 无真实 GitHub 合并权限下无法端到端复现；以 `tests/test_agent_runner_merge_queue.py::test_stack_downstream_converges_after_upstream_merged`（GitHub 客户端 mock、断言 `set_pull_request_base(pr, "main")` 与随后 rebase）作为自动化 oracle。真实 PR 合并腿留给 verifier / 人工 |
| rv-3 via-main 场景 base 落后被刷新 | ✅ 通过 | 下方 transcript：本地 main 停在 `efa0ad4e`，远端已到 `b2718475`，worktree HEAD == 远端 main 且能看到上游新增文件 |
| rv-4 stack 中途合并队列不合并下游 | ✅ 通过 | `tests/test_agent_runner_merge_queue.py::test_stack_downstream_skipped_until_upstream_merged` |
| rv-5 stack 等待态且不消耗配额 | ⚠️ 部分 | 等待态有判别测试（`test_stack_blocked_when_upstream_branch_not_pushed`）；"不消耗配额"仅由控制流结构保证（ready 循环 `continue` 在 `processed_count += 1` 之前），无专门配额断言 |
| rv-6 非法 Sequence fail fast | ✅ 通过 | `test_invalid_sequence_rejected` + `test_stack_multiple_upstreams_rejected` |
| rv-7 非 stack 零回归 | ✅ 通过 | `test_via_main_ignores_branch_readiness`、`test_via_main_issue_never_retargets_base`、`test_resolve_fork_base_via_main_*` |

## verifier 复核（第 1 轮）与整改

独立 verifier 复核冻结树（`git diff HEAD -- src tests` sha256 `9dfa768d…`）后给出 FAIL，指出一个 blocker 与若干 minor，均已处理：

- **blocker（已修）**：收敛 rebase 后，`_diff_paths` 仍用 rebase 前的 `pr_context.head_sha`，`origin/main...<stale>` 会把上游改动算进禁改扫描，上游若动了 `.env.*` 等禁路径会误判下游 `blocked_forbidden`。修复：rebase 成功后刷新 `pr_context`；回归测试 `test_convergence_refreshes_head_before_forbidden_scan`（用陈旧 head 的 diff 命中 `.env.example`、新 head 干净来判别）。
- **major（披露）**：上游多提交 squash 后 rebase 可能 add/add 冲突（走既有 agent 路径），rv-2 干净 diff 不是自动达成的，已在 docs 与本文披露。
- **minor（已修）**：`_resolve_dependencies` 返回注解 3 元组；显式 `mode="stack"` marker 无 `Sequence` 字段时不再被丢弃；stack 等待态下 best-effort 查询上游 failed/blocked 标签以提示（`test_stack_upstream_failure_surfaces_warning`）。
- **minor（接受并披露）**：via-main 从 `origin/<base>` fork 而非快进本地 base（行为等价且不污染本地分支）；rv-5 配额无专门断言（见上）。

## negative control

- rv-1 负控制：把 stack 误写成 via-main（无 marker）→ `_resolve_fork_base` 返回 `origin/main` 而非上游分支，见 `test_resolve_fork_base_via_main_refreshes_and_prefers_remote`。
- rv-4 负控制：非 stack Issue 不被跳过、不 retarget，见 `test_via_main_issue_never_retargets_base`。
- rv-6 负控制：`Sequence: stack` 声明多个上游时 fail fast，见 `test_stack_multiple_upstreams_rejected`。

## transcript（rv-1）

```text
=== rv-1: stack downstream forks from the upstream branch ===
  parsed: gate='hard' issues=(42,) sequence='stack'
  marker: <!-- iar:depends-on #42 mode="stack" -->
  worktree: <tmp>/local/.iar-worktrees/issue-9001
  $ git rev-parse HEAD
    1b745ec9af58b189696463bdfcba9e8f81b867a5
  $ git merge-base --is-ancestor 1b745ec9... 1b745ec9...
  PROOF rv-1: upstream tip 1b745ec9 is ancestor of 1b745ec9
```

## transcript（rv-3）

```text
=== rv-3: via-main base is refreshed before forking ===
  local main before fork attempt: efa0ad4e
  remote main:                    b2718475
  local main after (unchanged):   efa0ad4e
  $ git rev-parse HEAD
    b27184757b7070442d240a48e5218e2d0550bc87
  PROOF rv-3: worktree HEAD b2718475 == remote main; sees merged file
```

## 未覆盖 / 局限

- 无 PNG 截图：rv-1/rv-3 的判别值是 git 祖先关系与文件内容，文本 transcript 即为高保真证据；截图只是呈递形式。
- rv-2 的真实 GitHub retarget+rebase 未在本环境端到端复现（无对目标仓库的写权限）；自动化等价覆盖 + 人工/verifier 真实 PR 复核。

## Appendix: gen_evidence.py 完整源码

（`tasks/evidence/**` 下只有 `*.md` 入库；本脚本原样内嵌于此，复制到任意路径后按上文命令运行即可复现。）

```python
"""Reproducible real-git evidence generator for the stack sequencing PRD.

Runs the real PRD parser, marker materialiser and ``create_or_reuse_worktree``
(which shells out to the real ``iar worktree create``) against on-disk
repositories. Prints a transcript that proves:

- rv-1: a ``Sequence: stack`` downstream worktree forks from the upstream
  branch, so the upstream tip is an ancestor of the downstream HEAD.
- rv-3: a via-main downstream forks from a base refreshed from the remote even
  when the local base lags.

No GitHub writes are performed; git operations run in a throwaway temp repo.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from backend.core.shared.models.agent_runner import (
    AppConfig,
    GitConfig,
    IssueSummary,
    WorktreeConfig,
)
from backend.core.use_cases.agent_runner_dependencies import (
    DependencyDeclaration,
    format_dependency_marker,
    parse_dependency_marker,
)
from backend.core.use_cases.agent_runner_worktree_create import create_or_reuse_worktree
from backend.core.use_cases.create_issue_from_prd import _resolve_dependencies
from backend.infrastructure.process_runner import SubprocessRunner


def run(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout, echoing it for the transcript."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(f"  $ git {' '.join(args)}")
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    return result.stdout.strip()


def init_repo(path: Path, *, bare: bool = False) -> Path:
    """Initialise a deterministic git repo (or bare remote)."""
    subprocess.run(
        ["git", "init", "--bare" if bare else "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
    )
    if bare:
        return path
    run(path, "config", "user.email", "evidence@example.com")
    run(path, "config", "user.name", "Evidence")
    return path


def commit(path: Path, message: str) -> str:
    """Commit (allowing empty) and return the new HEAD sha."""
    run(path, "add", "-A")
    run(path, "commit", "--allow-empty", "-m", message)
    return run(path, "rev-parse", "HEAD")


def config_for(local_repo: Path) -> AppConfig:
    """Return a worktree config whose create/path go through the real iar CLI."""
    return AppConfig(
        git=GitConfig(remote="origin", base_branch="main"),
        worktree=WorktreeConfig(provision_database=False),
    )


def iar_init(path: Path) -> None:
    """Write a minimal repo-local ``.iar.toml`` so the real ``iar`` CLI works.

    ``iar init`` is skipped because it also tries to install the remote
    template skill, which fails in this non-interactive, throwaway repo.
    """
    print("  $ write .iar.toml (repository + git remote/base)")
    path.joinpath(".iar.toml").write_text(
        "[agent_runner.repository]\n"
        'id = "evidence"\n'
        "enabled = true\n\n"
        "[agent_runner.git]\n"
        'remote = "origin"\n'
        'base_branch = "main"\n',
        encoding="utf-8",
    )


def rv1_stack_fork(root: Path) -> None:
    """Prove rv-1: stack downstream HEAD has the upstream tip as an ancestor."""
    print("\n=== rv-1: stack downstream forks from the upstream branch ===")
    root = root / "rv1"
    root.mkdir()
    remote = init_repo(root / "remote.git", bare=True)
    local = init_repo(root / "local")
    run(local, "remote", "add", "origin", str(remote))
    iar_init(local)
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    commit(local, "initial")
    run(local, "push", "-u", "origin", "main")

    run(local, "checkout", "-b", "issue-42")
    (local / "upstream.txt").write_text("upstream change\n", encoding="utf-8")
    upstream_tip = commit(local, "upstream change")
    run(local, "push", "-u", "origin", "issue-42")
    run(local, "checkout", "main")

    prd_text = (
        "## Delivery Dependencies\n\n"
        "- Depends on tasks/issues: #42\n"
        "- Gate type: hard\n"
        "- Sequence: stack\n"
    )
    gate, issues, sequence = _resolve_dependencies(prd_text)
    print(f"  parsed: gate={gate!r} issues={issues} sequence={sequence!r}")
    marker = format_dependency_marker(issue_numbers=issues, sequence=sequence)
    print(f"  marker: {marker}")
    declaration = parse_dependency_marker(marker)
    assert declaration == DependencyDeclaration(issue_numbers=(42,), sequence="stack")

    issue = IssueSummary(
        number=9001,
        title="downstream (stack)",
        url="https://example.invalid/issues/9001",
        body=marker,
        labels=(),
    )
    worktree_path = create_or_reuse_worktree(local, issue, config_for(local), SubprocessRunner())
    print(f"  worktree: {worktree_path}")
    downstream_head = run(worktree_path, "rev-parse", "HEAD")
    run(local, "merge-base", "--is-ancestor", upstream_tip, downstream_head)
    print(f"  PROOF rv-1: upstream tip {upstream_tip[:8]} is ancestor of {downstream_head[:8]}")
    assert (worktree_path / "upstream.txt").read_text(encoding="utf-8") == "upstream change\n"


def rv3_base_refresh(root: Path) -> None:
    """Prove rv-3: via-main forks from a refreshed base even when local lags."""
    print("\n=== rv-3: via-main base is refreshed before forking ===")
    root = root / "rv3"
    root.mkdir()
    remote = init_repo(root / "remote.git", bare=True)
    local = init_repo(root / "local")
    run(local, "remote", "add", "origin", str(remote))
    iar_init(local)
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    commit(local, "initial")
    run(local, "push", "-u", "origin", "main")
    stale_local_main = run(local, "rev-parse", "main")

    other = init_repo(root / "other")
    run(other, "remote", "add", "origin", str(remote))
    run(other, "fetch", "origin")
    run(other, "checkout", "-b", "main", "--track", "origin/main")
    (other / "merged-upstream.txt").write_text("merged upstream\n", encoding="utf-8")
    remote_main = commit(other, "upstream merged into main")
    run(other, "push", "origin", "main")
    assert remote_main != stale_local_main

    issue = IssueSummary(
        number=9002,
        title="downstream (via-main)",
        url="https://example.invalid/issues/9002",
        body="",
        labels=(),
    )
    worktree_path = create_or_reuse_worktree(local, issue, config_for(local), SubprocessRunner())
    print(f"  worktree: {worktree_path}")
    fresh_local_main = run(local, "rev-parse", "main")
    print(f"  local main before fork attempt: {stale_local_main[:8]}")
    print(f"  remote main:                    {remote_main[:8]}")
    print(f"  local main after (unchanged):   {fresh_local_main[:8]}")
    assert (worktree_path / "merged-upstream.txt").exists()
    worktree_head = run(worktree_path, "rev-parse", "HEAD")
    assert worktree_head == remote_main
    print(f"  PROOF rv-3: worktree HEAD {worktree_head[:8]} == remote main; sees merged file")


def main() -> None:
    """Run both oracles in a throwaway directory."""
    root = Path(tempfile.mkdtemp(prefix="stack-evidence-"))
    print(f"evidence workspace: {root}")
    try:
        rv1_stack_fork(root)
        rv3_base_refresh(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\nALL EVIDENCE ORACLES PASSED")


if __name__ == "__main__":
    main()
```
