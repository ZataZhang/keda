# 验证计划

## 范围与规则

按 PRD §7.6 的 rv-1 至 rv-4 顺序验证最终工作树。每项先运行能让对应断言变红的负控，再运行修复后路径；证据按 `rv-<item_number>-<slug>.<ext>` 单项保存。真实入口不替换为 mock；仅在 PRD 明确允许处替换 provider/GitHub。

原始输出与脚本位于同目录的 `scripts/` 和 `rv-*.txt` 中，受仓库 `.gitignore` 的证据白名单约束，不进入代码 diff。当前工作树还没有最终 commit tree，后续 runner 需在提交后绑定最终 tree 与证据哈希。

## Oracle 执行计划与结果

| 项目 | 最高保真度入口与执行 | 负控与预期失败 | 结果与证据 |
|---|---|---|---|
| rv-1 · R3 · human | 用配置默认 Codex 在真实 PTY 裸运行 `uv run kc`；读取当前仓库/operator skill；随后在 provider 对话中明确请求 preview，观察真实 dev server 与 loopback URL。 | 修改 skill-conflict 门禁使其仅提示，运行 `user_edited_skill_is_preserved_and_blocks_session`；预期测试 `DID NOT RAISE RuntimeError`。再以不支持 interactive profile 的 `qoder` 进入真实 TTY，预期 provider 启动前退出。 | 负控红、恢复后 22 个 session + 25 个 packaged-skill 测试通过（47 total）；最终裸入口真实 TTY 启动 Codex v0.162.0，但宿主在首个输入前拒绝 `Operation not permitted`。没有对话、preview 或截图，因此该轮 rv-1 为 INCONCLUSIVE。见 `rv-1-automated-tests.txt`、`rv-1-kc-native-session.txt`。**后续轮次已取代该结论**：改在交付树同一 HEAD 的隔离 clone（`/private/tmp/issue256-rv1/repo`）用 tmux 真实 PTY 完成对话、preview 启停与终端画面采集，现为 PASS（executor evidence），采集方式变更与差异披露见证据报告「采集方式变更」与「验证限制」。 |
| rv-2 · R1 · verifier | 真实安装入口运行 `kc --help`、`kc repl --help`、`kc console --help`、`printf ... | uv run kc` 与 `kc schema --json`；pytest 覆盖空 argv TTY dispatch、真实 console script no-TTY、REPL/Console 命令树与 parser/schema 对齐。 | 将 no-TTY 空 argv 分支返回值由 1 改为 0；预期真实 console-script 回归断言 `returncode == 1` 失败。 | 负控真实返回 0 并使回归测试变红；源码恢复后 bare pipe 输出帮助并返回 1，CLI/schema/兼容路由与 shell completion 测试 11 passed。复核期间还修正了旧证据命令在 zsh 中使用只读特殊变量 `status` 的问题。见 `rv-2-cli-routing.txt`、`rv-2-negative-control.txt`。 |
| rv-3 · R3 · verifier | `tests/test_agent_runner_stall_supervision.py` 通过真实 `SubprocessRunner` 和 OS 子进程组验证 healthy/stalled、owner 复核、精确取消和 recovery 顺序。provider verdict 与 GitHub 由 PRD 允许的边界控制。 | 去掉 unknown-owner handoff 审计调用；预期 handoff marker/reason 断言失败且目标进程不收信号。再去掉取消前实时 process creation time 复核；预期旧 attempt identity 被错误取消。 | 两个负控均红并恢复源码；63 项模块测试通过。healthy/unknown/变化快照不误杀，目标组确认退出后才进入 recovery。见 `rv-3-stall-supervision.txt`。 |
| rv-4 · R2 · verifier | 真实 `kc preview start/status/stop`、新进程 TOML 加载、global/repo 合并、TTY session profile 解析与 scheduler 测试。 | 移除 loopback 校验；预期公网 `ready_url` 被接受、fresh CLI 测试失败。再去掉 preview owner 的 process creation time 检查；预期复用 PID/PGID 的 foreign 进程仍被当成 KC preview。 | 两个负控均红、源码恢复；20 个 preview 测试、22 个 session 测试、10 个配置/监督选择测试通过。隔离真实 PTY 选中 repo profile，preview CLI 启停 loopback 进程组。见 `rv-4-config-supervision.txt`。 |

PRD checker `--all` 通过，FR-1 至 FR-8 可解析。`--check-provided --archive-ready` 现为 PASS（Final Reconciliation 完整，验收状态横幅与 §9 一致：`Human-Confirmed` 仍留三个空框，banner 为 `🧍 待人工验收`）。runner-owned 的独立 verifier gate 仍为 `[~]`，执行器不代为勾选。PRD 位置：已在上一轮提交尝试中由 `archive-tasks` hook 归档到 `tasks/archive/`，执行器不自行移回。

## 通用验证命令

```bash
UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest tests/test_cli_agent_session_entry.py tests/test_repl_session.py tests/test_kc_preview.py tests/test_agent_runner_stall_supervision.py tests/test_agent_runner_cli.py tests/test_cli_schema.py tests/test_kedacode_operator_skill.py --no-testmon -q --no-header
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just test all
UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run mkdocs build --strict
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just lint --full
UV_CACHE_DIR=/private/tmp/issue256-uv-cache just lint --reuse
```

最终定向命令 `UV_CACHE_DIR=/private/tmp/issue256-uv-cache uv run pytest tests/test_cli_agent_session_entry.py tests/test_repl_session.py tests/test_kc_preview.py tests/test_agent_runner_stall_supervision.py tests/test_agent_runner_cli.py tests/test_cli_schema.py tests/test_kedacode_operator_skill.py --no-testmon -q --no-header` 为 `309 passed`；`UV_CACHE_DIR=/private/tmp/issue256-uv-cache just test all` 在修复本地状态落点后的最终树（HEAD `0c18369a` / tree `f6785ef6`）上为 `3903 passed, 1 skipped`（exit 0，286.28s）。新增 recovery 集成、pre-PR review 与 final-verifier 定向集为 `5 passed`；受本地状态默认值改动影响的配置层定向复跑为 `86 passed`。早期全量运行暴露 4 个 daemon CLI 用例对真实 state-home 锁目录的权限限制、10 个 config migration 用例对宿主进程扫描的依赖；现通过测试内注入受控 lock/scanner adapter 结果隔离 CLI 行为，并保留 dedicated lock 与 scanner 不可用时 fail-closed 测试。没有重定向 HOME 或放宽安全检查。既有 Ruff F821 已修复并添加 backlog route 回归。

最终 `just lint --reuse`（exit 0）、`uv run mkdocs build --strict`（exit 0）与 PRD checker `--all` / `--check-provided --archive-ready`（均 PASS）通过；快档 `just lint` 只检查 staged 文件，本轮执行器未 stage 任何内容（全部 hook `(no files to check)Skipped`），其 exit 0 不作为证据，以全文件档逐 hook 结果为准。`SKIP=check-test-flag just lint --full` exit 0：18 个 hook 中 15 Passed、2 个按文件过滤 Skipped、`Check just test flag` 由 `SKIP` 显式 Skipped，0 Failed，且 `trim trailing whitespace` / `fix end of files` / ruff-format 均未改写任何文件（运行前后 `git status --porcelain` 与 `quality_effective_tree working test` 逐项一致，仍为标记 tree `f6785ef6`）。`check-test-flag` 此前的持续失败已回到触发源修复，而非归因于「runner 重写自有文件不带换行的竞态」：真实机理是本分支 `MemoryConfig` / `AgentRunnerMemorySettings` 的默认目录仍是改名前的 `.iar/memory` / `.iar/skills`，而 `.gitignore` 只忽略 `.iar-worktrees/` 与 `.kedacode/`，于是 `just test` 期间测试自身向这条被跟踪路径写入，标记追不上提交树；对齐 `.kedacode/` 后红绿对照为修复前 attempt 342→360、修复后 252→252 且全量运行前后工作树无差异。该 hook 的最终判定对象是提交时的 staged 树，执行器不得 `git add`，故本轮只证明成立条件：`quality_effective_tree working test` 与标记 tree 相同，runner 提交路径中的 `git add -A` 归一后即一致（`scripts/shared/hooks/check_test_flag.sh` 的提示口径与此一致）。没有修改 hook、质量树排除规则或 `tests/guards/**`。jscpd 仅在三个必需相同接口签名周围使用 `jscpd:ignore` 注释，方法体仍检查；没有调低重复检测阈值或修改 hook。恢复预算、验证失败、pre-push review 和 final-verifier 的 recovery-gate 负控已记录于 `rv-3-stall-recovery-gates.txt`。
