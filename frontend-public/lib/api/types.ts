// Shared API types used by the frontend.
// Keep these aligned with the backend dataclasses under
// `src/backend/core/use_cases/agent_runner_monitor.py`.

export type UserSession = {
  user_id: string;
  display_name: string;
  email: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Agent Runner Monitoring Dashboard
// ─────────────────────────────────────────────────────────────────────────────

export type AnomalySeverity = "warning" | "error";

export type AnomalyType =
  | "label_pr_mismatch"
  | "pr_dirty_in_review"
  | "dirty_worktree_mismatch"
  | "event_label_mismatch";

export type Anomaly = {
  type: AnomalyType;
  severity: AnomalySeverity;
  message: string;
  suggested_cli: string[];
};

export type QueueLabels = {
  ready: string;
  running: string;
  supervising: string;
  review: string;
  failed: string;
  blocked: string;
};

export type RepositoryHealth = {
  gh_available: boolean;
  repo_path_exists: boolean;
  publish_remote_exists: boolean;
};

export type WorktreeStatus = {
  exists: boolean;
  path: string;
  branch: string;
  head_sha: string;
  is_clean: boolean;
  dirty_files: string[];
};

export type PullRequestContext = {
  number: number | null;
  url: string;
  branch: string;
  head_sha: string;
  base_sha: string;
  mergeable: boolean | null;
  checks_state: string | null;
  checks_summary: string[];
};

export type EventTimelineEntry = {
  phase: string;
  cycle: number;
  comment_index: number;
  action: string | null;
  head_sha: string | null;
  pr_branch: string | null;
  checks_state: string | null;
  mergeable: boolean | null;
  raw_marker: string;
};

export type LatestEventMarker = {
  version: number;
  phase: string;
  cycle: number;
  head_sha: string | null;
  base_sha: string | null;
  pr_branch: string | null;
  action: string | null;
  checks_state: string | null;
  mergeable: boolean | null;
  issue_comments_count: number | null;
  pr_comments_count: number | null;
};

export type IssueMonitoringSnapshot = {
  number: number;
  title: string;
  url: string;
  labels: string[];
  state: string;
  primary_label: string;
  pr: PullRequestContext | null;
  worktree: WorktreeStatus;
  timeline: EventTimelineEntry[];
  latest_event: LatestEventMarker | null;
  anomalies: Anomaly[];
  suggested_cli_commands: string[];
  has_anomaly: boolean;
  anomaly_types: AnomalyType[];
};

export type QueueCounts = {
  ready: number;
  running: number;
  supervising: number;
  review: number;
  failed: number;
  blocked: number;
};

export type AnomalySummary = {
  warning: number;
  error: number;
};

export type RepositoryMonitoringOverview = {
  repo_id: string;
  display_name: string;
  enabled: boolean;
  base_branch: string;
  remote: string;
  health: RepositoryHealth;
  queue_counts: QueueCounts;
  labels: QueueLabels;
  issues: IssueMonitoringSnapshot[];
  anomaly_count: number;
  anomaly_summary: AnomalySummary;
  scanned_at: string;
};

export type MonitoringOverview = {
  repositories: RepositoryMonitoringOverview[];
  scanned_at: string;
  unreachable_repositories?: UnreachableRepository[];
};

// ── 本地快照与后台同步（GET /overview/snapshots、GET|PATCH /console/monitor/settings）

/** 快照读取接口返回的单个仓库条目；`overview` 与 per-repo overview 结构一致。 */
export type MonitorSnapshotEntry = {
  repo_id: string;
  scanned_at: string;
  overview: RepositoryMonitoringOverview;
};

/** 后端对当前快照覆盖情况的整体判定。 */
export type MonitorSyncStatus = "ready" | "partial" | "pending_first_sync";

export type MonitorSnapshotsResponse = {
  repositories: MonitorSnapshotEntry[];
  missing_repo_ids: string[];
  sync_status: MonitorSyncStatus;
  /** 各仓库快照时间的最大值；无任何快照时为 null。 */
  scanned_at: string | null;
  unreachable_repositories: UnreachableRepository[];
};

/** 全局后台同步设置（开关 + 间隔秒数）。 */
export type MonitorSettings = {
  sync_enabled: boolean;
  sync_interval_seconds: number;
  updated_at: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Agent Runner Operations Console
// Keep these aligned with the backend dataclasses under
// `src/backend/core/shared/interfaces/runner_console.py`.
// ─────────────────────────────────────────────────────────────────────────────

export type RunnerProcessKind =
  | "daemon"
  | "review_daemon"
  | "run_once"
  | "review_once"
  | "blocked_continue";

export type RunnerProcessStatus = "running" | "exited" | "stopped" | "killed";

export type RunnerProcessRecord = {
  process_id: string;
  repo_id: string;
  kind: RunnerProcessKind;
  pid: number;
  status: RunnerProcessStatus;
  exit_code: number | null;
  log_path: string;
  command: string[];
  started_at: string;
  stopped_at: string | null;
};

export type ProcessLogChunk = {
  content: string;
  next_offset: number;
  eof: boolean;
};

// Issue 实时输出（与后端 `issue_logs` 用例的读取状态对齐）。
export type IssueLogStatus =
  | "ok"
  | "no_attempt"
  | "attempt_gone"
  | "truncated"
  | "repo_not_found";

export type IssueLogChunk = {
  repo_id: string;
  issue_number: number;
  status: IssueLogStatus;
  attempt_id: string | null;
  latest_attempt_id: string | null;
  content: string;
  next_offset: number;
  eof: boolean;
};

export type ConsoleActionResult = {
  action: string;
  result: "accepted" | "rejected" | "error";
  detail: string;
  process: RunnerProcessRecord | null;
};

export type RepositoryCompletionStats = {
  repo_id: string;
  display_name: string;
  total_tracked: number;
  completed: number;
  failed: number;
  blocked: number;
  open_in_pipeline: number;
  completion_rate: number | null;
  truncated: boolean;
  error: string | null;
};

export type DailyRunTrendEntry = {
  day: string;
  completed: number;
  failed: number;
  blocked: number;
  average_duration_seconds: number | null;
};

export type RunRecordEntry = {
  repo_id: string;
  repo_path: string;
  issue_number: number;
  trigger: string;
  agent: string;
  outcome: "completed" | "failed" | "blocked";
  error_summary: string | null;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
};

export type AuditEntry = {
  occurred_at: string;
  actor: string;
  action: string;
  repo_id: string | null;
  issue_number: number | null;
  params_json: string;
  result: "accepted" | "rejected" | "error";
  detail: string | null;
};

export type RegistryRepositoryEntry = {
  repo_id: string;
  path: string;
  enabled: boolean;
  display_name: string | null;
  path_exists: boolean;
};

/**
 * console 进程 cwd 与 registry 的匹配结果（`GET /console/context`）。
 *
 * `status` 取值：`matched` / `not_git_repo` / `not_registered` / `disabled` /
 * `ambiguous`。只有 `matched` 时 `repo_id` 才有值。
 */
export type ConsoleContext = {
  cwd: string;
  git_root: string | null;
  repo_id: string | null;
  status: "matched" | "not_git_repo" | "not_registered" | "disabled" | "ambiguous";
  candidates: string[];
};

export type DiscoveredRepositoryEntry = {
  repo_id: string;
  path: string;
  display_name: string | null;
  already_registered: boolean;
};

export type BrowsableDirectoryEntry = {
  name: string;
  path: string;
  is_git_repo: boolean;
  has_iar_config: boolean;
  already_registered: boolean;
  suggested_repo_id: string;
};

export type DirectoryBrowseResult = {
  path: string;
  parent: string | null;
  home: string;
  suggested_repo_id: string;
  suggested_display_name: string;
  directories: BrowsableDirectoryEntry[];
};

export type BatchAddRepositoriesResult = {
  added: RegistryRepositoryEntry[];
  skipped: string[];
  errors: { repo_id: string; detail: string }[];
};

export type UnreachableRepository = {
  repo_id: string;
  display_name: string;
  configured_path: string;
  error: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Backlog
// Keep these aligned with the backend dataclasses under
// `src/backend/core/shared/models/backlog.py`.
// ─────────────────────────────────────────────────────────────────────────────

export type BacklogPrdState =
  | "not_started"
  | "ready"
  | "running"
  | "supervising"
  | "review"
  | "failed"
  | "blocked"
  | "merged"
  | "archived"
  | "unresolved_dependency"
  | "waiting";

export type BacklogDependencyKind = "prd" | "issue" | "unresolved";

export type BacklogDependency = {
  from_path: string;
  to_path: string;
  kind: BacklogDependencyKind;
  detail: string;
};

export type BacklogNextAction = {
  label: string;
  url: string | null;
};

export type BacklogPrd = {
  prd_path: string;
  title: string;
  status: "pending" | "archived";
  priority: string;
  issue_url: string | null;
  issue_number: number | null;
  state: BacklogPrdState;
  acceptance_total: number;
  acceptance_checked: number;
  delivery_dependencies: BacklogDependency[];
  updated_at: string;
  block_reason: string | null;
  next_action: BacklogNextAction | null;
};

/** Backlog 列表读路径的响应：数据来自后端本地快照，新鲜度由 stale 如实交代。 */
export type BacklogPrdsResponse = {
  prds: BacklogPrd[];
  skipped: string[];
  repo_id: string;
  include_archived: boolean;
  /** 快照构建时间；还没有可用快照时为 null，此时页面显示「正在同步」。 */
  scanned_at: string | null;
  /** 快照缺失、损坏或已超过后端 TTL：后台正在重扫，前端应短间隔继续轮询。 */
  stale: boolean;
};

export type BacklogSettings = {
  repo_id: string;
  max_parallel: number;
  default_view: "timeline" | "list";
  updated_at: string;
};

export type BacklogActionResult = {
  prd_path: string;
  issue_number: number | null;
  state: BacklogPrdState;
  detail: string;
};

export type BacklogGlobalStartResult = {
  started: BacklogActionResult[];
  queued: string[];
  skipped: string[];
};

/**
 * 仓库级 Backlog 自动推进状态与相关运行条件。
 *
 * `enabled` 是生效的 `backlog.auto_advance` 值（写后 fresh load），
 * `persisted_enabled` 是仓库 `.kedacode.toml` 里的值（缺失时为 null）。
 * `auto_merge_enabled` 表示自动合并的两道配置门都已开启，`daemon_running`
 * 表示后台进程正在运行。三者互相独立。
 */
export type BacklogAutopilotState = {
  repo_id: string;
  enabled: boolean;
  auto_merge_enabled: boolean;
  daemon_running: boolean;
  max_parallel: number;
  config_source: string;
  persisted_enabled: boolean | null;
};

// ─────────────────────────────────────────────────────────────────────────────
// Backlog CI/CD 交付尾段
// Keep these aligned with `backend.core.shared.models.backlog`
// (`BacklogCiDelivery` / `CiCheckProblem` / `CiRepairPolicy` /
// `CiDeliveryStatus`) — the Console API and `kc backlog ci status --json`
// share the same DTO.
// ─────────────────────────────────────────────────────────────────────────────

export type CiRepairPolicy = "inherit" | "on" | "off";

export type CiDeliveryStatus = "no_pr" | "pending" | "success" | "failure" | "unavailable";

export type CiCheckProblem = {
  name: string;
  summary: string;
  url: string | null;
  round_number: number | null;
};

export type BacklogCiDelivery = {
  prd_path: string;
  issue_number: number | null;
  status: CiDeliveryStatus;
  checks_state: string | null;
  checks_summary: string[];
  pr_url: string | null;
  head_sha: string | null;
  round_count: number;
  max_rounds: number;
  problems: CiCheckProblem[];
  stored_policy: CiRepairPolicy;
  global_enabled: boolean;
  effective_enabled: boolean;
  exhausted: boolean;
  exhausted_reason: string | null;
  last_synced_at: string | null;
};

export type BacklogCiRepairGlobalState = {
  repo_id: string;
  global_enabled: boolean;
  max_rounds: number;
};

export type BacklogEvidenceRole =
  | "evidence_report"
  | "verifier_report"
  | "verification_plan"
  | "artifact";

export type BacklogEvidenceFile = {
  name: string;
  size_bytes: number;
  media_type: string;
  role: BacklogEvidenceRole;
  artifact_token: string;
};

/**
 * 某个 PRD 在仓库中仍保留的验收证据清单。
 *
 * 事实源是配置证据目录下该 PRD 的子目录（`tasks/evidence/<prd-stem>/` 或
 * legacy 扁平目录）；`exists=false` 表示目录缺失，页面据此渲染明确空态，
 * 而不是用验收清单勾选数冒充文件数。
 */
export type BacklogPrdEvidenceManifest = {
  prd_path: string;
  prd_stem: string;
  evidence_dir: string;
  exists: boolean;
  files: BacklogEvidenceFile[];
};

// ─────────────────────────────────────────────────────────────────────────────
// PRD 生命周期观测
// Keep these aligned with the backend dataclasses under
// `src/backend/core/shared/models/backlog.py`（PrdLifecycle* 一族）。
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 单次 PRD 生命周期的耗时拆分（互斥口径）。
 *
 * `end_to_end_seconds` 为 null 表示尚无任何事件；`active` / `waiting` /
 * `blocked` 三者互斥且相加等于端到端耗时。
 */
export type PrdLifecycleDurations = {
  end_to_end_seconds: number | null;
  active_seconds: number;
  waiting_seconds: number;
  blocked_seconds: number;
};

/** 生命周期时间线中的单条事件；`detail` 为结构化摘要（可能为空对象）。 */
export type PrdLifecycleEventView = {
  event_type: string;
  phase: string;
  /** 事件自身的语义状态（按 event_type 写入时冻结），状态徽章的事实源。 */
  status: string;
  actor: string;
  occurred_at: string;
  detail: Record<string, unknown>;
};

/** 单个 PRD 的生命周期详情（Backlog 详情“执行过程”标签的数据源）。 */
export type PrdLifecycleDetail = {
  repo_id: string;
  prd_path: string;
  run_id: string | null;
  issue_number: number | null;
  trigger: string | null;
  current_phase: string;
  in_progress: boolean;
  outcome: string | null;
  history_complete: boolean;
  started_at: string | null;
  finished_at: string | null;
  durations: PrdLifecycleDurations;
  events: PrdLifecycleEventView[];
  /** false 表示该 PRD 还没有任何 lifecycle run/event。 */
  has_data: boolean;
};

/** 仓库级 PRD 生命周期统计中的单行 PRD 明细。 */
export type PrdLifecycleStatsRow = {
  run_id: string;
  prd_path: string;
  issue_number: number | null;
  outcome: string | null;
  current_phase: string;
  in_progress: boolean;
  history_complete: boolean;
  started_at: string;
  finished_at: string | null;
  durations: PrdLifecycleDurations;
};

/**
 * 仓库级 PRD 端到端统计（Stats 页“PRD 执行分析”的数据源）。
 *
 * 分位数/均值在没有已完成 run 时为 null；`unlinked_run_count` 为无法可靠
 * 关联 PRD 的旧记录，`incomplete_run_count` 为账本不完整的 run，二者均不
 * 进入分位数。
 */
export type PrdLifecycleStats = {
  repo_id: string | null;
  window_days: number;
  completed_runs: number;
  average_end_to_end_seconds: number | null;
  median_end_to_end_seconds: number | null;
  p90_end_to_end_seconds: number | null;
  average_blocked_seconds: number | null;
  bottleneck_phase: string | null;
  bottleneck_phase_seconds: number | null;
  unlinked_run_count: number;
  incomplete_run_count: number;
  runs: PrdLifecycleStatsRow[];
  token_usage: TokenUsageStats;
  /**
   * PRD（Issue）维度 token 汇总（按 `total_tokens` 降序），与 CLI
   * `kc tokens` 的「按 PRD」表同源同口径。旧响应缺省该字段时按空数组处理。
   */
  token_usage_by_prd?: PrdTokenUsageEntry[];
};

/**
 * 单个 PRD（Issue）维度的 token 用量累计。
 *
 * 同一 PRD 的多次 run 合并为一行；`run_count` 是参与累计的 run 条数。
 * 与后端 `PrdTokenUsageEntry` 对齐。
 */
export type PrdTokenUsageEntry = {
  repo_id: string | null;
  prd_path: string | null;
  issue_number: number | null;
  run_count: number;
  totals: TokenUsageTotals;
};

/**
 * 一个分组（流程或 agent）的 token 用量累计。
 *
 * `total_tokens` 为四项之和（实际处理量口径，含缓存命中与写入）；
 * `usage_count` 是计入汇总的用量条数。与后端 `TokenUsageTotals` 对齐。
 */
export type TokenUsageTotals = {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  total_tokens: number;
  usage_count: number;
};

/**
 * 仓库窗口内的 token 用量汇总（Stats 页 Token 汇总区的数据源）。
 *
 * `by_flow` 按 agent 调用流程分组（implement / verify / supervise / fix /
 * closeout）；`by_agent` 按 agent 名分组。与后端 `TokenUsageStats` 对齐。
 */
export type TokenUsageStats = {
  by_flow: Record<string, TokenUsageTotals>;
  by_agent: Record<string, TokenUsageTotals>;
};

// ─────────────────────────────────────────────────────────────────────────────
// Idea Inbox
// Keep these aligned with the backend dataclasses under
// `src/backend/core/shared/models/idea_inbox.py`.
// ─────────────────────────────────────────────────────────────────────────────

export type IdeaInboxSource = "frontend" | "inbound" | "feishu" | "manual";

export type PrdDraftStatus = "pending-review" | "approved" | "rejected";

export type IdeaEntry = {
  entry_id: string;
  occurred_at: string;
  source: IdeaInboxSource;
  author: string;
  text: string;
};

export type PrdDraftMetadata = {
  draft_id: string;
  status: PrdDraftStatus;
  repo_id: string;
  source_idea_refs: string[];
  priority: string;
  prd_type: string;
  created_at: string;
  approved_pending_path: string | null;
};

export type PrdDraftSummary = {
  metadata: PrdDraftMetadata;
  draft_path: string;
  title: string;
  body_excerpt: string;
};

export type IdeaInboxSnapshot = {
  repo_id: string;
  ideas_path: string;
  summary_path: string;
  drafts_dir: string;
  ideas_raw: string;
  summary_raw: string;
  entries: IdeaEntry[];
  drafts: PrdDraftSummary[];
};

export type AppendIdeaResponse = {
  entry: IdeaEntry;
  ideas_path: string;
};

export type RefreshSummaryResponse = {
  summary_path: string;
  summary_text: string;
  source: string;
};

export type IdeaInboxMetadata = {
  priorities: string[];
  prd_types: string[];
  inbound_signature_header: string;
  inbound_secret_env: string;
};

export type CreateDraftResponse = {
  draft: PrdDraftSummary;
  draft_path: string;
};

export type ApproveDraftResponse = {
  draft: PrdDraftSummary;
  pending_path: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// 生命周期 Agent 矩阵 / agent 回退顺序 / agent 标签 / PRD 覆盖
// 与 `src/backend/core/use_cases/lifecycle_agents_console.py` 的视图字段对齐。
// ─────────────────────────────────────────────────────────────────────────────

/** 九个生命周期键；顺序与后端 `LIFECYCLE_AGENT_KEYS` 一致（即 UI 展示顺序）。 */
export type LifecycleAgentKey =
  | "implementation"
  | "fix"
  | "closeout"
  | "verifier"
  | "review"
  | "supervisor"
  | "planner"
  | "content_generation"
  | "deliberate";

/** 生效值来源层。 */
export type LifecycleAgentSource =
  | "prd_override"
  | "repository"
  | "global"
  | "legacy"
  | "builtin";

/** 矩阵编辑视角：全局层（config.toml）或仓库层（.kedacode.toml）。 */
export type LifecycleAgentScope = "global" | "repository";

/** 单个生命周期键在某视角下的生效视图。 */
export type LifecycleAgentEntry = {
  key: LifecycleAgentKey;
  /**
   * 行所属的触发入口分组 id（pipeline / discussion_content / standalone）。
   * 与 `entry_groups[].entry` 配对使用，**不是行自身的 id**；分组中文名与组说明
   * 只在 `LifecycleAgentEntryGroup` 上携带一份。
   */
  entry: string;
  /** 该阶段「何时被读」的一句话触发时机（后端下发）。 */
  trigger: string;
  /** 是否允许取值为 `auto`。 */
  auto_allowed: boolean;
  /** 是否允许取值为 `executor`（仅 fix / closeout）。 */
  executor_allowed: boolean;
  /** `auto` 在该阶段的真实语义说明。 */
  auto_description: string | null;
  /** 本层是否显式声明了该键。 */
  declared_in_scope: boolean;
  /** 本层的声明值（未声明时为 null）。 */
  declared_value: string | null;
  /** 另一层的声明值（仓库视角下即全局层）。 */
  inherited_value: string | null;
  /** 当前生效的具体 agent；跟随实现者时为 null。 */
  effective_agent: string | null;
  /** 是否跟随实现阶段选中的 agent（fix / closeout）。 */
  follows_executor: boolean;
  /** 生效值来源层。 */
  source: LifecycleAgentSource;
};

/** 触发入口分组（后端按展示顺序下发，前端只渲染、不持有第二份映射）。 */
export type LifecycleAgentEntryGroup = {
  /** 组 id（pipeline / discussion_content / standalone）。 */
  entry: string;
  /** 组中文名（组标题）。 */
  label: string;
  /** 一行组说明（组标题旁的触发入口描述）。 */
  summary: string;
};

/** 生命周期矩阵某视角的完整视图。 */
export type LifecycleAgentsView = {
  scope: LifecycleAgentScope;
  repo_id: string | null;
  agents: string[];
  lifecycles: LifecycleAgentEntry[];
  /** 触发入口分组，按展示顺序排列。 */
  entry_groups: LifecycleAgentEntryGroup[];
  /** 来源层 key -> 中文标签。 */
  source_layers: Record<string, string>;
  /** 本层「恢复/删除本键」操作的提示文案。 */
  restore_hint: string;
};

/** agent 回退顺序视图。 */
export type AgentFallbackOrderView = {
  agent_fallback_order: string[];
  max_agent_switches: number;
  agents: string[];
};

/** 单个 agent 的路由标签配置。 */
export type AgentLabelEntry = {
  agent: string;
  label: string;
  label_color: string;
  label_description: string;
};

/** agent 标签列表视图。 */
export type AgentLabelsView = {
  labels: AgentLabelEntry[];
};

/** PRD 级覆盖视图（含可供选择的 agent 与继承视角的矩阵）。 */
export type PrdAgentOverrideView = {
  repo_id: string;
  prd_path: string;
  overrides: Record<string, string>;
  agents: string[];
  lifecycles: LifecycleAgentEntry[];
  /** 触发入口分组，按展示顺序排列（PRD 覆盖抽屉沿用同一分组渲染）。 */
  entry_groups: LifecycleAgentEntryGroup[];
};

/** PRD 级覆盖写回后的响应。 */
export type PrdAgentOverrideUpdateResponse = {
  repo_id: string;
  prd_path: string;
  overrides: Record<string, string>;
};

// ─────────────────────────────────────────────────────────────────────────────
// Console CLI 对齐操作（Issue 全量视图 / 标签编辑 / 一句话建 Issue / 启动选项）
// 与后端 `console_issues` / `issue_label_actions` / `console_issue_creation`
// 用例的返回结构对齐。
// ─────────────────────────────────────────────────────────────────────────────

/** 全量 Issue 列表的一行（`ConsoleIssueEntry`）；`monitored` 表示监控列表是否收录。 */
export type ConsoleIssueEntry = {
  number: number;
  title: string;
  url: string;
  /** GitHub 状态原值（`"OPEN"` / `"CLOSED"`）。 */
  state: string;
  labels: string[];
  monitored: boolean;
};

/** Issue 标签读取结果：现有标签 + 允许通过网页增删的标准标签集合。 */
export type IssueLabelSnapshot = {
  issue_number: number;
  labels: string[];
  allowed_labels: string[];
};

/** 一句话建 Issue 的结果（建完停在未入队态，不自动打就绪标签）。 */
export type ConsoleCreatedIssue = {
  number: number;
  issue_url: string;
};

/** 启动高级选项的候选清单（agent 名单与已定义模型预设名）。 */
export type LaunchOptionsView = {
  repo_id: string;
  agents: string[];
  presets: string[];
};

/**
 * 「开始此 PRD」的高级选项载荷；全部可选，缺省即不加任何旗标。
 *
 * 每个字段与 `kc run` 同名旗标一一对应，由后端折算为 argv 片段。
 * 直出 PR（`direct_pr`）不在其中：backlog start 的目标按构造就是带 PRD 锚点的
 * Issue，CLI 对该组合硬性拒绝，后端在发起端返回 400，因此前端不暴露该开关。
 */
export type StartPrdLaunchOptions = {
  fast_merge?: boolean;
  agent?: string | null;
  preset?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
};

// ── runner 状态 / 健康（GET /agent-runner/status 与 /health，只读）

/** runner 配置摘要与仓库清单（后端 status 接口的既定结构）。 */
export type RunnerStatusSummary = {
  daemon_mode: boolean;
  config: {
    max_issues: number;
    default_agent: string;
    max_recovery_attempts: number;
    recovery_retry_delay_seconds: number;
    ready_label: string;
    running_label: string;
    supervising_label: string;
    review_label: string;
    failed_label: string;
    base_branch: string;
    remote: string;
    auto_merge: boolean;
    forbidden_path_patterns: string[];
    autopilot_enabled: boolean;
    autopilot_merge_method: string;
    autopilot_require_verifier_pass: boolean;
    autopilot_auto_sign_off: boolean;
    autopilot_merge_check_timeout_seconds: number;
    pre_pr_review_enabled: boolean;
    post_pr_supervisor_enabled: boolean;
  };
  repositories: {
    repo_id: string;
    display_name: string;
    enabled: boolean;
    base_branch: string;
    remote: string;
  }[];
};

/** gh CLI 健康探测结果。 */
export type RunnerHealthStatus = {
  status: "healthy" | "degraded";
  gh_cli_available: boolean;
};
