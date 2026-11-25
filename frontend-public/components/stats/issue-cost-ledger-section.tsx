"use client"

import { useMemo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { formatTokenCount } from "@/components/backlog/prd-lifecycle-format"
import { estimateModelUsageCost, formatUsdCost } from "@/lib/model-pricing"
import { handleExternalLinkClick } from "@/lib/open-external"
import { sumTokenUsageTotals } from "@/lib/token-usage"
import { formatLocalDateTime } from "@/lib/utils"
import type { IssueCostStatsIssue, ModelTokenPricing } from "@/lib/api/types"

type IssueCostSortKey = "recent" | "cost" | "tokens" | "runs"
type IssueStatusFilter = "all" | "blocked" | "failed" | "in_progress" | "completed" | "unknown"

const ISSUE_STATUS_LABELS: Record<IssueStatusFilter, string> = {
  all: "全部状态",
  blocked: "阻塞",
  failed: "失败",
  in_progress: "进行中",
  completed: "已完成",
  unknown: "未知",
}

const ISSUE_STATUS_BADGES: Record<Exclude<IssueStatusFilter, "all">, "blocked" | "failed" | "running" | "ready" | "default"> = {
  blocked: "blocked",
  failed: "failed",
  in_progress: "running",
  completed: "ready",
  unknown: "default",
}

/**
 * 展示按仓库与 Issue 汇总的本机执行、Token 与估算费用。
 *
 * @param props.issues - 当前仓库状态与期间账本合并后的 Issue 行。
 * @param props.pricingModels - 本机当前保存的模型价格。
 * @param props.loading - 账本请求是否正在执行。
 * @param props.error - 账本读取失败时的摘要。
 * @param props.statusFilter - 由页面状态视图跳转传入的状态筛选。
 * @param props.onStatusFilterChange - 状态筛选变更回调。
 * @param props.truncatedRunHistory - 本地运行历史是否触达读取上限。
 * @param props.unlinkedInvocationCount - 当前期间无法关联到执行的调用数。
 * @returns 可搜索、排序、筛选并行内展开的 Issue 账本。
 */
export function IssueCostLedgerSection({
  issues,
  pricingModels,
  loading,
  error,
  statusFilter,
  onStatusFilterChange,
  truncatedRunHistory,
  unlinkedInvocationCount,
}: {
  issues: IssueCostStatsIssue[]
  pricingModels: ModelTokenPricing[]
  loading: boolean
  error: string | null
  statusFilter: IssueStatusFilter
  onStatusFilterChange: (filter: IssueStatusFilter) => void
  truncatedRunHistory: boolean
  unlinkedInvocationCount: number
}) {
  const [searchQuery, setSearchQuery] = useState("")
  const [sortKey, setSortKey] = useState<IssueCostSortKey>("recent")
  const [expandedIssueKey, setExpandedIssueKey] = useState<string | null>(null)
  const [expandedPanel, setExpandedPanel] = useState<"executions" | "models">("executions")

  const visibleIssues = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLocaleLowerCase()
    return issues
      .filter((issue) => {
        const matchesSearch =
          normalizedSearch.length === 0 ||
          `${issue.issue_number} ${issue.title} ${issue.repository_name} ${issue.repo_id}`
            .toLocaleLowerCase()
            .includes(normalizedSearch)
        const matchesStatus =
          statusFilter === "all" || issue.status_filters.includes(statusFilter)
        return matchesSearch && matchesStatus
      })
      .sort((leftIssue, rightIssue) => {
        if (sortKey === "cost") {
          const leftCost = getIssueCost(leftIssue, pricingModels)
          const rightCost = getIssueCost(rightIssue, pricingModels)
          return rightCost - leftCost || compareRecentExecution(leftIssue, rightIssue)
        }
        if (sortKey === "tokens") {
          return rightIssue.totals.total_tokens - leftIssue.totals.total_tokens ||
            compareRecentExecution(leftIssue, rightIssue)
        }
        if (sortKey === "runs") {
          return rightIssue.period_run_count - leftIssue.period_run_count ||
            compareRecentExecution(leftIssue, rightIssue)
        }
        return compareRecentExecution(leftIssue, rightIssue)
      })
  }, [issues, pricingModels, searchQuery, sortKey, statusFilter])

  return (
    <Card className="gap-4" data-testid="stats-issue-ledger">
      <CardHeader>
        <CardTitle className="text-sm">按 Issue 汇总</CardTitle>
        <CardDescription>
          当前状态来自 GitHub；执行次数与调用用量来自本机账本。未能关联到模型或价格的部分不会估成 $0。
        </CardDescription>
        <div className="flex flex-wrap gap-2 pt-1">
          <Input
            className="h-8 min-w-48 flex-1 text-sm sm:max-w-sm"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="搜索 Issue 标题或编号"
            aria-label="搜索 Issue 标题或编号"
          />
          <NativeSelect
            className="h-8 min-w-32"
            value={statusFilter}
            onChange={(event) => onStatusFilterChange(event.target.value as IssueStatusFilter)}
            aria-label="Issue 状态筛选"
          >
            {Object.entries(ISSUE_STATUS_LABELS).map(([statusKey, statusLabel]) => (
              <option key={statusKey} value={statusKey}>{statusLabel}</option>
            ))}
          </NativeSelect>
          <NativeSelect
            className="h-8 min-w-36"
            value={sortKey}
            onChange={(event) => setSortKey(event.target.value as IssueCostSortKey)}
            aria-label="Issue 排序方式"
          >
            <option value="recent">最近执行</option>
            <option value="cost">费用从高到低</option>
            <option value="tokens">Token 从高到低</option>
            <option value="runs">执行次数从多到少</option>
          </NativeSelect>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {unlinkedInvocationCount > 0 || truncatedRunHistory ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200" role="status" data-testid="stats-issue-coverage-warning">
            {unlinkedInvocationCount > 0 ? (
              <p>{unlinkedInvocationCount} 条调用记录无法关联到期间内的执行，未分摊到 Issue。</p>
            ) : null}
            {truncatedRunHistory ? <p>运行历史达到读取上限，当前账本可能不完整。</p> : null}
          </div>
        ) : null}
        {error ? <p className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:text-red-300" role="alert">Issue 账本读取失败：{error}</p> : null}
        {loading && issues.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">正在读取 Issue 账本…</p>
        ) : visibleIssues.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground" data-testid="stats-issue-ledger-empty">
            {issues.length === 0 ? "当前仓库没有可显示的 workflow Issue 或期间运行记录。" : "没有符合搜索和状态条件的 Issue。"}
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="w-9 px-2 py-2" aria-label="展开" />
                  <th className="px-3 py-2 font-medium">仓库</th>
                  <th className="px-3 py-2 font-medium">Issue</th>
                  <th className="px-3 py-2 font-medium">标题</th>
                  <th className="px-3 py-2 font-medium">当前状态</th>
                  <th className="px-3 py-2 text-right font-medium">执行次数</th>
                  <th className="px-3 py-2 text-right font-medium">Token 总量</th>
                  <th className="px-3 py-2 text-right font-medium">估算费用（USD）</th>
                </tr>
              </thead>
              <tbody>
                {visibleIssues.map((issue) => {
                  const issueKey = `${issue.repo_id}#${issue.issue_number}`
                  const isExpanded = expandedIssueKey === issueKey
                  return (
                    <IssueLedgerRows
                      key={issueKey}
                      issue={issue}
                      isExpanded={isExpanded}
                      expandedPanel={expandedPanel}
                      pricingModels={pricingModels}
                      onToggle={() => {
                        setExpandedIssueKey(isExpanded ? null : issueKey)
                        setExpandedPanel("executions")
                      }}
                      onPanelChange={setExpandedPanel}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** 绘制一条 Issue 汇总行及其可展开的执行、模型详情。 */
function IssueLedgerRows({
  issue,
  isExpanded,
  expandedPanel,
  pricingModels,
  onToggle,
  onPanelChange,
}: {
  issue: IssueCostStatsIssue
  isExpanded: boolean
  expandedPanel: "executions" | "models"
  pricingModels: ModelTokenPricing[]
  onToggle: () => void
  onPanelChange: (panel: "executions" | "models") => void
}) {
  const status = issue.current_status as Exclude<IssueStatusFilter, "all">
  return (
    <>
      <tr className="border-t" data-testid={`stats-issue-row-${issue.repo_id}-${issue.issue_number}`}>
        <td className="px-2 py-2">
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={`${isExpanded ? "收起" : "展开"} ${issue.repo_id} Issue #${issue.issue_number}`}
            aria-expanded={isExpanded}
            aria-controls={`stats-issue-expansion-${issue.repo_id}-${issue.issue_number}`}
            onClick={onToggle}
            data-testid={`stats-issue-expand-${issue.repo_id}-${issue.issue_number}`}
          >
            {isExpanded ? <ChevronDown aria-hidden="true" size={14} /> : <ChevronRight aria-hidden="true" size={14} />}
          </Button>
        </td>
        <td className="px-3 py-2 font-medium">{issue.repository_name}</td>
        <td className="px-3 py-2 font-mono text-xs">
          {issue.issue_url ? (
            <a href={issue.issue_url} target="_blank" rel="noreferrer" onClick={handleExternalLinkClick} className="text-sky-700 hover:underline dark:text-sky-300">
              #{issue.issue_number}
            </a>
          ) : `#${issue.issue_number}`}
        </td>
        <td className="max-w-[300px] truncate px-3 py-2" title={issue.title || undefined}>{issue.title || "—"}</td>
        <td className="px-3 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={ISSUE_STATUS_BADGES[status] ?? "default"}>{ISSUE_STATUS_LABELS[status] ?? "未知"}</Badge>
            {!issue.period_data_available ? <span className="text-[11px] leading-4 text-muted-foreground">无期间数据</span> : null}
          </div>
        </td>
        <td className="px-3 py-2 text-right tabular-nums">{issue.period_run_count}</td>
        <td className="px-3 py-2 text-right tabular-nums">{formatTokenCount(issue.totals.total_tokens)}</td>
        <td className="px-3 py-2 text-right font-medium tabular-nums">{formatIssueCost(issue, pricingModels)}</td>
      </tr>
      {isExpanded ? (
        <tr className="border-t bg-muted/10">
          <td colSpan={8} className="px-4 py-3">
            <div id={`stats-issue-expansion-${issue.repo_id}-${issue.issue_number}`} data-testid={`stats-issue-expansion-${issue.repo_id}-${issue.issue_number}`}>
              <div className="mb-3 flex gap-1 border-b" role="tablist" aria-label={`${issue.repo_id} Issue #${issue.issue_number} 明细`}>
                <button type="button" role="tab" aria-selected={expandedPanel === "executions"} className={detailTabClass(expandedPanel === "executions")} onClick={() => onPanelChange("executions")} data-testid="stats-issue-detail-tab-executions">执行记录</button>
                <button type="button" role="tab" aria-selected={expandedPanel === "models"} className={detailTabClass(expandedPanel === "models")} onClick={() => onPanelChange("models")} data-testid="stats-issue-detail-tab-models">模型用量</button>
              </div>
              {expandedPanel === "executions" ? (
                <ExecutionUsageTable issue={issue} pricingModels={pricingModels} />
              ) : (
                <ModelUsageTable modelUsage={issue.model_usage} pricingModels={pricingModels} emptyLabel="当前 Issue 没有可关联的模型用量；无法关联的记录不会分摊到 Issue。" />
              )}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}

/** 展示 Issue 期间内的执行结果、Token、估价与缺失 usage。 */
function ExecutionUsageTable({ issue, pricingModels }: { issue: IssueCostStatsIssue; pricingModels: ModelTokenPricing[] }) {
  if (issue.executions.length === 0) {
    return <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">该 Issue 在所选期间没有本机运行记录。</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[620px] text-left text-sm" data-testid="stats-issue-executions">
        <thead className="bg-muted/30 text-xs text-muted-foreground"><tr><th className="px-3 py-2 font-medium">执行</th><th className="px-3 py-2 font-medium">结果</th><th className="px-3 py-2 font-medium">开始时间</th><th className="px-3 py-2 text-right font-medium">Token</th><th className="px-3 py-2 text-right font-medium">费用（USD）</th></tr></thead>
        <tbody>
          {issue.executions.map((execution, index) => {
            const executionTotals = sumTokenUsageTotals(execution.model_usage.map((modelUsage) => modelUsage.totals))
            const executionEstimate = estimateModelUsageCost(Object.fromEntries(execution.model_usage.map((modelUsage) => [modelUsage.model_id, modelUsage.totals])), pricingModels)
            const outcomeLabel = execution.outcome === "completed" ? "已完成" : execution.outcome === "failed" ? "失败" : execution.outcome === "blocked" ? "阻塞" : execution.outcome
            const outcomeVariant = execution.outcome === "completed" ? "ready" : execution.outcome === "failed" ? "failed" : execution.outcome === "blocked" ? "blocked" : "default"
            const costLabel = (execution.missing_usage_count > 0 || issue.truncated) && executionEstimate.usageCount > 0
              ? `${formatUsdCost(executionEstimate.costUsd)} · 部分计价`
              : formatEstimate(executionEstimate)
            return (
              <tr key={execution.execution_id} className="border-t" data-testid="stats-issue-execution-row">
                <td className="px-3 py-2.5 font-mono text-xs">#{index + 1}<span className="ml-2 text-muted-foreground">{execution.agent}</span></td>
                <td className="px-3 py-2.5"><Badge variant={outcomeVariant}>{outcomeLabel}</Badge>{execution.error_summary ? <span className="ml-2 text-xs text-muted-foreground" title={execution.error_summary}>{execution.error_summary}</span> : null}</td>
                <td className="px-3 py-2.5 text-xs tabular-nums" title={execution.started_at}>{formatLocalDateTime(execution.started_at)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{formatTokenCount(executionTotals.total_tokens)}{execution.missing_usage_count > 0 ? <span className="ml-1 text-[11px] leading-4 text-amber-700 dark:text-amber-400">用量缺失 {execution.missing_usage_count}</span> : null}</td>
                <td className="px-3 py-2.5 text-right text-xs font-medium tabular-nums">{costLabel}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {issue.truncated ? <p className="border-t px-3 py-2 text-xs text-amber-800 dark:text-amber-300">调用历史达到读取上限，本 Issue 明细可能不完整。</p> : null}
    </div>
  )
}

/** 展示某个 Issue 或所选期间的模型 Token 明细及当前价格估算。 */
export function ModelUsageTable({
  modelUsage,
  pricingModels,
  emptyLabel,
}: {
  modelUsage: IssueCostStatsIssue["model_usage"]
  pricingModels: ModelTokenPricing[]
  emptyLabel: string
}) {
  if (modelUsage.length === 0) {
    return <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">{emptyLabel}</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[760px] text-left text-sm" data-testid="stats-issue-model-usage">
        <thead className="bg-muted/30 text-xs text-muted-foreground"><tr><th className="px-3 py-2 font-medium">模型</th><th className="px-3 py-2 text-right font-medium">输入</th><th className="px-3 py-2 text-right font-medium">输出</th><th className="px-3 py-2 text-right font-medium">缓存读</th><th className="px-3 py-2 text-right font-medium">缓存写</th><th className="px-3 py-2 text-right font-medium">Token 总量</th><th className="px-3 py-2 text-right font-medium">估算费用（USD）</th></tr></thead>
        <tbody>
          {modelUsage.map((usageRow) => {
            const estimate = estimateModelUsageCost({ [usageRow.model_id]: usageRow.totals }, pricingModels)
            return (
              <tr key={usageRow.model_id} className="border-t">
                <td className="px-3 py-2.5 font-medium">{usageRow.model_id === "unknown" ? <Badge variant="warning">未识别模型</Badge> : usageRow.model_id}{usageRow.configuration_fallback_count > 0 ? <span className="ml-2 text-xs font-normal text-muted-foreground">含配置估价</span> : null}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{formatTokenCount(usageRow.totals.input_tokens)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{formatTokenCount(usageRow.totals.output_tokens)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{formatTokenCount(usageRow.totals.cache_read_input_tokens)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{formatTokenCount(usageRow.totals.cache_creation_input_tokens)}</td>
                <td className="px-3 py-2.5 text-right font-semibold tabular-nums">{formatTokenCount(usageRow.totals.total_tokens)}</td>
                <td className="px-3 py-2.5 text-right text-xs font-medium tabular-nums">{formatEstimate(estimate)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 格式化 Issue 的估价，并区分无估价与部分计价。 */
function formatIssueCost(issue: IssueCostStatsIssue, pricingModels: ModelTokenPricing[]): string {
  if (!issue.period_data_available || issue.totals.usage_count === 0) return "暂无估价"
  const estimate = estimateModelUsageCost(Object.fromEntries(issue.model_usage.map((modelUsage) => [modelUsage.model_id, modelUsage.totals])), pricingModels)
  if (estimate.tokenCount === 0) return "用量上报为 0"
  if (estimate.unpricedModelIds.length > 0 && estimate.pricedTokenCount === 0) return "暂无估价"
  const isPartial = estimate.unpricedModelIds.length > 0 || issue.missing_usage_count > 0 || issue.truncated
  return `${formatUsdCost(estimate.costUsd)}${isPartial ? " · 部分计价" : ""}`
}

/** 格式化模型估价，并对无价格的模型保留未知状态。 */
function formatEstimate(estimate: ReturnType<typeof estimateModelUsageCost>): string {
  if (estimate.usageCount === 0) return "暂无估价"
  if (estimate.tokenCount === 0) return "用量上报为 0"
  if (estimate.unpricedModelIds.length > 0 && estimate.pricedTokenCount === 0) return "暂无估价"
  return `${formatUsdCost(estimate.costUsd)}${estimate.unpricedModelIds.length > 0 ? " · 部分计价" : ""}`
}

/** 返回可确认的 Issue 费用，用于费用排序。 */
function getIssueCost(issue: IssueCostStatsIssue, pricingModels: ModelTokenPricing[]): number {
  return estimateModelUsageCost(Object.fromEntries(issue.model_usage.map((modelUsage) => [modelUsage.model_id, modelUsage.totals])), pricingModels).costUsd
}

/** 按最新执行时间降序排列，时间相同时按 Issue 编号排序。 */
function compareRecentExecution(leftIssue: IssueCostStatsIssue, rightIssue: IssueCostStatsIssue): number {
  const leftStartedAt = leftIssue.executions.at(-1)?.started_at ?? ""
  const rightStartedAt = rightIssue.executions.at(-1)?.started_at ?? ""
  return rightStartedAt.localeCompare(leftStartedAt) || leftIssue.issue_number - rightIssue.issue_number
}

/** 返回行内明细页签对应的选中样式。 */
function detailTabClass(isActive: boolean): string {
  return `border-b-2 px-3 py-2 text-sm ${isActive ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`
}
