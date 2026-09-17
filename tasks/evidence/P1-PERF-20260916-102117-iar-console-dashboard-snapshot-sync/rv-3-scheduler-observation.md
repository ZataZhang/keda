# rv-3 调度观察记录（设置持久化与周期一致性）

- 真实入口：`IAR_CONFIG=/tmp/iar-rv-monitor/config.toml uv run iar console --port 8313 --no-browser`，页面 http://127.0.0.1:8313/app/dashboard/
- console 日志：`/tmp/rv-console-2.log`
- 观察窗口长度：75s（等于设置里的 1 分钟间隔 + 余量）

## 时间线（ISO 时间戳）

- 2026-09-16T17:58:28.051Z 启动观察：console 日志中已有 2 条同步记录
- 2026-09-16T17:58:29.218Z 面板初始设置：{"sync_enabled":false,"sync_interval_seconds":60,"updated_at":"2026-09-16T17:55:39+00:00"}
- 2026-09-16T17:58:29.734Z 界面「开启 + 1 分钟」后 settings={"sync_enabled":true,"sync_interval_seconds":60,"updated_at":"2026-09-16T17:58:29+00:00"}（PATCH→落库→GET 读回耗时 516ms）；面板选中态已断言为 1 分钟
- 2026-09-16T17:58:29.757Z 启用窗口开始，当前 scanned_at=2026-09-16T17:57:56+00:00
- 2026-09-16T17:59:31.897Z 观察到相邻周期间隔 60s（首条为 PATCH wake 触发的即时周期，其后按设置的 60s 节奏）
- 2026-09-16T17:59:31.901Z 自动同步记录：2026-09-17 01:58:29 - backend.core.use_cases.monitor_snapshots - INFO - monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
- 2026-09-16T17:59:31.901Z 自动同步记录：2026-09-17 01:59:29 - backend.core.use_cases.monitor_snapshots - INFO - monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
- 2026-09-16T17:59:32.134Z 界面「关闭」后 settings={"sync_enabled":false,"sync_interval_seconds":60,"updated_at":"2026-09-16T17:59:32+00:00"}
- 2026-09-16T18:01:42.346Z 关闭窗口 130s 内新增自动同步记录=0，scanned_at 是否变化=true
- 2026-09-16T18:01:50.071Z 手动刷新成功：scanned_at 2026-09-16T17:59:39+00:00 → 2026-09-16T18:01:49+00:00
- 2026-09-16T18:01:51.381Z 关闭态面板复核：开关选中「关闭」、间隔仍为 1 分钟

## 原始自动同步记录（console 日志摘录）
```text
monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
monitor_snapshots.py:652 - Monitor sync cycle: refreshing 1 repositories (interval 60s)
```

## 重启后设置保持（真实进程重启复核）

- 重启前 GET：`{"sync_enabled":false,"sync_interval_seconds":60,"updated_at":"2026-09-16T17:59:32+00:00"}`
- 重启后 GET：`{"sync_enabled":false,"sync_interval_seconds":60,"updated_at":"2026-09-16T17:59:32+00:00"}`（进程 PID 已变，值逐字段一致）
- 重启后日志中的 `Monitor sync` 记录数：0（关闭态不触发首扫，也没有周期扫描）
- 重启后日志头部：
  ```text
  iar console listening on http://127.0.0.1:8313/ (Ctrl+C to stop)
  INFO:     Started server process [37842]
  INFO:     Waiting for application startup.
  INFO:     Application startup complete.
  ```

## negative control（实现前代码上的同一请求）

```text
=== 实现前：main @ 0d36877 ===
PATCH /api/v1/agent-runner/console/monitor/settings -> 404
GET   /api/v1/agent-runner/console/monitor/settings -> 404
GET   /api/v1/agent-runner/overview/snapshots -> 404
VERDICT: endpoints absent (pre-implementation behavior)

=== 实现后：perf/iar-console-dashboard-snapshot-sync（本分支工作区）===
PATCH /api/v1/agent-runner/console/monitor/settings -> 200
GET   /api/v1/agent-runner/console/monitor/settings -> 200
GET   /api/v1/agent-runner/overview/snapshots -> 200
VERDICT: endpoints exist (post-implementation behavior)
```

> 复现命令：`PYTHONPATH=<树>/src .venv/bin/python tasks/evidence/<prd-stem>/scripts/rv3_negative_control.py`
> （脚本与输出全文见同次执行记录；实现前树由 `git archive main` 解出到 `/tmp/keda-rv1-baseline`。）

## 判读

- **设置即时生效**：界面「开启 + 1 分钟」后 516ms 内 `GET` 读回 `sync_interval_seconds=60` 且 `sync_enabled=true`；
  面板选中态由脚本用计算样式断言（唯一非白底按钮 = 1 分钟），截图与该断言同一次采集。
- **wake 生效**：PATCH 的同一秒（01:58:29）就打出第一条周期记录，说明保存后的 wake 打断了旧的 300s 等待窗口。
- **周期与设置一致**：随后两条周期记录为 01:58:29 与 01:59:29，**相差 60s**，与界面设置一致。
- **关闭后零自动同步**：关闭窗口 130s（≈2 个 60s 周期）内新增 `Monitor sync cycle` 记录为 0
  （判定依据是周期日志计数本身；窗口内的 `scanned_at` 仍变化一次，成因是**关闭前** 01:59:29 已派发的在途扫描
  在数秒后完成落库——与 PRD「已在途扫描不取消」一致，且此后再无新调度）。
- **手动刷新仍可用**：关闭自动同步后点「刷新全部」，`scanned_at` 由 17:59:39 更新为 18:01:49。
- **重启保持**：进程重启后 `GET` 返回同一份设置，且重启后 0 条自动同步记录。
- **negative control**：实现前代码上三个新增端点全部 404，实现后全部 200。
