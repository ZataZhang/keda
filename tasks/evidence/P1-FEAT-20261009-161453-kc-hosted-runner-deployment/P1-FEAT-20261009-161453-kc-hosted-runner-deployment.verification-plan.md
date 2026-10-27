# 验证计划与执行状态

本计划对应 Issue #265 和 canonical PRD 的 4 个 Realistic Validation 检查点。四项均已按 manifest 命令真实运行并为本地子项保存红态与绿态证据；缺少 staging 资源的完整 oracle 仍保持未完成。

| RV | 当前状态 | 本次执行与证据 | 尚未完成的真实验证 |
|---|---|---|---|
| rv-1 | **本地 Compose/回归子项通过；整体未完成。** | `scripts/rv-1-run-local.sh` 先注入架构依赖、Docker 依赖和 host-global state mount 负控，再重跑绿态。真实 Compose parser、repo 专属状态卷、缺失 auth 路径红控和隔离 project `down` 见 `rv-1-compose-config.txt`、`rv-1-stop-compose-interpolation-*.txt`；托管回归 160 passed。 | 客户专用 VM、打包镜像真实启动与 inspect、tmpfs handoff/stop 生命周期、第二客户身份越权负例。 |
| rv-2 | **本地维护/CLI 子项通过；真实数据保留 oracle 未完成。** | `scripts/rv-2-run-local.sh` 注入活动 worktree、低磁盘、日志删除失效、GC 参数和文档故障；维护/worktree/container/CLI 回归 244 passed。真实临时 Git/bare-origin cleanup preview 保留远端分支；隔离 Docker CLI dry-run/apply/fresh probe 仅删掉本脚本创建的 dangling image。文档/Skill、`mkdocs --strict` 和 Skill guard 通过。 | hosted daemon pass 后过期 Issue log/SQLite 摘要删除及 audit/config/queue/credentials 保留的 fresh-state probe；安全 worktree apply；真实低磁盘恢复循环。 |
| rv-3 | **本地 Compose 契约通过；staging CI 未执行。** | `scripts/rv-3-run-local.sh` 先注入 PostgreSQL sidecar 使单 runner 约束失败，再运行真实 Compose contract tests（2 passed），见 `rv-3-compose-negative-red.txt` 与 `rv-3-compose-contract-green.txt`。 | staging PR、GitHub Actions 数据库 service、真实集成测试和故意失败的 required-check/merge-gate 验证。 |
| rv-4 | **本地 hosted API、生产前端和 Linux 真实浏览器子流程通过；staging oracle 未完成。** | `scripts/rv-4-run-local.sh` 的完整 manifest 命令复跑后端/本机边界 62 passed、2 passed，ESLint、TypeScript 与生产静态构建通过。Linux 检查以同步前台 `docker run --rm` 运行真实 `kc console`，确认 `runner_auth_root` 是 tmpfs；Chromium 负控先使无 session 凭据面板可见断言超时，再真实走完访客拒绝、一次性引导、secret 不回显、错误口令拒绝、正确口令登录和本机模式回退。`rv-4-linux-entrypoint-check.txt` 记录容器退出状态和 15 秒浏览器生命周期；`rv-4-run-local-result.txt` 记录整条 manifest 命令的总耗时并确认低于 300 秒。浏览器日志、单项终端输出与 6 张真实入口截图见 `rv-4-console-onboarding.md`、`rv-4-console-onboarding-run.txt`、`rv-4-local-console-run.txt` 及 `rv-4-*.png`。 | 客户 HTTPS URL/VM、真实 disposable PAT/provider key、provider 计费调用、上游 revoke、跨客户隔离、删除本地副本后 runner 重启与凭据失效。 |

## 红绿判别力

每项的 manifest `negative_control` 与 `expected_fail` 描述可观察故障。rv-1 注入违规依赖和宿主全局 state mount；rv-2 注入活动 worktree 删除、低磁盘准入、日志删除失效、互斥参数和文档缺失；rv-3 注入 PostgreSQL sidecar；rv-4 在真实 Linux Chromium 中断言未认证用户可见凭据面板，必须超时变红后才执行 green browser flow。所有负控文件和绿态文件都按单项分开保存。完整 staging 负例仍未执行。

## 本次重跑命令

- `bash tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/scripts/rv-1-run-local.sh`
- `bash tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/scripts/rv-2-run-local.sh`
- `bash tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/scripts/rv-3-run-local.sh`
- `bash tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/scripts/rv-4-run-local.sh`
- `env -u VIRTUAL_ENV UV_CACHE_DIR=/private/tmp/keda-issue265-uv-cache uv run python tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/scripts/rv-validate-manifest.py`

manifest 校验器以 canonical PRD 提取的 4 个 checklist item 验证 `evidence.json`，确认 4 个整数编号组及 40 个引用文件全部存在且编号一致。脚本和临时设置都留在 evidence `scripts/` 目录，不进入代码变更。

## 边界与剩余风险

- 证据中的 repo、登录口令和 provider key 都是虚构 fixture；bootstrap code 仅在浏览器脚本进程间的内存环境变量中传递，没有落入证据文件。
- RV-4 是 loopback HTTP、隔离 Linux 容器和本地 tmpfs 的浏览器验证，不等价于客户 HTTPS staging、真实 provider、跨客户 OS 身份或上游 revoke。
- 未运行 `just test all`。本次按 PRD 执行四项真实验证计划及 manifest 结构校验；验收清单与 Human-Confirmed 状态不因本地子项通过而改变。
- canonical PRD 仍在 `tasks/archive/`；没有移动或勾选 PRD 项。归档不表示 Realistic Validation 或人工验收完成。
