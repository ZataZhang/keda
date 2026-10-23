"""把导出的样本归档成"每个样本一个文件"，供提交进数据集仓库。

归档的目的有两个：**长期保存**（本机的快照、会话、运行记录都可能丢或被清理）和**留下版本**。

- **每个样本一个文件**，按来源与仓库分目录，原地更新；版本靠 git 历史保存，所以每天定期导出
  不会像"每次复制一份全量文件"那样让仓库膨胀；
- **序列化稳定**（键排序、固定缩进、不含导出时间），内容没变文件就逐字节不变，git 里没有噪音；
- **只增不删**：本地来源消失（会话被清理、运行记录被删）后，归档里已有的样本继续保留；
- **不碰补全层**：补全记录放在数据集仓库的 ``curation/`` 里，由另一条路径叠加，归档只写 ``raw/``。

本模块只写文件，不做任何 git 操作；提交与推送由调用方（定时脚本）负责。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.dataset_export.sanitize import find_sensitive_kinds

RAW_DIRNAME = "raw"
MANIFEST_FILENAME = "MANIFEST.json"
MANIFEST_SCHEMA_VERSION = 1
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
STATUS_ADDED = "added"
STATUS_UPDATED = "updated"
STATUS_UNCHANGED = "unchanged"


class ArchivePrivacyError(Exception):
    """归档前的兜底检查发现样本里还有个人信息；此时不写任何文件。"""


@dataclass
class ArchiveReport:
    """一次归档的结果：各状态的样本数，以及归档目录里按来源统计的总数。"""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    total_by_origin: dict[str, int] = field(default_factory=dict)


def safe_name(text: str) -> str:
    """把任意文本变成安全的目录名或文件名（路径分隔符、``#`` 等都换成下划线）。"""
    safe = _UNSAFE_NAME_CHARS.sub("_", text).strip(".")
    return safe or "_"


def sample_file_path(archive_dir: Path, sample: dict[str, Any]) -> Path:
    """某个样本在归档里的位置：``raw/<来源>/<仓库>/<样本 id>.json``。"""
    return (
        archive_dir
        / RAW_DIRNAME
        / safe_name(sample["origin"])
        / safe_name(sample["repo_id"])
        / f"{safe_name(sample['sample_id'])}.json"
    )


def serialize_sample(sample: dict[str, Any]) -> str:
    """稳定的序列化：键排序、两格缩进、末尾换行；样本本身不含导出时间。"""
    return json.dumps(sample, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_sample_if_changed(path: Path, sample: dict[str, Any]) -> str:
    """内容变了才写；返回 ``added`` / ``updated`` / ``unchanged``。"""
    content = serialize_sample(sample)
    if path.is_file():
        if path.read_text(encoding="utf-8") == content:
            return STATUS_UNCHANGED
        status = STATUS_UPDATED
    else:
        status = STATUS_ADDED
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return status


def _count_archived_by_origin(archive_dir: Path) -> dict[str, int]:
    """统计归档目录里每种来源已有多少个样本文件。"""
    raw_dir = archive_dir / RAW_DIRNAME
    if not raw_dir.is_dir():
        return {}
    counts = Counter(path.relative_to(raw_dir).parts[0] for path in raw_dir.rglob("*.json"))
    return dict(sorted(counts.items()))


def archive_samples(samples: list[dict[str, Any]], archive_dir: Path) -> ArchiveReport:
    """把样本逐个写进归档目录，并刷新 ``MANIFEST.json``（只含数量，不含时间，避免每天的无意义改动）。

    写入之前先对**全部**样本做兜底检查：只要有一个样本还带着邮箱、本机路径、令牌或私钥，
    就抛 :class:`ArchivePrivacyError` 且一个文件都不写，宁可让定时任务失败，也不把个人信息写进长期保存的仓库。
    """
    findings = {
        sample["sample_id"]: sorted(kinds)
        for sample in samples
        if (kinds := find_sensitive_kinds(sample))
    }
    if findings:
        shown = "; ".join(
            f"{sample_id}: {', '.join(kinds)}" for sample_id, kinds in list(findings.items())[:5]
        )
        raise ArchivePrivacyError(
            f"{len(findings)} 个样本含敏感信息，已拒绝归档（只列种类，不列值）：{shown}"
        )
    report = ArchiveReport()
    for sample in samples:
        status = write_sample_if_changed(sample_file_path(archive_dir, sample), sample)
        setattr(report, status, getattr(report, status) + 1)
    report.total_by_origin = _count_archived_by_origin(archive_dir)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "total_by_origin": report.total_by_origin,
        "total": sum(report.total_by_origin.values()),
    }
    manifest_path = archive_dir / MANIFEST_FILENAME
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != manifest_text:
        archive_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest_text, encoding="utf-8")
    return report
