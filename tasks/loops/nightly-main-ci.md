---
id: nightly-main-ci
schedule: "0 1 * * *"
repo_id: keda
managed_by: agent-daemon
issue_type: bug
agent: auto
labels:
  - scope/ci
publish_prd: false
queue_ready: true
run_now: false
timezone: Asia/Shanghai
priority: P1
slug: nightly-main-ci
skip_when: last_ci_status=success,none
pre_command: 'status="$(gh run list --branch main --status completed --limit 1 --json conclusion -q ".[] | .conclusion" 2>/dev/null)" && if [ -n "$status" ]; then printf "last_ci_status=%s\n" "$status"; else printf "last_ci_status=none\n"; fi || printf "last_ci_status=unavailable\n"'
---

# Main CI 夜间巡检 — {{date}}

这是 `keda` 仓库的每日 main CI 巡检任务。最近一次 main workflow 的结论是：
`{{last_ci_status}}`。

## 目标

确认 main 的最新 GitHub Actions workflow 是否失败；如果失败，定位根因并提交最小
修复 PR。如果没有可操作的问题，不要为了产生改动而制造 PR。

## 执行规则

1. 先运行 `gh run list --branch main --limit 5`，再用
   `gh run view <run-id> --log-failed` 查看失败 job 的日志。
2. `failure`、`timed_out`、`cancelled` 或 `action_required` 时，复现真实失败入口，
   修复源代码 / 测试 / 配置，并运行与改动匹配的验证；本仓库日常验证优先使用
   `just test`，必要时补跑对应 lint 或文档构建。
3. 目前已知的硬门禁问题是单个 Python 文件非空行超过 1000 行；如果仍然命中，
   优先拆分触发门禁的文件，不要把文件加入 allowlist 绕过检查。
4. 最近一次 main run 为 `success` 或 `none` 时，调度器已通过 `skip_when` 直接跳过，不会
   生成本任务；能看到本 Issue 说明需要排查（含 `unavailable`：无法读取 CI 状态）。不要主动
   扩大范围做重构或依赖升级。

## 完成条件

- 有失败：修复、验证并创建 draft PR，PR 描述中引用本 Issue 和失败 run。
- 若复查发现最新 run 其实已经恢复：在 Issue 留下检查结果并关闭 Issue，不创建 PR。
- 如果外部 GitHub 权限或 workflow 状态无法确认，保留 Issue 为 blocked，说明需要的
  人工动作，不要猜测结论。
