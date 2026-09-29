"""迁移旧版 ``iar init`` 写入的仓库级 ``.iar.toml``。

旧版 ``iar init`` 把全部默认值（含 ``generated_content``）逐项钉进 ``.iar.toml``。
被钉死的值不会跟随之后的默认值升级，早年初始化的仓库因此一直停在 template 模式。
现在的脚手架不再写这一段；本模块把旧脚手架留下的钉子清掉，让这些仓库重新继承当前
默认值。

只清“看起来是脚手架噪音”的钉子：

- 值必须与旧脚手架写入过的值相等；值不同说明仓库主动改过，一律保留。
- ``mode = "template"`` 只在没有自定义 ``title_template`` / ``body_template`` 时清：
  有自定义模板说明仓库是有意使用模板渲染，不替它改行为。
- ``output`` 只在没有自定义 ``prompt`` 时清：输出格式必须与提示词的回复格式一致，
  自定义提示词自己决定格式。
- ``enabled = false`` 只报告不清：最早的脚手架写的就是它，但从值上分不清是遗留还是
  有意关闭；清掉会让仓库开始调用 AI 生成，这个决定留给仓库自己。

实现是逐行编辑而不是重新序列化，这样注释和排版都保留：只删钉子本身、紧贴在它上面的
注释行，以及因此变空的表头。写回前用 ``tomllib`` 重新解析，结果必须恰好等于“原配置
去掉被清的键”，否则拒绝写入。
"""

from __future__ import annotations

import copy
import re
import shutil
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from backend.engines.agent_runner.repository_local import IARRepositoryNotInitializedError
from backend.infrastructure.config.agent_runner_settings import (
    GENERATED_CONTENT_TARGET_NAMES,
    AgentRunnerGeneratedContentSettings,
)
from backend.infrastructure.config.settings import IAR_REPOSITORY_CONFIG_FILENAME

_GENERATED_CONTENT_TABLE_NAME = "agent_runner.generated_content"

# 旧脚手架写入 generated_content 的历史值。这是冻结的历史，不能从当前默认值推导：
# 当前默认已经变了（mode / output），而旧仓库里的钉子不会跟着变。
# - mode：template 是各 target 曾经的默认；agent 在一天窗口（2026-07-01）里也曾被
#   写入过，且与当前默认相同，删掉不改变行为。
# - timeout_seconds：2026-06-15 前默认 60，之后 120。
# - enabled：最早的脚手架（2026-06-12）写 false，随后改成 true。
_LEGACY_SECTION_PIN_VALUES: dict[str, tuple[object, ...]] = {
    "enabled": (True, False),
    "fallback": ("template",),
    "max_input_chars": (20000,),
    "default_agent": ("auto",),
}
_LEGACY_TARGET_PIN_VALUES: dict[str, tuple[object, ...]] = {
    "enabled": (True, False),
    "mode": ("template", "agent"),
    "output": ("json",),
    "title_template": ("",),
    "body_template": ("",),
    "agent": ("auto",),
    "timeout_seconds": (60, 120),
    "prompt": ("",),
    "include_commit_log": (True,),
    "include_diff_stat": (True,),
}

_REMOVED_REASON = "written by an older `iar init`"
_UNLOCATED_REASON = (
    "written as dotted keys or an inline table, which this command does not edit; "
    "remove it by hand"
)

# 只按 LF 切行并保留行尾（CRLF 文件的 ``\r`` 留在行内），拼回去与原文逐字节一致。
_LINE_PATTERN = re.compile(r"[^\n]*\n|[^\n]+")
_HEADER_PATTERN = re.compile(r"^\s*\[(?P<array>\[)?\s*(?P<name>[^\]]+?)\s*\]\]?\s*(?:#.*)?$")
_PAIR_PATTERN = re.compile(r"""^\s*(?P<key>[A-Za-z0-9_-]+|"[^"]*"|'[^']*')\s*=""")

_StatementKind = Literal["header", "pair", "comment", "blank"]


class ConfigMigrationError(ValueError):
    """配置无法安全迁移：不是合法 TOML，或迁移后的解析结果与预期不符。"""


@dataclass(frozen=True)
class PinDecision:
    """一个候选钉子及其处置。

    Attributes:
        table_name: 钉子所在表的点分名，如 ``agent_runner.generated_content.draft_pr``。
        key_name: 键名。
        pinned_value: 配置里写着的值（``tomllib`` 解析后的原生类型）。
        reason: 为什么清掉 / 为什么保留，用于报告。
    """

    table_name: str
    key_name: str
    pinned_value: object
    reason: str


@dataclass(frozen=True)
class ConfigMigrationResult:
    """一次迁移的结果。

    Attributes:
        config_path: 被迁移的 ``.iar.toml`` 路径。
        original_text: 迁移前的全文。
        migrated_text: 迁移后的全文；没有可清的钉子时与 ``original_text`` 相同。
        removed_pins: 已清掉的钉子。
        kept_pins: 值与旧脚手架相同、但有意保留的钉子（含原因）。
        wrote_file: 是否已写回文件（dry-run 或无变化时为 ``False``）。
    """

    config_path: Path
    original_text: str
    migrated_text: str
    removed_pins: tuple[PinDecision, ...]
    kept_pins: tuple[PinDecision, ...]
    wrote_file: bool

    @property
    def changed(self) -> bool:
        """迁移后的文本是否与原文不同。"""
        return self.migrated_text != self.original_text


@dataclass(frozen=True)
class _Statement:
    """TOML 文本里的一条语句（表头、键值对、注释或空行）及其行范围。"""

    kind: _StatementKind
    first_line_index: int
    last_line_index: int
    # 语句所在表的点分名（表头是它自己）；数组表 ``[[x]]`` 下为 None，根表为 ""。
    table_name: str | None
    key_name: str | None = None


def migrate_repository_local_config(
    repo_root_path: Path, *, dry_run: bool = False
) -> ConfigMigrationResult:
    """清掉旧脚手架在 ``.iar.toml`` 里钉死的 generated_content 默认值。

    Args:
        repo_root_path: 目标 Git 仓库根目录。
        dry_run: 为 True 时只计算并返回结果，不写文件。

    Returns:
        迁移结果；``wrote_file`` 表示是否真的写了文件。

    Raises:
        IARRepositoryNotInitializedError: 仓库根目录没有 ``.iar.toml``。
        ConfigMigrationError: ``.iar.toml`` 不是合法 TOML，或迁移结果未通过写回前校验。
    """
    config_path = repo_root_path / IAR_REPOSITORY_CONFIG_FILENAME
    if not config_path.is_file():
        raise IARRepositoryNotInitializedError(repo_root_path, config_path)

    original_text = config_path.read_bytes().decode("utf-8")
    original_config = _parse_toml(original_text, source_label=str(config_path))

    removable_pins, intentionally_kept_pins = _plan_pins(original_config)
    toml_lines = _LINE_PATTERN.findall(original_text)
    statements = _scan_statements(toml_lines)
    deleted_line_indexes, located_pins, unlocated_pins = _plan_line_deletions(
        statements, removable_pins
    )

    migrated_text = original_text
    if located_pins:
        migrated_text = _rebuild_text(toml_lines, deleted_line_indexes)
        _verify_migrated_text(original_config, migrated_text, located_pins)

    wrote_file = bool(located_pins) and not dry_run
    if wrote_file:
        # 先写同目录临时文件再替换，进程中途退出也不会留下写了一半的配置；
        # 解析符号链接后替换真实文件，保留原文件权限。
        target_path = config_path.resolve()
        temporary_path = target_path.with_name(f"{target_path.name}.migrating")
        try:
            temporary_path.write_bytes(migrated_text.encode("utf-8"))
            shutil.copymode(target_path, temporary_path)
            temporary_path.replace(target_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    return ConfigMigrationResult(
        config_path=config_path,
        original_text=original_text,
        migrated_text=migrated_text,
        removed_pins=tuple(located_pins),
        kept_pins=tuple(
            [*intentionally_kept_pins]
            + [replace(pin, reason=_UNLOCATED_REASON) for pin in unlocated_pins]
        ),
        wrote_file=wrote_file,
    )


def _parse_toml(toml_text: str, *, source_label: str) -> dict[str, object]:
    """解析 TOML，失败时抛带来源的 ``ConfigMigrationError``。"""
    try:
        return tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigMigrationError(f"{source_label} is not valid TOML: {exc}") from exc


def _nested_table(config: dict[str, object], dotted_table_name: str) -> dict[str, object] | None:
    """按点分表名取嵌套表；路径不存在或不是表时返回 None。"""
    current_node: object = config
    for name_part in dotted_table_name.split("."):
        if not isinstance(current_node, dict) or name_part not in current_node:
            return None
        current_node = current_node[name_part]
    return current_node if isinstance(current_node, dict) else None


def _is_blank_text(config_value: object) -> bool:
    """模板 / prompt 类字段是否为空（字符串或字符串数组，全空白也算空）。"""
    if isinstance(config_value, list):
        return not "".join(str(text_part) for text_part in config_value).strip()
    return not str(config_value).strip()


def _keep_reason(
    table_name: str, key_name: str, pinned_value: object, table_values: dict[str, object]
) -> str | None:
    """旧脚手架值虽然可清，但清掉会改变仓库有意为之的行为时，返回保留原因。"""
    if key_name == "enabled" and pinned_value is False:
        return (
            "generation stays off here; delete this line to follow the current default, "
            "which is on"
        )
    if key_name == "mode" and pinned_value == "template":
        has_custom_template = any(
            not _is_blank_text(table_values.get(template_key, ""))
            for template_key in ("title_template", "body_template")
        )
        if has_custom_template:
            return "custom title_template / body_template: template rendering looks intentional"
    if key_name == "output" and not _is_blank_text(table_values.get("prompt", "")):
        # 钉子恰好等于该 target 现在的默认输出格式时，清掉不改变任何行为，无需保留。
        default_target_settings = getattr(
            AgentRunnerGeneratedContentSettings(), table_name.rpartition(".")[2]
        )
        if pinned_value != default_target_settings.output:
            return "custom prompt: the prompt decides its own reply format"
    return None


def _plan_pins(original_config: dict[str, object]) -> tuple[list[PinDecision], list[PinDecision]]:
    """按解析后的配置决定哪些钉子该清、哪些像旧脚手架值却要保留。

    Returns:
        ``(removable_pins, intentionally_kept_pins)``。值与旧脚手架不同的钉子不出现在
        任何一边：那是仓库主动配置，原样保留且无需报告。
    """
    legacy_values_by_table_name = {
        _GENERATED_CONTENT_TABLE_NAME: _LEGACY_SECTION_PIN_VALUES,
        **{
            f"{_GENERATED_CONTENT_TABLE_NAME}.{target_name}": _LEGACY_TARGET_PIN_VALUES
            for target_name in GENERATED_CONTENT_TARGET_NAMES
        },
    }
    removable_pins: list[PinDecision] = []
    intentionally_kept_pins: list[PinDecision] = []
    for table_name, legacy_values_by_key in legacy_values_by_table_name.items():
        table_values = _nested_table(original_config, table_name)
        if table_values is None:
            continue
        for key_name, pinned_value in table_values.items():
            # 类型也要一致：`True == 1`，但整数 1 不是旧脚手架写过的布尔值。
            is_legacy_pin = any(
                type(pinned_value) is type(legacy_value) and pinned_value == legacy_value
                for legacy_value in legacy_values_by_key.get(key_name, ())
            )
            if not is_legacy_pin:
                continue
            keep_reason = _keep_reason(table_name, key_name, pinned_value, table_values)
            if keep_reason is None:
                removable_pins.append(
                    PinDecision(table_name, key_name, pinned_value, _REMOVED_REASON)
                )
            else:
                intentionally_kept_pins.append(
                    PinDecision(table_name, key_name, pinned_value, keep_reason)
                )
    return removable_pins, intentionally_kept_pins


def _plan_line_deletions(
    statements: list[_Statement], removable_pins: list[PinDecision]
) -> tuple[set[int], list[PinDecision], list[PinDecision]]:
    """把要清的钉子落到具体行：钉子本身、紧贴其上的注释行，以及因此变空的表头。

    Returns:
        ``(deleted_line_indexes, located_pins, unlocated_pins)``。``unlocated_pins`` 是
        在文本里找不到“表头下的独立 ``key = value`` 语句”的钉子（点分键 / 内联表写法），
        本命令不编辑它们。
    """
    statement_by_line_index = {
        line_index: statement
        for statement in statements
        for line_index in range(statement.first_line_index, statement.last_line_index + 1)
    }
    pair_by_location = {
        (statement.table_name, statement.key_name): statement
        for statement in statements
        if statement.kind == "pair" and statement.key_name is not None
    }

    deleted_line_indexes: set[int] = set()
    located_pins: list[PinDecision] = []
    unlocated_pins: list[PinDecision] = []
    for pin in removable_pins:
        pair_statement = pair_by_location.get((pin.table_name, pin.key_name))
        if pair_statement is None:
            unlocated_pins.append(pin)
            continue
        located_pins.append(pin)
        deleted_line_indexes.update(
            range(pair_statement.first_line_index, pair_statement.last_line_index + 1)
        )
        deleted_line_indexes.update(
            _attached_comment_line_indexes(statement_by_line_index, pair_statement.first_line_index)
        )

    header_by_table_name = {
        statement.table_name: statement for statement in statements if statement.kind == "header"
    }
    for table_name in {pin.table_name for pin in located_pins}:
        header_statement = header_by_table_name.get(table_name)
        if header_statement is None:
            continue
        has_remaining_pair = any(
            statement.kind == "pair"
            and statement.table_name == table_name
            and statement.first_line_index not in deleted_line_indexes
            for statement in statements
        )
        if has_remaining_pair:
            continue
        deleted_line_indexes.add(header_statement.first_line_index)
        deleted_line_indexes.update(
            _attached_comment_line_indexes(
                statement_by_line_index, header_statement.first_line_index
            )
        )
    return deleted_line_indexes, located_pins, unlocated_pins


def _attached_comment_line_indexes(
    statement_by_line_index: dict[int, _Statement], first_line_index: int
) -> list[int]:
    """紧贴在某条语句上方（中间没有空行）的连续注释行下标。"""
    attached_line_indexes: list[int] = []
    probe_line_index = first_line_index - 1
    while probe_line_index >= 0 and statement_by_line_index[probe_line_index].kind == "comment":
        attached_line_indexes.append(probe_line_index)
        probe_line_index -= 1
    return attached_line_indexes


def _rebuild_text(toml_lines: list[str], deleted_line_indexes: set[int]) -> str:
    """去掉被删行后拼回全文，并收拢删除留下的连续空行。"""
    rebuilt_lines: list[str] = []
    previous_line_index = -1
    for line_index, line_text in enumerate(toml_lines):
        if line_index in deleted_line_indexes:
            continue
        has_deletion_gap_before = line_index != previous_line_index + 1
        previous_line_index = line_index
        # 删除区两侧各有一个空行时只留一个，避免拼出连续空行。
        if (
            has_deletion_gap_before
            and not line_text.strip()
            and (not rebuilt_lines or not rebuilt_lines[-1].strip())
        ):
            continue
        rebuilt_lines.append(line_text)
    # 一直删到文件末尾时，不留悬空的尾部空行。
    if max(deleted_line_indexes) == len(toml_lines) - 1:
        while rebuilt_lines and not rebuilt_lines[-1].strip():
            rebuilt_lines.pop()
    return "".join(rebuilt_lines)


def _verify_migrated_text(
    original_config: dict[str, object],
    migrated_text: str,
    located_pins: list[PinDecision],
) -> None:
    """迁移后的解析结果必须恰好等于“原配置去掉被清的键”，否则拒绝写入。

    空表在比较时视为不存在：清空后被剪掉的表头在 ``tomllib`` 里不留痕迹，而显式的
    空表对配置模型没有任何含义。
    """
    expected_config = copy.deepcopy(original_config)
    for pin in located_pins:
        expected_table = _nested_table(expected_config, pin.table_name)
        assert expected_table is not None  # noqa: S101 - 规划阶段已确认该表存在
        del expected_table[pin.key_name]
    migrated_config = _parse_toml(migrated_text, source_label="migrated config")
    if _without_empty_tables(migrated_config) != _without_empty_tables(expected_config):
        raise ConfigMigrationError(
            "migrated config does not match the expected result; nothing was written"
        )


def _without_empty_tables(config_node: object) -> object:
    """递归去掉空表，用于忽略“被剪掉的空表头”造成的差异。"""
    if not isinstance(config_node, dict):
        return config_node
    pruned_config = {
        key_name: _without_empty_tables(child_node) for key_name, child_node in config_node.items()
    }
    return {key_name: child for key_name, child in pruned_config.items() if child != {}}


def _scan_statements(toml_lines: list[str]) -> list[_Statement]:
    """把逐行文本切成语句：多行字符串 / 多行数组里的行归属于同一条键值对语句。"""
    statements: list[_Statement] = []
    current_table_name: str | None = ""
    line_index = 0
    while line_index < len(toml_lines):
        line_text = toml_lines[line_index].rstrip("\r\n")
        stripped_text = line_text.strip()
        if not stripped_text or stripped_text.startswith("#"):
            comment_or_blank: _StatementKind = "comment" if stripped_text else "blank"
            statements.append(
                _Statement(comment_or_blank, line_index, line_index, current_table_name)
            )
            line_index += 1
            continue
        header_match = _HEADER_PATTERN.match(line_text)
        if header_match:
            current_table_name = (
                None if header_match["array"] else re.sub(r"\s+", "", header_match["name"])
            )
            statements.append(_Statement("header", line_index, line_index, current_table_name))
            line_index += 1
            continue
        pair_match = _PAIR_PATTERN.match(line_text)
        value_offset = pair_match.end() if pair_match else line_text.find("=") + 1
        last_line_index = _find_statement_end(toml_lines, line_index, value_offset)
        raw_key_text = pair_match["key"] if pair_match else None
        # 带引号的键只去掉两侧各一个引号；点分键匹配不上，key_name 为 None。
        key_name = raw_key_text[1:-1] if raw_key_text and raw_key_text[0] in "\"'" else raw_key_text
        statements.append(
            _Statement("pair", line_index, last_line_index, current_table_name, key_name)
        )
        line_index = last_line_index + 1
    return statements


def _find_statement_end(toml_lines: list[str], first_line_index: int, value_offset: int) -> int:
    """返回从 ``first_line_index`` 起的键值语句的最后一行下标。

    从值的起点逐字符扫描：跳过字符串（含多行字符串），在括号深度回到 0 且不在多行
    字符串内的那一行结束。扫不到结尾时退回最后一行，交给写回前的 ``tomllib`` 校验拒绝。
    """
    bracket_depth = 0
    multiline_delimiter: str | None = None
    line_index = first_line_index
    column_index = value_offset
    while line_index < len(toml_lines):
        line_text = toml_lines[line_index]
        while column_index < len(line_text):
            if multiline_delimiter is not None:
                if multiline_delimiter == '"""' and line_text[column_index] == "\\":
                    column_index += 2
                elif line_text.startswith(multiline_delimiter, column_index):
                    multiline_delimiter = None
                    column_index += 3
                else:
                    column_index += 1
                continue
            current_char = line_text[column_index]
            if current_char == "#":
                break
            if line_text.startswith(('"""', "'''"), column_index):
                multiline_delimiter = line_text[column_index : column_index + 3]
                column_index += 3
            elif current_char in "\"'":
                column_index = _skip_single_line_string(line_text, column_index)
            else:
                if current_char in "[{":
                    bracket_depth += 1
                elif current_char in "]}":
                    bracket_depth -= 1
                column_index += 1
        if bracket_depth <= 0 and multiline_delimiter is None:
            return line_index
        line_index += 1
        column_index = 0
    return len(toml_lines) - 1


def _skip_single_line_string(line_text: str, opening_quote_index: int) -> int:
    """返回单行字符串（基本 / 字面）收尾引号之后的列下标；没有收尾时返回行尾。"""
    quote_char = line_text[opening_quote_index]
    column_index = opening_quote_index + 1
    while column_index < len(line_text):
        if quote_char == '"' and line_text[column_index] == "\\":
            column_index += 2
        elif line_text[column_index] == quote_char:
            return column_index + 1
        else:
            column_index += 1
    return column_index


__all__ = [
    "ConfigMigrationError",
    "ConfigMigrationResult",
    "PinDecision",
    "migrate_repository_local_config",
]
