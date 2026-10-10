"""PR body 与 prd skill 发布契约（PR-Native Acceptance）的对齐层。

背景：runner 的 draft PR 正文由写死的 config prompt 生成（或降级到 fallback
body），与 prd skill ``references/pr-evidence-and-merge-acceptance.md`` 要求的
PR-Native Acceptance 结构（唯一 PRD 路径、"合并即接受"声明、证据呈现）脱节，
曾产出 PR #163 这类"缺 PRD 链接、缺声明、rv 预勾"却无任何标注的正文。

本模块提供四段能力，全部是纯函数 + 文件读取，无网络与副作用：

1. **教学**（prompt 注入）：把 skill 的发布契约参考文本注入 draft PR 的
   agent prompt（调用侧包装 config，见 :mod:`agent_runner_publish`）。
2. **确定性正文补锚点**：fallback body 与 ``body_template`` 渲染结果由代码而非
   agent 产出，没有"教学"可言，缺锚点由代码直接补齐（PRD 引用 + "合并即接受"
   声明）。agent 撰写的正文不走这一步。
3. **发布端软门**：对最终正文做可 grep 锚点校验；缺失时不阻断发布，而是
   在正文末尾追加显式的不合规标注块（机器 marker + 人读说明）。经第 2 段补齐
   后仍缺锚点的只剩 agent 无视教学的正文，标注正好把这个信号暴露出来。
4. **合并端硬门**：合并队列只消费发布端写下的 ``iar:pr-contract`` 标注
   marker——runner 自己声明过不合规的 PR 不自动合并。发布于本机制上线前
   的存量 PR 无 marker，不受影响，避免在途 PR 被永久卡死。

约定：契约校验只作用于 Issue body 带 ``PRD path:`` 锚点的 PRD 交付
（prd skill 的管辖范围）；无 PRD 的轻量 Issue 不新增要求。
所有 hidden marker 与 ``agent_runner_events.py`` 的 ``iar:event`` 同型
（``<!-- iar:... -->`` + 命名捕获组正则）。

批次总 PR（v2）：夜间聚合把一组同仓库来源 Issue / PR 合成唯一总 Draft PR，其正文
用 ``<!-- iar:aggregate-pr version=1 issues=... source_prs=... -->`` 记录来源集合，
用 ``<!-- iar:merge-acceptance version=2 -->`` 声明"合并本总 PR 即接受正文列出的每
一份 PRD"。下方 ``aggregate`` 前缀的一组纯函数只做**本地字符串**的集合断言（去重、
完整性、重复拒绝、来源 marker 匹配），不发布 PR、不读取 GitHub。普通单 PRD ``v1``
判定完全不变：v2 是叠加在聚合正文上的独立校验面，历史 v1 正文仍按 v1 规则通过。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from backend.core.use_cases.agent_runner_feedback import (
    extract_prd_path,
    resolve_prd_archive_path,
)
from backend.core.shared.prd_skill_location import resolve_prd_skill_path

# "合并即接受"声明必须携带的 hidden marker（prompt 教学与校验共用同一常量）。
MERGE_ACCEPTANCE_MARKER_TEXT = "<!-- iar:merge-acceptance version=1 -->"

_MERGE_ACCEPTANCE_MARKER_PATTERN = re.compile(
    r"<!--\s*iar:merge-acceptance\s+version=(?P<version>\d+)\s*-->"
)

# 发布端不合规标注块（合并端硬门的唯一事实源）。
_PR_CONTRACT_MARKER_PATTERN = re.compile(
    r"<!--\s*iar:pr-contract\s+version=(?P<version>\d+)\s+" r"missing=(?P<missing>[^\s>]+)\s*-->"
)

# 确定性正文补锚点时写入的"合并即接受"声明：形态取自 skill 参考文档的 PR body
# 模板，但只指向 PRD、不内联决策与可见结果清单（那是 agent 撰写正文的职责）。
# PRD 已在本 PR 里归档（Machine Contract v5），合并授权的是事后补记验收结论，
# 不是归档。小节标题被合并队列消费方与测试钉住，沿用旧名不改。
_DETERMINISTIC_ACCEPTANCE_HEADING = "## Human Acceptance And PRD Archive"
_DETERMINISTIC_ACCEPTANCE_STATEMENT = (
    "Merging this PR means that the merger accepts the human decisions and "
    "human-visible outcomes recorded in the linked PRD and authorizes post-merge "
    "acceptance recording on that PRD (its Human-Confirmed items are ticked and its "
    "banner becomes ✅ 已验收), provided the required gates remain green and the "
    "merged Git tree matches the verified tree."
)

# skill 仓库内发布契约参考文档相对 SKILL.md 的位置。
_PUBLISH_CONTRACT_REFERENCE_RELPATH = Path("references") / "pr-evidence-and-merge-acceptance.md"

# 锚点标识 → 人读含义（标注块与错误信息共用，避免两处文案漂移）。
_CONTRACT_ANCHOR_DESCRIPTIONS: dict[str, str] = {
    "prd-link": "body references neither the archived PRD path nor the Issue's pending PRD path",
    "merge-acceptance": (
        "body is missing the `<!-- iar:merge-acceptance version=1 -->` "
        "merge-means-acceptance declaration"
    ),
}


def load_prd_publish_contract(explicit_skill_path: Path | None = None) -> str | None:
    """读取 prd skill 的发布契约参考文本；不可达时安全返回 ``None``。

    解析逻辑复用 :func:`resolve_prd_skill_path`（``KEDACODE_PRD_SKILL_PATH`` /
    ``KEDACODE_SKILLS_DIR`` 覆盖，且优先取 keda 自有 ``~/.kedacode/skills``），在其
    ``SKILL.md`` 同级的 ``references/`` 下定位参考文档。文件缺失
    只意味着教学缺失——发布端会照常校验锚点并标注，调用方无需特殊处理。

    Args:
        explicit_skill_path: 显式 ``SKILL.md`` 路径（测试用）；``None`` 时按
            环境解析。

    Returns:
        契约参考文本（已 strip）；skill 不可达或文档缺失/为空时 ``None``。
    """
    skill_path = resolve_prd_skill_path(explicit_skill_path)
    contract_path = skill_path.parent / _PUBLISH_CONTRACT_REFERENCE_RELPATH
    if not contract_path.is_file():
        return None
    try:
        contract_text = contract_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return contract_text.strip() or None


def build_contract_prompt_prefix(contract_text: str) -> str:
    """构建注入 draft PR agent prompt 的发布契约教学段。

    教学只加要求、不改既有输出约束：``Closes #N`` 首行锚点仍由 config prompt
    与 ``_validate_pr_body`` 把关，本段不重复声明以免漂移。
    """
    return "\n".join(
        [
            "The PR body you produce must satisfy the repository's PR-Native "
            "Acceptance contract (from the prd skill).",
            "",
            "In addition to the instructions below:",
            "1. Include a line `- PRD: <relative prd path>` pointing at the "
            "archived PRD: the runner archives it inside this PR, so take the file "
            "name from the Issue body's `PRD path:` anchor and use its "
            "`tasks/archive/` location.",
            "2. Include the hidden marker `" + MERGE_ACCEPTANCE_MARKER_TEXT + "` "
            "in the body, immediately followed by a sentence stating that "
            "merging this PR constitutes acceptance of the listed human "
            "decisions and visible outcomes and authorizes post-merge acceptance "
            "recording on the linked PRD.",
            "3. Never pre-tick the Realistic Validation sign-off checklist; "
            "those items are for human review.",
            "",
            "--- prd skill publish contract reference ---",
            contract_text,
            "--- end of reference ---",
        ]
    )


def find_pr_body_contract_violations(pr_body: str, issue_body: str) -> list[str]:
    """返回 PR 正文缺失的契约锚点标识列表；完全合规时为空。

    仅当 Issue body 携带 ``PRD path:`` 锚点（即 PRD 交付）时才校验；无 PRD 的
    Issue 返回空列表。当前锚点：

    - ``prd-link``：正文既未引用 PRD 的归档路径，也未引用 Issue 指向的 pending
      路径。PRD 在本 PR 里归档，归档路径是首选写法；pending 路径照样认，存量
      PR 与人手写的正文不会因此被标注。
    - ``merge-acceptance``：正文缺少 "合并即接受" hidden marker。
    """
    prd_relative_path = extract_prd_path(issue_body)
    if prd_relative_path is None:
        return []
    violations: list[str] = []
    accepted_prd_paths = [prd_relative_path]
    archive_relative_path = resolve_prd_archive_path(prd_relative_path)
    if archive_relative_path is not None:
        accepted_prd_paths.append(archive_relative_path)
    if not any(accepted_prd_path in pr_body for accepted_prd_path in accepted_prd_paths):
        violations.append("prd-link")
    if not _MERGE_ACCEPTANCE_MARKER_PATTERN.search(pr_body):
        violations.append("merge-acceptance")
    return violations


def append_missing_contract_anchors(pr_body: str, issue_body: str) -> str:
    """为 runner 自己拼装的确定性正文补齐缺失的契约锚点。

    fallback body 与 ``body_template`` 渲染结果由代码而非 agent 产出，缺锚点
    只能由代码补。只补 PRD 引用与"合并即接受"声明（marker + 指向 PRD 的声明句），
    不内联 PRD 的决策与可见结果清单，避免确定性正文冒充 agent 撰写的证据呈现。
    PRD 引用写归档路径（PRD 在本 PR 里归档）；Issue 路径不在 ``tasks/pending/``
    下、换算不出归档路径时，原样写 Issue 路径。

    agent 撰写的正文不应经过本函数：agent 无视教学时应由发布端软门标注暴露，
    而不是被静默补齐。

    Args:
        pr_body (str): 确定性生成的 PR 正文。
        issue_body (str): Issue 正文，用于提取 ``PRD path:`` 锚点。

    Returns:
        str: 补齐后的正文；Issue 无 PRD 锚点或正文锚点已齐全时原样返回 ``pr_body``。
    """
    prd_relative_path = extract_prd_path(issue_body)
    missing_anchors = find_pr_body_contract_violations(pr_body, issue_body)
    if prd_relative_path is None or not missing_anchors:
        return pr_body
    anchor_section_lines = [_DETERMINISTIC_ACCEPTANCE_HEADING, ""]
    if "prd-link" in missing_anchors:
        linked_prd_path = resolve_prd_archive_path(prd_relative_path) or prd_relative_path
        anchor_section_lines.extend([f"- PRD: {linked_prd_path}", ""])
    if "merge-acceptance" in missing_anchors:
        anchor_section_lines.extend(
            [MERGE_ACCEPTANCE_MARKER_TEXT, _DETERMINISTIC_ACCEPTANCE_STATEMENT]
        )
    anchor_section_text = "\n".join(anchor_section_lines).rstrip()
    return f"{pr_body.rstrip()}\n\n{anchor_section_text}\n"


def build_contract_annotation_block(violations: list[str]) -> str:
    """构建追加到 PR 正文末尾的不合规标注块（软门，不阻断发布）。"""
    missing_text = ",".join(violations)
    violation_lines = [
        f"- `{anchor}`: {_CONTRACT_ANCHOR_DESCRIPTIONS.get(anchor, 'unknown anchor')}"
        for anchor in violations
    ]
    return "\n".join(
        [
            f"<!-- iar:pr-contract version=1 missing={missing_text} -->",
            "## PR Body Contract Violation (auto-generated)",
            "",
            "This PR body does not satisfy the prd skill PR-Native Acceptance "
            "contract, so the autopilot merge queue will refuse to auto-merge "
            "it. Missing anchors:",
            "",
            *violation_lines,
            "",
            "<!-- iar:pr-contract-end -->",
        ]
    )


def parse_contract_annotation(pr_body: str) -> list[str]:
    """解析正文中的 ``iar:pr-contract`` 标注 marker，返回缺失锚点列表。

    无标注时返回空列表。列表内容以 marker 为准（发布时的判定结果），不重算。
    """
    match = _PR_CONTRACT_MARKER_PATTERN.search(pr_body)
    if match is None:
        return []
    return [part for part in match.group("missing").split(",") if part]


def has_contract_annotation(pr_body: str) -> bool:
    """PR 正文是否被发布端标注为契约不合规（合并队列硬门的判定源）。"""
    return bool(_PR_CONTRACT_MARKER_PATTERN.search(pr_body))


# ---------------------------------------------------------------------------
# 批次总 PR 多 PRD 验收契约（v2）——纯本地字符串 / 路径集合断言，无 GitHub 副作用。
# ---------------------------------------------------------------------------

# 总 PR "合并即接受整组 PRD"声明必须携带的 hidden marker（v2，区别于单 PRD 的 v1）。
MERGE_ACCEPTANCE_V2_MARKER_TEXT = "<!-- iar:merge-acceptance version=2 -->"

# 来源集合 marker 的版本与正则：``issues=`` 必填，``source_prs=`` 允许为空串
# （dry-run 预览阶段来源 PR 尚未产生）。
_AGGREGATE_SOURCE_MARKER_VERSION = 1
_AGGREGATE_SOURCE_MARKER_PATTERN = re.compile(
    r"<!--\s*iar:aggregate-pr\s+version=(?P<version>\d+)\s+"
    r"issues=(?P<issues>[^\s>]*)\s+source_prs=(?P<source_prs>[^\s>]*)\s*-->"
)

# 正文里的 PRD 引用行（v1 确定性补锚点与 v2 聚合清单共用同一 ``- PRD: <path>`` 形态）。
_PRD_LIST_LINE_PATTERN = re.compile(r"^[-*]\s+PRD:\s*(?P<path>\S+)\s*$", re.MULTILINE)

# 聚合正文里"合并即接受整组 PRD"的确定性小节标题与声明句（发布端与测试钉住同一文案）。
_AGGREGATE_ACCEPTANCE_HEADING = "## Human Acceptance And Aggregated PRDs"
AGGREGATE_MERGE_ACCEPTANCE_STATEMENT_TEXT = (
    "Merging this aggregate PR means that the merger accepts, for every PRD listed "
    "above, the human decisions and human-visible outcomes recorded in that PRD, and "
    "authorizes post-merge acceptance recording on each listed PRD individually (its "
    "Human-Confirmed items are ticked and its banner becomes \u2705 \u5df2\u9a8c\u6536), provided the "
    "required gates remain green and the merged Git tree matches that PRD's verified "
    "tree. Closing a source PR is never, on its own, acceptance of any PRD."
)

# 聚合契约锚点标识 → 人读含义（错误信息与标注块共用，避免文案漂移）。
_AGGREGATE_CONTRACT_ANCHOR_DESCRIPTIONS: dict[str, str] = {
    "aggregate-source-marker": (
        "body is missing a valid `<!-- iar:aggregate-pr version=1 issues=... "
        "source_prs=... -->` source marker, or its Issue / source PR set does not "
        "match the batch's declared members"
    ),
    "merge-acceptance-v2": (
        "body is missing the `<!-- iar:merge-acceptance version=2 -->` "
        "merge-means-acceptance declaration for the aggregated PRD set"
    ),
    "prd-link": (
        "the set of `- PRD:` paths listed in the body is not exactly the batch's "
        "unique PRD set (a PRD is missing or an unrelated PRD is listed)"
    ),
    "prd-duplicate": "the body lists the same PRD path on more than one `- PRD:` line",
    "prd-order": "the body does not list unique PRD paths in stable lexical order",
    "prd-path": "the body contains an unsafe or non-archived PRD path",
    "merge-acceptance-statement": (
        "body is missing the explicit statement that merging accepts each listed PRD's "
        "human-visible outcomes and authorizes individual post-merge acceptance records"
    ),
}


@dataclass(frozen=True)
class AggregateSourceDeclaration:
    """解析出的聚合来源 marker 内容。

    Attributes:
        version: marker 的 ``version=`` 字段。
        issue_numbers: marker 声明的来源 Issue 编号（升序去重）。
        source_pr_numbers: marker 声明的来源 PR 编号（升序去重，可为空）。
    """

    version: int
    issue_numbers: tuple[int, ...]
    source_pr_numbers: tuple[int, ...]


def unique_prd_paths(prd_paths: Iterable[str]) -> tuple[str, ...]:
    """把 PRD 路径集合规范化为**去重且稳定排序**的元组。

    发布端用它生成正文清单，校验端用它做集合相等比较，两处共用一份定义，避免
    "排序不稳定"导致的漏列 / 重复列判定漂移。空串与首尾空白条目被丢弃。

    Args:
        prd_paths: 任意来源的 PRD 路径可迭代对象（可能含重复 / 乱序）。

    Returns:
        升序去重后的仓库相对路径元组。
    """
    normalized = {path.strip() for path in prd_paths if path and path.strip()}
    return tuple(sorted(normalized))


def _parse_marker_numbers(raw: str) -> tuple[int, ...] | None:
    """严格解析 marker 编号；空集合可用，非法、非正或重复条目返回 ``None``。"""
    if not raw:
        return ()
    tokens = raw.split(",")
    if any(not token.isdigit() or int(token) <= 0 for token in tokens):
        return None
    numbers = tuple(int(token) for token in tokens)
    if len(set(numbers)) != len(numbers):
        return None
    return tuple(sorted(numbers))


def _format_marker_numbers(numbers: Iterable[int]) -> str:
    """把编号可迭代对象规范化为升序去重的逗号串（供 marker 拼接）。"""
    return ",".join(str(number) for number in sorted({int(number) for number in numbers}))


def build_aggregate_source_marker(
    *,
    issue_numbers: Iterable[int],
    source_pr_numbers: Iterable[int] = (),
) -> str:
    """构建总 PR 的来源集合 hidden marker。

    Args:
        issue_numbers: 批次来源 Issue 编号。
        source_pr_numbers: 批次来源 PR 编号；dry-run 阶段可为空。

    Returns:
        ``<!-- iar:aggregate-pr version=1 issues=... source_prs=... -->`` 单行 marker。
    """
    issues_text = _format_marker_numbers(issue_numbers)
    source_prs_text = _format_marker_numbers(source_pr_numbers)
    return (
        f"<!-- iar:aggregate-pr version={_AGGREGATE_SOURCE_MARKER_VERSION} "
        f"issues={issues_text} source_prs={source_prs_text} -->"
    )


def parse_aggregate_source_marker(pr_body: str) -> AggregateSourceDeclaration | None:
    """解析总 PR 正文里的聚合来源 marker；缺失或不合规时返回 ``None``。

    ``version`` 非 1 视为无法识别的契约，同样返回 ``None``，让调用方 fail closed。
    """
    match = _AGGREGATE_SOURCE_MARKER_PATTERN.search(pr_body)
    if match is None:
        return None
    version = int(match.group("version"))
    if version != _AGGREGATE_SOURCE_MARKER_VERSION:
        return None
    issue_numbers = _parse_marker_numbers(match.group("issues"))
    source_pr_numbers = _parse_marker_numbers(match.group("source_prs"))
    if issue_numbers is None or not issue_numbers or source_pr_numbers is None:
        return None
    return AggregateSourceDeclaration(
        version=version,
        issue_numbers=issue_numbers,
        source_pr_numbers=source_pr_numbers,
    )


def _has_merge_acceptance_version(pr_body: str, version: int) -> bool:
    """正文是否携带指定 ``version`` 的合并即接受 marker（v1 / v2 分别判定）。"""
    return any(
        int(match.group("version")) == version
        for match in _MERGE_ACCEPTANCE_MARKER_PATTERN.finditer(pr_body)
    )


def extract_prd_reference_paths(pr_body: str) -> tuple[str, ...]:
    """按正文出现顺序抽取所有 ``- PRD: <path>`` 引用路径（不去重）。

    校验端据此同时判断"完整性"（集合相等）与"重复"（同一路径出现多次），因此保留
    文档顺序与重复项，不在这里做 :func:`unique_prd_paths` 的收敛。
    """
    return tuple(match.group("path").strip() for match in _PRD_LIST_LINE_PATTERN.finditer(pr_body))


def build_aggregate_merge_acceptance_block(prd_paths: Iterable[str]) -> str:
    """构建总 PR 的多 PRD "合并即接受"确定性小节（去重排序清单 + v2 marker + 声明）。

    发布端与确定性正文共用本函数，保证正文清单与校验端的"唯一集合"判定同源。

    Args:
        prd_paths: 批次来源 Issue 解析出的 PRD 路径（pending 或 archive 形态均可，
            调用方负责传入已在集成树里归档后的路径）。

    Returns:
        以标题起、以声明句止的小节文本（无末尾换行）。
    """
    prd_lines = [f"- PRD: {prd_path}" for prd_path in unique_prd_paths(prd_paths)]
    return "\n".join(
        [
            _AGGREGATE_ACCEPTANCE_HEADING,
            "",
            *prd_lines,
            "",
            MERGE_ACCEPTANCE_V2_MARKER_TEXT,
            AGGREGATE_MERGE_ACCEPTANCE_STATEMENT_TEXT,
        ]
    )


def find_aggregate_pr_body_contract_violations(
    pr_body: str,
    *,
    expected_prd_paths: Iterable[str],
    expected_issue_numbers: Iterable[int],
    expected_source_pr_numbers: Iterable[int] = (),
) -> list[str]:
    """返回总 PR 正文缺失 / 不匹配的 v2 契约锚点标识列表；完全合规时为空。

    校验面（全部本地字符串断言，不发布 PR、不读 GitHub）：

    - ``aggregate-source-marker``：正文缺少合规的来源 marker，或其 Issue / 来源 PR
      集合与批次声明的成员不一致。
    - ``merge-acceptance-v2``：正文缺少 ``version=2`` 的合并即接受 marker（v1 单
      PRD marker 不算）。
    - ``prd-link``：正文列出的 ``- PRD:`` 路径集合不等于批次唯一 PRD 集合（漏列或
      多列）。
    - ``prd-duplicate``：正文把同一 PRD 路径列了多于一行。

    Args:
        pr_body: 总 PR 正文。
        expected_prd_paths: 批次来源 Issue 的唯一 PRD 路径集合（权威源，由调用方
            从 GitHub Issue / 集成树解析得到）。
        expected_issue_numbers: 批次来源 Issue 编号集合。
        expected_source_pr_numbers: 批次来源 PR 编号集合；dry-run 预览可为空。

    Returns:
        缺失 / 不匹配的锚点标识列表（顺序稳定：marker 类 → PRD 集合类）。
    """
    violations: list[str] = []
    declaration = parse_aggregate_source_marker(pr_body)
    expected_issues = tuple(sorted({int(number) for number in expected_issue_numbers}))
    expected_prs = tuple(sorted({int(number) for number in expected_source_pr_numbers}))
    if (
        declaration is None
        or declaration.issue_numbers != expected_issues
        or declaration.source_pr_numbers != expected_prs
    ):
        violations.append("aggregate-source-marker")
    if not _has_merge_acceptance_version(pr_body, 2):
        violations.append("merge-acceptance-v2")
    if AGGREGATE_MERGE_ACCEPTANCE_STATEMENT_TEXT not in pr_body:
        violations.append("merge-acceptance-statement")
    listed_paths = extract_prd_reference_paths(pr_body)
    if any(count > 1 for count in Counter(listed_paths).values()):
        violations.append("prd-duplicate")
    expected_paths = unique_prd_paths(expected_prd_paths)
    all_prd_paths = (*listed_paths, *expected_paths)
    if any(
        Path(prd_path).is_absolute()
        or "\\" in prd_path
        or ".." in Path(prd_path).parts
        or Path(prd_path).suffix.lower() != ".md"
        or Path(prd_path).parts[:2] != ("tasks", "archive")
        for prd_path in all_prd_paths
    ):
        violations.append("prd-path")
    if set(listed_paths) != set(expected_paths):
        violations.append("prd-link")
    elif listed_paths != expected_paths:
        violations.append("prd-order")
    return violations


def build_aggregate_contract_annotation_block(violations: list[str]) -> str:
    """构建追加到总 PR 正文末尾的 v2 不合规标注块（与 v1 标注块同型、独立 marker）。

    合并队列硬门据此拒绝自动合并被 runner 自报为聚合契约不合规的总 PR。
    """
    missing_text = ",".join(violations)
    violation_lines = [
        f"- `{anchor}`: " f"{_AGGREGATE_CONTRACT_ANCHOR_DESCRIPTIONS.get(anchor, 'unknown anchor')}"
        for anchor in violations
    ]
    return "\n".join(
        [
            f"<!-- iar:aggregate-contract version=1 missing={missing_text} -->",
            "## Aggregate PR Body Contract Violation (auto-generated)",
            "",
            "This aggregate PR body does not satisfy the multi-PRD acceptance "
            "contract, so the autopilot merge queue will refuse to auto-merge "
            "it. Missing or mismatched anchors:",
            "",
            *violation_lines,
            "",
            "<!-- iar:aggregate-contract-end -->",
        ]
    )
