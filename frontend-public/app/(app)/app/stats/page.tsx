"use client"

// 统计页：实时完成度（GitHub 口径）+ 本地运行历史趋势（SQLite 口径）。

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatLifecycleDuration,
  PHASE_LABELS,
} from "@/components/roadmap/prd-lifecycle-view";
import { formatLocalDateTime } from "@/lib/utils";
import {
  fetchCompletionStats,
  fetchPrdLifecycleStats,
  fetchRecentRuns,
  fetchRunHistoryTrend,
} from "@/lib/api/console";
import type {
  DailyRunTrendEntry,
  PrdLifecycleStats,
  RepositoryCompletionStats,
  RunRecordEntry,
  TokenUsageTotals,
} from "@/lib/api/types";
import { formatTokenCount } from "@/components/roadmap/prd-lifecycle-view";

export default function StatsPage() {
  const [stats, setStats] = useState<RepositoryCompletionStats[] | null>(null);
  const [trendRepoId, setTrendRepoId] = useState("");
  const [trendDays, setTrendDays] = useState(30);
  const [trend, setTrend] = useState<DailyRunTrendEntry[]>([]);
  const [recentRuns, setRecentRuns] = useState<RunRecordEntry[]>([]);
  const [lifecycleStats, setLifecycleStats] = useState<PrdLifecycleStats | null>(null);

  useEffect(() => {
    fetchCompletionStats()
      .then(setStats)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载完成度统计。",
        );
        setStats([]);
      });
  }, []);

  useEffect(() => {
    fetchRunHistoryTrend({ repoId: trendRepoId || undefined, days: trendDays })
      .then(setTrend)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载历史趋势。",
        );
      });
    fetchRecentRuns({ repoId: trendRepoId || undefined, limit: 30 })
      .then(setRecentRuns)
      .catch(() => setRecentRuns([]));
  }, [trendRepoId, trendDays]);

  useEffect(() => {
    let isCancelled = false;
    setLifecycleStats(null);
    fetchPrdLifecycleStats({ repoId: trendRepoId || undefined, days: trendDays })
      .then((stats) => {
        if (!isCancelled) {
          setLifecycleStats(stats);
        }
      })
      .catch((error: unknown) => {
        if (isCancelled) {
          return;
        }
        toast.error(
          error instanceof Error ? error.message : "无法加载 PRD 生命周期统计。",
        );
      });
    return () => {
      isCancelled = true;
    };
  }, [trendRepoId, trendDays]);

  const trendMax = useMemo(
    () =>
      Math.max(
        1,
        ...trend.map((entry) => entry.completed + entry.failed + entry.blocked),
      ),
    [trend],
  );

  return (
    <div className="flex flex-col gap-4 p-4 lg:p-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-50">
          完成度统计
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          实时口径来自 GitHub（closed Issue 含 agent workflow label 计为已处理）；
          历史趋势来自本地运行记录（CLI 直跑与面板托管共用）。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">实时完成度（GitHub）</CardTitle>
        </CardHeader>
        <CardContent>
          {stats === null ? (
            <Skeleton className="h-24" />
          ) : stats.length === 0 ? (
            <p className="text-sm text-slate-500">暂无仓库统计。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <th className="py-2 pr-3">仓库</th>
                    <th className="py-2 pr-3">完成率</th>
                    <th className="py-2 pr-3">完成</th>
                    <th className="py-2 pr-3">失败</th>
                    <th className="py-2 pr-3">Blocked</th>
                    <th className="py-2 pr-3">进行中</th>
                    <th className="py-2 pr-3">总计</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.map((entry) => (
                    <tr
                      key={entry.repo_id}
                      className="border-b border-slate-100 dark:border-slate-800"
                    >
                      <td className="py-2 pr-3 font-medium">
                        {entry.display_name}
                        {entry.truncated ? (
                          <Badge variant="warning" className="ml-2 text-[10px]">
                            截断
                          </Badge>
                        ) : null}
                        {entry.error ? (
                          <Badge variant="warning" className="ml-2 text-[10px]">
                            查询失败
                          </Badge>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3">
                        <CompletionRateBar rate={entry.completion_rate} />
                      </td>
                      <td className="py-2 pr-3 text-emerald-700 dark:text-emerald-400">
                        {entry.completed}
                      </td>
                      <td className="py-2 pr-3 text-red-700 dark:text-red-400">
                        {entry.failed}
                      </td>
                      <td className="py-2 pr-3 text-amber-700 dark:text-amber-400">
                        {entry.blocked}
                      </td>
                      <td className="py-2 pr-3">{entry.open_in_pipeline}</td>
                      <td className="py-2 pr-3">{entry.total_tracked}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-sm">历史趋势（本地运行记录）</CardTitle>
            <div className="flex items-center gap-2">
              <select
                className="h-8 rounded-md border border-slate-200 bg-transparent px-2 text-xs dark:border-slate-700"
                value={trendRepoId}
                onChange={(event) => setTrendRepoId(event.target.value)}
                aria-label="趋势仓库筛选"
              >
                <option value="">全部仓库</option>
                {(stats ?? []).map((entry) => (
                  <option key={entry.repo_id} value={entry.repo_id}>
                    {entry.display_name}
                  </option>
                ))}
              </select>
              <select
                className="h-8 rounded-md border border-slate-200 bg-transparent px-2 text-xs dark:border-slate-700"
                value={trendDays}
                onChange={(event) => setTrendDays(Number(event.target.value))}
                aria-label="趋势天数"
              >
                <option value={7}>7 天</option>
                <option value={30}>30 天</option>
                <option value={90}>90 天</option>
              </select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {trend.length === 0 ? (
            <p className="text-sm text-slate-500">
              所选范围内暂无运行记录。运行 <code>iar run</code> 或通过面板触发
              执行后，这里会出现按天聚合的成功/失败曲线。
            </p>
          ) : (
            <div className="flex items-end gap-1 overflow-x-auto pb-2">
              {trend.map((entry) => {
                const total = entry.completed + entry.failed + entry.blocked;
                return (
                  <div
                    key={entry.day}
                    className="flex min-w-7 flex-col items-center gap-1"
                    title={`${entry.day}：完成 ${entry.completed} / 失败 ${entry.failed} / blocked ${entry.blocked}`}
                  >
                    <div className="flex h-32 w-5 flex-col-reverse overflow-hidden rounded-sm bg-slate-100 dark:bg-slate-800">
                      <div
                        className="w-full bg-emerald-500"
                        style={{ height: `${(entry.completed / trendMax) * 100}%` }}
                      />
                      <div
                        className="w-full bg-red-500"
                        style={{ height: `${(entry.failed / trendMax) * 100}%` }}
                      />
                      <div
                        className="w-full bg-amber-500"
                        style={{ height: `${(entry.blocked / trendMax) * 100}%` }}
                      />
                    </div>
                    <span className="text-[10px] text-slate-500">
                      {entry.day.slice(5)}
                    </span>
                    <span className="text-[10px] font-medium">{total}</span>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">最近运行记录</CardTitle>
        </CardHeader>
        <CardContent>
          {recentRuns.length === 0 ? (
            <p className="text-sm text-slate-500">暂无运行记录。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <th className="py-2 pr-3">时间</th>
                    <th className="py-2 pr-3">仓库</th>
                    <th className="py-2 pr-3">Issue</th>
                    <th className="py-2 pr-3">结果</th>
                    <th className="py-2 pr-3">触发</th>
                    <th className="py-2 pr-3">Agent</th>
                    <th className="py-2 pr-3">耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {recentRuns.map((run, index) => (
                    <tr
                      key={`${run.repo_id}-${run.issue_number}-${run.started_at}-${index}`}
                      className="border-b border-slate-100 dark:border-slate-800"
                    >
                      <td className="py-2 pr-3 font-mono text-[11px] text-slate-500">
                        {formatLocalDateTime(run.started_at)}
                      </td>
                      <td className="py-2 pr-3">{run.repo_id}</td>
                      <td className="py-2 pr-3 font-mono text-xs">
                        #{run.issue_number}
                      </td>
                      <td className="py-2 pr-3">
                        <OutcomeBadge outcome={run.outcome} />
                      </td>
                      <td className="py-2 pr-3 text-xs">{run.trigger}</td>
                      <td className="py-2 pr-3 text-xs">{run.agent}</td>
                      <td className="py-2 pr-3 text-xs">
                        {formatDuration(run.duration_seconds)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card data-testid="stats-prd-lifecycle">
        <CardHeader>
          <CardTitle className="text-sm">PRD 执行分析（生命周期口径）</CardTitle>
          <CardDescription>
            以完整 PRD 生命周期为口径（非单次 runner 调用），共用上方的仓库与时间范围筛选；
            进行中记录与无法关联 PRD 的旧记录不进入完成分位数。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {lifecycleStats === null ? (
            <Skeleton className="h-24" />
          ) : (
            <div className="space-y-4">
              <div
                className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6"
                data-testid="stats-prd-lifecycle-metrics"
              >
                <LifecycleMetricTile
                  label="已完成 PRD 数"
                  value={String(lifecycleStats.completed_runs)}
                />
                <LifecycleMetricTile
                  label="平均端到端"
                  value={formatLifecycleDuration(lifecycleStats.average_end_to_end_seconds)}
                />
                <LifecycleMetricTile
                  label="中位数"
                  value={formatLifecycleDuration(lifecycleStats.median_end_to_end_seconds)}
                />
                <LifecycleMetricTile
                  label="P90"
                  value={formatLifecycleDuration(lifecycleStats.p90_end_to_end_seconds)}
                />
                <LifecycleMetricTile
                  label="平均阻塞"
                  value={formatLifecycleDuration(lifecycleStats.average_blocked_seconds)}
                />
                <LifecycleMetricTile
                  label="阶段瓶颈"
                  value={formatBottleneck(lifecycleStats)}
                />
              </div>

              {lifecycleStats.runs.length === 0 ? (
                <p className="text-sm text-slate-500" data-testid="stats-prd-lifecycle-empty">
                  所选范围内暂无 PRD 生命周期记录。
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                        <th className="py-2 pr-3">PRD</th>
                        <th className="py-2 pr-3">Issue</th>
                        <th className="py-2 pr-3">当前阶段</th>
                        <th className="py-2 pr-3">端到端</th>
                        <th className="py-2 pr-3">执行</th>
                        <th className="py-2 pr-3">等待</th>
                        <th className="py-2 pr-3">阻塞</th>
                        <th className="py-2 pr-3">完整性</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lifecycleStats.runs.map((run) => (
                        <tr
                          key={run.run_id}
                          className="border-b border-slate-100 dark:border-slate-800"
                        >
                          <td
                            className="py-2 pr-3 font-medium"
                            title={run.prd_path}
                          >
                            {prdBasename(run.prd_path)}
                          </td>
                          <td className="py-2 pr-3 font-mono text-xs">
                            {run.issue_number ? `#${run.issue_number}` : "—"}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {run.in_progress
                              ? "进行中"
                              : (PHASE_LABELS[run.current_phase] ?? run.current_phase)}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {formatLifecycleDuration(run.durations.end_to_end_seconds)}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {formatLifecycleDuration(run.durations.active_seconds)}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {formatLifecycleDuration(run.durations.waiting_seconds)}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {formatLifecycleDuration(run.durations.blocked_seconds)}
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {run.history_complete ? (
                              <span className="text-emerald-700 dark:text-emerald-400">完整</span>
                            ) : (
                              <Badge variant="warning" className="text-[10px]">
                                不完整
                              </Badge>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <p
                className="text-xs text-slate-500"
                data-testid="stats-prd-lifecycle-disclosure"
              >
                未关联 PRD 的旧记录 {lifecycleStats.unlinked_run_count} 条（已排除出分位数）；
                观测不完整的 run {lifecycleStats.incomplete_run_count} 条。
              </p>

              <TokenUsageSection stats={lifecycleStats} />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * 把阶段瓶颈渲染为中文标签加耗时。
 *
 * @param stats - 生命周期统计（含可空的瓶颈阶段与耗时）。
 * @returns 中文阶段标签与耗时；无瓶颈时为 ``—``。
 */
function formatBottleneck(stats: PrdLifecycleStats): string {
  if (stats.bottleneck_phase === null) {
    return "—";
  }
  const label = PHASE_LABELS[stats.bottleneck_phase] ?? stats.bottleneck_phase;
  if (stats.bottleneck_phase_seconds === null) {
    return label;
  }
  return `${label} · ${formatLifecycleDuration(stats.bottleneck_phase_seconds)}`;
}

/**
 * 取 PRD 仓库相对路径的文件名用于表格展示。
 *
 * @param prdPath - PRD 的仓库相对路径。
 * @returns 路径最后一段；路径为空时原样返回。
 */
function prdBasename(prdPath: string): string {
  const segments = prdPath.split("/");
  return segments[segments.length - 1] || prdPath;
}

function LifecycleMetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

/** 流程标识 -> 中文标签；未识别的 flow 原样展示。 */
const TOKEN_FLOW_LABELS: Record<string, string> = {
  implement: "实现",
  verify: "验证",
  supervise: "评审",
  fix: "修复",
  closeout: "收尾",
};

/**
 * 计算缓存命中率。
 *
 * 口径与后端一致：命中 ÷ 输入侧实际处理量（输入 + 缓存读 + 缓存写）。
 *
 * @param row - 单个分组的 token 累计。
 * @returns 0–1 之间的比率；输入侧为 0（无缓存数据）时返回 null。
 */
function cacheHitRate(row: TokenUsageTotals): number | null {
  const inputSide =
    row.input_tokens + row.cache_read_input_tokens + row.cache_creation_input_tokens;
  if (inputSide <= 0) {
    return null;
  }
  return row.cache_read_input_tokens / inputSide;
}

/**
 * PRD 生命周期卡片内的 Token 用量汇总区。
 *
 * 数据来自 lifecycle 事件 detail 的聚合（``token_usage``）；缺 usage 的事件
 * 不计入。按流程与按 agent 两张小表分别给出总量、四项明细与缓存命中率。
 *
 * @param props.stats - 仓库级 PRD 生命周期统计。
 * @returns Token 汇总区块；无数据时渲染明确空态。
 */
function TokenUsageSection({ stats }: { stats: PrdLifecycleStats }) {
  const byFlow = Object.entries(stats.token_usage?.by_flow ?? {});
  const byAgent = Object.entries(stats.token_usage?.by_agent ?? {});
  const hasData = byFlow.length > 0 || byAgent.length > 0;
  return (
    <div data-testid="stats-token-usage" className="space-y-3">
      <p className="text-sm font-medium">Token 用量</p>
      {!hasData ? (
        <p className="text-sm text-slate-500" data-testid="stats-token-usage-empty">
          所选范围内暂无 token 用量数据（agent 未上报 usage 或为旧记录）。
        </p>
      ) : (
        <>
          <TokenUsageTable title="按流程" rows={byFlow} />
          <TokenUsageTable title="按 agent" rows={byAgent} />
          <p className="text-xs text-slate-500" data-testid="stats-token-usage-note">
            总量为实际处理量口径（输入 + 输出 + 缓存读 + 缓存写）；缺 usage 的调用不计入，
            展示为「—」而非 0。
          </p>
        </>
      )}
    </div>
  );
}

/**
 * 渲染一张 Token 用量小表（按总量降序）。
 *
 * @param props.title - 表格标题（按流程 / 按 agent）。
 * @param props.rows - 分组键到用量累计的条目列表。
 * @returns 紧凑用量表格。
 */
function TokenUsageTable({
  title,
  rows,
}: {
  title: string;
  rows: [string, TokenUsageTotals][];
}) {
  const sorted = [...rows].sort(
    ([, left], [, right]) => right.total_tokens - left.total_tokens,
  );
  return (
    <div className="overflow-x-auto">
      <p className="mb-1 text-xs text-slate-500">{title}</p>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
            <th className="py-2 pr-3">分组</th>
            <th className="py-2 pr-3">总量</th>
            <th className="py-2 pr-3">输入</th>
            <th className="py-2 pr-3">输出</th>
            <th className="py-2 pr-3">缓存读</th>
            <th className="py-2 pr-3">缓存写</th>
            <th className="py-2 pr-3">命中率</th>
            <th className="py-2 pr-3">调用数</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(([key, row]) => {
            const rate = cacheHitRate(row);
            const label = TOKEN_FLOW_LABELS[key] ?? key;
            return (
              <tr
                key={key}
                className="border-b border-slate-100 dark:border-slate-800"
              >
                <td className="py-2 pr-3 font-medium">{label}</td>
                <td className="py-2 pr-3 font-semibold">
                  {formatTokenCount(row.total_tokens)}
                </td>
                <td className="py-2 pr-3 text-xs">{formatTokenCount(row.input_tokens)}</td>
                <td className="py-2 pr-3 text-xs">{formatTokenCount(row.output_tokens)}</td>
                <td className="py-2 pr-3 text-xs">
                  {formatTokenCount(row.cache_read_input_tokens)}
                </td>
                <td className="py-2 pr-3 text-xs">
                  {formatTokenCount(row.cache_creation_input_tokens)}
                </td>
                <td className="py-2 pr-3 text-xs">
                  {rate === null ? "—" : `${Math.round(rate * 100)}%`}
                </td>
                <td className="py-2 pr-3 text-xs">{row.usage_count}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CompletionRateBar({ rate }: { rate: number | null }) {
  if (rate === null) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  const percent = Math.round(rate * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full rounded-full bg-emerald-500"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs font-medium">{percent}%</span>
    </div>
  );
}

function OutcomeBadge({ outcome }: { outcome: RunRecordEntry["outcome"] }) {
  const variant =
    outcome === "completed" ? "ready" : outcome === "failed" ? "warning" : "default";
  return <Badge variant={variant}>{outcome}</Badge>;
}

function formatDuration(durationSeconds: number): string {
  if (durationSeconds < 60) {
    return `${Math.round(durationSeconds)}s`;
  }
  return `${Math.round(durationSeconds / 60)}m`;
}
