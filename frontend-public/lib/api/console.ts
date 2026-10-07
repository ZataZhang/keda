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
  ConsoleContext,
  DailyRunTrendEntry,
  DirectoryBrowseResult,
  DiscoveredRepositoryEntry,
  IssueLogChunk,
  MonitorSettings,
  PrdLifecycleStats,
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

// ── Issue 实时输出 ──────────────────────────────────────────────────────────

/**
 * 按仓库 + Issue 号续读该 Issue 的 Agent 可见输出。
 *
 * 与托管进程日志端点刻意分开：Issue 日志不属于托管进程 registry，读取范围
 * 被限制在该注册仓库的固定日志子树内，不接受客户端传入的文件路径。
 *
 * @param params.repoId - 已注册仓库 ID。
 * @param params.issueNumber - Issue 编号（正整数）。
 * @param params.offset - 字节偏移；``0`` 表示从头读。
 * @param params.attemptId - 显式指定的尝试；省略表示读最新尝试。
 * @param params.tail - 为真时忽略 ``offset``，返回末尾窗口并对齐
 *   ``next_offset`` 到文件末尾（用于「先尾部后增量」的首次拉取）。
 * @returns 带尝试标识与续读偏移的日志块。
 */
export async function fetchIssueLog(params: {
  repoId: string;
  issueNumber: number;
  offset?: number;
  attemptId?: string | null;
  tail?: boolean;
}): Promise<IssueLogChunk> {
  const searchParams = new URLSearchParams();
  if (params.offset !== undefined) {
    searchParams.set("offset", String(params.offset));
  }
  if (params.attemptId) {
    searchParams.set("attempt_id", params.attemptId);
  }
  if (params.tail) {
    searchParams.set("tail", "true");
  }
  const query = searchParams.toString();
  return get<IssueLogChunk>(
    `${BASE_PATH}/console/repositories/${encodeURIComponent(params.repoId)}` +
      `/issues/${params.issueNumber}/logs${query ? `?${query}` : ""}`,
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

/**
 * 读取 PRD 维度的端到端统计（含均值 / 中位数 / P90 / 阶段瓶颈与 PRD 明细）。
 *
 * 与 `fetchRunHistoryTrend`（单次 runner 调用口径）刻意分开；本接口直接返回
 * 统计对象本身，不带 `{ trend }` 之类的外层包裹。
 *
 * @param params.repoId - 仓库标识；省略表示全部仓库。
 * @param params.days - 时间窗口天数（7/30/90），默认 30。
 * @returns 仓库级 PRD 生命周期统计。
 */
export async function fetchPrdLifecycleStats(params: {
  repoId?: string;
  days?: number;
}): Promise<PrdLifecycleStats> {
  const searchParams = new URLSearchParams();
  if (params.repoId) {
    searchParams.set("repo_id", params.repoId);
  }
  searchParams.set("days", String(params.days ?? 30));
  return get<PrdLifecycleStats>(
    `${BASE_PATH}/console/stats/prd-lifecycle?${searchParams.toString()}`,
  );
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

/**
 * 读取 console 进程 cwd 匹配到的 registry 仓库。
 *
 * 面板首屏默认仓库的事实来源：在哪个仓库目录敲 `iar console` 就优先选中哪个
 * 仓库。cwd 匹配不上是正常状态，由返回值的 `status` 表达而非报错。
 */
export async function fetchConsoleContext(): Promise<ConsoleContext> {
  return get<ConsoleContext>(`${BASE_PATH}/console/context`);
}

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
