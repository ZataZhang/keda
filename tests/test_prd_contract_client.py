"""``prd_contract_client`` 的边界行为测试。

客户端的职责只有一件：把 PRD 文本交给 prd skill 的 ``scripts/prd_contract.py``，
把 JSON 拿回来。因此这里断言的全是**接缝**行为——脚本缺失、执行失败、输出非法、
超时、payload 形状不对——而不是解析结果本身（那些归模板仓的
``tests/test_prd_contract.py``）。

用临时目录里的一次性脚本当被调方：这样既不依赖真实 skill 是否安装，也不是在
测试树里复刻一份解析实现（这些脚本只做"输出固定文本 / 退出码 / 睡一会儿"）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.shared import prd_contract_client as client


def _write_script(tmp_path: Path, body: str) -> Path:
    """把一个一次性 Python 脚本写到临时目录并返回其路径。"""
    script_path = tmp_path / "prd_contract.py"
    script_path.write_text(body, encoding="utf-8")
    return script_path


class TestRunContractScript:
    """``_run_contract_script`` 的失败面与成功面。"""

    def test_missing_script_is_reported_with_the_install_hint(self, tmp_path: Path) -> None:
        """脚本不存在时给出可行动的报错（指向 kc init / KEDACODE_PRD_SKILL_PATH）。"""
        missing_script_path = tmp_path / "scripts" / "prd_contract.py"

        with pytest.raises(client.PrdContractError) as exc_info:
            client._run_contract_script(missing_script_path, [])

        message = str(exc_info.value)
        assert "不存在" in message
        assert "kc init" in message
        assert "KEDACODE_PRD_SKILL_PATH" in message

    def test_nonzero_exit_surfaces_the_exit_code_and_stderr(self, tmp_path: Path) -> None:
        """非零退出要把退出码与 stderr 带出来，否则失败无从诊断。"""
        script_path = _write_script(
            tmp_path, "import sys\nsys.stderr.write('backend exploded')\nsys.exit(3)\n"
        )

        with pytest.raises(client.PrdContractError) as exc_info:
            client._run_contract_script(script_path, [])

        message = str(exc_info.value)
        assert "退出码 3" in message
        assert "backend exploded" in message

    def test_stdout_that_is_not_json_is_reported(self, tmp_path: Path) -> None:
        """stdout 不是合法 JSON 时报错并回显输出片段。"""
        script_path = _write_script(tmp_path, "print('this is not json')\n")

        with pytest.raises(client.PrdContractError) as exc_info:
            client._run_contract_script(script_path, [])

        assert "不是合法 JSON" in str(exc_info.value)

    def test_non_object_json_top_level_is_reported(self, tmp_path: Path) -> None:
        """JSON 顶层必须是对象（list 之类要拒掉）。"""
        script_path = _write_script(tmp_path, "print('[]')\n")

        with pytest.raises(client.PrdContractError, match="顶层不是对象"):
            client._run_contract_script(script_path, [])

    def test_timeout_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """脚本挂住时按超时报错，而不是无限等待。"""
        script_path = _write_script(tmp_path, "import time\ntime.sleep(30)\n")
        monkeypatch.setattr(client, "DEFAULT_TIMEOUT_SECONDS", 0.5)

        with pytest.raises(client.PrdContractError, match="超时"):
            client._run_contract_script(script_path, [])

    def test_successful_run_returns_the_payload(self, tmp_path: Path) -> None:
        """正常输出时原样返回 payload 字典。"""
        payload = {"contract_version": 4, "prds": [], "errors": []}
        script_path = _write_script(tmp_path, f"import json\nprint(json.dumps({payload!r}))\n")

        assert client._run_contract_script(script_path, []) == payload


class TestParsePrdContract:
    """``parse_prd_contract`` 对 payload 形状的处理。"""

    def test_returns_the_first_prd_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """返回 ``prds[0]``（含 checklist / change_log 子对象）。"""
        first_prd = {"path": "x.md", "checklist": {"section_found": True}, "change_log": {}}
        payload_json = json.dumps({"prds": [first_prd], "errors": []})
        script_path = _write_script(tmp_path, f"print({payload_json!r})\n")
        monkeypatch.setattr(client, "resolve_prd_contract_script", lambda *a, **k: script_path)

        assert client.parse_prd_contract("## Acceptance Checklist\n") == first_prd

    def test_empty_prd_list_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """payload 里没有 PRD 结果时报错并带上 errors。"""
        script_path = _write_script(
            tmp_path,
            "import json\nprint(json.dumps({'prds': [], 'errors': [{'path': 'a', 'error': 'boom'}]}))\n",
        )
        monkeypatch.setattr(client, "resolve_prd_contract_script", lambda *a, **k: script_path)

        with pytest.raises(client.PrdContractError) as exc_info:
            client.parse_prd_contract("## Acceptance Checklist\n")

        message = str(exc_info.value)
        assert "没有返回任何 PRD 结果" in message
        assert "boom" in message


class TestContractVersion:
    """``contract_version`` 的取值与类型防御。"""

    def test_reads_the_integer_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """payload 里的整数版本号原样返回。"""
        script_path = _write_script(
            tmp_path, "import json\nprint(json.dumps({'contract_version': 4}))\n"
        )
        monkeypatch.setattr(client, "resolve_prd_contract_script", lambda *a, **k: script_path)

        assert client.contract_version() == 4

    def test_non_integer_version_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """版本号缺失或不是整数时返回 ``None``，由调用方决定怎么处理。"""
        script_path = _write_script(
            tmp_path, "import json\nprint(json.dumps({'contract_version': 'four'}))\n"
        )
        monkeypatch.setattr(client, "resolve_prd_contract_script", lambda *a, **k: script_path)

        assert client.contract_version() is None
