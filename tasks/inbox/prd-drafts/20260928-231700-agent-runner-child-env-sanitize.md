---
Draft ID: 20260928-231700
Draft Status: pending-review
Repo ID: keda
Priority: P1
Type: bug
Created At: 2026-09-28 23:17
Source Idea Refs: 2026-09-28 23:17
---

# PRD 草案：Agent Runner 派发的子进程环境净化（child env sanitize）

> 本文件是 `tasks/inbox/prd-drafts/` 下的草稿，供人审阅；确认后按流程复制到
> `tasks/pending/` 并补充完整 PRD 章节（Part A / Part B、Machine Contract 等）。
> 事实来源：`tasks/inbox/ideas.md` 2026-09-28 23:17 条目 + 2026-09-28 Issue #156 排查记录。

## 1. 问题陈述

`iar run` / `iar review` 派发的 headless agent 子进程（codebuddy/claude/codex 等）
通过 `subprocess.Popen(...)` 未传 `env=`，**原样继承 runner 进程的完整环境**。
当 runner 本身是从某个交互式 AI 会话（CodeBuddy/Claude Code 的 Bash 工具）启动时，
父环境携带该会话的私有注入变量，污染子进程：

- **已证实的致毒变量**：`SERVER__PORT=56469`。交互式 CodeBuddy 会话的 daemon
  监听该端口，headless 子进程继承后尝试监听同一端口 →
  `listen EADDRINUSE: address already in use 127.0.0.1:56469`（unhandledRejection，
  子进程自身日志可见），随后在第一个模型请求前永久卡死、stdout 零输出，
  20 分钟后被 `_ProcessWatchdog` 的 inactivity timeout 杀掉。
- **事故影响**（2026-09-28）：Issue #156 连续 3 次尝试同因失败，浪费约 70 分钟
  runner 时间并产生误导性的 `transient` attempt 记录；另一会话的 Issue #159 同样卡死。
- **隐患面**：任何"从 AI 会话内启动 runner/daemon"的用法都会复现；
  这是文档推荐的自动化路径（用户常在自己会话里手动触发 `iar run`）。

## 2. 目标

1. runner 派发的所有 agent 子进程默认获得**净化后的环境**：剔除已知的
   会话私有/致毒变量，保留业务所需变量（PATH、HOME、代理、API key 等）。
2. 净化行为**可观测**：被剔除的变量以 WARNING 级日志记录（变量名 + 值的长度
   摘要，不记录完整值，避免泄密）。
3. 净化名单**可配置**：默认内置最小名单，允许通过配置追加（不做 allowlist
   白名单模式——继承语义太广，容易误伤鉴权/代理变量）。

## 3. 非目标

- 不实现通用的"子进程环境审计"功能；只覆盖 agent/工具子进程派发路径。
- 不处理 runner 自身读取环境的行为（如 `IAR_CONFIG`、config.toml 加载）。
- 不修复 codebuddy CLI 侧对 `EADDRINUSE` 的处理（那是上游 CLI 的健壮性问题，
  本提案只做平台侧防御）。

## 4. 方案草案

### 4.1 核心改动

1. 在 `src/backend/infrastructure/process_runner.py` 新增
   `build_sanitized_child_env(extra: dict[str, str] | None = None) -> dict[str, str]`：
   - 基线 = `os.environ` 拷贝；
   - 按内置 denylist 剔除变量，默认名单（首版，全部有实证或强理由）：
     - `SERVER__PORT` —— 已证实致毒（单变量复现）；
     - `CODEBUDDY_SERVICE_PROXY_URL`、`CODEBUDDY_SESSION_ID`、
       `CODEBUDDY_CONVERSATION_REQUEST_ID`、`CODEBUDDY_ROOT_REQUEST_ID`、
       `CODEBUDDY_CONVERSATION_MESSAGE_ID`、`CODEBUDDY_PROJECT_DIR`、
       `CODEBUDDY_CURRENT_MODEL_ID` —— 指向父会话私有状态的注入变量，
       单独存在无害（已实测），但对 headless 子进程纯属噪声，剔除可消除
       一类未来漂移风险；
   - 每剔除一个变量打一条 WARNING 日志（`child env sanitized: removed KEY=value-len`）；
   - 支持 `extra`（调用方追加自定义剔除名单）与配置覆盖
     （`config.toml` → `[agent_runner] child_env_extra_denylist = [...]`，
     合并进默认名单；不改默认名单本身）。
2. 将该 env 应用到所有 agent 子进程派发点：
   - `process_runner.py` 中 4 处 `subprocess.Popen(...)`（约 :273/:365/:675/:814）；
   - `src/backend/engines/agent_runner/output_protocols/plain.py:35`、
     `pi_json_lines.py:36`（engines 层从 infrastructure 导入同一 helper，
     保持四层依赖方向合法：engines → infrastructure）。
3. `src/backend/infrastructure/console/process_supervisor.py:311` 的
   console 守护子进程**暂不改**（它管理的是 iar 自身 daemon，依赖父环境
   语义不同），但在 PRD 中记录该决定。

### 4.2 实现注意

- 四层架构：helper 落在 infrastructure，engines 只导入使用，不得反向。
- 单文件行数红线：`process_runner.py` 已接近 1000 非空行上限（本地 lint warn、
  CI「Validate Template」硬挂），**新增代码前先跑
  `scripts/shared/check_max_file_lines.py` 确认余量**；必要时把 helper 放到
  独立模块（如 `infrastructure/child_env.py`）再导入。
- 守卫测试：`tests/guards/` 增加一条"agent 子进程派发必须传 env="的静态守卫
  （扫描 Popen 调用点），防止未来新增派发点回退。

## 5. 验收清单草案（≥5 条，正式 PRD 需按 Realistic Validation Plan 扩写）

1. 在父环境含 `SERVER__PORT=56469` 的条件下启动 `iar run`，headless agent
   能正常产出 stream 输出并完成任务（复现事故场景的反向验证）。
2. runner 日志出现对被剔除变量的 WARNING 记录，且不包含变量完整值。
3. 默认名单外的变量（如 `PATH`、`https_proxy`、`DASHSCOPE_API_KEY`）原样
   传递给子进程（MCP bailian-image 等依赖仍工作）。
4. 通过 `child_env_extra_denylist` 配置追加的变量同样被剔除。
5. 守卫测试通过：所有 agent 子进程 Popen 调用点都传入净化 env；新增裸
   Popen 会被守卫拦截。
6. `uv run pytest tests/test_pr_supervisor.py -o addopts=""` 与
   `CI=true just test all` 全绿。

## 6. 待用户拍板

- [ ] 默认 denylist 范围：只剔 `SERVER__PORT`（最小方案） vs 本草案的
      "1 致毒 + 7 会话噪声"（推荐，风险已实测为零）？
- [ ] 是否需要 config 覆盖能力（`child_env_extra_denylist`），还是先硬编码？
- [ ] `process_supervisor.py`（console daemon 子进程）是否纳入同一净化？
