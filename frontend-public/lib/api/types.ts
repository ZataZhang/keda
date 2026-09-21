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
// Roadmap
// Keep these aligned with the backend dataclasses under
// `src/backend/core/shared/models/roadmap.py`.
// ─────────────────────────────────────────────────────────────────────────────

export type RoadmapPrdState =
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

export type RoadmapDependencyKind = "prd" | "issue" | "group" | "unresolved";

export type RoadmapDependency = {
  from_path: string;
  to_path: string;
  kind: RoadmapDependencyKind;
  detail: string;
};

export type RoadmapNextAction = {
  label: string;
  url: string | null;
};

export type RoadmapPrd = {
  prd_path: string;
  title: string;
  status: "pending" | "archived";
  priority: string;
  issue_url: string | null;
  issue_number: number | null;
  state: RoadmapPrdState;
  acceptance_total: number;
  acceptance_checked: number;
  delivery_dependencies: RoadmapDependency[];
  updated_at: string;
  block_reason: string | null;
  next_action: RoadmapNextAction | null;
};

export type RoadmapSettings = {
  repo_id: string;
  max_parallel: number;
  default_view: "timeline" | "list";
  updated_at: string;
};

export type RoadmapActionResult = {
  prd_path: string;
  issue_number: number | null;
  state: RoadmapPrdState;
  detail: string;
};

export type RoadmapGlobalStartResult = {
  started: RoadmapActionResult[];
  queued: string[];
  skipped: string[];
};

/**
 * 仓库级 Autopilot 完整闭环状态。
 *
 * `enabled` 是**生效**配置的值（写后 fresh load），`persisted_enabled` 是仓库
 * `.iar.toml` 里的持久值（文件缺失或键未设置时为 null）。两者与
 * `auto_merge_enabled` / `daemon_running` 必须分别展示——只有全部成立才是
 * 真正的全自动闭环。
 */
export type RoadmapAutopilotState = {
  repo_id: string;
  enabled: boolean;
  auto_merge_enabled: boolean;
  daemon_running: boolean;
  max_parallel: number;
  config_source: string;
  persisted_enabled: boolean | null;
};

export type RoadmapEvidenceRole =
  | "evidence_report"
  | "verifier_report"
  | "verification_plan"
  | "artifact";

export type RoadmapEvidenceFile = {
  name: string;
  size_bytes: number;
  media_type: string;
  role: RoadmapEvidenceRole;
  artifact_token: string;
};

/**
 * 某个 PRD 在仓库中仍保留的验收证据清单。
 *
 * 事实源是配置证据目录下该 PRD 的子目录（`tasks/evidence/<prd-stem>/` 或
 * legacy 扁平目录）；`exists=false` 表示目录缺失，页面据此渲染明确空态，
 * 而不是用验收清单勾选数冒充文件数。
 */
export type RoadmapPrdEvidenceManifest = {
  prd_path: string;
  prd_stem: string;
  evidence_dir: string;
  exists: boolean;
  files: RoadmapEvidenceFile[];
};

// ─────────────────────────────────────────────────────────────────────────────
// PRD 生命周期观测
// Keep these aligned with the backend dataclasses under
// `src/backend/core/shared/models/roadmap.py`（PrdLifecycle* 一族）。
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
  actor: string;
  occurred_at: string;
  detail: Record<string, unknown>;
};

/** 单个 PRD 的生命周期详情（Roadmap 详情“执行过程”标签的数据源）。 */
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

/** 矩阵编辑视角：全局层（config.toml）或仓库层（.iar.toml）。 */
export type LifecycleAgentScope = "global" | "repository";

/** 单个生命周期键在某视角下的生效视图。 */
export type LifecycleAgentEntry = {
  key: LifecycleAgentKey;
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

/** 生命周期矩阵某视角的完整视图。 */
export type LifecycleAgentsView = {
  scope: LifecycleAgentScope;
  repo_id: string | null;
  agents: string[];
  lifecycles: LifecycleAgentEntry[];
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
};

/** PRD 级覆盖写回后的响应。 */
export type PrdAgentOverrideUpdateResponse = {
  repo_id: string;
  prd_path: string;
  overrides: Record<string, string>;
};
