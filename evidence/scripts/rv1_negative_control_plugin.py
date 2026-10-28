import pytest
import backend.core.use_cases.run_agent_daemon as daemon_module

@pytest.fixture(autouse=True)
def _drop_execution_slots(monkeypatch):
    original_run_once = daemon_module.run_once
    def _blocking_run_once(**kwargs):
        kwargs["execution_slots"] = None  # 退回"一轮跑完再看下一轮"
        return original_run_once(**kwargs)
    monkeypatch.setattr(daemon_module, "run_once", _blocking_run_once)
