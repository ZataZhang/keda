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
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.core.use_cases.agent_runner_feedback import extract_prd_path
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
_DETERMINISTIC_ACCEPTANCE_HEADING = "## Human Acceptance And PRD Archive"
_DETERMINISTIC_ACCEPTANCE_STATEMENT = (
    "Merging this PR means that the merger accepts the human decisions and "
    "human-visible outcomes recorded in the linked PRD and authorizes post-merge "
    "archival of that PRD, provided the required gates remain green and the "
    "merged Git tree matches the verified tree."
)

# skill 仓库内发布契约参考文档相对 SKILL.md 的位置。
_PUBLISH_CONTRACT_REFERENCE_RELPATH = Path("references") / "pr-evidence-and-merge-acceptance.md"

# 锚点标识 → 人读含义（标注块与错误信息共用，避免两处文案漂移）。
_CONTRACT_ANCHOR_DESCRIPTIONS: dict[str, str] = {
    "prd-link": "body does not reference the Issue's pending PRD path",
    "merge-acceptance": (
        "body is missing the `<!-- iar:merge-acceptance version=1 -->` "
        "merge-means-acceptance declaration"
    ),
}


def load_prd_publish_contract(explicit_skill_path: Path | None = None) -> str | None:
    """读取 prd skill 的发布契约参考文本；不可达时安全返回 ``None``。

    解析逻辑复用 :func:`resolve_prd_skill_path`（``IAR_PRD_SKILL_PATH`` /
    ``IAR_SKILLS_DIR`` 覆盖，且优先取 keda 自有 ``~/.iar/skills``），在其
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
            "pending PRD referenced by the Issue (copy the exact path from the "
            "Issue body's `PRD path:` anchor).",
            "2. Include the hidden marker `" + MERGE_ACCEPTANCE_MARKER_TEXT + "` "
            "in the body, immediately followed by a sentence stating that "
            "merging this PR constitutes acceptance of the listed human "
            "decisions and visible outcomes and authorizes post-merge archival.",
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

    - ``prd-link``：正文未引用 Issue 指向的 pending PRD 路径。
    - ``merge-acceptance``：正文缺少 "合并即接受" hidden marker。
    """
    prd_relative_path = extract_prd_path(issue_body)
    if prd_relative_path is None:
        return []
    violations: list[str] = []
    if prd_relative_path not in pr_body:
        violations.append("prd-link")
    if not _MERGE_ACCEPTANCE_MARKER_PATTERN.search(pr_body):
        violations.append("merge-acceptance")
    return violations


def append_missing_contract_anchors(pr_body: str, issue_body: str) -> str:
    """为 runner 自己拼装的确定性正文补齐缺失的契约锚点。

    fallback body 与 ``body_template`` 渲染结果由代码而非 agent 产出，缺锚点
    只能由代码补。只补 PRD 引用与"合并即接受"声明（marker + 指向 PRD 的声明句），
    不内联 PRD 的决策与可见结果清单，避免确定性正文冒充 agent 撰写的证据呈现。

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
        anchor_section_lines.extend([f"- PRD: {prd_relative_path}", ""])
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
