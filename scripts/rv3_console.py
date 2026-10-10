"""rv-3 证据：真实 ``kc console`` + 真实 API + 真实 SQLite + Playwright 页面三态。

隔离口径（PRD rv-3 mock_boundary：无 mock）：
- 真实 ``kc console`` 进程（本机单用户 no-op 会话）；
- 真实 typed API → ``PATCH /backlog/settings`` → console DB → fresh ``GET`` 读回；
- 真实只读 SQLite 会话核对设置行；
- 浏览器只经页面控件写入（fill + 保存 / 恢复继承），不直接调 API 冒充页面通过。

产出：三态分图 + 保存 / 恢复继承后的 fresh 分图（PNG）与逐条人类可读报告。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from fixture_lib import EVIDENCE_DIR, Fixture, SCRIPTS_DIR, write_text

CAPACITY = 4
DRIVER = SCRIPTS_DIR / "rv3_driver.mjs"
REPO_NODE = EVIDENCE_DIR.parent.parent.parent  # repo root（供 createRequire 基准，仅注释）


def build_plan() -> list[dict]:
    """五步状态序列：继承 → 设置 2 → 受限 8 → 自检 4 → 恢复继承。"""
    return [
        {
            "name": "inherit",
            "action": "noop",
            "screenshot": "rv-3-backlog-concurrency-inherit.png",
            "expect_text": "并发 4（继承 runner 配置）",
            "expect_source": "inherited",
        },
        {
            "name": "policy",
            "action": "set_save",
            "value": 2,
            "screenshot": "rv-3-backlog-concurrency-policy.png",
            "expect_text": "并发 2（Backlog 设置）",
            "expect_source": "policy",
        },
        {
            "name": "capped",
            "action": "set_save",
            "value": 8,
            "screenshot": "rv-3-backlog-concurrency-capped.png",
            "expect_text": "并发 4（受 runner 容量限制），容量 4",
            "expect_source": "capped_by_capacity",
        },
        {
            "name": "saved4-fresh",
            "action": "set_save",
            "value": 4,
            "screenshot": "rv-3-backlog-concurrency-saved4-fresh.png",
            "expect_text": "并发 4（Backlog 设置）",
            "expect_source": "policy",
        },
        {
            "name": "restored-fresh",
            "action": "restore",
            "screenshot": "rv-3-backlog-concurrency-restored-fresh.png",
            "expect_text": "并发 4（继承 runner 配置）",
            "expect_source": "inherited",
        },
    ]


def run_driver(port: int, plan_path: Path, summary_path: Path) -> None:
    result = subprocess.run(
        [
            "node",
            str(DRIVER),
            "--port",
            str(port),
            "--repo",
            "rv-fixture",
            "--plan",
            str(plan_path),
            "--out-dir",
            str(EVIDENCE_DIR),
            "--summary",
            str(summary_path),
        ],
        cwd=str(EVIDENCE_DIR),
        capture_output=True,
        text=True,
        timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"driver failed (exit {result.returncode})\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def main() -> int:
    fixture = Fixture("rv3-console", capacity=CAPACITY)
    console_log = fixture.state_dir / "console.log"
    proc = fixture.start_console(console_log)
    lines: list[str] = []
    failures: list[str] = []
    try:
        # 起步态必须是「从未保存」：设置行不存在。
        row0 = fixture.read_settings_row()
        if row0 is not None:
            failures.append(f"起步设置行应缺失，实际：{row0}")

        plan = build_plan()
        plan_path = fixture.state_dir / "rv3_plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        summary_path = fixture.state_dir / "rv3_summary.jsonl"

        # 期望的 DB 状态：每步名 → 保存后的 max_parallel（None=行应删除）。
        expected_db_after = {
            "policy": 2,
            "capped": 8,
            "saved4-fresh": 4,
            "restored-fresh": None,
        }
        # 每步 fresh GET /settings 应回读的策略值（未设置＝None）。
        expected_policy_after = {"inherit": None, **expected_db_after}

        run_driver(fixture.port, plan_path, summary_path)
        records = [
            json.loads(line)
            for line in summary_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        by_step = {rec["step"]: rec for rec in records}

        for step in plan:
            name = step["name"]
            rec = by_step.get(name)
            lines.append(f"[{name}] action={step['action']}")
            if rec is None:
                failures.append(f"{name}: 驱动未产出记录")
                continue
            lines.append(f"  期望文案: {step['expect_text']}")
            lines.append(f"  实测文案: {rec['observed_text']}")
            lines.append(f"  截图: {rec['screenshot']}")
            if not rec["text_match"]:
                failures.append(
                    f"{name}: 页面文案不匹配 —— 期望「{step['expect_text']}」实测「{rec['observed_text']}」"
                )
            autopilot = rec.get("autopilot") or {}
            settings_get = rec.get("settings_get") or {}
            source = autopilot.get("ceiling_source")
            effective = autopilot.get("effective_max_parallel")
            max_parallel = autopilot.get("max_parallel")
            lines.append(
                f"  fresh GET /autopilot: ceiling_source={source} effective={effective} "
                f"max_parallel={max_parallel} runner_capacity={autopilot.get('runner_capacity')}"
            )
            if source != step["expect_source"]:
                failures.append(f"{name}: API ceiling_source={source} ≠ 期望 {step['expect_source']}")
            expected_policy = expected_policy_after[name]
            if settings_get.get("max_parallel") != expected_policy:
                failures.append(
                    f"{name}: fresh GET /settings max_parallel={settings_get.get('max_parallel')} "
                    f"≠ 期望策略 {expected_policy}"
                )
            if settings_get.get("effective_max_parallel") != effective:
                failures.append(
                    f"{name}: fresh GET /settings effective={settings_get.get('effective_max_parallel')} "
                    f"与 /autopilot effective={effective} 不同源"
                )
            # 页面数字必须与 fresh API 负载同源（非组件状态）。
            rendered = rec["observed_text"]
            if effective is not None and f"并发 {effective}" not in rendered:
                failures.append(
                    f"{name}: 页面数字与 fresh GET effective_max_parallel={effective} 不同源：{rendered}"
                )
            patch = rec.get("settings_patch")
            if step["action"] in ("set_save", "restore"):
                expected_value = expected_db_after[name]
                if patch is None:
                    failures.append(f"{name}: 缺少 PATCH /backlog/settings 写入证据")
                else:
                    request_body = patch.get("request_body") or {}
                    response_body = patch.get("response_body") or {}
                    lines.append(
                        f"  写入 status={patch.get('status')} "
                        f"请求体={json.dumps(request_body, ensure_ascii=False)} "
                        f"响应体 max_parallel={response_body.get('max_parallel')} "
                        f"effective={response_body.get('effective_max_parallel')} "
                        f"source={response_body.get('ceiling_source')}"
                    )
                    if patch.get("method") != "PATCH":
                        failures.append(f"{name}: 写入证据方法不是 PATCH：{patch.get('method')}")
                    if request_body.get("_non_json") or "max_parallel" not in request_body:
                        failures.append(
                            f"{name}: 未能从 PATCH 请求体取得 max_parallel 字段：{request_body}"
                        )
                    elif request_body.get("max_parallel") != expected_value:
                        failures.append(
                            f"{name}: PATCH 请求负载 max_parallel={request_body.get('max_parallel')} "
                            f"≠ 期望 {expected_value}"
                        )
                    if patch.get("status") != 200:
                        failures.append(f"{name}: PATCH 返回 status={patch.get('status')} ≠ 200")
                    if response_body.get("max_parallel") != expected_value:
                        failures.append(
                            f"{name}: PATCH 响应体 max_parallel={response_body.get('max_parallel')} "
                            f"≠ 期望 {expected_value}（写回值与请求不一致）"
                        )

        # 终态 DB 核对：恢复继承后设置行应被删除（行缺失＝未设置）。
        row_end = fixture.read_settings_row()
        lines.append(f"[DB 终态] 恢复继承后设置行: {row_end}")
        if row_end is not None:
            failures.append(f"恢复继承后设置行应删除，实际仍存在：{row_end}")

        lines.append("")
        if failures:
            lines.append("RESULT: FAIL")
            lines.extend(f"  - {item}" for item in failures)
        else:
            lines.append("RESULT: PASS")
            lines.append(
                "  三态文案、fresh GET 负载、PATCH 写入与终态 DB 行删除全部一致；"
                "数字与来源均由真实 API 渲染，非组件预览 / mock。"
            )

        report_path = write_text("rv-3-console-backlog-report.txt", "\n".join(lines) + "\n")
        # 附上 console 日志尾部，便于排查渲染 / 路由问题。
        tail = console_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        report_path.write_text(
            "\n".join(lines) + "\n\n--- console.log tail ---\n" + "\n".join(tail) + "\n",
            encoding="utf-8",
        )

        print("\n".join(lines))
        if failures:
            print("\nRV-3 FAILED:", failures, file=sys.stderr)
            return 1
        print("\nRV-3 PASSED")
        return 0
    finally:
        Fixture.stop(proc)
        # 端口释放需要一点时间，避免下一个 fixture 撞端口。
        time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
