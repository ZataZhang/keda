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

`forbidden_path_exceptions` 使用与 `forbidden_path_patterns` 相同的 `fnmatch` 通配语义；路径或文件名命中例外后会跳过禁改拦截。例外会覆盖任意禁改模式，因此应只列出范围明确的安全文件。

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
    "review_label": "agent/automated-review-passed",
    "failed_label": "agent/failed",
    "base_branch": "main",
    "remote": "origin",
    "auto_merge": false,
    "forbidden_path_patterns": [".env", ".env.*", "secrets/*"],
    "forbidden_path_exceptions": [".env.example", ".env.*.example"]
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
      "has_repository_config": true,
      "already_registered": false,
      "suggested_repo_id": "foo"
    }
  ]
}
```

字段说明：

- `path` / `parent` / `home`：当前目录、上级目录（已是文件系统根时为 `null`）与用户主目录。
- `suggested_repo_id` / `suggested_display_name`：用于回填「添加仓库」表单的建议值，`suggested_repo_id` 由后端按 `normalize_repository_id` 规则从目录名生成，与 `kc registry` 扫描保持一致。
- `directories[].is_git_repo` / `has_repository_config` / `already_registered`：该子目录是否为 git 仓库、是否已 `kc init`、是否已在 registry 中注册。

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

设置单个 PRD 的策略覆盖，请求体 `{"repo_id": "...", "value": "inherit|on|off"}`。写入对应 GitHub Issue 的 latest-wins `kc:ci-auto-repair-policy` marker（`inherit` 表示清除显式覆盖），成功响应是**写后 fresh 回读评论流**得到的 `ci_delivery`。无关联 Issue 的 PRD 返回 `400`。

### `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/ci-repair`

显式请求一次修复（问题卡「立即修复」与 `kc backlog ci repair` 共用语义）。服务端 fresh 解析当前 PR head 并以 `head SHA + repair action` 为幂等键：同一失败轮次的重复请求零新增副作用；修复仍走既有 run 侧 repair 路径，保留轮数上限、worktree 与禁止路径门禁。无法获取当前 PR context 时返回 `409`。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/evidence`

列出某个 PRD 在仓库中**当前仍保留**的验收证据文件。每次请求重新读盘，不复用任何缓存。运行中的 PRD 走「分支优先、主仓库兜底」：先定位其执行工作区，同名文件取工作区版本，主仓库独有文件保留。

查询参数：

- `repo_id`：仓库标识，必填。

```json
{
  "prd_path": "tasks/pending/P1-FEAT-20260101-demo.md",
  "prd_stem": "P1-FEAT-20260101-demo",
  "evidence_dir": "tasks/evidence/P1-FEAT-20260101-demo",
  "exists": true,
  "status": "complete",
  "files": [
    {
      "name": "P1-FEAT-20260101-demo.evidence-report.md",
      "size_bytes": 2048,
      "media_type": "text/markdown",
      "role": "evidence_report",
      "artifact_token": "UDE1LUZFQVQtMjAyNjAxMDEtZGVtby5ldmlkZW5jZS1yZXBvcnQubWQ=",
      "source": "worktree"
    }
  ]
}
```

- `evidence_dir` 由既有 `resolve_evidence_dir` 解析（默认 `tasks/evidence/<prd-stem>/`，显式 legacy 目录沿用扁平语义）；
- 只列一层普通非隐藏文件，`scripts/` 子目录与 oracle 源码不会混作证据；`size_bytes` 来自真实 `stat`，不从验收清单勾选数推断；
- `files[].source`：`worktree`（执行工作区、尚未合并）或 `main`（主仓库）。无候选工作区时统一为 `main`；页面据此对未合并文件加「未合并」标注；
- `status`：`complete` 表示候选目录成功枚举；`unavailable` 表示工作区发现失败或多重匹配，或某个存在的候选目录枚举失败（无权限等）——此时 `files` 只是可读取到的部分，**不得**把结果当作「确认无文件」的空态呈现；
- 目录缺失且来源可确定为 `exists: false` 且 `files` 为空列表，页面据此显示空态；PRD 路径越界返回 `400`。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/evidence/{artifact_token}`

受限读取单个证据文件。`artifact_token` 是文件名的 URL-safe base64 编码（取自 manifest 的 `artifact_token` 字段）。同名文件按「分支优先、主仓库兜底」解析，第一个命中即返回；校验规则不变。

- 文本类（`text/*`、`application/json`）以 `text/<type>; charset=utf-8` 内联返回；
- 图片类型内联返回（`Content-Disposition: inline`）；
- 其余类型以附件形式下载；
- 所有响应带 `X-Content-Type-Options: nosniff` 与 `Cache-Control: no-store`。

安全边界：token 解码后必须是纯 basename（拒绝 `/`、`\`、`\0`、`.`/`..` 与隐藏文件），解析后的真实路径必须是某个候选证据目录的**直接子文件**（挡住符号链接逃逸，各候选独立 containment 到本候选根），单文件上限 10 MiB；任何越界请求返回 `400` 且不泄露仓外内容。

### `GET /api/v1/agent-runner/backlog/prds/{encoded_path}/progress`

聚合单个 PRD 的交付进度读数（清单 / 证据计划与产出 / 影响树 / 工作区）。每次请求 fresh 读盘，不复用列表快照或缓存。运行中的读数来自真实执行工作区（主仓库兜底），主仓库归档副本是终态事实来源。**所有读数都是弱信号**：文件归属 ≠ 验证通过，影响树触碰 ≠ 改对；不可比较用 `null`，读取失败用各字段状态单独披露，绝不以空数组或零冒充成功读取。

查询参数：

- `repo_id`：仓库标识，必填。

```json
{
  "prd_path": "tasks/pending/P1-FEAT-20260101-demo.md",
  "prd_stem": "P1-FEAT-20260101-demo",
  "prd_source": "worktree",
  "worktree_status": "matched",
  "worktree": { "branch": "issue-256" },
  "checklist": { "status": "available", "checked": 3, "total": 12 },
  "evidence": {
    "planned": [
      { "id": "rv-1", "summary": "运行中详情显示交付读数", "reviewer": "human" },
      { "id": "rv-2", "summary": "读取边界与工作区优先级正确", "reviewer": "verifier" },
      { "id": "rv-3", "summary": "影响树读数与 CLI 一致", "reviewer": "verifier" }
    ],
    "produced_ids": ["rv-1", "rv-3"],
    "produced_planned_count": 2,
    "file_count": 4,
    "attribution_source": "manifest",
    "status": "complete",
    "reports": { "verification_plan": true, "evidence_report": false, "verifier_report": false }
  },
  "impact_status": "available",
  "impact": { "touched": 7, "judgeable": 12, "unresolvable": 2 }
}
```

字段语义：

- `prd_source`：`worktree` / `main` / `null`（来源不可用）；`worktree_status`：`matched`（唯一命中）/ `none`（检索成功但未命中）/ `unavailable`（无法检索）/ `ambiguous`（多重匹配）/ `not_applicable`（主仓库归档终态，无需匹配）；`worktree` 只返回分支名，不返回本地路径。
- `checklist.status`：`available` / `unavailable`；仅成功读取的空清单记 `0/0`（前端显示 `-`），失败时 `checked/total` 为 `null`。
- `evidence.planned`：§7.6 验证计划顶层条目（编号 / 摘要 / `human|verifier`）；成功读取但无计划时为 `[]`，来源不可用时为 `null`（不伪造空计划）。`produced_planned_count` 是有可读取文件的不同计划条目数，`file_count` 是合并去重后可展示文件数。`attribution_source`：`manifest`（清单 `item_number` 映射）/ `filename`（`rv-<n>` 前缀回退）/ `mixed` / `none`。`reports` 三件套在完整读取时为布尔，不完整时未知项为 `null`。
- `evidence.status`：`complete` 表示候选目录成功枚举；`unavailable` 表示来源不可用或某个存在的候选目录枚举失败（无权限等）——此时 `file_count` 与 `produced_planned_count` 为 `null`、`reports` 未知项为 `null`，不得折算成 0。
- `impact`：`impact_status=available` 时给出 `touched/judgeable/unresolvable`（无法判定节点以 `?n` 披露、不进分母）。`impact` 为 `null` 时由 `impact_status` 说明原因：`terminal_archive`（归档终态不参与分支比较）/ `no_worktree` / `no_tree` / `no_nodes` / `no_base`（无有效 merge-base，显示 `-` 而非 `~0/y`）/ `unavailable`（查询或 git 命令失败）。
- 归档终态规则同样适用于 progress：主仓库已归档时直接读主仓库、`worktree_status=not_applicable`、`impact_status=terminal_archive`，不受 worktree 查询失败影响。PRD 路径非法/不存在返回 `400`。

### `POST /api/v1/agent-runner/backlog/prds/{encoded_path}/start`

启动单个 PRD。三种 Backlog 视图（依赖图 / 时间轴 / 列表）现在都会经右侧统一详情走这一个端点；成功后该仓的 PRD 列表缓存会立即失效，下一次刷新即可看到新状态。基础请求体为 `{"repo_id": "..."}`；可选的 `auto_merge_prd: true` 会给该 Issue 设置单 PRD 自动合并授权标签，只跳过独立 verifier，保留仓库验证命令、PR checks 和分支保护。该选项要求 `autopilot.enabled = true`，但不要求打开全局 `safety.auto_merge`；合并后不会修改 PRD 人工验收记录。PRD 不在 `tasks/pending/` 或 `tasks/archive/` 内、或仍被上游依赖阻塞时，由既有依赖门禁返回 `400`。

## 模型评测 Console 端点

多执行预设 × 多评审模型的 Agent/模型效果对照评测（Console「Evaluations」页的后端）。功能语义、suite YAML 规范与授权边界见 [评测与测试](../dev/evaluation.md)。所有端点位于 `/api/v1/agent-runner/console/evaluation/` 之下；动作级错误按分类映射 HTTP 状态：`not_found → 404`、`conflict → 409`、`invalid → 400`，`detail` 为可读原因。

### `GET /api/v1/agent-runner/console/evaluation/suites`

列出 `tasks/evaluation-suites/` 下的评测集与逐文件校验结果（维护者自查入口，不需要任何凭据）。条目字段：`file`、`valid`、`errors`；校验通过时另含 `suite_id`、`version`、`repo_path`、`base_ref`、`content_hash`、`rubric_version`、`check_timeout_seconds` 与 `cases`。同一目录内 `suite_id` 重复时，涉及的两个文件**一起**返回 `valid=false`（原因含「重复定义」），与 registry README 口径一致。

### `GET /api/v1/agent-runner/console/evaluation/presets`

页面下拉数据：`execution_presets`（来自既有 `[agent_runner.presets.<name>]`）、`judge_presets`（评审预设即同一份执行预设，逐项带 `agent` / `model` / `reasoning_effort` 与 `available` / `reason` 可用性探针——只检查 agent 是否声明禁用工具的 `judge` 形态且 CLI 在 PATH 上，**不起进程、不发起网络调用**；codex / kimi / opencode 等无该形态的 agent 为 `available=false`）、`default_judge_presets` 与并发/超时配置。所有条目 `cost_status` 固定为 `unknown`。

### `GET /api/v1/agent-runner/console/evaluation/runs` / `POST .../runs`

列表按创建时间倒序返回全部 run；创建请求体 `{"suite_id": "...", "execution_presets": ["..."], "judge_presets": ["..."]}`（`execution_presets` 至少一个；`judge_presets` 可为空，表示本轮只做确定性检查对照、不进入评审授权）。创建是**全量 fail-fast 校验 + 快照冻结**（suite hash、base commit、预设快照、矩阵上限 32 单元），成功返回 `201` 与 `status=created` 的 run；不启动任何执行，也不发起任何 provider 调用。

### `GET /api/v1/agent-runner/console/evaluation/runs/{run_id}`

run 详情：`run`、`results`（逐候选执行）、`scores`（逐评审评分，含全部失败态）、`summary`（只从 `valid` 评分行实时派生的每维中位数 / min / max / N，不落库）与 `payload_state`（`is_active` / `awaiting_consent` / `judge_selected`）。不存在返回 404。

### `POST .../runs/{run_id}/start` / `POST .../runs/{run_id}/cancel` / `DELETE .../runs/{run_id}`

- start：`created → queued` 并排程候选执行；本动作**绝不**触发评审调用。
- cancel：仅 `created/queued/running/awaiting_judge_consent` 可取消（否则 409，`judging` 已获授权不撤回）；取消把未完成候选落 `cancelled` 状态并清除暂存 payload。在 `awaiting_judge_consent` 上取消时，还会为已产出的候选 × 评审者写入 `cancelled` 评分行（与 deny 的 `unauthorized` 同理，不产生任何分数）。
- delete：级联删除 run 的 DB 行、结果、评分与本机暂存 payload / worktree；不存在返回 404。

### `GET .../runs/{run_id}/judge-preview`

返回与实际发送**逐字节一致**的 payload 预览：逐条 `payload_preview` / `payload_sha256`、`manifest_hash`、run 冻结的 `frozen_manifest_hash`、`estimated_requests`（条目 × 评审者数）与敏感内容提示。预览上限覆盖评审 payload 的最坏尺寸（diff 字节上限加固定开销）；仅极端超长内容会截断并置 `payload_truncated=true`，此时 hash 仍绑定完整暂存文件。run 不处于 `awaiting_judge_consent` 或暂存 manifest 缺失时返回 409。

### `POST .../runs/{run_id}/judge-consent`

显式授权动作，请求体 `{"decision": "approve" | "deny", "manifest_hash": "..."}`。服务端在发出任何评审请求前重新核对请求 hash == run 冻结 hash == 当前暂存文件重算 hash，任何不一致返回 **409**（过期须重新预览）且零外发：

- approve：`awaiting_judge_consent → judging` 并排程评审调用；
- deny：run 收尾为 `completed`，为每个候选 × 评审者写入 `unauthorized` 评分行（区分「评审者被拒绝」与「评审者缺席」），并清除暂存 payload。

### 状态枚举与外发/密钥边界

- run 状态：`created / queued / running / awaiting_judge_consent / judging / completed / cancelled / interrupted`（Console 重启时活跃 run 一律自愈为 `interrupted`；不提供续跑，用户删除该 run 后用同一 suite 新建 run 重跑）。
- 候选结果状态：`pending / running / succeeded / failed / timeout / cancelled`；check 状态：`passed / failed / timeout / not_run`。
- 评分状态：`valid / invalid / refusal / timeout / provider_error / unauthorized / cancelled`——**任何非 valid 状态都不折算为 0 分**，聚合只统计 valid 行；`unauthorized` / `cancelled` 是授权前 deny / 取消的留痕行，分数恒为空。
- 默认不开启评审外发：judge 调用只可能发生在 approve 之后；payload 与响应 hash 落本地账本，持久层不保存 raw prompt、完整 patch、密钥值或环境变量。

### `GET /api/v1/agent-runner/console/runtime/tasks`

值守看板（`/app/dashboard/` 默认视图）的任务摘要，只读。**不新增任何存储**：任务身份与标签来自监控快照，运行终态来自运行账本，阶段 / preset / 模型来自 attempt 账本，PRD 关联来自生命周期账本。

- 数据范围严格绑定当前 registry 的启用仓库：账本按 `repo_id` 逐仓库读取，已删除或禁用的仓库即使仍有历史运行行也不会回到看板。
- GitHub Issue 为 closed 时优先归入 `closed`，不受残留 `agent/*` 标签影响，也不表示 Agent 已成功完成；open Issue 再按 `agent/*` 标签推导状态，其中 `agent/blocked` 对应 `blocked`、`agent/failed` 对应 `failed`、`agent/review` 与 `agent/ready` 同归 `waiting`。没有可判定标签时依次回落到运行账本终态、`unknown`，未知不折算成零。
- 分桶顺序为 `running` → `blocked` → `failed` → `waiting` → `completed` → `closed` → `unknown`，同状态内按开始时间新者在前。
- `status_reason` 说明状态来源（当前监控标签、Issue 关闭状态或运行账本终态）；监控异常只放在独立的 `anomaly_types` 列表中，不覆盖状态来源，也不直接表示失败原因。
- `duration_seconds` 对运行中的任务由「attempt 开始时间 → 生成时间」推算，是如实的已耗时，不是任何百分比。

```json
{
  "generated_at": "2026-10-12T12:00:00+00:00",
  "tasks": [
    {
      "repo_id": "keda",
      "issue_number": 283,
      "task_key": "keda#283",
      "repo_display_name": "KedaCode",
      "issue_title": "统一运行时看板与桌面进度窗",
      "issue_url": "https://github.com/…/issues/283",
      "status": "running",
      "status_reason": "当前监控标签：agent/running",
      "anomaly_types": [],
      "phase": "implement",
      "agent": "claude",
      "preset": "fast",
      "model": "…",
      "attempt_number": 2,
      "prd_path": "tasks/archive/P1-FEAT-20261012-103222-unified-runtime-dashboard.md",
      "started_at": "2026-10-12T11:30:00+00:00",
      "finished_at": null,
      "duration_seconds": 1800.0,
      "last_outcome": null,
      "labels": ["agent/running"]
    }
  ],
  "status_counts": {
    "running": 1,
    "waiting": 0,
    "blocked": 0,
    "failed": 0,
    "completed": 0,
    "closed": 0,
    "unknown": 0
  },
  "unavailable_sources": [],
  "missing_repo_ids": [],
  "sync_status": "ready",
  "scanned_at": "2026-10-12T11:59:00+00:00",
  "truncated": false
}
```

字段语义：

- `unavailable_sources`：读取失败或整体不可信的数据来源名（`monitor_snapshot` / `run_history` / `prd_lifecycle` / `attempt_ledger`）。任一来源失败只把该来源标为不可用，不折叠成「没有任务」。
- `sync_status` / `scanned_at` / `missing_repo_ids`：直接透传快照读路径的结果，`pending_first_sync` 表示整体还不可信。
- `truncated`：任务数超过 100 行上限时为 `true`，此时列表是排序后的前 100 条；历史复盘请看 Stats 页面。
- **改动树触达读数不在本响应里**：摘要只带 `prd_path`，展示侧按它调用 `GET …/backlog/prds/{encoded_path}/progress`，避免出现第二份影响树检测逻辑。

### `GET /api/v1/agent-runner/console/desktop-companion/status`

桌面小窗状态探针，只读，网页看板与原生 helper 共用。GET 不要求 `Origin`（浏览器对同源 GET 可能不带该头）。

```json
{
  "status": "running",
  "console_origin": "http://127.0.0.1:8765",
  "console_session": "3f2b…",
  "focus_ticket": 2,
  "close_ticket": 0,
  "detail": null,
  "hint": "桌面小窗正在运行。",
  "window_exists": true,
  "companion_url": "http://127.0.0.1:8765/app/companion/"
}
```

`status` 取值：`launched` / `focused` / `running` / `idle` / `not_installed` / `unsupported_platform` / `failed`。`companion_url` 仅在 `window_exists=true` 时非空，避免把「可以开」画成「已经开着」。`console_session` 是本次 Console 进程的身份串，helper 用它识别上一个 Console 留下的孤儿窗口并自行退出。

### `POST /api/v1/agent-runner/console/desktop-companion/window`

开启或聚焦桌面小窗。响应体与 status 探针同构，`status` 为 `launched`（新建）或 `focused`（已存在，只带到前台）。

请求来源约束（**硬性**，任一不满足即拒绝且不起进程）：

- 绑定地址必须来自 ASGI `scope["server"]`（由 uvicorn 按真实 bind 写入，不读 `Host` 头，因此伪造 `Host` 无效），且主机在本机回环集合（`127.0.0.1` / `localhost` / `::1`）内；非回环绑定返回 `403`。
- `Origin` 必须存在、scheme 为 `http`、主机是回环别名、端口与 Console 实际绑定端口一致、且不带 path / query / fragment；不一致返回 `403`，缺失返回 `403`。
- 请求体不接受任何命令、主机、端口或路径参数：helper 的加载地址固定为 `{由绑定推出的 console_origin}/app/companion/`（IPv6 绑定会补方括号），子进程 argv 固定；`Origin` 头只参与同源判定，不会成为窗口地址。

同一个 Console 进程最多一个窗口：重复调用只递增 `focus_ticket`，由 helper 自己提前台，不再新建进程。窗口身份还带 `console_session`：共用同一状态目录的另一个 Console 进程认不出别人的窗口，既不会把它报成「已聚焦」，也不会替对方关窗，各入口开各自的小窗。

### `POST /api/v1/agent-runner/console/desktop-companion/window/close`

请求小窗自行关窗退出（同样要求同源 `Origin`）。后端只递增 `close_ticket`，**不 kill 进程**，因此窗口不会被半路截断；返回的 `status` 在 helper 退出后为 `idle`。

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

### PR 正文快速通道 marker `kc:fast-merge`

`kc run --fast-merge` 开出的 Draft PR 正文末尾带一条机器可读 marker（同族于 `kc:ci-auto-repair-policy` / `post_pr_rework_requested` 等 latest-wins marker）：

```html
<!-- kc:fast-merge issued=<Issue 编号> -->
```

- 语义：该 PR 经快速通道发布，builder 提交后**跳过了 Phase 4.5 的 rv re-exec 与独立 verifier 两道验证门禁**，未经自动化验证；正文同时附人读「合并前请人工验证」说明。
- 判别：**无此 marker 的 PR 才代表走了完整验证**。下游工具（merge queue / CI）可据此拦截或降级处理未验证 PR。
- 拒绝面：`--fast-merge` 与 `--all-ready` 组合、或目标 Issue 声明 `stack` 顺序依赖（`kc:depends-on ... mode="stack"`）时，以 `USAGE`（退出码 `2`）拒绝，不启动 agent、不产生 PR。该旗标不进入 daemon、无配置项，仅作用于当次显式调用。
