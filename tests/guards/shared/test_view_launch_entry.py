"""守护 ``just view`` 客户端入口的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

被测对象：``justfile.shared`` 的 ``view`` recipe 与 ``scripts/shared/view/launch.py``。
核心不变量：

**入口必须能在 ``-S``（跳过 site 初始化）下运行，因此它的 import 闭包只能是标准库
加同目录的兄弟模块。**

``just view`` 的复用命中路径有 150ms 的进程侧预算。site 初始化要扫一堆路径与 ``.pth`` 文件，
本机实测空载只省约 1.5ms，但在 CPU 被别的进程挤占时省约 30ms——预算真正吃紧的正是
机器忙的时候，所以 recipe 显式传 ``-S`` 跳过 site 初始化。

代价是这条入口进程里 **没有 site-packages**：``launch.py`` 一旦 import 任何第三方包
（或 import 一个模块级 import 了第三方包的兄弟模块），``just view`` 会在启动瞬间
ImportError，而这条路径平时不会被业务测试覆盖，坏掉时很难第一时间归因。语法高亮
依赖 ``pygments`` 只出现在服务进程里，那个进程由 ``launch.py`` 单独以**不带** ``-S``
的方式拉起，因此不受这条约束影响。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_PROJECT_ROOT_PATH = Path(__file__).resolve().parents[3]
_VIEW_SCRIPTS_PATH = _PROJECT_ROOT_PATH / "scripts" / "shared" / "view"
_LAUNCH_SCRIPT_PATH = _VIEW_SCRIPTS_PATH / "launch.py"
_JUSTFILE_PATH = _PROJECT_ROOT_PATH / "justfile.shared"

#: skip site 初始化的 CPython 开关；recipe 传了它，下面的 import 约束才成立。
_NO_SITE_FLAG = "-S"


def _imported_module_names(script_path: Path) -> set[str]:
    """收集脚本里出现的顶层模块名，含函数内的惰性 import。

    惰性 import 同样在 ``-S`` 的解释器里执行，因此和模块级 import 受同一条约束。

    Args:
        script_path (Path): 待分析的脚本路径。

    Returns:
        set[str]: 顶层模块名集合（``from a.b import c`` 记作 ``a``）。
    """
    syntax_tree = ast.parse(script_path.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for syntax_node in ast.walk(syntax_tree):
        if isinstance(syntax_node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in syntax_node.names)
        elif isinstance(syntax_node, ast.ImportFrom) and syntax_node.module is not None:
            imported_names.add(syntax_node.module.split(".")[0])
    return imported_names


def _transitive_import_closure(entry_script_path: Path) -> dict[str, set[str]]:
    """沿同目录兄弟模块展开 import 闭包。

    Args:
        entry_script_path (Path): 入口脚本路径。

    Returns:
        dict[str, set[str]]: 模块名到该模块所 import 的顶层模块名集合。
    """
    import_closure: dict[str, set[str]] = {}
    pending_script_paths = [entry_script_path]
    while pending_script_paths:
        current_script_path = pending_script_paths.pop()
        imported_names = _imported_module_names(current_script_path)
        import_closure[current_script_path.stem] = imported_names
        for imported_name in imported_names:
            sibling_script_path = current_script_path.parent / f"{imported_name}.py"
            if sibling_script_path.exists() and imported_name not in import_closure:
                pending_script_paths.append(sibling_script_path)
    return import_closure


def _view_recipe_body_lines() -> list[str]:
    """取出 ``justfile.shared`` 里 ``view`` recipe 的首行正文。

    Returns:
        list[str]: recipe 正文行（不含 recipe 头与注释）。
    """
    recipe_body_lines: list[str] = []
    is_inside_view_recipe = False
    for justfile_line in _JUSTFILE_PATH.read_text(encoding="utf-8").splitlines():
        if justfile_line.startswith("view "):
            is_inside_view_recipe = True
            continue
        if not is_inside_view_recipe:
            continue
        if justfile_line.startswith((" ", "\t")) and justfile_line.strip():
            recipe_body_lines.append(justfile_line)
            continue
        break
    return recipe_body_lines


def test_view_recipe_runs_the_launch_entry_without_site_initialization() -> None:
    """``view`` recipe 必须给入口传 ``-S``，否则下面那条 import 约束就失去意义。

    这条断言把「预算 → ``-S`` → import 只能标准库」这条因果链钉在测试里：任何一环
    被单独拿掉，都会在这里显形，而不是等到某天 ``just view`` 突然起不来。
    """
    recipe_body_text = "\n".join(_view_recipe_body_lines())
    assert recipe_body_text, "justfile.shared 里找不到 view recipe 的正文"
    assert (
        f" {_NO_SITE_FLAG} " in f" {recipe_body_text} "
    ), "view recipe 没有传 -S：复用命中路径会退回完整的 site 初始化，机器忙时多花约 30ms"


def test_launch_entry_import_closure_is_standard_library_only() -> None:
    """入口的 import 闭包只能是标准库加同目录兄弟模块。

    闭包里的任何第三方包都会在 ``-S`` 下 ImportError；这条断言在评审阶段就把
    那个失败模式挡下来，而不是等到运行时。
    """
    import_closure = _transitive_import_closure(_LAUNCH_SCRIPT_PATH)
    assert "launch" in import_closure, "入口脚本自身没有被解析到"

    non_standard_library_names: list[str] = []
    for module_name, imported_names in import_closure.items():
        for imported_name in sorted(imported_names):
            if imported_name in import_closure:
                continue
            if imported_name in sys.stdlib_module_names:
                continue
            non_standard_library_names.append(f"{module_name}.py -> {imported_name}")

    hint = f"（{_LAUNCH_SCRIPT_PATH} 由 justfile.shared 的 view recipe 以 -S 启动）"
    assert not non_standard_library_names, (
        f"launch 入口在 -S 下无法 import 这些非标准库模块{hint}：" f"{non_standard_library_names}"
    )
