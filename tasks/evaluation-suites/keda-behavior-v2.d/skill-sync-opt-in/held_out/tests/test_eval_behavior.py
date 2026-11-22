"""启动同步通过真实入口调用，安装副作用由计数替身观察。"""

import pytest

from backend.api import cli_startup_skill_sync as startup


@pytest.mark.parametrize(
    "opt_in, disabled, expected", [(None, None, 0), ("0", None, 0), ("1", None, 1), ("1", "1", 0)]
)
def test_startup_skill_sync_requires_opt_in(tmp_path, monkeypatch, opt_in, disabled, expected):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in ("KEDACODE_AUTO_SKILL_SYNC", "KEDACODE_NO_SKILL_SYNC", "KEDACODE_NO_UPDATE_CHECK"):
        monkeypatch.delenv(name, raising=False)
    if opt_in is not None:
        monkeypatch.setenv("KEDACODE_AUTO_SKILL_SYNC", opt_in)
    if disabled is not None:
        monkeypatch.setenv("KEDACODE_NO_SKILL_SYNC", disabled)
    monkeypatch.setattr(startup, "_should_skip_startup_update_check", lambda *args, **kwargs: False)
    monkeypatch.setattr(startup, "resolve_keda_version", lambda: "9.8.7")
    calls = []
    monkeypatch.setattr(
        startup, "sync_packaged_operator_skills", lambda **kwargs: calls.append(kwargs) or ()
    )
    startup.startup_skill_sync(["logs"])
    assert len(calls) == expected
