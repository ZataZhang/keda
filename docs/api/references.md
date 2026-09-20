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
- `suggested_repo_id` / `suggested_display_name`：用于回填「添加仓库」表单的建议值，`suggested_repo_id` 由后端按 `normalize_repository_id` 规则从目录名生成，与 `iar registry` 扫描保持一致。
- `directories[].is_git_repo` / `has_iar_config` / `already_registered`：该子目录是否为 git 仓库、是否已 `iar init`、是否已在 registry 中注册。

### `GET /api/v1/agent-runner/roadmap/prds/{encoded_path}/content`

返回单个 PRD 的 Markdown 原文（`text/plain; charset=utf-8`，与磁盘文件逐字节一致）。该端点为**只读**。

`encoded_path` 是 PRD 相对路径的 URL-safe base64 编码，与 `POST /api/v1/agent-runner/roadmap/prds/{encoded_path}/start` 使用同一约定；路径本身取自 `GET /api/v1/agent-runner/roadmap/prds` 列表响应中的 `prd_path`。

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
  "http://127.0.0.1:8000/api/v1/agent-runner/roadmap/prds/${encoded_path}/content?repo_id=keda-main"
```

### `GET /api/v1/agent-runner/roadmap/autopilot`

返回当前仓库 Autopilot 的完整闭环状态。每次调用都重新加载配置（**不复用** `GET /roadmap/prds` 的 30 秒缓存），以保证页面展示的是写后读回的真实值。

查询参数：

- `repo_id`：仓库标识，必填。

响应字段：

```json
{
  "repo_id": "keda-main",
  "enabled": true,
  "auto_merge_enabled": false,
  "daemon_running": true,
  "max_parallel": 2,
  "config_source": ".iar.toml",
  "persisted_enabled": true
}
```

- `enabled`：生效配置中的 `agent_runner.autopilot.enabled`；
- `auto_merge_enabled`：`safety.auto_merge`，即第二道危险动作门禁（只读展示）；
- `daemon_running`：该仓库是否存在 `kind=daemon` 且存活的进程（来自既有 process supervisor 记录）；
- `persisted_enabled`：目标仓库 `.iar.toml` 中的持久值；文件缺失或键未设置时为 `null`。

### `PATCH /api/v1/agent-runner/roadmap/autopilot`

只修改目标仓库 `.iar.toml` 的 `[agent_runner.autopilot].enabled`。请求体：

```json
{ "repo_id": "keda-main", "enabled": true }
```

成功响应体来自**写后 fresh load** 的生效配置，不回显请求体；两者的字段与 `GET` 一致。约束：

- 只改这一个布尔键；注释、同级键（`merge_method` / `require_verifier_pass` / `auto_sign_off` / `merge_check_timeout_seconds`）与未知子表逐字保留，写入采用同目录临时文件 + 完整加载校验 + `os.replace` 原子替换；
- 不会修改 `safety.auto_merge`，也不会自动启动或停止 daemon；
- 未知仓库返回 `400`；目标仓缺 `.iar.toml`、配置非法、不可写或写后读回不一致返回 `409`，且原文件保持不变。

### `GET /api/v1/agent-runner/roadmap/prds/{encoded_path}/evidence`

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

### `GET /api/v1/agent-runner/roadmap/prds/{encoded_path}/evidence/{artifact_token}`

受限读取单个证据文件。`artifact_token` 是文件名的 URL-safe base64 编码（取自 manifest 的 `artifact_token` 字段）。

- 文本类（`text/*`、`application/json`）以 `text/<type>; charset=utf-8` 内联返回；
- 图片类型内联返回（`Content-Disposition: inline`）；
- 其余类型以附件形式下载；
- 所有响应带 `X-Content-Type-Options: nosniff` 与 `Cache-Control: no-store`。

安全边界：token 解码后必须是纯 basename（拒绝 `/`、`\`、`\0`、`.`/`..` 与隐藏文件），解析后的真实路径必须是该 PRD 证据目录的**直接子文件**（挡住符号链接逃逸），单文件上限 10 MiB；任何越界请求返回 `400` 且不泄露仓外内容。

### `POST /api/v1/agent-runner/roadmap/prds/{encoded_path}/start`

启动单个 PRD（既有端点，行为不变）。三种 Roadmap 视图（依赖图 / 时间轴 / 列表）现在都会经右侧统一详情走这一个端点；成功后该仓的 PRD 列表缓存会立即失效，下一次刷新即可看到新状态。请求体为 `{"repo_id": "..."}`；PRD 不在 `tasks/pending/` 或 `tasks/archive/` 内、或仍被上游依赖阻塞时，由既有依赖门禁返回 `400`。

## 模型模块

### `backend.infrastructure.models.model_loader`

::: backend.infrastructure.models.model_loader
    handler: python
    options:
      show_root_heading: true
      members_order: source
