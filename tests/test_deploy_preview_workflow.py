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
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
TEMPLATE_ROOT = REPO_ROOT / "src" / "backend" / "engines" / "agent_runner" / "templates" / "preview"

# keda 自身不再跑预览部署，模板副本因此成为这个工作流的唯一副本，守卫直接指向它。
WORKFLOW_PATH = TEMPLATE_ROOT / ".github" / "workflows" / "deploy-preview.yml"

# 空值 key 的代价与文件归属无关，所以仓库自身的工作流和随产品分发的模板工作流
# 都要检查——后者一旦带着这种缺陷发出去，每个下游项目都会复现 startup failure。
ALL_WORKFLOW_PATHS = sorted(WORKFLOWS_DIR.glob("*.yml")) + [WORKFLOW_PATH]

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


def _job_has_checkout_step(job_definition: dict) -> bool:
    """Report whether a job checks the repository out."""
    return any("actions/checkout" in str(step.get("uses", "")) for step in job_definition["steps"])


def test_gh_cli_calls_pass_repo_when_the_job_has_no_checkout(workflow_jobs):
    """没有 checkout 的 job 调用 gh 时必须带 --repo。

    `gh` 默认从 `.git` 推断目标仓库。`preview-context` 刻意不做 checkout（它只需
    解析出 PR 号与 SHA），所以漏掉 `--repo` 时会以
    `fatal: not a git repository` 失败——这条路径让 `workflow_dispatch` 与
    `/deploy` 评论两种触发方式长期不可用。
    """
    for job_name, job_definition in workflow_jobs.items():
        if _job_has_checkout_step(job_definition):
            continue
        for step in job_definition["steps"]:
            run_script = str(step.get("run", ""))
            for gh_invocation in re.findall(r"gh (?:pr|issue|api|run) [^\n)]*", run_script):
                assert "--repo" in gh_invocation, (
                    f"job `{job_name}` 没有 checkout，但其中的 gh 调用未带 --repo："
                    f"{gh_invocation.strip()}"
                )


def test_comment_trigger_is_gated_on_write_access(workflow_jobs):
    """`/deploy` 评论必须限定在本来就有写权限的人。

    issue_comment 带着完整 secrets 运行（fork 的 pull_request 不会），而 deploy job
    会检出 PR head 并在持有 SERVER_SSH_KEY 的环境里执行其中的代码。缺了这道闸门，
    公开仓库里任何人都能借一条评论把 PR 代码提权到可读取部署凭据。
    """
    context_guard = str(workflow_jobs["preview-context"].get("if", ""))

    assert "author_association" in context_guard, (
        "preview-context 没有按 author_association 限制 issue_comment，"
        "任何人都能通过 /deploy 评论让 PR 代码在持有 SERVER_SSH_KEY 的 job 里执行。"
    )

    # CONTRIBUTOR 听起来像自己人，实际只表示历史上有 PR 被合并过，不含写权限；
    # 放进白名单等于这道闸门不存在。
    for untrusted_association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"):
        assert untrusted_association not in context_guard, (
            f"`{untrusted_association}` 不代表写权限，不能出现在 /deploy 的放行名单里："
            f"{context_guard}"
        )


def test_non_deploy_comments_use_a_separate_concurrency_group():
    """无关评论必须与部署分属不同的 concurrency 分组。

    取消发生在**运行级别**：只要分组相同，一条无关评论建立的运行就会取消正在跑
    的部署，哪怕它自己什么都不做。GitHub 表达式无法在本地求值，因此这里只能做
    结构性断言——分组表达式必须按评论内容区分。
    """
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    concurrency_group = workflow["concurrency"]["group"]

    assert "github.event.comment.body" in concurrency_group, (
        "concurrency.group 没有按评论内容区分，任意一条 PR 评论都会取消正在进行的"
        f"预览部署。当前表达式：{concurrency_group}"
    )


@pytest.mark.parametrize(
    "workflow_path",
    ALL_WORKFLOW_PATHS,
    ids=lambda path: str(path.relative_to(REPO_ROOT)),
)
def test_workflow_has_no_null_valued_keys(workflow_path: Path):
    """任何 key 的值为 null 都会让 GitHub 判定 schema 非法并整体拒绝解析。

    空的 `env:` 曾让 deploy-preview 连续两个月处于 startup failure：YAML 解析得动，
    但 Actions schema 要求 mapping，报 `Unexpected value ''`。本地的 check-yaml
    验不出这一类问题，所以在这里显式拦一道。

    这一类缺陷与具体工作流无关——代价又格外高（整个文件拒绝解析，`on:` 读不到，
    失败会挂到不相干的 push 上），因此对 `.github/workflows/` 下每个文件都检查。
    """
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
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
        f"{workflow_path.name} 里以下 key 的值为 null，GitHub 会拒绝解析整个工作流"
        f"并产生 startup failure：{null_valued_paths}"
    )
