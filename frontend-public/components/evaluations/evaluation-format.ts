// 模型评测页的状态文案与格式化：所有失败态原样展示，绝不折算为分数。

import type { BadgeVariant } from "@/components/ui/badge";
import type {
  EvaluationCheckStatus,
  EvaluationResultStatus,
  EvaluationRunStatus,
  EvaluationScoreStatus,
} from "@/lib/api/types";

/** run 状态 -> 中文标签与 Badge 变体。 */
export const RUN_STATUS_META: Record<
  EvaluationRunStatus,
  { label: string; variant: BadgeVariant }
> = {
  created: { label: "待启动", variant: "default" },
  queued: { label: "已排队", variant: "supervising" },
  running: { label: "执行中", variant: "running" },
  awaiting_judge_consent: { label: "待授权评审", variant: "warning" },
  judging: { label: "评审中", variant: "review" },
  completed: { label: "已完成", variant: "ready" },
  cancelled: { label: "已取消", variant: "blocked" },
  interrupted: { label: "已中断", variant: "error" },
};

/** 候选结果状态 -> 中文标签与 Badge 变体。 */
export const RESULT_STATUS_META: Record<
  EvaluationResultStatus,
  { label: string; variant: BadgeVariant }
> = {
  pending: { label: "待执行", variant: "default" },
  running: { label: "执行中", variant: "running" },
  succeeded: { label: "成功", variant: "ready" },
  failed: { label: "失败", variant: "failed" },
  timeout: { label: "超时", variant: "error" },
  cancelled: { label: "已取消", variant: "blocked" },
};

/** 确定性检查状态 -> 中文标签与 Badge 变体。 */
export const CHECK_STATUS_META: Record<
  EvaluationCheckStatus,
  { label: string; variant: BadgeVariant }
> = {
  passed: { label: "检查通过", variant: "ready" },
  failed: { label: "检查失败", variant: "failed" },
  timeout: { label: "检查超时", variant: "error" },
  not_run: { label: "未执行检查", variant: "default" },
};

/** 逐评审评分状态 -> 中文标签与 Badge 变体。 */
export const SCORE_STATUS_META: Record<
  EvaluationScoreStatus,
  { label: string; variant: BadgeVariant }
> = {
  valid: { label: "有效", variant: "ready" },
  refusal: { label: "评审拒绝", variant: "warning" },
  invalid: { label: "响应不合规", variant: "warning" },
  timeout: { label: "评审超时", variant: "error" },
  provider_error: { label: "Provider 错误", variant: "error" },
  unauthorized: { label: "未授权外发", variant: "blocked" },
  cancelled: { label: "已取消", variant: "blocked" },
};

/** rubric v1 四维度展示名（与后端 EVALUATION_RUBRIC_DIMENSIONS 对齐）。 */
export const DIMENSION_LABELS: Record<string, string> = {
  correctness: "正确性 / 任务要求",
  completeness: "完整性与范围",
  implementation_quality: "实现质量",
  verification_evidence: "验证证据",
};

/**
 * 把秒数格式化为紧凑人读时长。
 *
 * @param seconds - 秒数；``null`` 表示未记录（≠ 0 秒）。
 * @returns 类似 ``3m 12s`` 的字符串；无数据返回 ``—``。
 */
export function formatEvaluationDuration(
  seconds: number | null | undefined,
): string {
  if (seconds === null || seconds === undefined) {
    return "—";
  }
  const total = Math.round(seconds);
  if (total < 60) {
    return `${total}s`;
  }
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    return `${minutes}m ${total % 60}s`;
  }
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/**
 * 聚合值展示：中位数保留一位小数，无有效评分时明确写「无有效评分」。
 *
 * @param median - 后端派生的中位数（可为 null）。
 * @param validCount - 参与聚合的有效评分条数。
 * @returns 展示文本。
 */
export function formatAggregateMedian(
  median: number | null,
  validCount: number,
): string {
  if (validCount === 0 || median === null) {
    return "无有效评分";
  }
  return median.toFixed(1);
}
