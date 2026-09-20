// Agent Runner Operations Console API wrapper.
//
// This module is the only place the console pages talk to
// `/api/v1/agent-runner/console/*` and `/api/v1/agent-runner/repositories*`.
// All write operations map to backend whitelisted actions; the frontend
// never sends raw shell commands.

import { del, get, patch, post } from "./client";
import type {
  AuditEntry,
  BatchAddRepositoriesResult,
  ConsoleActionResult,
  DailyRunTrendEntry,
  DirectoryBrowseResult,
  DiscoveredRepositoryEntry,
  MonitorSettings,
  ProcessLogChunk,
  RegistryRepositoryEntry,
  RepositoryCompletionStats,
  RunnerProcessKind,
  RunnerProcessRecord,
  RunRecordEntry,
} from "./types";

const BASE_PATH = "/v1/agent-runner";

// ── 托管进程 ────────────────────────────────────────────────────────────────

export async function fetchProcesses(): Promise<RunnerProcessRecord[]> {
  const response = await get<{ processes: RunnerProcessRecord[] }>(
    `${BASE_PATH}/console/processes`,
  );
  return response.processes;
}

export async function startProcess(params: {
  repo_id: string;
  kind: RunnerProcessKind;
}): Promise<RunnerProcessRecord> {
  return post<RunnerProcessRecord>(`${BASE_PATH}/console/processes`, params);
}

export async function stopProcess(
  processId: string,
): Promise<RunnerProcessRecord> {
  return post<RunnerProcessRecord>(
    `${BASE_PATH}/console/processes/${processId}/stop`,
  );
}

export async function fetchProcessLog(
  processId: string,
  offset: number,
): Promise<ProcessLogChunk> {
  return get<ProcessLogChunk>(
    `${BASE_PATH}/console/processes/${processId}/logs?offset=${offset}`,
  );
}

// ── 白名单动作 ──────────────────────────────────────────────────────────────

export async function executeRepositoryAction(
  repoId: string,
  action: "run_once" | "review_once",
): Promise<ConsoleActionResult> {
  return post<ConsoleActionResult>(
    `${BASE_PATH}/console/repositories/${encodeURIComponent(repoId)}/actions`,
    { action },
  );
}

export async function executeIssueAction(
  repoId: string,
  issueNumber: number,
  action: "retry_failed" | "blocked_continue",
): Promise<ConsoleActionResult> {
  return post<ConsoleActionResult>(
    `${BASE_PATH}/console/repositories/${encodeURIComponent(repoId)}/issues/${issueNumber}/actions`,
    { action },
  );
}

// ── 统计 / 历史 / 审计 ──────────────────────────────────────────────────────

export async function fetchCompletionStats(): Promise<
  RepositoryCompletionStats[]
> {
  const response = await get<{ repositories: RepositoryCompletionStats[] }>(
    `${BASE_PATH}/console/stats/overview`,
  );
  return response.repositories;
}

export async function fetchRunHistoryTrend(params: {
  repoId?: string;
  days?: number;
}): Promise<DailyRunTrendEntry[]> {
  const searchParams = new URLSearchParams();
  if (params.repoId) {
    searchParams.set("repo_id", params.repoId);
  }
  searchParams.set("days", String(params.days ?? 30));
  const response = await get<{ trend: DailyRunTrendEntry[] }>(
    `${BASE_PATH}/console/stats/history?${searchParams.toString()}`,
  );
  return response.trend;
}

export async function fetchRecentRuns(params: {
  repoId?: string;
  limit?: number;
}): Promise<RunRecordEntry[]> {
  const searchParams = new URLSearchParams();
  if (params.repoId) {
    searchParams.set("repo_id", params.repoId);
  }
  searchParams.set("limit", String(params.limit ?? 50));
  const response = await get<{ runs: RunRecordEntry[] }>(
    `${BASE_PATH}/console/runs?${searchParams.toString()}`,
  );
  return response.runs;
}

export async function fetchAuditLog(limit = 100): Promise<AuditEntry[]> {
  const response = await get<{ audits: AuditEntry[] }>(
    `${BASE_PATH}/console/audit?limit=${limit}`,
  );
  return response.audits;
}

// ── 监控同步设置 ────────────────────────────────────────────────────────────

/** Read the global background sync settings (falls back to the static default). */
export async function fetchMonitorSettings(): Promise<MonitorSettings> {
  return get<MonitorSettings>(`${BASE_PATH}/console/monitor/settings`);
}

/**
 * Save the global background sync settings.
 *
 * @param params - Sync switch and interval in seconds (backend accepts 60–3600).
 */
export async function updateMonitorSettings(params: {
  sync_enabled: boolean;
  sync_interval_seconds: number;
}): Promise<MonitorSettings> {
  return patch<MonitorSettings>(`${BASE_PATH}/console/monitor/settings`, params);
}

// ── 仓库 registry 管理 ──────────────────────────────────────────────────────

export async function fetchRegistryRepositories(): Promise<
  RegistryRepositoryEntry[]
> {
  const response = await get<{ repositories: RegistryRepositoryEntry[] }>(
    `${BASE_PATH}/repositories`,
  );
  return response.repositories;
}

export async function addRegistryRepository(params: {
  repo_id: string;
  path: string;
  display_name?: string;
}): Promise<RegistryRepositoryEntry> {
  return post<RegistryRepositoryEntry>(`${BASE_PATH}/repositories`, params);
}

export async function discoverRepositories(
  scanRoot: string,
): Promise<DiscoveredRepositoryEntry[]> {
  const response = await get<{ repositories: DiscoveredRepositoryEntry[] }>(
    `${BASE_PATH}/repositories/discover?scan_root=${encodeURIComponent(scanRoot)}`,
  );
  return response.repositories;
}

/**
 * 只读列举本机子目录，供「选取仓库路径」的选择器使用。
 *
 * 浏览器拿不到真实绝对路径，必须由本机后端列举。不传 path 时从用户主目录开始。
 *
 * @param path 要浏览的目录绝对路径；省略则从用户主目录开始。
 */
export async function browseDirectories(
  path?: string,
): Promise<DirectoryBrowseResult> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return get<DirectoryBrowseResult>(`${BASE_PATH}/repositories/browse${query}`);
}

export async function batchAddRepositories(
  repositories: DiscoveredRepositoryEntry[],
): Promise<BatchAddRepositoriesResult> {
  return post<BatchAddRepositoriesResult>(`${BASE_PATH}/repositories/batch`, {
    repositories,
  });
}

export async function setRegistryRepositoryEnabled(
  repoId: string,
  enabled: boolean,
): Promise<void> {
  await patch(`${BASE_PATH}/repositories/${encodeURIComponent(repoId)}`, {
    enabled,
  });
}

/**
 * 移除一个仓库的注册：停掉其常驻进程并删除 registry 条目。
 *
 * 只移除注册，本地仓库目录不受影响。
 *
 * @param repoId 待移除的 registry 条目 ID。
 */
export async function removeRegistryRepository(repoId: string): Promise<void> {
  await del(`${BASE_PATH}/repositories/${encodeURIComponent(repoId)}`);
}
