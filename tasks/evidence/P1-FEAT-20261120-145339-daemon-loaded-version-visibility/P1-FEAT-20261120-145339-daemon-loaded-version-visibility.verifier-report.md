# Verifier Report — P1-FEAT-20261120-145339-daemon-loaded-version-visibility

Verdict: PASS

- 验证对象：`main` HEAD `616d0ea7`（功能由 PR #426 合入，合并提交 `e328acbe`）
- 验证日期：2026-11-24
- 验证环境：Windows 10，`uv run`；另引用 `main` HEAD 的 CI 结果（Linux 全量测试档）
- 结论口径：只判定执行侧验收项；两条 Human-Confirmed 项和“独立 verifier 判定 PASS”项不在本表内

## 1. 独立复跑的命令与结果

| # | 命令 | 结果 |
|---|---|---|
| C1 | `uv run python tasks/evidence/P1-FEAT-20261120-145339-daemon-loaded-version-visibility/scripts/rv_status_smoke.py <临时目录>` | 输出 `PASS`，退出码 0。表格：`0.0.1 (installed 0.9.0)`，表格下方有提示 `kc registry stop --repo-id rv-demo && kc registry start --repo-id rv-demo`，exit_code=0。JSON 三次调用均 exit_code=0：`mismatch` / `current` / `null + unknown`。输出与 `rerun-616d0ea7/` 中的内容一致（仅占位 PID 不同） |
| C2 | `uv run pytest -o addopts='' tests/test_daemon_parallel_limit_status.py -k loaded_version -q` | 2 passed |
| C3 | `uv run pytest -o addopts='' tests/test_agent_runner_cli.py -k version_resolved_once -q` | 1 passed（`resolve_keda_version` 恰好调用 1 次，`run_agent_daemon` 收到 `loaded_version='0.9.0'`，exit 0） |
| C4 | `uv run pytest -o addopts='' tests/test_daemon_parallel_limit_status.py -q` | 12 passed，1 failed：`test_daemon_records_the_cli_override_it_actually_uses` 抛出 `UnsupportedPlatformFeatureError`（POSIX 进程组守卫）。该用例本 PRD 未修改，也在基线失败集合中 |
| C5 | `uv run pytest -o addopts='' tests/test_daemon_parallel_limit_status.py tests/test_cli_registry.py tests/test_agent_runner_cli.py -q` | 10 failed，200 passed，1 skipped。失败集合与 `rerun-616d0ea7/target-tests-base-e328acbe-parent.txt` 中的基线（合并前父提交，187 passed）**逐条相同**（已 diff），没有新增失败；新增的 13 个用例全部通过 |
| C6 | `uv run pytest -o addopts='' tests/test_cli_time_display.py -q` | Windows 上 8 个失败，原因都是 `time.tzset` 不存在（环境问题）。其中包括 PR 修改的 `test_daemon_status_table_renders_started_at_locally`，本机无法验证，改由 C7 覆盖 |
| C7 | `gh run view 45335744562`（`main` HEAD `616d0ea7` 的 push CI，Validate Template job） | `Run pre-commit checks`、`Check max file lines`、`Run local test suite`（push 事件 = 全量档，Linux）、`Build documentation` 全部 success |
| C8 | `grep -rn "from backend.api\|import backend.api" src/backend/core` | 无输出 |
| C9 | `git diff e328acbe^ 616d0ea7 -- src/backend/core/use_cases/daemon_single_instance.py` | 为空 |
| C10 | `grep -n "version_status" docs/guides/agent-runner.md src/backend/engines/agent_runner/templates/skills/kedacode-operator/references/daemon.md` | 两个文件均有命中（各 1 处） |
| C11 | `uv run python -c "...describe_daemon_version(None), daemon_version_fields(None, '0.9.1')"` | `'-'` 与 `{'loaded_version': None, 'installed_version': '0.9.1', 'version_status': None}` |

## 2. FR 代码核对（读 `git diff e328acbe^ 616d0ea7`）

- **FR-1**：已实现。`run_daemon_command`（`src/backend/api/cli_parsed_commands/runner.py:541`）在启动前调用一次 `resolve_keda_version()`，经 `run_agent_daemon(loaded_version=...)` 传到 `_AgentDaemonLoopRequest.loaded_version`，每轮由 `write_daemon_parallel_limit_status` 原样写出。`run_agent_daemon` 只有这一个调用点；`src/backend/core` 中没有任何 `resolve_keda_version` 引用，所以运行中不可能重新解析版本。
- **FR-2**：已实现。表格新增 `version` 列，由 `describe_daemon_version` 渲染：版本一致时显示版本号；不一致时显示 `X (installed Y)`；未上报时显示 `unknown`；非 daemon 行显示 `-`。JSON 由 `daemon_version_fields` 增加三个字段，supervise-daemon 行的 `loaded_version` 与 `version_status` 为 null。
- **FR-3**：已实现。提示只在 `_render_table` 内生成和打印，按 `is_managed` 给出托管或手动两种命令。`check_daemon_version` 的判断顺序与 §7.1 伪代码一致：先判断“未上报”（提示），再判断“已安装版本不可知”（不提示）。
- **FR-4**：已实现。函数末尾固定 `return 0`；`emit` 在 JSON 模式下只调用 `emit_json`，不执行表格渲染器，所以 stdout 不会混入提示（C1 的 `json.loads(stdout)` 通过）。
- **FR-5**：已实现。读取时只取 dataclass 已知字段，`loaded_version` 默认为 `None`，异常捕获增加了 `AttributeError`。PID 存活校验未改。
- **FR-6**：docs 与 skill 已同步。ROADMAP 部分见第 4 节。
- **Non-Goals**：没有自动重启；`kc registry list` 与管理终端未改；supervise-daemon 不上报版本。均符合。

## 3. §9 执行侧逐项判定

| 分组 | 验收项 | 判定 | 证据 / 命令 |
|---|---|---|---|
| Behavior | 不一致：表格版本列与提示正确，退出码 0（rv-1） | 已证实 | C1；`rv-1-status-mismatch.txt`；`rerun-616d0ea7/rv-1-status-mismatch.txt`；`negative-control/rv-1-status-mismatch.txt`（实现前没有 version 列） |
| Behavior | JSON：三字段齐全，`mismatch` / `current` / `unknown` 三态正确，stdout 可解析（rv-2） | 已证实 | C1；`rv-2-status-json.txt`；`rerun-616d0ea7/rv-2-status-json.txt`；`tests/test_cli_registry.py::test_daemon_status_json_reports_loaded_and_installed_versions` |
| Behavior | 启动时版本不随磁盘变化（rv-3） | 已证实 | C2、C3、C8，以及 `core` 中没有 `resolve_keda_version` 引用（结构上不可达） |
| Behavior | supervise-daemon 行版本列为 `-`，JSON 中 `loaded_version` / `version_status` 为 null | 已证实（有小缺口） | JSON 部分由 `test_daemon_status_json_reports_loaded_and_installed_versions` 断言（`p-review` 行）。表格 `-` 部分**没有测试断言**，由代码核对（非 daemon 行 `_version_check_of` 返回 None）与 C11 直接调用证实。见缺陷 D-1 |
| Architecture | `core/` 不导入 `api/`；`just lint` 通过（输出存证） | 已证实 | C8；C7（pre-commit 含 ruff 与架构检查，在 HEAD 的 CI 上 success）。本地没有保存 lint 输出文件，以 CI run 45335744562 作为存证 |
| Architecture | 锁文件格式未改 | 已证实 | C9 |
| Documentation | `version_status` 在两个文件中均有命中 | 已证实 | C10 |
| Documentation | `ROADMAP.md` 维护带对应条目已指向本 PRD | 不适用（N/A），不阻塞 | 见第 4 节。需在 PRD Change Log / Final Reconciliation 中记为 N/A 并写明原因，**不得勾选为已完成** |
| Validation | 真实 `kc` 子进程验证 rv-1、rv-2 通过 | 已证实 | C1；`rv-1-status-mismatch.txt`、`rv-2-status-json.txt`；`rerun-616d0ea7/` |
| Validation | 负向对照：实现前 rv-1 为红 | 已证实 | `negative-control/rv-1-status-mismatch.txt`：表格没有 `version` 列、没有提示、没有 `installed` 子串；`negative-control/rv-2-status-json.txt` 中也没有三个字段 |
| Validation | 新增用例全部通过，且失败集合与基线一致 | 已证实 | C5（与基线 diff 完全相同）；C4 的失败属于基线；PR 修改的 time-display 用例由 C7 的 Linux 全量档覆盖 |
| Delivery Readiness | PR 证据评论或完成消息原样携带 9.1 内容（rv-1 关键行内联） | 尚未证实，不阻塞 | PR #426 正文转述了 rv-1 结果（`0.0.1 (installed 0.9.0)` 加上提示中的 `kc registry stop/start`），但不是终端原文，PR 也没有评论。该项允许以“完成消息”满足，须在归档完成消息中原样内联 rv-1 的表格行和提示两行 |

## 4. 关于 ROADMAP 条目的裁定

- 事实：`10ca36c7`（2026-11-23）把 `ROADMAP.md` 从 532 行重写为 25 行，原“维护带 · 常驻 daemon 的版本可见性”条目（旧文件第 421 行）已删除。该提交是 PR 合并提交 `e328acbe` 的祖先，所以执行者开工时这个条目已经不存在。`docs/project/roadmap-history.md` 明确写明：具体任务状态以 PRD 为准，根目录 Roadmap 只在工作目标或投入顺序变化时更新。在当前 `ROADMAP.md` 和 `docs/project/` 中，都找不到可以挂接的条目。
- 裁定：**不适用，不阻塞 PASS。** 该项的目的（让 Roadmap 的待办可以追溯到承接它的 PRD）已经被新的项目约定取代。如果现在把条目加回去，反而违反新约定。
- 条件：执行者在 PRD 的 Change Log（Type: scope 或 evidence）和 Final Reconciliation 中写明“ROADMAP 于 2026-11-23 由 10ca36c7 重写，条目已移除，按新约定不再维护条目级链接，本项与 FR-6 中的 ROADMAP 部分记为 N/A”。该复选框应保持未勾选或标为 N/A，**不得作为“已完成”勾选**。§7.2 变更树中的 `ROADMAP.md` 节点同理。

## 5. 缺陷与观察（均不阻塞）

- **D-1（测试缺口，低）**：没有测试断言表格模式下 supervise-daemon 行的 version 列为 `-`。`test_daemon_status_table_shows_version_and_restart_hints` 不含 REVIEW_DAEMON 行。行为已由代码和 C11 证实，建议后续在该测试中补一行 supervise-daemon 并断言其不出现在提示中。
- **D-2（文档结构，低）**：在 `src/backend/engines/agent_runner/templates/skills/kedacode-operator/references/daemon.md` 中，新增的 “Is the daemon running the installed code?” 段落插在了 “Reading the value actually in use” 列表的中间。原列表最后一条（“The console Backlog control shows the daemon's live value…”，讲并行上限）因此落到了版本段落之后，读起来像是在讲版本。建议把这一条移回版本段落之前。
- **D-3（文档措辞，低）**：`docs/guides/agent-runner.md` 写着“已安装版本本身取不到时 … `version_status` 为 `unknown` 且不提示重启”。实际行为是：如果 daemon 同时也没有上报版本，仍会提示重启。这与 §7.1 的判断顺序一致，代码没有问题，只是文档没有覆盖这个组合，可以补半句。
- **O-1**：PR #426 正文把 rv-5 写成了 “Validation blocker”。按 §7.6，rv-5 是 `required_for_acceptance: false` 的 opt-in 项，不构成验证阻塞，归档时不应使用 `⏸️ 验证阻塞` 横幅，走正常的 `🧍 待人工验收` 路径即可。
- **O-2**：PRD Change Log 中说改动前后均为“14 个”既有失败，而 `rerun-616d0ea7/` 与本次复跑都是 10 个。数量差异来自不同的复跑时点，结论（无新增失败）不受影响。建议 Final Reconciliation 以 `rerun-616d0ea7/` 的数据为准。
- **O-3**：rv-3 的单元测试对 `backend.api.version_info.resolve_keda_version` 的 monkeypatch 在 core 路径上实际上不起作用，因为 core 不导入它。“启动时版本不漂移”的真正保证来自结构约束（C8）加上入口测试（C3），与 PRD 中 rv-3 的 negative_control 说明一致，可以接受。
