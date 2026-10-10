"""为 rv-1 与 rv-2 输出绑定本次工作树源码内容的哈希。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = Path(__file__).resolve().parents[1]
RV1_PATHS = [
    "src/backend/api/routes/agent_runner_console.py",
    "src/backend/core/shared/interfaces/runner_console.py",
    "src/backend/core/shared/statistics.py",
    "src/backend/core/use_cases/console_stats.py",
    "src/backend/infrastructure/persistence/console_store.py",
    "src/backend/infrastructure/persistence/console_store_history.py",
    "src/backend/infrastructure/persistence/console_store_lifecycle.py",
    "tests/test_agent_runner_console_api.py",
    "tests/test_console_stats.py",
]
RV2_PATHS = [
    "docs/guides/agent-runner.md",
    "frontend-public/app/(app)/app/stats/page.tsx",
    "frontend-public/lib/api/console.ts",
    "frontend-public/lib/api/types.ts",
    "mkdocs.yml",
    "scripts/shared/just/process_guard.sh",
    "src/backend/api/static/console/_next/static/chunks/17ov6-6ryva9v.js",
    "tests/playwright-e2e/tests/smoke/stats-agent-performance.spec.ts",
]
RV2_PATHS.extend(
    path.relative_to(REPOSITORY_ROOT).as_posix()
    for path in sorted((REPOSITORY_ROOT / "frontend-public/components/stats").glob("**/*.tsx"))
)


def render_tree_evidence(item_name: str, paths: list[str]) -> str:
    """返回指定 RV 源文件集合对应的提交与内容哈希。"""
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree_hash = hashlib.sha256()
    lines = [f"item={item_name}", f"HEAD={head_commit}"]
    for relative_path in paths:
        source_path = REPOSITORY_ROOT / relative_path
        source_bytes = source_path.read_bytes()
        file_hash = hashlib.sha256(source_bytes).hexdigest()
        lines.append(f"{file_hash}  {relative_path}")
        tree_hash.update(relative_path.encode("utf-8"))
        tree_hash.update(b"\0")
        tree_hash.update(source_bytes)
        tree_hash.update(b"\0")
    lines.insert(2, f"worktree_source_sha256={tree_hash.hexdigest()}")
    return "\n".join(lines) + "\n"


def main() -> None:
    """只为指定的 Realistic Validation 条目刷新源码摘要。"""
    if len(sys.argv) != 2 or sys.argv[1] not in {"1", "2"}:
        raise SystemExit("usage: capture_final_tree_evidence.py <1|2>")

    item_number = sys.argv[1]
    item_name = f"rv-{item_number}"
    source_paths = RV1_PATHS if item_number == "1" else RV2_PATHS
    evidence_path = EVIDENCE_ROOT / f"rv-{item_number}-final-tree.txt"
    evidence_path.write_text(
        render_tree_evidence(item_name, source_paths),
        encoding="utf-8",
    )
    print(f"wrote={evidence_path.name}")


if __name__ == "__main__":
    main()
