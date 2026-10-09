# P1-FEAT：KedaCode 托管 Runner 部署与自动资源清理

- GitHub Issue: https://github.com/ZataZhang/keda/issues/257

> 交付前置：✅ 无上游依赖，可独立执行
> 验收状态：⬜ 未开工

> **Feature Overview（功能一览）**
> - FR-1：现有本地 daemon 继续免费、照常运行；托管部署作为可选运维方式。
> - FR-2：托管 runner 只执行代码任务，不在 runner 容器内启动项目的数据库或其他中间件；这些集成验证由 CI 执行。
> - FR-3：托管部署按客户隔离代码、GitHub 凭据和模型凭据，并通过受保护的宿主机目录提供给 runner。
> - FR-4：托管模式自动回收满足安全条件的过期 worktree、原始日志和运行历史，限制容器日志、镜像缓存和磁盘增长。
> - FR-5：磁盘空间低于安全线时停止领取新任务、保留运行中的任务并提示运营者。
> - FR-6：不建 SaaS 控制面、门户、集中式服务端数据库或自动计费；首期由运营者按部署收取基础设施费用。

# Part A · Review Layer（评审层）

## 1. Problem And Interpretation

### Problem Statement

KedaCode 已能把单仓库 daemon 放进 Docker，但现有容器化方案主要面向开发者自己的电脑：目标仓库从本机路径挂载，认证从本机 agent CLI 快照导入，操作者通过本机 Docker 命令管理。

要将 daemon 部署在远程机器并为客户代管，当前缺少客户隔离的部署边界、适合无人值守的资源回收政策，以及“项目数据库测试交给 CI”的明确运行约定。现有 Issue 原始输出写到仓库挂载目录下的 `logs/agent-runner/issues/`，容器 Compose 没有日志轮转规则；daemon 的运行历史和审计记录另存于挂载的本地 SQLite 文件。应用日志已有 14 天保留策略，但它不覆盖上述所有数据。worktree 清理目前要求人工运行 `kc worktree cleanup`。

### Interpretation (解读回显)

我把这项需求读作：在现有 Docker runner 上增加面向托管部署的运维约定和自动资源卫生能力。客户仍通过 GitHub Issue 提交代码任务、通过 PR/CI 验收；KedaCode 容器只运行 agent CLI 与代码工具链，不启动客户项目的数据库、Redis、消息队列或其他中间件。

行为样例中，活动任务不被清理；当项目必须使用数据库测试时，由 PR 的 CI 工作流提供对应服务并报告检查状态。表中每一行的动作和可观察结果将成为对应的验收 oracle；修正行为单元格即修正验收标准。

| 验证方式 | 输入 / 操作 | 期望观察到的结果 |
|---|---|---|
| 🤖 自动验证 | 用户继续在自己的电脑运行 `kc daemon` | 不需要托管服务或付费账户；本地执行路径和任务状态语义保持不变 |
| 🤖 自动验证 | 托管 runner 处理一个代码 Issue；代码验证需要 PostgreSQL 或 Redis | runner 不安装、不启动、不连接项目中间件；PR 的 CI 检查使用工作流服务并成为合并前检查 |
| 👀 人审 + 自动验证 | 任务已合并且远端分支已删除；运营者查看资源清理预览后运行清理 | 只有关闭 Issue、无远端分支、干净且已合并的托管 worktree 会被移除；活动、脏或未合并的 worktree 明确跳过并说明原因 |
| 🤖 自动验证 | 宿主机剩余空间低于配置安全线，daemon 正在处理任务 | 当前任务继续；daemon 不领取下一项任务并输出可诊断的磁盘空间提示 |

### 我默默定了这些

- 首个托管试点采用“每个客户一台专用 Linux VM、每个仓库一个 runner 容器”的隔离边界；不共用客户工作目录、认证目录或状态库。
- 推荐客户提供 GitHub 与模型供应商凭据，基础设施费和模型用量分开结算；此费用边界仍待 D-01 确认。
- daemon 自己的 SQLite 是单机运行元数据，不是客户项目数据库中间件；保留必要状态，只对运行历史按已确认保留期清理。
- 首期仅托管代码任务的执行和 PR 流程，不替客户执行有状态数据库迁移，也不自动合并 PR。
- 自动清理默认只作用于显式开启托管维护配置的部署；普通本地 `kc daemon` 保持原样。

### 我理解为不做

- 不做多租户共享主机、集中式 SaaS 控制面、门户、在线注册或自动计费。
- 不做在 runner 容器中为每个项目构建 PostgreSQL/Redis 等服务编排，也不把这些依赖从客户项目中“猜测”出来。
- 不自动删除未合并、未关闭、仍有远端分支或存在未提交改动的 worktree；不自动清除认证和 agent 会话数据。

**可证伪的需求解读：** 这是 KedaCode 现有 daemon 的可选托管运行方式，代码仍通过客户自己的 GitHub 仓库和 PR 流程交付；项目集成测试在 CI 中运行。它不是把 KedaCode 改造成云端 IDE，也不是集中运行客户数据库的 PaaS。若一个任务必须依赖本地不可替代的数据库服务才能执行，它不属于首期托管执行目标，必须在 CI 或客户自己的环境补齐验证。托管模式与本地免费路径并存，不强制迁移或改变本地行为。

### What The User Gets

- 个人与团队可继续免费在自己的电脑运行 KedaCode daemon。
- 需要持续在线执行时，可让 KedaCode 运营者把 runner 部署在隔离的云主机上；客户仍从 GitHub Issue 发起工作，从 PR 和 CI 查看结果。
- 客户项目所需数据库测试由客户仓库的 GitHub Actions 等 CI 工作流启动，不占用 daemon 容器，也不需要在宿主机上长期运行中间件。
- 运营者得到明确的空间清理和磁盘保护策略，减少因长期驻留而人工登录清理的频率。
- 推荐以部署与维护基础设施为单位收费，模型供应商账户与用量由客户自行管理和支付；具体费用边界待 D-01 确认。

### Measurable Objectives

1. 不启用托管维护配置时，本地 `kc daemon` 的任务处理与现有测试结果一致。
2. runner 的 Compose 定义没有数据库/中间件服务、宿主 Docker socket 挂载或对外监听端口；执行不依赖 KedaCode 中心数据库。
3. 已配置 PostgreSQL/Redis 服务的 CI 工作流能在真实 PR 检查中运行项目集成测试；失败检查保持为失败状态，不被 runner 本地跳过或伪造为通过。
4. 清理预览和实删结果遵循同一组安全规则；脏、未合并、远端仍存在、活动或无法判断状态的 worktree 均保留。
5. 托管维护启用后，Issue 原始日志、运行历史和容器日志不超过各自配置保留策略；可保留的审计、配置、队列状态不会被日志清理误删。
6. 达到磁盘安全线后不再领取新的 Issue；daemon 记录磁盘使用量和恢复条件，当前任务不被强制终止。

## 2. Human Review Map（需要你确认的选择）

### 决策一：首期托管服务的客户隔离与费用边界

推荐“运营者管理的专用 VM，每客户隔离部署；客户提供 GitHub 和模型凭据，模型用量直接由客户向供应商支付，基础设施服务费单独收取”。现有容器是一仓库一个 runner，专用 VM 可沿用该结构，避免首期先建设共享宿主机的跨客户安全隔离。代价是每位客户都有独立 VM 的基础成本，运营者需处理凭据轮换和退订回收。

**请确认：** 首期是否采用专用 VM + 客户自带 GitHub/模型凭据 + 模型用量由客户直接支付？备选是客户自有主机（BYOC），由运营者收部署/维护费。多客户共享 VM 超出本 PRD 范围；若选该项，需先补充跨客户安全隔离设计并重新评估范围。

**验收：** 部署说明明确 VM 所有者、仓库与凭据边界、基础设施费与模型费用的承担方；验证报告证明一个客户无法读取另一客户的仓库、认证目录或 daemon 状态。

### 决策二：自动清理的保留周期与删除边界

推荐将详细 Issue 输出日志保留 14 天、运行/attempt 摘要保留 90 天；审计记录、队列配置、活动状态和客户认证不按日志策略删除。已关闭 Issue 只有在远端分支已删除、worktree 干净且已合并时才可自动删除；镜像仅清理悬空镜像与过期 build cache，不删 Docker volume。退订时停止 daemon、撤销凭据，再按合同与客户确认的导出/删除流程处置该客户全部数据。较短保留期降低磁盘和敏感输出留存，较长保留期便于故障追溯。

**请确认：** 是否接受 14 天原始输出、90 天运行摘要并永久保留审计记录直到退订？可以调整日志/摘要周期或要求退订前先导出数据。

**验收：** 清理预览准确显示拟删内容、时间范围与跳过原因；执行后边界内旧文件/摘要消失，审计、活动任务和未满足安全条件的 worktree 仍可读取。

### 自动门禁，不需要逐项人工审阅

CI 工作流、CLI 参数与 Compose 配置由失败可区分的自动验证覆盖；当前本地运行路径的兼容性通过回归验证。没有新增管理台或客户 UI。

**本次明确不涉及：** 数据库 schema、数据库迁移、计费系统、客户门户、共享宿主机、自动合并，以及前端页面。除以上两项产品选择外，具体 CLI 输出、配置组织和日志轮转参数由执行者按仓库既有模式落实。

## 3. Usage And Impact After Implementation

### 个人开发者

继续使用安装好的 `kc daemon` 和现有 GitHub / agent CLI 配置。在自己机器上运行不需要新增云端账号、容器或费用；本地 daemon 不启用托管专用的自动清理行为。

### 托管服务运营者

在客户专用 Linux VM 上准备客户授权的仓库 checkout 和凭据，按托管部署指南运行 `kc container up --repo <路径> --repo-id <仓库标识>`。通过 GitHub Issue 队列、`kc container logs` 和现有 daemon 状态命令观察任务；使用 dry-run 预览与托管维护配置核对空间回收。项目数据库集成测试由 GitHub PR 的 CI 状态提供结果。

客户退订时，运营者先停止 daemon、撤销 GitHub 和模型凭据，按已确认的导出/删除规则清理客户数据，再释放 VM。

### 托管服务客户（代码任务提交者与 PR 审阅者）

在授权的 GitHub 仓库创建或标记代码 Issue。agent 通过现有 daemon 执行代码、跑配置好的快速本地检查并创建 PR；GitHub Actions 运行所需数据库集成测试。客户从 Issue、PR、CI 检查看到结果并进行代码评审。CI 失败继续显示为失败；KedaCode 不会把“已创建 PR”表述为“已经通过项目验收”。

### 项目维护者与 CI 配置者

为数据库集成测试维护仓库自己的 CI service 配置和必需检查项；daemon 的 `verification_commands` 只列 runner 可执行的快速检查。仓库若没有配置必需的 CI 检查，KedaCode 不推断数据库依赖，也不保证项目集成测试已在云端执行。

### 对现有行为的影响

- 普通本地 `kc daemon`、本地容器化使用和现有 Issue/PR 状态契约继续可用；托管自动维护通过明确配置启用。
- 托管仍沿用一个容器执行一个仓库 daemon 的方式，不要求用户转到新的 SaaS 控制面。
- daemon 自身 SQLite 状态仍位于客户专用宿主机的持久化状态目录中；不新增网络数据库服务。
- 现有日志目录路径与 `LOG_RETENTION_DAYS` 应保持兼容；新增清理必须补上 Issue 原始日志和运行摘要，并明确每类数据的策略。
- 不改变本地验证先于提交的流程；托管仓库应将不能在 runner 内运行的集成测试配置为 CI 检查。

## 4. Requirement Shape

- **actor**：本地 KedaCode 用户、托管服务运营者、托管客户（Issue 提交者和 PR 审阅者）、客户仓库 CI 维护者。
- **trigger**：用户选择让 KedaCode 运营者部署一个常驻 daemon，并要求它持续处理代码 Issue，同时不在 runner 中托管项目数据库。
- **expected behavior**：按客户隔离部署现有 runner；把代码改动与完整数据库集成测试分开；自动清理有限的临时资源；磁盘不足时停止领取新任务；本地免费路径不变。
- **explicit scope boundary**：首期只托管代码 Issue → agent → PR → CI 的流程；不托管项目运行时，不实现共享多租户控制面或计费产品。

# Part B · Build Layer（执行层）

## 5. Repository Context And Architecture Fit

### 现有能力与事实

- `src/backend/engines/agent_runner/templates/runner_container/Dockerfile.runner` 已预装 Python、Node、agent CLI、GitHub CLI、uv、just、git 和 KedaCode。
- `docker-compose.runner.yml` 已是单仓库 runner；没有 DB/Redis sidecar、没有公开端口，也没有 Docker socket 挂载。它将仓库、container-auth 与 KedaCode 状态目录挂载进容器，重启策略为 `unless-stopped`。
- `kc container up/down/logs`、认证快照导入、dry-run 与宿主 daemon 互斥检查均已存在。`kc container up` 支持 `--repo`、`--repo-id`、`--gh-token` 和 `--build`；托管文档不得把密钥写进仓库 dotenv 或公开日志。
- 每日应用日志已有 14 天清理；Issue 执行输出写入目标仓库 `logs/agent-runner/issues/<repo_id>/`，需单独纳入策略。
- `src/backend/infrastructure/persistence/console_store.py` 将运行、attempt、审计、队列与监控数据写入本地 SQLite。此数据库是 daemon 自身元数据，不是客户项目的 DB middleware；首期只清理明确定义的运行摘要，不清理审计、设置或活动状态。
- `src/backend/core/use_cases/worktree_cleanup.py` 已有关闭 Issue、远端分支已删除、托管路径、干净且已合并等保护条件；`kc worktree cleanup` 默认 dry-run，支持既有人工检查结果。任何 daemon 自动化不得使用 `force` 绕过安全条件。
- daemon 同一仓库单进程锁在 `src/backend/core/use_cases/daemon_single_instance.py`；Issue 有独立认领状态与 worktree。自动清理需在一次执行批次完成、并发 worker 已退出后进行，并增加对活动认领的保护。
- runner 在提交前会运行 `.kedacode.toml` 的 `verification_commands`；CI 失败处理由既有 PR 检查和 `post_pr_supervisor` 能力承担。数据库服务应在客户 GitHub Actions job 中声明。

### 架构边界

遵循 `src/backend/api/ → src/backend/core/ → src/backend/engines/ → src/backend/infrastructure/`：

- API/CLI 负责解析 `kc container gc` 的预览与执行选项。
- core 管理托管维护策略、清理候选判定、磁盘准入和 daemon 批次空闲边界。
- engines 调用 Docker CLI 或复用现有 worktree 清理，不引入 Docker Python SDK。
- infrastructure 负责 SQLite 运行历史保留、日志清理、磁盘信息和配置读取。
- daemon 不增加访问中央数据库或启动项目服务的基础设施依赖。
- `kedacode-operator` 随包 skill 与 `docs/` 同步新增 CLI 和托管运维约定。

### Frontend Impact

**No frontend impact.** 目标是 CLI、容器运行资产和运维文档。仓库有 `frontend-admin/` 与 `frontend-public/` 应用，但本次不改变路由、组件、客户端请求或用户界面。CLI 的真实输出通过终端证据呈现，不需要交互原型。

### Existing PRD Relationship

- `tasks/archive/P2-FEAT-20260707-141659-iar-runner-docker-containerization.md` 已交付基础 Docker runner 与 `kc container` 命令。本 PRD 是其托管运维扩展，不重复实现容器化。
- `tasks/archive/P2-FEAT-20260610-144433-iar-worktree-cleanup.md` 已交付保守的 worktree 清理；本 PRD 只在托管配置下复用并自动触发，不改变本地命令默认值。
- `tasks/pending/P1-FEAT-20261009-133425-lifecycle-agent-model-settings.md` 和 `tasks/pending/P1-FEAT-20261009-123512-kc-agentic-entry-and-stall-supervision.md` 的范围不同，互不依赖；可独立实施。
- 不需要前置数据库或 API 层迁移 PRD。

## 6. Recommendation

### Recommended Approach

在已有单仓库 Docker runner 上补足托管运行配置、无人值守资源回收和运营文档。托管工作负载由运营者在客户专用 VM 上管理；runner 只持有该客户仓库 checkout、GitHub 凭据和模型凭据。所有数据库/Redis 等客户项目服务由客户 CI workflow 按需创建。无需新增后端服务、外部数据库、控制面 API 或前端页面。

### Why This Fits

容器化 runner 已提供工具链安装、单仓库挂载和生命周期 CLI。用现有容器作为隔离执行单元、在 daemon 批次空闲边界复用 worktree 清理、在宿主机由现有 Docker CLI 管理镜像缓存，是投入最低且保留现有 GitHub 交付链路的做法。专用 VM 把“谁能读到客户代码和凭据”边界放在操作系统/云实例层，不需要首期自建复杂共享宿主机沙箱。

### Redundant Abstractions To Avoid

- 不新增 Cloud Worker、消息队列、服务端任务数据库或容器编排层。
- 不复制一套 worktree cleaner；在现有清理用例上增加托管所需的活动认领保护和批次触发入口。
- 不在 runner 内安装 Docker daemon，也不把宿主 `/var/run/docker.sock` 挂进容器。
- 不把每种客户数据库封装成通用 middleware provisioning API。

### Proposed Solution Summary (实现机制)

运营者提供目标客户仓库路径、仓库 registry 配置、GitHub 凭据和客户授权的 agent CLI 凭据；系统只消费显式配置，不推断项目需要什么数据库，也不自动安装项目服务。复用现有 `kc container up` 与 Compose runner，在托管运行配置打开维护策略。daemon 每完成一次工作批次、所有 Issue worker 退出后，调用现有安全 worktree cleaner；该批次同时清理过期 Issue 原始日志和运行摘要，并在领取下一项任务前检查磁盘剩余空间。空间不足时保留现有活动任务、不再领取新工作并记录原因。Compose 设置容器 stdout 日志轮转；宿主机 `kc container gc` 清理悬空镜像与超过保留窗口的 build cache，不删卷或活动镜像。CI 是项目集成测试的唯一运行位置，GitHub required checks 是 PR 合并前的门禁。

托管状态继续用客户专用宿主机上的现有 SQLite 与配置目录；不创建中心化存储，不改变本地默认配置，不为本 PRD 新增数据库 schema。

### Alternatives Considered

1. **多客户共用 VM + 多个容器：** 单机成本更低，但当前 runner 并非经过跨租户安全评审；共享内核、挂载路径、Docker 守护进程与运营凭据扩大泄露影响面。首期不推荐。
2. **客户自有云主机（BYOC）：** 客户掌握代码和凭据，运营者可收部署/维护服务费，但运维权限、网络连通和版本管理要求更高。可作为替代，但不与首期托管试点同时做。
3. **让 daemon 启动项目数据库容器：** 每个项目的版本、迁移、初始化和生命周期都不同；会让长驻容器承载项目测试服务，增加资源和维护成本。CI 已有面向 PR 的环境和结果状态，ROI 较低。

## 7. Implementation Guide

This section is a living implementation guide based on current repository analysis. If implementation discovers additional affected files, hidden dependencies, edge cases, or a better path, update this PRD before proceeding.

### Core Logic

1. `kc container up` 在托管 VM 上挂载客户单仓库，配置容器日志轮转，继续使用现有认证隔离与单仓库 daemon lock。不要将 GH_TOKEN 放进仓库文件、compose 输出、审计文本或进程参数明文。
2. daemon 按现有生命周期领取 Issue 并完成当前 work batch；等待所有并行 worker 退出后，若托管维护开关开启，执行一次有界清理。每个清理项均有类型、候选数、删除数、跳过原因和空间前后值。
3. worktree 清理必须遵循现有 `cleanup_iar_worktrees` 守卫，并确认该 Issue 没有活动 claim / worker。验证失败或 GitHub 状态不可查询时 fail-closed：保留 worktree，不尝试强制删除。
4. 日志清理删除超出已确认保留期的 per-Issue 输出和既有 app 日志；运行摘要仅删除终态且超过摘要保留期的数据。清理必须按显式表/文件类型执行，不能递归删除整个 `logs/`、状态目录或认证目录。
5. 周期维护检查宿主数据盘可用空间。在配置安全线下不启动新的 Issue；等待当前 worker 结束，打印 free/total 和停领原因。超过恢复线后自动恢复领取，避免频繁抖动。
6. `kc container gc --dry-run` 展示工作树、日志、运行摘要、容器日志轮转配置与 Docker host cache 的拟处理量；`--apply` 才执行删除。需要 host Docker 权限的操作只从 VM 宿主 CLI 运行；runner 容器中不提供 Docker socket。
7. CI workflow 按项目声明 Postgres/Redis 等 service containers，并把集成测试作为 PR required check。KedaCode 不伪造或覆盖 GitHub 检查结果。若项目只在 CI 才能验证，PR 保持等待或失败状态，沿用现有 supervisor 语义。

### Change Impact Tree

文件清单是按当前仓库搜索得出的预期落点；实现如发现新增关联文件，先更新本 PRD 并按 Executor Drift Guard 校准。

```text
.
├── Infrastructure
│   ├── src/backend/engines/agent_runner/templates/runner_container/docker-compose.runner.yml [修改]
│   │   【总结】为 runner 容器日志配置大小与文件数轮转，维持无端口、无 Docker socket、无项目 DB sidecar。
│   ├── src/backend/infrastructure/config/agent_runner_settings.py [修改]
│   │   【总结】声明默认关闭的托管清理、保留周期、磁盘停领阈值与恢复阈值配置。
│   ├── src/backend/infrastructure/logging/logger.py [修改]
│   │   【总结】复用应用日志保留实现并纳入托管周期清理。
│   ├── src/backend/infrastructure/persistence/console_store.py [修改]
│   │   【总结】提供仅针对终态运行/attempt 摘要的过期清理，保留审计、队列配置和活动状态。
│   ├── src/backend/infrastructure/persistence/console_store_invocations.py [修改]
│   │   【总结】按确认的运行摘要周期清理过期 invocation events，不删活动调用记录。
│   └── src/backend/engines/agent_runner/container_ops.py [修改]
│       【总结】封装宿主机 Docker 悬空镜像与过期构建缓存清理，并提供只读预览结果。
├── Domain / Core
│   ├── src/backend/core/use_cases/run_agent_daemon.py [修改]
│   │   【总结】在批次 worker 全部结束后执行可选维护，并在磁盘低于安全线时停止领取新 Issue。
│   ├── src/backend/core/use_cases/worktree_cleanup.py [修改]
│   │   【总结】为托管自动清理增加活动 claim 保护并始终复用关闭、无远端分支、干净、已合并守卫。
│   ├── src/backend/core/shared/interfaces/container_runner.py [修改]
│   │   【总结】声明容器 GC 预览、执行与空间状态所需的 core 端口类型。
│   └── src/backend/core/use_cases/agent_runner_container.py [修改]
│       【总结】编排 GC 预览/执行请求，不绕过现有 Docker ops 和依赖方向。
├── API
│   ├── src/backend/api/cli_typer_container.py [修改]
│   │   【总结】增加 `kc container gc` 的 `--dry-run` / `--apply` CLI 入口。
│   └── src/backend/api/cli_parsed_commands/container.py [修改]
│       【总结】呈现清理分类、拟删数量、跳过原因和磁盘空间摘要。
├── Tests
│   ├── tests/test_worktree_cleanup.py [修改]
│   │   【总结】覆盖活动 claim、关闭状态、远端分支、脏状态、合并状态和 fail-closed 自动清理。
│   ├── tests/test_daemon_parallel_concurrency.py [修改]
│   │   【总结】证明维护只在批次 worker 全部退出后运行，磁盘低位不领取下一项工作。
│   ├── tests/test_cli_container.py [修改]
│   │   【总结】验证 GC dry-run/显式 apply 参数路径与密钥不回显。
│   ├── tests/test_agent_runner_config.py [修改]
│   │   【总结】验证托管维护配置默认关闭、保留期边界与高低水位校验。
│   ├── tests/test_console_store.py [修改]
│   │   【总结】证明只清理超期终态运行摘要，不误删审计、设置和活动队列。
│   └── tests/test_agent_runner_agent_invocation.py [修改]
│       【总结】验证 invocation events 只按终态与保留窗口安全清理。
└── Docs And Packaged Skill
    ├── docs/guides/agent-runner.md [修改]
    │   【总结】说明托管 VM 部署、CI/数据库分界、凭据管理、清理预览/周期及退订删除步骤。
    ├── docs/getting-started/installation.md [修改]
    │   【总结】说明本地免费路径与可选托管服务的定位，不将托管描述为使用前置条件。
    ├── src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md [修改]
    │   【总结】同步 operator agent 的托管部署、GC 命令、安全边界和 CI 约定。
```

`mkdocs.yml` 已导航到 Agent Runner 和安装指南，预期只更新正文；若实现需要新增文档入口，再同步导航。

### Risk Classification Register

| 变更点 | 层 | 等级 | 决定因素 | 介入方式与失败区分门禁 |
|---|---|---|---|---|
| 客户 VM、repo 与 credentials 隔离，容器不挂 Docker socket | infrastructure / API | R3 | 凭据和跨客户安全信任边界 | 人工确认 D-01；rv-1 必须在 staging VM 检查真实挂载与权限，并以第二客户作为越权负例 |
| 日志、SQLite 摘要与 worktree 自动删除 | core / infrastructure | R3 | 不可逆数据删除与并发状态 | 人工确认 D-02；rv-2 对安全候选实删并证明 dirty/active/unmerged 负例保留 |
| 不在 daemon 容器跑 DB tests、由 GitHub CI service 与 required checks 验收 | core / docs | R2 | 跨越 daemon、GitHub Actions 和 PR 合并状态 | 执行器 + rv-3 staging PR required-check 烟测与失败负例 |
| GC CLI 与预览输出 | API | R1 | 限定在容器命令组的新增 CLI 行为 | `tests/test_cli_container.py` 失败区分 dry-run 与 apply，验证 secret 不回显 |
| Compose stdout 日志轮转 | infrastructure | R1 | runner 的单服务容器配置 | `docker compose config` 检查实际日志 driver/limit 且没有端口、socket、DB 服务 |
| 托管部署和 packaged skill 文档 | docs | R0 | 信息同步 | 文档引用与 CLI schema 静态检查 |

### Executor Drift Guard

开始实现前重新搜索以下语义锚点，若实现位置或事实变化，先更新 PRD 再改代码：

- `rg -n "cleanup_iar_worktrees|run_agent_daemon|daemon-locks|worktree.*claim" src/backend`
- `rg -n "per_issue_log_path|log_retention_days|SqliteConsoleStore|attempt_records|audit_logs" src/backend`
- `rg -n "container up|container down|container logs|docker-compose.runner.yml" src/backend/api src/backend/engines/agent_runner`
- `rg -n "verification_commands|post_pr_supervisor|services:" docs/guides/agent-runner.md .github/workflows`
- 检查 `tasks/pending/` 与 `tasks/archive/` 是否出现更新的重叠托管、清理或 docker PRD。

### Flow / Architecture Diagram

```mermaid
flowchart TD
    Customer[客户 GitHub Issue] --> VM[客户专用 Linux VM]
    VM --> Runner[单仓库 KedaCode runner 容器]
    Runner --> Agent[Agent CLI 与代码工具链]
    Agent --> Repo[客户仓库 worktree 与 PR]
    Repo --> CI[客户 GitHub Actions CI]
    CI --> Services[按需启动 PostgreSQL / Redis]
    CI --> Checks[PR required checks]
    Checks --> Human[客户审阅与合并]
    Runner --> State[该 VM 上的本地 SQLite 与状态目录]
    Runner --> GC[托管维护：worktree、日志、历史与磁盘阈值]
    Host[VM 宿主机 Docker CLI] --> GC
    Host --> Runner
```

**No data model changes in this PRD.** SQLite 使用既有表；保留策略不新增表或迁移。

**Low-Fidelity Prototype:** 无前端改动，因此不需要界面原型图；托管状态由真实 CLI 与 GitHub PR/CI 输出呈现。

**Interactive Prototype Change Log:** No interactive prototype file changes in this PRD.

**External Validation:** No external validation required; repository evidence was sufficient.

### Realistic Validation Plan

#### rv-1：托管部署隔离与凭据边界

```yaml
- id: rv-1
  behavior: "客户专用 VM 上启动单仓库 runner；只挂载该客户 repo、状态和 credentials；另一个客户无法读取这些路径。"
  reviewer: verifier
  real_entry: "专用 staging Linux VM 上执行 `kc container up --repo /srv/kedacode-pilot/repo --repo-id pilot --build`，再用 `docker compose -f <packaged-compose> config` 与 `docker inspect <runner-container>` 检查实际资源。"
  expected: "运行容器只挂载 pilot 的仓库/认证/状态目录，不发布端口、不挂载 Docker socket；secret 不出现在 compose 计划、进程参数或日志。第二客户 VM/账号无法读取 pilot 路径。"
  mock_boundary: "必须使用真实 Docker Engine、打包后的 runner Compose 和隔离 staging VM；agent API 与 GitHub 外部服务可使用 dry-run/mock，不能 mock Compose 配置解析或实际 mount 权限。"
  tier: R3
  test_layer: sandbox
  required_for_acceptance: true
  critical_value_source: "从真实 kc container up 解析出的 repo 路径、state-home、credential 路径与当前 compose environment/mount；不得从 PRD 手工重建预期路径。"
  must_cross: "kc container up CLI → API parser → core container use case → packaged compose → Docker Engine 创建容器 → 在宿主机以第二客户 OS 身份读取 mount 的负例检查。"
  forbidden_bypasses: "不允许只断言静态 YAML；不允许 mock docker inspect/mount；不允许使用同一客户 shell 身份证明跨客户隔离；不允许把凭据打印在 --gh-token 参数、compose 展开结果或命令输出中。"
  fresh_state_probe: "容器启动后重新执行 docker inspect，并从独立的第二客户用户/VM 检查路径权限与可见性。"
  final_tree_evidence: "保存 compose config、docker inspect 摘要（脱敏）、权限检查与 staging VM 标识；实现树或 Compose/Dockerfile 变化后重跑，并记录最终 Git tree。"
  negative_control: "在测试边界把第二客户 mount 指向 pilot state-home 后重跑隔离断言；负例必须被权限/挂载检查拒绝。"
  expected_fail: "第二客户身份能列出或读取 pilot 的 credential/state 文件时 rv-1 为红。"
- id: rv-2
  behavior: "托管 GC 预览和执行只回收已确认保留期的日志、终态摘要及安全 worktree，保护活动/脏/未合并/远端仍存在的数据。"
  reviewer: human
  real_entry: "在隔离 fixture repo 中通过真实入口运行 `uv run kc container gc --repo <fixture-repo> --dry-run`，检查清理摘要；确认后运行 `uv run kc container gc --repo <fixture-repo> --apply`。"
  expected: "dry-run 只列出超期且满足全部规则的候选；apply 后这些候选消失；活动、dirty、未合并、远端仍存在的 worktree、审计记录和认证文件仍存在，输出逐类给出删除/跳过数量和原因。"
  mock_boundary: "真实 CLI、临时 Git repo/worktree、真实文件删除与 SQLite fixture 必须执行；GitHub Issue/PR API 可在 IGitHubClient 边界替身以固定 closed/open 状态，Docker Engine 只在 host-cache 子项使用可用时的 sandbox smoke。"
  tier: R3
  test_layer: integration
  required_for_acceptance: true
  presentation: "tasks/evidence/<prd-stem>/rv-2-gc-preview.txt；交付时在人读呈递区展示该终端输出，并指出 dirty/active 项的跳过原因。"
  critical_value_source: "候选文件 mtime/retention setting、真实 `git worktree list` 与 `git status`、从 GitHub boundary 返回的 Issue 状态、真实 SQLite audit/run 行；预览所打印的路径必须来自清理器扫描结果。"
  must_cross: "kc container gc CLI → parser/core 清理策略 →真实临时文件和 git worktree → GitHub 状态 boundary → 删除完成后的新进程文件/SQLite 查询。"
  forbidden_bypasses: "不得直接调用内部清理函数替代 CLI；不得只 mock 文件系统或 Git；不得通过 `--force` 绕过 worktree 守卫；不得只查看进程返回值而不从 fresh process 重新查询文件与 SQLite；不得删除认证目录、客户仓库、volume 或 audit_logs。"
  fresh_state_probe: "apply 后以新 CLI 进程重新扫描目录、`git worktree list --porcelain` 和 SQLite audit/run 查询；验证删除与保留集合。"
  final_tree_evidence: "保存 dry-run、apply 和 fresh probe 输出及 fixture manifest，标注最终 Git tree；worktree cleaner、保留策略或 CLI 任何改动都会使该证据失效。"
  negative_control: "在 fixture 中加入一条仍有远端分支或未提交修改的 worktree，并将其标成活动 claim；预期 dry-run 将该条列入 skipped，apply 后目录与分支仍存在。"
  expected_fail: "若清理预览把活动、脏、未合并或远端仍存在的候选标为可删除，或 apply 删除其中任一项，rv-2 为红。"
- id: rv-3
  behavior: "数据库依赖集成测试在客户 PR 的 GitHub Actions service 中执行，CI failure 不被 daemon 本地成功状态覆盖。"
  reviewer: verifier
  real_entry: "授权 staging 仓库创建一个代码 Issue 并运行托管 daemon；agent 创建 PR 后观察该 PR 的 GitHub Actions required checks。"
  expected: "runner Compose 中没有数据库服务；CI job 启动 workflow 声明的 PostgreSQL/Redis service 并运行集成测试；故意失败的测试让 required check 失败且 PR 保持未通过。"
  mock_boundary: "staging 验收必须经过真实 GitHub Actions 与真实服务容器；Agent 模型调用可用受控测试账号或跳过此 smoke，普通 CI 回归通过本仓库的 workflow/static contract tests，不依赖客户凭据。"
  tier: R2
  test_layer: manual
  required_for_acceptance: true
  critical_value_source: "从 staging PR 的 Checks 页面/API 实际读取 workflow、service health、测试 job 状态与 required status；不从 runner 日志推断 CI 结果。"
  must_cross: "客户 GitHub Issue → daemon 创建 PR → GitHub Actions workflow → service container ready → 数据库集成测试 → PR required check 汇总。"
  forbidden_bypasses: "不允许本地 mock CI status、跳过 required check、把 daemon 的 verification_commands 绿色当作数据库集成通过，或只验证 workflow YAML 格式而不执行 staging job。"
  fresh_state_probe: "在真实 PR 中先跑绿的服务测试，再推送一个故意失败的测试变更；通过独立 PR Checks 页面/API 确认状态转红且 required merge gate 未放行。"
  final_tree_evidence: "保存 staging PR URL、workflow run URL、测试结果和 repo branch-protection required-check 摘要；影响 workflow/daemon CI 状态语义的改动后重跑。"
  negative_control: "在 staging PR 测试分支让集成断言失败，保持 required check 设置不变。"
  expected_fail: "workflow 未启动服务、测试没有运行或故意失败仍显示 required check 成功时 rv-3 为红。"
```

**失败排查：** rv-1 先检查 VM / state-home 的实际 mount 和目录权限，再检查 compose 的 environment 展开；rv-2 先检查 Issue claim、`git status`、远端分支和时间阈值；rv-3 先检查 Actions service health log、测试命令和分支保护 required checks。

### Usage And Impact After Implementation

#### 托管部署的操作脚本

1. 为客户建立独立 Linux VM 和 OS 用户，不与其他客户共享工作目录、Docker 上的凭据目录或状态路径。
2. 在该 VM 准备授权仓库 checkout、客户 GitHub token 与模型 CLI 凭据；权限设为仅托管账户可读。不要将 secret 保存进仓库、`.env`、可追踪部署脚本或命令行参数。
3. 安装 KedaCode 并运行 `kc container auth import` / `kc container up --repo <repo-path> --repo-id <repo-id>`；确认 daemon lock 与容器 mount 正确，再开启托管维护配置。
4. 客户为每个需要数据库服务的测试配置 CI service 与 required check；daemon `verification_commands` 只运行容器可用的快速检查。
5. 用 `kc container gc --dry-run` 检查维护候选；清理执行日志会记录分类计数、跳过原因、保留时间范围和磁盘空间。
6. 按客户约定收取 VM/部署维护基础费用；模型供应商账户和用量仍归客户。
7. 退订时停止 runner、撤销/轮换 GitHub 与模型凭据，按数据导出/删除约定销毁该 VM。

#### 客户 CI 配置者的任务脚本

在客户仓库的 GitHub Actions workflow 中声明需要的 service container（例如 PostgreSQL），等待 health check 成功后运行真实集成测试，并把 workflow job 配置为 PR required check。daemon 只执行本地快速命令和代码任务，不在 runner 里“临时凑出”数据库服务。

#### Compatibility And Migration

- 无既有用户需要迁移；托管功能是 opt-in。
- 不更改 `.kedacode.toml` 现有 `verification_commands` 默认语义。本地/容器用户需自行把数据库集成命令移入 CI workflow，才具备无需本地 DB 的托管体验。
- existing `kc container up/down/logs` 语法保持不变；`container gc` 是新增 CLI surface，必须同步 CLI skill 与 docs。
- 如果用户关闭托管维护，日志和 history 保持现状；容器 Compose 的日志轮转只限制 Docker stdout/stderr，不删除 volume 或 bind-mounted 文件。

### Low-Fidelity Prototype

**Waived:** no frontend app or visual interaction changes. The terminal GC preview is validated from the real CLI and presented as text evidence, not as a UI mockup.

### Interactive Prototype Change Log

No interactive prototype file changes in this PRD.

### External Validation

No external validation required; repository evidence was sufficient.

### Realistic Validation Plan

See the structured `rv-1`–`rv-3` YAML oracle block above. The core tests listed in the Change Impact Tree are implementation gates; they do not replace the three real-entry oracles. `rv-1`/`rv-3` use operator-owned staging resources and run opt-in after merge or during release validation. The fallback without provider credentials is static Compose validation plus safe local fixture GC; no live agent/model request is required.

### Executor Drift Guard

If the review discovers these modules have shifted, update the Change Impact Tree before implementing. Search for `cleanup_iar_worktrees`, `run_agent_daemon`, per-Issue log paths, `SqliteConsoleStore`, `agent_runner_container.py`, and container CLI registration to find the current owners. Do not create parallel cleanup or storage modules until those paths are checked.

### Flow / Architecture Diagram

See Mermaid diagram in Section 7 above.

### ER Diagram

No data model changes in this PRD.

### Decision Log

D-01 follows the recommended isolated managed-host model, pending the customer isolation and billing choice in Section 2. D-02 follows the recommended retained data periods, pending the data cleanup choice in Section 2.

## 8. Delivery Dependencies

### Delivery Dependencies

- Depends on tasks/issues:
  - none
- Gate type: none
- Sequence: via-main
- Notes: This PRD extends the archived Docker runner and worktree-cleanup work. It can be implemented independently of the unrelated pending lifecycle-settings and agentic-entry PRDs.

## 9. Acceptance Checklist

### 9.1 人读呈递区（Human Review Surface）

| 人需要看的结果 | 呈递内容 | 10 秒自检 |
|---|---|---|
| 托管 GC 预览能说明哪些过期输出会删除，以及活动、脏或未合并 worktree 为什么保留 | `tasks/evidence/<prd-stem>/rv-2-gc-preview.txt`；交付时在 PR evidence comment 或完成消息中嵌入对应终端输出 | 查输出中的 `eligible`、`deleted`、`skipped` 分类数量；dirty/active fixture 应在 `skipped` |
| 自动清理执行后，审计和客户凭据仍可读，安全候选才消失 | 同一 evidence bundle 中 rv-2 的 apply 与 fresh-process probe 片段 | 对照 dry-run manifest，确认 dirty/active 路径存在、超期 safe 路径不存在 |

`reviewer: verifier` 且不需人工看图的结果不放在本区：rv-1 隔离/Compose 资源证明、rv-3 GitHub Actions required-check 结果、配置和单测/静态检查。若没有私有 staging 证据发布权限，执行者须在交付说明中给出受限访问的单一 review 入口，不得公开客户凭据或代码。

### 9.2 Acceptance Evidence Package

#### Human-Confirmed

- [ ] **D-01 托管边界**：人工确认专用 VM / BYOC / 共享主机选择，并确认基础设施费与模型用量的承担方；记录来自 `rv-1` 的隔离检查和部署示例。选择结果需回填 PRD Decision Log。
- [ ] **D-02 数据保留**：人工确认原始日志、运行摘要与退订时的数据删除周期；记录来自 `rv-2` 的预览和 fresh-state 删除/保留证明。选择结果需回填配置示例与 Decision Log。
- [ ] **人读呈递区审阅**：查看 rv-2 清理预览与执行后的新进程探测结果，确认终端显示和实际清理集合一致。

#### Architecture Acceptance

- [ ] `docker compose -f src/backend/engines/agent_runner/templates/runner_container/docker-compose.runner.yml config` 与 runner service 列表证明无数据库/中间件 sidecar、公开端口和 Docker socket mount。
- [ ] `just lint` 中现有 architecture 检查通过，证明后端依赖仍为 `api → core → engines → infrastructure`。

#### Dependency Acceptance

- [ ] `pyproject.toml` 与锁文件没有新增 Docker SDK、外部 scheduler 或服务端数据库依赖。

#### Behavior Acceptance

- [ ] 托管维护关闭时，本地 `kc daemon` / `kc container up` 不进入新清理分支；配置默认关闭并有对应的回归证据。
- [ ] 有效关闭 Issue 的安全 worktree 在 worker 全部结束后被清理；活动、dirty、unmerged、remote-exists 和 GitHub 状态不可用候选都被保留。
- [ ] 达到磁盘阈值时 active worker 不被杀；该 daemon pass 结束后停领并输出已用/可用空间，空间恢复越过高水位后继续领取。
- [ ] 过期运行摘要按确定的 keyset/事务边界删除；audit、queue settings、活动队列、agent credentials 与 provider sessions 不受影响。
- [ ] Compose JSON log driver 的最大文件尺寸/数量有界；host GC 只清理悬空镜像与过期 build cache，不运行 volume prune，不删除活动容器正在引用的镜像。

#### Documentation Acceptance

- [ ] `docs/guides/agent-runner.md` 和 `docs/getting-started/installation.md` 明确本地免费路径、托管试点边界、客户凭据与模型费用、数据库测试转 CI、磁盘与退订数据处理。
- [ ] `src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md` 同步准确描述 `kc container gc` 参数、dry-run/apply、默认关闭和“绝不挂 Docker socket”边界。
- [ ] `mkdocs.yml` 导航与文档实际位置一致，不添加重复入口。

#### Validation Acceptance

- [ ] rv-1 的实际 Compose config、docker inspect、跨客户权限负例和 final tree 标识通过；改动挂载、启动或凭据处理后重跑。
- [ ] rv-2 真实 `kc container gc` dry-run → 显式 apply → 新 CLI 进程 probe 通过；证据 manifest 证明 dirty/active/unmerged 数据保留。
- [ ] rv-3 staging PR 的 DB-backed CI service 测试绿；负例能让 required check 红，PR merge gate 不放行。
- [ ] `uv run pytest tests/test_worktree_cleanup.py tests/test_daemon_parallel_concurrency.py tests/test_cli_container.py tests/test_agent_runner_config.py tests/test_console_store.py tests/test_agent_runner_agent_invocation.py -q` 与 `just lint` 通过；相关行为变更后重跑。
- [ ] `uv run kc container gc --repo <fixture-repo> --dry-run` 真实 CLI 入口退出成功；相同候选的 `--apply` 只在明确指定后执行，fresh process 结果符合 rv-2。该证据与最终实现 Git tree 绑定。

#### Delivery Readiness

- [ ] 实施、文档和随包 skill 均完成；部署命令与数据保留行为没有未决生产阻断项；失败或外部 staging 不可用时如实记录并保持未归档。
- [ ] PR 描述/evidence comment 或完成消息逐字呈现 9.1 的两项结果与可执行 review 入口；证据只对授权评审人可见，没有公开客户代码/凭据。
- [ ] 非人工验收项全勾选或按仓库 PRD 流程记录 `[~]` 有理由；Final Reconciliation 完成；本 PRD 随实现归档，`Human-Confirmed` 保留开放复选框等待人工验收。

## 10. Functional Requirements

- **FR-1 Local free mode:** 本地 `kc daemon` 继续独立运行；托管功能及维护策略默认关闭，不要求新增云端账户或云端连通。
- **FR-2 Single-tenant hosted runner:** 支持在独立 Linux VM 上启动单仓库 Docker runner；对该 VM 专用 repo、KedaCode 状态和 credential paths 做权限检查；不向公网发布服务端口。
- **FR-3 No project middleware in runner:** KedaCode runner Compose 不安装、启动或连接客户项目 Postgres/MySQL/Redis/队列；不挂载 Docker socket。客户自行在 CI workflow 声明集成测试服务。
- **FR-4 CI gate remains authoritative:** 代码任务在 runner 内完成所配置的快速验证并创建 PR；项目级数据库检查必须由 PR CI 执行。GitHub required check 未绿时不得声称 PR 已通过集成验收，也不得绕过现有 merge/status 门禁。
- **FR-5 Credential handling:** GitHub token 与模型 CLI 凭据只由运营者按客户隔离提供；文件限宿主托管用户读写；禁止提交仓库、打印 secret 或把 secret 放在脚本/命令行参数中。退订必须先撤销凭据再销毁 VM。
- **FR-6 Hosted resource cleanup:** 托管维护启用后按已确认周期清理 per-Issue raw logs 和过期终态运行/attempt 摘要；复用 worktree cleaner 的所有安全条件并增加活动认领保护；保留 audit、active state、settings 和 credentials。
- **FR-7 Safe host Docker cleanup:** `kc container gc` 支持 dry-run 和显式 apply，报告候选、理由、磁盘空间与结果；host cleanup 只处理不被容器引用的悬空镜像和超期 builder cache，不删 volume、bind mount 或活动镜像。
- **FR-8 Daemon disk guard:** 每轮领取新 Issue 前查看可用空间；低于 configured low-water mark 时暂停新的领取、不杀现有 worker，并给运营者输出容量和恢复条件；越过 recovery watermark 后恢复领取。
- **FR-9 Bounded container logs:** Compose 为 stdout/stderr 配置有界轮转，避免 Docker daemon log 文件在无人值守运行中无限增长。
- **FR-10 Operational visibility:** GC 输出逐类展示 scanned/eligible/deleted/skipped/failed 数量、保留期限、跳过原因和错误；部分清理失败返回可监控的非零状态，不掩盖失败。
- **FR-11 Docs and CLI knowledge:** 更新部署/CI/退订文档与随包 `kedacode-operator` skill，CLI schema/help、配置名和文档保持一致。

## 11. Non-Goals

- 客户自助注册、运营后台 UI、SaaS control plane、集中式多客户数据库、自动发票/收款或用量计费。
- 将客户项目数据库、消息队列、浏览器或服务依赖部署到 daemon 容器。
- 将该 VM 作为可供客户任意 SSH/shell 的通用云开发机或云 IDE。
- 多客户共享同一个 VM/容器/认证目录/状态目录的首期实现。
- PR 自动合并、分支保护自动配置、CI 凭据自动签发。
- 清除 customer repo、GitHub remote 分支、active/dirty/unmerged worktree、Docker volumes、运行中的镜像或 credentials。
- 为 daemon 内部 SQLite 建立独立的数据库服务。
- 默认开启本地 daemon 的托管垃圾回收行为。

## 12. Risks And Follow-Ups

- **托管安全责任：** 专用 VM 隔离仍要求宿主机安全更新、最小权限、磁盘加密与备份策略；PRD 验证证明部署隔离，不替代云主机日常安全运营。
- **凭据轮换与客户退订：** 客户提供的 CLI credential 可能是可续期 session。必须验证导入/更新/撤销步骤，避免停服后旧凭据仍能调用模型或访问仓库。
- **CI 配置因仓库而异：** daemon 无法推断数据库版本、迁移顺序或种子数据；CI 没配置 required check 时会造成项目集成测试缺口，部署前 checklist 必须提醒客户。
- **清理与诊断取舍：** 14/90 天是推荐值，需由人工确认；改动期限后必须同步说明实际影响。审计和退订数据仍需受限访问。
- **磁盘门槛：** 一项任务可能大于剩余空间；设计应避免新任务启动后立即失败，必要时由运营者扩容后自动恢复，不能自动删除未知客户文件来腾空间。
- **本地路径与单仓库限制：** 当前 Compose 通过宿主路径挂载一个 checkout，首期运维脚本按单仓库 VM 边界执行；多仓库共用 VM 要等隔离和容量有证据后再评估。

## 13. Decision Log

| ID | 决策问题 | 当前推荐 | 替代方案 | 原因 | 状态 |
|---|---|---|---|---|---|
| D-01 | 首期托管隔离与模型用量如何归属？ | 每客户专用 VM；客户自带 GitHub/模型凭据并直接支付模型费用；运营者收基础设施服务费 | BYOC；多客户共享 VM；运营者包模型费用 | 复用现有单仓库容器并把跨客户边界放到 VM，避免先建共享沙箱和用量计费 | 待人工确认 |
| D-02 | 原始日志、运行摘要及退订数据如何保留/删除？ | 原始日志 14 天、运行摘要 90 天、审计保留至退订；safe worktree 与 host cache 自动回收 | 更长保留、退订前强制导出或自定义周期 | 现有 app log 默认 14 天；摘要足以追踪近期任务，审计独立于普通日志 | 待人工确认 |

### Final Reconciliation

- Interpretation: pending implementation.
- Public behavior and contracts: pending implementation.
- Related PRD status: reviewed against repository state on 2026-10-09.
- Requirements and risks: pending implementation.
- Reconciled differences:
  - none; this is a new pending PRD and no delivery evidence exists.
