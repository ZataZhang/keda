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

## 模型模块

### `backend.infrastructure.models.model_loader`

::: backend.infrastructure.models.model_loader
    handler: python
    options:
      show_root_heading: true
      members_order: source
