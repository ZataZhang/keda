"use client"

import Link from "next/link"
import { useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ModelUsageTable } from "@/components/stats/issue-cost-ledger-section"
import { formatTokenCount } from "@/components/backlog/prd-lifecycle-format"
import { estimateModelUsageCost, formatUsdCost } from "@/lib/model-pricing"
import { addTokenUsageTotals } from "@/lib/token-usage"
import type { IssueCostStats, IssueCostStatsIssue, ModelTokenPricing, TokenUsageTotals } from "@/lib/api/types"

export type IssueLedgerStatus = "blocked" | "failed" | "in_progress" | "completed" | "unknown"

const REPOSITORY_STATUS_LABELS: Record<Exclude<IssueLedgerStatus, "unknown">, string> = {
  blocked: "阻塞",
  failed: "失败",
  in_progress: "进行中",
  completed: "已完成",
}

/** 展示 GitHub 当前状态，并允许直接打开该状态对应的 Issue 集合。 */
export function RepositoryStatusSection({
  stats,
  onSelectStatus,
}: {
  stats: IssueCostStats | null
  onSelectStatus: (repoId: string, status: IssueLedgerStatus) => void
}) {
  if (!stats) return <LoadingCard title="仓库状态" />
  return (
    <Card className="gap-4" data-testid="stats-repository-status">
      <CardHeader>
        <CardTitle className="text-sm">GitHub 当前状态</CardTitle>
        <CardDescription>不受上方本地运行日期范围影响。点击状态数量可打开对应 Issue 集合；期间无运行的 Issue 仍会显示。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {stats.repositories.length === 0 ? <p className="text-sm text-muted-foreground">暂无已启用仓库。</p> : null}
        {stats.repositories.map((repository) => (
          <div key={repository.repo_id} className="rounded-lg border" data-testid={`stats-repository-status-${repository.repo_id}`}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
              <p className="text-sm font-medium">{repository.display_name}</p>
              <span className="text-xs text-muted-foreground">{repository.issue_count} 个跟踪 Issue</span>
            </div>
            {repository.github_error ? <p className="px-3 py-2 text-sm text-amber-800 dark:text-amber-300" role="status">GitHub 状态未知：{repository.github_error}</p> : null}
            <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-4">
              {(Object.keys(REPOSITORY_STATUS_LABELS) as Exclude<IssueLedgerStatus, "unknown">[]).map((status) => (
                <button
                  key={status}
                  type="button"
                  className="rounded-md border px-3 py-2 text-left hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => onSelectStatus(repository.repo_id, status)}
                  aria-label={`查看 ${repository.display_name} ${REPOSITORY_STATUS_LABELS[status]} Issue ${repository.status_counts[status]} 个`}
                  data-testid={`stats-repository-status-link-${repository.repo_id}-${status}`}
                >
                  <span className="block text-xs text-muted-foreground">{REPOSITORY_STATUS_LABELS[status]}</span>
                  <span className="mt-1 block text-lg font-semibold tabular-nums">{repository.status_counts[status]}</span>
                </button>
              ))}
            </div>
            {repository.github_truncated ? <p className="px-3 pb-3 text-xs text-amber-800 dark:text-amber-300">GitHub 返回结果达到查询上限，状态集合可能不完整。</p> : null}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

/** 根据真实执行开始日展示期间用量趋势。 */
export function UsageTrendSection({
  stats,
  pricingModels,
}: {
  stats: IssueCostStats | null
  pricingModels: ModelTokenPricing[]
}) {
  const [measure, setMeasure] = useState<"cost" | "tokens">("cost")
  const dailyTotals = useMemo(() => buildDailyIssueTotals(stats?.repositories.flatMap((repository) => repository.issues) ?? [], pricingModels), [stats, pricingModels])
  const maximumValue = Math.max(1, ...dailyTotals.map((dailyTotal) => measure === "cost" ? dailyTotal.costUsd : dailyTotal.totalTokens))
  if (!stats) return <LoadingCard title="用量趋势" />
  return (
    <Card className="gap-4" data-testid="stats-usage-trend">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm">用量趋势</CardTitle>
            <CardDescription>{stats.start_date} 至 {stats.end_date} · 按执行开始日统计，日期边界使用 UTC。</CardDescription>
          </div>
          <div className="flex gap-1" role="group" aria-label="趋势指标">
            <Button size="sm" variant={measure === "cost" ? "secondary" : "ghost"} onClick={() => setMeasure("cost")} aria-pressed={measure === "cost"} data-testid="stats-trend-measure-cost">费用</Button>
            <Button size="sm" variant={measure === "tokens" ? "secondary" : "ghost"} onClick={() => setMeasure("tokens")} aria-pressed={measure === "tokens"} data-testid="stats-trend-measure-tokens">Token</Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {dailyTotals.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">所选期间暂无执行记录。</p>
        ) : (
          <div className="flex min-h-48 items-end gap-2 overflow-x-auto rounded-lg bg-muted/20 px-3 pt-4 pb-3" data-testid="stats-trend-chart">
            {dailyTotals.map((dailyTotal) => {
              const currentValue = measure === "cost" ? dailyTotal.costUsd : dailyTotal.totalTokens
              const height = currentValue === 0 ? 2 : Math.max(4, (currentValue / maximumValue) * 100)
              const label = measure === "cost" ? formatDailyCost(dailyTotal) : formatTokenCount(dailyTotal.totalTokens)
              return (
                <div key={dailyTotal.day} className="flex min-w-12 flex-col items-center gap-1" title={`${dailyTotal.day} · ${label} · ${dailyTotal.runCount} 次执行`} data-testid="stats-trend-day">
                  <span className="text-[10px] leading-4 font-medium tabular-nums">{label}</span>
                  <div className="flex h-24 w-7 items-end overflow-hidden rounded-t bg-slate-200/70 dark:bg-slate-800">
                    {measure === "tokens" || dailyTotal.pricedTokenCount > 0 ? <div className="w-full rounded-t bg-primary" style={{ height: `${height}%` }} /> : null}
                  </div>
                  <span className="text-[10px] leading-4 text-muted-foreground">{dailyTotal.day.slice(5)}</span>
                </div>
              )
            })}
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">所选日期区间同时应用于按 Issue 账本；未计价模型与 usage 缺失调用不会被假设为免费。</p>
      </CardContent>
    </Card>
  )
}

/** 展示当前价格配置下的模型估价与 Token 计价覆盖率。 */
export function ModelPricingOverviewSection({
  stats,
  pricingModels,
  pricingError,
}: {
  stats: IssueCostStats | null
  pricingModels: ModelTokenPricing[]
  pricingError: string | null
}) {
  if (!stats) return <LoadingCard title="模型与计价" />
  const issues = stats.repositories.flatMap((repository) => repository.issues)
  const modelUsage = combineModelUsage(issues)
  const allModelTotals = Object.fromEntries(modelUsage.map((modelRow) => [modelRow.model_id, modelRow.totals]))
  const estimate = estimateModelUsageCost(allModelTotals, pricingModels)
  const missingUsageCount = issues.reduce((sum, issue) => sum + issue.missing_usage_count, 0)
  const coveragePercent = estimate.tokenCount > 0 ? Math.round((estimate.pricedTokenCount / estimate.tokenCount) * 100) : null
  return (
    <Card className="gap-4" data-testid="stats-model-pricing">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm">模型与计价</CardTitle>
            <CardDescription>按所选日期区间的调用账本汇总；估价使用本机当前价格，不代表供应商账单。</CardDescription>
          </div>
          <Button asChild size="sm" variant="outline"><Link href="/app/settings/">设置模型价格</Link></Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {pricingError ? <p className="rounded-lg border border-amber-200 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:text-amber-300" role="alert">模型价格设置读取失败：{pricingError}。当前费用结果可能未计价。</p> : null}
        <div className="grid gap-3 sm:grid-cols-3">
          <MetricTile label="已计价金额" value={estimate.usageCount === 0 ? "暂无估价" : estimate.tokenCount === 0 ? "用量上报为 0" : estimate.unpricedModelIds.length > 0 && estimate.pricedTokenCount === 0 ? "暂无估价" : `${formatUsdCost(estimate.costUsd)}${estimate.unpricedModelIds.length > 0 || missingUsageCount > 0 ? " · 部分计价" : ""}`} detail={estimate.unpricedModelIds.length > 0 ? `${estimate.unpricedModelIds.length} 个模型缺少价格` : missingUsageCount > 0 ? "另有调用未报告 usage" : "基于本机当前价格配置"} />
          <MetricTile label="Token 计价覆盖率" value={coveragePercent === null ? "—" : `${coveragePercent}%`} detail={`${formatTokenCount(estimate.pricedTokenCount)} / ${formatTokenCount(estimate.tokenCount)} 已配置价格`} />
          <MetricTile label="缺少 usage 的调用" value={String(missingUsageCount)} detail="未计入 Token 或美元估价" />
        </div>
        {estimate.tokenCount > 0 ? <div className="h-2 overflow-hidden rounded-full bg-muted" aria-label={`Token 计价覆盖率 ${coveragePercent}%`}><div className="h-full rounded-full bg-primary" style={{ width: `${coveragePercent ?? 0}%` }} /></div> : null}
        {modelUsage.length === 0 ? (
          <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">所选期间没有可关联到模型的用量。</p>
        ) : (
          <div className="space-y-2" data-testid="stats-model-pricing-table">
            <h3 className="text-sm font-semibold">按模型</h3>
            <ModelUsageTable modelUsage={modelUsage} pricingModels={pricingModels} emptyLabel="当前没有模型用量。" />
          </div>
        )}
        <p className="text-xs text-muted-foreground">覆盖率分母为账本中全部已记录 Token，未识别模型也计入分母。缺少 usage 的调用不计入 Token 分母；已知模型价格为 $0 时会明确显示 $0。</p>
      </CardContent>
    </Card>
  )
}

/** 按执行开始日合并 Issue 账本中的 Token 与可计价金额。 */
function buildDailyIssueTotals(issues: IssueCostStatsIssue[], pricingModels: ModelTokenPricing[]) {
  const dailyBuckets = new Map<string, { totalTokens: number; costUsd: number; pricedTokenCount: number; usageCount: number; missingUsageCount: number; runCount: number }>()
  for (const issue of issues) {
    for (const execution of issue.executions) {
      const day = execution.started_at.slice(0, 10)
      const currentBucket = dailyBuckets.get(day) ?? { totalTokens: 0, costUsd: 0, pricedTokenCount: 0, usageCount: 0, missingUsageCount: 0, runCount: 0 }
      const executionUsage = Object.fromEntries(execution.model_usage.map((modelRow) => [modelRow.model_id, modelRow.totals]))
      const executionEstimate = estimateModelUsageCost(executionUsage, pricingModels)
      currentBucket.totalTokens += executionEstimate.tokenCount
      currentBucket.costUsd += executionEstimate.costUsd
      currentBucket.pricedTokenCount += executionEstimate.pricedTokenCount
      currentBucket.usageCount += executionEstimate.usageCount
      currentBucket.missingUsageCount += execution.missing_usage_count
      currentBucket.runCount += 1
      dailyBuckets.set(day, currentBucket)
    }
  }
  return [...dailyBuckets.entries()]
    .map(([day, totals]) => ({ day, ...totals }))
    .sort((leftEntry, rightEntry) => leftEntry.day.localeCompare(rightEntry.day))
}

/** 格式化每日估价，同时保留部分计价提示。 */
function formatDailyCost(dailyTotal: ReturnType<typeof buildDailyIssueTotals>[number]): string {
  if (dailyTotal.usageCount === 0 || dailyTotal.pricedTokenCount === 0) return "暂无估价"
  const isPartial = dailyTotal.pricedTokenCount < dailyTotal.totalTokens || dailyTotal.missingUsageCount > 0
  return `${formatUsdCost(dailyTotal.costUsd)}${isPartial ? " · 部分" : ""}`
}

/** 合并所选 Issue 的模型用量，作为覆盖率分母及模型表来源。 */
function combineModelUsage(issues: IssueCostStatsIssue[]): IssueCostStatsIssue["model_usage"] {
  const modelBuckets = new Map<string, { totals: TokenUsageTotals; configurationFallbackCount: number; unresolvedAliasCount: number }>()
  for (const issue of issues) {
    for (const modelUsage of issue.model_usage) {
      const currentBucket = modelBuckets.get(modelUsage.model_id) ?? {
        totals: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, total_tokens: 0, usage_count: 0 },
        configurationFallbackCount: 0,
        unresolvedAliasCount: 0,
      }
      modelBuckets.set(modelUsage.model_id, {
        totals: addTokenUsageTotals(currentBucket.totals, modelUsage.totals),
        configurationFallbackCount: currentBucket.configurationFallbackCount + modelUsage.configuration_fallback_count,
        unresolvedAliasCount: currentBucket.unresolvedAliasCount + modelUsage.unresolved_model_alias_count,
      })
    }
  }
  return [...modelBuckets.entries()]
    .map(([model_id, modelBucket]) => ({
      model_id,
      totals: modelBucket.totals,
      configuration_fallback_count: modelBucket.configurationFallbackCount,
      unresolved_model_alias_count: modelBucket.unresolvedAliasCount,
    }))
    .sort((leftModel, rightModel) => rightModel.totals.total_tokens - leftModel.totals.total_tokens || leftModel.model_id.localeCompare(rightModel.model_id))
}

/** 展示模型计价页中的一项汇总指标。 */
function MetricTile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="rounded-xl border bg-muted/20 px-4 py-3"><p className="text-xs font-medium text-muted-foreground">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-xs text-muted-foreground">{detail}</p></div>
}

/** 展示统计请求尚未完成时的占位卡片。 */
function LoadingCard({ title }: { title: string }) {
  return <Card><CardHeader><CardTitle className="text-sm">{title}</CardTitle><CardDescription>正在读取统计数据…</CardDescription></CardHeader><CardContent><div className="h-24 animate-pulse rounded-lg bg-muted/40" /></CardContent></Card>
}
