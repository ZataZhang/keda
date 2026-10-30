"""Negative control for rv-1: neutralize the assertion judgment only.

The same exit-0 fixture must flip from ``failed`` to ``passed`` once the
orchestrator stops calling ``_validate_stdout_assertions``. This proves the
assertion — not the exit code — is the discriminating check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.core.shared.models.agent_runner import AppConfig, ValidationConfig
from backend.core.use_cases import agent_runner_evidence_assertions, agent_runner_evidence_check
from backend.infrastructure.process_runner import SubprocessRunner


def main() -> int:
    worktree_path = Path(sys.argv[1]).resolve()
    fixture_dir = worktree_path / "tasks" / "evidence" / "P1-rv1-fixture"
    # 只解除断言判定；manifest 解析、真实 bash 复跑、超时与结果分类保持原样。
    # 编排通过断言模块属性调用，因此替换同名模块属性即可让判定变为只看退出码。
    agent_runner_evidence_assertions._validate_stdout_assertions = (  # noqa: SLF001
        lambda block, command_result: None
    )
    report = agent_runner_evidence_check.run_evidence_check(
        worktree_path,
        AppConfig(
            validation=ValidationConfig(
                evidence_dir="tasks/evidence",
                reexecute_cache_enabled=False,
            )
        ),
        SubprocessRunner(),
        agent_runner_evidence_check.EvidenceCheckRequest(
            evidence_dir=fixture_dir,
            timeout_seconds=300,
            gate_timeout_seconds=300,
        ),
    )
    print(
        json.dumps(
            {
                "negative_control_passed": report.passed,
                "negative_control_status": report.item_results[0].status,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
