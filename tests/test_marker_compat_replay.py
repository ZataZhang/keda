"""GitHub 隐藏标记的逐字节兼容与认领语义回放测试。

事实源是 ``tests/support/marker_compat_baseline.json``：里面的每段标记文本都由**改名前
那棵树**（merge-base commit，见 fixture 的 ``generated_from_ref``）的真实写入函数产出，
解析结果同样来自那棵树的真实解析函数，生成脚本是
``.iar/evidence/scripts/rv-3-marker-snapshot.py``（证据脚本，不进改动集）。

本测试在当前树上做三件事：

1. 用当前树的真实写入函数按同样的入参重跑一遍，逐字节比对文本——写入侧不许因产品改名漂移。
2. 把记录在案的文本送进当前树的真实解析函数，比对解析结果——解析侧语义不许漂移，
   而不是只比字符串。
3. 按基线记下的入参复核认领判定：未过期的旧认领仍判「已占用」（不回收），过期认领
   按原规则可回收，且更晚的投标在仲裁里落败、不会二次认领同一个 Issue。

修改标记格式属于跨进程契约变更，必须连同基线一起重生成，不要手改 fixture 里的文本。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import importlib
import json
import pathlib
from typing import Any

import pytest

from backend.core.shared.models.agent_runner import AppConfig
from backend.core.use_cases.agent_runner_claim_arbitration import (
    ClaimArbitrationLost,
    ClaimBid,
    arbitrate_first_claim,
)
from backend.core.use_cases.agent_runner_reclaim import (
    classify_claim_staleness,
    parse_claim_marker_detail,
)
from tests.conftest import FakeGitHubClient

BASELINE_PATH = pathlib.Path(__file__).parent / "support" / "marker_compat_baseline.json"

EXPECTED_MARKER_KINDS: frozenset[str] = frozenset(
    {
        "iar:auto-sign-off",
        "iar:ci-auto-repair-policy",
        "iar:claim",
        "iar:claim-withdrawn",
        "iar:dependency-wait",
        "iar:depends-on",
        "iar:direct-pr",
        "iar:event",
        "iar:evidence-format",
        "iar:evidence-format-waived",
        "iar:failure-context",
        "iar:failure-context-ref",
        "iar:fast-merge",
        "iar:merge-acceptance",
        "iar:pr-contract",
        "iar:pr-contract-end",
        "iar:realistic-validation",
        "iar:realistic-validation-end",
        "iar:reconcile",
        "iar:structured-evidence",
        "iar:validation-evidence",
        "iar:validation-waived",
        "iar:verifier-verdict",
        "iar:verifier-warning",
        "iar-attempt-history",
        "iar-attempt-end",
    }
)
"""基线覆盖的标记种类（跨进程契约，永久保留旧拼写）。"""


def load_baseline() -> dict[str, Any]:
    """读取标记基线快照。"""
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


BASELINE = load_baseline()
ENTRIES: list[dict[str, Any]] = list(BASELINE["entries"])
ENTRY_IDS: list[str] = [str(entry["id"]) for entry in ENTRIES]


def _attr(dotted: str) -> Any:
    """按 ``module:attr.path`` 取对象。"""
    module_name, _, attr_path = dotted.partition(":")
    target: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        target = getattr(target, part)
    return target


def decode_input(spec: Any) -> Any:
    """把基线里记录的入参形态还原成对象（与生成脚本同一套规则）。"""
    if isinstance(spec, list):
        return [decode_input(item) for item in spec]
    if not isinstance(spec, dict):
        return spec
    if "__datetime__" in spec:
        return dt.datetime.fromisoformat(spec["__datetime__"])
    if "__path__" in spec:
        return pathlib.Path(spec["__path__"])
    if "__attr__" in spec:
        return _attr(spec["__attr__"])
    if "__dataclass__" in spec:
        cls = _attr(spec["__dataclass__"])
        return cls(**{name: decode_input(value) for name, value in spec["kwargs"].items()})
    if "__tuple__" in spec:
        return tuple(decode_input(item) for item in spec["__tuple__"])
    if "__pairs__" in spec:
        return {decode_input(key): decode_input(value) for key, value in spec["__pairs__"]}
    raise AssertionError(f"未知的入参编码形态: {sorted(spec)}")


def encode_result(value: Any) -> Any:
    """把解析结果压成朴素 JSON（与生成脚本同一套规则，便于直接比较）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, pathlib.PurePath):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return {"__enum__": f"{type(value).__name__}.{value.name}", "value": value.value}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: encode_result(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [encode_result(item) for item in value]
    if isinstance(value, dict):
        return {str(key): encode_result(item) for key, item in value.items()}
    return repr(value)


def _literal_from_source(module_name: str, needle: str) -> str:
    """从模块源码的字符串常量里取出含 ``needle`` 的最短一条（与生成脚本同法）。"""
    import ast

    module_path = pathlib.Path(str(importlib.import_module(module_name).__file__))
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    found = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and needle in node.value
    ]
    assert found, f"{module_name} 源码里找不到含 {needle!r} 的字面量"
    return min(found, key=len)


def rebuild_output(builder: dict[str, Any]) -> str:
    """用当前树的写入函数重放一个写入步骤。"""
    if builder.get("const"):
        return _attr(builder["const"])
    if builder.get("literal_in"):
        return _literal_from_source(builder["literal_in"], builder["contains"])
    callable_ref = _attr(builder["call"])
    kwargs = {name: decode_input(spec) for name, spec in builder["kwargs"].items()}
    return callable_ref(**kwargs)


def replay_parse(step: dict[str, Any], outputs: dict[str, str]) -> Any:
    """用当前树的解析函数重放一个解析步骤。"""
    target = _attr(step["parser"])
    text = outputs[step["input"]] if step.get("input") else None
    extra = {name: decode_input(spec) for name, spec in step.get("kwargs", {}).items()}
    if step.get("method") == "search":
        match = target.search(text)
        return None if match is None else {"match": match.group(0), "groups": match.groupdict()}
    if step.get("method") == "contains":
        assert isinstance(target, str), "contains 步骤的 parser 必须是字符串常量"
        return target in text
    if step.get("as_list"):
        return encode_result(target([text], **extra))
    if step.get("text_kwarg"):
        return encode_result(target(**{step["text_kwarg"]: text}, **extra))
    if text is None:
        return encode_result(target(**extra))
    return encode_result(target(text, **extra))


def _writers_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return list(entry["writers"])


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_marker_writers_still_emit_baseline_bytes(entry: dict[str, Any]) -> None:
    """当前树的写入函数对同一组入参必须产出与基线逐字节相同的文本。"""
    recorded = entry["outputs"]
    for builder in _writers_of(entry):
        name = builder["as"]
        assert (
            rebuild_output(builder) == recorded[name]
        ), f"{entry['kind']} 的 {name} 文本已漂移（写入侧不许改跨进程契约）"


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_marker_parsers_replay_baseline_semantics(entry: dict[str, Any]) -> None:
    """基线文本在当前树的解析器里必须得到与基线一致的判定结果。"""
    outputs = dict(entry["outputs"])
    for step in entry["parses"]:
        assert (
            replay_parse(step, outputs) == step["result"]
        ), f"{entry['kind']} 经 {step['parser']} 的解析结果与基线不一致"


def test_baseline_covers_every_wire_marker_kind() -> None:
    """基线覆盖的种类集合就是跨进程契约的全集，少一种即回归。"""
    assert set(BASELINE["kinds"]) == set(EXPECTED_MARKER_KINDS)
    assert len(ENTRIES) == len(EXPECTED_MARKER_KINDS)


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_each_entry_text_carries_its_own_kind_token(entry: dict[str, Any]) -> None:
    """每条记录的文本里确实带着它声称的那个标记（防止 fixture 与种类对不上）。"""
    token = entry["kind"]
    assert any(token in text for text in entry["outputs"].values()), token


def test_claim_judgement_keeps_baseline_occupancy_semantics() -> None:
    """未过期的旧认领仍判已占用（不回收）；过期或进程死亡的按原规则可回收。"""
    claim_entry = next(entry for entry in ENTRIES if entry["kind"] == "iar:claim")
    judgement = claim_entry["claim_judgement"]
    comment_text = claim_entry["outputs"][judgement["input"]]
    detail = parse_claim_marker_detail(comment_text)
    assert detail is not None
    cases = judgement["cases"]
    assert encode_result(detail) == cases[0]["claim_detail"]

    expected_by_case = {case["case"]: case["result"] for case in cases}
    observed_by_case: dict[str, Any] = {}
    for case in cases:
        inputs = case["inputs"]
        observed_by_case[case["case"]] = encode_result(
            classify_claim_staleness(
                claim_pid=detail.pid,
                claim_started_at=detail.started_at,
                effective_now=decode_input(inputs["effective_now"]),
                ttl_seconds=inputs["ttl_seconds"],
                pid_alive=lambda _pid, alive=inputs["pid_alive"]: alive,
            )
        )
    assert observed_by_case == expected_by_case
    assert observed_by_case["live_within_ttl"] is None
    assert observed_by_case["live_past_ttl"] == "ttl_expired"
    assert observed_by_case["dead_pid"] == "dead_pid"


def test_late_bidder_still_loses_arbitration_against_baseline_claim() -> None:
    """基线认领评论仍是「更早的投标」：更晚的进程落败、不切 running、不改别人的评论。"""
    claim_entry = next(entry for entry in ENTRIES if entry["kind"] == "iar:claim")
    baseline_comment = claim_entry["outputs"]["comment"]
    claim_detail = claim_entry["claim_judgement"]["cases"][0]["claim_detail"]
    # 并发窗口只有 120 秒，历史认领要落在窗口内才参与仲裁，因此这里的 now 取认领后
    # 60 秒（TTL 判定的 now 由 fixture 记，两者互不干涉）。
    effective_now = dt.datetime.fromisoformat(claim_detail["started_at"]) + dt.timedelta(seconds=60)

    client = FakeGitHubClient()
    client.comment_issue(42, baseline_comment)
    late_bid_started_at = effective_now - dt.timedelta(seconds=30)
    winning_bidder = ClaimBid(
        comment_id=202,
        host="runner-late",
        pid=5150,
        started_at=late_bid_started_at,
        agent="codex",
    )

    with pytest.raises(ClaimArbitrationLost) as exc_info:
        arbitrate_first_claim(
            issue_number=42,
            github_client=client,
            config=AppConfig(),
            selected_agent="codex",
            grace_seconds=0.0,
            host=winning_bidder.host,
            pid=winning_bidder.pid,
            started_at=late_bid_started_at,
            sleeper=lambda _seconds: None,
            pid_alive=lambda candidate: True,
            now=effective_now,
        )

    assert exc_info.value.winner is not None
    assert exc_info.value.winner.host == claim_detail["host"]
    assert exc_info.value.winner.pid == claim_detail["pid"]
    label_calls = [call for call in client.calls if call["method"] == "edit_issue_labels"]
    assert not [call for call in label_calls if "agent/running" in call["add"]]


def test_withdrawn_claim_is_never_read_as_a_live_claim() -> None:
    """撤销 marker 不被认领解析器命中，线程里最近的认领仍是赢家的。"""
    withdrawn = next(entry for entry in ENTRIES if entry["kind"] == "iar:claim-withdrawn")
    comment_text = withdrawn["outputs"]["comment"]
    assert parse_claim_marker_detail(comment_text) is None
    claim_entry = next(entry for entry in ENTRIES if entry["kind"] == "iar:claim")
    thread = [claim_entry["outputs"]["comment"], comment_text]
    latest = next(
        (body for body in reversed(thread) if parse_claim_marker_detail(body) is not None),
        None,
    )
    assert latest == claim_entry["outputs"]["comment"]
