"""从冻结判分材料复用的证据清单夹具。"""

import json
from pathlib import Path


def _item(number: int, command: str, *, assertions: list[dict] | None = None) -> dict:
    """构造一条最小合法 evidence block；断言字段按需附加。"""
    item_block = {
        "item_number": number,
        "item_name": f"checkpoint {number}",
        "command": command,
        "evidence_files": [f"rv-{number}-probe.txt"],
        "output_summary": f"command {number} output captured.",
        "explanation": "The command probes the real entry point.",
        "risks": "Local-only fixture.",
        "negative_control": "Break the probe and re-run.",
        "expected_fail": "The check reports a failed item.",
    }
    if assertions is not None:
        item_block["stdout_assertions"] = assertions
    return item_block


def _write_manifest(evidence_dir: Path, item_blocks: list[dict]) -> None:
    """把 evidence.json 写入目录（不做门禁式文件存在性校验）。"""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "language": "zh-CN", "items": item_blocks}
    (evidence_dir / "evidence.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
