"""从 Claude Code 与 codex 的会话记录还原"真人发起的任务"样本。

交互式会话的 jsonl 里有当时**逐字**的用户首句、会话期间做过的提交，以及会话与 PR 的链接
（``pr-link`` 记录）。这是 keda 之外最接近"原始输入"的数据，但也最敏感，所以：

- **显式开启**：只读 ``--agent-sessions-repo`` 指定仓库对应的会话目录；
- **只取真人会话**：``entrypoint == "sdk-cli"`` 的程序化会话（keda runner、verifier、探针）排除，
  它们已经是 ``keda_run`` 样本；
- **只导出事实与脱敏后的首句**：不导出完整轨迹，不带本机路径；首句经 :mod:`redaction` 脱敏；
- **输入永远偏弱**：首句往往是对话式的（"你看一下这个"），不是任务规格，因此来源标
  ``session_first_prompt``，永远不计入 ``eval_ready``，留给后续人工与更强 Agent 整理。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.dataset_export.redaction import redact_text

CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
AGENT_CLAUDE_CODE = "claude_code"
AGENT_CODEX = "codex"
PROGRAMMATIC_ENTRYPOINTS = frozenset({"sdk-cli"})
MAX_PROMPT_CHARS = 20_000
# ``git commit`` 的输出行：``[main 1a2b3c4] subject``，首次提交带 ``(root-commit)``。
COMMIT_OUTPUT_PATTERN = re.compile(r"\[[\w./-]+(?: \(root-commit\))? ([0-9a-f]{7,40})\]")
# 斜杠命令与系统注入的"用户"消息不是人说的话。
_INJECTED_PROMPT_PREFIXES = ("<local-command", "<command-name>", "<command-message>", "Caveat:")
# codex 的首条"用户"消息是注入的 AGENTS.md 与环境上下文，不是人说的话。
_CODEX_INJECTED_PREFIXES = (
    "# AGENTS.md",
    "<environment_context>",
    "<turn_aborted>",
    "<send_user_message_question_reply>",
    "<user_instructions>",
)
_UNSAFE_PROJECT_DIR_CHARS = re.compile(r"[^A-Za-z0-9-]")


@dataclass
class ParsedSession:
    """从一个会话文件解析出的事实（尚未脱敏、尚未对照 git）。"""

    session_id: str
    entrypoint: str | None
    started_at: str | None
    ended_at: str | None
    first_prompt: str
    human_turns: int
    models: list[str] = field(default_factory=list)
    git_branch: str | None = None
    pr_links: list[dict[str, Any]] = field(default_factory=list)
    commit_shas: list[str] = field(default_factory=list)
    agent: str = AGENT_CLAUDE_CODE


def claude_project_dir(*, repo_path: Path, projects_root: Path | None = None) -> Path:
    """仓库对应的 Claude Code 会话目录：绝对路径里非字母数字的字符都换成 ``-``。"""
    root = projects_root or CLAUDE_PROJECTS_ROOT
    return root / _UNSAFE_PROJECT_DIR_CHARS.sub("-", str(repo_path.resolve()))


def _is_human_prompt(entry: dict[str, Any]) -> bool:
    """是否是真人在主会话里输入的一句话（排除工具结果、元消息、子代理、斜杠命令注入）。"""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = entry.get("message", {}).get("content")
    return isinstance(content, str) and not content.startswith(_INJECTED_PROMPT_PREFIXES)


def _tool_result_text(item: dict[str, Any]) -> str:
    """取 tool_result 的文本，兼容字符串与 ``[{"type": "text", ...}]`` 两种形态。"""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _collect_commit_tool_use_ids(entries: list[dict[str, Any]]) -> set[str]:
    """找出所有执行了 ``git commit`` 的 Bash 工具调用 id。"""
    tool_use_ids: set[str] = set()
    for entry in entries:
        content = (
            entry.get("message", {}).get("content") if entry.get("type") == "assistant" else None
        )
        for item in content if isinstance(content, list) else []:
            command = (
                (item.get("input") or {}).get("command", "")
                if item.get("type") == "tool_use"
                else ""
            )
            if item.get("name") == "Bash" and re.search(r"\bgit\b[^\n]*\bcommit\b", str(command)):
                tool_use_ids.add(item["id"])
    return tool_use_ids


def _collect_commit_shas(entries: list[dict[str, Any]], tool_use_ids: set[str]) -> list[str]:
    """按出现顺序收集这些提交命令输出里的提交哈希（去重）。"""
    shas: list[str] = []
    for entry in entries:
        content = entry.get("message", {}).get("content") if entry.get("type") == "user" else None
        for item in content if isinstance(content, list) else []:
            if item.get("type") == "tool_result" and item.get("tool_use_id") in tool_use_ids:
                for sha in COMMIT_OUTPUT_PATTERN.findall(_tool_result_text(item)):
                    if sha not in shas:
                        shas.append(sha)
    return shas


def parse_session_file(session_path: Path) -> ParsedSession | None:
    """解析一个会话文件；程序化会话、没有真人首句、文件损坏都返回 ``None``。"""
    entries: list[dict[str, Any]] = []
    try:
        for line in session_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    first_entry = next((entry for entry in entries if entry.get("type") == "user"), None)
    if first_entry is None or first_entry.get("entrypoint") in PROGRAMMATIC_ENTRYPOINTS:
        return None
    human_prompts = [entry for entry in entries if _is_human_prompt(entry)]
    if not human_prompts:
        return None
    timestamps = [entry["timestamp"] for entry in entries if entry.get("timestamp")]
    assistant_models = {
        entry["message"]["model"]
        for entry in entries
        if entry.get("type") == "assistant"
        and not entry.get("isSidechain")
        and entry.get("message", {}).get("model")
    }
    pr_links = {
        (entry["prNumber"], entry.get("prRepository")): {
            "number": entry["prNumber"],
            "repository": entry.get("prRepository"),
        }
        for entry in entries
        if entry.get("type") == "pr-link" and entry.get("prNumber")
    }
    return ParsedSession(
        session_id=str(first_entry.get("sessionId") or session_path.stem),
        entrypoint=first_entry.get("entrypoint"),
        started_at=min(timestamps) if timestamps else None,
        ended_at=max(timestamps) if timestamps else None,
        first_prompt=human_prompts[0]["message"]["content"],
        human_turns=len(human_prompts),
        models=sorted(assistant_models),
        git_branch=first_entry.get("gitBranch"),
        pr_links=list(pr_links.values()),
        commit_shas=_collect_commit_shas(entries, _collect_commit_tool_use_ids(entries)),
    )


def redact_first_prompt(prompt: str) -> dict[str, Any]:
    """截断并脱敏首句，返回写进样本 ``input`` 的字段。"""
    truncated = len(prompt) > MAX_PROMPT_CHARS
    result = redact_text(prompt[:MAX_PROMPT_CHARS])
    return {
        "body": result.text,
        "truncated": truncated,
        "redaction_count": result.redaction_count,
        "redaction_reviewed": False,
    }


def read_codex_session_meta(session_path: Path) -> dict[str, Any] | None:
    """只读 codex 会话文件的第一行（``session_meta``），用来在完整解析前按仓库过滤。"""
    try:
        with session_path.open(encoding="utf-8") as session_file:
            first_line = session_file.readline()
        first_entry = json.loads(first_line)
    except (OSError, json.JSONDecodeError):
        return None
    payload = first_entry.get("payload") if first_entry.get("type") == "session_meta" else None
    return payload if isinstance(payload, dict) else None


def is_human_codex_session(meta: dict[str, Any]) -> bool:
    """真人在 codex 里交互发起的会话：排除 ``codex exec``（keda runner）与子代理线程。"""
    return meta.get("originator") == "codex-tui" and isinstance(meta.get("source"), str)


def codex_session_belongs_to_repo(meta: dict[str, Any], *, repo_path: Path) -> bool:
    """会话的工作目录是否就是该仓库或其子目录；codex 会话按日期分区，只能靠 ``cwd`` 归属。"""
    cwd = meta.get("cwd")
    if not isinstance(cwd, str):
        return False
    session_cwd = Path(cwd)
    resolved_repo = repo_path.resolve()
    return session_cwd == resolved_repo or resolved_repo in session_cwd.parents


def _codex_text(items: Any) -> str:
    """拼接 codex 消息或工具输出里所有带 ``text`` 的片段（图片等忽略）。"""
    if isinstance(items, str):
        return items
    if isinstance(items, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in items
            if isinstance(item, dict) and item.get("text")
        )
    return ""


def parse_codex_session_file(session_path: Path) -> ParsedSession | None:
    """解析一个 codex 会话文件；非真人会话、没有真人首句、文件损坏都返回 ``None``。"""
    entries: list[dict[str, Any]] = []
    try:
        for line in session_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    meta = next((e["payload"] for e in entries if e.get("type") == "session_meta"), None)
    if meta is None or not is_human_codex_session(meta):
        return None
    human_prompts: list[str] = []
    commit_call_ids: set[str] = set()
    commit_shas: list[str] = []
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("type") != "response_item":
            continue
        kind = payload.get("type")
        if kind == "message" and payload.get("role") == "user":
            text = _codex_text(payload.get("content"))
            if text and not text.startswith(_CODEX_INJECTED_PREFIXES):
                human_prompts.append(text)
        elif kind == "custom_tool_call" and re.search(
            r"\bgit\b[^\n]*\bcommit\b", str(payload.get("input", ""))
        ):
            commit_call_ids.add(payload.get("call_id"))
        elif kind == "custom_tool_call_output" and payload.get("call_id") in commit_call_ids:
            for sha in COMMIT_OUTPUT_PATTERN.findall(_codex_text(payload.get("output"))):
                if sha not in commit_shas:
                    commit_shas.append(sha)
    if not human_prompts:
        return None
    timestamps = [entry["timestamp"] for entry in entries if entry.get("timestamp")]
    git_info = meta.get("git") if isinstance(meta.get("git"), dict) else {}
    return ParsedSession(
        session_id=str(meta.get("id") or session_path.stem),
        entrypoint=meta.get("originator"),
        started_at=meta.get("timestamp") or (min(timestamps) if timestamps else None),
        ended_at=max(timestamps) if timestamps else None,
        first_prompt=human_prompts[0],
        human_turns=len(human_prompts),
        models=[],
        git_branch=git_info.get("branch"),
        pr_links=[],
        commit_shas=commit_shas,
        agent=AGENT_CODEX,
    )
