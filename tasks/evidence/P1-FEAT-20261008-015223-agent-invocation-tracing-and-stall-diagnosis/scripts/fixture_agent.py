"""Realistic Validation 夹具用的确定性执行器（Issue #242）。

被 ``bin/claude`` / ``bin/kimi`` 等 shim 调用，agent 名由 ``RV_FIXTURE_AGENT``
给出。行为完全由 ``RV_FIXTURE_PLAN`` 指向的 JSON 计划决定：按 (agent, role) 分
队列消费，队列耗尽后重复最后一步，因此同一角色被多次调用时行为可预期。

角色由提示词里的固定开头识别（``agent_review`` / ``agent_runner_feedback`` /
``run_verifier_agent`` 的模板首行），识别不出时归入 ``implement``。

每次调用都往 ``RV_FIXTURE_PROBE`` 追加一行 JSONL 探针，记录 argv、cwd 与提示词
开头——这是"fixture 真的被真实进程边界调用了"的独立证据。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("review", "Pre-PR Review for Issue #"),
    ("fix", "Fix the verification failure for GitHub Issue #"),
    ("verify", "You are an INDEPENDENT verifier for issue #"),
    ("supervisor", "Post-PR Supervisor"),
)

_DEFAULT_STEP: dict[str, Any] = {"action": "stdout", "text": "fixture agent: nothing to do"}


def _env_path(name: str) -> Path | None:
    raw_value = os.environ.get(name)
    return Path(raw_value) if raw_value else None


def _read_plan() -> dict[str, Any]:
    plan_path = _env_path("RV_FIXTURE_PLAN")
    if plan_path is None:
        return {}
    with plan_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _extract_prompt(argv: list[str]) -> str:
    """按声明式投递形态取回提示词：``--prompt <p>``、``-p`` 尾部或整个尾部。"""
    for flag in ("--prompt", "-p"):
        if flag in argv:
            index = argv.index(flag)
            if index + 1 < len(argv) and argv[index + 1] not in ("", flag):
                candidate = argv[index + 1]
                if not candidate.startswith("-"):
                    return candidate
    return argv[-1] if argv else ""


def _resolve_role(prompt: str) -> str:
    for role, marker in _ROLE_MARKERS:
        if marker in prompt:
            return role
    return "implement"


def _next_step(plan: dict[str, Any], agent: str, role: str) -> dict[str, Any]:
    """取出该 (agent, role) 的下一步，并把消费进度写回计数文件。"""
    agent_plan = plan.get("agents", {}).get(agent, {})
    queue = agent_plan.get("roles", {}).get(role) or agent_plan.get("default")
    if not queue:
        queue = plan.get("default") or [_DEFAULT_STEP]
    counter_dir = _env_path("RV_FIXTURE_COUNTER_DIR")
    used = 0
    if counter_dir is not None:
        counter_path = counter_dir / f"{agent}-{role}.count"
        if counter_path.exists():
            used = int(counter_path.read_text(encoding="utf-8").strip() or "0")
        counter_path.write_text(str(used + 1), encoding="utf-8")
    return queue[min(used, len(queue) - 1)]


def _record_probe(agent: str, role: str, argv: list[str], prompt: str) -> None:
    probe_path = _env_path("RV_FIXTURE_PROBE")
    if probe_path is None:
        return
    record = {
        "agent": agent,
        "role": role,
        "argv": argv,
        "cwd": os.getcwd(),
        "prompt_head": prompt[:160],
        "prompt_length": len(prompt),
    }
    with probe_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _emit_reported_model(step: dict[str, Any], argv: list[str]) -> None:
    """按调用方的输出协议形态自报模型；没有声明就什么都不报。"""
    reported_model = step.get("reported_model")
    session_id = step.get("session_id")
    if reported_model is None and session_id is None:
        return
    if "--output-format" in argv and "stream-json" in argv:
        init_event: dict[str, Any] = {"type": "system", "subtype": "init"}
        if session_id is not None:
            init_event["session_id"] = session_id
        if reported_model is not None:
            init_event["model"] = reported_model
        print(json.dumps(init_event, ensure_ascii=False), flush=True)
        return
    if reported_model is not None:
        # plain 协议靠事后逐行解析：一行合法 JSON 且带顶层 model 即命中。
        print(json.dumps({"model": reported_model}, ensure_ascii=False), flush=True)


def _write_work(step: dict[str, Any]) -> None:
    """在 cwd 里产生真实改动，并按受限提交代理契约写 commit-request.json。"""
    for change in step.get("files", []):
        target = Path(change["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change["content"], encoding="utf-8")
    if "commit_message" in step:
        request_dir = Path(".agent-runner")
        request_dir.mkdir(parents=True, exist_ok=True)
        (request_dir / "commit-request.json").write_text(
            json.dumps({"commit_message": step["commit_message"]}, ensure_ascii=False),
            encoding="utf-8",
        )


def main(argv: list[str]) -> int:
    agent = os.environ.get("RV_FIXTURE_AGENT") or (argv[0] if argv else "unknown")
    plan = _read_plan()
    prompt = _extract_prompt(argv[1:])
    role = _resolve_role(prompt)
    _record_probe(agent, role, argv, prompt)
    step = _next_step(plan, agent, role)

    _emit_reported_model(step, argv)

    action = step.get("action", "stdout")
    if action == "sleep":
        time.sleep(float(step.get("seconds", 30)))
    elif action == "hang":
        # 静默期超时用：一直不产出任何字节，直到被 runner 杀掉。
        while True:
            time.sleep(5)

    if step.get("text"):
        print(step["text"], flush=True)
    if step.get("stderr"):
        print(step["stderr"], file=sys.stderr, flush=True)
    if action in ("implement", "fix"):
        _write_work(step)
    return int(step.get("exit", 0))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
