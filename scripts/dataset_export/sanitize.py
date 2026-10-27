"""导出样本前的统一清理：去掉不该进入数据集的个人信息，并给归档加一道兜底。

早期版本只给会话首句和提交正文做了脱敏，而 keda 运行样本直接带出了 ``gh`` 返回的原始 PR 对象
（含每个提交作者的姓名、登录名和邮箱）、错误摘要里的本机绝对路径、以及 Issue 正文里的示例邮箱。
这里用两道防线补上：

1. **白名单**：PR 只保留判定"已合并"与还原提交链所需的字段，作者信息一律不进样本；
2. **统一脱敏**：所有样本的自由文本字段（任务标题与正文、错误摘要、提交主题）最后再过一遍
   :func:`redaction.redact_text`，它是幂等的，已经脱敏过的文本不会被重复计数。

兜底检查见 :func:`find_sensitive_kinds`：归档前如果还能在样本里找到邮箱、本机路径、令牌或私钥，
就拒绝写入，宁可让定时任务失败，也不把个人信息写进长期保存的仓库。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from scripts.dataset_export.redaction import detect_sensitive_kinds, redact_text

# 判定"已合并"、还原提交链、统计规模所需的 PR 字段；其余（尤其是 authors、messageBody）一律丢弃。
PR_KEPT_FIELDS = (
    "number",
    "state",
    "mergedAt",
    "headRefName",
    "headRefOid",
    "additions",
    "deletions",
    "changedFiles",
)


def summarize_pull_request(pull_request: dict[str, Any]) -> dict[str, Any]:
    """把 ``gh`` 返回的原始 PR 对象收窄到白名单字段；提交只留哈希与标题行（标题行再脱敏）。"""
    kept: dict[str, Any] = {key: pull_request[key] for key in PR_KEPT_FIELDS if key in pull_request}
    merge_commit = pull_request.get("mergeCommit")
    if isinstance(merge_commit, dict):
        kept["mergeCommit"] = {"oid": merge_commit.get("oid")}
    if isinstance(pull_request.get("commits"), list):
        kept["commits"] = [
            {
                "oid": commit.get("oid"),
                "messageHeadline": redact_text(commit.get("messageHeadline") or "").text,
            }
            for commit in pull_request["commits"]
        ]
    return kept


def sanitize_sample_text(sample: dict[str, Any]) -> None:
    """原地清理样本里的自由文本字段；新发现的脱敏次数累加进 ``input.redaction_count``。"""
    sample_input = sample["input"]
    extra_hits = 0
    for key in ("title", "body"):
        if isinstance(sample_input.get(key), str):
            result = redact_text(sample_input[key])
            sample_input[key] = result.text
            extra_hits += result.redaction_count
    for run in sample.get("runs") or []:
        if isinstance(run.get("error_summary"), str):
            run["error_summary"] = redact_text(run["error_summary"]).text
    for commit in (sample.get("code") or {}).get("commits", []):
        if isinstance(commit.get("subject"), str):
            commit["subject"] = redact_text(commit["subject"]).text
    sample_input["redaction_count"] = int(sample_input.get("redaction_count") or 0) + extra_hits
    sample_input.setdefault("redaction_reviewed", False)


def _iter_strings(value: Any) -> Iterator[str]:
    """递归取出样本里所有真实的字符串值。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def find_sensitive_kinds(sample: dict[str, Any]) -> set[str]:
    """兜底检查：返回样本里还能找到的敏感信息种类（不返回具体值，免得又把它写进日志）。

    **逐个检查真实的字符串值，而不是检查序列化后的 JSON 文本**：JSON 会把引号转义成 ``\\"``，
    让 ``grep -n "/Users/" config.toml`` 这种不带用户名的裸路径被误判成"路径后面跟着用户名"，
    与脱敏时的口径不一致。
    """
    kinds: set[str] = set()
    for text in _iter_strings(sample):
        kinds |= detect_sensitive_kinds(text)
    return kinds
