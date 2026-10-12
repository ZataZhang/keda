"use client"

import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { formatTokenCount } from "@/components/backlog/prd-lifecycle-format"
import { estimateModelUsageCost, formatUsdCost } from "@/lib/model-pricing"
import type {
  ModelTokenPricing,
  ModelTokenUsageStats,
  PrdModelTokenUsageEntry,
  PrdLifecycleStats,
  PrdTokenUsageEntry,
  TokenUsageTotals,
} from "@/lib/api/types"

/** 流程标识 -> 中文标签；未识别的 flow 原样展示。 */
const TOKEN_FLOW_LABELS: Record<string, string> = {
  implement: "实现",
  verify: "验证",
  supervise: "评审",
  fix: "修复",
  closeout: "收尾",
}

/**
 * 展示 token 用量、模型成本估算，以及按 Agent 和 Issue 拆分的费用。
 *
 * @param props.stats - 当前仓库与时间窗口的生命周期统计。
 * @param props.pricingModels - 本机已保存的模型价格清单。
 * @param props.pricingError - 价格配置读取失败时的错误摘要。
 * @returns 含模型费用、Agent 与 Issue 用量表的统计卡片。
 */
export function TokenUsageSection({
  stats,
  pricingModels,
  pricingError,
}: {
  stats: PrdLifecycleStats
  pricingModels: ModelTokenPricing[]
  pricingError: string | null
}) {
  const byFlow = Object.entries(stats.token_usage?.by_flow ?? {})
  const byAgent = Object.entries(stats.token_usage?.by_agent ?? {})
  const byPrd = stats.token_usage_by_prd ?? []
  const modelUsageStats: ModelTokenUsageStats = stats.token_usage_by_model ?? {
    by_model: {},
    by_agent: {},
    by_prd: [],
    truncated: false,
  }
  const modelRows = Object.entries(modelUsageStats.by_model).sort(
    ([leftModelId, leftTotals], [rightModelId, rightTotals]) =>
      rightTotals.total_tokens - leftTotals.total_tokens ||
      leftModelId.localeCompare(rightModelId)
  )
  const overallCost = estimateModelUsageCost(
    modelUsageStats.by_model,
    pricingModels
  )
  const overallCostValue =
    overallCost.usageCount === 0
      ? "—"
      : overallCost.unpricedModelIds.length > 0 &&
          overallCost.pricedTokenCount === 0
        ? "待配置"
        : `${formatUsdCost(overallCost.costUsd)}${overallCost.unpricedModelIds.length > 0 ? " + 未计价" : ""}`
  const costsByPrd = buildCostsByPrd(modelUsageStats.by_prd, pricingModels)
  const hasData =
    byFlow.length > 0 ||
    byAgent.length > 0 ||
    byPrd.length > 0 ||
    modelRows.length > 0

  return (
    <Card
      data-testid="stats-token-usage"
      className="gap-5 border-border/70 shadow-sm"
    >
      <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1.5">
          <CardTitle className="text-base">Token 用量与费用</CardTitle>
          <CardDescription>
            按实际调用记录中的模型和四类 token
            用量估算；价格按本机设置，缺少价格的模型会单独标出。
          </CardDescription>
        </div>
        <Button asChild size="sm" variant="outline" className="shrink-0">
          <Link href="/app/settings/">设置模型价格</Link>
        </Button>
      </CardHeader>
      <CardContent className="space-y-6">
        {pricingError ? (
          <p
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
            role="alert"
          >
            模型价格设置读取失败：{pricingError}。当前费用结果可能未计价。
          </p>
        ) : null}
        {!hasData ? (
          <p
            className="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground"
            data-testid="stats-token-usage-empty"
          >
            所选范围内暂无 token 用量数据。没有实际 usage 的调用不会估算费用。
          </p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <CostMetric
                label="已计价费用"
                value={overallCostValue}
                detail={
                  overallCost.unpricedModelIds.length > 0
                    ? `另有 ${overallCost.unpricedModelIds.length} 个模型待配置`
                    : "当前筛选范围内的估算总额"
                }
              />
              <CostMetric
                label="已覆盖 Token"
                value={
                  modelRows.length > 0
                    ? `${formatTokenCount(overallCost.pricedTokenCount)} / ${formatTokenCount(overallCost.tokenCount)}`
                    : "—"
                }
                detail={
                  modelRows.length > 0 && overallCost.tokenCount > 0
                    ? `${Math.round((overallCost.pricedTokenCount / overallCost.tokenCount) * 100)}% 已按模型价格计价`
                    : "当前范围内暂无模型归属数据"
                }
              />
              <CostMetric
                label="识别到的模型"
                value={String(modelRows.length)}
                detail={`${overallCost.usageCount} 次有 usage 的模型调用`}
              />
            </div>

            {modelUsageStats.truncated ? (
              <p
                className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
                role="status"
              >
                至少一个 Issue 的调用记录达到 500 条读取上限，模型费用可能少计。
              </p>
            ) : null}

            <div className="space-y-2" data-testid="stats-token-cost-by-model">
              <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold">按模型</h3>
                  <p className="text-xs text-muted-foreground">
                    价格单位：USD / 百万 token
                  </p>
                </div>
                {overallCost.unpricedModelIds.length > 0 ? (
                  <span className="text-xs text-amber-700 dark:text-amber-400">
                    有未配置模型，费用合计为部分计价
                  </span>
                ) : null}
              </div>
              <div className="overflow-x-auto rounded-xl border">
                {modelRows.length === 0 ? (
                  <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                    尚无可关联到模型的调用用量；历史旧记录不会推测模型。
                  </p>
                ) : (
                  <table className="w-full min-w-[560px] text-left text-sm">
                    <thead className="bg-muted/40 text-xs text-muted-foreground">
                      <tr>
                        <th className="px-4 py-3 font-medium">模型</th>
                        <th className="px-4 py-3 text-right font-medium">
                          Token 总量
                        </th>
                        <th className="px-4 py-3 text-right font-medium">
                          调用数
                        </th>
                        <th className="px-4 py-3 text-right font-medium">
                          估算费用
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {modelRows.map(([modelId, totals]) => {
                        const modelCost = estimateModelUsageCost(
                          { [modelId]: totals },
                          pricingModels
                        )
                        return (
                          <tr key={modelId} className="border-t">
                            <td
                              className="max-w-[420px] truncate px-4 py-3 font-medium"
                              title={modelId}
                            >
                              {modelId === "unknown" ? (
                                <Badge variant="warning">未识别模型</Badge>
                              ) : (
                                modelId
                              )}
                            </td>
                            <td className="px-4 py-3 text-right tabular-nums">
                              {formatTokenCount(totals.total_tokens)}
                            </td>
                            <td className="px-4 py-3 text-right tabular-nums">
                              {totals.usage_count}
                            </td>
                            <td className="px-4 py-3 text-right font-semibold tabular-nums">
                              <CostValue estimate={modelCost} />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            <div className="space-y-3 border-t pt-5">
              <h3 className="text-sm font-semibold">用量明细</h3>
              <TokenUsageTable title="按流程" rows={byFlow} />
              <TokenUsageTable
                title="按 Agent"
                rows={byAgent}
                modelUsageByGroup={modelUsageStats.by_agent}
                pricingModels={pricingModels}
              />
              <PrdTokenUsageTable
                rows={byPrd}
                costsByPrd={costsByPrd}
                modelUsageStats={modelUsageStats}
              />
              <p
                className="text-xs text-muted-foreground"
                data-testid="stats-token-usage-note"
              >
                Token 总量为实际处理量（输入 + 输出 + 缓存读 +
                缓存写）。费用按未缓存输入、输出、缓存读与缓存写分别计价；缺少模型或价格时不计入费用。
              </p>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function CostMetric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="rounded-xl border bg-muted/20 px-4 py-3">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tracking-tight tabular-nums">
        {value}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}

function CostValue({
  estimate,
}: {
  estimate: ReturnType<typeof estimateModelUsageCost> | null
}) {
  if (estimate === null) {
    return (
      <span
        className="text-muted-foreground"
        title="旧记录没有可关联的模型调用明细"
      >
        无模型记录
      </span>
    )
  }
  if (estimate.usageCount === 0) {
    return <span className="text-muted-foreground">—</span>
  }
  if (estimate.unpricedModelIds.length > 0 && estimate.pricedTokenCount === 0) {
    return (
      <span
        className="text-amber-700 dark:text-amber-400"
        title="请在设置中填写此模型的单价"
      >
        待配置
      </span>
    )
  }
  return (
    <span
      title={
        estimate.unpricedModelIds.length > 0
          ? `部分计价，待配置：${estimate.unpricedModelIds.join("、")}`
          : undefined
      }
    >
      {formatUsdCost(estimate.costUsd)}
      {estimate.unpricedModelIds.length > 0 ? (
        <span className="ml-1 text-xs font-normal text-amber-700 dark:text-amber-400">
          部分
        </span>
      ) : null}
    </span>
  )
}

function buildCostsByPrd(
  modelUsageByPrd: PrdModelTokenUsageEntry[],
  pricingModels: ModelTokenPricing[]
): Map<string, ReturnType<typeof estimateModelUsageCost>> {
  const modelTotalsByPrd = new Map<string, Record<string, TokenUsageTotals>>()
  for (const entry of modelUsageByPrd) {
    const prdKey = makePrdKey(entry.repo_id, entry.prd_path, entry.issue_number)
    const totalsByModel = modelTotalsByPrd.get(prdKey) ?? {}
    totalsByModel[entry.model_id] = entry.totals
    modelTotalsByPrd.set(prdKey, totalsByModel)
  }
  return new Map(
    [...modelTotalsByPrd.entries()].map(([prdKey, totalsByModel]) => [
      prdKey,
      estimateModelUsageCost(totalsByModel, pricingModels),
    ])
  )
}

function makePrdKey(
  repoId: string | null,
  prdPath: string | null,
  issueNumber: number | null
) {
  return JSON.stringify([repoId ?? "", prdPath ?? "", issueNumber ?? ""])
}

function cacheHitRate(row: TokenUsageTotals): number | null {
  const inputSide =
    row.input_tokens +
    row.cache_read_input_tokens +
    row.cache_creation_input_tokens
  return inputSide > 0 ? row.cache_read_input_tokens / inputSide : null
}

function prdBasename(prdPath: string): string {
  const segments = prdPath.split("/")
  return segments[segments.length - 1] || prdPath
}

function TokenUsageTable({
  title,
  rows,
  modelUsageByGroup,
  pricingModels = [],
}: {
  title: string
  rows: [string, TokenUsageTotals][]
  modelUsageByGroup?: Record<string, Record<string, TokenUsageTotals>>
  pricingModels?: ModelTokenPricing[]
}) {
  const sorted = [...rows].sort(
    ([, left], [, right]) => right.total_tokens - left.total_tokens
  )
  return (
    <div className="overflow-x-auto rounded-lg border">
      <p className="bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
        {title}
      </p>
      <table className="w-full min-w-[700px] text-left text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">分组</th>
            <th className="px-3 py-2 text-right font-medium">总量</th>
            <th className="px-3 py-2 text-right font-medium">输入</th>
            <th className="px-3 py-2 text-right font-medium">输出</th>
            <th className="px-3 py-2 text-right font-medium">缓存读</th>
            <th className="px-3 py-2 text-right font-medium">缓存写</th>
            <th className="px-3 py-2 text-right font-medium">命中率</th>
            <th className="px-3 py-2 text-right font-medium">调用数</th>
            {modelUsageByGroup ? (
              <th className="px-3 py-2 text-right font-medium">估算费用</th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {sorted.map(([key, row]) => {
            const rate = cacheHitRate(row)
            const label = TOKEN_FLOW_LABELS[key] ?? key
            const modelTotalsByGroup = modelUsageByGroup?.[key]
            const costEstimate = modelTotalsByGroup
              ? estimateModelUsageCost(modelTotalsByGroup, pricingModels)
              : null
            return (
              <tr key={key} className="border-t">
                <td className="px-3 py-2.5 font-medium">{label}</td>
                <td className="px-3 py-2.5 text-right font-semibold tabular-nums">
                  {formatTokenCount(row.total_tokens)}
                </td>
                <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                  {formatTokenCount(row.input_tokens)}
                </td>
                <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                  {formatTokenCount(row.output_tokens)}
                </td>
                <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                  {formatTokenCount(row.cache_read_input_tokens)}
                </td>
                <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                  {formatTokenCount(row.cache_creation_input_tokens)}
                </td>
                <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                  {rate === null ? "—" : `${Math.round(rate * 100)}%`}
                </td>
                <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                  {row.usage_count}
                </td>
                {modelUsageByGroup ? (
                  <td className="px-3 py-2.5 text-right text-xs font-medium tabular-nums">
                    <CostValue estimate={costEstimate} />
                  </td>
                ) : null}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function PrdTokenUsageTable({
  rows,
  costsByPrd,
  modelUsageStats,
}: {
  rows: PrdTokenUsageEntry[]
  costsByPrd: Map<string, ReturnType<typeof estimateModelUsageCost>>
  modelUsageStats: ModelTokenUsageStats
}) {
  const sorted = [...rows].sort(
    (left, right) => right.totals.total_tokens - left.totals.total_tokens
  )
  const modelUsageByPrd = new Map<string, PrdModelTokenUsageEntry[]>()
  for (const entry of modelUsageStats.by_prd) {
    const prdKey = makePrdKey(entry.repo_id, entry.prd_path, entry.issue_number)
    const currentRows = modelUsageByPrd.get(prdKey) ?? []
    currentRows.push(entry)
    modelUsageByPrd.set(prdKey, currentRows)
  }
  return (
    <div
      className="overflow-x-auto rounded-lg border"
      data-testid="stats-token-usage-by-prd"
    >
      <p className="bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
        按 PRD / Issue
      </p>
      {sorted.length === 0 ? (
        <p
          className="px-3 py-4 text-sm text-muted-foreground"
          data-testid="stats-token-usage-by-prd-empty"
        >
          所选范围内暂无 PRD token 用量数据。
        </p>
      ) : (
        <table className="w-full min-w-[900px] text-left text-sm">
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Issue</th>
              <th className="px-3 py-2 font-medium">PRD</th>
              <th className="px-3 py-2 text-right font-medium">总量</th>
              <th className="px-3 py-2 text-right font-medium">估算费用</th>
              <th className="px-3 py-2 text-right font-medium">输入</th>
              <th className="px-3 py-2 text-right font-medium">输出</th>
              <th className="px-3 py-2 text-right font-medium">缓存读</th>
              <th className="px-3 py-2 text-right font-medium">缓存写</th>
              <th className="px-3 py-2 text-right font-medium">命中率</th>
              <th className="px-3 py-2 text-right font-medium">调用数</th>
              <th className="px-3 py-2 text-right font-medium">执行次数</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((entry) => {
              const totals = entry.totals
              const prdKey = makePrdKey(
                entry.repo_id,
                entry.prd_path,
                entry.issue_number
              )
              const rate = cacheHitRate(totals)
              const costEstimate = costsByPrd.get(prdKey) ?? null
              const modelUsageRows = modelUsageByPrd.get(prdKey) ?? []
              return (
                <tr
                  key={prdKey}
                  className="border-t"
                  title={
                    modelUsageRows.length > 0
                      ? `按模型计价：${modelUsageRows.map((modelRow) => modelRow.model_id).join("、")}`
                      : undefined
                  }
                >
                  <td className="px-3 py-2.5 font-mono text-xs">
                    {entry.issue_number === null
                      ? "—"
                      : `#${entry.issue_number}`}
                  </td>
                  <td
                    className="max-w-[240px] truncate px-3 py-2.5 font-medium"
                    title={entry.prd_path ?? undefined}
                  >
                    {entry.prd_path ? prdBasename(entry.prd_path) : "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right font-semibold tabular-nums">
                    {formatTokenCount(totals.total_tokens)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs font-medium tabular-nums">
                    <CostValue estimate={costEstimate} />
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {formatTokenCount(totals.input_tokens)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {formatTokenCount(totals.output_tokens)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {formatTokenCount(totals.cache_read_input_tokens)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {formatTokenCount(totals.cache_creation_input_tokens)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {rate === null ? "—" : `${Math.round(rate * 100)}%`}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {totals.usage_count}
                  </td>
                  <td className="px-3 py-2.5 text-right text-xs tabular-nums">
                    {entry.run_count}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
