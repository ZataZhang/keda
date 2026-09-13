"""Structural guards for the preview deployment workflow.

这些用例针对的是一类**静默失效**：workflow 语法完全正确、job 也报绿，但某个
step 因为读不到 secret 而被 `if:` 跳过，实际什么都没做。

具体踩过的坑：`teardown` job 没有声明 `environment: preview`，而
`SERVER_HOST` / `SERVER_USER` / `SERVER_SSH_KEY` 只存在于 preview 环境里。
未声明 environment 时 `secrets.SERVER_*` 解析为空字符串，于是
`if: env.SERVER_HOST != ''` 恒为假、拆栈步骤被跳过；跳过不算失败，job 依旧
success——预览栈因此从未被拆除，每个关闭的 PR 都在服务器上留下一套。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-preview.yml"
TEMPLATE_WORKFLOW_PATH = (
    REPO_ROOT
    / "src"
    / "backend"
    / "engines"
    / "agent_runner"
    / "templates"
    / "preview"
    / ".github"
    / "workflows"
    / "deploy-preview.yml"
)

# GITHUB_TOKEN 由 Actions 自动注入，任何 job 都能读到，无需声明 environment。
# 其余 secret 在本仓库都配置在 preview 环境下，必须声明 environment 才可见。
AMBIENT_SECRET_NAMES = frozenset({"GITHUB_TOKEN"})

SECRET_REFERENCE_PATTERN = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")


@pytest.fixture(scope="module")
def workflow_jobs() -> dict[str, dict]:
    """Parse the deploy-preview workflow and return its jobs mapping."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]


def _referenced_secret_names(job_definition: dict) -> set[str]:
    """Collect every `secrets.X` name referenced anywhere inside a job."""
    return set(SECRET_REFERENCE_PATTERN.findall(yaml.safe_dump(job_definition)))


def test_jobs_using_environment_secrets_declare_an_environment(workflow_jobs):
    """引用了非自动注入 secret 的 job 必须声明 environment，否则会静默读到空值。"""
    for job_name, job_definition in workflow_jobs.items():
        environment_scoped_secrets = _referenced_secret_names(job_definition) - AMBIENT_SECRET_NAMES
        if not environment_scoped_secrets:
            continue
        assert job_definition.get("environment"), (
            f"job `{job_name}` 引用了环境级 secret "
            f"{sorted(environment_scoped_secrets)}，但没有声明 `environment:`。"
            f"这些 secret 会解析为空字符串，依赖它们的 `if:` 条件恒为假，"
            f"相关步骤被静默跳过而 job 仍然报 success。"
        )


def test_teardown_job_can_reach_the_preview_server(workflow_jobs):
    """teardown 必须真的能拆栈：它的守卫条件所依赖的 secret 必须可见。"""
    teardown_job = workflow_jobs["teardown"]
    guarded_steps = [
        step
        for step in teardown_job["steps"]
        if "SERVER_HOST" in str(step.get("if", "")) or "SERVER_SSH_KEY" in str(step.get("if", ""))
    ]

    assert guarded_steps, "teardown 里找不到依赖 SERVER_* 的步骤，测试前提已失效"
    assert teardown_job.get("environment") == "preview", (
        "teardown 的拆栈步骤以 `env.SERVER_*` 非空为前提，因此该 job 必须声明 "
        "`environment: preview`，否则拆栈会被跳过、预览栈永久残留在服务器上。"
    )


def test_bundled_template_workflow_matches_source():
    """模板副本必须与源文件逐字节一致，否则下游项目会继承旧缺陷。"""
    assert TEMPLATE_WORKFLOW_PATH.read_text(encoding="utf-8") == WORKFLOW_PATH.read_text(
        encoding="utf-8"
    ), f"{TEMPLATE_WORKFLOW_PATH} 与 {WORKFLOW_PATH} 不一致，请同步模板副本"


def test_workflow_has_no_null_valued_keys():
    """任何 key 的值为 null 都会让 GitHub 判定 schema 非法并整体拒绝解析。

    空的 `env:` 曾让这个工作流连续两个月处于 startup failure：YAML 解析得动，
    但 Actions schema 要求 mapping，报 `Unexpected value ''`。本地的 check-yaml
    验不出这一类问题，所以在这里显式拦一道。
    """
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    null_valued_paths: list[str] = []

    def _walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child_path = f"{path}.{key}"
                # 触发器可以只写名字不带子项（如 `workflow_dispatch:`），这是合法的。
                if value is None and path != ".on":
                    null_valued_paths.append(child_path)
                _walk(value, child_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                _walk(value, f"{path}[{index}]")

    # PyYAML 会把未加引号的 `on:` 解析成布尔 True，这里统一改回字符串再遍历。
    normalized_workflow = {("on" if key is True else key): value for key, value in workflow.items()}
    _walk(normalized_workflow, "")

    assert not null_valued_paths, (
        f"以下 key 的值为 null，GitHub 会拒绝解析整个工作流并产生 startup failure："
        f"{null_valued_paths}"
    )
