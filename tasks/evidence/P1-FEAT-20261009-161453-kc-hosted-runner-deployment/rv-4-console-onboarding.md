# rv-4 托管 Console onboarding 真实浏览器视觉证据（本地沙箱档）

- 捕获时间(UTC)：2026-10-11T07:56:43+00:00
- 入口：`kc console`（FastAPI 同源托管 production 静态导出，`frontend-public` `next build output:export` 产物，经 `just console-sync` 同步）+ 真实浏览器（Playwright → Chromium headless，viewport 1280x900）。
- 隔离：`kc console` 在 Docker Linux 容器内运行，`runner_auth_root` 指向真实 `/run/kedacode/runner-auth` tmpfs；临时 `HOME`、KEDACODE_CONFIG、DB/密文/密钥文件均在容器可写层，容器结束后删除；浏览器通过同容器 loopback 访问，不触碰真实 `~/.kedacode`。
- 数据：仓库、口令、API key 均为虚构（`fictional-org/fictional-demo-repo`、`sk-ant-api03-FICTIONAL-NOT-A-REAL-KEY-*`）；一次性引导码经环境变量注入浏览器，从未写入任何证据文件或日志。
- 边界披露：本证据为 loopback HTTP + Linux tmpfs 本地容器档，非客户 HTTPS staging 档。PRD rv-4 的完整 oracle（真实 HTTPS 反代、disposable 真实凭据、真实模型计费调用、上游 revoke、第二客户 VM 越权、与原型图并排人审）仍需 staging + 人工确认，acceptance 项保持 `[~]`；本组截图把 `[~]` 项中“production frontend build + 真实 hosted auth/API 路由 + 真实加密存储”子链从纯文本证据升级为真实浏览器视觉证据。

## 运行日志（脚本 stdout 原样）

```text
[api] GET /api/hosted/status -> 200 bootstrapped=False
[api] GET /api/hosted/credentials (无会话) -> 401（期望 401）
browser: bundled-chromium 153.0.8010.12
  screenshot -> rv-4-guest-login-gate.png
  [UI] 未认证 /app/settings/ -> 重定向 /login/，凭据面板不可见（红态对照）
  screenshot -> rv-4-bootstrap-form.png
  [UI] /login 渲染『托管 Console 首次引导』表单（空码/空口令，不含 secret）
  [UI] 引导成功 -> 会话建立 -> /app/settings 出现『托管 Console 运行凭据』面板
  [UI] 保存后页面文本/HTML 不含引导码、API key、管理员口令（不回显断言通过）
  screenshot -> rv-4-console-onboarding.png
  [UI] 主证据截图 rv-4-console-onboarding.png（虚构仓库 + 未验证状态，无 secret）
  screenshot -> rv-4-hosted-login-form.png
  [UI] 已引导后 /login 渲染口令登录表单（单管理员、无注册入口）
  screenshot -> rv-4-wrong-password-rejected.png
  [UI] 错误口令提交 -> 刷新回 /login 登录表单，未获得会话（红态对照）
[api] POST /api/auth/login（错误口令）-> 401（期望 401）
  [UI] 正确口令登录成功 -> /app/dashboard（会话闭环）
[api] POST /api/hosted/bootstrap（已消费引导码重放）-> 401（期望 401）
```

## 截图清单（同目录）

| 文件 | 内容 | 性质 |
|---|---|---|
| rv-4-guest-login-gate.png | 未认证访问 /app/settings/ 被重定向回登录闸门，凭据面板不可见 | 负例（红态） |
| rv-4-bootstrap-form.png | 托管未引导时 /login 渲染首次引导表单 | 正例 |
| rv-4-console-onboarding.png | 引导+登录后 /app/settings 凭据面板：来源指引、虚构凭据行（未验证）、不回显断言 | 正例（主证据） |
| rv-4-hosted-login-form.png | 已引导后新浏览器会话的口令登录表单 | 正例 |
| rv-4-wrong-password-rejected.png | 错误口令提交后被 401 拦截器刷新回登录表单（协议层同时断言 POST /api/auth/login→401） | 负例（红态） |
| rv-4-local-mode-login.png | hosted.enabled=false 时 /login 回退本机 no-op 表单 | 边界负例 |

另附协议级断言：无会话 `GET /api/hosted/credentials` → 401；已消费引导码重放 `POST /api/hosted/bootstrap` → 401；本机模式 `GET /api/hosted/status` → 404（见日志）。


## 附：本机模式（hosted.enabled=false）边界运行

```text
[api] 本机模式 GET /api/hosted/status -> 404（期望 404，路由未挂载）
browser: bundled-chromium 153.0.8010.12
  screenshot -> rv-4-local-mode-login.png
  [UI] 本机模式 /login 渲染原 identifier+password no-op 表单，托管 UI 不复现
```
