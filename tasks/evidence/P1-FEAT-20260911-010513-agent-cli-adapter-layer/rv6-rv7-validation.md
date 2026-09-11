# rv-6 / rv-7 取证（final tree，c6faff8 基线 + 本次改动）

## rv-6 搜索断言

```
$ rg -n --glob "!**/__pycache__/**" '_build_(claude|kimi|codex|deliberation|content_generation)_command|_AGENT_COMMAND_BUILDERS|should_filter_claude_stream' src
(无命中——旧构造点符号零残留)

$ rg -l --glob "!**/__pycache__/**" "\"(claude|codex|kimi)\"" src/backend | wc -l
18  （改造前 24）
```

18 个文件逐一归类：

- 注册表定义/内置默认（唯一数据来源）：`core/shared/models/agent_spec.py`、`infrastructure/config/settings.py`（含旧 labels 键兼容读取）、`core/shared/models/agent_runner.py`（agent_fallback_order 默认值）、`core/shared/models/agent_deliberation.py`、`core/shared/models/agent_decision.py`
- auto/缺省兜底语义：`core/use_cases/run_agent_once.py`（auto→claude）、`core/use_cases/recover_publish.py`（supervisor auto→claude）、`core/use_cases/generated_content.py`（_DEFAULT_AUTO_AGENT）、`core/use_cases/interactive_decision.py`（synthesizer 缺省）、`core/use_cases/agent_review.py`（同 agent 禁止时落 codex）、`core/use_cases/create_issue_from_prd.py`（resolve_agent_name 缺省）、`core/use_cases/idea_prd_drafts.py`（参数缺省）、`api/routes/agent_runner_idea_inbox.py`（Field 缺省）
- docstring/提示文案示例：`core/use_cases/agent_invocation.py`、`engines/agent_runner/container_auth.py`、`core/shared/interfaces/runner_live_view.py`、`engines/agent_runner/repository_local.py`（help 文案）、`generated_content.py`/`create_issue_from_prd.py` 的 docstring
- 模型名家族检测（非 agent 路由）：`infrastructure/models/model_loader.py`

结论：src/ 下只剩 agent_invocation.py 一处 argv 构造点。

## rv-7 全量回归与质量门禁

```
$ just test all
======================= 1921 passed in 96.94s (0:01:36) ========================
✅ just test flag updated: P1-FEAT-20260911-010513-agent-cli-adapter-layer @ c6faff87441dc4d626be4a56f78080a76c62e1de

$ just lint --full
✅ just test 标记有效 (P1-FEAT-20260911-010513-agent-cli-adapter-layer @ c6faff87)，允许提交。
✅ just lint --full flag valid: P1-FEAT-20260911-010513-agent-cli-adapter-layer @ c6faff87

$ uv run mkdocs build --strict
(exit 0)
```
