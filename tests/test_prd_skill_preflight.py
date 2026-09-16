"""prd skill Machine Contract 启动预检测试（PRD: iar-prd-skill-alignment, FR-6 / rv-4）。

预检入口：``ensure_prd_machine_contract_available``（挂在 ``run_preflight_checks``，
即 daemon 起执行循环前）。用 ``IAR_PRD_SKILL_PATH`` env 覆盖指向缺失/低版本/正常
的 fixture，断言 fail fast 与放行路径。

注意：会话级 conftest fixture 已把 ``IAR_PRD_SKILL_PATH`` 指向合法 fixture，
这里的失败用例用 ``monkeypatch.setenv`` 再覆盖为坏路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.shared.prd_machine_contract import (
    SUPPORTED_MACHINE_CONTRACT_VERSION,
    PrdSkillPreflightError,
    parse_machine_contract_version,
)
from backend.core.use_cases.agent_runner_publish import run_preflight_checks
from backend.core.use_cases.generated_content import (
    ensure_prd_machine_contract_available,
)
from tests.conftest import FakeProcessRunner
from backend.core.shared.models.agent_runner import CommandResult


def _skill_fixture(tmp_path: Path, text: str) -> Path:
    skill_path = tmp_path / "prd" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(text, encoding="utf-8")
    return skill_path


def test_skill_preflight_version_marker_parsing() -> None:
    """版本标记解析：独立标记行 → 整数；无标记 → None。"""
    assert (
        parse_machine_contract_version("## Machine Contract (v1)\n\nMachine-Contract-Version: 1\n")
        == 1
    )
    assert parse_machine_contract_version("Machine-Contract-Version:  2\n") == 2
    assert parse_machine_contract_version("# no marker here\n") is None


def test_skill_preflight_fails_fast_when_skill_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 不可解析 → fail fast，报错含 iar init 修复指引。"""
    missing_path = tmp_path / "missing" / "SKILL.md"
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(missing_path))

    with pytest.raises(PrdSkillPreflightError, match="iar init"):
        ensure_prd_machine_contract_available()


def test_skill_preflight_fails_fast_on_version_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约主版本不符（v0 fixture）→ fail fast，报错含版本与修复指引。"""
    stale_skill = _skill_fixture(tmp_path, "# prd\n\nMachine-Contract-Version: 0\n")
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(stale_skill))

    with pytest.raises(PrdSkillPreflightError) as exc_info:
        ensure_prd_machine_contract_available()
    assert "v0" in str(exc_info.value)
    assert "iar init" in str(exc_info.value)


def test_skill_preflight_fails_fast_without_version_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 存在但没有 Machine Contract 标记 → 视为版本不符，fail fast。"""
    unversioned_skill = _skill_fixture(tmp_path, "# prd\n\nno contract section\n")
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(unversioned_skill))

    with pytest.raises(PrdSkillPreflightError, match="iar init"):
        ensure_prd_machine_contract_available()


def test_skill_preflight_passes_with_matching_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 存在且主版本匹配 → 放行并返回 skill 路径。"""
    good_skill = _skill_fixture(
        tmp_path,
        f"# prd\n\nMachine-Contract-Version: {SUPPORTED_MACHINE_CONTRACT_VERSION}\n",
    )
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(good_skill))

    assert ensure_prd_machine_contract_available() == good_skill


def test_skill_preflight_via_run_preflight_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实挂接点：daemon 的 run_preflight_checks 在 skill 缺失时 fail fast。"""
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(tmp_path / "missing" / "SKILL.md"))
    fake_runner = FakeProcessRunner(
        responses={
            ("git", "remote"): CommandResult(
                command=("git", "remote"), return_code=0, stdout="origin\n", stderr=""
            )
        }
    )

    with pytest.raises(PrdSkillPreflightError, match="iar init"):
        run_preflight_checks(tmp_path, AppConfig(), fake_runner)
