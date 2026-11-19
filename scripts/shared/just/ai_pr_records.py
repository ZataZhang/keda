"""保存 PR 交付上下文与逐轮事实，不把合并状态当成任务成功标签。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

BEGIN_MARKER = "<!-- just-ai-pr:begin -->"
END_MARKER = "<!-- just-ai-pr:end -->"


@dataclass(frozen=True)
class DeliveryContext:
    """当前对话 Agent 提供的交付输入；不从 diff 反推用户原话。"""

    payload: dict[str, Any]

    @classmethod
    def read(cls, context_path: Path) -> DeliveryContext:
        """读取并校验发布所需的最小上下文。

        Args:
            context_path: 当前 Agent 准备的 UTF-8 JSON 文件。

        Returns:
            已校验的上下文。

        Raises:
            ValueError: 字段缺失、文件路径越界或状态非法。
        """
        payload = json.loads(context_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("Expected a context object with schema_version=1.")
        for field_name in ("title", "task", "summary", "commit_message"):
            if not isinstance(payload.get(field_name), str) or not payload[field_name].strip():
                raise ValueError(f"Missing non-empty string: {field_name}")
        if "\n" in payload["title"] or payload["title"].startswith("-"):
            raise ValueError("title must be one line and must not start with '-'.")
        if payload.get("request_source") not in {"conversation", "retrospective"}:
            raise ValueError("request_source must be conversation or retrospective.")
        messages = payload.get("user_messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("user_messages must contain relevant verbatim user messages.")
        if any(not isinstance(message, str) or not message.strip() for message in messages):
            raise ValueError("Each user message must be a non-empty string.")
        validation = payload.get("validation")
        if not isinstance(validation, dict) or validation.get("status") not in {
            "passed",
            "pending",
            "failed",
        }:
            raise ValueError("validation.status must be passed, pending or failed.")
        if not isinstance(validation.get("details"), str) or not validation["details"].strip():
            raise ValueError("validation.details must explain evidence and limitations.")
        human_validation = payload.get("human_validation")
        if "human_validation" in payload and (
            not isinstance(human_validation, dict)
            or human_validation.get("status")
            not in {"not_requested", "pending", "passed", "failed"}
            or not isinstance(human_validation.get("details"), str)
            or not human_validation["details"].strip()
        ):
            raise ValueError(
                "human_validation requires an explicit status and acceptance evidence."
            )
        if "iteration_summary" in payload and (
            not isinstance(payload["iteration_summary"], str)
            or not payload["iteration_summary"].strip()
        ):
            raise ValueError("iteration_summary must be a non-empty string when provided.")
        file_paths = payload.get("files")
        if not isinstance(file_paths, list) or any(
            not isinstance(file_path, str)
            or not file_path
            or PurePosixPath(file_path).is_absolute()
            or any(part in {"..", ".git"} for part in PurePosixPath(file_path).parts)
            or file_path in {".", "./"}
            for file_path in file_paths
        ):
            raise ValueError("files must list literal repository-relative file paths.")
        issue_number = payload.get("issue")
        if issue_number is not None and (type(issue_number) is not int or issue_number < 1):
            raise ValueError("issue must be a positive issue number in the target repository.")
        return cls(payload)

    @property
    def validation_passed(self) -> bool:
        """真实入口已完成，且不存在待处理的显式人工验收条件。"""
        return self.payload["validation"]["status"] == "passed" and self.payload.get(
            "human_validation", {}
        ).get("status", "not_requested") not in {"pending", "failed"}

    def issue_body(self, base_sha: str) -> str:
        """生成只含任务输入的 Issue 正文，避免泄露实现答案。

        Args:
            base_sha: 本次任务代码起点。

        Returns:
            原话、来源与 AI 整理任务分开的 Markdown。
        """
        quotations = "\n\n".join(
            "\n".join(f"> {line}" for line in message.split("\n"))
            for message in self.payload["user_messages"]
        )
        return (
            f"## User requests (verbatim)\n\n{quotations}\n\n"
            f"Source: `{self.payload['request_source']}`; captured at delivery time.\n\n"
            f"## Task (AI synthesis)\n\n{self.payload['task']}\n\n"
            f"Code starting point: `{base_sha}`\n\n"
            "<!-- just-ai-pr:task-v1 -->\n"
        )


class DeliveryJournal:
    """在 Git 元数据中追加交付事件，状态文件只用于中断恢复。"""

    def __init__(self, common_git_dir: Path, branch_name: str) -> None:
        """按分支隔离可恢复状态。

        Args:
            common_git_dir: Git common dir 的绝对路径。
            branch_name: 当前交付分支。
        """
        branch_key = hashlib.sha256(branch_name.encode("utf-8")).hexdigest()[:24]
        self.directory = common_git_dir / "ai-pr" / branch_key
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "state.json"
        self.state = (
            json.loads(self.state_path.read_text(encoding="utf-8"))
            if self.state_path.exists()
            else {}
        )

    def save(self, **updates: Any) -> None:
        """原子替换恢复状态，不覆盖事件历史。

        Args:
            **updates: 已完成操作对应的恢复字段。
        """
        self.state.update(updates)
        pending_path = self.directory / "state.pending"
        pending_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        pending_path.replace(self.state_path)

    def append(self, event_name: str, **facts: Any) -> dict[str, Any]:
        """逐行追加事实，并返回可发布的同一记录。

        Args:
            event_name: 事件名称，不代表样本质量。
            **facts: 当前观察到的上下文、SHA 与状态。

        Returns:
            已落盘的事件。
        """
        event = {
            "schema_version": 2,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "event": event_name,
            **facts,
        }
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as event_file:
            event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


def build_observations(context: DeliveryContext, ci_status: str) -> dict[str, Any]:
    """区分程序观察、调用方真实入口报告与人工验收，绝不合并为总 passed。

    Args:
        context: 当前对话准备的上下文。
        ci_status: 与本轮 head 匹配的 GitHub 检查状态。

    Returns:
        四条独立验证轴，缺少人工确认时保持未申请。
    """
    return {
        "local": {"status": "passed", "source": "command", "details": "just test returned 0."},
        "github_ci": {"status": ci_status, "source": "github"},
        "real_entry": {**context.payload["validation"], "source": "agent_report"},
        "human": {
            **context.payload.get(
                "human_validation",
                {"status": "not_requested", "details": "No human acceptance was claimed."},
            ),
            "source": "caller_report" if "human_validation" in context.payload else "not_provided",
        },
    }


def render_validation(observations: dict[str, Any]) -> str:
    """生成没有总体通过标签的验证概况，供评论和 PR 正文共用。

    Args:
        observations: 四条独立验证轴。

    Returns:
        人读 Markdown 状态表。
    """
    rows = (
        ("local", "Local checks"),
        ("github_ci", "GitHub CI"),
        ("real_entry", "Real-entry validation (agent report)"),
        ("human", "Human acceptance"),
    )
    return "| Validation | Status |\n| --- | --- |\n" + "\n".join(
        f"| {label} | {observations.get(key, {}).get('status', 'unknown')} |" for key, label in rows
    )


def render_overview(event: dict[str, Any]) -> str:
    """生成 PR 最新概况，保留功能摘要并分列验证状态。

    Args:
        event: 当前上下文、版本与独立验证观察。

    Returns:
        用于 PR 受管区的正文。
    """
    return (
        f"Closes #{event['issue']}\n\n## Changes\n\n{event['context']['summary']}\n\n"
        f"## Validation observations\n\n{render_validation(event['observations'])}\n\n"
        f"Head: `{event['head_sha']}`; delivery base: `{event['base_sha']}`.\n\n"
        "Evidence and original context are preserved in iteration comments. "
        "Merging is not a human-acceptance or training-quality label."
    )


def render_iteration(event: dict[str, Any]) -> str:
    """把逐轮事实原样嵌入评论，squash 后仍可关联失败与修正。

    Args:
        event: 包含本轮上下文与代码 SHA 的记录。

    Returns:
        同时可供人阅读与后续解析的 Markdown。
    """
    context = event["context"]
    observations = event.get(
        "observations",
        {
            "real_entry": context["validation"],
            "human": context.get("human_validation", {"status": "unknown"}),
        },
    )
    previous_head = event.get("previous_head_sha")
    if previous_head == event["head_sha"]:
        code_change = "No new code commit; validation update only."
    else:
        code_change = (
            f"Code version: `{previous_head or event['base_sha']}` → `{event['head_sha']}`."
        )
    round_summary = event.get("iteration_summary") or context.get("iteration_summary")
    if not round_summary and previous_head != event["head_sha"]:
        round_summary = context["commit_message"].splitlines()[0]
    encoded_event = json.dumps(event, ensure_ascii=False, indent=2)
    # 超过载荷中最长反引号段，避免原话关闭代码围栏。
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", encoded_event)), default=0) + 1)
    return (
        f"## Delivery update\n\n{code_change}\n\n"
        f"{round_summary or 'Updated validation observations for the same code version.'}\n\n"
        f"{render_validation(observations)}\n\n"
        f"<details><summary>Original task and iteration record</summary>\n\n"
        f"{fence}json\n{encoded_event}\n{fence}\n\n</details>\n"
    )


def update_pr_body(existing_body: str, managed_body: str) -> str:
    """仅更新命令拥有的正文区，保留审查者与用户已有内容。

    Args:
        existing_body: GitHub 当前 PR 正文。
        managed_body: 本次生成的最新交付概况。

    Returns:
        原内容与最新受管区组合后的正文。
    """
    managed_section = f"{BEGIN_MARKER}\n{managed_body}\n{END_MARKER}"
    if BEGIN_MARKER in existing_body and END_MARKER in existing_body:
        prefix, remainder = existing_body.split(BEGIN_MARKER, 1)
        _, suffix = remainder.split(END_MARKER, 1)
        return prefix + managed_section + suffix
    return existing_body.rstrip() + "\n\n" + managed_section
