"""内置 loop 模板的 CLI 参数适配。"""

from __future__ import annotations

import argparse
import shlex

from backend.api.cli_exit_codes import ExitCode
from backend.api.cli_output import CliError
from backend.api.cli_utils import format_local_display_time
from backend.core.shared.interfaces.loop_scheduler import ILoopStateStore
from backend.core.shared.models.loop import LoopSchedule
from backend.core.use_cases.agent_runner_factory import (
    get_agent_runner_settings,
    resolve_repository_targets,
)
from backend.core.use_cases.loop_builtin import BuiltinLoopRequest, create_builtin_loop
from backend.core.use_cases.loop_create import LoopAlreadyExistsError


def run_builtin_loop_create(
    parsed: argparse.Namespace, *, state_store: ILoopStateStore, schedule: LoopSchedule | None
) -> int:
    """显式选择已注册仓库后，生成并注册内置模板。

    Args:
        parsed: 已解析的 loop create 参数。
        state_store: 现有调度存储。
        schedule: 可选调度覆盖。

    Returns:
        成功退出码。

    Raises:
        CliError: 仓库选择、模板或配方冲突。
    """
    repo_id = getattr(parsed, "loop_repo_id", None)
    if not repo_id or getattr(parsed, "loop_repo", None):
        raise CliError(
            "--template requires an enabled registered --repo-id; --repo is not supported.",
            code=ExitCode.USAGE,
            suggestion="kc loop create my-repo-blockers --template blocker-monitor --repo-id <id>",
        )
    try:
        contexts = resolve_repository_targets(get_agent_runner_settings(), repo_id=repo_id)
        task = create_builtin_loop(
            BuiltinLoopRequest(
                loop_id=parsed.loop_id,
                template_name=parsed.template,
                schedule=schedule,
                overwrite=bool(getattr(parsed, "force", False)),
            ),
            context=contexts[0],
            state_store=state_store,
        )
    except (FileExistsError, LoopAlreadyExistsError) as exc:
        raise CliError(
            f"{exc} Existing recipes are preserved; register them with --recipe.",
            code=ExitCode.CONFLICT,
            suggestion=f"kc loop create {parsed.loop_id} --recipe "
            f"tasks/loops/{parsed.loop_id}.md --force",
        ) from exc
    except ValueError as exc:
        raise CliError(str(exc), code=ExitCode.USAGE) from exc
    print(f"Wrote loop recipe: {task.recipe_path}")
    print(
        f"Registered loop '{task.id}' (repo_id={task.repo_id}, "
        f"schedule={task.schedule.expression}, output={task.output}, "
        f"next_fire={format_local_display_time(task.next_fire_at) or '—'})."
    )
    print(
        "Enabled technical-blocker diagnosis and verified recovery; human decisions stay blocked. "
        f"Requires kc daemon --repo-id {shlex.quote(repo_id)}; "
        f"disable with kc loop cancel {task.id}."
    )
    return 0
