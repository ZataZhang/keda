"""隔离 HOME、真实 Git 与安装后的 CLI 验证 Skill 刷新。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cli_observations(tmp_path_factory):
    source = Path.cwd()
    root = tmp_path_factory.mktemp("skill-refresh")
    home, repo, templates = root / "home", root / "repo", root / "templates"
    home.mkdir()
    skills = root / "skills"
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        KEDACODE_NO_UPDATE_CHECK="1",
        KEDACODE_CONFIG=str(root / "global.toml"),
        KEDACODE_SKILLS_DIR=str(skills),
        GIT_ALLOW_PROTOCOL="file",
        GIT_CONFIG_NOSYSTEM="1",
    )
    (root / "global.toml").write_text("[agent_runner]\n", encoding="utf-8")
    for target in (repo, templates):
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(target)], env=env, check=True)
    for name in ("prd", "code-reviewer"):
        target = templates / "skills" / name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Local fixture.\n---\n\nFixture.\n", encoding="utf-8"
        )
    subprocess.run(["git", "-C", str(templates), "add", "skills"], env=env, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(templates),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Local fixture",
        ],
        env=env,
        check=True,
    )
    # 只替远程 Git 传输，实际 clone / fetch / checkout 与安装逻辑仍执行。
    from backend.engines.agent_runner import remote_template_skills

    url = remote_template_skills.REMOTE_TEMPLATE_SKILLS_REPOSITORY_URL
    revision = getattr(remote_template_skills, "REMOTE_TEMPLATE_SKILLS_REF", "main")
    if revision != "main":
        subprocess.run(["git", "-C", str(templates), "branch", revision], env=env, check=True)
    subprocess.run(
        ["git", "config", "--global", f"url.{templates.as_uri()}.insteadOf", url],
        env=env,
        check=True,
    )
    config = repo / ".kedacode.toml"
    config.write_text('[agent_runner.repository]\nid="keep-existing"\n', encoding="utf-8")
    before = config.read_bytes()
    command = Path(sys.executable).with_name("kc.exe" if os.name == "nt" else "kc")
    observations = {}
    for label, arguments in (
        ("init", ["init"]),
        ("schema", ["schema", "--json"]),
        ("install", ["skill", "install"]),
    ):
        process = subprocess.run(
            [str(command), *arguments],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
        )
        observations[label] = process
    packaged = (
        source / "src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md"
    )
    installed = skills / "kedacode-operator/SKILL.md"
    observations["config_preserved"] = before == config.read_bytes()
    observations["skill_exact"] = (
        installed.is_file() and installed.read_bytes() == packaged.read_bytes()
    )
    return observations


def test_init_guidance_reaches_operator(cli_observations):
    process = cli_observations["init"]
    assert process.returncode != 0
    assert "kc skill install" in process.stdout + process.stderr


def test_schema_advertises_skill_install(cli_observations):
    process = cli_observations["schema"]
    assert process.returncode == 0
    assert ("skill", "install") in {
        tuple(entry["path"]) for entry in json.loads(process.stdout)["commands"]
    }


def test_skill_installs_without_changing_repository_config(cli_observations):
    process = cli_observations["install"]
    assert process.returncode == 0, process.stdout + process.stderr
    assert cli_observations["skill_exact"]
    assert cli_observations["config_preserved"]
