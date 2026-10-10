# 验证计划

本计划依据当前 canonical PRD 的 rv-1 至 rv-4 编写。它记录已执行的局部验证，也明确未完成的 staging 边界；不把本地测试替代为托管产品验收。

| RV | 当前状态 | 实际入口与证据 | 负控与预期失败 |
|---|---|---|---|
| rv-1 | **局部通过，整体未通过**。 | `uv run python hooks/shared/check_architecture.py` 扫描 339 个文件通过；依赖清单无变化；输出见 `rv-1-architecture-green.txt`、`rv-1-dependencies-green.txt`。 | 架构反向依赖与 Docker SDK 依赖负控分别注入临时 fixture 并以 exit 1 失败，见 `rv-1-architecture-red.txt`、`rv-1-dependency-red.txt`。隔离 VM、Compose config/docker inspect、跨客户读权限和 tmpfs/provider auth handoff 未执行。当前 Compose 仍把 GH_TOKEN 暴露为环境变量。 |
| rv-2 | **本地安全清理和准入通过；真实 apply/fresh probe 未完成**。 | 12 个相关模块（含 PRD 指定六个模块）关闭 testmon 后 `239 passed`。另有 worktree cleanup、summary retention、disk gate、日志 retention、Compose/GC engine 和默认关闭的独立输出。真实 `kc worktree cleanup --dry-run` 输出 `would_delete=1, skipped=19`；真实 `kc container gc --dry-run` 扫描 13 项、eligible=0。`rv-2-tree-fingerprint.txt` 是当前工作树指纹，不是最终提交树。 | 活动 worktree 守卫、禁止过期日志删除、错误磁盘阈值配置和 `--apply --dry-run` 互斥参数均按预期变红。宿主 Docker 没有 eligible 候选，故没有执行 apply；也没有从新 CLI 进程 probe 删除/保留集合、审计和 credentials。 |
| rv-3 | **staging required-check 未执行；仅 runner Compose 子项通过**。 | `test_runner_compose_is_single_service_without_public_port_or_socket` 通过；只证明 runner 没有 DB sidecar、公开端口或 Docker socket。 | 临时注入 PostgreSQL sidecar 后单服务契约负控 exit 1。没有 staging PR、DB-backed Actions service job、故意失败的测试、required check 红态或 merge gate 证据。 |
| rv-4 | **未实现 / 未通过**。 | 真实未认证请求当前返回 HTTP 200，字段只有 session keys 的名字；本机 Console loopback/no-op 兼容测试通过。分别见 `rv-4-auth-red.txt` 与 `rv-4-local-console-green.txt`。 | 该红态本身是缺陷证据；没有 hosted auth 修复后的绿态。bootstrap replay、session expiry、PAT/provider 凭据校验、费用确认、secret 加密/不回显、tmpfs handoff/清除、上游 revoke 和真实 HTTPS 浏览器流程均未执行。 |

## 本地门禁

- `UV_CACHE_DIR=/private/tmp/keda-issue265-uv-cache PRE_COMMIT_HOME=/private/tmp/keda-issue265-precommit just lint --reuse`：通过。
- `SKIP=check-test-flag UV_CACHE_DIR=... PRE_COMMIT_HOME=... just lint --full`：除跳过 freshness 守卫外的全量 hooks 通过。正常 `just lint` 被 `.last_tested_commit` 对当前 working tree 的过期 fingerprint 拒绝；没有手动重写标记。
- 最后一次 `just test`（testmon 增量档）：11 passed、10 failed、4 deselected。10 个失败都在 `tests/test_cli_config_migrate.py`，因为 sandbox 禁止 `psutil.process_iter()` 调用 macOS `sysctl()`，触发 `process scanner unavailable` 并按设计 fail-closed。关闭 testmon 的 PRD 相关模块单独运行为 239 passed。相关迁移源文件与测试未修改。详见 evidence report。
- `UV_CACHE_DIR=... uv run mkdocs build --strict`：exit 0；构建器仍提示仓库既有未导航 prototype 页和锚点信息。
- `git diff --check`：提交前最后检查。

## 交付边界

部署 Console 与密钥生命周期不是本地资源维护能力的一部分：当前没有 hosted auth、bootstrap-token CLI、credential encryption/API、provider adapters、tmpfs mount 或 hosted frontend。因此 rv-1/rv-4 仍开放，rv-3 staging CI 和 rv-2 实际 apply/fresh probe 也缺失。保留相应 acceptance 项未勾选，不移动或归档 PRD。
