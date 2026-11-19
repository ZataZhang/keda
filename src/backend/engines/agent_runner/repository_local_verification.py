"""探测目标仓库支持的验证工具与执行命令。"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path
from typing import Any

_logger = logging.getLogger("backend.engines.agent_runner.repository_local")


def _dependency_name_matches(dependency_spec: str, package_name: str) -> bool:
    """判断 PEP 508 依赖项是否声明了目标包。"""
    spec_package_name = re.split(r"[<>=!~\[ ;]", dependency_spec.strip(), maxsplit=1)[0]
    return spec_package_name.replace("_", "-").lower() == package_name


def _uv_dependency_flag(pyproject_data: dict[str, Any], package_name: str) -> str | None:
    """返回通过 ``uv run`` 使用目标依赖所需的参数。

    主依赖或默认 ``dev`` 组返回空字符串；其它依赖组或 optional extra
    返回对应的 ``--group`` / ``--extra`` 参数；未声明时返回 ``None``。
    """
    project_table = pyproject_data.get("project") or {}
    for dependency_spec in project_table.get("dependencies") or []:
        if _dependency_name_matches(dependency_spec, package_name):
            return ""
    dependency_groups = pyproject_data.get("dependency-groups") or {}
    for group_name, group_entries in dependency_groups.items():
        group_specs = [entry for entry in group_entries if isinstance(entry, str)]
        if any(_dependency_name_matches(spec, package_name) for spec in group_specs):
            return "" if group_name == "dev" else f" --group {group_name}"
    optional_extras = project_table.get("optional-dependencies") or {}
    for extra_name, extra_specs in optional_extras.items():
        if any(_dependency_name_matches(spec, package_name) for spec in extra_specs):
            return f" --extra {extra_name}"
    return None


# 在第 0 列匹配 ``test:``、``@test:``、``test *args:`` 等 recipe，排除
# ``test := ...`` 变量赋值与 ``test_setup:`` 等名称。
_JUST_TEST_RECIPE_PATTERN = re.compile(r"^@?test\b[^\n]*:(?!=)", re.MULTILINE)
# 捕获 ``import`` / ``import?`` / ``!include`` 指令中的目标路径，支持带引号的路径，
# 以便继续查找导入文件中的 ``test`` recipe。
_JUST_IMPORT_PATTERN = re.compile(
    r"""^\s*(?:import\??|!include)\s+['"]?([^'"\n]+?)['"]?\s*$""",
    re.MULTILINE,
)
_MAX_JUSTFILE_IMPORT_DEPTH = 8


def _justfile_defines_test_recipe(
    justfile_path: Path,
    visited_paths: set[Path] | None = None,
    depth: int = 0,
) -> bool:
    """检查当前 justfile 及其导入文件是否定义了 ``test`` recipe。

    相对每个 justfile 所在目录解析导入路径，并检测循环与递归深度。
    """
    if visited_paths is None:
        visited_paths = set()
    if depth > _MAX_JUSTFILE_IMPORT_DEPTH:
        return False
    try:
        resolved_path = justfile_path.resolve()
    except OSError:
        return False
    if resolved_path in visited_paths:
        return False
    visited_paths.add(resolved_path)
    if not justfile_path.is_file():
        return False
    try:
        justfile_text = justfile_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _JUST_TEST_RECIPE_PATTERN.search(justfile_text):
        return True
    for import_match in _JUST_IMPORT_PATTERN.finditer(justfile_text):
        imported_path = justfile_path.parent / import_match.group(1).strip()
        if _justfile_defines_test_recipe(imported_path, visited_paths, depth + 1):
            return True
    return False


def _has_just_test_recipe(repo_root_path: Path) -> bool:
    """检查仓库中的 justfile 是否定义了 ``test`` recipe。

    支持 ``test:``, ``@test:`` 和 ``test *args:`` 等形式，并跟随 ``import``
    指令查找 ``justfile.shared`` 等导入文件。
    """
    for filename in ("justfile", "Justfile", ".justfile"):
        justfile_path = repo_root_path / filename
        if justfile_path.is_file() and _justfile_defines_test_recipe(justfile_path):
            return True
    return False


def _build_precommit_run_command(pyproject_data: dict[str, Any], skip_check_test_flag: bool) -> str:
    """生成 ``uv run pre-commit run --all-files`` 命令字符串。"""
    precommit_flag = _uv_dependency_flag(pyproject_data, "pre-commit") or ""
    skip_prefix = "SKIP=check-test-flag " if skip_check_test_flag else ""
    return f"{skip_prefix}uv run{precommit_flag} pre-commit run --all-files"


def _detect_precommit_verification_command(
    repo_root_path: Path, pyproject_data: dict[str, Any]
) -> str | None:
    """探测安全可执行的通用 ``pre-commit run`` 命令。

    没有 pre-commit 配置、项目未声明 pre-commit，或配置包含依赖 ``just test``
    标记的 ``check-test-flag`` 时返回 ``None``，避免预检因旧标记卡住。
    """
    precommit_config_path, precommit_config_text = _read_precommit_config(repo_root_path)
    if precommit_config_path is None:
        return None
    if _uv_dependency_flag(pyproject_data, "pre-commit") is None:
        return None
    if "check-test-flag" in precommit_config_text:
        _logger.warning(
            "Repository at %s installs the check-test-flag pre-commit hook but "
            "has no detectable `just test` recipe to refresh its marker; "
            "omitting `pre-commit run` from verification_commands to avoid a "
            "commit deadlock. Add a `just test` recipe or remove check-test-flag.",
            repo_root_path,
        )
        return None
    return _build_precommit_run_command(pyproject_data, skip_check_test_flag=False)


def _read_precommit_config(repo_root_path: Path) -> tuple[Path, str] | tuple[None, str]:
    """读取 pre-commit 配置路径和内容；文件不存在或读取失败时返回空结果。"""
    precommit_config_path = repo_root_path / ".pre-commit-config.yaml"
    if not precommit_config_path.is_file():
        return None, ""
    try:
        return precommit_config_path, precommit_config_path.read_text(encoding="utf-8")
    except OSError:
        return None, ""


def detect_pre_commit_verification_command(repo_root_path: Path) -> str | None:
    """探测 runner 在 ``git commit`` 前应执行的 pre-commit 命令。

    Args:
        repo_root_path: 目标仓库根目录。

    Returns:
        可执行的 ``uv run pre-commit`` 命令；配置或依赖不满足时返回 ``None``。
    """
    pyproject_path = repo_root_path / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        with open(pyproject_path, "rb") as pyproject_file:
            pyproject_data: dict[str, Any] = tomllib.load(pyproject_file)
    except tomllib.TOMLDecodeError:
        return None

    precommit_config_path, precommit_config_text = _read_precommit_config(repo_root_path)
    if precommit_config_path is None:
        return None
    if _uv_dependency_flag(pyproject_data, "pre-commit") is None:
        return None
    skip_check_test_flag = "check-test-flag" in precommit_config_text
    return _build_precommit_run_command(pyproject_data, skip_check_test_flag)


def detect_verification_commands(repo_root_path: Path) -> list[str]:
    """按目标仓库声明生成实际可运行的验证命令。

    Args:
        repo_root_path: 目标仓库根目录。

    Returns:
        验证命令列表，始终包含 ``git diff --check``，并按仓库声明补充文档构建、
        ``just test`` 或安全可运行的 pre-commit / pytest 命令。
    """
    verification_commands = ["git diff --check"]
    pyproject_path = repo_root_path / "pyproject.toml"
    if not pyproject_path.is_file():
        return verification_commands
    try:
        with open(pyproject_path, "rb") as pyproject_file:
            pyproject_data: dict[str, Any] = tomllib.load(pyproject_file)
    except tomllib.TOMLDecodeError:
        return verification_commands

    if (repo_root_path / "mkdocs.yml").is_file():
        mkdocs_flag = _uv_dependency_flag(pyproject_data, "mkdocs")
        if mkdocs_flag is not None:
            verification_commands.append(f"uv run{mkdocs_flag} mkdocs build")

    has_tests = (repo_root_path / "tests").is_dir()
    if _has_just_test_recipe(repo_root_path):
        # ``just test`` 是项目聚合门禁，会执行提交钩子使用的 lint、格式化和测试，
        # 并刷新提交标记，因此无需另加 pre-commit 命令。
        if has_tests:
            verification_commands.append("just test")
    else:
        # 没有 ``just test`` 时，先运行项目自身的 pre-commit，再运行 pytest，
        # 让格式检查在验证阶段报错，而不是拖到提交时。
        precommit_command = _detect_precommit_verification_command(repo_root_path, pyproject_data)
        if precommit_command is not None:
            verification_commands.append(precommit_command)
        if has_tests:
            pytest_flag = _uv_dependency_flag(pyproject_data, "pytest")
            if pytest_flag is not None:
                verification_commands.append(f"uv run{pytest_flag} pytest -q")

    return verification_commands
