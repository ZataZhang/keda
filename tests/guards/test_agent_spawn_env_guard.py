"""守护 agent 子进程派发点环境净化的守卫测试（guard test）。

本文件位于 ``tests/guards/``，失败意味着 agent 子进程派发点违反了
「必须传入净化环境（``build_sanitized_child_env``）」的仓库约定
（PRD: ``tasks/pending/P1-BUG-20260928-232844-agent-runner-child-env-sanitize.md``）。
正确做法是修复触发它的源代码，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新对应文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

背景：agent 子进程若原样继承 runner 父环境，交互式 AI 会话注入的
``SERVER__PORT`` 等私有变量会使其 EADDRINUSE 卡死（2026-09-28
Issue #156 事故）。因此所有 agent 派发点的 ``subprocess.Popen`` 调用
必须传 ``env=build_sanitized_child_env()``。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# process_runner.py 中只有这两个函数是 agent 流式派发点；
# SubprocessRunner.run / _run_captured_process 服务于工具命令（git/gh/pytest），
# 按约定不做净化，不在本守卫范围内。
PROCESS_RUNNER_AGENT_SPAWN_FUNCTIONS = frozenset(
    {
        "run_filtered_claude_stream",
        "_run_pty_stream",
    }
)

# engines 输出协议目录下的全部 Popen 都面向 agent 子进程，整目录覆盖，
# 未来新增协议文件自动纳入。
OUTPUT_PROTOCOLS_DIR = (
    REPO_ROOT / "src" / "backend" / "engines" / "agent_runner" / "output_protocols"
)

PROCESS_RUNNER_PATH = REPO_ROOT / "src" / "backend" / "infrastructure" / "process_runner.py"

SANITIZER_NAME = "build_sanitized_child_env"


@dataclass(frozen=True)
class _SpawnSite:
    """一个 ``subprocess.Popen`` 调用点及其来源信息。"""

    file: Path
    function: str
    line: int
    has_sanitized_env: bool


def _is_subprocess_popen(call: ast.Call) -> bool:
    """判断调用表达式是否为 ``subprocess.Popen(...)``。"""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "Popen"
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
    )


def _references_sanitizer(node: ast.expr) -> bool:
    """判断 env= 的值表达式是否引用了 ``build_sanitized_child_env``。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == SANITIZER_NAME:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == SANITIZER_NAME:
            return True
    return False


def _collect_spawn_sites(path: Path, allowed_functions: frozenset[str] | None) -> list[_SpawnSite]:
    """收集文件中 agent 派发点的 ``subprocess.Popen`` 调用。

    Args:
        path: 待扫描的 Python 文件。
        allowed_functions: 限定扫描的函数名集合；``None`` 表示扫描文件内
            全部函数（用于整目录覆盖的协议文件）。

    Returns:
        命中目标函数的 Popen 调用点列表（含是否传入净化 env）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[_SpawnSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_popen(node):
            continue
        function = _enclosing_function_name(node, tree)
        if function is None:
            continue
        if allowed_functions is not None and function not in allowed_functions:
            continue
        env_kwarg = next((kw for kw in node.keywords if kw.arg == "env"), None)
        has_sanitized_env = env_kwarg is not None and _references_sanitizer(env_kwarg.value)
        sites.append(
            _SpawnSite(
                file=path,
                function=function,
                line=node.lineno,
                has_sanitized_env=has_sanitized_env,
            )
        )
    return sites


def _enclosing_function_name(target: ast.AST, tree: ast.AST) -> str | None:
    """返回包含 ``target`` 节点的最内层函数名（含方法）。"""
    result: str | None = None

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            nonlocal result
            if any(child is target for child in ast.walk(node)):
                if result is None:
                    result = node.name
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    _Visitor().visit(tree)
    return result


def test_agent_spawn_sites_pass_sanitized_env() -> None:
    """全部 agent 派发点的 Popen 必须传入净化 env，且目标函数必须真实存在。"""
    violations: list[str] = []

    process_runner_sites = _collect_spawn_sites(
        PROCESS_RUNNER_PATH, PROCESS_RUNNER_AGENT_SPAWN_FUNCTIONS
    )
    found_functions = {site.function for site in process_runner_sites}
    if found_functions != set(PROCESS_RUNNER_AGENT_SPAWN_FUNCTIONS):
        missing = set(PROCESS_RUNNER_AGENT_SPAWN_FUNCTIONS) - found_functions
        violations.append(f"{PROCESS_RUNNER_PATH}: agent 派发函数缺失或被重命名: {sorted(missing)}")

    protocol_files = sorted(OUTPUT_PROTOCOLS_DIR.glob("*.py"))
    assert protocol_files, f"output_protocols 目录为空: {OUTPUT_PROTOCOLS_DIR}"
    for protocol_file in protocol_files:
        for site in _collect_spawn_sites(protocol_file, None):
            if not site.has_sanitized_env:
                violations.append(
                    f"{site.file}:{site.line} ({site.function}): Popen 未传 "
                    f"env={SANITIZER_NAME}()"
                )

    for site in process_runner_sites:
        if not site.has_sanitized_env:
            violations.append(
                f"{site.file}:{site.line} ({site.function}): Popen 未传 " f"env={SANITIZER_NAME}()"
            )

    assert not violations, "agent 派发点环境净化约定被违反:\n" + "\n".join(violations)


def test_guard_detects_bare_popen_regression(tmp_path: Path) -> None:
    """负控：对构造性违规（未传净化 env 的 agent Popen），扫描逻辑必须报出。"""
    fixture = tmp_path / "plain_like.py"
    fixture.write_text(
        "import subprocess\n"
        "\n"
        "\n"
        "def relay():\n"
        "    process = subprocess.Popen(['agent'])\n"
        "    return process\n",
        encoding="utf-8",
    )

    sites = _collect_spawn_sites(fixture, None)

    assert len(sites) == 1
    assert not sites[0].has_sanitized_env
