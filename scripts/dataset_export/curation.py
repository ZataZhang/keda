"""后期补全（curation）叠加层：在不改动原始记录的前提下补全样本。

数据集会不断优化。原始层只记事实，输入偏弱的样本（PR 描述、会话首句、提交信息）靠后期的人
或更强的 Agent 补成真正的任务描述。补全记录放在单独的目录里（以后可以放进数据集仓库），按
样本 id 对应：

- **不改原始记录**：导出时才叠加；补全过输入的样本把原来的输入保留在 ``input.original``。
- **只追加**：同一样本可以有多条补全，同一字段以时间最晚的为准。
- **注明来源**：每条记录写明是人还是 Agent（哪个模型）补的、何时补的、有没有人复核；
  Agent 补的在人复核之前仍算输入偏弱，不计入 ``eval_ready``。
- **同样脱敏**：补全后的输入文本也过一遍 :mod:`redaction`。

补全文件是 ``*.jsonl``，每行一条::

    {"schema_version": 1, "sample_id": "owner/repo#commit-1a2b3c4d5e",
     "at": "2026-10-22T08:00:00Z",
     "by": {"kind": "human", "name": "zata"},
     "input": {"title": "...", "body": "..."},
     "exclude": false, "check_recipe": "pytest", "notes": "..."}

``by.kind`` 是 ``human`` 或 ``agent``；Agent 补全可再带 ``by.model`` 与 ``reviewed_by_human``。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.dataset_export.export_samples import WEAK_INPUT_PROVENANCES, is_eval_ready
from scripts.dataset_export.redaction import redact_text

_logger = logging.getLogger(__name__)

CURATION_SCHEMA_VERSION = 1
KIND_HUMAN = "human"
KIND_AGENT = "agent"
PROVENANCE_CURATED_HUMAN = "curated_human"
PROVENANCE_CURATED_AGENT_REVIEWED = "curated_agent_reviewed"
PROVENANCE_CURATED_AGENT = "curated_agent"
INPUT_PATCH_FIELDS = ("title", "body")
WORKLIST_MAX_COMMIT_SUBJECTS = 30


@dataclass(frozen=True)
class CurationEntry:
    """一条补全记录。"""

    sample_id: str
    at: str
    by_kind: str
    by_name: str | None
    by_model: str | None
    reviewed_by_human: bool
    input_patch: dict[str, str]
    exclude: bool | None
    check_recipe: str | None
    notes: str | None


@dataclass
class CurationLoad:
    """读取补全目录的结果。"""

    entries_by_sample: dict[str, list[CurationEntry]] = field(default_factory=dict)
    skipped_lines: int = 0


@dataclass
class CurationReport:
    """叠加补全的结果：多少样本被补全，哪些补全记录找不到对应样本。"""

    applied_samples: int = 0
    unknown_sample_ids: list[str] = field(default_factory=list)


def parse_curation_record(record: Any) -> CurationEntry | None:
    """校验并解析一行补全记录；格式不对、版本未知或字段不认识都返回 ``None``。"""
    if not isinstance(record, dict):
        return None
    version = record.get("schema_version")
    if not isinstance(version, int) or version > CURATION_SCHEMA_VERSION:
        return None
    sample_id = record.get("sample_id")
    by = record.get("by")
    if not isinstance(sample_id, str) or not isinstance(by, dict):
        return None
    if by.get("kind") not in (KIND_HUMAN, KIND_AGENT):
        return None
    input_patch = record.get("input") or {}
    if not isinstance(input_patch, dict) or not set(input_patch) <= set(INPUT_PATCH_FIELDS):
        return None
    if not all(isinstance(value, str) for value in input_patch.values()):
        return None
    exclude = record.get("exclude")
    if exclude is not None and not isinstance(exclude, bool):
        return None
    return CurationEntry(
        sample_id=sample_id,
        at=str(record.get("at") or ""),
        by_kind=by["kind"],
        by_name=by.get("name"),
        by_model=by.get("model"),
        reviewed_by_human=bool(record.get("reviewed_by_human")),
        input_patch=dict(input_patch),
        exclude=exclude,
        check_recipe=record.get("check_recipe"),
        notes=record.get("notes"),
    )


def load_curation_entries(curation_dir: Path) -> CurationLoad:
    """读取目录下全部 ``*.jsonl``；无效行跳过并告警，同一样本的记录按时间升序（同时间保持文件顺序）。"""
    load = CurationLoad()
    for curation_path in sorted(curation_dir.glob("*.jsonl")):
        for line_number, line in enumerate(
            curation_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                entry = parse_curation_record(json.loads(line))
            except json.JSONDecodeError:
                entry = None
            if entry is None:
                load.skipped_lines += 1
                _logger.warning("skip invalid curation line %s:%d", curation_path, line_number)
                continue
            load.entries_by_sample.setdefault(entry.sample_id, []).append(entry)
    for entries in load.entries_by_sample.values():
        entries.sort(key=lambda entry: entry.at)
    return load


def provenance_for(entry: CurationEntry) -> str:
    """补全后的输入来源：人补的可信；Agent 补的要有人复核才可信。"""
    if entry.by_kind == KIND_HUMAN:
        return PROVENANCE_CURATED_HUMAN
    if entry.reviewed_by_human:
        return PROVENANCE_CURATED_AGENT_REVIEWED
    return PROVENANCE_CURATED_AGENT


def _apply_input_patches(sample: dict[str, Any], entries: list[CurationEntry]) -> None:
    """把输入补全叠加到样本上：保留原输入、脱敏补全文本、按最后写正文的人决定来源。"""
    patched_entries = [entry for entry in entries if entry.input_patch]
    if not patched_entries:
        return
    sample_input = sample["input"]
    sample_input.setdefault(
        "original", {key: sample_input.get(key) for key in ("provenance", "title", "body")}
    )
    redaction_count = int(sample_input.get("redaction_count") or 0)
    for entry in patched_entries:
        for field_name, value in entry.input_patch.items():
            redacted = redact_text(value)
            sample_input[field_name] = redacted.text
            redaction_count += redacted.redaction_count
    sample_input["redaction_count"] = redaction_count
    body_entries = [entry for entry in patched_entries if "body" in entry.input_patch]
    sample_input["provenance"] = provenance_for((body_entries or patched_entries)[-1])


def apply_curation(samples: list[dict[str, Any]], load: CurationLoad) -> CurationReport:
    """把补全记录叠加到样本上（原地修改），并重算 ``eval_ready``。"""
    report = CurationReport()
    sample_ids = {sample["sample_id"] for sample in samples}
    report.unknown_sample_ids = sorted(set(load.entries_by_sample) - sample_ids)
    for sample in samples:
        entries = load.entries_by_sample.get(sample["sample_id"])
        if not entries:
            continue
        _apply_input_patches(sample, entries)
        excluded_entries = [entry for entry in entries if entry.exclude is not None]
        excluded = excluded_entries[-1].exclude if excluded_entries else False
        recipe_entries = [entry for entry in entries if entry.check_recipe]
        last_entry = entries[-1]
        sample["curation"] = {
            "entry_count": len(entries),
            "last_by": {
                "kind": last_entry.by_kind,
                "name": last_entry.by_name,
                "model": last_entry.by_model,
                "at": last_entry.at,
                "reviewed_by_human": last_entry.reviewed_by_human,
            },
            "exclude": bool(excluded),
            "check_recipe": recipe_entries[-1].check_recipe if recipe_entries else None,
            "notes": [entry.notes for entry in entries if entry.notes],
        }
        labels = sample["labels"]
        labels["excluded"] = bool(excluded)
        labels["eval_ready"] = not excluded and is_eval_ready(
            labels=labels, input_provenance=sample["input"]["provenance"]
        )
        report.applied_samples += 1
    return report


def needs_curation(sample: dict[str, Any]) -> bool:
    """输入偏弱、是开发类、有提交、也没被排除的样本，值得补全。"""
    labels = sample["labels"]
    return bool(
        sample["input"]["provenance"] in WEAK_INPUT_PROVENANCES
        and labels["task_kind"] == "development"
        and labels["has_commits"]
        and not labels.get("excluded")
    )


def build_curation_worklist(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """生成待补全清单：每条带足够的上下文，交给人或更强的 Agent 写出任务描述。

    清单里是已脱敏的输入与提交主题，不含代码本身；补全记录写回 ``--curation-dir`` 即可。
    """
    worklist: list[dict[str, Any]] = []
    for sample in samples:
        if not needs_curation(sample):
            continue
        code = sample.get("code") or {}
        worklist.append(
            {
                "sample_id": sample["sample_id"],
                "origin": sample["origin"],
                "repo_id": sample["repo_id"],
                "current_input": {
                    "provenance": sample["input"]["provenance"],
                    "title": sample["input"].get("title"),
                    "body": sample["input"].get("body"),
                },
                "commit_subjects": [
                    commit["subject"]
                    for commit in code.get("commits", [])[:WORKLIST_MAX_COMMIT_SUBJECTS]
                ],
                "base_sha": code.get("base_sha"),
                "final_sha": code.get("final_sha"),
                "size": sample.get("size"),
                "linked_sample_ids": (sample.get("session") or {}).get("linked_sample_ids", []),
                "linked_session_ids": sample.get("linked_session_ids", []),
            }
        )
    return worklist
