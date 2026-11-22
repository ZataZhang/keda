"""真实阻塞子进程验证协议取消，正常完成作为回归对照。"""

import sys
import time

import pytest

from backend.engines.agent_runner.output_protocols import get_output_protocol_registry
from backend.engines.agent_runner.protocol_routing_runner import ProtocolRoutingProcessRunner
from backend.infrastructure.process_runner import SubprocessRunner


@pytest.mark.parametrize("protocol", ["claude-stream-json", "codex-json-lines"])
def test_cancel_stops_protocol_owned_child(tmp_path, protocol):
    runner = ProtocolRoutingProcessRunner(SubprocessRunner(), get_output_protocol_registry())
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        runner.run(
            [sys.executable, "-u", "-c", "import time; print('{}', flush=True); time.sleep(60)"],
            cwd=tmp_path,
            timeout=8,
            output_protocol=protocol,
            cancel_check=lambda: time.monotonic() - started > 0.2,
        )
    assert time.monotonic() - started < 6


@pytest.mark.parametrize("protocol", ["claude-stream-json", "codex-json-lines"])
def test_false_cancel_probe_keeps_normal_completion(tmp_path, protocol):
    runner = ProtocolRoutingProcessRunner(SubprocessRunner(), get_output_protocol_registry())
    outcome = runner.run(
        [sys.executable, "-c", "print('{}')"],
        cwd=tmp_path,
        timeout=8,
        output_protocol=protocol,
        cancel_check=lambda: False,
    )
    assert outcome.return_code == 0
