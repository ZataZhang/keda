"""守护 PRD 影响树触达进度（FILES 列）的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``scripts/shared/just/prd_impact_tree.py`` 与 ``prd_status.py`` 的
FILES 列。核心不变量：

1. **触达判据是"本次分支改动过"，不是"文件在磁盘上存在"。** 模板历史 PRD 的影响树
   里 57% 的节点是 ``[修改]``，那些文件开工前就躺在磁盘上；用存在性判定，一条尚未
   动工的 PRD 会直接显示过半完成——比没有这一列更糟。
2. **未提交的改动与未 ``git add`` 的新文件都算触达。** executor 执行途中往往还没
   提交，只看 commit 会让进度长期卡在 0，这一列就失去了"补上开工到验收之间的
   进度粒度"这个唯一存在理由。
3. **无法判定的节点不进分母，单独披露。** 跨仓库路径、``{a,b}.py`` 花括号展开、
   通配符本地都无法唯一定位。把它们算进分母会凭空压低进度，算作已判定则会用一个
   假分母换好看的百分比；正确做法是排除 + 用 ``?n`` 明示。
4. **目录节点下的裸文件名只按前缀拼接，不回退仓库根。** ``zata-ops/`` 下的
   ``pyproject.toml`` 若退回仓库根解释，会撞上本仓库同名文件，把一个本该报"无法
   判定"的跨仓库节点算成已判定——这正是 2 号不变量被悄悄绕开的方式。
5. **FILES 列永不转绿。** 它是与 ACTIVITY 列 mtime 启发式同级的弱信号：影响树自称
   "起点而非穷尽清单"，且"文件被碰过"不等于"改对了"。绿色会让它被误读成验收信号，
   混进 CHECKLIST → EVIDENCE → verifier 那条强信号链。
6. **没有分支 worktree 可比对时显示 ``-``。** 主仓库副本永远是开工前的样子，在那里
   比对恒等于 0，显示 ``~0/n`` 会把"测不了"伪装成"没进展"。
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

# prd_status.py / prd_impact_tree.py 不是包的一部分，import 前需把它们所在目录放到 sys.path。
_JUST_SCRIPTS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "shared" / "just"
if str(_JUST_SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(_JUST_SCRIPTS_PATH))

import prd_impact_tree  # noqa: E402
import prd_lock  # noqa: E402
import prd_status  # noqa: E402

_FIXTURE_PRD_NAME = "P2-FEAT-20260101-000000-impact-demo"
_FIXTURE_BRANCH_NAME = "impact-demo"
_PLAIN_PALETTE = prd_status.Palette(enabled=False)

# 覆盖真实影响树里出现过的全部形态：目录节点 + 裸文件名、层级标签 + 完整相对
# 路径、顶层文件、一行两文件、同行动作标记、跨仓库节点。
_FIXTURE_IMPACT_TREE_PRD_TEXT = """# fixture PRD

### Change Impact Tree

```text
.
├── scripts/shared/just/
│   ├── existing_tool.py
│   │   [修改]
│   │   【总结】目录节点下的裸文件名
│   └── brand_new_tool.py
│       [新增]
│       【总结】尚未落盘的新文件
├── justfile.shared
│   [修改]
│   【总结】顶层文件
├── Infrastructure
│   └── docs/ai-standards/tooling.md
│       [修改]【总结】层级标签下挂完整相对路径，且动作与总结同行
├── scripts/shared/worktree/
│   └── first.sh / second.sh
│       [修改]
│       【总结】一行两文件
└── other-repo/ (NEW REPOSITORY)
    └── pyproject.toml
        [新增]
        【总结】跨仓库节点，本地无法判定
```

## Acceptance Checklist

- [ ] item one
"""

_FIXTURE_REPO_FILES = (
    "scripts/shared/just/existing_tool.py",
    "justfile.shared",
    "docs/ai-standards/tooling.md",
    "scripts/shared/worktree/first.sh",
    "scripts/shared/worktree/second.sh",
)


def _run_git(repo_path: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """在指定目录执行 git 命令并返回结果。"""
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )


def _init_main_repo(repo_path: Path, prd_text: str = _FIXTURE_IMPACT_TREE_PRD_TEXT) -> Path:
    """初始化一个带影响树 PRD 与全部被引用源文件的真实 git 仓库。

    Args:
        repo_path (Path): 待初始化的仓库目录。
        prd_text (str): fixture PRD 的正文。

    Returns:
        Path: 初始化后的仓库根。
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.email", "guard@example.com")
    _run_git(repo_path, "config", "user.name", "guard-test")

    for relative_file_text in _FIXTURE_REPO_FILES:
        tracked_file_path = repo_path / relative_file_text
        tracked_file_path.parent.mkdir(parents=True, exist_ok=True)
        tracked_file_path.write_text("original\n", encoding="utf-8")

    prd_file_path = repo_path / "tasks" / "pending" / f"{_FIXTURE_PRD_NAME}.md"
    prd_file_path.parent.mkdir(parents=True, exist_ok=True)
    prd_file_path.write_text(prd_text, encoding="utf-8")
    (repo_path / "tasks" / "archive").mkdir(parents=True, exist_ok=True)

    _run_git(repo_path, "add", "-A")
    _run_git(repo_path, "commit", "-m", "init")
    return repo_path


def _add_branch_worktree(main_repo_path: Path) -> Path:
    """为主仓库创建一个分支名与 PRD slug 匹配的 linked worktree。"""
    worktree_path = main_repo_path.parent / "wt-impact-demo"
    _run_git(main_repo_path, "worktree", "add", "-b", _FIXTURE_BRANCH_NAME, str(worktree_path))
    return worktree_path


def _collect_fixture_record(repo_path: Path) -> prd_status.PrdRecord:
    """收集 fixture PRD 的看板记录，工作树列表按仓库现状实时获取。"""
    return prd_status.collect_prd_record(
        repo_path / "tasks" / "pending" / f"{_FIXTURE_PRD_NAME}.md",
        repo_path / "tasks" / "evidence",
        prd_lock.list_linked_worktree_branches(repo_path),
    )


def _render_impact_cell(repo_path: Path) -> str:
    """渲染 FILES 单元格（无颜色）。"""
    return prd_status.format_impact_cell(_collect_fixture_record(repo_path), _PLAIN_PALETTE)


def test_untouched_branch_reports_zero_despite_existing_files(tmp_path: Path) -> None:
    """分支上一个文件都没碰时必须是 ~0/n，哪怕影响树里的文件全都已存在于磁盘。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    _add_branch_worktree(main_repo_path)

    # 6 个可判定节点（5 个已存在 + 1 个父目录存在的新文件），跨仓库节点被排除。
    assert _render_impact_cell(main_repo_path) == "~0/6?1"


def test_uncommitted_modification_counts_as_touched(tmp_path: Path) -> None:
    """已改但未提交的文件必须计入触达，否则执行途中进度恒为 0。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    (worktree_path / "justfile.shared").write_text("changed\n", encoding="utf-8")

    assert _render_impact_cell(main_repo_path) == "~1/6?1"


def test_untracked_new_file_counts_as_touched(tmp_path: Path) -> None:
    """尚未 git add 的新文件必须计入触达：[新增] 节点多数时间处于这个状态。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    (worktree_path / "scripts" / "shared" / "just" / "brand_new_tool.py").write_text(
        "new\n", encoding="utf-8"
    )

    assert _render_impact_cell(main_repo_path) == "~1/6?1"


def test_committed_change_counts_as_touched(tmp_path: Path) -> None:
    """已提交的改动同样计入触达：基准取与主线的分叉点，而不是 HEAD。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    (worktree_path / "scripts" / "shared" / "just" / "existing_tool.py").write_text(
        "changed\n", encoding="utf-8"
    )
    _run_git(worktree_path, "add", "-A")
    _run_git(worktree_path, "commit", "-m", "work")

    assert _render_impact_cell(main_repo_path) == "~1/6?1"


def test_layer_label_node_resolves_to_repo_relative_path(tmp_path: Path) -> None:
    """层级标签（Infrastructure）不是路径段：其下的完整相对路径必须按仓库根解析。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    (worktree_path / "docs" / "ai-standards" / "tooling.md").write_text(
        "changed\n", encoding="utf-8"
    )

    assert _render_impact_cell(main_repo_path) == "~1/6?1"


def test_multi_path_node_counts_every_file(tmp_path: Path) -> None:
    """``first.sh / second.sh`` 是两个节点：只碰一个时不得算作整行完成。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    (worktree_path / "scripts" / "shared" / "worktree" / "first.sh").write_text(
        "changed\n", encoding="utf-8"
    )

    assert _render_impact_cell(main_repo_path) == "~1/6?1"


def test_cross_repo_node_is_disclosed_not_resolved_against_repo_root(tmp_path: Path) -> None:
    """跨仓库目录下的裸文件名不得回退仓库根解释，必须计入 ``?n`` 披露。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    # 本仓库根有同名 pyproject.toml，且在分支上被改过；跨仓库节点仍不得算作触达。
    (worktree_path / "pyproject.toml").write_text("changed\n", encoding="utf-8")

    assert _render_impact_cell(main_repo_path) == "~0/6?1"


def test_impact_cell_never_renders_green(tmp_path: Path) -> None:
    """全部触达也不得转绿：FILES 是弱信号，绿色会让它被误读成验收通过。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")
    worktree_path = _add_branch_worktree(main_repo_path)

    for relative_file_text in _FIXTURE_REPO_FILES:
        (worktree_path / relative_file_text).write_text("changed\n", encoding="utf-8")
    (worktree_path / "scripts" / "shared" / "just" / "brand_new_tool.py").write_text(
        "new\n", encoding="utf-8"
    )

    color_palette = prd_status.Palette(enabled=True)
    rendered_cell_text = prd_status.format_impact_cell(
        _collect_fixture_record(main_repo_path), color_palette
    )

    assert rendered_cell_text == color_palette.yellow("~6/6?1")
    assert rendered_cell_text != color_palette.green("~6/6?1")


def test_without_matching_worktree_falls_back_to_dash(tmp_path: Path) -> None:
    """没有分支 worktree 可比对时显示 ``-``，不得把"测不了"渲染成"没进展"。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    assert _render_impact_cell(main_repo_path) == "-"


def test_prd_without_impact_tree_falls_back_to_dash(tmp_path: Path) -> None:
    """PRD 没写影响树时显示 ``-``，不得凭空造出一个 0/0 的分母。"""
    main_repo_path = _init_main_repo(
        tmp_path / "repo",
        prd_text="# fixture PRD\n\n## Acceptance Checklist\n\n- [ ] item one\n",
    )
    _add_branch_worktree(main_repo_path)

    assert _render_impact_cell(main_repo_path) == "-"


def test_ambiguous_nodes_are_excluded_from_denominator() -> None:
    """花括号展开与通配符节点本地无法唯一定位，必须留空候选交由调用方披露。"""
    ambiguous_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "├── src/backend/api/{agent_router,tool_router}.py\n"
        "│   [修改]\n"
        "│   【总结】花括号展开\n"
        "└── components/layout/*\n"
        "    [修改]\n"
        "    【总结】通配符\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(ambiguous_prd_text)

    assert len(impact_nodes_tuple) == 2
    assert all(not impact_node.candidate_paths for impact_node in impact_nodes_tuple)


def test_multi_path_sibling_inherits_first_file_directory() -> None:
    """``a/b/c.bash / c.zsh`` 的第二个文件写的是裸名，必须继承第一个文件的目录。

    真实案例：PRD 执行锁那份 PRD 把两个补全脚本写在同一行，第二个文件若按祖先前缀
    （此处为空）解析就会变成仓库根下的 ``worktree_completion.zsh``，永远判不出触达。
    """
    sibling_path_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── scripts/shared/just/worktree_completion.bash / worktree_completion.zsh\n"
        "    [修改]\n"
        "    【总结】一行两文件，第二个写裸名\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(sibling_path_prd_text)

    assert [impact_node.candidate_paths[0] for impact_node in impact_nodes_tuple] == [
        "scripts/shared/just/worktree_completion.bash",
        "scripts/shared/just/worktree_completion.zsh",
    ]


def test_inline_action_suffix_style_is_parsed() -> None:
    """动作标记跟在路径后面（``x.py [新增]``，下一行才是【总结】）必须照样识别。

    真实案例：派生项目 ai-assistant 的 PRD 几乎全用这种写法。漏掉它会把每个文件节点
    误判成目录，整棵树解析出 0 个节点，FILES 列对那个项目永久显示 ``-``。
    """
    inline_suffix_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "├── src/backend/infrastructure/\n"
        "│   ├── crypto/secret_box.py [新增]\n"
        "│   │   【总结】加解密封装\n"
        "│   └── config/settings.py [修改]\n"
        "│       【总结】新增配置段\n"
        "└── justfile.shared [修改]\n"
        "    【总结】顶层文件\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(inline_suffix_prd_text)

    assert [
        (impact_node.candidate_paths[0], impact_node.action) for impact_node in impact_nodes_tuple
    ] == [
        ("src/backend/infrastructure/crypto/secret_box.py", "新增"),
        ("src/backend/infrastructure/config/settings.py", "修改"),
        ("justfile.shared", "修改"),
    ]


def test_directory_node_counts_as_touched_when_a_file_under_it_changed(tmp_path: Path) -> None:
    """树里点到目录时，目录下任一文件被改就算触达。

    真实案例：ai-assistant 的 PRD 有 5 个节点写的是目录（``src/backend/core/fcl_sync``），
    只做文件级精确比较会让它们永远判不出触达，哪怕目录下 13 个文件都改了。
    """
    directory_node_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── scripts/shared/worktree [修改]\n"
        "    【总结】整个目录都要调整\n"
        "```\n"
        "\n"
        "## Acceptance Checklist\n"
        "\n"
        "- [ ] item one\n"
    )
    main_repo_path = _init_main_repo(tmp_path / "repo", prd_text=directory_node_prd_text)
    worktree_path = _add_branch_worktree(main_repo_path)

    assert _render_impact_cell(main_repo_path) == "~0/1"

    (worktree_path / "scripts" / "shared" / "worktree" / "first.sh").write_text(
        "changed\n", encoding="utf-8"
    )

    assert _render_impact_cell(main_repo_path) == "~1/1"


def test_plausibility_is_case_sensitive_and_ignores_untracked_reality() -> None:
    """可判定性只认 git 报出的真实路径，不做文件系统探测。

    ``Path.is_dir()`` 在 macOS/Windows 上大小写不敏感：层级标签 ``Docs`` 会命中真实
    目录 ``docs/``，于是 ``Docs/mkdocs.yml`` 被判成"讲得通"进了分母，却永远匹配不上
    git 报出的 ``mkdocs.yml``——而同一份 PRD 在 Linux/CI 上会判成"无法判定"。
    分母不该随操作系统变。
    """
    repo_path_index = prd_impact_tree.RepoPathIndex.from_file_paths(
        frozenset({"docs/ai-standards/tooling.md", "mkdocs.yml"})
    )

    # 大小写必须严格：``Docs`` 不是 ``docs``。
    assert not repo_path_index.is_plausible_path("Docs/mkdocs.yml")
    assert not repo_path_index.is_plausible_path("Docs/README.md")
    # 真实路径与其下的新文件仍然讲得通。
    assert repo_path_index.is_plausible_path("docs/ai-standards/tooling.md")
    assert repo_path_index.is_plausible_path("docs/ai-standards/new-page.md")
    assert repo_path_index.is_plausible_path("mkdocs.yml")
    # 目录本身讲得通（供目录节点使用）。
    assert repo_path_index.is_plausible_path("docs/ai-standards")


def test_gitignored_directory_does_not_make_a_path_judgeable(tmp_path: Path) -> None:
    """被 gitignore 的目录不得让候选路径"看起来讲得通"。

    真实案例：模板仓库有被忽略的运行时 ``logs/``，它让跨仓库 PRD 里的
    ``zata-ops/logs/tail.py`` 通过了 ``Path.exists()`` 探测，凭空混进分母。
    """
    ignored_dir_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── logs/tail.py [修改]\n"
        "    【总结】落在被忽略目录下\n"
        "```\n"
        "\n"
        "## Acceptance Checklist\n"
        "\n"
        "- [ ] item one\n"
    )
    main_repo_path = _init_main_repo(tmp_path / "repo", prd_text=ignored_dir_prd_text)
    (main_repo_path / ".gitignore").write_text("logs/\n", encoding="utf-8")
    _run_git(main_repo_path, "add", "-A")
    _run_git(main_repo_path, "commit", "-m", "ignore logs")
    worktree_path = _add_branch_worktree(main_repo_path)

    # 文件真实存在于磁盘，但 git 不认；不得据此判成可判定。
    ignored_file_path = worktree_path / "logs" / "tail.py"
    ignored_file_path.parent.mkdir(parents=True, exist_ok=True)
    ignored_file_path.write_text("runtime\n", encoding="utf-8")

    assert _render_impact_cell(main_repo_path) == "-"


def test_ideographic_comma_splits_multiple_files() -> None:
    """顿号是一行多文件的分隔符，后续文件继承第一个文件的目录。

    真实案例：``frontend-admin/src/locales/zh.json、en.json`` 与
    ``docs/guides/a.md、b.md、c.md``。把顿号当歧义符会让整行判不了。
    """
    ideographic_comma_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "├── frontend-admin/src/locales/zh.json、en.json [修改]\n"
        "│   【总结】两份文案\n"
        "└── docs/guides/configuration.md、catalog.md、runs.md [修改]\n"
        "    【总结】三份文档\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(ideographic_comma_prd_text)

    assert [impact_node.candidate_paths[0] for impact_node in impact_nodes_tuple] == [
        "frontend-admin/src/locales/zh.json",
        "frontend-admin/src/locales/en.json",
        "docs/guides/configuration.md",
        "docs/guides/catalog.md",
        "docs/guides/runs.md",
    ]


def test_layer_label_above_real_directory_yields_unlabeled_candidate() -> None:
    """层级标签 + 真实目录 + 裸文件名：必须给出去掉标签的候选。

    真实案例：ai-assistant 的 PRD 写成 ``Database`` → ``src/backend/.../models/`` →
    ``x.py``。祖先链里标签与真实目录混在一起，只拼完整链会得到
    ``Database/src/backend/.../models/x.py``，永远判不出触达——该 PRD 36 个节点里
    有 9 个因此被误报成"无法判定"。
    """
    labeled_directory_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── Database\n"
        "    └── src/backend/infrastructure/persistence/models/\n"
        "        └── fcl_sync.py [新增]\n"
        "            【总结】新表模型\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(labeled_directory_prd_text)

    assert len(impact_nodes_tuple) == 1
    assert (
        "src/backend/infrastructure/persistence/models/fcl_sync.py"
        in impact_nodes_tuple[0].candidate_paths
    )
    # 裸文件名仍不得退回仓库根，否则跨仓库节点会撞上同名文件。
    assert "fcl_sync.py" not in impact_nodes_tuple[0].candidate_paths


def test_generated_filename_placeholder_is_not_judgeable() -> None:
    """``<由 … 生成>`` 这类占位文件名本地无法定位，必须留空候选交由调用方披露。"""
    placeholder_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── alembic/versions/<由 just new-migration 生成>.py [新增]\n"
        "    【总结】迁移脚本\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(placeholder_prd_text)

    assert len(impact_nodes_tuple) == 1
    assert not impact_nodes_tuple[0].candidate_paths


def test_qualified_heading_is_recognized() -> None:
    """``### Core Logic / Change Impact Tree`` 这类带前置限定语的标题必须照样识别。"""
    qualified_heading_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### Core Logic / Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── justfile.shared [修改]\n"
        "    【总结】顶层文件\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(qualified_heading_prd_text)

    assert [impact_node.candidate_paths for impact_node in impact_nodes_tuple] == [
        ("justfile.shared",)
    ]


def test_numbered_heading_is_recognized() -> None:
    """``### 7.2 Change Impact Tree`` 这类带小节编号的标题必须照样识别。"""
    numbered_heading_prd_text = (
        "# fixture PRD\n"
        "\n"
        "### 7.2 Change Impact Tree\n"
        "\n"
        "```text\n"
        ".\n"
        "└── justfile.shared\n"
        "    [修改]\n"
        "    【总结】顶层文件\n"
        "```\n"
    )

    impact_nodes_tuple = prd_impact_tree.parse_impact_tree(numbered_heading_prd_text)

    assert [impact_node.candidate_paths for impact_node in impact_nodes_tuple] == [
        ("justfile.shared",)
    ]


def test_render_prd_table_header_includes_files_column(tmp_path: Path, capsys) -> None:
    """表头必须含 FILES 列：这一列是开工到验收之间唯一的进度粒度。"""
    main_repo_path = _init_main_repo(tmp_path / "repo")

    prd_status.render_prd_table(
        [_collect_fixture_record(main_repo_path)],
        _PLAIN_PALETTE,
        main_repo_path,
        main_repo_path / "tasks" / "evidence",
        main_repo_path / "tasks" / "pending",
        main_repo_path / "tasks" / "archive",
    )

    captured_header_text = capsys.readouterr().out.splitlines()[0]
    assert "FILES" in captured_header_text
    assert captured_header_text.index("CHECKLIST") < captured_header_text.index("FILES")
    assert captured_header_text.index("FILES") < captured_header_text.index("EVIDENCE")


def test_wide_impact_cell_keeps_evidence_column_aligned(tmp_path: Path, capsys) -> None:
    """FILES 列宽必须按实际单元格算，不能写死。

    节点多的 PRD 会渲染出 ``~14/24?12`` 这种 9 字符单元格（派生项目 ai-assistant 的
    真实数据）。列宽写死成 8 时右侧的 EVIDENCE 列会被挤歪一格，整张表错位。
    """
    main_repo_path = _init_main_repo(tmp_path / "repo")

    # 给 EVIDENCE 列一个可定位的内容（``plan✓``），否则它渲染成 ``-``，对不齐也看不出来。
    evidence_dir_path = main_repo_path / "tasks" / "evidence" / _FIXTURE_PRD_NAME
    evidence_dir_path.mkdir(parents=True, exist_ok=True)
    (evidence_dir_path / f"{_FIXTURE_PRD_NAME}.verification-plan.md").write_text(
        "plan\n", encoding="utf-8"
    )

    # 直接注入 ai-assistant 真实看到的那组计数：``~14/24?12`` 是 9 字符，比表头宽。
    wide_impact_record = dataclasses.replace(
        _collect_fixture_record(main_repo_path),
        impact_progress=prd_impact_tree.ImpactProgress(
            judgeable_total=24, touched_total=14, unresolvable_total=12
        ),
    )

    prd_status.render_prd_table(
        [wide_impact_record],
        _PLAIN_PALETTE,
        main_repo_path,
        main_repo_path / "tasks" / "evidence",
        main_repo_path / "tasks" / "pending",
        main_repo_path / "tasks" / "archive",
    )

    captured_lines_list = capsys.readouterr().out.splitlines()
    header_text, row_text = captured_lines_list[0], captured_lines_list[1]
    # 单元格比表头 ``FILES`` 宽，因此它决定列宽；EVIDENCE 必须与行内证据列同列起步。
    assert "~14/24?12" in row_text
    assert header_text.index("EVIDENCE") == row_text.index("plan")
