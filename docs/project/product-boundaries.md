# 产品边界

KedaCode 以 `kc` CLI 为核心，编排从 GitHub Issue 到 PR 的本地交付。人和拥有相应权限的外部 Agent 都可以调用同一套命令，通过机器可读输出、语义退出码和运行时自省跟进结果。执行任务时可按阶段选用已接入的 Agent CLI。命令与状态流转见 [Agent Runner 指南](../guides/agent-runner.md)，执行器接入原则见 [Agent 选型](../architecture/agent-selection.md)，分层与职责见[系统架构](../architecture/system-design.md)。

## CLI 优先与前端插件

核心工作流优先通过 `kc` 提供，明确命令参数、机读输出和退出码。内置 Console 是默认前端，`kc console` 在本机同时托管界面和同源 HTTP API。共享会话或远程访问有具体需求时，再评估复用现有服务中的相应接口。当前结构见[前端架构](../architecture/frontend-architecture.md)。

自定义前端先按独立运行的本机插件接入。桌面插件通过本地进程调用 `kc`，Web 插件由本机服务代调预设命令并返回结构化结果。比如任务列表插件可在已配置仓库里运行 `kc issue list --state open --json`，用返回结果渲染页面。插件通过 `kc schema --json` 确认可用参数、输出格式和退出码。机读契约见 [Agent Runner 指南](../guides/agent-runner.md)。

首版先用独立客户端验证替换能力。[构想工作台 PRD](https://github.com/ZataZhang/keda/blob/main/tasks/pending/P2-FEAT-20261120-012205-cli-first-concept-workbench.md)已规划让默认界面和自建客户端读取、继续同一个会话，可作为共享状态的首个验证场景。出现多个可复用插件或集中分发需求后，再评估统一安装和管理。

## 任务准入与交付

- **人工授权。** 守护进程从 `agent/ready` 队列认领 Issue，可按标签指定 Agent。需要指定本机时，由人在本机显式运行该 Issue，无需先打就绪标签。Backlog 可按仓库配置自动推进符合条件的 PRD。配置与当前行为见 [Backlog 指南](../guides/agent-runner.md#backlog)。
- **按需澄清。** 无 PRD 的 Issue 也可执行，Issue 正文提供需求依据。复杂需求可通过评论补充背景、复现路径、预期、约束和图片，再按需讨论或生成 PRD。
- **隔离交付。** Runner 在独立 worktree 实现和验证，按发布档位创建 Draft PR。Supervisor 随 PR、CI 和讨论变化继续监督，在 `agent/supervising` 内完成对账、修复或 rebase，遇到需要人工决策的情况交回维护者。
- **任务与战略各有入口。** Console 的 Backlog 管待办和执行状态。项目的工作目标在[仓库根目录的 Roadmap](https://github.com/ZataZhang/keda/blob/main/ROADMAP.md)。

## 验证门禁的目标边界（M12）

仓库维护者决定运行或观察哪些检查、哪些结果是合并和验收的必需条件。KedaCode 负责运行、等待、记录、恢复和监督。检查需要绑定某个版本时，由检查提供方报告适用版本。

仓库级配置契约和兼容迁移仍是待设计项。设计时优先复用本地验证命令、GitHub checks 和现有 verifier，明确缺配、待运行、失败、通过等状态如何处理，以及自动合并如何消费必需检查。发布档位和当前门禁以 [Agent Runner 指南](../guides/agent-runner.md)为准。

## 候选扩展

- **FreshAI 本机执行器。** FreshAI 管理通用工作项、运行记录与评审，Keda 管理本机凭证、路径、隔离执行和 Apply 回执。先等 FreshAI 的 Worker Contract 落地，再评审 Keda 连接器。若实际使用反复需要在普通文件夹上执行与评审，首版用单机 CLI 验证复用收益。实施范围与前置条件见[连接器 PRD](https://github.com/ZataZhang/keda/blob/main/tasks/pending/P2-FEAT-20261107-162227-freshai-local-executor-connector.md)。
- **代码托管平台。** GitHub 是主工作流。其他平台的工作由[仓库平台选择](https://github.com/ZataZhang/keda/issues/398)和 [Gitea 对接](https://github.com/ZataZhang/keda/issues/416)承接。

原版工作流、FreshAI 边界图和其他设计问题可从[路线历史](roadmap-history.md)回看。
