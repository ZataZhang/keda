"""守护迁移「外键约束先于索引删除」顺序约定的守卫测试（guard test）。

本文件位于 ``tests/guards/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。

背景与约定正文见 ``docs/ai-standards/alembic.md`` 的「外键约束与索引删除顺序」
小节：MySQL 8 InnoDB 拒绝删除仍被外键约束依赖的索引，``DROP INDEX``/
``ALTER TABLE ... DROP KEY`` 在该索引还支撑着一个 FOREIGN KEY 时报 1553
（``Cannot drop index 'X': needed in a foreign key constraint``）。这是 MySQL/
InnoDB 存储引擎的实现细节，不是通用 SQL 语义——PostgreSQL 的外键约束不要求
被引用列以外的任何一侧存在索引，子表 FK 列上的普通索引与该 FK 约束之间没有
目录级依赖，删除顺序不受影响；这条约定与本守卫测试因此只针对 MySQL 方言。

这个坑在 SQLite 上完全不可见：Alembic 的 ``batch_alter_table`` 在 SQLite 上走
"整表重建"策略（建临时表 -> 拷贝数据 -> 删旧表 -> 改名），不会对旧表执行真正
的 ``DROP INDEX``。仓库自身的迁移 round-trip 守卫测试
（``tests/guards/test_migrations.py::test_migrations_upgrade_downgrade_upgrade``）
固定使用 SQLite，不随 CI matrix 的 ``DATABASE_URL`` 切换到真实数据库，因此即使
CI 已经起了 MySQL 服务（见 ``.github/workflows/ci.yml`` 的 ``validate-template``
job），也不会覆盖这条路径——该 job 里对 MySQL 服务只执行
``alembic upgrade head``，从未执行过 ``downgrade``。也就是说，在真正补上一条会
对 MySQL 服务执行 upgrade→downgrade→upgrade 回环的 CI 检查之前，本文件是这类
缺陷在派生项目里唯一的自动化防线。

检查范围（刻意收窄，避免误报）：**只检查同一个 upgrade()/downgrade() 函数内、
同一张表上「显式 drop_constraint(type_="foreignkey") 与
drop_index/drop_constraint(type_="unique") 同时出现」的情形**，要求每一次
索引/唯一约束删除之前，该表必须已经有至少一次外键约束删除。

本文件**不**检查"索引删除后紧跟整表 drop_table"这一种形状（例如某张表的
downgrade() 里，先 ``drop_index`` 再紧跟 ``drop_table`` 同一张表）。原因：这一
形状是否真的有 1553 风险，取决于被删索引的列是否恰好是某个外键约束的列——
仅按"这张表在别处有没有任意外键"做表级粗判并不安全：一张表完全可能既有外键
约束（引用其他表），又有一批与该外键毫不相干的普通索引，这些索引在
downgrade() 里被删、随后整表被删除，全程不会触发 1553（DROP TABLE 本身是原子
操作，会连同索引与外键约束一起清除，不受"索引仍被外键依赖"这条限制）。要安全
覆盖这一种形状，需要把 upgrade() 里每个外键约束的列集合与每个索引的列集合做
静态交叉比对（含 ``op.f()`` 包装、多列索引/外键、inline
``sa.ForeignKey``/``ForeignKeyConstraint`` 等多种写法），复杂度与误判面显著
高于收益，因此本文件不做这一种检查；这一种形状的回归需要真实 MySQL 的
upgrade→downgrade→upgrade 回环验证兜底。

本文件随 sync 分发到派生项目，而 ``alembic/versions`` 是项目自有对象：派生项目
可能尚未引入任何迁移，或压根不用 Alembic。这种仓库没有可检查的对象，"没有迁移"
并不等于"违反约定"，因此仓库扫描在找不到任何迁移文件时**跳过**而不是判失败——
否则每个还没写迁移的派生项目都会在默认门禁下平白变红。跳过用 ``pytest.skip``
显式呈现，使"守卫暂未生效"在测试报告里可见，而不是静默通过。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT_PATH = Path(__file__).resolve().parents[3]
VERSIONS_DIR_PATH = REPO_ROOT_PATH / "alembic" / "versions"

_INDEX_OR_UNIQUE_KIND = "index_or_unique"
_FOREIGN_KEY_KIND = "foreignkey"

_TRACKED_FUNCTION_NAMES = ("upgrade", "downgrade")


@dataclass(frozen=True)
class _DropEvent:
    """upgrade()/downgrade() 函数体内一次与索引/外键约束删除相关的 op 调用。"""

    table_name: str
    kind: str
    line_number: int
    call_description: str


def _string_literal_value(node: ast.expr | None) -> str | None:
    """若 node 是字符串字面量则返回其值，否则返回 None。

    Args:
        node: 待判断的 AST 表达式节点，可能为 None。

    Returns:
        字符串字面量的值；非字面量或无法静态确定时返回 None。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _keyword_value(call_node: ast.Call, keyword_name: str) -> ast.expr | None:
    """从函数调用节点里按关键字参数名取值表达式。

    Args:
        call_node: 函数调用 AST 节点。
        keyword_name: 关键字参数名。

    Returns:
        对应的表达式节点；未传该关键字参数时返回 None。
    """
    for keyword in call_node.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    return None


def _call_root_and_method(call_node: ast.Call) -> tuple[str, str] | None:
    """取 ``root.method(...)`` 形状调用里的 root 变量名与方法名。

    Args:
        call_node: 函数调用 AST 节点。

    Returns:
        (root 变量名, 方法名) 二元组；调用不是简单的
        ``变量.属性(...)`` 形状时返回 None。
    """
    if not isinstance(call_node.func, ast.Attribute):
        return None
    if not isinstance(call_node.func.value, ast.Name):
        return None
    return call_node.func.value.id, call_node.func.attr


def _table_name_from_args(call_node: ast.Call) -> str | None:
    """从顶层 ``op.drop_index``/``op.drop_constraint`` 调用取 table_name。

    Alembic 的这两个函数都把 ``table_name`` 声明为第二个位置参数，调用方既
    可能用关键字也可能用位置传参，因此两种形状都要识别。

    Args:
        call_node: 函数调用 AST 节点。

    Returns:
        表名字面量；关键字与位置参数都不是字符串字面量时返回 None。
    """
    table_name = _string_literal_value(_keyword_value(call_node, "table_name"))
    if table_name is None and len(call_node.args) >= 2:
        table_name = _string_literal_value(call_node.args[1])
    return table_name


def _classify_constraint_kind(constraint_type: str | None) -> str | None:
    """把 drop_constraint 的 type_ 映射成本守卫关心的 kind。

    Args:
        constraint_type: ``type_=`` 关键字参数的字面量值。

    Returns:
        ``_FOREIGN_KEY_KIND`` 或 ``_INDEX_OR_UNIQUE_KIND``；``type_`` 是
        primary key/check 等本守卫不关心的取值，或无法静态确定时返回 None。
    """
    if constraint_type == "foreignkey":
        return _FOREIGN_KEY_KIND
    if constraint_type == "unique":
        return _INDEX_OR_UNIQUE_KIND
    return None


def _classify_call(
    call_node: ast.Call, batch_alias_to_table: dict[str, str]
) -> tuple[str, str] | None:
    """把一次函数调用分类成 (表名, kind)，无法识别时返回 None。

    识别两种调用形状：顶层 ``op.drop_index``/``op.drop_constraint``（表名来自
    ``table_name`` 参数），以及 ``with op.batch_alter_table("表名") as 别名:``
    块内的 ``别名.drop_index``/``别名.drop_constraint``（表名来自
    ``batch_alias_to_table`` 里记录的当前别名映射）。

    Args:
        call_node: 函数调用 AST 节点。
        batch_alias_to_table: 当前作用域内 batch_alter_table 别名到表名的映射。

    Returns:
        (表名, kind) 二元组；调用形状未识别、表名无法静态确定或约束类型不
        相关时返回 None。
    """
    root_and_method = _call_root_and_method(call_node)
    if root_and_method is None:
        return None
    root_name, method_name = root_and_method

    if root_name == "op":
        if method_name == "drop_index":
            table_name = _table_name_from_args(call_node)
            return (table_name, _INDEX_OR_UNIQUE_KIND) if table_name else None
        if method_name == "drop_constraint":
            table_name = _table_name_from_args(call_node)
            if table_name is None:
                return None
            kind = _classify_constraint_kind(
                _string_literal_value(_keyword_value(call_node, "type_"))
            )
            return (table_name, kind) if kind else None
        return None

    if root_name in batch_alias_to_table:
        table_name = batch_alias_to_table[root_name]
        if method_name == "drop_index":
            return (table_name, _INDEX_OR_UNIQUE_KIND)
        if method_name == "drop_constraint":
            kind = _classify_constraint_kind(
                _string_literal_value(_keyword_value(call_node, "type_"))
            )
            return (table_name, kind) if kind else None
        return None

    return None


def _extract_batch_alter_table_name(context_expr: ast.expr) -> str | None:
    """若 with 项是 ``op.batch_alter_table("表名", ...)`` 调用则取出表名。

    Args:
        context_expr: ``with`` 语句某一项的上下文表达式。

    Returns:
        表名字面量；调用形状不匹配或表名非字面量时返回 None。
    """
    if not isinstance(context_expr, ast.Call):
        return None
    if _call_root_and_method(context_expr) != ("op", "batch_alter_table"):
        return None
    if not context_expr.args:
        return None
    return _string_literal_value(context_expr.args[0])


def _extract_simple_alias_name(optional_vars: ast.expr | None) -> str | None:
    """取 ``as 别名`` 里的简单变量名。

    Args:
        optional_vars: ``with`` 语句某一项的 ``as`` 目标表达式。

    Returns:
        变量名；不是简单 Name（例如元组解包）或缺省时返回 None。
    """
    if isinstance(optional_vars, ast.Name):
        return optional_vars.id
    return None


def _collect_from_with_statement(
    with_statement: ast.With,
    batch_alias_to_table: dict[str, str],
    events: list[_DropEvent],
) -> None:
    """处理一个 ``with`` 语句：识别 batch_alter_table 别名并递归进入块体。

    Args:
        with_statement: ``with`` 语句 AST 节点。
        batch_alias_to_table: 外层作用域的别名映射；派生出的扩展映射只在本
            with 块体内生效，不修改调用方持有的字典，避免兄弟 with 块之间
            串味（不同块常常复用同一个别名变量名，如 ``batch_op``）。
        events: 收集结果的输出列表，按遍历顺序原地追加。
    """
    extended_alias_map = dict(batch_alias_to_table)
    for item in with_statement.items:
        batch_table_name = _extract_batch_alter_table_name(item.context_expr)
        alias_name = _extract_simple_alias_name(item.optional_vars)
        if batch_table_name is not None and alias_name is not None:
            extended_alias_map[alias_name] = batch_table_name
    _collect_drop_events(with_statement.body, extended_alias_map, events)


def _collect_drop_events(
    statements: list[ast.stmt],
    batch_alias_to_table: dict[str, str],
    events: list[_DropEvent],
) -> None:
    """按源码顺序递归收集语句列表里的索引/外键约束删除调用。

    Args:
        statements: 待遍历的语句列表（函数体或某个复合语句的 body/orelse/
            finalbody）。
        batch_alias_to_table: 当前作用域内 batch_alter_table 别名到表名的
            映射。
        events: 收集结果的输出列表，按遍历顺序原地追加。
    """
    for statement in statements:
        if isinstance(statement, ast.With):
            _collect_from_with_statement(statement, batch_alias_to_table, events)
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            classified = _classify_call(statement.value, batch_alias_to_table)
            if classified is not None:
                table_name, kind = classified
                events.append(
                    _DropEvent(
                        table_name=table_name,
                        kind=kind,
                        line_number=statement.lineno,
                        call_description=ast.unparse(statement.value),
                    )
                )
            continue
        # 兜底：其余复合语句（If/For/While/Try 等）递归进入其子语句列表，
        # 保证嵌套在条件分支或循环体里的调用也不会漏检。
        for child_field_name in ("body", "orelse", "finalbody"):
            child_statements = getattr(statement, child_field_name, None)
            if child_statements:
                _collect_drop_events(child_statements, batch_alias_to_table, events)


def _find_ordering_violations(
    events: list[_DropEvent], file_name: str, function_name: str
) -> list[str]:
    """按表分组后检查「外键约束先于索引删除」顺序，返回违规描述行列表。

    只处理同时具备外键约束删除事件与索引/唯一约束删除事件的表——没有外键
    约束删除事件时，无法判断该表是否真的有外键依赖，为避免误报宁可不判断
    （见模块 docstring 的检查范围说明）。

    Args:
        events: ``_collect_drop_events`` 收集到的、按源码顺序排列的事件列表。
        file_name: 所属迁移文件名，仅用于报告。
        function_name: 所属函数名（upgrade/downgrade），仅用于报告。

    Returns:
        违规描述字符串列表；无违规时为空列表。
    """
    events_by_table: dict[str, list[_DropEvent]] = {}
    for event in events:
        events_by_table.setdefault(event.table_name, []).append(event)

    violation_reports: list[str] = []
    for table_name, table_events in events_by_table.items():
        foreign_key_drop_lines = [
            event.line_number for event in table_events if event.kind == _FOREIGN_KEY_KIND
        ]
        if not foreign_key_drop_lines:
            continue
        for event in table_events:
            if event.kind != _INDEX_OR_UNIQUE_KIND:
                continue
            if any(fk_line < event.line_number for fk_line in foreign_key_drop_lines):
                continue
            violation_reports.append(
                f"{file_name}:{event.line_number} {function_name}(): 表 `{table_name}` 的 "
                f"`{event.call_description}` 出现在该表所有 "
                'drop_constraint(type_="foreignkey") 调用之前；MySQL 上会因外键约束仍引用'
                "该索引而报 1553，应把外键约束删除调整到索引/唯一约束删除之前"
            )
    return violation_reports


def _iter_migration_function_defs(module_ast: ast.Module) -> list[ast.FunctionDef]:
    """取模块顶层的 upgrade/downgrade 函数定义。

    Args:
        module_ast: 已解析的迁移文件模块 AST。

    Returns:
        找到的 upgrade()/downgrade() 函数定义节点列表，按源码顺序排列。
    """
    return [
        node
        for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name in _TRACKED_FUNCTION_NAMES
    ]


def _find_violations_in_source(source_code: str, display_file_name: str) -> list[str]:
    """解析一段迁移源码并返回外键/索引删除顺序违规列表。

    Args:
        source_code: 待检查的 Python 源码文本（模块级，含 upgrade/downgrade
            定义）。
        display_file_name: 违规报告里使用的文件名。

    Returns:
        违规描述字符串列表；无违规时为空列表。
    """
    module_ast = ast.parse(source_code)
    violation_reports: list[str] = []
    for function_def in _iter_migration_function_defs(module_ast):
        events: list[_DropEvent] = []
        _collect_drop_events(function_def.body, {}, events)
        violation_reports.extend(
            _find_ordering_violations(events, display_file_name, function_def.name)
        )
    return violation_reports


def test_migration_index_drops_do_not_precede_their_foreign_key_drops() -> None:
    """新迁移不得引入「索引/唯一约束删除先于该表外键约束删除」的顺序缺陷。

    本测试只做静态 AST 顺序检查，不连接真实数据库，因此可以作为默认门禁在
    每次改动时自动生效；检查范围的刻意收窄见模块 docstring。
    """
    migration_file_paths = sorted(VERSIONS_DIR_PATH.glob("*.py"))
    if not migration_file_paths:
        pytest.skip(f"{VERSIONS_DIR_PATH} 下没有迁移文件，无待检查对象")

    all_violation_reports: list[str] = []
    for migration_file_path in migration_file_paths:
        all_violation_reports.extend(
            _find_violations_in_source(
                migration_file_path.read_text(encoding="utf-8"), migration_file_path.name
            )
        )

    assert not all_violation_reports, (
        "以下迁移函数存在「索引/唯一约束删除先于该表外键约束删除」的顺序缺陷"
        "（MySQL 1553 风险，约定见 docs/ai-standards/alembic.md）：\n"
        + "\n".join(all_violation_reports)
    )


def test_detector_flags_index_drop_before_foreign_key_drop() -> None:
    """自测：检测逻辑必须能识别"索引删除先于外键约束删除"的顺序缺陷。

    防止检测逻辑本身被意外改坏成恒真（例如条件写反、遍历提前 return），
    导致上面的主检查永远通过而失去保护作用。
    """
    broken_source = (
        "def downgrade() -> None:\n"
        '    with op.batch_alter_table("child_table") as batch_op:\n'
        '        batch_op.drop_index("ix_child_table_parent_id")\n'
        "        batch_op.drop_constraint(\n"
        '            "fk_child_table_parent_id", type_="foreignkey"\n'
        "        )\n"
    )
    violation_reports = _find_violations_in_source(broken_source, "synthetic.py")
    assert len(violation_reports) == 1
    assert "child_table" in violation_reports[0]


def test_detector_allows_foreign_key_drop_before_index_drop() -> None:
    """自测：修复后的正确顺序（外键约束先于索引）不应被误报。"""
    fixed_source = (
        "def downgrade() -> None:\n"
        '    with op.batch_alter_table("child_table") as batch_op:\n'
        "        batch_op.drop_constraint(\n"
        '            "fk_child_table_parent_id", type_="foreignkey"\n'
        "        )\n"
        '        batch_op.drop_index("ix_child_table_parent_id")\n'
    )
    assert _find_violations_in_source(fixed_source, "synthetic.py") == []


def test_detector_ignores_index_drop_when_table_has_no_tracked_foreign_key_drop() -> None:
    """自测：函数里完全没有外键约束删除事件时不应误报（避免表级粗判假阳性）。

    对应"表本身在别处有外键，但被删的这个索引跟那个外键毫不相干，随即整表
    drop_table"的真实无害情形：此时函数体内没有对应的显式
    drop_constraint(foreignkey) 调用（外键随整表 drop_table 一起隐式消失），
    不能仅凭"存在 drop_index"就判定有 1553 风险。
    """
    harmless_source = (
        "def downgrade() -> None:\n"
        '    op.drop_index("ix_child_table_unrelated_column", table_name="child_table")\n'
        '    op.drop_table("child_table")\n'
    )
    assert _find_violations_in_source(harmless_source, "synthetic.py") == []
