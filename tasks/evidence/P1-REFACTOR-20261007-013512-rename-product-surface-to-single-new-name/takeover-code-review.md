# 接管后的代码审查与验证

2026-10-08：用户明确接受证据呈递缺口，要求以代码审查决定交付；本报告是代码审查记录，不代表旧 RV 证据包已在新代码树完整重采，也不声明独立 PRD verifier PASS。

已停止原九小时执行循环、备份工作树，并在独立 integration worktree 整合 main 1d03aa3712c990a3d629dfd006ea8629d92225c9。

## 修复

- 环境变量冲突提醒不再打印任何值，避免 HMAC secret 泄露。
- 未初始化 Git 仓库的配置迁移在本机步骤成功后跳过仓库步骤，正式与预演均返回 0。
- 容器 up/down/logs 统一注入生效状态目录，保留环境净化。
- 合并保留 main 时间戳路由、环境净化、首页 Backlog 导航和事实 PR 正文默认值。

## 当前验证

- 两个分工审查均给出 code review APPROVE，未发现代码合并阻塞。
- 迁移、身份与状态目录测试：74 passed；容器 targeted：16 passed；主干整合 targeted：80 passed。
- 最终 `GUARD_UPDATE_ACK=1 just test all`：3436 passed / 1 skipped，175.80 秒；前置完整 lint 通过。ACK 仅用于合入主干已经存在的 guard 注释更新，相对目标 main 的 guard 无差异。
- `uv run mkdocs build --strict`：通过。
- 真实 Docker Compose 配置解析：通过。
- `just lint --reuse`：pylint、架构、规范一致性、文件长度通过；jscpd 报主干已有 Stats 重复片段。按目标 main 的改动行做诊断后另报 CLI recover/blocked-continue 的既有相同流程（本次仅 iar 文案变 kc）。未改 hook 或降低阈值，完整 reuse 不声明通过。

## 限制

- 原任务的 RV 文件和已勾选清单是历史执行记录，不可当成本次修复后完整 verifier PASS；本次采用用户授权的代码审查交付。
- mounted 旧状态目录时 dry-run 未提前报告 EXDEV；正式迁移拒绝且不会跨盘复制或删除数据。
- 新安装器需与包含 kc 的新版 wheel 同步发布，当前旧发布包不含 kc 的安装校验会失败。
- 前端本轮保留生产首页边界及已有 E2E 断言，未重新运行浏览器流程。

## 首轮 CI 后的精准修复

首轮 CI 的 12 个测试失败已在 fresh HOME、外部新名 PRD skill 路径下原样复现。会话 fixture 统一隔离/恢复双名变量；状态路径测试分别精确覆盖新机器与仅旧目录兼容，路径优先级测试保留新名优先及显式最优先。包含双名 CONFIG 污染的相关 207 项测试通过。未改生产逻辑或弱化失败契约。

stock-python 安装 CI 在非发布事件安装当前 checkout 并保留低版本 ambient Python；release 继续经真实 PyPI installer 安装对应 tag。之前用旧公共 wheel 验证新 kc 的版本错位已修正，真实 Ubuntu/macOS 档仍由新 HEAD CI 判定。
