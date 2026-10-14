"use client";

/**
 * Backlog PRD 业务阶段总览。
 *
 * 固定展示六类生命周期阶段；只按明确的事件类型和调用 phase 归类，原始事件与尝试
 * 仍由完整时间线组件展示。
 */

import {
  EventRowItem,
  invocationGapKindOf,
  InvocationRows,
} from "@/components/backlog/prd-lifecycle-row-components";
import type {
  EventRow,
  InvocationGapKind,
  PrdLifecycleDisplayProps,
} from "@/components/backlog/prd-lifecycle-row-components";
import { Badge } from "@/components/ui/badge";
import type { PrdInvocationRow, PrdLifecycleEventView } from "@/lib/api/types";

type LifecycleStageKey =
  | "implementation"
  | "fix"
  | "closeout"
  | "verification"
  | "review"
  | "supervisor";

type LifecycleStageDefinition = {
  key: LifecycleStageKey;
  label: string;
  trigger: string;
  action: string;
};

/** 用户可见的业务阶段顺序与说明。 */
const LIFECYCLE_STAGE_DEFINITIONS: LifecycleStageDefinition[] = [
  {
    key: "implementation",
    label: "实现",
    trigger: "runner / daemon 认领 Issue 后",
    action: "在隔离 worktree 中按 Issue 和 PRD 完成代码修改。",
  },
  {
    key: "fix",
    label: "修复",
    trigger: "验证、审查或同步分支发现问题后",
    action: "针对失败结果修正代码或处理冲突。",
  },
  {
    key: "closeout",
    label: "收尾",
    trigger: "交付门禁失败时",
    action: "补齐交付清单、变更记录和证据等交付项。",
  },
  {
    key: "verification",
    label: "校验",
    trigger: "发布前验证阶段",
    action: "独立核验实现结果与验证证据。",
  },
  {
    key: "review",
    label: "审核",
    trigger: "创建 Draft PR 前",
    action: "检查代码 findings，并推动问题收敛。",
  },
  {
    key: "supervisor",
    label: "监督",
    trigger: "Draft PR 创建后，或显式运行 kc supervise",
    action: "持续检查 PR、CI 与讨论状态，决定后续处理。",
  },
];

/** 调用账本 phase -> 业务阶段；恢复调用明确归入「修复」。 */
const INVOCATION_STAGE_BY_PHASE = new Map<string, LifecycleStageKey>([
  ["implementation", "implementation"],
  ["fix", "fix"],
  ["verification_recovery", "fix"],
  ["review_repair", "fix"],
  ["rebase_recovery", "fix"],
  ["supervisor_repair", "fix"],
  ["closeout", "closeout"],
  ["verification", "verification"],
  ["review", "review"],
  ["supervisor", "supervisor"],
]);

/** 粗粒度生命周期阶段到业务阶段的明确映射。 */
const LIFECYCLE_PHASE_STAGE_BY_PHASE = new Map<string, LifecycleStageKey>([
  ["queued", "implementation"],
  ["executing", "implementation"],
  ["validating", "verification"],
  ["merging", "supervisor"],
]);

/** 仅将明确表达阶段的生命周期里程碑放入阶段卡片。 */
const LIFECYCLE_EVENT_STAGE_BY_TYPE = new Map<string, LifecycleStageKey>([
  ["implementation_completed", "implementation"],
  ["validation_started", "verification"],
  ["validation_passed", "verification"],
  ["validation_failed", "verification"],
  ["review_started", "review"],
  ["review_passed", "review"],
  ["review_failed", "review"],
  ["merge_started", "supervisor"],
  ["merged", "supervisor"],
  ["archived", "supervisor"],
]);

/** 一个业务阶段；标题与触发条件常驻可见，具体记录可展开。 */
function LifecycleStageBlock({
  stage,
  events,
  invocations,
  lifecycleEvents,
  invocationGap,
  onSelectEvent,
  attemptDetails,
  attemptDetailsStatus,
  isCurrentStage,
}: {
  stage: LifecycleStageDefinition;
  events: EventRow[];
  invocations: PrdInvocationRow[];
  lifecycleEvents: PrdLifecycleEventView[];
  invocationGap: InvocationGapKind;
  onSelectEvent: (event: PrdLifecycleEventView) => void;
  attemptDetails: PrdLifecycleDisplayProps["attemptDetails"];
  attemptDetailsStatus: PrdLifecycleDisplayProps["attemptDetailsStatus"];
  isCurrentStage: boolean;
}) {
  const failedInvocations = invocations.filter((invocation) => invocation.outcome !== "ok").length;
  const hasStageFailure = events.some((row) =>
    ["failed", "validation_failed", "review_failed"].includes(row.primary.event_type),
  );
  const stageSummaryText = invocations.length > 0
    ? `${invocations.length} 次进程调用${failedInvocations > 0 ? ` · ${failedInvocations} 次异常或未闭合` : ""}`
    : events.length > 0
      ? `${events.length} 条阶段事件`
      : invocationGap === "available"
        ? "暂无阶段记录"
        : "暂无可用调用记录";
  const stageBadgeVariant = failedInvocations > 0 || hasStageFailure
    ? "error"
    : invocations.length > 0 || events.length > 0
      ? "ready"
      : "default";
  const stageIndex = LIFECYCLE_STAGE_DEFINITIONS.findIndex(
    (candidate) => candidate.key === stage.key,
  );

  return (
    <li
      data-testid={`prd-lifecycle-stage-${stage.key}`}
      data-current={isCurrentStage ? "true" : undefined}
      aria-current={isCurrentStage ? "step" : undefined}
      className="grid grid-cols-[1.75rem_minmax(0,1fr)] items-stretch gap-2"
    >
      <span aria-hidden="true" className="relative flex justify-center">
        {stageIndex < LIFECYCLE_STAGE_DEFINITIONS.length - 1 ? (
          <span className="absolute bottom-[-1.75rem] left-1/2 top-5 w-px -translate-x-1/2 bg-violet-200 dark:bg-violet-800" />
        ) : null}
        <span
          className={`relative z-10 mt-1.5 flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
            isCurrentStage
              ? "bg-violet-600 text-white ring-4 ring-violet-100 dark:ring-violet-950"
              : "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-200"
          }`}
        >
          {String(stageIndex + 1).padStart(2, "0")}
        </span>
      </span>
      <details
        open={hasStageFailure}
        className={`group min-w-0 rounded-lg border px-3 ${
          isCurrentStage
            ? "border-violet-300 bg-violet-50/50 dark:border-violet-800 dark:bg-violet-950/30"
            : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950"
        }`}
      >
        <summary className="grid min-w-0 cursor-pointer list-none grid-cols-[minmax(0,1fr)] gap-1.5 py-1.5 @5xl/lifecycle:grid-cols-[minmax(9rem,0.75fr)_minmax(13rem,1fr)_minmax(16rem,1.5fr)_auto] @5xl/lifecycle:items-center @5xl/lifecycle:gap-3">
          <span className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
            {stage.label}
            {isCurrentStage ? (
              <Badge variant="running" data-testid="prd-lifecycle-current-stage-marker">
                流程位置
              </Badge>
            ) : null}
          </span>
          <span className="min-w-0 break-words text-xs leading-5 text-slate-600 dark:text-slate-300">
            <span className="font-medium text-slate-400 @5xl/lifecycle:hidden">触发时机：</span>
            {stage.trigger}
          </span>
          <span className="min-w-0 break-words text-xs leading-5 text-slate-600 dark:text-slate-300">
            <span className="font-medium text-slate-400 @5xl/lifecycle:hidden">做什么：</span>
            {stage.action}
          </span>
          <span className="flex min-w-0 flex-wrap items-center gap-1 @5xl/lifecycle:justify-end">
            <Badge variant={stageBadgeVariant} className="whitespace-nowrap">
              {stageSummaryText}
            </Badge>
            <span
              aria-hidden="true"
              className="text-xs text-slate-400 transition-transform group-open:rotate-180"
            >
              ⌄
            </span>
          </span>
        </summary>
        {events.length > 0 || invocations.length > 0 ? (
          <div className="border-t border-slate-100 pb-3 pt-1 dark:border-slate-800">
            {events.length > 0 ? (
              <ul data-testid={`prd-lifecycle-stage-events-${stage.key}`}>
                {events.map((row, index) => (
                  <EventRowItem
                    key={`${row.primary.event_type}-${row.primary.occurred_at}-${index}`}
                    row={row}
                    onSelectEvent={onSelectEvent}
                    isLast={index === events.length - 1 && invocations.length === 0}
                  />
                ))}
              </ul>
            ) : null}
            {invocations.length > 0 ? (
              <InvocationRows
                invocations={invocations}
                lifecycleEvents={lifecycleEvents}
                attemptDetails={attemptDetails}
                attemptDetailsStatus={attemptDetailsStatus}
              />
            ) : null}
          </div>
        ) : (
          <p className="border-t border-slate-100 py-2 text-xs text-slate-500 dark:border-slate-800">
            {invocationGap === "truncated"
              ? "读取已截断；当前范围内没有该阶段记录。"
              : invocationGap === "unavailable" || invocationGap === "absent"
                ? "调用明细不可用或未提供；无法确认该阶段是否有调用。"
                : "当前账本没有该阶段的调用或里程碑记录。"}
          </p>
        )}
      </details>
    </li>
  );
}

/**
 * 渲染 PRD 的六类业务生命周期阶段。
 *
 * @param props.events - 后端返回的生命周期事件。
 * @param props.invocations - PRD 调用明细读取区。
 * @param props.currentPhase - 后端聚合出的粗粒度当前阶段。
 * @param props.inProgress - 当前生命周期 run 是否仍在进行。
 * @param props.startedAt - 当前生命周期 run 的开始时间，用于排除旧调用。
 * @param props.onSelectEvent - 打开事件详情抽屉的回调。
 * @returns 固定阶段顺序的主视图，以及带原因的失败事件摘要。
 */
export function PrdLifecycleStageTimeline({
  events,
  invocations,
  attemptDetails,
  attemptDetailsStatus,
  currentPhase,
  inProgress,
  startedAt,
  onSelectEvent,
}: PrdLifecycleDisplayProps & {
  currentPhase: string;
  inProgress: boolean;
  startedAt: string | null;
}) {
  const invocationGap = invocationGapKindOf(invocations);
  const invocationRows = invocations?.rows ?? [];
  const runStartedAtMs = startedAt === null ? Number.NaN : Date.parse(startedAt);
  const currentRunInvocations = inProgress &&
      invocations !== null &&
      invocations !== undefined &&
      invocations.available &&
      !invocations.truncated &&
      Number.isFinite(runStartedAtMs)
    ? invocationRows
      .filter((invocation) => {
        const invocationStartedAtMs = invocation.started_at === null
          ? Number.NaN
          : Date.parse(invocation.started_at);
        return Number.isFinite(invocationStartedAtMs) && invocationStartedAtMs >= runStartedAtMs;
      })
      .sort((leftInvocation, rightInvocation) =>
        Date.parse(leftInvocation.started_at ?? "") - Date.parse(rightInvocation.started_at ?? ""),
      )
    : [];
  const latestCurrentRunInvocation = currentRunInvocations[currentRunInvocations.length - 1];
  const latestInvocationStage = latestCurrentRunInvocation
    ? INVOCATION_STAGE_BY_PHASE.get(latestCurrentRunInvocation.phase)
    : undefined;
  const lifecycleStageKeys = events
    .map((event) => LIFECYCLE_EVENT_STAGE_BY_TYPE.get(event.event_type))
    .filter((stageKey): stageKey is LifecycleStageKey => stageKey !== undefined);
  const observedStageKeys = [
    ...currentRunInvocations
      .map((invocation) => INVOCATION_STAGE_BY_PHASE.get(invocation.phase))
      .filter((stageKey): stageKey is LifecycleStageKey => stageKey !== undefined),
    ...lifecycleStageKeys,
  ];
  const coarseStageKey = LIFECYCLE_PHASE_STAGE_BY_PHASE.get(currentPhase);
  const invocationHistoryComplete = invocations?.available === true && !invocations.truncated;
  if (coarseStageKey !== undefined &&
      (currentPhase !== "executing" || invocationHistoryComplete)) {
    observedStageKeys.push(coarseStageKey);
  }
  const furthestStageIndex = observedStageKeys.reduce((furthestIndex, stageKey) =>
    Math.max(
      furthestIndex,
      LIFECYCLE_STAGE_DEFINITIONS.findIndex((stage) => stage.key === stageKey),
    ), -1);
  const currentStageKey = inProgress && furthestStageIndex >= 0
    ? LIFECYCLE_STAGE_DEFINITIONS[furthestStageIndex].key
    : undefined;
  const currentStageIndex = currentStageKey === undefined
    ? -1
    : LIFECYCLE_STAGE_DEFINITIONS.findIndex((stage) => stage.key === currentStageKey);
  const currentStageDefinition = currentStageIndex < 0
    ? undefined
    : LIFECYCLE_STAGE_DEFINITIONS[currentStageIndex];
  const latestInvocationStageDefinition = latestInvocationStage === undefined
    ? undefined
    : LIFECYCLE_STAGE_DEFINITIONS.find((stage) => stage.key === latestInvocationStage);
  const unclassifiedInvocations = invocationRows.filter(
    (invocation) => !INVOCATION_STAGE_BY_PHASE.has(invocation.phase),
  );
  const failedEvents = events.filter((event) =>
    event.event_type === "failed" &&
    typeof event.detail.error_summary === "string" &&
    Boolean(event.detail.error_summary.trim()),
  );
  const invocationNotices: string[] = [];
  if (invocations === null || invocations === undefined) {
    invocationNotices.push("本次响应未提供调用明细；阶段卡片仍会展示已记录的生命周期里程碑。");
  } else if (!invocations.available) {
    invocationNotices.push("调用账本不可用；阶段调用次数无法确认。");
  } else if (invocations.rows.length === 0) {
    invocationNotices.push("调用账本可读，但没有本 PRD 的调用记录。");
  }
  if (invocations?.truncated) {
    invocationNotices.push("调用记录已截断；阶段调用展示仅覆盖当前返回范围。");
  }

  return (
    <section
      data-testid="prd-lifecycle-stage-timeline"
      className="space-y-2"
      aria-label="PRD 业务生命周期阶段"
    >
      <div>
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          业务生命周期阶段
        </h3>
        <p className="mt-1 text-xs text-slate-500">
          按调用账本 phase 和明确的里程碑事件归类；没有记录的阶段仍列出，不按时间推测。
          {currentStageDefinition ? (
            <span data-testid="prd-lifecycle-current-stage-summary">
              {` 流程已推进至第 ${currentStageIndex + 1}/${LIFECYCLE_STAGE_DEFINITIONS.length} 步：${currentStageDefinition.label}。该位置只前进不回退；修复、收尾等阶段按触发条件进入。`}
              {latestInvocationStageDefinition &&
                  latestInvocationStage !== currentStageKey ? (
                ` 最近一次调用记录属于「${latestInvocationStageDefinition.label}」，按调用阶段单独归档。`
              ) : null}
            </span>
          ) : null}
        </p>
      </div>
      {invocationNotices.length > 0 ? (
        <div
          role="note"
          data-testid="prd-lifecycle-stage-invocation-notice"
          className="rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
        >
          {invocationNotices.join("")}
        </div>
      ) : null}
      <div
          className="ml-9 hidden grid-cols-[minmax(9rem,0.75fr)_minmax(13rem,1fr)_minmax(16rem,1.5fr)_auto] gap-3 px-3 text-[11px] font-medium text-slate-400 @5xl/lifecycle:grid"
        aria-hidden="true"
      >
        <span>生命周期阶段</span>
        <span>触发时机</span>
        <span>做什么</span>
        <span className="text-right">本 PRD 记录</span>
      </div>
      <ol
        aria-label="按业务顺序排列的六个生命周期阶段"
        className="space-y-2"
        data-testid="prd-lifecycle-stage-list"
      >
        {LIFECYCLE_STAGE_DEFINITIONS.map((stage) => (
          <LifecycleStageBlock
            key={stage.key}
            stage={stage}
            events={events
              .filter((event) => LIFECYCLE_EVENT_STAGE_BY_TYPE.get(event.event_type) === stage.key)
              .map((event) => ({ primary: event, folded: [] }))}
            invocations={invocationRows.filter(
              (invocation) => INVOCATION_STAGE_BY_PHASE.get(invocation.phase) === stage.key,
            )}
            lifecycleEvents={events}
            invocationGap={invocationGap}
            onSelectEvent={onSelectEvent}
            attemptDetails={attemptDetails}
            attemptDetailsStatus={attemptDetailsStatus}
            isCurrentStage={stage.key === currentStageKey}
          />
        ))}
      </ol>
      {unclassifiedInvocations.length > 0 ? (
        <details
          data-testid="prd-lifecycle-stage-unclassified-invocations"
          className="rounded-lg border border-dashed border-slate-300 px-3 dark:border-slate-700"
        >
          <summary className="cursor-pointer py-2 text-xs font-medium text-slate-600 dark:text-slate-300">
            其他 / 未映射阶段调用（{unclassifiedInvocations.length} 次）
          </summary>
          <InvocationRows
            invocations={unclassifiedInvocations}
            lifecycleEvents={events}
            attemptDetails={attemptDetails}
            attemptDetailsStatus={attemptDetailsStatus}
          />
        </details>
      ) : null}
      {failedEvents.length > 0 ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50/60 px-3 dark:border-rose-900/70 dark:bg-rose-950/20">
          <p className="pt-2 text-xs font-semibold text-rose-800 dark:text-rose-200">
            失败原因（账本未标明具体业务阶段）
          </p>
          <ul data-testid="prd-lifecycle-stage-unassigned-failures">
            {failedEvents.map((event, index) => (
              <EventRowItem
                key={`${event.event_type}-${event.occurred_at}-${index}`}
                row={{ primary: event, folded: [] }}
                onSelectEvent={onSelectEvent}
                isLast={index === failedEvents.length - 1}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
