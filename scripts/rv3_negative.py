"""rv-3 现场负控：往 console **静态构建产物**注入旧口径，证明该检查点能变红。

旧行为＝控制条数字读伪造默认值（未设置时显示 2），而不是生效值。注入点只碰
``src/backend/api/static/console/``（gitignored 构建产物，非生产源码、非代码 diff），
脚本结束后必定还原并校验字节一致，因此不影响任何源码。

产出 ``rv-3-negative-control.txt``：红态报告（RESULT: FAIL 与逐条断言失败原因）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from fixture_lib import EVIDENCE_DIR, SCRIPTS_DIR, write_text

REPO_ROOT = EVIDENCE_DIR.parent.parent.parent
CHUNKS_DIR = REPO_ROOT / "src" / "backend" / "api" / "static" / "console" / "_next" / "static" / "chunks"
# 生效值渲染处：页面数字取自 fresh API 的 effective_max_parallel。
ANCHOR = 'e.effective_max_parallel,"（"'
# 注入为旧口径：数字取策略值，未设置时落到伪造默认 2。
INJECT = '(e.max_parallel??2),"（"'


def find_chunk() -> Path:
    hits = [
        path
        for path in sorted(CHUNKS_DIR.glob("*.js"))
        if ANCHOR in path.read_text(encoding="utf-8", errors="replace")
    ]
    if len(hits) != 1:
        raise RuntimeError(f"注入锚点在 bundle 中命中 {len(hits)} 个文件，需人工确认：{hits}")
    return hits[0]


def run_rv3() -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "rv3_console.py"],
        cwd=str(SCRIPTS_DIR),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.returncode, result.stdout + "\n--- stderr ---\n" + result.stderr


def main() -> int:
    chunk = find_chunk()
    original = chunk.read_bytes()
    backup = chunk.with_suffix(chunk.suffix + ".rv3-negbak")
    shutil.copyfile(chunk, backup)
    lines = [
        "rv-3 现场负控：在 console 静态构建产物里把控制条数字改回旧口径（伪造默认 2）。",
        f"注入文件：{chunk.relative_to(REPO_ROOT)}（gitignored 构建产物，非生产源码）",
        f"锚点：{ANCHOR}",
        f"替换为：{INJECT}",
        "",
    ]
    try:
        mutated = original.decode("utf-8")
        if ANCHOR not in mutated:
            raise RuntimeError("锚点已不存在，注入未生效")
        chunk.write_text(mutated.replace(ANCHOR, INJECT, 1), encoding="utf-8")
        lines.append("注入完成，运行 rv3_console.py（期望 RESULT: FAIL / 非零退出）。")
        exit_code, output = run_rv3()
        lines.append(f"rv3_console.py exit={exit_code}")
        lines.append("")
        lines.append(output)
        expected_red = exit_code != 0 and "RESULT: FAIL" in output and "并发 2" in output
        lines.append("")
        lines.append("NEG CONTROL: RED 已复现" if expected_red else "NEG CONTROL: 未复现红态（负控无效）")
        write_text("rv-3-negative-control.txt", "\n".join(lines) + "\n")
        if not expected_red:
            print("\n".join(lines))
            return 1
        print("\n".join(lines[-6:]))
        print("NEG CONTROL PASSED (rv-3 可变红，且已还原 bundle)")
        return 0
    finally:
        chunk.write_bytes(original)
        backup.unlink(missing_ok=True)
        restored_ok = chunk.read_bytes() == original
        print(f"bundle 还原：{'OK 字节一致' if restored_ok else 'FAILED 需人工还原 ' + str(backup)}")
        if not restored_ok:
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
