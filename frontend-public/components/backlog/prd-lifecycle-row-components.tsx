"use client";

/** 生命周期事件与调用行组件，两种执行过程视图共用。 */

import type { ReactNode } from "react";

import {
  describeInvocationModel,
  EVENT_TYPE_LABELS,
  eventStatusLabel,
  formatLifecycleDuration,
  INVOCATION_RUN_DETAIL_KEY,
  INVOCATION_PHASE_LABELS,
  INVOCATION_ROLE_LABELS,
  invocationOutcomeBadge,
  isObservationEvent,
  PHASE_LABELS,
  phaseBadgeVariant,
  statusBadgeVariant,
  summarizeEventDetail,
  summarizeTokenUsage,
} from "@/components/backlog/prd-lifecycle-format";
import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import { cn, formatLocalDateTime } from "@/lib/utils";
import type {
  PrdInvocationDetail,
  PrdInvocationRow,
  PrdLifecycleEventView,
  RunAttemptDetailEntry,
} from "@/lib/api/types";

/** 折叠后的一行：主事件 + 同秒折叠项（仍可分别查看原始 detail）。 */
export type EventRow = {
  primary: PrdLifecycleEventView;
  folded: PrdLifecycleEventView[];
};

/** 尝试展开区的调用数据缺口类型。 */
export type InvocationGapKind = "available" | "unavailable" | "absent" | "truncated";

/** 阶段视图与完整轨迹共用的生命周期展示参数。 */
export type PrdLifecycleDisplayProps = {
  events: PrdLifecycleEventView[];
  invocations: PrdInvocationDetail | null | undefined;
  attemptDetails: RunAttemptDetailEntry[];
  attemptDetailsStatus: "loading" | "available" | "unavailable" | "not-queryable";
  onSelectEvent: (event: PrdLifecycleEventView) => void;
};

/**
 * 判定尝试展开区空态该说哪一种成因。
 *
 * 「读不到」「没带这个区」「只读到最近一段」「确实没有记录」是四件不同的事，说错成因
 * 会让人去修错误的一端：截断时这次尝试的调用存在，只是没被返回，不能写成
 * 「观测早于本次执行上线」。
 *
 * @param invocations - 调用明细读取区；null / undefined 表示响应未带该区域。
 * @returns 空态成因标签；available 表示账本可读且未截断。
 */
export function invocationGapKindOf(
  invocations: PrdInvocationDetail | null | undefined,
): InvocationGapKind {
  if (invocations === null || invocations === undefined) {
    return "absent";
  }
  if (!invocations.available) {
    return "unavailable";
  }
  return invocations.truncated ? "truncated" : "available";
}

const INVOCATION_RETRY_SOURCE_LABELS: Record<string, string> = {
  transient_failure: "上一次进程瞬态失败后自动重试",
  executor_fallback: "原执行器失败后切换执行器重试",
  resume_not_started: "会话续接未启动后重新调用",
  timeout_resume: "进程超时后尝试续接会话",
};

const ATTEMPT_FAILURE_SOURCE_LABELS: Record<string, string> = {
  agent_error: "Agent 执行异常",
  delivery_closeout: "交付收尾门禁未通过",
  forbidden_blocked: "命中禁止路径",
  no_commits: "未产生提交",
  provider_capacity: "供应商容量不足",
  stale_attempt: "上一次运行记录过期",
  transient: "Agent 遇到瞬态错误",
  unrecoverable: "错误无法恢复",
  uncommitted_changes: "变更未提交",
  verification_failed: "实现验证未通过",
};

const ATTEMPT_FAILURE_PHASE_LABELS: Record<string, string> = {
  agent: "Agent 执行",
  verification: "实现验证",
  prd_delivery: "PRD 交付门禁",
  evidence: "证据检查",
  rv_reexec: "真实验证复执行",
  verifier: "独立验证",
  commit: "提交检查",
};

const RUN_TRIGGER_LABELS: Record<string, string> = {
  cli_run: "kc run",
  cli_daemon: "kc daemon",
  console_run: "Console 单次启动",
  console_daemon: "Console 守护进程",
};

const ISSUE_KIND_LABELS: Record<string, string> = {
  ready: "常规实现路径（定向启动也会进入此路径）",
  running_rework: "运行中返工路径",
  running_publish_recovery: "已提交变更发布恢复路径",
  blocked_resolution: "解除阻塞恢复路径",
  direct_pr_cleanup: "Direct PR 收尾路径",
};

type ImplementationInvocationContext = {
  callReason: string;
  launcher: string;
  executionPath: string;
  attemptOutcome: string;
  attemptStatusLabel: string | null;
  attemptStatusVariant: BadgeVariant | null;
  failureDetail: string | null;
  failurePhase: string | null;
  failureDetailUnavailable: boolean;
  failureDetailMissing: boolean;
  failureDetailNotQueryable: boolean;
};

/** 按 attempt 事件的精确开始时间关联 SQLite 中保存的诊断详情。 */
function attemptDetailForEvent(
  event: PrdLifecycleEventView | undefined,
  attemptDetails: RunAttemptDetailEntry[],
): RunAttemptDetailEntry | undefined {
  if (!event || typeof event.detail.attempt_number !== "number") {
    return undefined;
  }
  const eventStartedAt = Date.parse(event.occurred_at);
  if (!Number.isFinite(eventStartedAt)) {
    return undefined;
  }
  const eventAgent = event.detail.agent;
  return attemptDetails.find((attemptDetail) =>
    attemptDetail.attempt_number === event.detail.attempt_number &&
    Date.parse(attemptDetail.started_at) === eventStartedAt &&
    (typeof eventAgent !== "string" || attemptDetail.agent === eventAgent),
  );
}

/** 把持久化的失败阶段映射为用户可读名称，未知值保留原文。 */
function readableAttemptFailurePhase(phase: string | null | undefined): string | null {
  if (!phase) {
    return null;
  }
  return ATTEMPT_FAILURE_PHASE_LABELS[phase] ?? phase;
}

/** 为实现进程调用关联调用原因、会话入口、执行路径和 Agent 尝试结论。 */
function implementationInvocationContextOf(
  invocation: PrdInvocationRow,
  lifecycleEvents: PrdLifecycleEventView[],
  attemptDetails: RunAttemptDetailEntry[],
  attemptDetailsStatus: "loading" | "available" | "unavailable" | "not-queryable",
): ImplementationInvocationContext {
  const sessionEvents = lifecycleEvents.filter(
    (event) => event.detail[INVOCATION_RUN_DETAIL_KEY] === invocation.run_id,
  );
  const attemptNumber = invocation.attempt_number;
  const previousAttemptEvent = attemptNumber !== null && attemptNumber > 1
    ? sessionEvents.find(
        (event) =>
          event.event_type === "attempt" &&
          event.detail.attempt_number === attemptNumber - 1,
      )
    : undefined;
  const previousFailureType = previousAttemptEvent?.detail.failure_type;
  const previousAttemptDetail = attemptDetailForEvent(previousAttemptEvent, attemptDetails);
  const previousFailurePhase = readableAttemptFailurePhase(
    previousAttemptDetail?.failure_phase ??
      (typeof previousAttemptEvent?.detail.failure_phase === "string"
        ? previousAttemptEvent.detail.failure_phase
        : null),
  );
  const readablePreviousFailure =
    typeof previousFailureType === "string"
      ? `${ATTEMPT_FAILURE_SOURCE_LABELS[previousFailureType] ?? previousFailureType}（${previousFailureType}）`
      : null;

  let callReason: string;
  if (invocation.retry_of) {
    const retryLabel = invocation.retry_reason
      ? INVOCATION_RETRY_SOURCE_LABELS[invocation.retry_reason] ??
        `上一次调用后重试（${invocation.retry_reason}）`
      : "替代上一次进程调用（重试原因未记录）";
    callReason = retryLabel;
  } else if (attemptNumber !== null && attemptNumber > 1) {
    if (previousFailureType === "success") {
      callReason = `Agent 尝试 #${attemptNumber - 1} 已通过；之后再次调用的原因未记录`;
    } else {
      callReason = readablePreviousFailure
        ? `Agent 尝试 #${attemptNumber - 1} 未通过（${readablePreviousFailure}${previousFailurePhase ? `；${previousFailurePhase}阶段失败` : ""}），触发第 ${attemptNumber} 次恢复调用`
        : `第 ${attemptNumber} 次恢复调用；上一轮失败原因未记录`;
    }
  } else if (attemptNumber === 1) {
    callReason = "本会话 Agent 尝试 #1 的首次进程调用；跨会话的上游原因未记录";
  } else {
    callReason = "未关联 Agent 尝试；本次调用原因未记录";
  }

  const runStartEvent = sessionEvents.find((event) => event.event_type === "started");
  const rawRunTrigger = runStartEvent?.detail.run_trigger;
  const launcher =
    typeof rawRunTrigger === "string"
      ? RUN_TRIGGER_LABELS[rawRunTrigger] ?? `未识别入口（${rawRunTrigger}）`
      : "历史记录未保存发起入口";
  const issueKind = runStartEvent?.detail.issue_kind;
  const executionPath =
    typeof issueKind === "string"
      ? ISSUE_KIND_LABELS[issueKind] ?? `未识别执行路径（${issueKind}）`
      : "历史记录未保存执行路径";

  const currentAttemptEvent = attemptNumber === null
    ? undefined
    : sessionEvents.find(
        (event) =>
          event.event_type === "attempt" &&
          event.detail.attempt_number === attemptNumber,
      );
  const currentFailureType = currentAttemptEvent?.detail.failure_type;
  const currentAttemptDetail = attemptDetailForEvent(currentAttemptEvent, attemptDetails);
  const failurePhase = readableAttemptFailurePhase(
    currentAttemptDetail?.failure_phase ??
      (typeof currentAttemptEvent?.detail.failure_phase === "string"
        ? currentAttemptEvent.detail.failure_phase
        : null),
  );
  const isFailedAttempt = typeof currentFailureType === "string" && currentFailureType !== "success";
  const rawFailureDetail = currentAttemptDetail?.detail ?? currentAttemptEvent?.detail.failure_detail;
  const failureDetail = typeof rawFailureDetail === "string" ? rawFailureDetail.trim() || null : null;
  let attemptOutcome = "所属 Agent 尝试结论未记录";
  let attemptStatusLabel: string | null = null;
  let attemptStatusVariant: BadgeVariant | null = null;
  if (currentFailureType === "success") {
    attemptOutcome = `Agent 尝试 #${attemptNumber} 最终通过实现阶段门禁`;
    attemptStatusLabel = "尝试通过";
    attemptStatusVariant = "ready";
  } else if (typeof currentFailureType === "string") {
    const readableFailure =
      ATTEMPT_FAILURE_SOURCE_LABELS[currentFailureType] ?? currentFailureType;
    attemptOutcome = `Agent 尝试 #${attemptNumber} 未通过（${readableFailure} · ${currentFailureType}${failurePhase ? `；失败阶段：${failurePhase}` : ""}）`;
    attemptStatusLabel = currentFailureType === "agent_error" ? "尝试异常" : "尝试未通过";
    attemptStatusVariant = currentFailureType === "agent_error" ? "error" : "warning";
  }

  return {
    callReason,
    launcher,
    executionPath,
    attemptOutcome,
    attemptStatusLabel,
    attemptStatusVariant,
    failureDetail: isFailedAttempt ? failureDetail : null,
    failurePhase,
    failureDetailUnavailable:
      isFailedAttempt && failureDetail === null && attemptDetailsStatus === "unavailable",
    failureDetailMissing:
      isFailedAttempt && failureDetail === null && attemptDetailsStatus === "available",
    failureDetailNotQueryable:
      isFailedAttempt && failureDetail === null && attemptDetailsStatus === "not-queryable",
  };
}

/**
 * 渲染单个事件行（主事件 + 同秒折叠项）。
 *
 * 主事件的 testid 落在按钮本身（点行即开抽屉），折叠项各自可点开原始 detail；失败事件
 * 的原因直接展示。
 *
 * @param props.row - 折叠后的行。
 * @param props.onSelectEvent - 打开事件抽屉的回调。
 * @param props.isLast - 是否是当前时间线分组中的最后一行。
 * @returns 一行事件内容与时间线标记。
 */
export function EventRowItem({
  row,
  onSelectEvent,
  isLast = false,
}: {
  row: EventRow;
  onSelectEvent: (event: PrdLifecycleEventView) => void;
  isLast?: boolean;
}) {
  const failureSummary = row.primary.detail.error_summary;
  const hasFailureSummary =
    ["failed", "validation_failed", "review_failed"].includes(row.primary.event_type) &&
    typeof failureSummary === "string" &&
    Boolean(failureSummary.trim());
  const markerTone = ["failed", "validation_failed", "review_failed"].includes(
    row.primary.event_type,
  )
    ? "bg-rose-500 ring-rose-200 dark:ring-rose-950"
    : ["blocked", "queued", "retry"].includes(row.primary.event_type)
      ? "bg-amber-500 ring-amber-200 dark:ring-amber-950"
      : "bg-violet-500 ring-violet-200 dark:ring-violet-950";

  return (
    <li className="grid grid-cols-[max-content_1rem_minmax(0,1fr)] items-stretch gap-x-3 py-2.5 text-sm">
      <time className="whitespace-nowrap pt-1 font-mono text-[11px] leading-5 text-slate-500">
        {formatLocalDateTime(row.primary.occurred_at)}
      </time>
      <span className="relative flex justify-center" aria-hidden="true">
        <span
          className={cn(
            "relative z-10 mt-1 size-3 rounded-full border-2 border-white ring-1 dark:border-slate-950",
            markerTone,
          )}
        />
        {!isLast ? (
          <span className="absolute -bottom-3 top-4 w-px bg-slate-200 dark:bg-slate-700" />
        ) : null}
      </span>
      <div className="min-w-0 space-y-1">
        <button
          type="button"
          data-testid={`prd-lifecycle-event-${row.primary.event_type}`}
          onClick={() => onSelectEvent(row.primary)}
          className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-violet-50/70 dark:hover:bg-slate-900"
        >
          <EventBadgePair event={row.primary} />
          <span
            className={cn(
              "min-w-0 text-xs",
              hasFailureSummary
                ? "basis-full whitespace-pre-wrap break-words text-red-800 dark:text-red-200"
                : "flex-1 truncate text-slate-500",
            )}
          >
            {summarizeEventDetail(row.primary)}
          </span>
        </button>
        {row.folded.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 pl-1">
            {row.folded.map((foldedEvent, foldedIndex) => (
              <button
                key={`${foldedEvent.event_type}-${foldedEvent.occurred_at}-${foldedIndex}`}
                type="button"
                data-testid={`prd-lifecycle-event-${foldedEvent.event_type}`}
                onClick={() => onSelectEvent(foldedEvent)}
                className="flex items-center gap-x-2 rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs transition-colors hover:border-violet-200 hover:bg-violet-50 dark:border-slate-700 dark:bg-slate-950 dark:hover:bg-slate-800"
              >
                <span className="text-slate-400">+</span>
                <EventBadgePair event={foldedEvent} inline />
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </li>
  );
}

/** 事件标题与状态徽章。 */
function EventBadgePair({ event, inline = false }: { event: PrdLifecycleEventView; inline?: boolean }) {
  if (isObservationEvent(event.event_type)) {
    return (
      <>
        <span className="font-medium">{EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}</span>
        <Badge variant="default" data-testid={`prd-lifecycle-event-status-${event.event_type}`}>
          观测记录
        </Badge>
        {!inline ? <span className="text-xs text-slate-500">{event.actor}</span> : null}
      </>
    );
  }
  const statusForBadge = event.status && event.status !== "none" ? event.status : null;
  return (
    <>
      <span className="font-medium">{EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}</span>
      {statusForBadge ? (
        <Badge variant={statusBadgeVariant(statusForBadge)} data-testid={`prd-lifecycle-event-status-${event.event_type}`}>
          {eventStatusLabel(statusForBadge)}
        </Badge>
      ) : (
        <Badge variant={phaseBadgeVariant(event.phase)} data-testid={`prd-lifecycle-event-status-${event.event_type}`}>
          {PHASE_LABELS[event.phase] ?? event.phase}
        </Badge>
      )}
      {!inline ? <span className="text-xs text-slate-500">{event.actor}</span> : null}
    </>
  );
}

/**
 * 展开一次的调用行列表，并把实现调用关联到生命周期触发事件和尝试结论。
 *
 * @param props.invocations - 本次展示的子进程调用记录。
 * @param props.lifecycleEvents - 用于精确关联 run 与 Agent 尝试的生命周期事件。
 * @returns 调用行列表。
 */
export function InvocationRows({
  invocations,
  lifecycleEvents,
  attemptDetails,
  attemptDetailsStatus,
}: {
  invocations: PrdInvocationRow[];
  lifecycleEvents: PrdLifecycleEventView[];
  attemptDetails: RunAttemptDetailEntry[];
  attemptDetailsStatus: "loading" | "available" | "unavailable" | "not-queryable";
}) {
  return (
    <ul className="mt-2 space-y-2">
      {invocations.map((invocation, index) => {
        const outcomeBadge = invocationOutcomeBadge(invocation.outcome);
        const tokensSummary = summarizeTokenUsage(invocation.token_usage);
        const isFirstInvocationForAttempt = invocation.attempt_number !== null &&
          !invocations
            .slice(0, index)
            .some((earlier) => earlier.attempt_number === invocation.attempt_number);
        const implementationContext = invocation.phase === "implementation"
          ? implementationInvocationContextOf(
              invocation,
              lifecycleEvents,
              attemptDetails,
              attemptDetailsStatus,
            )
          : null;
        return (
          <li
            key={invocation.invocation_id}
            data-testid="prd-lifecycle-invocation-row"
            className="rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-slate-500">第 {index + 1} 次进程调用</span>
              <span className="font-medium">
                {INVOCATION_PHASE_LABELS[invocation.phase] ?? invocation.phase}
              </span>
              <span className="text-slate-500">
                {invocation.executor || "Agent 未记录"}
                {invocation.role
                  ? ` · ${INVOCATION_ROLE_LABELS[invocation.role] ?? invocation.role}`
                  : ""}
              </span>
              <Badge variant={outcomeBadge.variant}>{outcomeBadge.label}</Badge>
              {implementationContext?.attemptStatusLabel &&
              implementationContext.attemptStatusVariant ? (
                <Badge
                  variant={implementationContext.attemptStatusVariant}
                  data-testid="prd-lifecycle-invocation-attempt-status"
                >
                  {implementationContext.attemptStatusLabel}
                </Badge>
              ) : null}
              {invocation.retry_of ? (
                <span className="text-slate-500" data-testid="prd-lifecycle-invocation-retry-of">
                  替代上一次调用{invocation.retry_reason ? `（${invocation.retry_reason}）` : ""}
                </span>
              ) : null}
            </div>
            <dl className="mt-2 grid gap-x-4 gap-y-1 @5xl/lifecycle:grid-cols-2">
              <FieldRow label="开始">{formatLocalDateTime(invocation.started_at ?? "")}</FieldRow>
              <FieldRow label="结束" testId="prd-lifecycle-invocation-finished">
                {invocation.finished_at ? formatLocalDateTime(invocation.finished_at) : "未闭合"}
              </FieldRow>
              <FieldRow label="耗时" testId="prd-lifecycle-invocation-duration">
                {/* 未闭合调用的 duration 恒为 null：显示未记录，不估算。 */}
                {invocation.duration_seconds === null
                  ? "未记录"
                  : formatLifecycleDuration(invocation.duration_seconds)}
              </FieldRow>
              <FieldRow label="模型">{describeInvocationModel(invocation)}</FieldRow>
              {implementationContext ? (
                <>
                  <FieldRow label="调用原因" testId="prd-lifecycle-invocation-call-reason">
                    {implementationContext.callReason}
                  </FieldRow>
                  <FieldRow label="发起入口" testId="prd-lifecycle-invocation-launcher">
                    {implementationContext.launcher}
                  </FieldRow>
                  <FieldRow label="执行路径" testId="prd-lifecycle-invocation-execution-path">
                    {implementationContext.executionPath}
                  </FieldRow>
                  <FieldRow label="Agent 尝试结论" testId="prd-lifecycle-invocation-attempt-outcome">
                    {implementationContext.attemptOutcome}
                  </FieldRow>
                  {isFirstInvocationForAttempt && implementationContext.failureDetail ? (
                    <FieldRow
                      label={implementationContext.failurePhase
                        ? `${implementationContext.failurePhase}失败原因`
                        : "失败原因"}
                      testId="prd-lifecycle-invocation-attempt-failure-detail"
                    >
                      {implementationContext.failureDetail.length > 480 ? (
                        <div>
                          <p className="whitespace-pre-wrap break-words rounded bg-slate-50 p-2 font-mono text-[11px] font-normal dark:bg-slate-900">
                            {implementationContext.failureDetail.slice(0, 480)}…
                          </p>
                          <details className="mt-1">
                            <summary className="cursor-pointer font-normal text-blue-700 dark:text-blue-300">
                              查看完整失败原因
                            </summary>
                            <p className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 font-mono text-[11px] font-normal dark:bg-slate-900">
                              {implementationContext.failureDetail}
                            </p>
                          </details>
                        </div>
                      ) : (
                        <span className="block max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 font-mono text-[11px] font-normal dark:bg-slate-900">
                          {implementationContext.failureDetail}
                        </span>
                      )}
                    </FieldRow>
                  ) : isFirstInvocationForAttempt && implementationContext.failureDetailUnavailable ? (
                    <FieldRow
                      label="失败原因"
                      testId="prd-lifecycle-invocation-attempt-failure-unavailable"
                    >
                      尝试详情账本读取失败，暂时无法显示具体原因。
                    </FieldRow>
                  ) : isFirstInvocationForAttempt && implementationContext.failureDetailMissing ? (
                    <FieldRow
                      label="失败原因"
                      testId="prd-lifecycle-invocation-attempt-failure-missing"
                    >
                      已读取尝试账本，但没有匹配到这条尝试的详细失败记录。
                    </FieldRow>
                  ) : isFirstInvocationForAttempt && implementationContext.failureDetailNotQueryable ? (
                    <FieldRow
                      label="失败原因"
                      testId="prd-lifecycle-invocation-attempt-failure-not-queryable"
                    >
                      缺少 Issue 或执行时间，无法读取并关联尝试详情。
                    </FieldRow>
                  ) : null}
                </>
              ) : null}
              {invocation.failure_category ? (
                <FieldRow label="失败类别">{invocation.failure_category}</FieldRow>
              ) : null}
              {invocation.exit_code !== null ? (
                <FieldRow label="退出码">{invocation.exit_code}</FieldRow>
              ) : null}
              {tokensSummary ? <FieldRow label="用量">{tokensSummary}</FieldRow> : null}
              {invocation.log_locator ? (
                <FieldRow label="日志">
                  <span className="break-all font-mono">{invocation.log_locator}</span>
                </FieldRow>
              ) : null}
            </dl>
          </li>
        );
      })}
    </ul>
  );
}

/** 调用行里的一个「标签：值」字段。 */
function FieldRow({
  label,
  testId,
  children,
}: {
  label: string;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-2" data-testid={testId}>
      <dt className="shrink-0 text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{children}</dd>
    </div>
  );
}
