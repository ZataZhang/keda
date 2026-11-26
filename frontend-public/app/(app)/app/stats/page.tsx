"use client"

import { useEffect, useMemo, useState } from "react"

import { AgentPerformanceSection } from "@/components/stats/agent-performance-section"
import {
  IssueCostLedgerSection,
} from "@/components/stats/issue-cost-ledger-section"
import {
  ModelPricingOverviewSection,
  RepositoryStatusSection,
  UsageTrendSection,
  type IssueLedgerStatus,
} from "@/components/stats/issue-cost-views"
import { TokenUsageSection } from "@/components/stats/token-usage-section"
import {
  ConsolePage,
  ConsolePageHeader,
  ConsoleSectionTabs,
  ConsoleToolbar,
  consoleTabClassName,
} from "@/components/layout/console-page"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { NativeSelect } from "@/components/ui/native-select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  formatLifecycleDuration,
  PHASE_LABELS,
} from "@/components/backlog/prd-lifecycle-format"
import {
  fetchAgentPerformanceStats,
  fetchIssueCostStats,
  fetchModelPricingSettings,
  fetchPrdLifecycleStats,
} from "@/lib/api/console"
import type {
  AgentPerformanceStats,
  IssueCostStats,
  ModelTokenPricing,
  PrdLifecycleStats,
} from "@/lib/api/types"

type StatsViewKey = "issues" | "repositories" | "trend" | "pricing"

type DiagnosticQueryState = {
  filterKey: string
  agentStats: AgentPerformanceStats | null
  agentError: string | null
  lifecycleStats: PrdLifecycleStats | null
  lifecycleError: string | null
}

type IssueStatsQueryState = {
  filterKey: string
  stats: IssueCostStats | null
  error: string | null
  githubPending: boolean
}

const STATS_VIEWS: { key: StatsViewKey; label: string }[] = [
  { key: "issues", label: "按 Issue" },
  { key: "repositories", label: "仓库状态" },
  { key: "trend", label: "用量趋势" },
  { key: "pricing", label: "模型与计价" },
]

const STATS_SECTION_IDS = {
  agentPerformance: "stats-section-agent-performance",
  prdExecution: "stats-section-prd-execution",
} as const

/** 统计页默认显示 Issue 账本，辅助视图与既有诊断入口共用筛选状态。 */
export default function StatsPage() {
  const [activeView, setActiveView] = useState<StatsViewKey>("issues")
  const [repositoryId, setRepositoryId] = useState("")
  const [startDate, setStartDate] = useState(getDefaultStartDate)
  const [endDate, setEndDate] = useState(getUtcToday)
  const [issueStatsQueryState, setIssueStatsQueryState] = useState<IssueStatsQueryState | null>(null)
  const [modelPricing, setModelPricing] = useState<ModelTokenPricing[]>([])
  const [modelPricingError, setModelPricingError] = useState<string | null>(null)
  const [issueStatusFilter, setIssueStatusFilter] = useState<IssueLedgerStatus | "all">("all")
  const [diagnosticQueryState, setDiagnosticQueryState] = useState<DiagnosticQueryState | null>(null)

  const dateRangeError = useMemo(() => {
    if (!startDate || !endDate) return "请选择完整的开始与结束日期。"
    if (startDate > endDate) return "开始日期不能晚于结束日期。"
    if (differenceInUtcDays(startDate, endDate) >= 365) return "日期范围最多为 365 天。"
    return null
  }, [endDate, startDate])

  const issueStatsFilterKey = `${startDate}|${endDate}`
  const currentIssueStatsQueryState =
    issueStatsQueryState?.filterKey === issueStatsFilterKey
      ? issueStatsQueryState
      : null
  const issueStats = dateRangeError ? null : currentIssueStatsQueryState?.stats ?? null
  const issueStatsLoading = !dateRangeError && currentIssueStatsQueryState === null
  const issueStatsError = dateRangeError ?? currentIssueStatsQueryState?.error ?? null

  useEffect(() => {
    if (dateRangeError) return
    let isCancelled = false
    let hasFullResult = false
    const localLedger = fetchIssueCostStats({ startDate, endDate, includeGitHub: false })
      .then((stats) => {
        if (isCancelled || hasFullResult) return
        setIssueStatsQueryState({
          filterKey: issueStatsFilterKey,
          stats,
          error: null,
          githubPending: true,
        })
      })
      .catch(() => {
        // 完整请求仍可返回账本或给出错误。
      })

    fetchIssueCostStats({ startDate, endDate })
      .then((stats) => {
        if (isCancelled) return
        hasFullResult = true
        setIssueStatsQueryState({
          filterKey: issueStatsFilterKey,
          stats,
          error: null,
          githubPending: false,
        })
      })
      .catch(async (error: unknown) => {
        await localLedger
        if (isCancelled) return
        setIssueStatsQueryState((previous) => ({
          filterKey: issueStatsFilterKey,
          stats: previous?.filterKey === issueStatsFilterKey ? previous.stats : null,
          error: error instanceof Error ? error.message : "无法读取 Issue 账本。",
          githubPending: false,
        }))
      })
    return () => {
      isCancelled = true
    }
  }, [dateRangeError, endDate, issueStatsFilterKey, startDate])

  useEffect(() => {
    fetchModelPricingSettings()
      .then((settings) => setModelPricing(settings.models))
      .catch((error: unknown) => {
        setModelPricingError(error instanceof Error ? error.message : "无法读取模型价格设置。")
      })
  }, [])

  const diagnosticFilterKey = `${repositoryId}|${startDate}|${endDate}`
  useEffect(() => {
    if (dateRangeError) return
    let isCancelled = false
    Promise.allSettled([
      fetchAgentPerformanceStats({
        repoId: repositoryId || undefined,
        startDate,
        endDate,
      }),
      fetchPrdLifecycleStats({
        repoId: repositoryId || undefined,
        startDate,
        endDate,
      }),
    ]).then(([agentResult, lifecycleResult]) => {
      if (isCancelled) return
      setDiagnosticQueryState({
        filterKey: diagnosticFilterKey,
        agentStats: agentResult.status === "fulfilled" ? agentResult.value : null,
        agentError:
          agentResult.status === "rejected"
            ? errorMessage(agentResult.reason, "无法读取 Agent 执行表现。")
            : null,
        lifecycleStats:
          lifecycleResult.status === "fulfilled" ? lifecycleResult.value : null,
        lifecycleError:
          lifecycleResult.status === "rejected"
            ? errorMessage(lifecycleResult.reason, "无法加载 PRD 生命周期统计。")
            : null,
      })
    })
    return () => {
      isCancelled = true
    }
  }, [dateRangeError, diagnosticFilterKey, endDate, repositoryId, startDate])

  const selectedRepositories = useMemo(
    () =>
      (issueStats?.repositories ?? []).filter(
        (repository) => !repositoryId || repository.repo_id === repositoryId,
      ),
    [issueStats?.repositories, repositoryId],
  )
  const selectedIssueStats = useMemo(
    () =>
      issueStats
        ? { ...issueStats, repo_id: repositoryId || null, repositories: selectedRepositories, unlinked_invocation_count: repositoryId ? selectedRepositories.reduce((sum, repository) => sum + (repository.unlinked_invocation_count ?? 0), 0) : issueStats.unlinked_invocation_count }
        : null,
    [issueStats, repositoryId, selectedRepositories],
  )
  const selectedIssues = useMemo(
    () => selectedRepositories.flatMap((repository) => repository.issues),
    [selectedRepositories],
  )
  const selectedDiagnostics =
    diagnosticQueryState?.filterKey === diagnosticFilterKey
      ? diagnosticQueryState
      : null

  const chooseRepositoryStatus = (repoId: string, status: IssueLedgerStatus) => {
    setRepositoryId(repoId)
    setIssueStatusFilter(status)
    setActiveView("issues")
  }

  return (
    <ConsolePage>
      <ConsolePageHeader
        title="统计与成本"
        description="按 Issue 核对执行次数、Token 和本机估价；仓库状态、用量趋势与模型计价各有独立视图。"
      />

      <ConsoleToolbar aria-label="统计筛选">
        <NativeSelect
          className="h-8 min-w-36"
          value={repositoryId}
          onChange={(event) => setRepositoryId(event.target.value)}
          aria-label="统计仓库筛选"
          data-testid="stats-repository-filter"
        >
          <option value="">全部仓库</option>
          {(issueStats?.repositories ?? []).map((repository) => (
            <option key={repository.repo_id} value={repository.repo_id}>
              {repository.display_name}
            </option>
          ))}
        </NativeSelect>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          开始日期
          <input
            className="h-8 rounded-md border border-input bg-background px-2 text-sm text-foreground"
            type="date"
            value={startDate}
            onChange={(event) => setStartDate(event.target.value)}
            aria-label="统计开始日期"
            data-testid="stats-start-date"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          结束日期
          <input
            className="h-8 rounded-md border border-input bg-background px-2 text-sm text-foreground"
            type="date"
            value={endDate}
            onChange={(event) => setEndDate(event.target.value)}
            aria-label="统计结束日期"
            data-testid="stats-end-date"
          />
        </label>
        <span className="text-xs text-muted-foreground">按 UTC 日历日筛选本机账本</span>
      </ConsoleToolbar>

      <div className="flex flex-wrap items-end justify-between gap-3 border-b">
        <ConsoleSectionTabs role="tablist" aria-label="统计视图" className="static -mx-4 min-w-0 flex-1 border-0 px-4 pt-0 lg:-mx-6 lg:px-6">
          {STATS_VIEWS.map((view) => {
            const isActive = activeView === view.key
            return (
              <button
                key={view.key}
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-controls="stats-view-panel"
                tabIndex={isActive ? 0 : -1}
                onClick={() => setActiveView(view.key)}
                className={consoleTabClassName(isActive)}
                data-testid={`stats-view-tab-${view.key}`}
              >
                {view.label}
              </button>
            )
          })}
        </ConsoleSectionTabs>
        <div className="flex items-center gap-3 pb-2 text-xs text-muted-foreground">
          <a className="hover:text-foreground hover:underline" href={`#${STATS_SECTION_IDS.agentPerformance}`}>Agent 表现</a>
          <span aria-hidden="true">/</span>
          <a className="hover:text-foreground hover:underline" href={`#${STATS_SECTION_IDS.prdExecution}`}>PRD 执行分析</a>
        </div>
      </div>

      {dateRangeError ? <p className="text-sm text-red-700 dark:text-red-300" role="alert">{dateRangeError}</p> : null}
      <div id="stats-view-panel" role="tabpanel" aria-label={STATS_VIEWS.find((view) => view.key === activeView)?.label}>
        {activeView === "trend" ? (
          <UsageTrendSection
            stats={selectedIssueStats}
            pricingModels={modelPricing}
            onSelectDay={(day) => {
              setStartDate(day)
              setEndDate(day)
              setIssueStatusFilter("all")
            }}
          />
        ) : null}
        {currentIssueStatsQueryState?.githubPending ? (
          <p className="text-sm text-muted-foreground" role="status">
            已读取本机账本，正在补充 GitHub Issue 状态…
          </p>
        ) : issueStats && issueStatsError ? (
          <p className="text-sm text-amber-700 dark:text-amber-300" role="status">
            GitHub Issue 状态读取失败，当前显示本机账本。{issueStatsError}
          </p>
        ) : null}
        {activeView === "issues" || activeView === "trend" ? (
          <IssueCostLedgerSection
            issues={activeView === "trend" ? selectedIssues.filter((issue) => issue.period_run_count > 0) : selectedIssues}
            pricingModels={modelPricing}
            loading={issueStatsLoading}
            error={issueStats ? null : issueStatsError}
            statusFilter={issueStatusFilter}
            onStatusFilterChange={setIssueStatusFilter}
            truncatedRunHistory={Boolean(selectedIssueStats?.truncated_run_history)}
            unlinkedInvocationCount={selectedIssueStats?.unlinked_invocation_count ?? 0}
          />
        ) : null}
        {activeView === "repositories" ? (
          <RepositoryStatusSection stats={selectedIssueStats} onSelectStatus={chooseRepositoryStatus} />
        ) : null}
        {activeView === "pricing" ? (
          <div className="space-y-4">
            <ModelPricingOverviewSection stats={selectedIssueStats} pricingModels={modelPricing} pricingError={modelPricingError} />
            {selectedDiagnostics?.lifecycleStats ? (
              <TokenUsageSection
                stats={selectedDiagnostics.lifecycleStats}
                pricingModels={modelPricing}
                pricingError={modelPricingError}
              />
            ) : (
              <Card><CardHeader><CardTitle className="text-sm">用量分组明细</CardTitle><CardDescription>{selectedDiagnostics?.lifecycleError ?? "正在读取流程、Agent 与 PRD 分组…"}</CardDescription></CardHeader><CardContent><Skeleton className="h-24" /></CardContent></Card>
            )}
          </div>
        ) : null}
      </div>

      <section id={STATS_SECTION_IDS.agentPerformance} className="scroll-mt-20" aria-label="Agent 表现">
        <AgentPerformanceSection
          stats={selectedDiagnostics?.agentStats ?? null}
          error={selectedDiagnostics?.agentError ?? null}
        />
      </section>

      <section id={STATS_SECTION_IDS.prdExecution} className="scroll-mt-20">
        <Card data-testid="stats-prd-lifecycle" className="gap-4">
          <CardHeader>
            <CardTitle className="text-sm">PRD 执行分析</CardTitle>
            <CardDescription>
              以完整 PRD 生命周期为口径（非单次 runner 调用），使用上方相同的仓库与日期范围；进行中记录与无法关联 PRD 的旧记录不进入完成分位数。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {selectedDiagnostics?.lifecycleStats ? (
              <PrdLifecycleDetails stats={selectedDiagnostics.lifecycleStats} />
            ) : selectedDiagnostics?.lifecycleError ? (
              <p className="text-sm text-red-700 dark:text-red-300" role="alert">{selectedDiagnostics.lifecycleError}</p>
            ) : (
              <Skeleton className="h-24" />
            )}
          </CardContent>
        </Card>
      </section>
    </ConsolePage>
  )
}

/** 展示现有 PRD 生命周期分析明细。 */
function PrdLifecycleDetails({ stats }: { stats: PrdLifecycleStats }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="stats-prd-lifecycle-metrics">
        <LifecycleMetricTile label="已完成 PRD 数" value={String(stats.completed_runs)} />
        <LifecycleMetricTile label="平均端到端" value={formatLifecycleDuration(stats.average_end_to_end_seconds)} />
        <LifecycleMetricTile label="中位数" value={formatLifecycleDuration(stats.median_end_to_end_seconds)} />
        <LifecycleMetricTile label="P90" value={formatLifecycleDuration(stats.p90_end_to_end_seconds)} />
        <LifecycleMetricTile label="平均阻塞" value={formatLifecycleDuration(stats.average_blocked_seconds)} />
        <LifecycleMetricTile label="阶段瓶颈" value={formatBottleneck(stats)} />
      </div>
      {stats.runs.length === 0 ? (
        <p className="text-sm text-slate-500" data-testid="stats-prd-lifecycle-empty">所选范围内暂无 PRD 生命周期记录。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="bg-muted/40 text-xs text-muted-foreground"><th className="rounded-l-lg px-3 py-3 font-medium">PRD</th><th className="px-3 py-3 font-medium">Issue</th><th className="px-3 py-3 font-medium">当前阶段</th><th className="px-3 py-3 font-medium">端到端</th><th className="px-3 py-3 font-medium">执行</th><th className="px-3 py-3 font-medium">等待</th><th className="px-3 py-3 font-medium">阻塞</th><th className="rounded-r-lg px-3 py-3 font-medium">完整性</th></tr></thead>
            <tbody>
              {stats.runs.map((run) => (
                <tr key={run.run_id} className="border-b last:border-b-0">
                  <td className="max-w-[280px] truncate px-3 py-3 font-medium" title={run.prd_path}>{prdBasename(run.prd_path)}</td>
                  <td className="px-3 py-3 font-mono text-xs">{run.issue_number ? `#${run.issue_number}` : "—"}</td>
                  <td className="px-3 py-3 text-xs">{run.in_progress ? "进行中" : (PHASE_LABELS[run.current_phase] ?? run.current_phase)}</td>
                  <td className="px-3 py-3 text-xs">{formatLifecycleDuration(run.durations.end_to_end_seconds)}</td>
                  <td className="px-3 py-3 text-xs">{formatLifecycleDuration(run.durations.active_seconds)}</td>
                  <td className="px-3 py-3 text-xs">{formatLifecycleDuration(run.durations.waiting_seconds)}</td>
                  <td className="px-3 py-3 text-xs">{formatLifecycleDuration(run.durations.blocked_seconds)}</td>
                  <td className="px-3 py-3 text-xs">{run.history_complete ? <span className="text-emerald-700 dark:text-emerald-400">完整</span> : <Badge variant="warning" className="text-[10px]">不完整</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="rounded-lg bg-muted/30 px-3 py-2 text-xs text-muted-foreground" data-testid="stats-prd-lifecycle-disclosure">
        未关联 PRD 的旧记录 {stats.unlinked_run_count} 条（已排除出分位数）；观测不完整的 run {stats.incomplete_run_count} 条。
      </p>
    </div>
  )
}

/** 返回当前 UTC 日期，作为日期范围的默认结束值。 */
function getUtcToday(): string {
  return new Date().toISOString().slice(0, 10)
}

/** 返回默认三十天区间的 UTC 起始日期。 */
function getDefaultStartDate(): string {
  const utcStartDate = new Date()
  utcStartDate.setUTCDate(utcStartDate.getUTCDate() - 29)
  return utcStartDate.toISOString().slice(0, 10)
}

/** 计算两个 ISO 日期之间相差的 UTC 日历天数。 */
function differenceInUtcDays(startDate: string, endDate: string): number {
  const startMilliseconds = Date.parse(`${startDate}T00:00:00Z`)
  const endMilliseconds = Date.parse(`${endDate}T00:00:00Z`)
  return Math.floor((endMilliseconds - startMilliseconds) / 86_400_000)
}

/** 读取请求错误消息；未知错误使用调用方提供的文案。 */
function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) return error.message
  return fallback
}

/** 格式化当前瓶颈阶段及其持续时间。 */
function formatBottleneck(stats: PrdLifecycleStats): string {
  if (stats.bottleneck_phase === null) return "—"
  const label = PHASE_LABELS[stats.bottleneck_phase] ?? stats.bottleneck_phase
  if (stats.bottleneck_phase_seconds === null) return label
  return `${label} · ${formatLifecycleDuration(stats.bottleneck_phase_seconds)}`
}

/** 从仓库相对路径提取 PRD 文件名。 */
function prdBasename(prdPath: string): string {
  const segments = prdPath.split("/")
  return segments[segments.length - 1] || prdPath
}

/** 展示 PRD 生命周期统计页的单项指标。 */
function LifecycleMetricTile({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border bg-muted/20 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold tabular-nums">{value}</p></div>
}
