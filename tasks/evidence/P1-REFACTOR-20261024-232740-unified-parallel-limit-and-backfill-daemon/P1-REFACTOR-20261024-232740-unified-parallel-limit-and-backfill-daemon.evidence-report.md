# 证据报告：统一并行上限，daemon 执行期间持续补位

- PRD: `tasks/archive/P1-REFACTOR-20261024-232740-unified-parallel-limit-and-backfill-daemon.md`
- GitHub Issue: https://github.com/ZataZhang/keda/issues/316
- 交付 PR: #319（commit `1142ecb8`）+ 验收补齐 commit `3bd595d5`
- 证据生成时的 git tree: `bdb67a92ce68688bfdb0e2e70a1930dba39de633`（HEAD `3bd595d5`）
- 采集时间: 2026-10-28

## 采集方法（真实入口）

`scripts/rv_daemon_e2e.py` 在隔离工作根（默认 `/tmp/kc-rv-parallel-limit`，可用 `RV_WORK_ROOT` 覆盖）里：

1. 建独立 `HOME`、独立 `config.toml`（`KEDACODE_CONFIG`）、独立 git 仓库与裸 remote；
2. PATH 前置一个文件型假 `gh`（`scripts/fake_gh.py`：Issue/标签/评论/计数查询的真实读写，状态落 JSON）；
3. 以子进程启动真实 `kc daemon run`（真实配置加载、真实主循环、真实 `run_once` 候选选择、真实认领仲裁、真实对账与补位调度、真实标签/评论写入）；
4. agent CLI 换成确定性假 agent：prompt 里带 `ISSUE-MARK-<n>` 时阻塞到对应 `release-<n>` 文件出现，用于控制"谁在执行、执行多久"。

只替换 GitHub（gh）与 agent CLI 两个外部边界，正是 PRD `mock_boundary` 允许的范围。运行：

```bash
uv run python tasks/evidence/P1-REFACTOR-20261024-232740-unified-parallel-limit-and-backfill-daemon/scripts/rv_daemon_e2e.py
# 可只跑某组：... rv_daemon_e2e.py rv1 rv2b rv3
```

## rv-1 执行期间补位（R3）→ `rv-1-backfill.txt`

上限 2、轮询 4s。A 先被领取并阻塞；B、C 随后置为就绪。

| 断言 | 结果 |
|---|---|
| B 在一个轮询间隔内被领取 | PASS：就绪后 4.4s 被领取（4s 轮询 + 处理开销） |
| 领 B 时 A 仍在执行 | PASS：A 保持 `agent/running` |
| C 在空位出现前保持就绪、无认领评论 | PASS |
| A 结束后下一次轮询领取 C | PASS |
| 每个 Issue 认领评论各 1 条 | PASS：`{101: 1, 103: 1, 104: 1}` |
| 每次轮询都有 `Parallel limit` 行 | PASS：3 pass / 3 limit 行 |
| 补位期间没有重复认领或仲裁失败 | PASS |

## rv-2 不重复执行、计数失败、旧配置、CLI（R3）→ `rv-2-safety.txt`

| 断言 | 结果 |
|---|---|
| (a) 返工标记不重复认领在执行 Issue | PASS：A 认领评论仍为 1 |
| (a) 对账不把在执行的 A 当僵尸 | PASS：无对账/失败评论 |
| (b) 计数失败时本轮不领取 | PASS：日志出现 `claiming zero new Issues this pass`，新 Issue 认领评论 0 |
| (b) 已在执行的 Issue 不受影响 | PASS |
| (b) 下一次正常轮询恢复领取 | PASS |
| (c) 旧 `max_issues` 只提示一次 | PASS：日志中弃用提示 1 条 |
| (c) 旧配置下同时运行数不超 1 | PASS：16 次采样最大值 1 |
| (c) 生效上限取 `max_concurrent_issues` | PASS：`limit=1 source=config` |
| (d) daemon 拒绝 `--max-issues` | PASS：exit=2，提示 `kc daemon run --concurrency <N>` |

故障注入：`faults.json` 里 `fail_running_list` 让下一次"运行中计数"查询失败一次，验证 fail-closed。

## rv-3 页面设置成为生效值（human + daemon 日志闭环）→ `rv-3-daemon-log.txt`

在隔离环境启动真实 `kc console`，通过页面同源的 `PATCH /api/v1/agent-runner/backlog/settings` 保存 4，再启动真实 daemon：日志出现 `limit=4 source=policy`；恢复为配置文件值后接口回到 `2/config`。

人审截图（本地，不进 git）：`rv-3-dashboard-status-bar.png`（看板顶部"看板同步：每 N 分钟" + 跨仓库"默认并行上限"）、`rv-3-backlog-config-source.png`（10 配置文件）、`rv-3-backlog-policy-source.png`（4 页面设置）、`rv-3-backlog-restored.png`（恢复后 10 配置文件）。数值 10/4 与 PRD 示例的 2/4 不同是因为本机 keda 仓库真实配置是 `max_concurrent_issues = 10`，行为一致。

## rv-4 CLI 覆盖可见性（参考）

`rv-4-daemon-live-limit.txt` / `rv-4-backlog-daemon-cli-override.png`：`--concurrency` 覆盖时页面显示 `并行上限 5（daemon 命令行 --concurrency；配置文件 10 未生效）`。该组不在 `required_for_acceptance` 内。

## 测试与门禁

- `uv run pytest -o addopts=""` 相关六文件：137 passed。
- `CI=true just test`：5112 passed, 1 skipped。
- `just lint --full`：通过。
- 未新增迁移、未新增第三方依赖（`git show --numstat 1142ecb8 -- pyproject.toml uv.lock frontend-public/package.json` 为空）。

## 已知边界

1. 假 gh 不实现 PR 创建/合并，因此假 agent 结束后发布阶段必然失败、Issue 被判 `agent/failed`；本 PRD 的断言只涉及认领、并发上限与补位时序，该失败不影响断言，也不是真实 GitHub 行为的模拟失败。
2. 为把"进程结束即释放槽位"观察清楚，隔离配置把 `max_recovery_attempts` 设为 0、关闭 agent 回退；生产默认值不变。
3. 证据 tree `bdb67a92` 对应代码+文档补齐提交；PRD 归档提交会再改 tree，差异仅限 `tasks/` 下的 PRD 与证据说明，不含 `src/ tests/ docs/`。
