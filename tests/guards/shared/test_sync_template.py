"""守护 template sync 脚本的守卫测试（guard test）。

本文件位于 ``tests/guards/shared/``，失败意味着源代码、配置或脚本违反了仓库约定。
正确做法是修复触发它的源代码或配置，而不是修改本文件让测试通过；仅当约定
本身需要变更时才改本文件，并同步更新相关约定文档。详见
``docs/ai-standards/testing.md`` 的 Guard Tests 小节。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_TEMPLATE_SCRIPT_PATH = REPO_ROOT / "scripts" / "shared" / "template" / "sync_template.sh"


def run_command(
    command_parts: list[str], cwd_path: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with captured UTF-8 output."""

    return subprocess.run(
        command_parts,
        cwd=cwd_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def write_text_file(repo_path: Path, relative_path: str, content: str) -> None:
    """Create or overwrite a UTF-8 text file inside a repository."""

    file_path = repo_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def init_git_repo(repo_path: Path, *, commit_all: bool) -> None:
    """Initialize a git repository and optionally commit current files."""

    init_process = run_command(["git", "init", "-b", "main"], cwd_path=repo_path)
    assert init_process.returncode == 0, init_process.stderr

    config_name_process = run_command(
        ["git", "config", "user.name", "Codex Test"],
        cwd_path=repo_path,
    )
    assert config_name_process.returncode == 0, config_name_process.stderr

    config_email_process = run_command(
        ["git", "config", "user.email", "codex-tests@example.com"],
        cwd_path=repo_path,
    )
    assert config_email_process.returncode == 0, config_email_process.stderr

    if commit_all:
        add_process = run_command(["git", "add", "."], cwd_path=repo_path)
        assert add_process.returncode == 0, add_process.stderr
        commit_process = run_command(
            ["git", "commit", "-m", "Initial commit"],
            cwd_path=repo_path,
        )
        assert commit_process.returncode == 0, commit_process.stderr


def create_repo(
    repo_path: Path, file_contents_by_path: dict[str, str], *, commit_all: bool
) -> None:
    """Create a git repository populated with the provided files."""

    repo_path.mkdir(parents=True, exist_ok=True)
    for relative_path, content in file_contents_by_path.items():
        write_text_file(repo_path, relative_path, content)
    init_git_repo(repo_path, commit_all=commit_all)


def run_sync_template(
    project_repo_path: Path,
    template_repo_path: Path,
    *extra_args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the sync script in list-only mode against a local template repository."""

    environment = os.environ.copy()
    environment["SYNC_TEMPLATE_LIST_ONLY"] = "1"
    environment["SYNC_TEMPLATE_TEMPLATE_REPO"] = str(template_repo_path)
    if extra_env is not None:
        environment.update(extra_env)
    return run_command(
        ["bash", str(SYNC_TEMPLATE_SCRIPT_PATH), *extra_args],
        cwd_path=project_repo_path,
        env=environment,
    )


def test_sync_template_skips_configured_project_paths_by_default(
    tmp_path: Path,
) -> None:
    """Default sync listing should ignore configured project-specific paths."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "README.md": "Template README\n",
        "scripts/shared/tool.sh": "echo template\n",
        "src/backend/api/api.py": "print('template backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'template';\n",
        "infra/main.tf": "# template infra\n",
        "deploy/prod.yml": "name: template deploy\n",
    }
    project_files = {
        "README.md": "Project README\n",
        "scripts/shared/tool.sh": "echo project\n",
        "src/backend/api/api.py": "print('project backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'project';\n",
        "infra/main.tf": "# project infra\n",
        "deploy/prod.yml": "name: project deploy\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "README.md" not in completed_process.stdout
    assert "src/backend/api/api.py" not in completed_process.stdout
    assert "frontend-admin/src/App.tsx" not in completed_process.stdout
    assert "infra/main.tf" not in completed_process.stdout
    assert "deploy/prod.yml" not in completed_process.stdout


def test_sync_template_all_mode_respects_default_project_skip_paths(
    tmp_path: Path,
) -> None:
    """--all mode honours default project_skip_paths (does not bypass them)."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "README.md": "Template README\n",
        "scripts/shared/tool.sh": "echo template\n",
        "src/backend/api/api.py": "print('template backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'template';\n",
        "infra/main.tf": "# template infra\n",
        "deploy/prod.yml": "name: template deploy\n",
    }
    project_files = {
        "README.md": "Project README\n",
        "scripts/shared/tool.sh": "echo project\n",
        "src/backend/api/api.py": "print('project backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'project';\n",
        "infra/main.tf": "# project infra\n",
        "deploy/prod.yml": "name: project deploy\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    # --all bypasses the upstream-owned allowlist, but still respects
    # _is_always_skipped (README.md) and the default project_skip_paths
    # (src/backend/, frontend-admin/, infra/, deploy/).
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "README.md" not in completed_process.stdout
    assert "src/backend/api/api.py" not in completed_process.stdout
    assert "frontend-admin/src/App.tsx" not in completed_process.stdout
    assert "infra/main.tf" not in completed_process.stdout
    assert "deploy/prod.yml" not in completed_process.stdout


def test_sync_template_all_mode_uses_project_skip_paths_from_config(
    tmp_path: Path,
) -> None:
    """--all mode should honour project_skip_paths configured in config.toml."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "scripts/shared/tool.sh": "echo template\n",
        "src/backend/api/api.py": "print('template backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'template';\n",
    }
    project_files = {
        "config.toml": '[template_sync]\nproject_skip_paths = ["src/backend/"]\n',
        "scripts/shared/tool.sh": "echo project\n",
        "src/backend/api/api.py": "print('project backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'project';\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 2 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "CHANGED\tfrontend-admin/src/App.tsx" in completed_process.stdout
    assert "src/backend/api/api.py" not in completed_process.stdout


def test_sync_template_all_mode_include_paths_override_project_skips(
    tmp_path: Path,
) -> None:
    """--all mode: project_include_paths should override project_skip_paths."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "scripts/shared/tool.sh": "echo template\n",
        "src/backend/api/api.py": "print('template backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'template';\n",
    }
    project_files = {
        "config.toml": (
            "[template_sync]\n"
            'project_skip_paths = ["src/backend/", "frontend-admin/"]\n'
            'project_include_paths = ["frontend-admin/"]\n'
        ),
        "scripts/shared/tool.sh": "echo project\n",
        "src/backend/api/api.py": "print('project backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'project';\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    # frontend-admin/ is in both project_skip_paths and project_include_paths.
    # project_include_paths wins, so it should still appear.
    assert "Found 2 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "CHANGED\tfrontend-admin/src/App.tsx" in completed_process.stdout
    assert "src/backend/api/api.py" not in completed_process.stdout


def test_sync_template_all_mode_project_skip_paths_overridden_by_env(
    tmp_path: Path,
) -> None:
    """--all mode: env var should override config.toml project_skip_paths."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "scripts/shared/tool.sh": "echo template\n",
        "src/backend/api/api.py": "print('template backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'template';\n",
    }
    project_files = {
        "config.toml": '[template_sync]\nproject_skip_paths = ["src/backend/"]\n',
        "scripts/shared/tool.sh": "echo project\n",
        "src/backend/api/api.py": "print('project backend')\n",
        "frontend-admin/src/App.tsx": "export const App = 'project';\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
        extra_env={"SYNC_TEMPLATE_PROJECT_SKIP_PATHS": "frontend-admin/"},
    )

    assert completed_process.returncode == 0, completed_process.stderr
    # Env var replaces the config.toml skip list. src/backend/ is no longer
    # skipped, but frontend-admin/ now is.
    assert "Found 2 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "CHANGED\tsrc/backend/api/api.py" in completed_process.stdout
    assert "frontend-admin/src/App.tsx" not in completed_process.stdout


def test_sync_template_skips_scripts_root_by_default(
    tmp_path: Path,
) -> None:
    """Default sync listing should skip scripts/ root but include scripts/shared/."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "scripts/root_tool.sh": "echo template root\n",
        "scripts/shared/shared_tool.sh": "echo template shared\n",
    }
    project_files = {
        "scripts/root_tool.sh": "echo project root\n",
        "scripts/shared/shared_tool.sh": "echo project shared\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/shared_tool.sh" in completed_process.stdout
    assert "root_tool.sh" not in completed_process.stdout


def test_sync_template_all_mode_skips_project_private_scripts(
    tmp_path: Path,
) -> None:
    """--all mode still respects _is_always_skipped for project-private scripts/."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "scripts/root_tool.sh": "echo template root\n",
        "scripts/shared/shared_tool.sh": "echo template shared\n",
    }
    project_files = {
        "scripts/root_tool.sh": "echo project root\n",
        "scripts/shared/shared_tool.sh": "echo project shared\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    # _is_always_skipped filters out scripts/* except scripts/shared/* and
    # scripts/build/*. Project-private scripts/root_tool.sh is never synced.
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/shared_tool.sh" in completed_process.stdout
    assert "scripts/root_tool.sh" not in completed_process.stdout


def test_sync_template_skips_hooks_root_by_default(
    tmp_path: Path,
) -> None:
    """Default sync listing should skip hooks/ root but include hooks/shared/."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "hooks/root_hook.py": "print('template root')\n",
        "hooks/shared/shared_hook.py": "print('template shared')\n",
    }
    project_files = {
        "hooks/root_hook.py": "print('project root')\n",
        "hooks/shared/shared_hook.py": "print('project shared')\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\thooks/shared/shared_hook.py" in completed_process.stdout
    assert "root_hook.py" not in completed_process.stdout


def test_sync_template_all_mode_skips_project_private_hooks(
    tmp_path: Path,
) -> None:
    """--all mode still respects _is_always_skipped for project-private hooks/."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "hooks/root_hook.py": "print('template root')\n",
        "hooks/shared/shared_hook.py": "print('template shared')\n",
    }
    project_files = {
        "hooks/root_hook.py": "print('project root')\n",
        "hooks/shared/shared_hook.py": "print('project shared')\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    # _is_always_skipped filters out hooks/* except hooks/shared/*. Project-private
    # hooks/root_hook.py is never synced.
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\thooks/shared/shared_hook.py" in completed_process.stdout
    assert "hooks/root_hook.py" not in completed_process.stdout


def test_sync_template_default_mode_includes_e2e_infrastructure(
    tmp_path: Path,
) -> None:
    """Default sync listing should surface upstream-owned E2E infrastructure."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "tests/playwright-e2e/fixtures/base.ts": "// template fixture\n",
        "tests/playwright-e2e/support/env.ts": "// template env\n",
        "tests/playwright-e2e/tests/smoke/home.spec.ts": "// template spec\n",
    }
    project_files = {
        "tests/playwright-e2e/fixtures/base.ts": "// project fixture\n",
        "tests/playwright-e2e/support/env.ts": "// project env\n",
        "tests/playwright-e2e/tests/smoke/home.spec.ts": "// project spec\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\ttests/playwright-e2e/fixtures/base.ts" in completed_process.stdout
    assert "tests/playwright-e2e/support/env.ts" not in completed_process.stdout
    assert "tests/playwright-e2e/tests/smoke/home.spec.ts" not in completed_process.stdout


def test_sync_template_all_mode_includes_e2e_infrastructure_but_skips_specs(
    tmp_path: Path,
) -> None:
    """--all mode surfaces E2E infrastructure while skipping project specs."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "tests/playwright-e2e/fixtures/base.ts": "// template fixture\n",
        "tests/playwright-e2e/support/env.ts": "// template env\n",
        "tests/playwright-e2e/tests/smoke/home.spec.ts": "// template spec\n",
        "tests/backend/test_auth.py": "# template backend test\n",
    }
    project_files = {
        "config.toml": (
            "[template_sync]\n" 'project_skip_paths = ["tests/"]\n' "project_include_paths = []\n"
        ),
        "tests/playwright-e2e/fixtures/base.ts": "// project fixture\n",
        "tests/playwright-e2e/support/env.ts": "// project env\n",
        "tests/playwright-e2e/tests/smoke/home.spec.ts": "// project spec\n",
        "tests/backend/test_auth.py": "# project backend test\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\ttests/playwright-e2e/fixtures/base.ts" in completed_process.stdout
    assert "tests/playwright-e2e/support/env.ts" not in completed_process.stdout
    assert "tests/playwright-e2e/tests/smoke/home.spec.ts" not in completed_process.stdout
    assert "tests/backend/test_auth.py" not in completed_process.stdout


def test_sync_template_default_mode_includes_guard_shared_but_not_project_guards(
    tmp_path: Path,
) -> None:
    """Default sync listing surfaces tests/guards/shared/ but not root guard tests.

    tests/guards/shared/ guards upstream-owned code (hooks/shared,
    scripts/shared, scripts/build) and must follow the same distribution
    lifecycle; root-level guard tests guard project-owned objects and stay
    project-local.
    """

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "tests/guards/shared/test_check_max_file_lines.py": "# template shared guard\n",
        "tests/guards/test_openapi_schema.py": "# template root guard\n",
    }
    project_files = {
        "tests/guards/shared/test_check_max_file_lines.py": "# project shared guard\n",
        "tests/guards/test_openapi_schema.py": "# project root guard\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\ttests/guards/shared/test_check_max_file_lines.py" in completed_process.stdout
    assert "tests/guards/test_openapi_schema.py" not in completed_process.stdout


def test_sync_template_all_mode_includes_guard_shared_but_skips_root_guards(
    tmp_path: Path,
) -> None:
    """--all mode surfaces tests/guards/shared/ despite the tests/ project skip."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "tests/guards/shared/test_check_max_file_lines.py": "# template shared guard\n",
        "tests/guards/test_openapi_schema.py": "# template root guard\n",
    }
    project_files = {
        "config.toml": (
            "[template_sync]\n" 'project_skip_paths = ["tests/"]\n' "project_include_paths = []\n"
        ),
        "tests/guards/shared/test_check_max_file_lines.py": "# project shared guard\n",
        "tests/guards/test_openapi_schema.py": "# project root guard\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\ttests/guards/shared/test_check_max_file_lines.py" in completed_process.stdout
    assert "tests/guards/test_openapi_schema.py" not in completed_process.stdout


def test_sync_template_always_skips_template_internal_guard(
    tmp_path: Path,
) -> None:
    """Guards exercising template-only artifacts (skills/) are never synced."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "tests/guards/test_prd_skill_checker.py": "# template-internal guard\n",
    }
    project_files = {
        "tests/guards/test_prd_skill_checker.py": "# project-internal guard\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Everything is up to date with the template." in completed_process.stdout
    assert "tests/guards/test_prd_skill_checker.py" not in completed_process.stdout


def test_sync_template_skips_e2e_runtime_artifacts(
    tmp_path: Path,
) -> None:
    """E2E runtime artifacts must never appear in sync output."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "tests/playwright-e2e/.auth/session.json": "{}\n",
        "tests/playwright-e2e/test-results/last-run.json": "{}\n",
        "tests/playwright-e2e/playwright-report/index.html": "<html></html>\n",
        "tests/playwright-e2e/node_modules/foo/index.js": "// foo\n",
        "tests/playwright-e2e/.env.e2e.local": "SECRET=1\n",
        "tests/playwright-e2e/fixtures/base.ts": "// template fixture\n",
        "tests/playwright-e2e/support/env.ts": "// template env\n",
    }
    project_files = {
        "tests/playwright-e2e/.auth/session.json": "{}\n",
        "tests/playwright-e2e/test-results/last-run.json": "{}\n",
        "tests/playwright-e2e/playwright-report/index.html": "<html></html>\n",
        "tests/playwright-e2e/node_modules/foo/index.js": "// foo\n",
        "tests/playwright-e2e/.env.e2e.local": "SECRET=2\n",
        "tests/playwright-e2e/fixtures/base.ts": "// project fixture\n",
        "tests/playwright-e2e/support/env.ts": "// project env\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\ttests/playwright-e2e/fixtures/base.ts" in completed_process.stdout
    assert "tests/playwright-e2e/support/env.ts" not in completed_process.stdout
    assert ".auth/session.json" not in completed_process.stdout
    assert "test-results/last-run.json" not in completed_process.stdout
    assert "playwright-report/index.html" not in completed_process.stdout
    assert "node_modules/foo/index.js" not in completed_process.stdout
    assert ".env.e2e.local" not in completed_process.stdout


def test_sync_template_default_mode_skips_project_owned_configs_and_agents_md(
    tmp_path: Path,
) -> None:
    """Default mode skips project-owned tool configs and AGENTS.md."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "AGENTS.md": "# template agents\n",
        "pytest.ini": "[pytest]\naddopts = -v\n",
        "ruff.toml": "line-length = 88\n",
    }
    project_files = {
        "AGENTS.md": "# project agents\n",
        "pytest.ini": "[pytest]\naddopts = -q\n",
        "ruff.toml": "line-length = 100\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    assert "Everything is up to date with the template." in completed_process.stdout
    assert "pytest.ini" not in completed_process.stdout
    assert "ruff.toml" not in completed_process.stdout
    assert "AGENTS.md" not in completed_process.stdout


def test_sync_template_default_mode_skips_ai_adapter_files(tmp_path: Path) -> None:
    """Default sync listing skips AI adapter files (AI standards + AI entry files)."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "docs/ai-standards/index.md": "# template index\n",
        ".github/copilot-instructions.md": "# template copilot\n",
        ".github/instructions/python-tests.instructions.md": "# template instructions\n",
        ".cursor/commands/cursor.md": "# template cursor\n",
        "scripts/shared/tool.sh": "echo template\n",
    }
    project_files = {
        "docs/ai-standards/index.md": "# project index\n",
        ".github/copilot-instructions.md": "# project copilot\n",
        ".github/instructions/python-tests.instructions.md": "# project instructions\n",
        ".cursor/commands/cursor.md": "# project cursor\n",
        "scripts/shared/tool.sh": "echo project\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(project_repo_path, template_repo_path)

    assert completed_process.returncode == 0, completed_process.stderr
    # Only scripts/shared/tool.sh is upstream-owned and not an AI adapter file.
    assert "Found 1 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "docs/ai-standards/index.md" not in completed_process.stdout
    assert ".github/copilot-instructions.md" not in completed_process.stdout
    assert ".github/instructions/python-tests.instructions.md" not in completed_process.stdout
    assert ".cursor/commands/cursor.md" not in completed_process.stdout


def test_sync_template_all_mode_includes_ai_adapter_files(tmp_path: Path) -> None:
    """--all mode surfaces AI adapter files even when docs/ is in skip_paths."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "docs/ai-standards/index.md": "# template index\n",
        ".github/copilot-instructions.md": "# template copilot\n",
        ".cursor/commands/cursor.md": "# template cursor\n",
        "scripts/shared/tool.sh": "echo template\n",
    }
    project_files = {
        # docs/ is in default project_skip_paths, but docs/ai-standards/ is
        # upstream-owned so --all still surfaces it.
        "docs/ai-standards/index.md": "# project index\n",
        ".github/copilot-instructions.md": "# project copilot\n",
        ".cursor/commands/cursor.md": "# project cursor\n",
        "scripts/shared/tool.sh": "echo project\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    completed_process = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )

    assert completed_process.returncode == 0, completed_process.stderr
    # All four entries appear in --all mode.
    assert "Found 4 changed + 0 new entry/entries." in completed_process.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in completed_process.stdout
    assert "CHANGED\tdocs/ai-standards/index.md" in completed_process.stdout
    assert "CHANGED\t.github/copilot-instructions.md" in completed_process.stdout
    assert "CHANGED\t.cursor/commands/cursor.md" in completed_process.stdout


def test_sync_template_claude_md_appears_only_in_all_mode(tmp_path: Path) -> None:
    """CLAUDE.md is skipped in default mode but surfaced in --all mode."""

    template_repo_path = tmp_path / "template-repo"
    project_repo_path = tmp_path / "project-repo"

    template_files = {
        "CLAUDE.md": "# template claude\n",
        "AGENTS.md": "# template agents\n",
        "scripts/shared/tool.sh": "echo template\n",
    }
    project_files = {
        "CLAUDE.md": "# project claude\n",
        "AGENTS.md": "# project agents\n",
        "scripts/shared/tool.sh": "echo project\n",
    }

    create_repo(template_repo_path, template_files, commit_all=True)
    create_repo(project_repo_path, project_files, commit_all=False)

    # Default mode: only scripts/shared/tool.sh (CLAUDE.md and AGENTS.md are
    # not upstream-owned).
    default_result = run_sync_template(project_repo_path, template_repo_path)
    assert default_result.returncode == 0, default_result.stderr
    assert "Found 1 changed + 0 new entry/entries." in default_result.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in default_result.stdout
    assert "CLAUDE.md" not in default_result.stdout
    assert "AGENTS.md" not in default_result.stdout

    # --all mode: CLAUDE.md (no longer always-skipped) and AGENTS.md appear.
    all_result = run_sync_template(
        project_repo_path,
        template_repo_path,
        "--all",
    )
    assert all_result.returncode == 0, all_result.stderr
    assert "Found 3 changed + 0 new entry/entries." in all_result.stdout
    assert "CHANGED\tscripts/shared/tool.sh" in all_result.stdout
    assert "CHANGED\tCLAUDE.md" in all_result.stdout
    assert "CHANGED\tAGENTS.md" in all_result.stdout
