"""内置巡检配方的生成与显式注册，复用现有 loop 调度和持久化。"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.core.shared.interfaces.loop_scheduler import ILoopStateStore
from backend.core.shared.models.agent_runner import RepositoryRunContext
from backend.core.shared.models.loop import LoopSchedule, LoopTask
from backend.core.use_cases.agent_runner_repository_local import load_agent_runner_local_settings
from backend.core.use_cases.issue_prd_github_link import resolve_github_slug
from backend.core.use_cases.loop_create import LoopAlreadyExistsError, create_loop_from_recipe
from backend.core.use_cases.loop_recipe import parse_loop_recipe_text
from backend.core.use_cases.loop_scheduler import compute_next_fire


@dataclass(frozen=True)
class BuiltinLoopRequest:
    """显式启用内置 loop 时的注册选项。"""

    loop_id: str
    template_name: str
    schedule: LoopSchedule | None = None
    overwrite: bool = False


def render_blocker_monitor_recipe(context: RepositoryRunContext, *, loop_id: str) -> str:
    """按目标仓库和生效标签生成每 30 分钟的恢复巡检配方。

    Args:
        context: 已解析的目标仓库及其生效配置。
        loop_id: 用户选择的全局唯一 loop 标识。

    Returns:
        可由现有配方解析器读取的 Markdown。

    Raises:
        ValueError: 无法确定目标 GitHub 仓库。
    """
    identity = context.config.repositories.get(context.repo_id)
    local_settings = load_agent_runner_local_settings(context.repo_path)
    github_repo = (
        (identity.github_repo if identity else None)
        or (local_settings.github_repo if local_settings else None)
        or resolve_github_slug(context.repo_path)
    )
    if not github_repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", github_repo):
        raise ValueError(
            "Cannot resolve GitHub owner/repo; configure repository github_repo first."
        )
    labels = context.config.labels
    # 把 stderr 纳入失败诊断，归一化为单行；失败仍造票，但不把 unknown 当作空队列。
    pre_command = (
        f"target={shlex.quote(github_repo)}; "
        "blocked=unknown; failed=unknown; state_read_error=none; "
        f"for pair in {shlex.quote('blocked:' + labels.blocked)} "
        f"{shlex.quote('failed:' + labels.failed)}; do "
        'state=${pair%%:*}; label=${pair#*:}; '
        'if count=$(gh issue list --repo "$target" --state open --label "$label" '
        '--limit 1000 --json number --jq length 2>&1); then '
        'case "$count" in ""|*[!0-9]*) '
        'state_read_error="invalid count for $state"; count=unknown;; esac; '
        'else state_read_error="$state: $count"; count=unknown; fi; '
        'if [ "$state" = blocked ]; then blocked=$count; else failed=$count; fi; done; '
        'case "$blocked:$failed" in *unknown*) needs_attention=unknown;; '
        '*) needs_attention=$((blocked + failed));; esac; '
        "printf 'blocked_count=%s\\nfailed_count=%s\\nneeds_attention=%s\\n' "
        '"$blocked" "$failed" "$needs_attention"; '
        "printf 'state_read_error='; "
        'printf "%s" "$state_read_error" | tr "\\r\\n" "  " | cut -c 1-500'
    )
    metadata = {
        "id": loop_id,
        "schedule": "30m",
        "repo_id": context.repo_id,
        "managed_by": "agent-daemon",
        "issue_type": "bug",
        "agent": "auto",
        "queue_ready": "true",
        "output": "issue",
        "skip_when": "needs_attention=0",
        "pre_command": pre_command,
    }
    frontmatter = "\n".join(
        f"{key}: '{value.replace(chr(39), chr(39) * 2)}'"
        if key != "queue_ready"
        else f"{key}: {value}"
        for key, value in metadata.items()
    )
    repo_selector = shlex.quote(context.repo_id)
    body = f"""# 任务恢复巡检 — {{{{date}}}}

目标：`{github_repo}`（repo_id: `{context.repo_id}`）。
触发时 `{labels.blocked}` 数量：{{{{blocked_count}}}}；
`{labels.failed}` 数量：{{{{failed_count}}}}；合计：{{{{needs_attention}}}}。
每类计数上限为 1000，达到上限时仍须分页读取全部目标。
查询诊断：{{{{state_read_error}}}}。

## 授权与处理范围

维护者通过显式注册本模板，授权诊断失败任务、修复范围明确且可验证的技术根因，
并在验证成功后恢复原任务。代码修改走仓库现有规范与 Draft PR 流程。
产品取舍、权限、密钥、受保护路径和外部审批需要人工决定，不能猜测或绕过。

1. fresh read 当前 open 的 `{labels.blocked}` / `{labels.failed}` Issues，排除
   带 `loop/{loop_id}` 的本巡检 Issue；读取正文、Attempt History、相关 PR 和
   `kc logs --repo-id {repo_selector} --issue <N>`。不接管仍在运行的任务。
2. 计数为 unknown 时，先复现并诊断 GitHub 访问故障，保留错误证据，不能据此重试任务。
3. 对失败任务分类：发布收尾使用 `kc recover --repo-id {repo_selector} --issue <N>`；
   执行失败须先修复根因并验证，再按恢复流程重新排队。禁止无依据地反复重试。
   恢复命令须在原任务对应 worktree 中执行，先核对 Issue 和分支，不能冒用本巡检的分支。
4. 技术阻塞修复并验证后，用 `kc blocked-continue --repo-id {repo_selector} --issue <N>`
   恢复原任务并读回状态。需要人工决定时，在原 Issue 留下证据和最小待决项，保留 blocked；
   本巡检 Issue 也进入 blocked，等待维护者处理，不重复造票。
5. 将处置摘要写进本巡检 Issue 正文：目标 Issue/PR、根因、修复、验证结果、最新状态及
   待决事项。没有剩余工作时关闭本巡检 Issue；有修复 PR 时保留至交付。
   PR 使用 `Closes #<本巡检 Issue>`，未完成的目标仅用 `Addresses` / `Related to` 关联。

空队列由 pre_command 跳过，不创建 Issue、不调用 Agent。后台 `kc daemon` 停止时不调度；
上一轮巡检 Issue 未关闭时暂停后续轮次。停用使用 `kc loop cancel {loop_id}`。
"""
    return f"---\n{frontmatter}\n---\n\n{body}"


def create_builtin_loop(
    request: BuiltinLoopRequest,
    *,
    context: RepositoryRunContext,
    state_store: ILoopStateStore,
) -> LoopTask:
    """生成仓库内置配方并注册；不覆盖已有配方文件，不启动 daemon。

    Args:
        request: 模板、标识和调度选项。
        context: 已注册且启用的仓库上下文。
        state_store: 调度状态存储。

    Returns:
        注册成功的 loop。

    Raises:
        ValueError: 模板、标识或调度不合法。
        FileExistsError: 配方已存在，应改用 ``--recipe`` 注册。
        LoopAlreadyExistsError: 同名任务已注册且未允许替换。
        OSError: 无法写入配方或调度状态。
    """
    if request.template_name != "blocker-monitor":
        raise ValueError(f"Unknown loop template: {request.template_name!r}; use blocker-monitor.")
    recipe_text = render_blocker_monitor_recipe(context, loop_id=request.loop_id)
    recipe_path = context.repo_path / "tasks" / "loops" / f"{request.loop_id}.md"
    recipe = parse_loop_recipe_text(
        recipe_text, source_path=recipe_path, agents=context.config.agents
    )
    compute_next_fire(request.schedule or recipe.schedule, after=datetime.now(timezone.utc))
    state_store.load()
    if state_store.get_task(request.loop_id) is not None and not request.overwrite:
        raise LoopAlreadyExistsError(f"Loop '{request.loop_id}' is already registered.")
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    with recipe_path.open("x", encoding="utf-8") as recipe_file:
        recipe_file.write(recipe_text)
    try:
        return create_loop_from_recipe(
            recipe_path,
            state_store=state_store,
            schedule=request.schedule,
            overwrite=request.overwrite,
            agents=context.config.agents,
        )
    except Exception:
        recipe_path.unlink()
        raise
