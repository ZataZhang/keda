"""Tests for the loop recipe parser and template renderer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from backend.core.use_cases.loop_recipe import (
    parse_loop_recipe,
    parse_loop_recipe_text,
    parse_pre_command_output,
    render_loop_recipe,
    render_loop_recipe_title,
)


def _write_recipe(tmp_path: Path, body: str = "## Summary\n\nBody {{date}}") -> Path:
    recipe_path = tmp_path / "test-loop.md"
    recipe_path.write_text(body, encoding="utf-8")
    return recipe_path


def test_parse_loop_recipe_minimal(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: github-trending
schedule: 0 8 * * *
repo_id: keda-main
---

# GitHub Trending {{date}}
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    assert recipe.id == "github-trending"
    assert recipe.schedule.expression == "0 8 * * *"
    assert recipe.schedule.kind.value == "cron"
    assert recipe.repo_id == "keda-main"
    assert "# GitHub Trending" in recipe.body_template
    assert recipe.issue_type == "feature"
    assert recipe.agent == "auto"
    assert recipe.publish_prd is True
    assert recipe.queue_ready is True
    assert recipe.run_now is False
    assert recipe.managed_by == "loop-daemon"
    assert recipe.default_labels() == ("loop/github-trending",)


def test_parse_loop_recipe_with_labels_and_interval(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: hourly
schedule: 30m
repo_id: keda-main
issue_type: refactor
agent: codex
labels:
  - area/docs
  - priority/high
publish_prd: false
queue_ready: false
run_now: true
priority: P1
slug: hourly-trending
pre_command: echo "ready=ok"
timezone: UTC
managed_by: agent-daemon
---

Body
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    assert recipe.schedule.kind.value == "interval"
    assert recipe.schedule.expression == "30m"
    assert recipe.issue_type == "refactor"
    assert recipe.agent == "codex"
    assert recipe.labels == ("area/docs", "priority/high")
    assert recipe.publish_prd is False
    assert recipe.queue_ready is False
    assert recipe.run_now is True
    assert recipe.priority == "P1"
    assert recipe.slug == "hourly-trending"
    assert recipe.pre_command == 'echo "ready=ok"'
    assert recipe.timezone_name == "UTC"
    assert recipe.managed_by == "agent-daemon"
    assert recipe.effective_slug() == "hourly-trending"
    assert recipe.all_labels() == ("loop/hourly", "area/docs", "priority/high")


def test_parse_loop_recipe_comma_separated_labels(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: csv
schedule: 1h
repo_id: keda-main
labels: "a, b, c"
---

Body
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    assert recipe.labels == ("a", "b", "c")


def test_parse_loop_recipe_missing_field_raises(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: missing-schedule
repo_id: keda-main
---
""",
    )
    with pytest.raises(ValueError, match="schedule"):
        parse_loop_recipe(recipe_path)


def test_parse_loop_recipe_invalid_id_raises(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: "Not_Kebab"
schedule: 0 8 * * *
repo_id: keda-main
---
""",
    )
    with pytest.raises(ValueError, match="kebab-case"):
        parse_loop_recipe(recipe_path)


def test_parse_loop_recipe_invalid_issue_type_raises(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: typo
schedule: 0 8 * * *
repo_id: keda-main
issue_type: typo
---
""",
    )
    with pytest.raises(ValueError, match="issue_type"):
        parse_loop_recipe(recipe_path)


def test_parse_loop_recipe_invalid_manager_raises(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: invalid-manager
schedule: 0 8 * * *
repo_id: keda-main
managed_by: unknown-daemon
---
""",
    )
    with pytest.raises(ValueError, match="managed_by"):
        parse_loop_recipe(recipe_path)


def test_parse_loop_recipe_invalid_schedule_raises(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: bad
schedule: "every 5 minutes"
repo_id: keda-main
---
""",
    )
    with pytest.raises(ValueError, match="Unsupported loop schedule"):
        parse_loop_recipe(recipe_path)


def test_parse_loop_recipe_no_frontmatter(tmp_path: Path) -> None:
    recipe_path = _write_recipe(tmp_path, "# Just a title\n\nbody")
    with pytest.raises(ValueError, match="missing required field"):
        parse_loop_recipe(recipe_path)


def test_render_loop_recipe_built_in_variables(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: render
schedule: 0 8 * * *
repo_id: keda-main
---
# Loop {{loop_id}} for {{repo_id}} on {{date}} at {{timestamp}}
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    fire_at = datetime(2026, 6, 23, 8, 0, 0)
    rendered = render_loop_recipe(recipe, fire_at=fire_at)
    assert "Loop render for keda-main on 2026-06-23 at 20260623-080000" in rendered


def test_render_loop_recipe_extra_variables(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: ext
schedule: 0 8 * * *
repo_id: keda-main
---
Trending repo: {{repo}} (count={{count}})
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    rendered = render_loop_recipe(
        recipe,
        fire_at=datetime(2026, 1, 1, 0, 0, 0),
        extra_variables={"repo": "demo/demo", "count": "5"},
    )
    assert "Trending repo: demo/demo (count=5)" in rendered


def test_render_loop_recipe_undefined_variable_raises(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: undef
schedule: 0 8 * * *
repo_id: keda-main
---
{{missing}}
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    with pytest.raises(ValueError, match="undefined variable"):
        render_loop_recipe(recipe, fire_at=datetime(2026, 1, 1, 0, 0, 0))


def test_render_loop_recipe_title_extracts_h1(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: titled
schedule: 0 8 * * *
repo_id: keda-main
---
# Daily Report {{date}}

rest
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    title = render_loop_recipe_title(recipe, fire_at=datetime(2026, 6, 23, 0, 0, 0))
    assert title == "Daily Report 2026-06-23"


def test_render_loop_recipe_title_falls_back_to_id(tmp_path: Path) -> None:
    recipe_path = _write_recipe(
        tmp_path,
        """---
id: fallback
schedule: 0 8 * * *
repo_id: keda-main
---
No h1 line here.
""",
    )
    recipe = parse_loop_recipe(recipe_path)
    assert render_loop_recipe_title(recipe, fire_at=datetime(2026, 1, 1, 0, 0, 0)) == "fallback"


def test_parse_pre_command_output_handles_quotes_and_comments() -> None:
    stdout = """
# pre-command output
greeting = "hello world"
count = 3
name=alice
not_a_kv_line
flag = true
"""
    parsed = parse_pre_command_output(stdout)
    assert parsed == {
        "greeting": "hello world",
        "count": "3",
        "name": "alice",
        "flag": "true",
    }


def test_parse_loop_recipe_text_requires_source_path(tmp_path: Path) -> None:
    recipe = parse_loop_recipe_text(
        """---
id: t
schedule: 0 8 * * *
repo_id: keda-main
---
Body
""",
        source_path=tmp_path / "t.md",
    )
    assert recipe.source_path == tmp_path / "t.md"


def test_recipe_skip_when_is_parsed_and_validated(tmp_path: Path) -> None:
    """skip_when 形如 KEY=v1,v2；格式不对在解析期报错。"""
    from backend.core.use_cases.loop_recipe import parse_loop_recipe_text

    base_text = '---\nid: demo\nschedule: "0 1 * * *"\nrepo_id: r\n{extra}---\n\nbody\n'
    recipe = parse_loop_recipe_text(
        base_text.format(extra="skip_when: last_ci_status=success,none\n"),
        source_path=tmp_path / "demo.md",
    )
    assert recipe.skip_when == "last_ci_status=success,none"
    with pytest.raises(ValueError, match="skip_when"):
        parse_loop_recipe_text(
            base_text.format(extra="skip_when: nonsense\n"), source_path=tmp_path / "demo.md"
        )


def test_recipe_output_defaults_to_issue_and_rejects_unknown_values(tmp_path: Path) -> None:
    from backend.core.use_cases.loop_recipe import parse_loop_recipe_text

    base_text = '---\nid: demo\nschedule: "0 1 * * *"\nrepo_id: r\n{extra}---\n\nbody\n'
    default_recipe = parse_loop_recipe_text(
        base_text.format(extra=""), source_path=tmp_path / "d.md"
    )
    assert default_recipe.output == "issue"  # 新配方默认不生成 PRD
    prd_recipe = parse_loop_recipe_text(
        base_text.format(extra="output: prd\n"), source_path=tmp_path / "d.md"
    )
    assert prd_recipe.output == "prd"
    with pytest.raises(ValueError, match="output"):
        parse_loop_recipe_text(
            base_text.format(extra="output: markdown\n"), source_path=tmp_path / "d.md"
        )


def test_recipe_examples_in_the_loop_guide_all_parse(tmp_path: Path) -> None:
    """docs/guides/loop.md 里的每个配方示例都必须能被真实解析器解析。

    配方的简易 YAML 解析器只忽略**整行**注释，行内 ``# ...`` 会被当成值的一部分
    （``schedule`` 会直接解析失败）。示例是给人照抄的，不能带这种写法。
    """
    import re

    from backend.core.use_cases.loop_recipe import parse_loop_recipe_text

    guide_text = (Path(__file__).resolve().parents[1] / "docs" / "guides" / "loop.md").read_text(
        encoding="utf-8"
    )
    recipe_blocks = re.findall(
        r"```(?:markdown|yaml)\n(---\nid:.*?\n---\n.*?)```", guide_text, re.S
    )
    assert recipe_blocks, "没有在文档里找到配方示例"
    for recipe_block in recipe_blocks:
        # 文档里的 <占位符> 换成可解析的实值。
        concrete_text = re.sub(r"<[^>\n]+>", "x", recipe_block)
        parse_loop_recipe_text(concrete_text, source_path=tmp_path / "example.md")


@pytest.mark.parametrize(
    ("scalar_text", "expected_value"),
    [
        (r'"print \"x=\" $0"', 'print "x=" $0'),
        (r'"a\\b"', r"a\b"),
        (r'"keep \q as-is"', r"keep \q as-is"),
        ("'it''s'", "it's"),
        (r"'no \" escape'", r"no \" escape"),
    ],
)
def test_quoted_scalars_follow_yaml_escape_rules(scalar_text: str, expected_value: str) -> None:
    """双引号按 YAML 还原转义（未知序列原样保留），单引号只把 ``''`` 还原成 ``'``。"""
    from backend.core.use_cases.loop_recipe import _coerce_yaml_value

    assert _coerce_yaml_value(scalar_text) == expected_value


def test_pre_command_escaped_quotes_reach_the_shell_unescaped(tmp_path: Path) -> None:
    """``pre_command`` 里的 ``\\"`` 交给 shell 前必须还原成 ``"``（nightly-cleanup 回归）。"""
    recipe_path = tmp_path / "nightly.md"
    recipe_path.write_text(
        """---
id: nightly
schedule: "0 2 * * *"
repo_id: demo
pre_command: "printf ok | awk '{ print \\"status=\\" $0 }'"
---

Body {{status}}
""",
        encoding="utf-8",
    )

    recipe = parse_loop_recipe(recipe_path)

    assert recipe.pre_command == "printf ok | awk '{ print \"status=\" $0 }'"


def test_repository_loop_recipes_parse_without_leftover_escapes() -> None:
    """仓库内每个 loop 文件解析后，字符串字段里不得残留 ``\\"``。

    nightly-cleanup 的 ``pre_command`` 曾因解析器不处理转义，把 ``\\"`` 原样交给
    shell，自 2026-10-21 起每次触发都失败；新增或修改 loop 文件时由本测试兜住。
    """
    recipes_dir = Path(__file__).resolve().parents[1] / "tasks" / "loops"
    recipe_paths = sorted(recipes_dir.glob("*.md"))
    assert {"nightly-cleanup-keda.md", "nightly-cleanup-product.md"} <= {
        recipe_path.name for recipe_path in recipe_paths
    }

    for recipe_path in recipe_paths:
        recipe = parse_loop_recipe(recipe_path)
        string_fields = {
            field_name: field_value
            for field_name, field_value in vars(recipe).items()
            if isinstance(field_value, str) and field_name != "body_template"
        }
        leftover_fields = [name for name, value in string_fields.items() if '\\"' in value]
        assert leftover_fields == [], f"{recipe_path.name}: {leftover_fields}"
