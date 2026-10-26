"use client"

// 统计页：实时完成度（GitHub 口径）+ 本地运行历史趋势（SQLite 口径）。

import { ConsolePage, ConsolePageHeader, ConsoleToolbar } from "@/components/layout/console-page";
import { NativeSelect } from "@/components/ui/native-select";
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { RunDetailDialog } from "@/components/agent-runner/run-detail-dialog"
import { RecentRunGroups } from "@/components/agent-runner/recent-run-groups"
import { TokenUsageSection } from "@/components/stats/token-usage-section"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  STATS_SECTION_IDS,
  StatsSectionTabs,
} from "@/components/stats/stats-section-tabs"
import { AgentPerformanceSection } from "@/components/stats/agent-performance-section"
import {
  formatLifecycleDuration,
  PHASE_LABELS,
} from "@/components/backlog/prd-lifecycle-format"
import {
  fetchAgentPerformanceStats,
  fetchCompletionStats,
  fetchModelPricingSettings,
  fetchPrdLifecycleStats,
  fetchRecentRuns,
  fetchRunHistoryTrend,
} from "@/lib/api/console"
import type {
  AgentPerformanceStats,
  DailyRunTrendEntry,
  ModelTokenPricing,
  PrdLifecycleStats,
  RepositoryCompletionStats,
  RunRecordEntry,
} from "@/lib/api/types"

type AgentPerformanceQueryState = {
  filterKey: string
  stats: AgentPerformanceStats | null
  error: string | null
}

type PrdLifecycleQueryState = {
  filterKey: string
  stats: PrdLifecycleStats
}

/** 汇总 GitHub 完成情况、本地运行趋势、模型成本与 PRD 生命周期统计。 */
export default function StatsPage() {
  const [stats, setStats] = useState<RepositoryCompletionStats[] | null>(null)
  const [modelPricing, setModelPricing] = useState<ModelTokenPricing[]>([])
  const [modelPricingError, setModelPricingError] = useState<string | null>(
    null
  )
  const [trendRepoId, setTrendRepoId] = useState("")
  const [trendDays, setTrendDays] = useState(30)
  const [trend, setTrend] = useState<DailyRunTrendEntry[]>([])
  const [recentRuns, setRecentRuns] = useState<RunRecordEntry[]>([])
  const [selectedRun, setSelectedRun] = useState<RunRecordEntry | null>(null)
  const [lifecycleQueryState, setLifecycleQueryState] =
    useState<PrdLifecycleQueryState | null>(null)
  const [agentPerformanceQueryState, setAgentPerformanceQueryState] =
    useState<AgentPerformanceQueryState | null>(null)
  const agentPerformanceFilterKey = `${trendRepoId}|${trendDays}`
  const currentAgentPerformanceQueryState =
    agentPerformanceQueryState?.filterKey === agentPerformanceFilterKey
      ? agentPerformanceQueryState
      : null
  const agentPerformanceStats = currentAgentPerformanceQueryState?.stats ?? null
  const agentPerformanceError = currentAgentPerformanceQueryState?.error ?? null
  const lifecycleStats =
    lifecycleQueryState?.filterKey === agentPerformanceFilterKey
      ? lifecycleQueryState.stats
      : null

  useEffect(() => {
    fetchCompletionStats()
      .then(setStats)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载完成度统计。"
        )
        setStats([])
      })
  }, [])

  useEffect(() => {
    fetchModelPricingSettings()
      .then((settings) => setModelPricing(settings.models))
      .catch((error: unknown) => {
        setModelPricingError(
          error instanceof Error ? error.message : "无法读取模型价格设置。"
        )
      })
  }, [])

  useEffect(() => {
    fetchRunHistoryTrend({ repoId: trendRepoId || undefined, days: trendDays })
      .then(setTrend)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载历史趋势。"
        )
      })
    fetchRecentRuns({ repoId: trendRepoId || undefined, limit: 30 })
      .then(setRecentRuns)
      .catch(() => setRecentRuns([]))
  }, [trendRepoId, trendDays])

  useEffect(() => {
    let isCancelled = false
    fetchAgentPerformanceStats({
      repoId: trendRepoId || undefined,
      days: trendDays,
    })
      .then((performanceStats) => {
        if (!isCancelled) {
          setAgentPerformanceQueryState({
            filterKey: agentPerformanceFilterKey,
            stats: performanceStats,
            error: null,
          })
        }
      })
      .catch((error: unknown) => {
        if (isCancelled) {
          return
        }
        setAgentPerformanceQueryState({
          filterKey: agentPerformanceFilterKey,
          stats: null,
          error:
            error instanceof Error
              ? error.message
              : "无法读取 Agent 执行表现。",
        })
      })
    return () => {
      isCancelled = true
    }
  }, [trendRepoId, trendDays, agentPerformanceFilterKey])

  useEffect(() => {
    let isCancelled = false
    fetchPrdLifecycleStats({
      repoId: trendRepoId || undefined,
      days: trendDays,
    })
      .then((stats) => {
        if (!isCancelled) {
          setLifecycleQueryState({
            filterKey: agentPerformanceFilterKey,
            stats,
          })
        }
      })
      .catch((error: unknown) => {
        if (isCancelled) {
          return
        }
        toast.error(
          error instanceof Error ? error.message : "无法加载 PRD 生命周期统计。"
        )
      })
    return () => {
      isCancelled = true
    }
  }, [trendRepoId, trendDays, agentPerformanceFilterKey])

  const trendMax = useMemo(
    () =>
      Math.max(
        1,
        ...trend.map((entry) => entry.completed + entry.failed + entry.blocked)
      ),
    [trend]
  )
  const overviewTotals = useMemo(() => {
    const repositoryStats = stats ?? []
    return {
      completed: repositoryStats.reduce(
        (sum, entry) => sum + entry.completed,
        0
      ),
      inProgress: repositoryStats.reduce(
        (sum, entry) => sum + entry.open_in_pipeline,
        0
      ),
      trackedRepositories: repositoryStats.length,
      recentRuns: trend.reduce(
        (sum, entry) => sum + entry.completed + entry.failed + entry.blocked,
        0
      ),
    }
  }, [stats, trend])
  const selectedRepositoryName =
    stats?.find((entry) => entry.repo_id === trendRepoId)?.display_name ??
    "全部仓库"

  return (
    <ConsolePage>
      <ConsolePageHeader title="统计与成本" description="一处查看 GitHub 处理进度、本地运行趋势、Agent 表现和按模型价格估算的 token 成本。" />
      <ConsoleToolbar aria-label="统计筛选">
            <NativeSelect
              className="h-8 min-w-36"
              value={trendRepoId}
              onChange={(event) => setTrendRepoId(event.target.value)}
              aria-label="统计仓库筛选"
            >
              <option value="">全部仓库</option>
              {(stats ?? []).map((entry) => (
                <option key={entry.repo_id} value={entry.repo_id}>
                  {entry.display_name}
                </option>
              ))}
            </NativeSelect>
            <NativeSelect
              className="h-8 min-w-28"
              value={trendDays}
              onChange={(event) => setTrendDays(Number(event.target.value))}
              aria-label="统计时间范围"
            >
              <option value={7}>近 7 天</option>
              <option value={30}>近 30 天</option>
              <option value={90}>近 90 天</option>
            </NativeSelect>
      </ConsoleToolbar>

      <StatsSectionTabs />

      <section
        id={STATS_SECTION_IDS.overview}
        role="tabpanel"
        aria-labelledby="stats-section-tab-overview"
        className="grid scroll-mt-20 gap-3 sm:grid-cols-2 xl:grid-cols-4"
        aria-label="运行指标"
      >
        <StatsMetric
          label="已跟踪仓库"
          value={String(overviewTotals.trackedRepositories)}
          detail="GitHub 完成度来源"
        />
        <StatsMetric
          label="已处理 Issue"
          value={String(overviewTotals.completed)}
          detail="所有已跟踪仓库"
          accent="emerald"
        />
        <StatsMetric
          label="进行中"
          value={String(overviewTotals.inProgress)}
          detail="仍处于 Agent 工作流"
          accent="sky"
        />
        <StatsMetric
          label={`近 ${trendDays} 天运行`}
          value={String(overviewTotals.recentRuns)}
          detail="来自本地运行记录"
          accent="violet"
        />
      </section>

      <Card className="gap-4">
        <CardHeader>
          <CardTitle className="text-sm">仓库完成度</CardTitle>
          <CardDescription>
            GitHub 实时状态；closed Issue 带有 agent workflow label
            时计为已处理。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {stats === null ? (
            <Skeleton className="h-24" />
          ) : stats.length === 0 ? (
            <p className="text-sm text-slate-500">暂无仓库统计。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-left text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="rounded-l-lg px-3 py-3 font-medium">仓库</th>
                    <th className="px-3 py-3 font-medium">完成率</th>
                    <th className="px-3 py-3 text-right font-medium">完成</th>
                    <th className="px-3 py-3 text-right font-medium">失败</th>
                    <th className="px-3 py-3 text-right font-medium">
                      Blocked
                    </th>
                    <th className="px-3 py-3 text-right font-medium">进行中</th>
                    <th className="rounded-r-lg px-3 py-3 text-right font-medium">
                      总计
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {stats.map((entry) => (
                    <tr
                      key={entry.repo_id}
                      className="border-b last:border-b-0"
                    >
                      <td className="px-3 py-3 font-medium">
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
                      <td className="px-3 py-3">
                        <CompletionRateBar rate={entry.completion_rate} />
                      </td>
                      <td className="px-3 py-3 text-right font-medium text-emerald-700 tabular-nums dark:text-emerald-400">
                        {entry.completed}
                      </td>
                      <td className="px-3 py-3 text-right text-red-700 tabular-nums dark:text-red-400">
                        {entry.failed}
                      </td>
                      <td className="px-3 py-3 text-right text-amber-700 tabular-nums dark:text-amber-400">
                        {entry.blocked}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums">
                        {entry.open_in_pipeline}
                      </td>
                      <td className="px-3 py-3 text-right font-medium tabular-nums">
                        {entry.total_tracked}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="gap-4">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm">运行趋势</CardTitle>
              <CardDescription className="mt-1">
                {selectedRepositoryName} · 近 {trendDays} 天 · CLI
                与面板运行记录
              </CardDescription>
            </div>
            <div
              className="flex flex-wrap gap-3 text-xs text-muted-foreground"
              aria-label="趋势图例"
            >
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-emerald-500" />
                完成
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-red-500" />
                失败
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-amber-500" />
                Blocked
              </span>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {trend.length === 0 ? (
            <p className="text-sm text-slate-500">
              所选范围内暂无运行记录。运行 <code>kc run</code> 或通过面板触发
              执行后，这里会出现按天聚合的成功/失败曲线。
            </p>
          ) : (
            <div className="flex items-end gap-1 overflow-x-auto rounded-xl bg-muted/20 px-3 pt-3 pb-2">
              {trend.map((entry) => {
                const total = entry.completed + entry.failed + entry.blocked
                return (
                  <div
                    key={entry.day}
                    className="flex min-w-6 flex-col items-center gap-1"
                    title={`${entry.day}：完成 ${entry.completed} / 失败 ${entry.failed} / blocked ${entry.blocked}`}
                  >
                    <div className="flex h-24 w-4 flex-col-reverse overflow-hidden rounded-full bg-slate-200/70 dark:bg-slate-800">
                      <div
                        className="w-full bg-emerald-500"
                        style={{
                          height: `${(entry.completed / trendMax) * 100}%`,
                        }}
                      />
                      <div
                        className="w-full bg-red-500"
                        style={{
                          height: `${(entry.failed / trendMax) * 100}%`,
                        }}
                      />
                      <div
                        className="w-full bg-amber-500"
                        style={{
                          height: `${(entry.blocked / trendMax) * 100}%`,
                        }}
                      />
                    </div>
                    <span className="text-[10px] text-muted-foreground">
                      {entry.day.slice(5)}
                    </span>
                    <span className="text-[10px] font-medium tabular-nums">
                      {total}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="gap-4">
        <CardHeader>
          <CardTitle className="text-sm">最近运行记录</CardTitle>
          <CardDescription>
            按仓库和 Issue 分组；展开后可查看每次运行的详情，包括成功、失败与
            Blocked。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {recentRuns.length === 0 ? (
            <p className="text-sm text-slate-500">暂无运行记录。</p>
          ) : (
            <RecentRunGroups
              recentRuns={recentRuns}
              onSelectRun={setSelectedRun}
            />
          )}
        </CardContent>
      </Card>

      <RunDetailDialog
        run={selectedRun}
        open={selectedRun !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedRun(null)
          }
        }}
      />

      <div
        id={STATS_SECTION_IDS.tokenCost}
        role="tabpanel"
        aria-labelledby="stats-section-tab-tokenCost"
        className="scroll-mt-20"
      >
        {lifecycleStats === null ? (
          <Card className="border-border/70 shadow-sm">
            <CardHeader>
              <CardTitle className="text-sm">Token 用量与费用</CardTitle>
              <CardDescription>正在读取调用明细与模型价格…</CardDescription>
            </CardHeader>
            <CardContent>
              <Skeleton className="h-36" />
            </CardContent>
          </Card>
        ) : (
          <TokenUsageSection
            stats={lifecycleStats}
            pricingModels={modelPricing}
            pricingError={modelPricingError}
          />
        )}
      </div>

      <div
        id={STATS_SECTION_IDS.agentPerformance}
        role="tabpanel"
        aria-labelledby="stats-section-tab-agentPerformance"
        className="scroll-mt-20"
      >
        <AgentPerformanceSection
          stats={agentPerformanceStats}
          error={agentPerformanceError}
        />
      </div>

      <div
        id={STATS_SECTION_IDS.prdExecution}
        role="tabpanel"
        aria-labelledby="stats-section-tab-prdExecution"
        className="scroll-mt-20"
      >
        <Card
          data-testid="stats-prd-lifecycle"
          className="gap-4"
        >
          <CardHeader>
            <CardTitle className="text-sm">PRD 执行分析</CardTitle>
            <CardDescription>
              以完整 PRD 生命周期为口径（非单次 runner
              调用），共用上方的仓库与时间范围筛选； 进行中记录与无法关联 PRD
              的旧记录不进入完成分位数。
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
                    value={formatLifecycleDuration(
                      lifecycleStats.average_end_to_end_seconds
                    )}
                  />
                  <LifecycleMetricTile
                    label="中位数"
                    value={formatLifecycleDuration(
                      lifecycleStats.median_end_to_end_seconds
                    )}
                  />
                  <LifecycleMetricTile
                    label="P90"
                    value={formatLifecycleDuration(
                      lifecycleStats.p90_end_to_end_seconds
                    )}
                  />
                  <LifecycleMetricTile
                    label="平均阻塞"
                    value={formatLifecycleDuration(
                      lifecycleStats.average_blocked_seconds
                    )}
                  />
                  <LifecycleMetricTile
                    label="阶段瓶颈"
                    value={formatBottleneck(lifecycleStats)}
                  />
                </div>

                {lifecycleStats.runs.length === 0 ? (
                  <p
                    className="text-sm text-slate-500"
                    data-testid="stats-prd-lifecycle-empty"
                  >
                    所选范围内暂无 PRD 生命周期记录。
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="bg-muted/40 text-xs text-muted-foreground">
                          <th className="rounded-l-lg px-3 py-3 font-medium">
                            PRD
                          </th>
                          <th className="px-3 py-3 font-medium">Issue</th>
                          <th className="px-3 py-3 font-medium">当前阶段</th>
                          <th className="px-3 py-3 font-medium">端到端</th>
                          <th className="px-3 py-3 font-medium">执行</th>
                          <th className="px-3 py-3 font-medium">等待</th>
                          <th className="px-3 py-3 font-medium">阻塞</th>
                          <th className="rounded-r-lg px-3 py-3 font-medium">
                            完整性
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {lifecycleStats.runs.map((run) => (
                          <tr
                            key={run.run_id}
                            className="border-b last:border-b-0"
                          >
                            <td
                              className="max-w-[280px] truncate px-3 py-3 font-medium"
                              title={run.prd_path}
                            >
                              {prdBasename(run.prd_path)}
                            </td>
                            <td className="px-3 py-3 font-mono text-xs">
                              {run.issue_number ? `#${run.issue_number}` : "—"}
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {run.in_progress
                                ? "进行中"
                                : (PHASE_LABELS[run.current_phase] ??
                                  run.current_phase)}
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {formatLifecycleDuration(
                                run.durations.end_to_end_seconds
                              )}
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {formatLifecycleDuration(
                                run.durations.active_seconds
                              )}
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {formatLifecycleDuration(
                                run.durations.waiting_seconds
                              )}
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {formatLifecycleDuration(
                                run.durations.blocked_seconds
                              )}
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {run.history_complete ? (
                                <span className="text-emerald-700 dark:text-emerald-400">
                                  完整
                                </span>
                              ) : (
                                <Badge
                                  variant="warning"
                                  className="text-[10px]"
                                >
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
                  className="rounded-lg bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
                  data-testid="stats-prd-lifecycle-disclosure"
                >
                  未关联 PRD 的旧记录 {lifecycleStats.unlinked_run_count}{" "}
                  条（已排除出分位数）； 观测不完整的 run{" "}
                  {lifecycleStats.incomplete_run_count} 条。
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </ConsolePage>
  )
}

/**
 * 把阶段瓶颈渲染为中文标签加耗时。
 *
 * @param stats - 生命周期统计（含可空的瓶颈阶段与耗时）。
 * @returns 中文阶段标签与耗时；无瓶颈时为 ``—``。
 */
function formatBottleneck(stats: PrdLifecycleStats): string {
  if (stats.bottleneck_phase === null) {
    return "—"
  }
  const label = PHASE_LABELS[stats.bottleneck_phase] ?? stats.bottleneck_phase
  if (stats.bottleneck_phase_seconds === null) {
    return label
  }
  return `${label} · ${formatLifecycleDuration(stats.bottleneck_phase_seconds)}`
}

/**
 * 取 PRD 仓库相对路径的文件名用于表格展示。
 *
 * @param prdPath - PRD 的仓库相对路径。
 * @returns 路径最后一段；路径为空时原样返回。
 */
function prdBasename(prdPath: string): string {
  const segments = prdPath.split("/")
  return segments[segments.length - 1] || prdPath
}

function LifecycleMetricTile({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="rounded-xl border bg-muted/20 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
    </div>
  )
}

/** 展示统计页顶部的一个关键指标。 */
function StatsMetric({
  label,
  value,
  detail,
  accent = "slate",
}: {
  label: string
  value: string
  detail: string
  accent?: "slate" | "emerald" | "sky" | "violet"
}) {
  const accentClass = {
    slate: "text-slate-950 dark:text-slate-50",
    emerald: "text-emerald-700 dark:text-emerald-400",
    sky: "text-sky-700 dark:text-sky-400",
    violet: "text-violet-700 dark:text-violet-400",
  }[accent]
  return (
    <div className="rounded-xl border border-border/70 bg-card px-4 py-3 shadow-sm">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p
        className={`mt-1 text-2xl font-semibold tracking-tight tabular-nums ${accentClass}`}
      >
        {value}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}

function CompletionRateBar({ rate }: { rate: number | null }) {
  if (rate === null) {
    return <span className="text-xs text-slate-400">—</span>
  }
  const percent = Math.round(rate * 100)
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
  )
}
