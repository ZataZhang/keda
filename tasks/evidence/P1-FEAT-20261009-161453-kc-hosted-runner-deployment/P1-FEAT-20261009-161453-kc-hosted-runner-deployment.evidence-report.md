# 人审导航 / Human Review Navigation

本地证据仅供当前工作树评审；原始图片和终端输出不进入代码 diff。当前没有 staging URL、Pull Request 或 CI run，本报告链接到 [GitHub Issue #265](https://github.com/ZataZhang/keda/issues/265)。截图是从真实 `kc console` 路由经 Chromium 操作采集的 `real user flow`；它们不是客户 HTTPS staging 证明。仓库中没有找到 PRD 指定的托管 Runner 原型，因此不能做原型与实现并排比较，D-03 与 Console 人工审阅仍待人工完成。

| 人需要看的结果 | 本地呈递与打开方式 | 10 秒自检 |
|---|---|---|
| 托管 GC 预览候选与安全 worktree 保留原因 | `/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-2-gc-apply-fresh-probe.txt`、`rv-2-worktree-cli-preview.txt`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-2-gc-apply-fresh-probe.txt"` | 隔离 Docker 真实 CLI 只删除脚本创建的一个 dangling image：`eligible=1 deleted=1 skipped=29 failed=0`；新进程报告 `eligible=0`；Git probe 因远端分支仍存在而保留 worktree。该局部结果不证明完整日志/SQLite 清理策略。 |
| 托管 Console 首次设置、凭据状态与登录边界 | `/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-console-onboarding.png`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-console-onboarding.png"`；[本地图片](rv-4-console-onboarding.png) | 虚构仓库的 Agent key 行显示“未验证”，页面只显示状态/长度；访客与错误口令不能进入凭据面板。图中不含引导码、口令或真实凭据。 |
| GC apply 的安全范围和可读性 | `/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-2-gc-apply-fresh-probe.txt`；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-2-gc-apply-fresh-probe.txt"` | apply 只删候选临时镜像，不删除 29 个 Buildx cache；新 Docker 进程确认镜像已不存在。PRD 指出的 daemon log/SQLite 保留、audit/config/credential fresh-state 仍未证明。 |

## rv-4 浏览器证据图册（本地图片）

以下图片来自真实 `kc console` 页面和同一 1280×900 Chromium viewport。打开任一图片可单独检查；截图均使用虚构 repo、key 与密码。

### Hosted Console 已保存凭据状态

![Hosted Console 凭据状态：虚构仓库、未验证状态、无 secret 回显](rv-4-console-onboarding.png)

本地图片；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-console-onboarding.png"`

### 未引导时的访客入口

![未认证访问设置页后显示首次引导闸门，未显示凭据面板](rv-4-guest-login-gate.png)

本地图片；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-guest-login-gate.png"`

### 首次引导表单

![托管 Console 首次引导表单，输入框为空且没有 secret](rv-4-bootstrap-form.png)

本地图片；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-bootstrap-form.png"`

### 已引导后的管理员登录表单

![已引导托管 Console 的管理员口令登录表单](rv-4-hosted-login-form.png)

本地图片；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-hosted-login-form.png"`

### 错误口令拒绝

![错误管理员口令被拒绝，仍停留在登录页](rv-4-wrong-password-rejected.png)

本地图片；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-wrong-password-rejected.png"`

### 本机模式回退

![hosted 关闭时恢复本机 Console identifier 与 password 登录表单](rv-4-local-mode-login.png)

本地图片；打开：`open "/Users/zata/code/keda/.iar-worktrees/issue-265/tasks/evidence/P1-FEAT-20261009-161453-kc-hosted-runner-deployment/rv-4-local-mode-login.png"`

## 自动验证摘要

- `evidence.json` 使用 `version: 1`、`language: "zh-CN"`，按 PRD 的 4 个正整数 checklist item 分组；manifest checker 已验证 40 个纯文件名引用均存在，分组文件数为 10 / 9 / 3 / 18。
- RV-1：负控证明反向依赖、Docker 依赖和 host-global state mount 会失败；真实 Compose config 与隔离 `down` 通过；托管回归 160 passed。
- RV-2：活动 worktree、低磁盘、过期日志、互斥参数和文档条款负控均变红；维护回归 244 passed；真实 Git preview 保留 remote-existing worktree；真实 Docker CLI preview/apply/fresh probe 只删除本脚本临时 image；文档、`mkdocs --strict` 与 Skill guard 25 passed。
- RV-3：PostgreSQL sidecar 负控使单 runner Compose oracle 失败；当前真实 Compose contract tests 2 passed。
- RV-4：hosted service/auth/handoff 与本机边界测试 62 passed、2 passed；ESLint、TypeScript、Next production build 通过。Linux 浏览器检查以同步前台 `docker run --rm` 运行真实 `kc console`，tmpfs 和容器退出证据见 `rv-4-container-tmpfs.txt`、`rv-4-linux-entrypoint-check.txt`；先记录未认证凭据面板可见断言的预期红，再完成首次引导、凭据保存/不回显、错误口令拒绝、正确登录和本机模式回退。完整 RV-4 命令运行结果在 `rv-4-run-local-result.txt` 记录并检查总耗时小于 300 秒。
- 所有取证与临时 setup 脚本仅位于本 evidence 目录的 `scripts/` 并被 Git 忽略；`git diff --name-only` 不含 RV 脚本。一次性引导码只在进程内传递，没有写入证据文件。

## 完整 Realistic Validation 尚未完成

本地四项可执行子检查已按 manifest 命令运行；完整 staging oracle 仍未完成：没有客户专用 Linux VM/第二客户身份、专属 HTTPS Console、客户 disposable GitHub/provider 凭据、真实模型计费请求或上游 revoke，也没有 staging PR 的 GitHub Actions service/required-check 绿红结果。RV-2 仍缺 hosted daemon pass 后 raw log/SQLite 摘要删除与 audit/config/queue/credentials 保留的 fresh-state oracle、真实 safe worktree apply，以及真实低磁盘恢复循环。当前浏览器截图没有配套 prototype image，Console 人工审阅仍需 human confirmation。

上述本地证据没有使用客户数据或真实凭据；没有把静态 Compose、单测或 loopback 浏览器结果冒充 staging 行为。PRD 位于 `tasks/archive/P1-FEAT-20261009-161453-kc-hosted-runner-deployment.md`；本次没有移动 PRD 或勾选 `Human-Confirmed` 项。归档不代表 staging 验收或人工验收通过。
