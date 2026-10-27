// PRD 生命周期视图的展示词汇与格式化（时间线、Stats、运行详情弹窗共用同一份口径）。
//
// 这里只回答「后端已经确定的事实怎么说成人话」，不做任何推断：阶段、耗时、
// 调用终态的语义一律以后端闭集为准，前端不重算、不猜。两处刻意的诚实表述：
// 观测事件（Token 用量）用中性徽章而不是阶段标签；模型三态在「未下发」与
// 「执行器未上报」之间不互相冒充，宁可写「无法确认」也不编出具体模型。

import type { BadgeVariant } from "@/components/ui/badge"
import type { AgentModelConfigSnapshot, PrdLifecycleEventView } from "@/lib/api/types"

/** 当前阶段 / 事件阶段 -> 中文标签。 */
export const PHASE_LABELS: Record<string, string> = {
  none: "未开始",
  queued: "排队中",
  executing: "执行中",
  validating: "验证中",
  reviewing: "审阅中",
  merging: "合并中",
  blocked: "阻塞中",
  failed: "失败",
  completed: "已完成",
}

/** 事件类型 -> 中文标签。 */
export const EVENT_TYPE_LABELS: Record<string, string> = {
  queued: "已进入队列",
  started: "开始执行",
  claimed: "已被领取",
  attempt: "Agent 尝试",
  retry: "重试",
  recovered: "已恢复",
  implementation_completed: "实现完成",
  validation_started: "开始验证",
  validation_passed: "验证通过",
  validation_failed: "验证失败",
  review_started: "开始审阅",
  review_passed: "审阅通过",
  review_failed: "审阅失败",
  merge_started: "开始合并",
  merged: "已合并",
  archived: "已归档",
  blocked: "已阻塞",
  unblocked: "已解除阻塞",
  failed: "失败",
  agent_token_usage: "Token 用量",
}

/**
 * 状态徽章标签覆盖项（时间线状态徽章的事实源补充）。
 *
 * 后端按 event_type 在写入时冻结 `status`，比粗粒度 `phase` 更细，且其取值
 * 与 event_type 大多同名，因此默认复用 `EVENT_TYPE_LABELS`；仅对需要「状态化」
 * 区分的少数值在此覆盖，避免与 `EVENT_TYPE_LABELS` 形成大段重复。
 */
const STATUS_LABEL_OVERRIDES: Record<string, string> = {
  // 事件类型「已进入队列」，徽章仍显示排队中（保持既有正确行为）。
  queued: "排队中",
  // merged 归入完成态。
  completed: "已完成",
  failed: "已失败",
}

/**
 * 取得事件语义状态的中文徽章标签。
 *
 * @param status - 后端事件语义状态枚举值（与 event_type 大多同名）。
 * @returns 覆盖表优先命中的标签，否则回落到 `EVENT_TYPE_LABELS`，再否则原样返回。
 */
export function eventStatusLabel(status: string): string {
  return STATUS_LABEL_OVERRIDES[status] ?? EVENT_TYPE_LABELS[status] ?? status
}

const PHASE_BADGE_VARIANTS: Record<string, BadgeVariant> = {
  queued: "default",
  executing: "running",
  validating: "supervising",
  reviewing: "review",
  merging: "supervising",
  blocked: "blocked",
  failed: "error",
  completed: "ready",
  none: "default",
}

/** 事件语义状态 -> Badge 变体（配色），取值与 event_type / status 同名。 */
const STATUS_BADGE_VARIANTS: Record<string, BadgeVariant> = {
  queued: "default",
  started: "running",
  claimed: "supervising",
  attempt: "running",
  retry: "warning",
  recovered: "ready",
  implementation_completed: "supervising",
  validation_started: "supervising",
  validation_passed: "ready",
  validation_failed: "error",
  review_started: "review",
  review_passed: "ready",
  review_failed: "error",
  merge_started: "supervising",
  completed: "ready",
  archived: "ready",
  blocked: "blocked",
  unblocked: "running",
  failed: "error",
}

/**
 * 取得阶段对应的 Badge 变体。
 *
 * @param phase - 后端阶段枚举值。
 * @returns 该阶段的 Badge 变体，未知阶段回落到 ``default``。
 */
export function phaseBadgeVariant(phase: string): BadgeVariant {
  return PHASE_BADGE_VARIANTS[phase] ?? "default"
}

/**
 * 取得事件语义状态对应的 Badge 变体。
 *
 * @param status - 后端事件语义状态枚举值。
 * @returns 该状态的 Badge 变体，未知状态回落到 ``default``。
 */
export function statusBadgeVariant(status: string): BadgeVariant {
  return STATUS_BADGE_VARIANTS[status] ?? "default"
}

/**
 * 把秒数格式化为紧凑时长文案。
 *
 * @param seconds - 秒数；null 表示无数据。
 * @returns 形如 ``45s`` / ``12m`` / ``2h 18m`` 的字符串；无数据时为 ``—``。
 */
export function formatLifecycleDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—"
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`
  }
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.round((seconds % 3600) / 60)
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`
}

/**
 * 把 token 计数格式化为紧凑文本（``820`` / ``1.2k``）。
 *
 * @param count - token 数量。
 * @returns 千位以上保留一位小数加 ``k``，否则原样数字。
 */
export function formatTokenCount(count: number): string {
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}k`
  }
  return String(count)
}

/**
 * 把 ``token_usage`` 对象渲染为总量 + 明细的可读文本。
 *
 * 总量为「实际处理量」口径（输入 + 输出 + 缓存读 + 缓存写）。
 *
 * @param value - 事件 detail 或调用行里的 ``token_usage`` 原始值。
 * @returns 形如 ``tokens=2.5k（输入 1.2k · 输出 340 · 缓存读 800 · 缓存写 120）``
 *   的文本；无可用用量时返回 ``null``（不显示该段，而不是显示 0）。
 */
export function summarizeTokenUsage(value: unknown): string | null {
  if (typeof value !== "object" || value === null) {
    return null
  }
  const usage = value as Record<string, unknown>
  const readCount = (key: string): number | null => {
    const raw = usage[key]
    return typeof raw === "number" && Number.isFinite(raw) && raw >= 0
      ? raw
      : null
  }
  const inputTokens = readCount("input_tokens")
  const outputTokens = readCount("output_tokens")
  const cacheRead = readCount("cache_read_input_tokens")
  const cacheCreation = readCount("cache_creation_input_tokens")
  if (
    inputTokens === null &&
    outputTokens === null &&
    cacheRead === null &&
    cacheCreation === null
  ) {
    return null
  }
  const inputSide = (inputTokens ?? 0) + (cacheRead ?? 0) + (cacheCreation ?? 0)
  const total = inputSide + (outputTokens ?? 0)
  const detailParts = [
    `输入 ${formatTokenCount(inputTokens ?? 0)}`,
    `输出 ${formatTokenCount(outputTokens ?? 0)}`,
    `缓存读 ${formatTokenCount(cacheRead ?? 0)}`,
    `缓存写 ${formatTokenCount(cacheCreation ?? 0)}`,
  ]
  return `tokens=${formatTokenCount(total)}（${detailParts.join(" · ")}）`
}

/** 调用阶段 -> 人话描述（与运行详情弹窗同一份词汇，不各写一份）。 */
export const INVOCATION_PHASE_LABELS: Record<string, string> = {
  implementation: "编写代码",
  fix: "修复问题",
  review: "代码审查",
  review_repair: "按审查意见修复",
  verification: "运行验证",
  verification_recovery: "修复验证问题",
  rebase_recovery: "同步分支并处理冲突",
  closeout: "交付收尾",
  supervisor: "自动检查进度",
  supervisor_repair: "检查发现问题后的修复",
  content_generation: "生成任务内容",
  unspecified: "Agent 工作",
}

/** 调用角色 -> 人话描述。 */
export const INVOCATION_ROLE_LABELS: Record<string, string> = {
  implementer: "负责修改代码",
  fixer: "负责修复问题",
  reviewer: "负责审查代码",
  verifier: "负责运行验证",
  supervisor: "负责检查进度",
  content_generator: "负责生成内容",
  unspecified: "角色未记录",
}

/** 模型三态说明所需的最小字段集（调用行与运行详情行都满足）。 */
export type InvocationModelFields = {
  requested_model: string | null
  reported_model: string | null
  model_config_snapshot?: AgentModelConfigSnapshot | null
}

/** 用准确但易读的文字说明模型名称来自哪里。 */
export function describeInvocationModel(
  invocationDetail: InvocationModelFields
): string {
  const requestedModel = invocationDetail.requested_model
  const reportedModel = invocationDetail.reported_model

  if (requestedModel && reportedModel && requestedModel !== reportedModel) {
    return `KC 下发：${requestedModel}；执行器报告：${reportedModel}`
  }
  if (requestedModel && reportedModel) {
    return `KC 下发且执行器报告：${requestedModel}`
  }
  if (requestedModel) {
    return `KC 下发：${requestedModel}；执行器没有报告模型名称`
  }
  if (reportedModel) {
    return `执行器报告：${reportedModel}；KC 没有单独指定模型`
  }
  const configSnapshot = invocationDetail.model_config_snapshot
  if (configSnapshot) {
    return configSnapshot.is_alias
      ? `工具默认配置：${configSnapshot.model_id}（别名，具体模型待确认）；执行器未报告`
      : `工具默认配置：${configSnapshot.model_id}（仅配置快照，执行器未报告）`
  }
  return "无法确认具体模型：执行器没有上报，KC 也没有单独指定。"
}

/** 调用终态 -> 徽章文案与配色（未闭合不得显示成估算耗时）。 */
const INVOCATION_OUTCOME_PRESENTATION: Record<
  string,
  { label: string; variant: BadgeVariant }
> = {
  ok: { label: "正常退出", variant: "ready" },
  failed: { label: "异常退出", variant: "error" },
  error: { label: "执行报错", variant: "error" },
  timeout: { label: "超时", variant: "warning" },
  incomplete: { label: "记录不完整", variant: "warning" },
  unclosed: { label: "未闭合", variant: "default" },
}

/**
 * 取得调用终态的徽章文案与配色。
 *
 * @param outcome - 账本终态闭集值。
 * @returns 已知终态的展示定义；未知取值原样透出，不猜语义。
 */
export function invocationOutcomeBadge(outcome: string): {
  label: string
  variant: BadgeVariant
} {
  return (
    INVOCATION_OUTCOME_PRESENTATION[outcome] ?? {
      label: `状态：${outcome}`,
      variant: "default",
    }
  )
}

/** 观测类事件（不占时长、不推进阶段）的中性徽章口径。 */
const OBSERVATION_EVENT_TYPES = new Set(["agent_token_usage"])

/**
 * 判断事件是否为纯观测事件。
 *
 * @param eventType - 事件类型。
 * @returns 纯观测事件为 ``true``，其徽章不得携带阶段语义。
 */
export function isObservationEvent(eventType: string): boolean {
  return OBSERVATION_EVENT_TYPES.has(eventType)
}

/**
 * 把事件 detail 中的单个值渲染为可读文本。
 *
 * @param value - detail 中的原始值。
 * @returns 字符串/数字/布尔直接转文本，对象转 JSON，缺失时 ``—``。
 */
export function formatDetailValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "—"
  }
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value)
  }
  return JSON.stringify(value)
}

/** 生命周期事件 detail 里「所属执行会话」的键名，与后端常量同名。 */
export const INVOCATION_RUN_DETAIL_KEY = "invocation_run"

/**
 * 把事件 detail 渲染为一行短摘要。
 *
 * attempt 事件按 ``#N agent=... failure=... recovered=... tokens=...`` 排列
 * 关键字段；用量观测事件按 ``flow=... agent=... tokens=...`` 排列；其余事件
 * 退化为 ``key=value`` 拼接，但**跳过会话标识**——它是分组依据而非可读信息。
 *
 * @param event - 单条生命周期事件。
 * @returns 供时间线行展示的摘要文本；detail 为空时为 ``—``。
 */
export function summarizeEventDetail(event: PrdLifecycleEventView): string {
  const detail = event.detail ?? {}
  if (
    event.event_type === "failed" &&
    typeof detail.error_summary === "string"
  ) {
    const failureSummary = detail.error_summary.trim()
    if (failureSummary) {
      const summaryPreview = failureSummary.slice(0, 320)
      const overflowNotice =
        failureSummary.length > 320 ? "…（点击查看完整原因）" : ""
      return `失败原因：${summaryPreview}${overflowNotice}`
    }
  }
  if (event.event_type === "attempt") {
    const attemptNumber = detail.attempt_number
    const parts = [
      attemptNumber === undefined ? "#?" : `#${String(attemptNumber)}`,
      `agent=${formatDetailValue(detail.agent)}`,
      `failure=${formatDetailValue(detail.failure_type)}`,
      `recovered=${formatDetailValue(detail.recovered)}`,
    ]
    const tokensSummary = summarizeTokenUsage(detail.token_usage)
    if (tokensSummary !== null) {
      parts.push(tokensSummary)
    }
    return parts.join(" ")
  }
  if (event.event_type === "agent_token_usage") {
    const parts = [
      `flow=${formatDetailValue(detail.flow)}`,
      `agent=${formatDetailValue(detail.agent)}`,
    ]
    const tokensSummary = summarizeTokenUsage(detail.token_usage)
    parts.push(tokensSummary ?? "tokens=—")
    return parts.join(" ")
  }
  const entries = Object.entries(detail).filter(
    ([key]) => key !== INVOCATION_RUN_DETAIL_KEY
  )
  if (entries.length === 0) {
    return "—"
  }
  return entries
    .map(([key, value]) => `${key}=${formatDetailValue(value)}`)
    .join(" ")
}
