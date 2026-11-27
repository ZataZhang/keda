"""生成隔离恢复场景使用的证书、Agent 和仓库配置。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _make_certificate(scene_root: Path) -> Path:
    """为临时 HTTPS GitHub 服务生成本机可信证书。"""
    certificate_path = scene_root / "forge-cert.pem"
    private_key_path = scene_root / "forge-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=github.com",
            "-addext",
            "subjectAltName=DNS:github.com,DNS:api.github.com,IP:127.0.0.1",
            "-keyout",
            str(private_key_path),
            "-out",
            str(certificate_path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return certificate_path


def _write_agent_fixture(scene_root: Path, agent_log_path: Path) -> Path:
    """生成按 JSON 计划工作的外部 executor shim 与调用探针。"""
    agent_script_path = scene_root / "rv_agent.py"
    agent_script_path.write_text(
        """from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

scene_root = Path(os.environ["RV_SCENE_ROOT"])
invocation_log = scene_root / "agent-invocations.jsonl"
prompt_text = sys.stdin.read()
invocation = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "prompt_head": prompt_text[:240],
    "at_utc": datetime.now(timezone.utc).isoformat(),
    "executor": "codex",
    "role": "run",
}
with invocation_log.open("a", encoding="utf-8") as output_file:
    output_file.write(json.dumps(invocation, ensure_ascii=False) + "\\n")
worktree_path = Path.cwd()
readme_path = worktree_path / "README.md"
current_readme = readme_path.read_text(encoding="utf-8")
if "recovery fixture completed" not in current_readme:
    readme_path.write_text(current_readme + "\\nrecovery fixture completed\\n", encoding="utf-8")
commit_request_path = worktree_path / ".agent-runner" / "commit-request.json"
commit_request_path.parent.mkdir(parents=True, exist_ok=True)
commit_request_path.write_text(
    json.dumps({"commit_message": "feat: exercise recovery fixture"}),
    encoding="utf-8",
)
print("RV deterministic executor completed the implementation step.")
""",
        encoding="utf-8",
    )
    return agent_script_path


def _write_verification_fixture(scene_root: Path, verification_log_path: Path) -> Path:
    """生成通过时记录真实验证子进程调用的短命令。"""
    verification_script_path = scene_root / "rv_verify.py"
    verification_script_path.write_text(
        """from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

scene_root = Path(os.environ["RV_SCENE_ROOT"])
verification_log = scene_root / "verification-invocations.jsonl"
entry = {"cwd": os.getcwd(), "at_utc": datetime.now(timezone.utc).isoformat()}
with verification_log.open("a", encoding="utf-8") as output_file:
    output_file.write(json.dumps(entry, sort_keys=True) + "\\n")
print("RV verification passed.")
""",
        encoding="utf-8",
    )
    return verification_script_path


def _write_repository_config(
    repository_path: Path,
    state_home: Path,
    agent_script_path: Path,
    verification_script_path: Path,
    owner_repository: str,
) -> tuple[Path, Path]:
    """创建隔离的机器配置和仓库 runner 配置。"""
    state_home.mkdir(parents=True, exist_ok=True)
    config_path = state_home / "config.toml"
    database_path = state_home / "console.db"
    config_path.write_text(
        "[agent_runner.console]\n"
        f"history_db_path = {json.dumps(str(database_path))}\n"
        "\n[agent_runner]\n"
        "max_issues = 1\n"
        "\n[agent_runner.generated_content]\n"
        "enabled = false\n",
        encoding="utf-8",
    )
    repository_config_path = repository_path / ".kedacode.toml"
    repository_config_path.write_text(
        "[agent_runner.repository]\n"
        "id = \"rv-issue-457\"\n"
        f"github_repo = {json.dumps(owner_repository)}\n"
        "\n[agent_runner.runner]\n"
        "default_agent = \"codex\"\n"
        "fix_agent_enabled = false\n"
        "closeout_agent_enabled = false\n"
        f"verification_commands = [{json.dumps(f'{sys.executable} {verification_script_path}')} ]\n"
        "\n[agent_runner.worktree]\n"
        "provision_database = false\n"
        "\n[agent_runner.memory]\n"
        "enabled = false\n"
        "\n[agent_runner.pre_pr_review]\n"
        "enabled = false\n"
        "\n[agent_runner.post_pr_supervisor]\n"
        "enabled = false\n"
        "\n[agent_runner.autopilot]\n"
        "enabled = false\n"
        "\n[agent_runner.generated_content]\n"
        "enabled = false\n"
        "\n[agent_runner.generated_content.draft_pr]\n"
        "enabled = false\n"
        "\n[agent_runner.daemon]\n"
        "reconcile_stale_attempts = true\n"
        "\n[agent_runner.agents.codex]\n"
        f"bin = {json.dumps(sys.executable)}\n"
        "label = \"agent/codex\"\n"
        "\n[agent_runner.agents.codex.profiles.run]\n"
        f"args = [{json.dumps(str(agent_script_path))}]\n"
        "prompt_delivery = \"stdin\"\n"
        "output_protocol = \"plain\"\n"
        "tail_args = []\n"
        "read_only = false\n",
        encoding="utf-8",
    )
    return config_path, database_path

