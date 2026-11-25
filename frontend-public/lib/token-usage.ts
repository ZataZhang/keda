import type { TokenUsageTotals } from "@/lib/api/types"

/** 将两个 Token 用量记录逐字段相加。 */
export function addTokenUsageTotals(
  leftTotals: TokenUsageTotals,
  rightTotals: TokenUsageTotals,
): TokenUsageTotals {
  return {
    input_tokens: leftTotals.input_tokens + rightTotals.input_tokens,
    output_tokens: leftTotals.output_tokens + rightTotals.output_tokens,
    cache_read_input_tokens:
      leftTotals.cache_read_input_tokens + rightTotals.cache_read_input_tokens,
    cache_creation_input_tokens:
      leftTotals.cache_creation_input_tokens + rightTotals.cache_creation_input_tokens,
    total_tokens: leftTotals.total_tokens + rightTotals.total_tokens,
    usage_count: leftTotals.usage_count + rightTotals.usage_count,
  }
}

/** 合并一组模型 Token 用量记录。 */
export function sumTokenUsageTotals(
  usageTotals: TokenUsageTotals[],
): TokenUsageTotals {
  return usageTotals.reduce(
    addTokenUsageTotals,
    {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_input_tokens: 0,
      cache_creation_input_tokens: 0,
      total_tokens: 0,
      usage_count: 0,
    },
  )
}
