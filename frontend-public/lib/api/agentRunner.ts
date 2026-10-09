// Read-only Agent Runner monitoring API wrapper.
//
// This module is the only place the dashboard talks to `/api/v1/agent-runner/*`.
// It must never reach into GitHub or Git directly — keep that boundary enforced
// so the panel stays a monitoring surface, not a recovery surface.

import { get } from "./client";
import type {
  IssueMonitoringSnapshot,
  MonitorSnapshotsResponse,
  MonitoringOverview,
  RunnerHealthStatus,
  RunnerStatusSummary,
} from "./types";

const BASE_PATH = "/v1/agent-runner";

/** Parameters accepted by {@link fetchMonitoringOverview}. */
export type FetchMonitoringOverviewParams = {
  /** Optional whitelist of repository IDs to scope the overview to. */
  repoIds?: string[];
  /** When true, run the build asynchronously and return a job id instead of the payload. */
  asyncRun?: boolean;
};

/** Async-mode acknowledgement returned by the overview endpoint. */
export type OverviewJobHandle = {
  async: true;
  job_id: string;
  repo_ids: string[] | null;
};

/** Status of an async overview build. */
export type OverviewJobStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

/** Snapshot of an overview job returned by the polling endpoint. */
export type OverviewJobSnapshot = {
  job_id: string;
  status: OverviewJobStatus;
  repo_ids: string[] | null;
  created_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  payload: MonitoringOverview | null;
};

/** Mapping of repository id → job id returned by the per-repo endpoint. */
export type OverviewJobsByRepo = {
  jobs_by_repo: Record<string, string>;
  started_at: number;
};

function buildOverviewQuery(params: FetchMonitoringOverviewParams): string {
  const searchParams = new URLSearchParams();
  if (params.repoIds && params.repoIds.length > 0) {
    searchParams.set("repo_ids", params.repoIds.join(","));
  }
  if (params.asyncRun) {
    searchParams.set("async_run", "true");
  }
  const queryString = searchParams.toString();
  return queryString ? `?${queryString}` : "";
}

/** Fetch the monitoring overview. */
export async function fetchMonitoringOverview(
  params: FetchMonitoringOverviewParams = {},
): Promise<MonitoringOverview | OverviewJobHandle> {
  return get<MonitoringOverview | OverviewJobHandle>(
    `${BASE_PATH}/overview${buildOverviewQuery(params)}`,
  );
}

/** Poll an async overview job until it reaches a terminal state. */
export async function pollOverviewJob(
  jobId: string,
  options: { intervalMs?: number; signal?: AbortSignal } = {},
): Promise<OverviewJobSnapshot> {
  const intervalMs = options.intervalMs ?? 5000;
  while (true) {
    if (options.signal?.aborted) {
      throw new DOMException("Overview polling aborted", "AbortError");
    }
    const snapshot = await get<OverviewJobSnapshot>(
      `${BASE_PATH}/overview/jobs/${encodeURIComponent(jobId)}`,
    );
    if (snapshot.status === "completed" || snapshot.status === "failed") {
      return snapshot;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/**
 * Fetch monitoring detail for one Issue.
 *
 * @param issueNumber - Target Issue number.
 * @param params - Optional scope. `repoId` restricts the lookup to one
 * repository; Issue numbers collide across registered repositories, so the
 * dashboard always passes the repo the operator clicked.
 */
export async function fetchIssueDetail(
  issueNumber: number,
  params: { repoId?: string | null } = {},
): Promise<IssueMonitoringSnapshot> {
  const query = params.repoId
    ? `?${new URLSearchParams({ repo_id: params.repoId })}`
    : "";
  return get<IssueMonitoringSnapshot>(
    `${BASE_PATH}/issues/${issueNumber}${query}`,
  );
}

/**
 * Start one overview job per enabled repository and return the repo→job_id
 * mapping. The dashboard polls each job independently to render cards as
 * their scans complete (gradual reveal).
 */
export async function fetchOverviewJobsByRepo(): Promise<OverviewJobsByRepo> {
  return get<OverviewJobsByRepo>(`${BASE_PATH}/overview/per-repo`);
}

/**
 * Read the locally persisted monitoring snapshots.
 *
 * This is the dashboard's first-paint source: it only reads the local SQLite
 * snapshots, so it stays fast even when `gh` is slow or unreachable. Repositories
 * that are no longer enabled are filtered out by the backend.
 */
export async function fetchOverviewSnapshots(): Promise<MonitorSnapshotsResponse> {
  return get<MonitorSnapshotsResponse>(`${BASE_PATH}/overview/snapshots`);
}

/**
 * 读取 runner 配置摘要（daemon 模式、全局 runner 设置与仓库清单）。
 *
 * dashboard 状态条的第一数据源；只读，不触发任何执行。
 */
export async function fetchRunnerStatus(): Promise<RunnerStatusSummary> {
  return get<RunnerStatusSummary>(`${BASE_PATH}/status`);
}

/**
 * 读取 runner 健康探测结果（gh CLI 可用性）。
 *
 * gh 探测在后端真实执行 `gh --version`，失败时返回 `degraded` 而非报错。
 */
export async function fetchRunnerHealth(): Promise<RunnerHealthStatus> {
  return get<RunnerHealthStatus>(`${BASE_PATH}/health`);
}
