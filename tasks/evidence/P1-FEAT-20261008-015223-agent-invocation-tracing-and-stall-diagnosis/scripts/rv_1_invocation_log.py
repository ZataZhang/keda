"""rv-1 oracle：既有 ``kc logs --issue N`` 展示每次真实调用的完整身份与结局。

跑两个隔离场景（都是真实 ``kc run`` 子进程，Git / SQLite / CLI 全真，只有 GitHub
与 agent executable 是夹具）：

- 场景 A：首个执行器失败并回退到第二个执行器——回退必须是**两条独立记录**并用
  ``retry_of`` / ``retry_reason=executor_fallback`` 串起来，绝不合并成一条成功记录。
- 场景 B：交付门禁在 staged 校验处失败一次，触发 Fix Agent——``phase=fix`` 必须是
  独立的一条调用记录。

读取一律走**新起的 CLI 进程**（``kc logs --repo-id ... --issue ...``）与**新开的
SQLite 连接**，不复用运行中的内存状态。

退出码非 0 即检查点不成立；不使用 ``|| true`` 之类兜底。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from rv_harness import REPO_ID, Harness, build_harness, cleanup_harness  # noqa: E402
from rv_markers import Invocation, pair_invocations, parse_markers, summarize  # noqa: E402

EVIDENCE_DIR = SCRIPTS_DIR.parent
ISSUE_A = 242
ISSUE_B = 243

ISSUE_BODY = """\
### Summary

RV harness Issue：给示例仓库补一份说明文档。

### Acceptance

- [ ] 文档存在
"""

VERIFICATION_GATE_SCRIPT = """\
#!/bin/sh
# RV 夹具交付门禁：第 {fail_on} 次调用失败，其余通过。计数文件用绝对路径，
# 因为 runner 会以净化后的子进程环境执行验证命令。
set -u
counter_file="{counter_path}"
count=$(cat "$counter_file" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$counter_file"
echo "rv verification gate: call #$count"
if [ "$count" -eq {fail_on} ]; then
  echo "rv verification gate: deliberate failure on call #$count" >&2
  exit 1
fi
exit 0
"""


class OracleFailure(AssertionError):
    """检查点不成立。"""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise OracleFailure(message)


def _read_logs_via_new_cli_process(harness: Harness, issue_number: int) -> str:
    """用**新进程**的既有 ``kc logs --issue`` 入口读取该 Issue 的输出。"""
    completed = harness.run_kc(
        ["logs", "--repo-id", REPO_ID, "--issue", str(issue_number), "--lines", "2000"]
    )
    _check(
        completed.returncode == 0,
        f"kc logs --issue {issue_number} 退出码 {completed.returncode}，"
        f"stderr={completed.stderr[-800:]}",
    )
    return completed.stdout


def _cross_check_ledger(harness: Harness, issue_number: int, invocations: list[Invocation]) -> None:
    """新开的 SQLite 连接读账本，与日志标记逐条对齐。"""
    rows = harness.invocation_rows(issue_number)
    log_ids = [item.invocation_id for item in invocations]
    ledger_started = [row["invocation_id"] for row in rows if row["event_type"] == "invocation_started"]
    ledger_finished = [
        row["invocation_id"] for row in rows if row["event_type"] == "invocation_finished"
    ]
    _check(
        ledger_started == log_ids,
        f"账本 started 序列 {ledger_started} 与日志标记序列 {log_ids} 不一致",
    )
    _check(
        set(ledger_finished) <= set(log_ids),
        f"账本出现了日志里没有的 finished 事件：{sorted(set(ledger_finished) - set(log_ids))}",
    )
    _check(
        all(row["repo_id"] == REPO_ID for row in rows),
        "账本里出现了别的 repo_id，并发/隔离口径被破坏",
    )


def _assert_identity_fields(invocations: list[Invocation]) -> None:
    """每条调用都必须带齐身份、阶段、执行器、模型来源、结局与耗时。"""
    _check(bool(invocations), "没有解析到任何调用标记")
    for item in invocations:
        summary = summarize([item])[0]
        for field_name in ("invocation_id", "run", "issue", "phase", "role", "executor"):
            _check(bool(summary[field_name]), f"调用 {item.invocation_id} 缺字段 {field_name}")
        _check(
            summary["closed"],
            f"调用 {item.invocation_id} 未闭合（只有 start 没有 end）",
        )
        for field_name in ("outcome", "model_source", "duration_s", "model_requested"):
            _check(
                summary[field_name] is not None,
                f"调用 {item.invocation_id} 的结束标记缺字段 {field_name}",
            )
        _check(
            float(summary["duration_s"]) >= 0.0,
            f"调用 {item.invocation_id} 的 duration_s 不是合法非负数：{summary['duration_s']}",
        )
        _check(
            summary["model_source"] in ("executor_report", "unknown"),
            f"调用 {item.invocation_id} 的 model_source 越出闭集：{summary['model_source']}",
        )


def scenario_a_fallback(root: Path) -> dict[str, Any]:
    """场景 A：首个执行器失败并回退——回退必须是独立的一条调用记录。"""
    plan = {
        "agents": {
            "claude": {
                "roles": {
                    "implement": [
                        {
                            "action": "fail",
                            "exit": 1,
                            "stderr": "fixture: primary executor could not complete the task",
                            "reported_model": "claude-sonnet-4-5-20250929",
                            "session_id": "sess-rv1-claude",
                        }
                    ]
                }
            },
            "kimi": {
                "roles": {
                    "implement": [
                        {
                            "action": "implement",
                            "files": [
                                {
                                    "path": "docs/rv1-fallback-delivery.md",
                                    "content": "# RV-1 fallback delivery\n\n由夹具执行器 kimi 产出。\n",
                                }
                            ],
                            "commit_message": "docs: rv-1 harness fallback delivery",
                        }
                    ],
                    "review": [
                        {
                            "action": "stdout",
                            "text": '{"verdict": "approved", "summary": "rv harness fixture review"}',
                        }
                    ],
                }
            },
        },
        "default": [{"action": "stdout", "text": "fixture agent: nothing to do"}],
    }
    harness = build_harness(
        root / "scenario-a",
        issue_number=ISSUE_A,
        issue_title="RV-1 harness: fallback scenario",
        issue_body=ISSUE_BODY,
        plan=plan,
        repo_config_overrides={
            "runner": {
                "agent_fallback_order": ["claude", "kimi"],
                "max_recovery_attempts": 0,
                "transient_retry_attempts": 0,
                "timeout_seconds": 120,
                "inactivity_timeout_seconds": 90,
                "fix_agent_enabled": False,
            },
            # 预设绑定 claude + 一个模型 id：回退到 kimi 时该绑定会被丢弃，
            # 于是"请求模型"在回退那次调用里如实显示为未下发（rv-2 的核心事实）。
            "presets": {"rv-claude": {"agent": "claude", "model": "claude-sonnet-4-5"}},
        },
    )
    try:
        return _run_and_collect(
            harness,
            ISSUE_A,
            extra_run_args=["--preset", "rv-claude"],
            scenario_name="A-executor-fallback",
        )
    finally:
        cleanup_harness(harness)


def scenario_b_fix(root: Path) -> dict[str, Any]:
    """场景 B：staged 交付门禁失败一次，触发 Fix Agent（``phase=fix``）。"""
    counter_path = root / "scenario-b" / "verification-counter.txt"
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    gate_script = VERIFICATION_GATE_SCRIPT.format(fail_on=2, counter_path=counter_path)
    plan = {
        "agents": {
            "kimi": {
                "roles": {
                    "implement": [
                        {
                            "action": "implement",
                            "files": [
                                {
                                    "path": "docs/rv1-fix-delivery.md",
                                    "content": "# RV-1 fix delivery\n\n由夹具执行器 kimi 产出。\n",
                                }
                            ],
                            "commit_message": "docs: rv-1 harness implementation delivery",
                        }
                    ],
                    "fix": [
                        {
                            "action": "implement",
                            "files": [
                                {
                                    "path": "docs/rv1-fix-repair.md",
                                    "content": "# RV-1 fix repair\n\nFix Agent 产出。\n",
                                }
                            ],
                            "commit_message": "fix: rv-1 harness staged verification repair",
                        }
                    ],
                    "review": [
                        {
                            "action": "stdout",
                            "text": '{"verdict": "approved", "summary": "rv harness fixture review"}',
                        }
                    ],
                }
            }
        },
        "default": [{"action": "stdout", "text": "fixture agent: nothing to do"}],
    }
    harness = build_harness(
        root / "scenario-b",
        issue_number=ISSUE_B,
        issue_title="RV-1 harness: fix agent scenario",
        issue_body=ISSUE_BODY,
        plan=plan,
        seed_files={"rv-verification-gate.sh": gate_script},
        repo_config_overrides={
            "runner": {
                "agent_fallback_order": ["kimi"],
                "default_agent": "kimi",
                "max_recovery_attempts": 1,
                "transient_retry_attempts": 0,
                "timeout_seconds": 120,
                "inactivity_timeout_seconds": 90,
                "fix_agent_enabled": True,
                "verification_commands": ["sh ./rv-verification-gate.sh"],
            }
        },
    )
    try:
        return _run_and_collect(
            harness,
            ISSUE_B,
            extra_run_args=[],
            scenario_name="B-fix-agent",
        )
    finally:
        cleanup_harness(harness)


def _run_and_collect(
    harness: Harness,
    issue_number: int,
    *,
    extra_run_args: list[str],
    scenario_name: str,
) -> dict[str, Any]:
    """跑一次真实 ``kc run``，再用新进程读日志、新连接读账本。"""
    run_completed = harness.run_kc(
        [
            "run",
            "--repo-id",
            REPO_ID,
            "--issue",
            str(issue_number),
            "--max-issues",
            "1",
            *extra_run_args,
        ]
    )
    log_stdout = _read_logs_via_new_cli_process(harness, issue_number)
    markers = parse_markers(log_stdout)
    invocations = pair_invocations(markers)
    if not invocations:
        # 排障用：一条标记都没解析到时，先把真实 run 的输出摊开，否则无从定位。
        print(f"--- kc run 退出码 {run_completed.returncode} ---")
        print(run_completed.stdout[-4000:])
        print("--- kc run stderr ---")
        print(run_completed.stderr[-4000:])
        print(f"--- kc logs stdout（{len(log_stdout)} 字符）---")
        print(log_stdout[-2000:])
        print(f"--- 日志目录 {harness.issue_log_dir} ---")
        print([path.name for path in harness.issue_log_paths(issue_number)])
    _assert_identity_fields(invocations)
    _cross_check_ledger(harness, issue_number, invocations)
    return {
        "scenario": scenario_name,
        "issue_number": issue_number,
        "run_returncode": run_completed.returncode,
        "run_stdout_tail": run_completed.stdout[-2500:],
        "run_stderr_tail": run_completed.stderr[-1500:],
        "logs_returncode": 0,
        "marker_lines": [marker.raw for marker in markers],
        "invocations": summarize(invocations),
        "probe_lines": harness.probe_lines(),
        "gh_labels": harness.gh_state()["issues"][str(issue_number)]["labels"],
        "gh_pulls": [
            {"number": pull["number"], "url": pull["url"], "headRefName": pull["headRefName"]}
            for pull in harness.gh_state()["pulls"]
        ],
        "ledger_rows": harness.invocation_rows(issue_number),
        "implementation_tree": harness.implementation_tree(),
        "fixture_fingerprint": harness.fixture_fingerprint(),
    }


def _assert_fallback_is_two_records(result_a: dict[str, Any]) -> None:
    """负控核心：回退是两条记录，不是一条合并的成功记录。"""
    invocations = result_a["invocations"]
    implementation_calls = [item for item in invocations if item["phase"] == "implementation"]
    _check(
        len(implementation_calls) >= 2,
        f"期望至少两次 implementation 调用（失败 + 回退），实际 {len(implementation_calls)} 次："
        f"{json.dumps(implementation_calls, ensure_ascii=False)}",
    )
    executors = [item["executor"] for item in implementation_calls]
    _check(
        executors[0] == "claude" and "kimi" in executors[1:],
        f"期望首个执行器 claude 失败后回退到 kimi，实际执行器序列 {executors}",
    )
    first_call = implementation_calls[0]
    # 失败的执行器必须留下"这次失败了"的痕迹，而不是被回退后的成功记录盖掉。
    # outcome 的字面值取决于执行层是抛异常（error）还是返回非零码（failed），
    # 两者都诚实，所以只断言"不是 ok 且带失败分类与非零退出码"。
    _check(
        first_call["outcome"] != "ok" and first_call["failure_category"] is not None,
        f"首个执行器失败却记为 outcome={first_call['outcome']} "
        f"failure_category={first_call['failure_category']}",
    )
    _check(
        first_call["exit_code"] == "1",
        f"首个执行器的退出码应为 1，实际 {first_call['exit_code']}",
    )
    # 失败前执行器已经自报过模型名：这条记录必须保住它（回归点——协议路由
    # 执行器曾在新建失败异常时把 reported_model 丢掉，导致记成"未提供"）。
    _check(
        first_call["model_source"] == "executor_report"
        and first_call["model_reported"] == "claude-sonnet-4-5-20250929",
        f"失败调用的执行器自报模型丢失：model_source={first_call['model_source']} "
        f"model_reported={first_call['model_reported']}",
    )
    fallback_call = next(item for item in implementation_calls if item["executor"] == "kimi")
    _check(
        fallback_call["retry_of"] == first_call["invocation_id"],
        f"回退调用的 retry_of 应指向首个调用 {first_call['invocation_id']}，"
        f"实际 {fallback_call['retry_of']}",
    )
    _check(
        fallback_call["retry_reason"] == "executor_fallback",
        f"回退调用的 retry_reason 应为 executor_fallback，实际 {fallback_call['retry_reason']}",
    )
    ids = [item["invocation_id"] for item in invocations]
    _check(
        len(ids) == len(set(ids)),
        f"invocation id 出现重复，说明多次调用被合并成一条：{ids}",
    )
    starts = [line for line in result_a["marker_lines"] if "[iar-invocation-start]" in line]
    ends = [line for line in result_a["marker_lines"] if "[iar-invocation-end]" in line]
    _check(
        len(starts) == len(invocations),
        f"start 标记数 {len(starts)} 与配对出的调用数 {len(invocations)} 不一致",
    )
    _check(
        len(ends) == len([item for item in invocations if item["closed"]]),
        f"end 标记数 {len(ends)} 与已闭合调用数不一致",
    )


def _assert_fix_is_separate_record(result_b: dict[str, Any]) -> None:
    """场景 B：``phase=fix`` 是独立的一条调用记录。"""
    phases = [item["phase"] for item in result_b["invocations"]]
    _check(
        "fix" in phases,
        f"期望出现 phase=fix 的独立调用记录，实际阶段序列 {phases}；"
        f"runner stderr 尾部：{result_b['run_stderr_tail'][-800:]}",
    )
    fix_call = next(item for item in result_b["invocations"] if item["phase"] == "fix")
    _check(fix_call["role"] == "fixer", f"fix 调用的 role 应为 fixer，实际 {fix_call['role']}")
    _check(
        "implementation" in phases,
        f"期望同时存在 implementation 调用，实际阶段序列 {phases}",
    )


def _write_log_sample(result_a: dict[str, Any], result_b: dict[str, Any]) -> Path:
    """写出面向人审的日志样例（rv-1 的 presentation 产物）。"""
    sample_path = EVIDENCE_DIR / "rv-1-invocation-log-sample.md"
    lines = [
        "# rv-1 调用日志样例（既有 `kc logs --issue N` 入口原样输出）",
        "",
        "打开方式：",
        "",
        "```bash",
        f"open {sample_path}",
        "```",
        "",
        "下面的标记行是 `kc logs --repo-id rv-harness --issue <N> --lines 2000` 在**新进程**里",
        "读出来的原文（只截去行首时间戳之外的内容，未做任何改写）。",
        "",
        "## 场景 A — 首个执行器失败并回退（Issue #%d）" % ISSUE_A,
        "",
        "```text",
    ]
    lines.extend(result_a["marker_lines"])
    lines.extend(
        [
            "```",
            "",
            "解读要点：",
            "",
            "- 两次 `phase=implementation` 是**两条独立记录**，`invocation=` 各不相同；",
            "- 第二条的 `retry_of=` 指向第一条，`retry_reason=executor_fallback`；",
            "- `executor=` 记的是实际执行器：先是 `claude`，回退后是 `kimi`；",
            "- `model_source=executor_report` 表示执行器自己报了模型；`unknown` 表示没报，",
            "  此时 `model_reported=未提供`，**不会**用配置值冒充；",
            "- `duration_s=` 是单调时钟算出的墙钟耗时，不采信执行器自报的时长。",
            "",
            "## 场景 B — 交付门禁失败一次触发 Fix Agent（Issue #%d）" % ISSUE_B,
            "",
            "```text",
        ]
    )
    lines.extend(result_b["marker_lines"])
    lines.extend(
        [
            "```",
            "",
            "解读要点：`phase=fix` / `role=fixer` 是独立的一条调用记录，不会被折叠进",
            "`implementation` 那一条。",
            "",
            "## 覆盖披露",
            "",
            "- 本样例覆盖的阶段：`implementation`、`fix`、`review`（场景 A/B 合计）。",
            "- `verification` 阶段（独立复验门禁）只在 Issue 正文带 Realistic Validation 块与",
            "  `iar:structured-evidence` marker 时才会触发；harness Issue 不带这两者，因此该",
            "  阶段由 `src/backend/core/use_cases/run_verifier_agent.py` 的接线与已提交测试覆盖，",
            "  不在本次 CLI 场景内。",
            "- GitHub 与 agent executable 是夹具；Git、SQLite、CLI 全真。",
            "",
        ]
    )
    sample_path.write_text("\n".join(lines), encoding="utf-8")
    return sample_path


def main() -> int:
    """跑两个场景、执行全部断言、落证据文件。"""
    import contextlib
    import os
    import tempfile

    keep_harness = bool(os.environ.get("RV_KEEP_HARNESS"))
    persistent_root = Path("/tmp/rv1-invocation-debug")
    if keep_harness:
        persistent_root.mkdir(parents=True, exist_ok=True)
    root_context = (
        contextlib.nullcontext(str(persistent_root))
        if keep_harness
        else tempfile.TemporaryDirectory(prefix="rv1-invocation-")
    )

    with root_context as raw_root:
        root = Path(raw_root)
        print("=== rv-1 场景 A：首个执行器失败并回退 ===")
        result_a = scenario_a_fallback(root)
        print(f"kc run 退出码：{result_a['run_returncode']}")
        print(f"解析到 {len(result_a['invocations'])} 次调用")
        for row in result_a["invocations"]:
            print(json.dumps(row, ensure_ascii=False))
        _assert_fallback_is_two_records(result_a)

        print()
        print("=== rv-1 场景 B：交付门禁失败一次触发 Fix Agent ===")
        result_b = scenario_b_fix(root)
        print(f"kc run 退出码：{result_b['run_returncode']}")
        print(f"解析到 {len(result_b['invocations'])} 次调用")
        _assert_fix_is_separate_record(result_b)

    sample_path = _write_log_sample(result_a, result_b)

    transcript_path = EVIDENCE_DIR / "rv-1-run-transcript.json"
    transcript_path.write_text(
        json.dumps(
            {"scenario_a": result_a, "scenario_b": result_b},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print()
    print("=== 场景 A 调用清单 ===")
    for row in result_a["invocations"]:
        print(json.dumps(row, ensure_ascii=False))
    print()
    print("=== 场景 B 调用清单 ===")
    for row in result_b["invocations"]:
        print(json.dumps(row, ensure_ascii=False))
    print()
    print(f"实现树 src 短哈希：{result_a['implementation_tree']}")
    print(f"夹具脚本指纹：{result_a['fixture_fingerprint']}")
    print(f"人审样例：{sample_path}")
    print(f"完整 transcript：{transcript_path}")
    print()
    print("rv-1 PASS：回退与修复都是独立调用记录，身份/阶段/执行器/模型来源/结局/耗时齐全，")
    print("且全部经既有 `kc logs --issue N` 入口在新进程里读出，并与新连接读到的账本一致。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OracleFailure as failure:
        print(f"\nrv-1 FAIL: {failure}", file=sys.stderr)
        raise SystemExit(1) from failure
