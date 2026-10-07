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
    SUPPORTED_MACHINE_CONTRACT_VERSIONS,
    PrdSkillPreflightError,
    parse_machine_contract_version,
)
from backend.core.use_cases.agent_runner_publish import run_preflight_checks
from backend.core.use_cases.generated_prd_content import (
    ensure_prd_machine_contract_available,
)
from tests.conftest import FakeProcessRunner
from backend.core.shared.models.agent_runner import CommandResult


def _skill_fixture(tmp_path: Path, text: str, *, with_contract_script: bool = True) -> Path:
    """造一个 prd skill fixture；默认连兄弟解析脚本一起造。

    预检现在要求 ``SKILL.md`` 与 ``scripts/prd_contract.py`` 成对存在（keda 不自带
    解析实现），因此"合法 skill"的 fixture 必须带上脚本文件；脚本内容不参与这里的
    断言（预检只检查它存在，不执行它）。
    """
    skill_path = tmp_path / "prd" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(text, encoding="utf-8")
    if with_contract_script:
        contract_script_path = skill_path.parent / "scripts" / "prd_contract.py"
        contract_script_path.parent.mkdir(parents=True, exist_ok=True)
        contract_script_path.write_text("# test fixture placeholder\n", encoding="utf-8")
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
    """skill 不可解析 → fail fast，报错含 kc init 修复指引。"""
    missing_path = tmp_path / "missing" / "SKILL.md"
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(missing_path))

    with pytest.raises(PrdSkillPreflightError, match="kc init"):
        ensure_prd_machine_contract_available()


def test_skill_preflight_fails_fast_on_version_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧契约 v1 → fail fast，错误列出受支持版本，不建议盲目覆盖。"""
    stale_skill = _skill_fixture(tmp_path, "# prd\n\nMachine-Contract-Version: 1\n")
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(stale_skill))

    with pytest.raises(PrdSkillPreflightError) as exc_info:
        ensure_prd_machine_contract_available()
    assert "v1" in str(exc_info.value)
    assert "v3" in str(exc_info.value)
    assert "--force" not in str(exc_info.value)


def test_skill_preflight_fails_closed_on_unknown_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未知的未来契约版本也 fail closed。"""
    future_skill = _skill_fixture(tmp_path, "# prd\n\nMachine-Contract-Version: 99\n")
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(future_skill))

    with pytest.raises(PrdSkillPreflightError, match="v99"):
        ensure_prd_machine_contract_available()


def test_skill_preflight_fails_fast_without_version_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 存在但没有 Machine Contract 标记 → 视为版本不符，fail fast。"""
    unversioned_skill = _skill_fixture(tmp_path, "# prd\n\nno contract section\n")
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(unversioned_skill))

    with pytest.raises(PrdSkillPreflightError, match="kc init"):
        ensure_prd_machine_contract_available()


def test_skill_preflight_accepts_every_supported_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """集合里的每个主版本都放行——这是两侧发版能错开的前提。

    契约规定"改这一节必须 bump 版本"，而 skill 与 keda 无法原子发版：skill 先
    bump、keda 后跟进的那段时间里，硬相等 pin 会让所有 iar 起不来。因此受支持
    版本必须是集合语义，且当前集合中的每一版都真的能过预检。
    """
    for version in SUPPORTED_MACHINE_CONTRACT_VERSIONS:
        skill_path = _skill_fixture(
            tmp_path / f"v{version}",
            f"# prd\n\nMachine-Contract-Version: {version}\n",
        )
        monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(skill_path))

        assert ensure_prd_machine_contract_available() == skill_path


def test_skill_preflight_fails_closed_just_above_the_supported_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """集合之外的更高版本仍 fail closed：新契约必须先由 keda 表态支持。"""
    unsupported_version = max(SUPPORTED_MACHINE_CONTRACT_VERSIONS) + 1
    future_skill = _skill_fixture(
        tmp_path, f"# prd\n\nMachine-Contract-Version: {unsupported_version}\n"
    )
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(future_skill))

    with pytest.raises(PrdSkillPreflightError, match=f"v{unsupported_version}"):
        ensure_prd_machine_contract_available()


def test_skill_preflight_fails_fast_when_parser_script_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有 SKILL.md、缺 scripts/prd_contract.py → fail fast。

    keda 不再自带 PRD 解析：解析由 skill 的脚本提供。只装半套（例如手工更新了
    SKILL.md 而脚本没跟上）时，daemon 起得来、却会在第一次解析时报错——那是中途
    失败，不是启动失败。预检把这条接线检查提前到启动时，并点名缺的是哪个文件。
    """
    half_installed_skill = _skill_fixture(
        tmp_path, "# prd\n\nMachine-Contract-Version: 4\n", with_contract_script=False
    )
    monkeypatch.setenv("IAR_PRD_SKILL_PATH", str(half_installed_skill))

    with pytest.raises(PrdSkillPreflightError, match="prd_contract.py"):
        ensure_prd_machine_contract_available()


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

    with pytest.raises(PrdSkillPreflightError, match="kc init"):
        run_preflight_checks(tmp_path, AppConfig(), fake_runner)
