# Verifier Report

**Verdict: 8 PASS + 1 部分披露 + 1 INCONCLUSIVE — 独立 verifier 已实际运行，但未给出无条件 PASS。**
本报告由 executor 存档独立 verifier（只读调查 agent，未修改任何文件）的回执原文摘要；两个未闭合项已由 executor 在同一轮内处理（PRD 对账），`just test` 基线红留在 CI 与人工验收视野里，不当成已达成。

## 独立 verifier 的逐条判定

| # | 被质疑的说法 | 判定 | verifier 核对到的位置 |
|---|---|---|---|
| 1 | 可用性只有一处事实源 | PASS | `shutil.which` 用于 agent bin 的新增判断只在 `core/use_cases/agent_onboarding.py:83`，被 list / setup / 门禁 / 首启提示 / 两个 Console 视图共用；另外两处（`cli_parsed_commands/agent.py:437` doctor、`engines/model_evaluation/judge_client.py:93`）是**基线既有**、本次未改；`frontend-public/` 没有任何探测，只渲染 `agent_availability` / `bin_path` |
| 2 | 门禁在 claim 之前 | PASS | `run_agent_repositories_once.py:106` 在 `github_client_factory(...)` 之前、且无条件（含 dry-run）调用；`agent_runner_issue_handlers.py:399` 在认领 CAS 之前；`cli_exit_codes.py:86` 映射到 `NOT_FOUND(3)`，`rv-2-run-blocked.err` 记录了 3 与阶段/预设/agent/bin/setup 四个点名 |
| 3 | 缺失 ≠ 回退 | PASS | `agent_runner_orchestrate.py:514` 在 `except AgentUnavailableError`（:518）之前捕获并重新抛出 `PrimaryAgentNotInstalledError`；运行时阶梯（`run_agent_execution_loop.py:332`、`agent_review.py:389`）未被改动；`resolve_run_gate_block` 明确忽略 `agent_fallback_candidates` |
| 4 | 四条 oracle 是真入口 | PASS | 四个脚本都在 `env -i` 沙箱里跑 `uv run kc`，无 monkeypatch、无手写 TOML（绑定全部经 `kc agent preset set` / `lifecycle set` / `agent add` / `fallback candidate add`）；负控同时断言退出码与 sha256 相等，rv-2 两个方向都跑（可用→10，配了可用回退仍缺→3）；rv-4 起真实 `kc console` 到新端口，日志出现 `already running` 即判失败，`/app/settings/lifecycle/` 的 DOM 标记与同源 API 逐行对照，无路由拦截；所服务的前端包确实出自本树（`static/console/_next/.../14-hdnc2vw346.js` 含 `matrix-agent-missing`）。唯一偏差：rv-3 用 `doctor myagent --json` + `--lifecycle` 代替 PRD 文本的 `--all-profiles`，已在 `evidence.json` risks 中披露且技术上成立 |
| 5 | seed 行为真的改了且不破坏既有文件 | PASS | `settings_sources.py:63-90` 不再读源码根 `config.toml`，只写代码内 `_GLOBAL_CONFIG_SEED_TEMPLATE`（`[agent_runner]`），写前有 `is_file()` 守卫、写在 `config_write_lock` 内；仓库根 `config.toml` 里 `[agent_runner.presets.*]` 与 `[agent_runner.lifecycle_presets]` 已全部注释化 |
| 6 | `src/` 内没有新增 agent 专有分支 | PASS | `grep -rn "myagent" src/` 无命中；自定义 Agent 保持声明式 |
| 7 | FR-1..FR-7 覆盖 | PASS（一项已披露的缺口，见 #9） | FR-4 提示受 TTY 门禁、对 `_KC_COMPLETE` / 机器输出 / `--help` 静默、写 `~/.kedacode/.agent-setup-hint-shown`、永不阻断（`cli_agent_hint.py:67-94`，接在 `cli_typer_app.py:486`）；FR-6 默认预览（`written:false` + 哈希不变）并对重名 / 未注册协议 / 缺 `--prompt-flag` 以 2 拒绝在写之前；黄金快照 `tests/test_agent_invocation_golden.py` 未动且绿 |
| 8 | 文档与随包 skill 同步 | PASS | `docs/guides/agent-runner.md`、`configuration.md`、`lifecycle-agent-matrix.md`、`model-presets.md` 与 `templates/skills/kedacode-operator/references/setup-and-config.md` 的旗标与 `cli_parser_session_commands.py:226-308` 逐项一致；漂移守卫 `tests/test_kedacode_operator_skill.py`（28 项）通过；无新增文档页，`mkdocs.yml` 正确地未动 |
| 9 | 两条已知限制属实 | 属实，披露位置部分不实 | `loop_fire.py:317` 确实没传 `agents=`（内置 registry），而 `cli_loop.py:183` / `loop_create.py:113` 传了生效注册表；`.kedacode.toml:318-319` 仍绑 `qoder-qwen3_8-flash_max` 而模板已删其定义。两者在 `….verification-plan.md` 与 `….evidence-report.md` 中已披露，但 executor 当时写的「记入 PRD Change Log」为虚——PRD 里那还没有 Change Log。**已在本轮补写**。 |

## verifier 提出会 block 的两点

1. **PRD 未对账**：§Final Reconciliation 仍写着「这是新建待实施 PRD，尚无生产实现」，§9 当时 0/24 勾选，两条限制未进 PRD 正文。→ 属文档缺口而非事实造假，executor 在同一轮内完成对账（本报告的下一份改动）。
2. **`just test` 在本机不可复现为绿**：33 个失败集中在 `.iar` / 产品改名族与迁移 fixture（`test_console_processes.py::test_find_own_command_index`、`test_repository_local_migration.py`、`test_generated_prd_content.py::test_resolve_prd_skill_path_precedence` 等），不触碰交付代码；交付自身的 153 项与全量 4549 项通过。另外本机 `--testmon` 会 0 selected，所以「`just test` 绿」必须在 CI 上复核。→ §9 的「`just test` 与相关 e2e 在最终代码树上通过」**记为 `[~]`（部分）**：交付相关测试与 e2e 已在最终树绿，仓库级 `just test` 因 HEAD `27ad6b57` 遗留基线仍红，不由本 PRD 认领。

## 其它核对

无越界改动（impact tree 之外只碰了 5 个文件，每个是 1–4 行的注册表 / 诊断管道：`cli_skill.py`、`backlog_actions.py`、`interactive_decision.py`、`labels_issue.py`、`agent_invocation_tracing.py`）；证据里没有凭据落盘（rv-2 只把 `gh auth token` 经环境传递，未写入任何产物）；`evidence.json` 的 `stdout_assertions` 字面量除四条 `rv-N OK`（设计上只出现在 stdout）外，都能在记录产物中重放命中。

## 本报告不主张的事

- 不主张「独立 verifier 给了无条件 PASS」：verifier 的两条 block 项中，第 2 条以 `[~]` 形式保留在 §9，交给 CI 与人工验收。
- 不代替 `Human-Confirmed`：三项决策与 9.1 呈递仍需人工回复，本报告与勾选框无关。
