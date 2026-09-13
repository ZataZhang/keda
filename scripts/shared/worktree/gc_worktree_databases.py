#!/usr/bin/env python3
"""扫描并回收 worktree 专用孤儿数据库。

``create.sh`` 会为每个 worktree 创建独立数据库（标识符
``<repo>_wt_<branch>_<digest8>``），但 worktree 删除流程只清理目录与
分支，数据库会留存为孤儿库。本脚本负责盘点与回收：

- 仅当数据库名同时满足「前缀为 ``<repo>_wt_``」「以 ``_`` + 8 位十六
  进制摘要结尾」「不在当前注册 worktree 的存活集合中」三个条件时，才
  判定为孤儿；
- 默认只列出候选孤儿库（dry run）；传入 ``--gc`` 后逐个确认删除，
  ``--yes`` 可跳过确认；
- 连接凭据读取主仓库 ``.env.local`` 的 ``DATABASE_URL``，支持
  PostgreSQL 与 MySQL；配置缺失、驱动不可用或数据库服务不可达时安全
  跳过，绝不猜测、绝不误删。

用法（通常由 ``merge.sh --doctor`` 调用）::

    python gc_worktree_databases.py <repo_root> [--gc] [--yes]
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import ParseResult, urlparse

# 复用 setup_copied_database.py 的库名派生逻辑，保证「回收」与「创建」
# 对同一标识符推导出完全一致的库名，避免两套实现漂移。
_TEMPLATE_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "template" / "setup_copied_database.py"
)
_TEMPLATE_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "_setup_copied_database",
    _TEMPLATE_SCRIPT_PATH,
)
assert _TEMPLATE_SCRIPT_SPEC is not None
assert _TEMPLATE_SCRIPT_SPEC.loader is not None
_TEMPLATE_SCRIPT_MODULE = importlib.util.module_from_spec(_TEMPLATE_SCRIPT_SPEC)
_TEMPLATE_SCRIPT_SPEC.loader.exec_module(_TEMPLATE_SCRIPT_MODULE)

derive_database_name = _TEMPLATE_SCRIPT_MODULE.derive_database_name

# worktree 库名的固定尾巴：create.sh 追加的 8 位 git hash-object 摘要，
# 以及超长分支名截断时 derive_database_name 追加的 8 位 sha256 摘要，
# 两者都固定落在名称末尾，构成所有权判定的必要条件。
_WORKTREE_DATABASE_TAIL_PATTERN = re.compile(r".+_[0-9a-f]{8}$")


def parse_main_database_url(env_local_path: Path) -> Optional[ParseResult]:
    """读取主仓库 ``.env.local`` 中的 ``DATABASE_URL``。

    只接受 PostgreSQL / MySQL URL；SQLite 等其他 scheme、注释行与空值
    一律视为「无数据库可盘点」，返回 ``None``。

    Args:
        env_local_path: 主仓库 ``.env.local`` 路径。

    Returns:
        解析后的 URL；无法解析时返回 ``None``。
    """
    if not env_local_path.exists():
        return None
    for raw_line in env_local_path.read_text(encoding="utf-8").splitlines():
        trimmed_line = raw_line.strip()
        if trimmed_line.startswith("#") or not trimmed_line.startswith("DATABASE_URL="):
            continue
        url_value = trimmed_line.partition("=")[2].strip()
        if not url_value:
            return None
        parsed_url = urlparse(url_value)
        if parsed_url.scheme.startswith(("postgresql", "mysql")):
            return parsed_url
        return None
    return None


def compute_branch_digest(repo_root: Path, branch_name: str) -> str:
    """复算 ``create.sh`` 的分支摘要（``git hash-object --stdin`` 前 8 位）。

    Args:
        repo_root: 仓库根目录，作为 git 子进程的工作目录。
        branch_name: worktree 分支名。

    Returns:
        8 位十六进制摘要字符串。
    """
    digest_output = subprocess.run(
        ["git", "hash-object", "--stdin"],
        input=branch_name.encode("utf-8"),
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return digest_output.strip()[:8]


def build_worktree_database_identifier(
    repo_name: str,
    branch_name: str,
    branch_digest: str,
) -> str:
    """拼装与 ``create.sh`` 一致的 worktree 数据库标识符。

    Args:
        repo_name: 仓库根目录名。
        branch_name: worktree 分支名。
        branch_digest: :func:`compute_branch_digest` 得到的 8 位摘要。

    Returns:
        形如 ``<repo>_wt_<branch>_<digest8>`` 的标识符。
    """
    return f"{repo_name}_wt_{branch_name}_{branch_digest}"


def collect_registered_worktree_branches(repo_root: Path) -> list[str]:
    """收集当前注册的所有 worktree 分支名。

    Args:
        repo_root: 仓库根目录。

    Returns:
        分支名列表（不含 ``refs/heads/`` 前缀）；detached worktree 不在
        结果中（建库流程只服务于有分支的 worktree）。
    """
    porcelain_output = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    worktree_branch_names: list[str] = []
    for porcelain_line in porcelain_output.splitlines():
        if porcelain_line.startswith("branch refs/heads/"):
            worktree_branch_names.append(porcelain_line.removeprefix("branch refs/heads/"))
    return worktree_branch_names


def build_live_worktree_database_names(repo_root: Path, repo_name: str) -> set[str]:
    """推导当前注册 worktree 应占用的库名集合。

    Args:
        repo_root: 仓库根目录。
        repo_name: 仓库根目录名。

    Returns:
        存活 worktree 的库名集合。注册元数据残留（目录已删但未
        ``git worktree prune``）的 worktree 也会计入，避免误删。
    """
    live_database_names: set[str] = set()
    for worktree_branch_name in collect_registered_worktree_branches(repo_root):
        branch_digest = compute_branch_digest(repo_root, worktree_branch_name)
        worktree_identifier = build_worktree_database_identifier(
            repo_name,
            worktree_branch_name,
            branch_digest,
        )
        live_database_names.add(derive_database_name(worktree_identifier))
    return live_database_names


def filter_orphan_database_candidates(
    server_database_names: list[str],
    live_database_names: set[str],
    repo_prefix: str,
) -> list[str]:
    """从服务器库名清单中筛出孤儿 worktree 库。

    所有权判定同时要求「前缀为 ``<repo_prefix>_wt_``」与「以 ``_`` +
    8 位十六进制摘要结尾」，两个条件缺一不可；命中存活集合的直接排除。

    Args:
        server_database_names: 数据库服务器上的全部库名。
        live_database_names: 当前注册 worktree 的存活库名集合。
        repo_prefix: 仓库名派生出的前缀（通常即仓库目录名的小写形式）。

    Returns:
        排序后的孤儿库名列表。
    """
    ownership_pattern = re.compile(rf"^{re.escape(repo_prefix)}_wt_.+_[0-9a-f]{{8}}$")
    orphan_database_names: list[str] = []
    for server_database_name in sorted(server_database_names):
        if server_database_name in live_database_names:
            continue
        if not ownership_pattern.match(server_database_name):
            continue
        orphan_database_names.append(server_database_name)
    return orphan_database_names


def list_postgres_database_names(parsed_url: ParseResult) -> Optional[list[str]]:
    """连接 PostgreSQL 实例并列出全部用户数据库。

    Args:
        parsed_url: 主仓库 ``DATABASE_URL`` 的解析结果。

    Returns:
        库名列表；驱动缺失或连接失败时返回 ``None`` 并打印原因。
    """
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 不可用，跳过 PostgreSQL 孤儿库盘点。")
        return None

    admin_connection = None
    try:
        admin_connection = psycopg2.connect(
            host=parsed_url.hostname or "localhost",
            port=parsed_url.port or 5432,
            user=parsed_url.username,
            password=parsed_url.password,
            dbname="postgres",
        )
        with admin_connection.cursor() as admin_cursor:
            admin_cursor.execute(
                "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate;"
            )
            return sorted(row[0] for row in admin_cursor.fetchall())
    except psycopg2.Error as database_error:
        print(f"无法连接 PostgreSQL 实例，跳过孤儿库盘点: {database_error}")
        return None
    finally:
        if admin_connection is not None:
            admin_connection.close()


def list_mysql_database_names(parsed_url: ParseResult) -> Optional[list[str]]:
    """连接 MySQL 实例并列出全部数据库。

    Args:
        parsed_url: 主仓库 ``DATABASE_URL`` 的解析结果。

    Returns:
        库名列表；驱动缺失或连接失败时返回 ``None`` 并打印原因。
    """
    try:
        import pymysql
    except ImportError:
        print("PyMySQL 不可用，跳过 MySQL 孤儿库盘点。")
        return None

    admin_connection = None
    try:
        admin_connection = pymysql.connect(
            host=parsed_url.hostname or "localhost",
            port=parsed_url.port or 3306,
            user=parsed_url.username,
            password=parsed_url.password,
            database="mysql",
        )
        with admin_connection.cursor() as admin_cursor:
            admin_cursor.execute("SHOW DATABASES;")
            return sorted(row[0] for row in admin_cursor.fetchall())
    except pymysql.MySQLError as database_error:
        print(f"无法连接 MySQL 实例，跳过孤儿库盘点: {database_error}")
        return None
    finally:
        if admin_connection is not None:
            admin_connection.close()


def drop_orphan_database(
    parsed_url: ParseResult,
    orphan_database_name: str,
) -> bool:
    """删除单个孤儿数据库。

    库名在进入此函数前已通过所有权判定（仅含 ``[a-z0-9_]``），可直接
    拼接 SQL，无需引用符。

    Args:
        parsed_url: 主仓库 ``DATABASE_URL`` 的解析结果。
        orphan_database_name: 待删除的孤儿库名。

    Returns:
        删除成功返回 ``True``，失败返回 ``False``。
    """
    if parsed_url.scheme.startswith("postgresql"):
        try:
            import psycopg2
        except ImportError:
            print("psycopg2 不可用，无法删除。")
            return False
        admin_connection = None
        try:
            admin_connection = psycopg2.connect(
                host=parsed_url.hostname or "localhost",
                port=parsed_url.port or 5432,
                user=parsed_url.username,
                password=parsed_url.password,
                dbname="postgres",
            )
            admin_connection.autocommit = True
            with admin_connection.cursor() as admin_cursor:
                admin_cursor.execute(f"DROP DATABASE {orphan_database_name};")
            return True
        except psycopg2.Error as database_error:
            print(f"  删除失败: {database_error}")
            return False
        finally:
            if admin_connection is not None:
                admin_connection.close()

    try:
        import pymysql
    except ImportError:
        print("PyMySQL 不可用，无法删除。")
        return False
    admin_connection = None
    try:
        admin_connection = pymysql.connect(
            host=parsed_url.hostname or "localhost",
            port=parsed_url.port or 3306,
            user=parsed_url.username,
            password=parsed_url.password,
            database="mysql",
            autocommit=True,
        )
        with admin_connection.cursor() as admin_cursor:
            admin_cursor.execute(f"DROP DATABASE `{orphan_database_name}`;")
        return True
    except pymysql.MySQLError as database_error:
        print(f"  删除失败: {database_error}")
        return False
    finally:
        if admin_connection is not None:
            admin_connection.close()


def run_gc(
    parsed_url: ParseResult,
    orphan_database_names: list[str],
    auto_yes: bool,
) -> int:
    """执行删除流程：逐个确认并 DROP 孤儿库。

    Args:
        parsed_url: 主仓库 ``DATABASE_URL`` 的解析结果。
        orphan_database_names: 孤儿库名列表。
        auto_yes: ``True`` 时跳过逐个确认（配合脚本化调用）。

    Returns:
        全部删除成功返回 0，存在失败返回 1。
    """
    failed_database_names: list[str] = []
    for orphan_database_name in orphan_database_names:
        if not auto_yes:
            confirmation = input(f"  删除 {orphan_database_name}? [y/N] ")
            if confirmation.strip().lower() not in ("y", "yes"):
                print(f"  已跳过 {orphan_database_name}")
                continue
        if drop_orphan_database(parsed_url, orphan_database_name):
            print(f"  已删除 {orphan_database_name}")
        else:
            failed_database_names.append(orphan_database_name)
    if failed_database_names:
        print(f"⚠️ 以下 {len(failed_database_names)} 个库删除失败，请手动处理。")
        return 1
    return 0


def main() -> int:
    """脚本入口。"""
    argument_parser = argparse.ArgumentParser(
        description="扫描并回收 worktree 专用孤儿数据库。",
    )
    argument_parser.add_argument(
        "repo_root",
        nargs="?",
        default=".",
        help="主仓库根目录（默认当前目录）",
    )
    argument_parser.add_argument(
        "--gc",
        action="store_true",
        help="删除扫描出的孤儿库（默认只列出）",
    )
    argument_parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过逐个删除确认",
    )
    script_arguments = argument_parser.parse_args()

    repo_root = Path(script_arguments.repo_root).resolve()
    parsed_url = parse_main_database_url(repo_root / ".env.local")
    if parsed_url is None:
        print(".env.local 中未找到 PostgreSQL/MySQL 的 DATABASE_URL，无库可盘点。")
        return 0

    repo_name = repo_root.name
    repo_prefix = derive_database_name(repo_name)

    live_database_names = build_live_worktree_database_names(repo_root, repo_name)

    if parsed_url.scheme.startswith("postgresql"):
        server_database_names = list_postgres_database_names(parsed_url)
    else:
        server_database_names = list_mysql_database_names(parsed_url)
    if server_database_names is None:
        return 0

    orphan_database_names = filter_orphan_database_candidates(
        server_database_names,
        live_database_names,
        repo_prefix,
    )

    print(f"🗄️ Worktree 孤儿数据库盘点（仓库: {repo_name}）")
    if parsed_url.hostname not in ("localhost", "127.0.0.1", "::1"):
        print(f"   ⚠️ 目标数据库服务器非本机: {parsed_url.hostname}，删除前请二次确认。")
    if not orphan_database_names:
        print("✅ 未发现孤儿 worktree 数据库。")
        return 0

    for orphan_database_name in orphan_database_names:
        print(f"   - {orphan_database_name}")
    print(f"共 {len(orphan_database_names)} 个孤儿库。")

    if not script_arguments.gc:
        print("传入 --gc 可逐个确认删除；--yes 跳过确认。")
        return 0

    return run_gc(parsed_url, orphan_database_names, script_arguments.yes)


if __name__ == "__main__":
    sys.exit(main())
