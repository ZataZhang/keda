import type { ModelTokenPricing, TokenUsageTotals } from "@/lib/api/types"

export type ModelCostEstimate = {
  costUsd: number
  usageCount: number
  tokenCount: number
  pricedTokenCount: number
  unpricedModelIds: string[]
}

/**
 * 按模型单价估算一组调用用量；未配置的模型会保留在未计价清单中。
 *
 * @param usageByModel - 模型标识符到 token 用量汇总的映射。
 * @param modelPrices - 本机保存的每百万 token 价格。
 * @returns 费用、计价覆盖量和缺少价格的模型标识符。
 */
export function estimateModelUsageCost(
  usageByModel: Record<string, TokenUsageTotals>,
  modelPrices: ModelTokenPricing[]
): ModelCostEstimate {
  const priceByModelId = new Map(
    modelPrices.map((modelPrice) => [modelPrice.model_id, modelPrice])
  )
  let costUsd = 0
  let usageCount = 0
  let tokenCount = 0
  let pricedTokenCount = 0
  const unpricedModelIds: string[] = []

  for (const [modelId, usageTotals] of Object.entries(usageByModel)) {
    usageCount += usageTotals.usage_count
    tokenCount += usageTotals.total_tokens
    const modelPrice = priceByModelId.get(modelId)
    if (!modelPrice) {
      if (usageTotals.usage_count > 0) {
        unpricedModelIds.push(modelId)
      }
      continue
    }
    costUsd +=
      (usageTotals.input_tokens * modelPrice.input_usd_per_million +
        usageTotals.output_tokens * modelPrice.output_usd_per_million +
        usageTotals.cache_read_input_tokens *
          modelPrice.cache_read_usd_per_million +
        usageTotals.cache_creation_input_tokens *
          modelPrice.cache_creation_usd_per_million) /
      1_000_000
    pricedTokenCount += usageTotals.total_tokens
  }

  return {
    costUsd,
    usageCount,
    tokenCount,
    pricedTokenCount,
    unpricedModelIds: unpricedModelIds.sort((left, right) =>
      left.localeCompare(right)
    ),
  }
}

/**
 * 以六位小数显示低额 token 费用，避免小额成本被四舍五入成零。
 *
 * @param costUsd - 按 USD 计算的费用。
 * @returns 格式化后的美元金额。
 */
export function formatUsdCost(costUsd: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  }).format(costUsd)
}
