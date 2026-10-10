"""捕获统计集成用例经真实路由返回的 JSON 证据。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
TEST_MODULE_PATH = REPOSITORY_ROOT / "tests/test_agent_runner_console_api.py"
OUTPUT_PATH = Path(sys.argv[1]).resolve()

module_spec = importlib.util.spec_from_file_location(
    "test_agent_runner_console_api_capture", TEST_MODULE_PATH
)
if module_spec is None or module_spec.loader is None:
    raise RuntimeError(f"无法加载 API 集成测试：{TEST_MODULE_PATH}")

test_module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(test_module)

captured_responses: list[dict[str, object]] = []
original_get = test_module.client.get


def capture_stats_response(url: str, *args: object, **kwargs: object):
    """保存真实 TestClient 路由响应，同时返回原始响应供用例断言。"""
    response = original_get(url, *args, **kwargs)
    if "/console/stats/agent-performance" in str(url):
        captured_responses.append(
            {
                "status_code": response.status_code,
                "request_url": str(response.request.url),
                "json": response.json(),
            }
        )
    return response


test_module.client.get = capture_stats_response
try:
    with tempfile.TemporaryDirectory(prefix="agent-performance-evidence-") as temp_dir:
        temporary_root = Path(temp_dir) / "pytest-env"
        temporary_root.mkdir()
        monkeypatch = pytest.MonkeyPatch()
        try:
            environment = test_module.console_environment.__wrapped__(
                temporary_root,
                monkeypatch,
            )
            test_module.test_agent_performance_stats_reads_fresh_sqlite_rows_through_api(
                environment
            )
        finally:
            monkeypatch.undo()
finally:
    test_module.client.get = original_get

if len(captured_responses) != 3:
    raise RuntimeError(f"期望捕获 3 个过滤场景响应，实际为 {len(captured_responses)} 个")
if any(response["status_code"] != 200 for response in captured_responses):
    raise RuntimeError("统计 API 至少有一个仓库 / 时间窗口请求未返回 200")

OUTPUT_PATH.write_text(
    json.dumps(captured_responses, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"captured_responses={len(captured_responses)}")
print("captured_statuses=200,200,200")
print(f"captured_json={OUTPUT_PATH.name}")
