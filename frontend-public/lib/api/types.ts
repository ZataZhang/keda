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

export type ActiveVerifierState =
  | "running"
  | "takeover_requested"
  | "taken_over"
  | "takeover_failed"
  | "verifier_completed"
  | "verifier_stale"
  | "pr_merged"
  | "expired";

/** 当前 Issue 受控独立 verifier 的活动投影（后端 fresh read，非缓存推断）。 */
export type ActiveVerifier = {
  run_id: string;
  head_sha: string;
  state: ActiveVerifierState;
  can_take_over: boolean;
  pr_number: number | null;
  autopilot_hold: boolean;
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
  active_verifier?: ActiveVerifier | null;
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
  takeover_http_status?: number | null;
  takeover_outcome?: string | null;
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

export type FailureTypeCount = {
  failure_type: string;
  count: number;
};

export type AttemptPerformanceGroup = {
  repo_id: string;
  agent: string;
  preset: string | null;
  model: string | null;
  attempt_count: number;
  success_count: number;
  non_success_count: number;
  success_rate: number;
  non_success_rate: number;
  p50_duration_seconds: number | null;
  p90_duration_seconds: number | null;
  failure_types: FailureTypeCount[];
};

export type RunOutcomePerformanceGroup = {
  repo_id: string;
  outcome: "completed" | "failed" | "blocked";
  run_count: number;
  p50_duration_seconds: number | null;
  p90_duration_seconds: number | null;
};

export type UnboundPresetAttemptCount = {
  repo_id: string;
  attempt_count: number;
};

export type AgentPerformanceStats = {
  repo_id: string | null;
  window_days: number;
  agents: AttemptPerformanceGroup[];
  presets: AttemptPerformanceGroup[];
  unbound_preset_attempts: UnboundPresetAttemptCount[];
  runs: RunOutcomePerformanceGroup[];
};

export type RunRecordEntry = {
  repo_id: string;
  repo_path: string;
  issue_number: number;
  issue_title: string | null;
  issue_url: string | null;
  trigger: string;
  agent: string;
  outcome: "completed" | "failed" | "blocked";
  error_summary: string | null;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
};

export type RunAttemptDetailEntry = {
  attempt_number: number;
  failure_type: string;
  recovered: boolean;
  detail: string;
  agent: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  failure_phase: string | null;
  preset: string;
  model: string;
  phase_durations: Array<{
    name: string;
    seconds: number;
  }>;
};

export type RunInvocationDetailEntry = {
  invocation_id: string;
  phase: string;
  role: string;
  executor: string;
  retry_of: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  outcome: string;
  requested_model: string | null;
  reported_model: string | null;
  model_source: string | null;
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

/** 生效并发上限的来源：继承 runner 容量 / Backlog 设置 / 被 runner 容量压低。 */
export type BacklogCeilingSource = "inherited" | "policy" | "capped_by_capacity";

export type BacklogSettings = {
  repo_id: string;
  /** Backlog「并发」策略值；从未保存过时为 null（继承 runner 容量）。 */
  max_parallel: number | null;
  /** 解析后的单一生效并发上限，与 daemon 认领、补位、全局开始同值。 */
  effective_max_parallel: number;
  runner_capacity: number;
  ceiling_source: BacklogCeilingSource;
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
 * `max_parallel` 是可空的并发策略值（null = 从未保存），`effective_max_parallel`
 * 是与 daemon / 补位 / 全局开始共用同一解析函数的生效上限。
 */
export type BacklogAutopilotState = {
  repo_id: string;
  enabled: boolean;
  auto_merge_enabled: boolean;
  daemon_running: boolean;
  max_parallel: number | null;
  effective_max_parallel: number;
  runner_capacity: number;
  ceiling_source: BacklogCeilingSource;
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
  /** 文件来源：``worktree``（执行工作区、尚未合并）或 ``main``（主仓库）。 */
  source: "worktree" | "main";
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
  /**
   * 读取状态：``complete`` 表示候选目录成功枚举；``unavailable`` 表示工作区发现
   * 失败或多重匹配、无法确定证据来源——此时不得把结果当作「确认无文件」的空态呈现。
   */
  status: "complete" | "unavailable";
};

// ─────────────────────────────────────────────────────────────────────────────
// PRD 交付进度聚合（只读）
// Keep these aligned with the backend dataclasses in
// `src/backend/core/use_cases/backlog_prd_progress.py`。所有口径为弱信号：
// 文件归属 ≠ 验证通过，影响树触碰 ≠ 改对；不可比较显示 null/`-`，读取失败单独
// 用状态字段披露，不得用空数组或零冒充成功读取。
// ─────────────────────────────────────────────────────────────────────────────

/** 验证计划（§7.6）里的一个顶层条目。 */
export type PrdProgressPlannedItem = {
  id: string;
  summary: string;
  reviewer: string | null;
};

/** 验收清单勾选读数；``unavailable`` 时 checked/total 为 null。 */
export type PrdProgressChecklist = {
  status: "available" | "unavailable";
  checked: number | null;
  total: number | null;
};

/** 报告三件套的「是否已产出」；读取不完整时未知项为 null。 */
export type PrdProgressReports = {
  verification_plan: boolean | null;
  evidence_report: boolean | null;
  verifier_report: boolean | null;
};

/** 证据计划 vs 产出的聚合读数。 */
export type PrdProgressEvidence = {
  /** null 表示来源不可用（不伪造空计划）；成功读取但无计划时为 ``[]``。 */
  planned: PrdProgressPlannedItem[] | null;
  produced_ids: string[];
  produced_planned_count: number | null;
  file_count: number | null;
  /** 产出归属口径：清单映射 / 文件名前缀 / 两者混合 / 无可归属项。 */
  attribution_source: "manifest" | "filename" | "mixed" | "none";
  status: "complete" | "partial" | "unavailable";
  reports: PrdProgressReports;
};

/** 影响树触达读数（弱信号）；无法计算时整个对象为 null。 */
export type PrdProgressImpact = {
  touched: number;
  judgeable: number;
  unresolvable: number;
};

/** 工作区命中信息（不返回本地路径）。 */
export type PrdProgressWorktree = {
  branch: string | null;
};

/** 详情头部与证据标签共用的聚合读数（一次拉取，不轮询）。 */
export type BacklogPrdProgress = {
  prd_path: string;
  prd_stem: string;
  /** 事实来源：``worktree`` / ``main`` / null（来源不可用）。 */
  prd_source: string | null;
  /** 归档终态用 ``not_applicable``；其余见后端 WorktreeMatchStatus。 */
  worktree_status:
    | "matched"
    | "none"
    | "unavailable"
    | "ambiguous"
    | "not_applicable";
  worktree: PrdProgressWorktree | null;
  checklist: PrdProgressChecklist;
  evidence: PrdProgressEvidence;
  /** impact=null 时由此字段说明原因（`terminal_archive` / `no_worktree` / 等）。 */
  impact_status:
    | "available"
    | "terminal_archive"
    | "no_worktree"
    | "no_tree"
    | "no_nodes"
    | "no_base"
    | "unavailable";
  impact: PrdProgressImpact | null;
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

/**
 * 一次真实进程调用在「执行过程」视图里的展示行。
 *
 * 字段与后端 `PrdInvocationRow` 逐字对应，全部来自调用账本已落库的事实：
 * `outcome` 为闭集语义（ok / failed / timeout / error / unclosed / incomplete），
 * 未闭合调用的 `finished_at` 与 `duration_seconds` 恒为 null（前端不得估算）；
 * 模型三态由 requested/reported/model_source 共同表达。
 */
export type PrdInvocationRow = {
  invocation_id: string;
  run_id: string;
  attempt_number: number | null;
  phase: string;
  role: string;
  executor: string;
  outcome: string;
  failure_category: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  requested_model: string | null;
  reported_model: string | null;
  model_source: string | null;
  token_usage: Record<string, unknown> | null;
  retry_of: string | null;
  retry_reason: string | null;
  exit_code: number | null;
  log_locator: string | null;
};

/**
 * 调用明细读取区（缺口成因必须可区分，不得由前端推测）。
 *
 * 三种成因页面各说各话：`available=false` 只表示**账本读不到**（存储不具备 Issue 级
 * 调用读端，或读取异常）；「该 PRD 没有关联 Issue」是**无从归属**，后端根本不挂这个区
 * （`PrdLifecycleDetail.invocations` 为 null/缺位）；`available=true` 且 `rows` 为空
 * 表示账本可读但确实没有记录。`truncated=true` 表示返回条数已达读取上限，展示的不是
 * 完整历史——此时某个尝试没有行不代表它没调用过。
 */
export type PrdInvocationDetail = {
  available: boolean;
  truncated: boolean;
  rows: PrdInvocationRow[];
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
  /** 旧响应没有该字段，此时按「无调用明细区」处理。 */
  invocations?: PrdInvocationDetail | null;
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
  /** 调用账本按模型、Agent 与 PRD 聚合的 token 用量；旧后端响应缺省时视为空。 */
  token_usage_by_model?: ModelTokenUsageStats;
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

/** 单个模型的 USD / 百万 token 价格配置。 */
export type ModelTokenPricing = {
  model_id: string;
  input_usd_per_million: number;
  output_usd_per_million: number;
  cache_read_usd_per_million: number;
  cache_creation_usd_per_million: number;
};

/** 本机管理终端保存的模型价格清单。 */
export type ModelPricingSettings = {
  models: ModelTokenPricing[];
  updated_at: string;
};

/** 调用账本按模型、Agent 与 PRD 聚合的 token 用量。 */
export type ModelTokenUsageStats = {
  by_model: Record<string, TokenUsageTotals>;
  by_agent: Record<string, Record<string, TokenUsageTotals>>;
  by_prd: PrdModelTokenUsageEntry[];
  truncated: boolean;
};

/** 单个 PRD 在单个模型上的累计用量。 */
export type PrdModelTokenUsageEntry = {
  repo_id: string;
  prd_path: string | null;
  issue_number: number | null;
  model_id: string;
  totals: TokenUsageTotals;
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

/**
 * 单个已注册 agent 在本机的可用性事实。
 *
 * 后端只按 PATH 解析注册块声明的可执行文件（不起子进程、不探测 `--help`），
 * 键名与 `kc agent list --json` 的 `available` / `bin_path` 保持一致。
 */
export type AgentAvailabilityEntry = {
  agent: string;
  /** 注册块声明的可执行文件名。 */
  bin: string;
  /** 可执行文件是否解析得到。 */
  available: boolean;
  /** 解析到的绝对路径；未安装时为 null。 */
  bin_path: string | null;
  /** 该 agent 声明的非交互用途名。 */
  declared_profiles: string[];
};

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
  /** 注册表可用性事实（与矩阵视图同源），抽屉据此标注"未安装"。 */
  agent_availability: AgentAvailabilityEntry[];
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
// 聚合生命周期设置（矩阵 + 模型预设 + 执行器回退候选）
// 与后端 `build_lifecycle_settings_view` / `build_fallback_candidates_view` 对齐。
// ─────────────────────────────────────────────────────────────────────────────

/** 逐字段来源标签（后端 `FIELD_SOURCE_*`）；agent 字段还可能取配置层名。 */
export type LifecycleFieldSource =
  | "preset"
  | "inherited"
  | "agent_default"
  | "not_supported";

/** 单个生命周期阶段逐字段的来源。`agent` 除四种来源标签外还可能是层名（global/repository/legacy/builtin）。 */
export type LifecycleRowFieldSources = {
  agent: string;
  preset: string | null;
  model: string;
  reasoning_effort: string;
};

/** 聚合视图里一个生命周期阶段的完整行（含最终生效值、逐字段来源与影响阶段）。 */
export type LifecycleSettingsRow = {
  key: LifecycleAgentKey;
  label: string;
  /** 该阶段当前绑定的预设名（未绑定为 null）。 */
  preset_name: string | null;
  /** 最终生效 agent（跟随实现者且无上下文时可能为 null）。 */
  effective_agent: string | null;
  model: string | null;
  reasoning_effort: string | null;
  field_sources: LifecycleRowFieldSources;
  /** fix / closeout 无自绑时整体继承实现阶段解析结果。 */
  is_inherited: boolean;
  follows_implementation: boolean;
  /** 生效 agent 是否声明了 model 参数模板。 */
  model_supported: boolean;
  reasoning_effort_supported: boolean;
  /** 绑定预设共享的其它阶段（改这个预设会牵动的阶段）。 */
  affected_stages: LifecycleAgentKey[];
};

/** 预设定义的来源层（scope=repository 时对比全局得出）。 */
export type PresetDefinitionSource =
  | "global"
  | "repository_only"
  | "repository_overrides";

/** 一个命名模型预设的定义（原子三元组 agent/model/reasoning_effort）。 */
export type AgentPresetDefinition = {
  name: string;
  agent: string;
  model: string | null;
  reasoning_effort: string | null;
  model_supported: boolean;
  reasoning_effort_supported: boolean;
  /** 当前绑定了该预设的生命周期阶段。 */
  bound_stages: LifecycleAgentKey[];
  source: PresetDefinitionSource;
};

/** 单个回退候选（agent + 可选的同 agent 预设）在视图里的展开结果。 */
export type FallbackCandidateRow = {
  position: number;
  agent: string;
  preset: string | null;
  model: string | null;
  reasoning_effort: string | null;
  model_supported: boolean;
  reasoning_effort_supported: boolean;
  field_sources: {
    preset: string | null;
    model: string;
    reasoning_effort: string;
  };
};

/** 执行器回退候选视图（有序候选 + 候选步数预算 + 可用 agent）。 */
export type AgentFallbackCandidatesView = {
  candidates: FallbackCandidateRow[];
  max_agent_switches: number;
  /** 预算按候选步数消耗（后端恒为 true）。 */
  budget_by_candidate_step: boolean;
  agents: string[];
};

/** 聚合生命周期设置的完整读写视图。 */
export type LifecycleSettingsView = {
  scope: LifecycleAgentScope;
  repo_id: string | null;
  agents: string[];
  /**
   * 注册表可用性事实（只查 PATH）。页面把"未安装 / 解析路径"标在 Agent 下拉项、
   * 矩阵行、预设与回退候选上，只呈现不拦截：真正的阻断在 core 的启动前门禁。
   */
  agent_availability: AgentAvailabilityEntry[];
  lifecycles: LifecycleSettingsRow[];
  presets: AgentPresetDefinition[];
  fallback: AgentFallbackCandidatesView;
  /** 来源标签 -> 中文说明（后端下发，前端只渲染）。 */
  field_source_vocabulary: Record<string, string>;
};

/** 预设 upsert 载荷（`model` / `reasoning_effort` 给 null 表示不设置该字段）。 */
export type AgentPresetPayload = {
  agent: string;
  model: string | null;
  reasoning_effort: string | null;
};

/** 回退候选写回项（完整期望数组的一项）。 */
export type FallbackCandidateWriteEntry = {
  agent: string;
  preset: string | null;
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
 * 普通发布字段与 `kc run` 同名旗标一一对应，由后端折算为 argv 片段；
 * `auto_merge_prd` 则将授权持久化到目标 Issue 标签。
 * 直出 PR（`direct_pr`）不在其中：backlog start 的目标按构造就是带 PRD 锚点的
 * Issue，CLI 对该组合硬性拒绝，后端在发起端返回 400，因此前端不暴露该开关。
 */
export type StartPrdLaunchOptions = {
  fast_merge?: boolean;
  auto_merge_prd?: boolean;
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

// ── 模型评测（Model Evaluation，/agent-runner/console/evaluation/*）

/** 评测 run 状态机取值（与后端 EvaluationRunStatus 逐字对齐）。 */
export type EvaluationRunStatus =
  | "created"
  | "queued"
  | "running"
  | "awaiting_judge_consent"
  | "judging"
  | "completed"
  | "cancelled"
  | "interrupted";

/** 候选执行结果状态（失败态绝不折算为 0 分）。 */
export type EvaluationResultStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "timeout"
  | "cancelled";

/** 确定性检查状态。 */
export type EvaluationCheckStatus = "passed" | "failed" | "timeout" | "not_run";

/** 逐评审评分状态。 */
export type EvaluationScoreStatus =
  | "valid"
  | "refusal"
  | "invalid"
  | "timeout"
  | "provider_error"
  | "unauthorized"
  | "cancelled";

/** 评测集单条样本（case）。 */
export type EvaluationSuiteCase = {
  case_id: string;
  prompt: string;
  check_recipe: string;
};

/** suite 清单项：registry 目录内一个 YAML 文件的校验视图。 */
export type EvaluationSuiteEntry = {
  file: string;
  valid: boolean;
  suite_id?: string;
  version?: string;
  title?: string;
  repo_path?: string;
  base_ref?: string;
  content_hash?: string;
  rubric_version?: string;
  check_timeout_seconds?: number;
  cases?: EvaluationSuiteCase[];
  errors: string[];
};

/** run 内冻结的执行/评审预设快照。 */
export type EvaluationPresetSnapshot = {
  preset_name: string;
  agent?: string;
  model?: string | null;
  reasoning_effort?: string | null;
  cost_status?: string;
  cost_estimate?: number | null;
};

/** 一个评测 run（快照 + 状态机 + manifest hash）。 */
export type EvaluationRun = {
  run_id: string;
  suite_id: string;
  suite_version: string;
  suite_hash: string;
  rubric_version: string;
  repo_path: string;
  base_ref: string;
  base_commit: string;
  base_tree_hash: string;
  execution_preset_snapshots: EvaluationPresetSnapshot[];
  judge_preset_snapshots: EvaluationPresetSnapshot[];
  status: EvaluationRunStatus;
  manifest_hash: string | null;
  case_ids: string[];
  check_timeout_seconds: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

/** 矩阵单元（case × 执行预设）的候选结果。 */
export type EvaluationResult = {
  result_id: string;
  run_id: string;
  case_id: string;
  execution_preset_id: string;
  execution_model_snapshot: Record<string, unknown>;
  base_tree_hash: string;
  worktree_path: string | null;
  result_status: EvaluationResultStatus;
  check_status: EvaluationCheckStatus;
  check_recipe: string;
  check_exit_code: number | null;
  check_output_summary: string | null;
  agent_summary: string | null;
  duration_seconds: number | null;
  usage_summary: Record<string, unknown> | null;
  cost_status: string;
  diff_hash: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

/** 单评审 × 单候选的评分行。 */
export type EvaluationScore = {
  score_id: string;
  run_id: string;
  result_id: string;
  case_id: string;
  execution_preset_id: string;
  judge_preset_id: string;
  judge_model_snapshot: Record<string, unknown>;
  rubric_version: string;
  score_status: EvaluationScoreStatus;
  dimension_scores: Record<string, number>;
  rationale_summary: string | null;
  response_hash: string | null;
  duration_seconds: number | null;
  usage_summary: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
};

/** 维度聚合：只统计 valid 评分的 median / min / max / N。 */
export type EvaluationDimensionAggregate = {
  dimension: string;
  median: number | null;
  minimum: number | null;
  maximum: number | null;
  valid_count: number;
};

/** run 级派生汇总（不保存第二份统计事实）。 */
export type EvaluationSummary = {
  aggregates: EvaluationDimensionAggregate[];
  by_execution_preset: Record<string, EvaluationDimensionAggregate[]>;
  status_counts: Record<string, number>;
  valid_judge_count: number;
};

/** run 详情接口的完整载荷。 */
export type EvaluationRunDetail = {
  run: EvaluationRun;
  results: EvaluationResult[];
  scores: EvaluationScore[];
  summary: EvaluationSummary;
  payload_state: {
    is_active: boolean;
    awaiting_consent: boolean;
    judge_selected: boolean;
  };
};

/** 执行/评审预设目录（含评审可用性探针）。 */
export type EvaluationPresetOptions = {
  execution_presets: {
    name: string;
    agent: string;
    model: string | null;
    reasoning_effort: string | null;
    cost_status: string;
    cost_estimate: number | null;
  }[];
  /** 评审预设即执行预设；available=false 表示该 agent 无法保证"只读 payload"。 */
  judge_presets: {
    name: string;
    agent: string;
    model: string | null;
    reasoning_effort: string | null;
    available: boolean;
    reason: string | null;
    cost_status: string;
  }[];
  default_judge_presets: string[];
  max_concurrency: number;
  agent_timeout_seconds: number;
  judge_timeout_seconds: number;
};

/** 待授权评审 payload 预览（所见即所发）。 */
export type EvaluationJudgePreview = {
  run_id: string;
  manifest_hash: string | null;
  frozen_manifest_hash: string | null;
  judge_targets: EvaluationPresetSnapshot[];
  entries: {
    result_id: string;
    case_id: string;
    execution_preset_id: string;
    payload_file: string;
    payload_sha256: string;
    size_bytes: number;
    payload_available: boolean;
    payload_preview: string;
    payload_truncated: boolean;
  }[];
  estimated_requests: number;
  sensitive_notice: string;
};

// ── 统一运行时看板与桌面 companion（GET /console/runtime/tasks 等）

/** 任务生命周期分桶，与后端 `RuntimeTaskStatus` 取值一一对应。 */
export type RuntimeTaskStatus =
  | "running"
  | "waiting"
  | "blocked"
  | "completed"
  | "unknown";

/**
 * 看板上的一个 Issue 运行任务。
 *
 * 只带身份与运行观测字段：改动树触达读数不在这里，展示侧按 `prd_path`
 * 走现有 progress 契约取数，避免出现第二份检测实现。
 */
export type RuntimeTaskEntry = {
  repo_id: string;
  issue_number: number;
  task_key: string;
  repo_display_name: string | null;
  issue_title: string | null;
  issue_url: string | null;
  status: RuntimeTaskStatus;
  status_reason: string | null;
  phase: string | null;
  agent: string | null;
  preset: string | null;
  model: string | null;
  attempt_number: number | null;
  prd_path: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  last_outcome: string | null;
  labels: string[];
};

/** 看板任务摘要：列表 + 分桶计数 + 数据来源可用性披露。 */
export type RuntimeTaskSummary = {
  generated_at: string;
  tasks: RuntimeTaskEntry[];
  status_counts: Record<RuntimeTaskStatus, number>;
  unavailable_sources: string[];
  missing_repo_ids: string[];
  sync_status: string;
  scanned_at: string | null;
  truncated: boolean;
};

/** 桌面 companion 状态分桶，与后端 `DesktopCompanionStatus` 对齐。 */
export type DesktopCompanionStatus =
  | "launched"
  | "focused"
  | "running"
  | "idle"
  | "not_installed"
  | "unsupported_platform"
  | "failed";

/** companion 快照载荷；`companion_url` 仅在窗口确实存在时非空。 */
export type DesktopCompanionState = {
  status: DesktopCompanionStatus;
  console_origin: string | null;
  console_session: string;
  focus_ticket: number;
  close_ticket: number;
  detail: string | null;
  hint: string;
  window_exists: boolean;
  companion_url: string | null;
};
