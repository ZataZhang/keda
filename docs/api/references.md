# API 参考

本页通过 `mkdocstrings` 自动渲染核心模块的公开 API。

## 基础设施模块

### `backend.infrastructure.config.settings`

::: backend.infrastructure.config.settings
    handler: python
    options:
      show_root_heading: true
      members_order: source

### `backend.infrastructure.config.agent_runner_settings`

::: backend.infrastructure.config.agent_runner_settings
    handler: python
    options:
      show_root_heading: true
      members_order: source

### `backend.infrastructure.config.settings_sources`

::: backend.infrastructure.config.settings_sources
    handler: python
    options:
      show_root_heading: true
      members_order: source

### `backend.infrastructure.logging.logger`

::: backend.infrastructure.logging.logger
    handler: python
    options:
      show_root_heading: true
      members_order: source

### `backend.infrastructure.persistence.database`

::: backend.infrastructure.persistence.database
    handler: python
    options:
      show_root_heading: true
      members_order: source

### `backend.infrastructure.helpers`

::: backend.infrastructure.helpers
    handler: python
    options:
      show_root_heading: true
      members_order: source

## Agent Runner 端点

### `GET /api/v1/agent-runner/status`

返回 Agent Runner 配置摘要与多仓库列表。该端点为**只读**，不会触发 label sync、agent 执行或任何 Git 变更操作。

响应示例：

```json
{
  "daemon_mode": false,
  "config": {
    "max_issues": 1,
    "default_agent": "auto",
    "max_recovery_attempts": 5,
    "recovery_retry_delay_seconds": 30,
    "ready_label": "agent/ready",
    "running_label": "agent/running",
    "review_label": "agent/review",
    "failed_label": "agent/failed",
    "base_branch": "main",
    "remote": "origin",
    "auto_merge": false,
    "forbidden_path_patterns": [".env", ".env.*", "secrets/*"]
  },
  "repositories": [
    {
      "repo_id": "keda",
      "display_name": "Keda",
      "enabled": true,
      "base_branch": "main",
      "remote": "origin"
    }
  ]
}
```

字段说明：

- `daemon_mode`：当前是否以 daemon 模式运行。
- `config`：全局 `[agent_runner]` 合并后的有效配置。
- `repositories`：已配置的仓库列表，每项包含 `repo_id`、`display_name`、`enabled`、`base_branch` 和 `remote`。

### `GET /api/v1/agent-runner/health`

返回运行器健康状态。该端点为**只读**，仅检测 `gh` CLI 是否可用。

响应示例：

```json
{
  "status": "healthy",
  "gh_cli_available": true
}
```

### `GET /api/v1/agent-runner/repositories/browse`

只读列举本机某个目录下的子目录，供控制台「选取仓库路径」的目录选择器使用。浏览器拿不到真实绝对路径，因此目录列举必须由本机后端完成。

该端点与 `GET /api/v1/agent-runner/repositories/discover` 同级：控制台固定绑定 `127.0.0.1` 且为单用户部署，两处都不带鉴权。响应只含目录名与路径，**不返回任何文件内容**。

查询参数：

- `path`：要浏览的目录绝对路径，可含 `~`。省略时从用户主目录开始。路径不存在或不是目录时返回 `400`。

只返回非隐藏子目录（跳过 `.` 开头项），跳过无权限读取的条目而不是让整次请求失败。

响应示例：

```json
{
  "path": "/Users/me/code",
  "parent": "/Users/me",
  "home": "/Users/me",
  "suggested_repo_id": "code",
  "suggested_display_name": "code",
  "directories": [
    {
      "name": "foo",
      "path": "/Users/me/code/foo",
      "is_git_repo": true,
      "has_iar_config": true,
      "already_registered": false,
      "suggested_repo_id": "foo"
    }
  ]
}
```

字段说明：

- `path` / `parent` / `home`：当前目录、上级目录（已是文件系统根时为 `null`）与用户主目录。
- `suggested_repo_id` / `suggested_display_name`：用于回填「添加仓库」表单的建议值，`suggested_repo_id` 由后端按 `normalize_repository_id` 规则从目录名生成，与 `kc registry` 扫描保持一致。
- `directories[].is_git_repo` / `has_iar_config` / `already_registered`：该子目录是否为 git 仓库、是否已 `kc init`、是否已在 registry 中注册。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/content`

返回单个 PRD 的 Markdown 原文（`text/plain; charset=utf-8`，与磁盘文件逐字节一致）。该端点为**只读**。

`encoded_path` 是 PRD 相对路径的 URL-safe base64 编码，与 `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/start` 使用同一约定；路径本身取自 `GET /api/v1/agent-runner/backlog/prds` 列表响应中的 `prd_path`。

查询参数：

- `repo_id`：仓库标识，必填。

只允许读取 `tasks/pending/` 与 `tasks/archive/` 下后缀为 `.md` 的文件。路径在 `resolve()` 之后仍必须落在白名单目录内，因此 `../` 目录穿越、绝对路径、非 `.md` 后缀与符号链接逃逸都返回 `400`，且响应体不含任何文件内容；目标文件不存在同样返回 `400` 而不是 `500`。

```bash
# 把 PRD 相对路径编码成 URL-safe base64。
# 必须保留 `=` padding：后端按 base64.urlsafe_b64decode 解码，去掉 padding 会被判为
# 非法编码并返回 400。这与后端 _encode_prd_path 及前端 encodePrdPath 的编码一致。
encoded_path="$(printf '%s' 'tasks/pending/P1-FEAT-20260101-demo.md' \
  | base64 | tr -d '\n' | tr '+/' '-_')"
curl -sS \
  "http://127.0.0.1:8000/api/v1/agent-runner/backlog/prds/${encoded_path}/content?repo_id=keda-main"
```

### `GET /api/v1/agent-runner/backlog/autopilot`

返回当前仓库 Autopilot 的完整闭环状态。每次调用都重新加载配置（**不复用** `GET /backlog/prds` 的本地快照），以保证页面展示的是写后读回的真实值。

查询参数：

- `repo_id`：仓库标识，必填。

响应字段：

```json
{
  "repo_id": "keda-main",
  "enabled": true,
  "auto_merge_enabled": false,
  "daemon_running": true,
  "max_parallel": null,
  "effective_max_parallel": 2,
  "runner_capacity": 2,
  "ceiling_source": "inherited",
  "config_source": ".kedacode.toml",
  "persisted_enabled": true
}
```

- `enabled`：生效配置中的 `agent_runner.autopilot.enabled`；
- `auto_merge_enabled`：`safety.auto_merge`，即第二道危险动作门禁（只读展示）；
- `daemon_running`：该仓库是否存在 `kind=daemon` 且存活的进程（来自既有 process supervisor 记录）；
- `max_parallel`：Backlog「并发」**策略值**；`null` = 从未设置（存储层没有设置行），此时生效值继承 runner 容量；
- `effective_max_parallel`：控制台按 `min(策略, runner_capacity)` 解析出的并发上限，用于控制条与「全局开始」；
- `runner_capacity`：仓库配置 `[agent_runner.runner].max_concurrent_issues`（下限 1）。daemon 若显式传 `--concurrency`，本轮容量改用旗标值，其 daemon ceiling 可能低于此 API 快照；该端点不读取 daemon 进程旗标，实时值以 daemon 的 `Concurrency ceiling` 日志为准；
- `ceiling_source`：`inherited` / `policy` / `capped_by_capacity` 三态来源，UI 据此标注生效值出处；
- `persisted_enabled`：目标仓库 `.kedacode.toml` 中的持久值；文件缺失或键未设置时为 `null`。

### `PATCH /api/v1/agent-runner/backlog/autopilot`

只修改目标仓库 `.kedacode.toml` 的 `[agent_runner.autopilot].enabled`。请求体：

```json
{ "repo_id": "keda-main", "enabled": true }
```

成功响应体来自**写后 fresh load** 的生效配置，不回显请求体；两者的字段与 `GET` 一致。约束：

- 只改这一个布尔键；注释、同级键（`merge_method` / `require_verifier_pass` / `auto_sign_off` / `merge_check_timeout_seconds`）与未知子表逐字保留，写入采用同目录临时文件 + 完整加载校验 + `os.replace` 原子替换；
- 不会修改 `safety.auto_merge`，也不会自动启动或停止 daemon；
- 未知仓库返回 `400`；目标仓缺 `.kedacode.toml`、配置非法、不可写或写后读回不一致返回 `409`，且原文件保持不变。

### `GET /api/v1/agent-runner/backlog/ci-repair-global`

读取当前仓库的全局 CI/CD 自动修复开关（`post_pr_supervisor.auto_repair_ci`，默认 `false`）。每次调用 fresh load 配置。该开关与 `autopilot.enabled`、`safety.auto_merge`、`runner.fix_agent_enabled` 语义独立，互不联动。

```json
{ "repo_id": "keda-main", "global_enabled": false, "max_rounds": 2 }
```

### `PATCH /api/v1/agent-runner/backlog/ci-repair-global`

只修改目标仓库 `.kedacode.toml` 的 `[agent_runner.post_pr_supervisor].auto_repair_ci`。请求体 `{"repo_id": "...", "enabled": true}`；成功响应体来自写后 fresh load，字段与 `GET` 一致。写回复用受限配置编辑器（底层 `toml_section_editor.update_toml_table_keys` 原子替换），失败时原文件不变并返回 `409`。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/ci`

返回单个 PRD 的 CI/CD 交付尾段投影 `ci_delivery`（每次请求 fresh 读取 GitHub PR context 与 Issue marker，不持久化、不复用缓存）。该 DTO 同时是 `kc backlog ci status --json` 的机读输出，二者同构：

- `status`：`no_pr` / `pending` / `success` / `failure` / `unavailable`；`unavailable` 表示 GitHub 不可达或状态未知，按「未验证」呈现而非通过或失败；
- `round_count` / `max_rounds`：已发生的自动修复轮数与上限（复用 `post_pr_supervisor.max_repair_attempts`），轮次事实源是既有 `post_pr_rework_requested` marker 与 PR head SHA；
- `problems`：来自 GitHub `checks_summary` 的原始失败观察，不推断 job 日志或根因；
- `stored_policy` / `global_enabled` / `effective_enabled`：单 PRD 三态覆盖（`inherit/on/off`）、仓库全局值与服务端计算的最终生效值（`on → true`、`off → false`、`inherit → 全局值`）；
- `exhausted` / `exhausted_reason`：修复预算耗尽时停止自动副作用并持续显错。

### `PATCH /api/v1/agent-runner/backlog/prds/{encoded_path}/ci-policy`

设置单个 PRD 的策略覆盖，请求体 `{"repo_id": "...", "value": "inherit|on|off"}`。写入对应 GitHub Issue 的 latest-wins `iar:ci-auto-repair-policy` marker（`inherit` 表示清除显式覆盖），成功响应是**写后 fresh 回读评论流**得到的 `ci_delivery`。无关联 Issue 的 PRD 返回 `400`。

### `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/ci-repair`

显式请求一次修复（问题卡「立即修复」与 `kc backlog ci repair` 共用语义）。服务端 fresh 解析当前 PR head 并以 `head SHA + repair action` 为幂等键：同一失败轮次的重复请求零新增副作用；修复仍走既有 run 侧 repair 路径，保留轮数上限、worktree 与禁止路径门禁。无法获取当前 PR context 时返回 `409`。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/evidence`

列出某个 PRD 在仓库中**当前仍保留**的验收证据文件。每次请求重新读盘，不复用任何缓存。

查询参数：

- `repo_id`：仓库标识，必填。

```json
{
  "prd_path": "tasks/archive/P1-FEAT-20260101-demo.md",
  "prd_stem": "P1-FEAT-20260101-demo",
  "evidence_dir": "tasks/evidence/P1-FEAT-20260101-demo",
  "exists": true,
  "files": [
    {
      "name": "P1-FEAT-20260101-demo.evidence-report.md",
      "size_bytes": 2048,
      "media_type": "text/markdown",
      "role": "evidence_report",
      "artifact_token": "UDE1LUZFQVQtMjAyNjAxMDEtZGVtby5ldmlkZW5jZS1yZXBvcnQubWQ="
    }
  ]
}
```

- `evidence_dir` 由既有 `resolve_evidence_dir` 解析（默认 `tasks/evidence/<prd-stem>/`，显式 legacy 目录沿用扁平语义）；
- 只列一层普通非隐藏文件，`scripts/` 子目录与 oracle 源码不会混作证据；`size_bytes` 来自真实 `stat`，不从验收清单勾选数推断；
- 目录缺失时为 `exists: false` 且 `files` 为空列表，页面据此显示空态；PRD 路径越界返回 `400`。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/evidence/{artifact_token}`

受限读取单个证据文件。`artifact_token` 是文件名的 URL-safe base64 编码（取自 manifest 的 `artifact_token` 字段）。

- 文本类（`text/*`、`application/json`）以 `text/<type>; charset=utf-8` 内联返回；
- 图片类型内联返回（`Content-Disposition: inline`）；
- 其余类型以附件形式下载；
- 所有响应带 `X-Content-Type-Options: nosniff` 与 `Cache-Control: no-store`。

安全边界：token 解码后必须是纯 basename（拒绝 `/`、`\`、`\0`、`.`/`..` 与隐藏文件），解析后的真实路径必须是该 PRD 证据目录的**直接子文件**（挡住符号链接逃逸），单文件上限 10 MiB；任何越界请求返回 `400` 且不泄露仓外内容。

### `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/start`

启动单个 PRD（既有端点，行为不变）。三种 Backlog 视图（依赖图 / 时间轴 / 列表）现在都会经右侧统一详情走这一个端点；成功后该仓的 PRD 列表缓存会立即失效，下一次刷新即可看到新状态。请求体为 `{"repo_id": "..."}`；PRD 不在 `tasks/pending/` 或 `tasks/archive/` 内、或仍被上游依赖阻塞时，由既有依赖门禁返回 `400`。

## 模型模块

### `backend.infrastructure.models.model_loader`

::: backend.infrastructure.models.model_loader
    handler: python
    options:
      show_root_heading: true
      members_order: source

## CLI 机读契约

`kc` 对脚本与其他 agent 的调用面由三个模块定义，页面内容以代码为唯一事实源。

### 语义退出码 `backend.api.cli_exit_codes`

| 码 | 成员 | envelope `error` | 语义 |
|---|---|---|---|
| `0` | `SUCCESS` | `ok` | 请求完成 |
| `1` | `GENERAL` | `error` | 未归类失败（历史行为，只看非零的脚本不受影响） |
| `2` | `USAGE` | `usage_error` | 旗标或参数组合不成立 |
| `3` | `NOT_FOUND` | `not_found` | 目标不存在：仓库 / Issue（含显式定向时读不到或已关闭）/ registry 条目 / agent / 可执行文件 / 日志 |
| `4` | `PERMISSION` | `permission_denied` | 未授权：GitHub 未认证、registry 条目被禁用 |
| `5` | `CONFLICT` | `conflict` | 当前状态阻止：`--all-ready` 时同仓 daemon 正在轮询队列、显式定向的 Issue 被存活持有者认领或处于未解除的 `agent/blocked`、workflow 模板文件已安装、loop 条目已存在 |
| `10` | `DRY_RUN_OK` | `dry_run_ok` | 机器模式下 dry-run 校验通过且未写入任何东西 |

同一份表由 `EXIT_CODE_HELP` 印在 `kc --help` 与 `kc schema --json` 的顶层 `exit_codes.help` 中，`exit_codes.values` 则是「码 → 上表 `error` 名」的映射（与 envelope 同名，消费方不需要再翻译一次）；`translate_exit_code` 是「异常 → 退出码」的唯一落点（`FileNotFoundError` / `PermissionError` / `FileExistsError` 按语义归位，其余为 `1`）。

### 结构化错误 `backend.api.cli_output`

`--json` 是 `--output json` 的别名；默认永远是给人看的 `table`，非 TTY 不自动切换。机器模式下 `route_logs_to_stderr()` 把写向 stdout 的日志处理器改绑到 stderr，因此 stdout 只承载数据，序列化只有 `emit` / `emit_json` / `emit_ndjson` / `json_literal` 这一个出口。失败时 handler 抛 `CliError`，由 `cli.py` 中央调用 `render_cli_error` 落成 stderr envelope：

```json
{"error": "not_found", "message": "...", "suggestion": "kc registry list", "retryable": false, "exit_code": 3}
```

| 字段 | 含义 |
|---|---|
| `error` | 机器可读错误名，缺省按退出码推导（见上表） |
| `message` | 面向人类的说明，与人类模式文本同源 |
| `suggestion` | 一条可直接执行的下一步命令，无建议时为 `null` |
| `retryable` | 原样重试是否有可能成功 |
| `exit_code` | 与进程 `$?` 相同的语义码 |

### 运行时自省 `backend.api.cli_schema`

`kc schema --json` 从已注册的 Typer/click 命令树派生，不维护第二份命令清单：顶层为 `name` / `help` / `exit_codes` / `command_count` / `commands`；每条命令含 `path`、`help`、`arguments`、`options`；每个参数含名称、类型、是否必填、枚举取值、默认值与示例。`kc ask` 与 `kc deliberate` 的 `--output` 是输出目录（文本型），不在机器格式之列。`kc run --fast-merge` 作为 `run` 的一次性旗标由该自动派生暴露，不另立清单。

### PR 正文快速通道 marker `iar:fast-merge`

`kc run --fast-merge` 开出的 Draft PR 正文末尾带一条机器可读 marker（同族于 `iar:ci-auto-repair-policy` / `post_pr_rework_requested` 等 latest-wins marker）：

```html
<!-- iar:fast-merge issued=<Issue 编号> -->
```

- 语义：该 PR 经快速通道发布，builder 提交后**跳过了 Phase 4.5 的 rv re-exec 与独立 verifier 两道验证门禁**，未经自动化验证；正文同时附人读「合并前请人工验证」说明。
- 判别：**无此 marker 的 PR 才代表走了完整验证**。下游工具（merge queue / CI）可据此拦截或降级处理未验证 PR。
- 拒绝面：`--fast-merge` 与 `--all-ready` 组合、或目标 Issue 声明 `stack` 顺序依赖（`iar:depends-on ... mode="stack"`）时，以 `USAGE`（退出码 `2`）拒绝，不启动 agent、不产生 PR。该旗标不进入 daemon、无配置项，仅作用于当次显式调用。
