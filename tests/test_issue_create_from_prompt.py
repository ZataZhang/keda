"""``iar issue create --from-prompt`` 行为测试。

覆盖 PRD P1-FEAT-20261006-122336 的 FR-3 ~ FR-13：需求来源互斥校验、产物不含
PRD 指涉、工作区零 diff、验收段的确定性开关、agent 失败回退、标签与依赖在两种
输入方式下的一致行为。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.api.cli import main
from backend.api.cli_exit_codes import ExitCode
from backend.core.shared.models.agent_runner import (
    AppConfig,
    GeneratedContentConfig,
    GeneratedContentTargetConfig,
    LabelConfig,
)
from backend.core.use_cases.agent_runner_dependencies import parse_dependency_marker
from backend.core.use_cases.agent_runner_validation_parsing import validation_required
from backend.core.use_cases.create_issue_from_prompt import (
    IssueFromPromptRequest,
    build_prompt_fallback_body,
    create_issue_from_prompt,
    derive_prompt_title,
    strip_validation_section,
)
from backend.core.use_cases.generated_issue_prompt_content import (
    build_issue_prompt_context,
    contains_prd_reference,
    generate_issue_prompt_content,
)
from tests.conftest import FailingContentGenerator, FakeContentGenerator, FakeGitHubClient

PROMPT = "Stats 页 Token 用量表的数值列改成右对齐"

#: 与 config.toml 的 issue_from_prompt 同形的最小目标配置：提示词只要求 JSON，
#: 具体正文由被测试的 agent 假件给出。core 侧的模板是 ``str``（TOML 里的 list
#: 在配置加载层就被 join），所以这里直接写字符串。
_TARGET_PROMPT = (
    "Turn this requirement into a self-sufficient Issue. Return strict JSON with "
    "keys: title, body. {validation_directive} {issue_type} {title} {prompt_text}"
)

_TARGET_BODY = "\n".join(["## Requirement", "", "{prompt_text}", ""])


def _generated_config(**target_overrides: object) -> GeneratedContentConfig:
    """构造启用 ``issue_from_prompt`` 目标的生成配置。"""
    target_fields: dict[str, object] = {
        "prompt": _TARGET_PROMPT,
        "title_template": "{title}",
        "body_template": _TARGET_BODY,
    }
    target_fields.update(target_overrides)
    return GeneratedContentConfig(
        issue_from_prompt=GeneratedContentTargetConfig(**target_fields)  # type: ignore[arg-type]
    )


def _agent_returns(title: str, body: str) -> FakeContentGenerator:
    """返回会给出指定 JSON 的内容生成器。"""
    return FakeContentGenerator(response=json.dumps({"title": title, "body": body}))


def _request(**overrides: object) -> IssueFromPromptRequest:
    """构造一个最小可用的 --from-prompt 请求。"""
    fields: dict[str, object] = {
        "repo_path": Path("/tmp/repo"),
        "prompt_text": PROMPT,
        "issue_type": "feature",
        "generated_content_config": _generated_config(),
    }
    fields.update(overrides)
    return IssueFromPromptRequest(**fields)  # type: ignore[arg-type]


def _created_issue(fake_client: FakeGitHubClient) -> dict[str, object]:
    """取回 FakeGitHubClient 记录的 create_issue 调用。"""
    create_calls = [call for call in fake_client.calls if call["method"] == "create_issue"]
    assert len(create_calls) == 1
    return create_calls[0]


# ---------------------------------------------------------------------------
# FR-4：产物不指涉 PRD，且不碰工作区
# ---------------------------------------------------------------------------


def test_from_prompt_body_has_no_prd_reference() -> None:
    """生成的正文不含 ``PRD path:`` 锚点也不含 ``## Canonical PRD`` 小节。"""
    fake_client = FakeGitHubClient()
    issue_url = create_issue_from_prompt(
        request=_request(generated_content_config=_generated_config()),
        github_client=fake_client,
    )

    assert issue_url == fake_client._issue_url  # noqa: SLF001
    body = _created_issue(fake_client)["body"]
    assert contains_prd_reference(str(body)) is False
    assert "PRD path:" not in str(body)
    assert PROMPT in str(body)


def test_from_prompt_does_not_touch_the_workspace(tmp_path: Path) -> None:
    """--from-prompt 不写任何本地文件：需求来源是命令行文本，产物只在远端。"""
    (tmp_path / "tasks" / "pending").mkdir(parents=True)
    before = sorted(path.name for path in tmp_path.rglob("*"))

    create_issue_from_prompt(
        request=_request(repo_path=tmp_path),
        github_client=FakeGitHubClient(),
    )

    assert sorted(path.name for path in tmp_path.rglob("*")) == before


def test_agent_produced_prd_anchor_is_rejected(tmp_path: Path) -> None:
    """agent 自作主张写回 PRD 锚点时产物整份作废，退回无锚点的确定性正文。

    这是 rv-2 的负控制：若校验方向写反（像 ``issue_from_prd`` 那样要求锚点存在），
    本条会失败。
    """
    anchored = f"## Requirement\n\n{PROMPT}\n\n- PRD path: `tasks/pending/ghost.md`\n"
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(repo_path=tmp_path),
        github_client=fake_client,
        content_generator=_agent_returns("带锚点的标题", anchored),
    )

    body = str(_created_issue(fake_client)["body"])
    assert contains_prd_reference(body) is False
    assert "ghost.md" not in body


def test_failing_generator_still_creates_usable_issue() -> None:
    """FR-12：内容生成失败时回退确定性模板，仍能建出自带需求原文的 Issue。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(repo_path=Path("/tmp/repo")),
        github_client=fake_client,
        content_generator=FailingContentGenerator(FileNotFoundError("claude")),
    )

    call = _created_issue(fake_client)
    assert PROMPT in str(call["body"])
    assert str(call["title"]).startswith("[Feature] ")


def test_disabled_generated_content_uses_fallback_body() -> None:
    """生成配置未启用时直接使用确定性正文，不调用 agent。"""
    fake_client = FakeGitHubClient()
    generator = _agent_returns("不该出现", "不该出现")

    create_issue_from_prompt(
        request=_request(generated_content_config=GeneratedContentConfig(enabled=False)),
        github_client=fake_client,
        content_generator=generator,
    )

    assert generator.prompts == []
    assert PROMPT in str(_created_issue(fake_client)["body"])


# ---------------------------------------------------------------------------
# FR-9：验收段的确定性开关
# ---------------------------------------------------------------------------


def test_default_path_leaves_evidence_gate_off() -> None:
    """默认路径下 ``validation_required`` 为 False —— 不要求任何证据文件。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(request=_request(), github_client=fake_client)

    body = str(_created_issue(fake_client)["body"])
    assert validation_required(body, AppConfig()) is False


def test_agent_written_validation_section_is_stripped_by_default() -> None:
    """agent 自己写了验收段也不会把门禁打开：小节由旗标决定，不由 agent 决定。"""
    body_with_validation = (
        f"## Requirement\n\n{PROMPT}\n\n" "## Realistic Validation\n\n- [ ] rv-1: 截图对比\n"
    )
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(),
        github_client=fake_client,
        content_generator=_agent_returns("标题", body_with_validation),
    )

    created_body = str(_created_issue(fake_client)["body"])
    assert validation_required(created_body, AppConfig()) is False
    assert "Realistic Validation" not in created_body


def test_require_validation_materializes_section_from_agent_items() -> None:
    """``--require-validation`` 时门禁确定开启，条目取 agent 写的清单。"""
    agent_body = (
        f"## Requirement\n\n{PROMPT}\n\n"
        "## Realistic Validation\n\n- [ ] rv-1: 真实入口核对列对齐\n"
        "- [ ] rv-2: 暗色主题下复核\n"
    )
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(require_validation=True),
        github_client=fake_client,
        content_generator=_agent_returns("标题", agent_body),
    )

    created_body = str(_created_issue(fake_client)["body"])
    assert validation_required(created_body, AppConfig()) is True
    assert "rv-2: 暗色主题下复核" in created_body


def test_require_validation_without_agent_items_falls_back_to_one_item() -> None:
    """agent 没写清单时由代码兜一条，门禁仍开启（不依赖 agent 是否配合）。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(require_validation=True),
        github_client=fake_client,
        content_generator=_agent_returns("标题", f"## Requirement\n\n{PROMPT}\n"),
    )

    created_body = str(_created_issue(fake_client)["body"])
    assert validation_required(created_body, AppConfig()) is True
    assert PROMPT in created_body


def test_strip_validation_section_keeps_sibling_sections() -> None:
    """只剥离该小节：同级标题之后的内容必须留下。"""
    text = (
        "## A\n\nkeep me\n\n## Realistic Validation\n\n- [ ] gone\n\n"
        "### 子标题也 gone\n\n## B\n\nkeep me too\n"
    )

    stripped = strip_validation_section(text)

    assert "keep me" in stripped
    assert "keep me too" in stripped
    assert "gone" not in stripped


def test_strip_validation_section_is_fenced_code_aware() -> None:
    """围栏代码块内的 ``#`` 行是注释不是标题：不触发剥离，且围栏保持成对闭合。"""
    body = (
        "## Requirement\n\n保持我。\n\n"
        "```bash\n# 部署前先跑迁移\nuv run alembic upgrade head\n```\n\n"
        "## Realistic Validation\n\n- [ ] rv-1: 核对右对齐\n\n"
        "```bash\n# 验证命令\njust test\n```\n\n"
        "## Delivery Notes\n\n交接说明保留。\n"
    )

    stripped = strip_validation_section(body)

    # 验收段连同其中的围栏块一起移除，后面的同级小节存活。
    assert "Realistic Validation" not in stripped
    assert "验证命令" not in stripped
    assert "## Delivery Notes" in stripped
    assert "交接说明保留" in stripped
    # 需求段围栏内的 # 注释不被误判为标题，原样存活。
    assert "# 部署前先跑迁移" in stripped
    assert stripped.count("```") % 2 == 0


# ---------------------------------------------------------------------------
# FR-10：与 PRD 路径一致的标签与依赖行为
# ---------------------------------------------------------------------------


def test_labels_match_prd_path_routing_rules() -> None:
    """标签集合与 PRD 路径共用同一装配：type + backlog，``--ready`` 才进队列。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(queue_ready=True, issue_agent="claude", labels_config=LabelConfig()),
        github_client=fake_client,
    )

    labels = _created_issue(fake_client)["labels"]
    assert labels == ["type/feature", "status/backlog", "agent/ready", "agent/claude"]
    assert "source/prd" not in labels  # 无 PRD 的 Issue 不打 PRD 来源标签


def test_no_ready_label_by_default() -> None:
    """默认不进守护进程队列。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(labels_config=LabelConfig()), github_client=fake_client
    )

    labels = _created_issue(fake_client)["labels"]
    assert "agent/ready" not in labels


def test_unknown_agent_is_rejected() -> None:
    """agent 路由键非法时报错，与 PRD 路径同一判据。"""
    with pytest.raises(ValueError, match="issue_agent must be one of"):
        create_issue_from_prompt(
            request=_request(issue_agent="nope"), github_client=FakeGitHubClient()
        )


def test_empty_prompt_is_rejected() -> None:
    """空需求文本没有可执行内容，直接拒绝。"""
    with pytest.raises(ValueError, match="non-empty"):
        create_issue_from_prompt(
            request=_request(prompt_text="   "), github_client=FakeGitHubClient()
        )


def test_title_override_wins_over_generated_title() -> None:
    """``--title`` 覆盖生成标题与回退标题。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(
        request=_request(title_override="显式标题"),
        github_client=fake_client,
        content_generator=_agent_returns("agent 标题", f"## Requirement\n\n{PROMPT}\n"),
    )

    assert _created_issue(fake_client)["title"] == "显式标题"


def test_depends_on_marker_is_materialized() -> None:
    """``--depends-on`` 在无 PRD 路径下同样物化为机器可读 marker。"""
    fake_client = FakeGitHubClient()

    create_issue_from_prompt(request=_request(depends_on=(11, 12)), github_client=fake_client)

    body = str(_created_issue(fake_client)["body"])
    marker = parse_dependency_marker(body)
    assert marker is not None
    assert marker.issue_numbers == (11, 12)


def test_derive_prompt_title_uses_first_non_empty_line() -> None:
    """回退标题取首个非空行并压平，超长时截断。"""
    assert derive_prompt_title("\n\n  第二行才是需求  \n尾行") == "第二行才是需求"
    assert len(derive_prompt_title("很" * 200)) == 80


def test_fallback_body_is_self_sufficient() -> None:
    """回退正文逐字保留需求原文并说明无 PRD。"""
    body = build_prompt_fallback_body("第一行\n第二行细节")

    assert "第一行\n第二行细节" in body
    assert contains_prd_reference(body) is False


# ---------------------------------------------------------------------------
# 生成器级联（R1 判据）
# ---------------------------------------------------------------------------


def test_generate_cascade_falls_back_to_template() -> None:
    """agent 输出不可解析时按配置回退 template 渲染。"""
    config = _generated_config()
    context = build_issue_prompt_context(
        issue_type="feature",
        prompt_text=PROMPT,
        fallback_title="回退标题",
        require_validation=False,
    )

    result = generate_issue_prompt_content(
        config=config,
        context=context,
        fallback_title="回退标题",
        fallback_body="回退正文",
        generator=FakeContentGenerator(response="not json"),
        cwd=Path("/tmp/repo"),
    )

    assert result.source == "template"
    assert PROMPT in result.body


def test_generate_cascade_disabled_target_returns_fallback() -> None:
    """目标关闭时不跑 agent，直接用调用方的 fallback。"""
    config = _generated_config()
    config = GeneratedContentConfig(
        issue_from_prompt=GeneratedContentTargetConfig(enabled=False, prompt=_TARGET_PROMPT)
    )
    context = build_issue_prompt_context(
        issue_type="feature", prompt_text=PROMPT, fallback_title="t", require_validation=False
    )

    result = generate_issue_prompt_content(
        config=config,
        context=context,
        fallback_title="回退标题",
        fallback_body="回退正文",
        generator=_agent_returns("不该用", "不该用"),
        cwd=Path("/tmp/repo"),
    )

    assert (result.title, result.body, result.source) == ("回退标题", "回退正文", "fallback")


def test_require_validation_directive_reaches_the_prompt() -> None:
    """要求验收时提示词里带上写验收段的指令（点名门禁实际读取的小节标题）。"""
    context = build_issue_prompt_context(
        issue_type="feature",
        prompt_text=PROMPT,
        fallback_title="t",
        require_validation=True,
    )

    assert "## Realistic Validation" in context.validation_directive
    assert PROMPT in _TARGET_PROMPT.format(**context.__dict__)


def test_require_validation_directive_names_realistic_validation_section() -> None:
    """提示词点名 ``## Realistic Validation``（证据门禁读的就是这个标题），默认态为空指令。"""
    context = build_issue_prompt_context(
        issue_type="feature",
        prompt_text=PROMPT,
        fallback_title="t",
        require_validation=True,
    )

    assert "## Realistic Validation" in context.validation_directive
    assert "Acceptance Criteria" not in context.validation_directive

    default_context = build_issue_prompt_context(
        issue_type="feature",
        prompt_text=PROMPT,
        fallback_title="t",
        require_validation=False,
    )
    assert default_context.validation_directive == ""


# ---------------------------------------------------------------------------
# CLI 表面：来源互斥与矛盾旗标（FR-3 / FR-7 / FR-8）
# ---------------------------------------------------------------------------


def _patched_issue_create(**overrides: object):
    """把 ``iar issue create`` 的外部依赖换成假件（避免真实 gh 调用）。"""
    mock_context = MagicMock()
    mock_context.repo_path = Path.cwd()
    mock_context.config.labels = LabelConfig()
    mock_context.config.git.remote = "origin"
    mock_context.config.git.base_branch = "main"
    mock_context.config.generated_content = overrides.get(
        "generated_content", GeneratedContentConfig(enabled=False)
    )
    mock_context.config.validation.language = "zh-CN"
    mock_context.config.validation.structured_evidence = True
    mock_context.config.validation.evidence_dir = "tasks/evidence"
    return (
        patch("backend.api.cli.resolve_issue_from_prd_target", return_value=mock_context),
        patch("backend.api.cli_helpers.create_github_client"),
        patch("backend.api.cli.create_github_client"),
        patch("backend.api.cli.require_iar_repository_initialized"),
        patch("backend.api.cli._ensure_gh_auth_or_prompt"),
    )


def _run_issue_create(argv: list[str]) -> int:
    """在假件下执行 ``iar issue create``，返回退出码。"""
    patches = _patched_issue_create()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        return main(argv)


def test_cli_neither_source_is_usage_error() -> None:
    """PRD 路径与 ``--from-prompt`` 都不给 → 用法错误（退出码 2）。"""
    assert _run_issue_create(["issue", "create"]) == int(ExitCode.USAGE)


def test_cli_both_sources_are_usage_error() -> None:
    """同时给出两种需求来源 → 用法错误，不静默偏袒任一边。"""
    assert _run_issue_create(["issue", "create", "tasks/a.md", "--from-prompt", "一句话"]) == int(
        ExitCode.USAGE
    )


def test_cli_from_prompt_with_publish_prd_is_usage_error() -> None:
    """FR-8：``--from-prompt`` 下显式 ``--publish-prd`` / ``--no-publish-prd`` 都拒绝。"""
    for flag in ("--publish-prd", "--no-publish-prd"):
        assert _run_issue_create(["issue", "create", "--from-prompt", PROMPT, flag]) == int(
            ExitCode.USAGE
        )


def test_cli_from_prompt_with_force_is_usage_error() -> None:
    """FR-8：``--from-prompt`` 下显式 ``--force`` 同样拒绝。"""
    assert _run_issue_create(["issue", "create", "--from-prompt", PROMPT, "--force"]) == int(
        ExitCode.USAGE
    )


def test_cli_require_validation_without_from_prompt_is_usage_error() -> None:
    """``--require-validation`` 只对 ``--from-prompt`` 有意义（PRD 自己带清单）。"""
    assert _run_issue_create(["issue", "create", "tasks/a.md", "--require-validation"]) == int(
        ExitCode.USAGE
    )


def test_empty_from_prompt_is_usage_error() -> None:
    """空需求文本（含纯空白）是用法错误：没有可执行内容，也不落到 agent。"""
    for empty_prompt in ("", "   "):
        assert _run_issue_create(["issue", "create", "--from-prompt", empty_prompt]) == int(
            ExitCode.USAGE
        )


def test_cli_from_prompt_calls_the_prompt_use_case() -> None:
    """CLI 把 ``--from-prompt`` 接到无 PRD 用例上，并透传旗标。"""
    with patch(
        "backend.api.cli.create_issue_from_prompt", return_value="https://x/issues/9"
    ) as mock:
        exit_code = _run_issue_create(
            ["issue", "create", "--from-prompt", PROMPT, "--ready", "--require-validation"]
        )

    assert exit_code == 0
    request = mock.call_args.kwargs["request"]
    assert request.prompt_text == PROMPT
    assert request.queue_ready is True
    assert request.require_validation is True
    # 无 PRD 路径不应触碰 PRD 用例。
    with patch("backend.api.cli.create_issue_from_prd") as prd_case:
        _run_issue_create(["issue", "create", "--from-prompt", PROMPT])
        prd_case.assert_not_called()


def test_cli_prd_path_still_routes_to_prd_use_case() -> None:
    """FR-7：既有 PRD 路径仍走原用例，``--publish-prd`` 未给出时按默认开启。"""
    with patch("backend.api.cli.create_issue_from_prd", return_value="https://x/issues/9") as mock:
        with patch("backend.api.cli._expand_prd_paths", return_value=(["tasks/a.md"], [])):
            exit_code = _run_issue_create(["issue", "create", "tasks/a.md"])

    assert exit_code == 0
    request = mock.call_args.kwargs["request"]
    assert request.publish_prd is True
    assert request.force is False


def test_cli_parser_exposes_new_flags() -> None:
    """argparse 表面：两个新旗标可解析，位置参数变为可选。"""
    from backend.api.cli_parser import build_parser

    parsed = build_parser().parse_args(["issue", "create", "--from-prompt", PROMPT])

    assert parsed.prd_paths == []
    assert parsed.from_prompt == PROMPT
    assert parsed.require_validation is False
