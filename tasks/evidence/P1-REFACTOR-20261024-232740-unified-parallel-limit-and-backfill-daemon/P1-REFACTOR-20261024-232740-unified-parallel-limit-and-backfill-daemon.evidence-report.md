# 证据报告：统一并行上限，daemon 执行期间持续补位

## 人审导航 / Human Review Navigation

| 看什么 | 呈递物 | 预期 |
|---|---|---|
| 看板顶部与跨仓库状态栏（rv-3） | `rv-3-dashboard-status-bar.png` | 顶部写"看板同步：每 30 分钟"；状态栏写"默认并行上限 1"，不再出现"队列上限" |
| keda 仓库 Backlog 控件：未保存页面设置（rv-3） | `rv-3-backlog-config-source.png` | "并行上限 10（配置文件）" |
| 保存页面设置 4 之后（rv-3） | `rv-3-backlog-policy-source.png` | "并行上限 4（页面设置）"，旁边出现"恢复为配置文件值：10" |
| 恢复为配置文件值之后（rv-3） | `rv-3-backlog-restored.png` | 回到"并行上限 10（配置文件）" |
| daemon 以 `--concurrency 5` 运行时的 Backlog 控件（rv-4） | `rv-4-backlog-daemon-cli-override.png` | "并行上限 5（daemon 命令行 --concurrency；配置文件 10 未生效）"，旁边写"页面设置仅本机生效，不改 .kedacode.toml" |

![rv-3 看板顶部与状态栏](rv-3-dashboard-status-bar.png)

![rv-3 未保存页面设置](rv-3-backlog-config-source.png)

![rv-3 保存页面设置 4](rv-3-backlog-policy-source.png)

![rv-3 恢复为配置文件值](rv-3-backlog-restored.png)

![rv-4 daemon 命令行覆盖](rv-4-backlog-daemon-cli-override.png)

截图只保存在本地（证据目录的图片不进 git）。打开方式：`open tasks/evidence/P1-REFACTOR-20261024-232740-unified-parallel-limit-and-backfill-daemon/rv-3-backlog-policy-source.png`。

截图来自用本次代码在 18765 端口启动的真实管理终端（`just console-sync` 后 `kc console --port 18765`），操作对象是本机真实的 keda 仓库；保存前 keda 没有页面设置行，恢复后再次确认设置行已删除，本机状态与操作前一致（见 `rv-3-console.txt`）。

rv-4 的做法：本机常驻的 keda daemon（PID 78808）是旧代码，不会写状态文件；以它的 PID（正是 keda 单实例锁的存活持有者）临时写入一份 `--concurrency 5` 的状态文件，验证 `kc daemon status --json`、Autopilot 接口与页面三处都读到 5 / `cli_override`，截图后立即删除该文件（见 `rv-4-daemon-live-limit.txt`）。状态文件由真实 daemon 写出、退出时删除，由 `tests/test_daemon_parallel_limit_status.py` 覆盖。

## rv-1 执行期间补位

`rv-1-backfill.txt`：`tests/test_daemon_execution_slots.py` 6 项通过。其中 `test_daemon_backfills_a_new_issue_while_a_long_issue_is_running` 驱动真实的 `run_agent_daemon` 主循环与真实的 `run_once`，只替换 GitHub 客户端与单个 Issue 的处理函数：上限 2、#1 一直在跑时，第二次轮询领取了 #2、#3 留在队列。

负对照：保留新代码，只在 daemon 调用 `run_once` 时去掉 `execution_slots`（等于退回"一轮跑完再看下一轮"），同一测试失败为 `[1, 2, 3] == [1, 2]`，说明断言能区分新旧行为。

**未达到 PRD 规定的保真度**：PRD 的 rv-1 / rv-2 要求用真实 `kc daemon` 进程加文件型假 `gh` 跑端到端场景，本次没有搭建这套假 `gh`；证据是进程内的集成测试（真实主循环与调度，替换 GitHub 客户端）。

## rv-2 不重复执行与兼容

`rv-2-safety.txt`：
- `kc daemon run --max-issues 3` 返回退出码 2，提示改用 `--concurrency`（真实 CLI）。
- `kc config migrate --dry-run` 在隔离 HOME、keda 配置副本上列出并预演删除 `max_issues`，附删除原因（真实 CLI）。
- 对账跳过本机在执行的 Issue、补位只占空位并跳过在执行 Issue、标签延迟时按本机在执行数计入上限、同一 Issue 不能重复提交、旧 `max_issues` 只告警、迁移删除旧键、daemon 按"命令行 > 页面设置 > 配置文件"取值：对应测试全部通过。

## 全量测试与门禁

- `just test`：2036 passed（testmon 增量），`just lint --full` 与 `just lint --reuse` 通过。
- 全量 `uv run pytest -o addopts="" --ignore=tests/playwright-e2e`：4957 passed, 1 skipped。第一次全量运行发现 operator skill 的命令白名单仍收录 daemon 的 `--max-issues`、未收录 `kc config migrate`，修正后重跑通过。

## 尚未完成

- 没有运行独立 verifier，PRD 未归档，验收清单未勾选。
- 本机常驻的 keda daemon（PID 78808）是 10 月 20 日启动的旧进程，重启后才会运行新代码。
