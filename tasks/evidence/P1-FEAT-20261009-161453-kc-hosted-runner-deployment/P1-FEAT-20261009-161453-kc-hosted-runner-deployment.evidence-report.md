# 人审导航与验证状态

## 人审导航

| 需要查看的结果 | 当前证据 | 观察结果 / 限制 |
|---|---|---|
| 托管 GC 预览与 worktree 跳过原因 | `rv-2-worktree-dry-run.txt`、`rv-2-gc-dry-run.txt` | 真实 worktree 预览显示 `would_delete=1, skipped=19`；真实 host GC 为 `Scanned=13 eligible=0 deleted=0 skipped=13 failed=0`。没有 Docker GC eligible 候选，因此未执行 apply。 |
| 清理与磁盘准入回归 | `rv-2-worktree-cleanup-green.txt`、`rv-2-log-cleanup-green.txt`、`rv-2-summary-retention-green.txt`、`rv-2-disk-gate-green.txt`；对应 red 文件与 manifest | 临时 Git worktree、日志文件、SQLite 摘要和容量快照测试有红绿输出；不是一次真实 hosted daemon apply/fresh-process 探测。 |
| runner Compose 边界 | `rv-3-compose-contract-green.txt`、`rv-3-compose-negative-red.txt` | 本地单服务/无端口/无 Docker socket 契约通过，注入 PostgreSQL sidecar 的负控失败；尚无 GitHub Actions DB service required-check staging 结果。 |
| Console 认证 | `rv-4-auth-red.txt`、`rv-4-local-console-green.txt` | 本地 loopback/no-op session 兼容性通过；未认证访问现状返回 200，这是 hosted auth 尚未实现的红色证据。无 staging URL、原型图或 hosted green。 |

原始输出只含各自 RV 项的命令输出；`rv-4-auth-red.txt` 仅打印 HTTP 状态和响应字段名，不包含本机用户值、凭据或客户代码。证据 manifest 位于同目录 `evidence.json`，按 rv-1 至 rv-4 分组；仓库解析器确认 4 组、25 个文件均存在并匹配其 RV 编号。`rv-2-tree-fingerprint.txt` 记录当前工作树指纹，不等同于 runner 生成的最终提交树证据。

## 本次已实现并验证的本地部分

- 托管维护配置默认关闭；启用后 daemon 只在 worker batch 完成后触发 worktree 与日志清理，并按 90 天截止时间分批清理完成的 run/attempt 摘要。
- worktree 继续复用原有安全 cleaner；新增活动标签保护，GitHub Issue 状态查询失败时 fail closed。过期 Issue 原始输出清理限于当前仓库对应 repo id 目录中的普通 `.log` 文件，并拒绝符号链接和路径逃逸。
- 托管 daemon 使用可配置低/高水位：低于低水位时停止启动新 Issue，恢复到高水位后恢复准入；日志输出含 used/free/total 和恢复条件。当前实现也将同一准入检查传给 deliberation 队列。
- runner Compose 配置 Docker JSON log 轮转；`kc container gc` 有真实 dry-run 与互斥参数负控。host GC 仅预览，没有 eligible 候选可安全 apply。
- 相关本地测试：关闭 testmon 后 **239 passed**，覆盖 PRD 指定六个核心模块和本次增加的维护、Compose、daemon、deliberation 测试。

## 门禁与外部验证

- `just lint --reuse` 通过。`just lint --full` 在 `SKIP=check-test-flag` 时通过其余 hook；正常 `just lint` 当前因 `.last_tested_commit` 的 working tree fingerprint 过期而失败。没有手工更新测试标记。
- 最后一次 `just test`（testmon 增量档）为 11 passed、10 failed、4 deselected。10 个失败全部在未修改的 `tests/test_cli_config_migrate.py`，首个 actionable error 为 `cannot verify that no KedaCode process is running (process scanner unavailable)`；sandbox 拒绝 `psutil.process_iter()` 触发的 macOS `sysctl()`，迁移安全检查按设计 fail closed。关闭 testmon 的 PRD 相关模块独立回归为 239 passed。没有放宽检查或修改守卫。
- `uv run mkdocs build --strict` 退出 0；仍有仓库既有未导航 prototype 页面和链接锚点信息提示。
- rv-1 staging 隔离 VM、真实 `docker inspect`、credential/tmpfs handoff 和跨客户权限负例未执行。当前 Compose 中仍有 GH_TOKEN environment 映射，不能声称满足 hosted secret 边界。
- rv-3 没有 staging repo/PR 的 DB-backed GitHub Actions service 绿/红 required-check 与 merge gate 结果。
- rv-4 的 hosted bootstrap/auth、credential API、加密存储、PAT/provider 验证、真实模型费用确认、tmpfs runner handoff、轮换/删除、上游 revoke 和 production browser screenshots 均未实现或执行。
- D-01、D-02、D-03 与 GC / Console 人读呈递仍是人工决定，保持未勾选。

## 交付状态

这次恢复完成的是本地资源维护、磁盘准入、容器 GC 的一部分实现和红绿证据修复，不是完整托管 Runner 产品交付。PRD 保留在 `tasks/pending/`；Human-Confirmed 项保持开放；没有制作 PR、提交或归档证据。归档仍由 runner 的独立 verifier 门禁决定。
