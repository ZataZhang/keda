"use client"
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { IssueDetail } from "@/components/agent-runner/issue-detail";
import { MonitorSettingsPanel } from "@/components/agent-runner/monitor-settings-panel";
import { RepositoryOverview } from "@/components/agent-runner/repository-overview";
import { formatLocalDateTime } from "@/lib/utils";
import {
  fetchIssueDetail,
  fetchOverviewJobsByRepo,
  fetchOverviewSnapshots,
  pollOverviewJob,
} from "@/lib/api/agentRunner";
import {
  executeIssueAction,
  fetchCompletionStats,
} from "@/lib/api/console";
import type {
  IssueMonitoringSnapshot,
  MonitorSnapshotsResponse,
  RepositoryCompletionStats,
  RepositoryMonitoringOverview,
  UnreachableRepository,
} from "@/lib/api/types";

/** 轻量轮询本地快照的间隔：纯本地读，后台同步完成后界面自动跟上。 */
const POLL_INTERVAL_MS = 15_000;

type LoadState =
  | { kind: "loading" }
  | {
      kind: "ready";
      repos: RepoSlot[];
      scannedAt: string;
      syncStatus: MonitorSnapshotsResponse["sync_status"];
      unreachable: UnreachableRepository[];
    }
  | { kind: "error"; message: string };

type RefreshState =
  | { kind: "idle" }
  | { kind: "syncing"; jobId: string; startedAt: number; scope: "all" | string };

/** Display state for a per-repository card refresh button. */
type RepoRefreshState =
  | { kind: "idle" }
  | { kind: "loading"; startedAt: number }
  | { kind: "ready" }
  | { kind: "error"; message: string };

const STUCK_THRESHOLD_SECONDS = 120;

/** Per-repository slot — one card on the dashboard. */
type RepoSlot =
  /** 当前启用但本地还没有快照：显示"尚未同步"占位卡，等待后端首扫。 */
  | { kind: "missing"; repoId: string }
  | { kind: "ready"; repository: RepositoryMonitoringOverview };

/** Concurrency-limited async helper: at most `limit` tasks run concurrently. */
async function runWithLimit<T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let index = 0;
  const launch = async () => {
    while (index < items.length) {
      const current = index++;
      await worker(items[current]);
    }
  };
  const launchers = Array.from({ length: Math.min(limit, items.length) }, () => launch());
  await Promise.all(launchers);
}

/** Repository id for a slot — missing slots carry the id without a payload. */
function getRepoIdFromSlot(slot: RepoSlot): string {
  if (slot.kind === "ready") return slot.repository.repo_id;
  return slot.repoId;
}

export default function DashboardPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedIssueNumber, setSelectedIssueNumber] = useState<number | null>(
    null,
  );
  const [selectedIssue, setSelectedIssue] =
    useState<IssueMonitoringSnapshot | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [completionStats, setCompletionStats] = useState<
    RepositoryCompletionStats[]
  >([]);
  const [actionPending, setActionPending] = useState(false);
  const [refreshState, setRefreshState] = useState<RefreshState>({ kind: "idle" });
  const [repoRefresh, setRepoRefresh] = useState<Record<string, RepoRefreshState>>({});
  const [settingsOpen, setSettingsOpen] = useState(false);

  const refreshAbortRef = useRef<AbortController | null>(null);

  /**
   * 读本地快照并渲染。首屏与 15s 轮询都走这里——快照接口只查本地 SQLite,
   * 不扫描 GitHub,所以轮询成本约等于零。
   */
  const loadSnapshots = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetchOverviewSnapshots();
      if (signal?.aborted) return;
      const slots: RepoSlot[] = [
        ...response.repositories.map((entry) => ({
          kind: "ready" as const,
          repository: entry.overview,
        })),
        ...response.missing_repo_ids.map((repoId) => ({
          kind: "missing" as const,
          repoId,
        })),
      ];
      setState({
        kind: "ready",
        repos: slots,
        scannedAt: response.scanned_at ?? "",
        syncStatus: response.sync_status,
        unreachable: response.unreachable_repositories ?? [],
      });
    } catch (error: unknown) {
      if (signal?.aborted) return;
      setState((prev) =>
        // 已有数据时不因一次轮询失败把整页打成错误态。
        prev.kind === "ready"
          ? prev
          : {
              kind: "error",
              message:
                error instanceof Error ? error.message : "无法加载监控快照。",
            },
      );
    }
  }, []);

  /** 首屏进入后自动选中第一个 issue，避免右侧详情空着。 */
  useEffect(() => {
    if (selectedIssueNumber !== null) return;
    if (state.kind !== "ready") return;
    const firstIssue = state.repos
      .filter(
        (slot): slot is Extract<RepoSlot, { kind: "ready" }> =>
          slot.kind === "ready",
      )
      .flatMap((slot) => slot.repository.issues)
      .find(Boolean);
    if (firstIssue) {
      setSelectedIssueNumber(firstIssue.number);
      setSelectedIssue(firstIssue);
    }
  }, [state, selectedIssueNumber]);

  const loadCompletionStats = useCallback(async (signal?: AbortSignal) => {
    try {
      const stats = await fetchCompletionStats();
      if (signal?.aborted) return;
      setCompletionStats(stats);
    } catch {
      // 失败不影响监控主视图。
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadSnapshots(controller.signal);
    void loadCompletionStats(controller.signal);
    // 轻量轮询本地快照：后台同步一写回，界面最多 15 秒内自动跟上。
    const timer = window.setInterval(() => {
      void loadSnapshots(controller.signal);
    }, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [loadSnapshots, loadCompletionStats]);

  // 详情加载逻辑（点 issue 时拉一次）。
  useEffect(() => {
    if (selectedIssueNumber === null) {
      setSelectedIssue(null);
      return;
    }
    if (state.kind !== "ready") {
      return;
    }
    const cached = state.repos
      .filter((slot): slot is Extract<RepoSlot, { kind: "ready" }> => slot.kind === "ready")
      .flatMap((slot) => slot.repository.issues)
      .find((issue) => issue.number === selectedIssueNumber);
    if (cached) {
      setSelectedIssue(cached);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailError(null);
    fetchIssueDetail(selectedIssueNumber)
      .then((detail) => {
        if (cancelled) return;
        setSelectedIssue(detail);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setDetailError(
          error instanceof Error ? error.message : "无法加载 Issue 详情。",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIssueNumber, state]);

  /** 全量刷新：起后台 job 重扫并写回快照，期间保留旧数据供继续浏览。 */
  const handleRefreshAll = useCallback(async () => {
    if (refreshState.kind === "syncing") return;
    if (refreshAbortRef.current) refreshAbortRef.current.abort();
    const controller = new AbortController();
    refreshAbortRef.current = controller;
    const startedAt = Date.now();

    try {
      const handle = await fetchOverviewJobsByRepo();
      if (controller.signal.aborted) return;
      const repoIds = Object.keys(handle.jobs_by_repo);
      setRefreshState({
        kind: "syncing",
        jobId: repoIds.join(","),
        startedAt,
        scope: "all",
      });

      // 不清空 slot：刷新期间页面继续显示上一份快照，不阻塞浏览。
      const failures: string[] = [];
      await runWithLimit(repoIds, 3, async (repoId) => {
        if (controller.signal.aborted) return;
        const snapshot = await pollOverviewJob(
          handle.jobs_by_repo[repoId],
          { intervalMs: 5000, signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        if (snapshot.status !== "completed") {
          failures.push(`${repoId}: ${snapshot.error ?? "扫描失败"}`);
        }
      });
      if (controller.signal.aborted) return;

      // 刷新完成后重新读一次快照，让界面立即反映新数据。
      await loadSnapshots(controller.signal);
      void loadCompletionStats(controller.signal);
      if (failures.length > 0) {
        toast.error(`部分仓库刷新失败：${failures.join("；")}`);
      } else {
        const seconds = Math.round((Date.now() - startedAt) / 1000);
        toast.success(`监控数据已刷新（${seconds}s）`);
      }
      setRefreshState({ kind: "idle" });
    } catch (error: unknown) {
      if (controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : "刷新失败";
      toast.error(message);
      setRefreshState({ kind: "idle" });
    }
  }, [refreshState, loadCompletionStats, loadSnapshots]);

  useEffect(() => {
    return () => {
      refreshAbortRef.current?.abort();
    };
  }, []);

  /** 单仓库刷新:起独立 async job,完成后替换 slot。 */
  const handleRefreshRepo = useCallback(async (repoId: string) => {
    if (refreshAbortRef.current) {
      refreshAbortRef.current.abort();
    }
    const controller = new AbortController();
    refreshAbortRef.current = controller;
    const startedAt = Date.now();
    setRepoRefresh((prev) => ({
      ...prev,
      [repoId]: { kind: "loading", startedAt },
    }));
    try {
      // 单仓库也走 async job：后端扫描并写回快照，前端保留旧卡片不闪空。
      const handle = await fetchOverviewJobsByRepo();
      if (controller.signal.aborted) return;
      const jobId = handle.jobs_by_repo[repoId];
      if (!jobId) {
        throw new Error(`${repoId} 不在当前启用仓库列表中`);
      }
      const snapshot = await pollOverviewJob(jobId, {
        intervalMs: 5000,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (snapshot.status !== "completed") {
        throw new Error(snapshot.error ?? `${repoId} 失败`);
      }
      // 重新读快照：写入由后端完成，界面只负责呈现最新落库结果。
      await loadSnapshots(controller.signal);
      if (controller.signal.aborted) return;
      const seconds = Math.round((Date.now() - startedAt) / 1000);
      setRepoRefresh((prev) => ({
        ...prev,
        [repoId]: { kind: "ready" },
      }));
      toast.success(`${repoId} 已刷新（${seconds}s）`);
      window.setTimeout(() => {
        setRepoRefresh((prev) => {
          const next = { ...prev };
          delete next[repoId];
          return next;
        });
      }, 1500);
    } catch (error: unknown) {
      if (controller.signal.aborted) return;
      const message =
        error instanceof Error ? error.message : `${repoId} 刷新失败`;
      setRepoRefresh((prev) => ({
        ...prev,
        [repoId]: { kind: "error", message },
      }));
      toast.error(message);
    }
  }, [loadSnapshots]);

  const readySlots = useMemo(() => {
    if (state.kind !== "ready") return [] as Extract<RepoSlot, { kind: "ready" }>[];
    return state.repos.filter(
      (slot): slot is Extract<RepoSlot, { kind: "ready" }> =>
        slot.kind === "ready",
    );
  }, [state]);

  const totalIssues = useMemo(() => {
    return readySlots.reduce((acc, slot) => acc + slot.repository.issues.length, 0);
  }, [readySlots]);

  const totalAnomalies = useMemo(() => {
    return readySlots.reduce((acc, slot) => acc + slot.repository.anomaly_count, 0);
  }, [readySlots]);

  const selectedIssueRepoId = useMemo(() => {
    if (state.kind !== "ready" || selectedIssueNumber === null) {
      return null;
    }
    for (const slot of readySlots) {
      if (slot.repository.issues.some((issue) => issue.number === selectedIssueNumber)) {
        return slot.repository.repo_id;
      }
    }
    return readySlots[0]?.repository.repo_id ?? null;
  }, [state, selectedIssueNumber, readySlots]);

  const statsByRepoId = useMemo(() => {
    const lookup: Record<string, RepositoryCompletionStats> = {};
    for (const entry of completionStats) {
      lookup[entry.repo_id] = entry;
    }
    return lookup;
  }, [completionStats]);

  async function handleIssueAction(
    action: "retry_failed" | "blocked_continue",
  ) {
    if (!selectedIssue || !selectedIssueRepoId) return;
    const verb = action === "retry_failed" ? "重试" : "继续";
    const confirmed = window.confirm(
      `确认对 Issue #${selectedIssue.number} 执行「${verb}」？`,
    );
    if (!confirmed) return;
    setActionPending(true);
    try {
      const result = await executeIssueAction(
        selectedIssueRepoId,
        selectedIssue.number,
        action,
      );
      toast.success(result.detail);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `${verb}失败。`);
    } finally {
      setActionPending(false);
    }
  }

  const isRefreshingAll =
    refreshState.kind === "syncing" && refreshState.scope === "all";
  const refreshElapsed = refreshState.kind === "syncing"
    ? Math.floor((Date.now() - refreshState.startedAt) / 1000)
    : 0;

  return (
    <div className="flex flex-col gap-4 p-4 lg:p-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-50">
            Agent Runner 管理终端
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            多项目队列、事件时间线与异常监控。failed/blocked Issue 可直接重试或
            继续；进程托管与完成度统计见左侧导航。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="font-mono text-xs text-slate-500"
            data-testid="dashboard-last-sync"
          >
            {state.kind === "ready" && state.scannedAt
              ? `上次同步 ${formatLocalDateTime(state.scannedAt)}`
              : "尚未同步"}
          </span>
          {isRefreshingAll ? (
            <span className="text-xs text-slate-500">
              正在全量刷新… {refreshElapsed}s
            </span>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            onClick={() => setSettingsOpen((open) => !open)}
            data-testid="dashboard-settings-toggle"
          >
            同步设置
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={isRefreshingAll}
            onClick={() => void handleRefreshAll()}
            data-testid="dashboard-refresh-all"
          >
            {isRefreshingAll ? "刷新中…" : "刷新全部"}
          </Button>
        </div>
      </div>

      {settingsOpen ? (
        <MonitorSettingsPanel onClose={() => setSettingsOpen(false)} />
      ) : null}

      {state.kind === "loading" ? <LoadingSkeleton /> : null}

      {state.kind === "error" ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-red-700 dark:text-red-300">
              加载监控数据失败：{state.message}
            </p>
          </CardContent>
        </Card>
      ) : null}

      {state.kind === "ready" && state.syncStatus === "pending_first_sync" ? (
        <Card data-testid="dashboard-empty-state">
          <CardContent className="flex flex-wrap items-center gap-3 py-6">
            <span className="text-sm text-slate-600 dark:text-slate-300">
              ⏳ 尚未同步 — 正在后台执行首次扫描，完成后自动显示数据
            </span>
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={isRefreshingAll}
              onClick={() => void handleRefreshAll()}
            >
              立即刷新
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {state.kind === "ready" && state.unreachable.length > 0 ? (
        <Card
          className="border-amber-300 dark:border-amber-700"
          data-testid="dashboard-unreachable-warning"
        >
          <CardContent className="pt-4">
            <p className="mb-1 text-sm font-medium text-amber-800 dark:text-amber-200">
              {state.unreachable.length} 个已注册仓库无法访问（已从监控中跳过）：
            </p>
            <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-300">
              {state.unreachable.map((entry) => (
                <li key={entry.repo_id}>
                  <code>{entry.repo_id}</code>：{entry.configured_path} — {entry.error}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {state.kind === "ready" ? (
        <>
          <SummaryStrip
            repositoryCount={state.repos.length}
            loadedCount={readySlots.length}
            issueCount={totalIssues}
            anomalyCount={totalAnomalies}
            scannedAt={state.scannedAt}
          />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div className="space-y-4">
              {state.repos.map((slot, i) => (
                <RepoCard
                  key={i}
                  slot={slot}
                  stats={statsByRepoId[getRepoIdFromSlot(slot)]}
                  selectedIssueNumber={selectedIssueNumber}
                  onSelectIssue={(issue) => setSelectedIssueNumber(issue.number)}
                  onRefresh={(id) => void handleRefreshRepo(id)}
                  repoRefreshState={repoRefresh[getRepoIdFromSlot(slot)]}
                />
              ))}
            </div>
            <div className="min-h-[24rem]">
              {selectedIssue ? (
                <div className="space-y-2">
                  <IssueActionBar
                    issue={selectedIssue}
                    pending={actionPending}
                    onAction={(action) => void handleIssueAction(action)}
                  />
                  <IssueDetail issue={selectedIssue} />
                </div>
              ) : (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Issue 详情</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-slate-500">
                      点击左侧 Issue 行查看事件时间线和异常。
                    </p>
                  </CardContent>
                </Card>
              )}
              {detailError ? (
                <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                  详情加载失败：{detailError}
                </p>
              ) : null}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function RepoRefreshButton({
  repoId,
  state,
  onRefresh,
}: {
  repoId: string;
  state: RepoRefreshState | undefined;
  onRefresh: (repoId: string) => void;
}) {
  const kind = state?.kind ?? "idle";
  if (kind === "loading") {
    return (
      <Button
        size="sm"
        variant="ghost"
        disabled
        data-testid={`dashboard-refresh-repo-${repoId}`}
      >
        刷新中…
      </Button>
    );
  }
  if (kind === "error") {
    return (
      <Button
        size="sm"
        variant="outline"
        onClick={() => onRefresh(repoId)}
        data-testid={`dashboard-refresh-repo-${repoId}`}
      >
        重试
      </Button>
    );
  }
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => onRefresh(repoId)}
      data-testid={`dashboard-refresh-repo-${repoId}`}
    >
      {kind === "ready" ? "✓ 已刷新" : "刷新"}
    </Button>
  );
}

function IssueActionBar({
  issue,
  pending,
  onAction,
}: {
  issue: IssueMonitoringSnapshot;
  pending: boolean;
  onAction: (action: "retry_failed" | "blocked_continue") => void;
}) {
  const isFailed = issue.primary_label.includes("failed");
  const isBlocked = issue.primary_label.includes("blocked");
  if (!isFailed && !isBlocked) return null;
  return (
    <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950">
      <span className="text-xs text-amber-800 dark:text-amber-200">
        {isFailed
          ? "该 Issue 处于 failed 状态，可重试（label 翻转回 ready）。"
          : "该 Issue 处于 blocked 状态，可继续（启动 blocked-continue 进程）。"}
      </span>
      <Button
        size="sm"
        variant="outline"
        className="ml-auto"
        disabled={pending}
        onClick={() => onAction(isFailed ? "retry_failed" : "blocked_continue")}
      >
        {pending ? "执行中…" : isFailed ? "重试" : "继续"}
      </Button>
    </div>
  );
}

function CompletionSummaryStrip({
  stats,
}: {
  stats: RepositoryCompletionStats | undefined;
}) {
  if (!stats || stats.total_tracked === 0) return null;
  const percent =
    stats.completion_rate !== null
      ? `${Math.round(stats.completion_rate * 100)}%`
      : "—";
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
      <span className="font-medium text-slate-700 dark:text-slate-300">
        完成率 {percent}
      </span>
      <span>✓ {stats.completed}</span>
      <span className="text-red-600 dark:text-red-400">✗ {stats.failed}</span>
      <span className="text-amber-600 dark:text-amber-400">
        ⛔ {stats.blocked}
      </span>
      <span>进行中 {stats.open_in_pipeline}</span>
    </div>
  );
}

function RepoCard({
  slot,
  stats,
  selectedIssueNumber,
  onSelectIssue,
  onRefresh,
  repoRefreshState,
}: {
  slot: RepoSlot;
  stats: RepositoryCompletionStats | undefined;
  selectedIssueNumber: number | null;
  onSelectIssue: (issue: IssueMonitoringSnapshot) => void;
  onRefresh: (repoId: string) => void;
  repoRefreshState: RepoRefreshState | undefined;
}) {
  const repoId = getRepoIdFromSlot(slot);

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 px-1">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="font-mono text-xs text-slate-700 dark:text-slate-300"
            data-testid="dashboard-repo-header"
          >
            {repoId}
          </span>
          {slot.kind === "ready" ? (
            <CompletionSummaryStrip stats={stats} />
          ) : null}
        </div>
        <RepoRefreshButton
          repoId={repoId}
          state={repoRefreshState}
          onRefresh={onRefresh}
        />
      </div>
      {slot.kind === "ready" ? (
        <RepositoryOverview
          repository={slot.repository}
          onSelectIssue={onSelectIssue}
          selectedIssueNumber={selectedIssueNumber}
        />
      ) : (
        <Card data-testid={`dashboard-repo-missing-${repoId}`}>
          <CardContent className="flex items-center gap-3 py-6">
            <Skeleton className="h-10 w-10 rounded-full" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-3 w-1/2" />
            </div>
            <span className="text-xs text-slate-500">尚未同步，等待后台扫描…</span>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function SummaryStrip({
  repositoryCount,
  loadedCount,
  issueCount,
  anomalyCount,
  scannedAt,
}: {
  repositoryCount: number;
  loadedCount: number;
  issueCount: number;
  anomalyCount: number;
  scannedAt: string;
}) {
  const allLoaded = loadedCount >= repositoryCount && repositoryCount > 0;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
      <Badge variant={allLoaded ? "ready" : "warning"}>
        仓库 {loadedCount}/{repositoryCount}
      </Badge>
      <Badge variant="default">活跃 Issue {issueCount}</Badge>
      <Badge
        variant={anomalyCount > 0 ? "warning" : "ready"}
        className="text-xs"
      >
        异常 {anomalyCount}
      </Badge>
      <span className="ml-auto font-mono text-[11px] text-slate-400">
        {scannedAt ? `scanned_at ${formatLocalDateTime(scannedAt)}` : "等待首批结果…"}
      </span>
    </div>
  );
}

function LoadingSkeleton() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const remaining = Math.max(0, 15 - elapsed);
  const isStuck = elapsed > STUCK_THRESHOLD_SECONDS;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>正在扫描仓库状态，请稍候…</span>
          <span className="font-mono">
            {remaining > 0
              ? `预计还需 ${remaining} 秒`
              : isStuck
                ? "用时较长，可点击「刷新全部」切换异步模式"
                : "即将完成，请稍候…"}
          </span>
        </div>
        <Skeleton className="h-64" />
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>加载 Issue 详情与事件时间线…</span>
          {isStuck ? (
            <span className="text-amber-600 dark:text-amber-400">
              首次加载 7 个仓库需要较长时间
            </span>
          ) : null}
        </div>
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}
