"use client";

// PRD「执行过程」视图：先按实现 / 修复 / 收尾 / 校验 / 审核 / 监督展示业务阶段，
// 再提供执行会话 → Agent 尝试 → 调用的完整事件轨迹。
//
// 事实源分工是这里所有判断的前提：
// - 阶段与耗时口径来自后端生命周期账本，前端只做格式化（口径见 prd-lifecycle-format）。
// - 调用序列来自后端 `invocations` 读取区，前端只做归组展示，不按时间窗口猜测归属。
// - 「不可用」「无归属」「已截断」是三类不同的缺口，页面必须分别披露，任何一类都
//   不得降级成「看起来没有发生」的空白。
// - 归属只用显式字段：会话行匹配不到任何已展示尝试的调用（closeout /
//   content_generation 这类账本里本来就没有尝试编号的调用）挂在会话级「其他调用」，
//   会话标识对不上任何分组的调用单独披露条数——一条都不能静默消失。
// - 去噪只作用于展示层：同秒成对事件折叠成一行、重试折叠成尝试行上的标记；被折叠
//   的原始事件仍可单独点开查看原始 detail，账本内容零改写、零删除。

import { useState } from "react";

import {
  formatLifecycleDuration,
  INVOCATION_RUN_DETAIL_KEY,
} from "@/components/backlog/prd-lifecycle-format";
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
import { formatLocalDateTime } from "@/lib/utils";
import type {
  PrdInvocationDetail,
  PrdInvocationRow,
  PrdLifecycleEventView,
} from "@/lib/api/types";


/** 同一秒内成对出现、应折叠成一行的事件类型组（展示层去噪，不删事件）。 */
const SAME_SECOND_MERGE_GROUPS: string[][] = [
  ["started", "claimed"],
  ["attempt", "retry"],
];

/** attempt detail 里表示「这次没失败」的字面值，摘要中不作为失败类别展示。 */
const SUCCESS_FAILURE_TYPE = "success";

/** 一个执行会话分组：会话标识 + 会话级事件行 + 各 Agent 尝试分组。 */
type SessionGroup = {
  runId: string | null;
  firstOccurredAt: string;
  eventRows: EventRow[];
  attempts: AttemptGroup[];
};

/** 一次 Agent 尝试分组：所属事件行 + 该尝试的调用行（展开后可见）。 */
type AttemptGroup = {
  attemptNumber: number;
  eventRows: EventRow[];
  invocations: PrdInvocationRow[];
};

/** 尝试行摘要：只从 attempt 族事件的 detail 取值，缺失的字段不显示。 */
type AttemptSummary = {
  agent: string | null;
  failureType: string | null;
  durationSeconds: number | null;
  recovered: boolean | null;
};

/** 尝试族事件类型——它们带着 attempt detail，是摘要的唯一事实源。 */
const ATTEMPT_FAMILY_EVENT_TYPES = new Set(["attempt", "retry", "recovered"]);

/** 尝试展开区没有调用行时按成因分别说明，不把「无从归属」说成「读取失败」。 */
const ATTEMPT_INVOCATION_EMPTY_TEXT: Record<InvocationGapKind, string> = {
  available: "这次尝试没有可读到的调用记录（调用观测早于本次执行上线，或该阶段未产生进程调用）。",
  unavailable: "调用明细不可用：账本读取失败，展开项无法显示调用序列。",
  absent: "响应未携带调用明细区（旧响应，或该 PRD 没有关联 Issue）：展开项不显示调用序列。",
  truncated: "调用记录已截断：读取只返回最近一段，这次尝试的调用序列可能未被包含。",
};

/**
 * 生成事件行的稳定 key。
 *
 * 同组内同类型同秒的事件是合法的（`event_key` 显式不同时账本会照写），只用
 * `event_type + occurred_at` 会撞 key，因此带上行序号。
 *
 * @param row - 折叠后的事件行。
 * @param index - 行在该分组内的位置。
 * @returns 组内唯一的 React key。
 */
function eventRowKey(row: EventRow, index: number): string {
  return `${row.primary.event_type}-${row.primary.occurred_at}-${index}`;
}

/**
 * 读取一个尝试分组的摘要字段。
 *
 * @param attempt - 尝试分组。
 * @returns agent / 失败类别 / 耗时 / 是否恢复；账本没写的字段返回 ``null``（不猜、不显示占位）。
 */
function attemptSummaryOf(attempt: AttemptGroup): AttemptSummary {
  const sourceEvent = attempt.eventRows
    .flatMap((row) => [row.primary, ...row.folded])
    .find((event) => ATTEMPT_FAMILY_EVENT_TYPES.has(event.event_type));
  const detail = sourceEvent?.detail ?? {};
  const readNumber = (key: string): number | null => {
    const rawValue = detail[key];
    return typeof rawValue === "number" && Number.isFinite(rawValue) ? rawValue : null;
  };
  const readString = (key: string): string | null => {
    const rawValue = detail[key];
    return typeof rawValue === "string" && rawValue ? rawValue : null;
  };
  const rawFailureType = readString("failure_type");
  return {
    agent: readString("agent"),
    failureType:
      rawFailureType === null || rawFailureType === SUCCESS_FAILURE_TYPE ? null : rawFailureType,
    durationSeconds: readNumber("duration_seconds"),
    recovered: typeof detail.recovered === "boolean" ? detail.recovered : null,
  };
}

/** 取得事件所属执行会话标识；旧事件没有该键时为 ``null``（不猜）。 */
function sessionKeyOf(event: PrdLifecycleEventView): string | null {
  const rawRunId = event.detail?.[INVOCATION_RUN_DETAIL_KEY];
  return typeof rawRunId === "string" && rawRunId ? rawRunId : null;
}

/** 取得事件的尝试编号；没有该字段时为 ``null``。 */
function attemptNumberOf(event: PrdLifecycleEventView): number | null {
  const rawAttempt = event.detail?.attempt_number;
  return typeof rawAttempt === "number" && Number.isFinite(rawAttempt) ? rawAttempt : null;
}

/**
 * 把同一秒成对出现的里程碑事件折叠成一行。
 *
 * 只有 ``SAME_SECOND_MERGE_GROUPS`` 声明的组合会被折叠；其余同秒事件各自成行，
 * 因为「无法区分」的问题由会话分组解决，而不是靠合并任意事件。
 *
 * 主事件取组内**声明顺序最靠前**的类型，而不是账本里先写入的那条：`retry` 早于
 * `attempt` 落盘时，行首仍是「尝试」，被折叠的是重试标记——展示口径不能随写入顺序漂移。
 *
 * @param events - 已按发生顺序排列的事件。
 * @returns 折叠后的行集合，行顺序沿用聚类里最早出现的位置。
 */
function foldSameSecondEvents(events: PrdLifecycleEventView[]): EventRow[] {
  const rows: EventRow[] = [];
  const consumed = new Set<number>();
  events.forEach((event, index) => {
    if (consumed.has(index)) {
      return;
    }
    const mergeGroup = SAME_SECOND_MERGE_GROUPS.find((group) => group.includes(event.event_type));
    if (!mergeGroup) {
      consumed.add(index);
      rows.push({ primary: event, folded: [] });
      return;
    }
    const clusterIndices: number[] = [];
    events.forEach((candidate, candidateIndex) => {
      if (
        !consumed.has(candidateIndex) &&
        candidate.occurred_at === event.occurred_at &&
        mergeGroup.includes(candidate.event_type)
      ) {
        consumed.add(candidateIndex);
        clusterIndices.push(candidateIndex);
      }
    });
    const anchorIndex = clusterIndices.reduce((bestIndex, candidateIndex) =>
      mergeGroup.indexOf(events[candidateIndex].event_type) <
      mergeGroup.indexOf(events[bestIndex].event_type)
        ? candidateIndex
        : bestIndex,
    );
    rows.push({
      primary: events[anchorIndex],
      folded: clusterIndices
        .filter((candidateIndex) => candidateIndex !== anchorIndex)
        .map((candidateIndex) => events[candidateIndex]),
    });
  });
  return rows;
}

/**
 * 按「执行会话 → Agent 尝试」两级归组生命周期事件。
 *
 * 分组键只取事件 detail 里的会话标识；任何事件缺少该标识时都进入「未归属」列表，
 * 不做时间窗口推断。返回 ``null`` 表示整条时间线都没有会话标识（旧数据），
 * 调用方据此回落到平铺展示。
 *
 * @param events - 已按发生顺序排列的事件。
 * @returns 会话分组列表，以及无法归属到任何会话的事件行。
 */
function groupSessions(events: PrdLifecycleEventView[]): {
  sessions: SessionGroup[];
  unattributedRows: EventRow[];
} {
  const sessions: SessionGroup[] = [];
  const unattributed: PrdLifecycleEventView[] = [];
  events.forEach((event) => {
    const runId = sessionKeyOf(event);
    if (runId === null) {
      unattributed.push(event);
      return;
    }
    const existingSession = sessions.find((session) => session.runId === runId);
    const targetSession = existingSession ?? {
      runId,
      firstOccurredAt: event.occurred_at,
      eventRows: [],
      attempts: [],
    };
    if (!existingSession) {
      sessions.push(targetSession);
    }
    const attemptNumber = attemptNumberOf(event);
    if (attemptNumber === null) {
      targetSession.eventRows.push({ primary: event, folded: [] });
      return;
    }
    const existingAttempt = targetSession.attempts.find(
      (attempt) => attempt.attemptNumber === attemptNumber,
    );
    const targetAttempt = existingAttempt ?? {
      attemptNumber,
      eventRows: [],
      invocations: [],
    };
    if (!existingAttempt) {
      targetSession.attempts.push(targetAttempt);
    }
    targetAttempt.eventRows.push({ primary: event, folded: [] });
  });

  // 折叠只发生在同一分组内部：跨会话/跨尝试的同秒事件不属于「重复对」。
  const foldedSessions = sessions.map((session) => ({
    ...session,
    eventRows: foldSameSecondEvents(session.eventRows.map((row) => row.primary)),
    attempts: session.attempts.map((attempt) => ({
      ...attempt,
      eventRows: foldSameSecondEvents(attempt.eventRows.map((row) => row.primary)),
    })),
  }));
  return {
    sessions: foldedSessions,
    unattributedRows: foldSameSecondEvents(unattributed),
  };
}

/**
 * 把尝试摘要拼成一行文本。
 *
 * 账本没写的字段直接不出现，整条摘要为空时返回 ``null``，避免「undefined · —」这类
 * 把缺数据伪装成事实的排版。
 *
 * @param summary - 尝试摘要。
 * @returns ``agent · 失败类别 … · 耗时 … · 已恢复`` 形态的一行文本，全空时为 ``null``。
 */
function attemptSummaryParts(summary: AttemptSummary): string | null {
  const parts: string[] = [];
  if (summary.agent) {
    parts.push(summary.agent);
  }
  if (summary.failureType) {
    parts.push(`失败类别 ${summary.failureType}`);
  }
  if (summary.durationSeconds !== null) {
    parts.push(`耗时 ${formatLifecycleDuration(summary.durationSeconds)}`);
  }
  if (summary.recovered !== null) {
    parts.push(summary.recovered ? "已恢复" : "未恢复");
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * 会话级「其他调用」：账本里存在、但不属于任何已展示尝试的调用行。
 *
 * `closeout` / `content_generation` 这类调用本来就没有尝试编号，验证或收尾调用也
 * 可能落在没有事件的历史尝试上。它们同样是已落库事实，收在这里而不是让它们消失。
 *
 * @param props.invocations - 该会话内未关联到尝试的调用行（顺序即账本顺序）。
 * @param props.lifecycleEvents - 用于关联实现调用来源和尝试结论的会话事件。
 * @returns 可展开的调用行区块。
 */
function OtherInvocationBlock({
  invocations,
  lifecycleEvents,
  attemptDetails,
  attemptDetailsStatus,
}: {
  invocations: PrdInvocationRow[];
  lifecycleEvents: PrdLifecycleEventView[];
  attemptDetails: PrdLifecycleDisplayProps["attemptDetails"];
  attemptDetailsStatus: PrdLifecycleDisplayProps["attemptDetailsStatus"];
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className="mt-2 rounded-md border border-dashed border-slate-300 px-3 dark:border-slate-700"
      data-testid="prd-lifecycle-other-invocations"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 py-2">
        <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
          其他调用（不属于任何已展示的 Agent 尝试）
        </p>
        <button
          type="button"
          aria-expanded={expanded}
          data-testid="prd-lifecycle-other-invocations-toggle"
          onClick={() => setExpanded((current) => !current)}
          className="text-xs text-blue-700 underline underline-offset-2 hover:text-blue-900 dark:text-blue-300"
        >
          {expanded ? "收起调用序列" : `展开调用序列（${invocations.length} 次）`}
        </button>
      </div>
      {expanded ? (
        <InvocationRows
          invocations={invocations}
          lifecycleEvents={lifecycleEvents}
          attemptDetails={attemptDetails}
          attemptDetailsStatus={attemptDetailsStatus}
        />
      ) : null}
    </div>
  );
}

/**
 * 一个 Agent 尝试分组：尝试事件行 + 可展开的调用序列。
 *
 * @param props.attempt - 尝试分组。
 * @param props.lifecycleEvents - 用于关联实现调用来源和尝试结论的会话事件。
 * @param props.invocationGap - 调用明细区的缺口成因，决定展开区空态怎么说。
 * @param props.onSelectEvent - 打开事件抽屉的回调。
 */
function AttemptGroupBlock({
  attempt,
  lifecycleEvents,
  invocationGap,
  onSelectEvent,
  attemptDetails,
  attemptDetailsStatus,
}: {
  attempt: AttemptGroup;
  lifecycleEvents: PrdLifecycleEventView[];
  invocationGap: InvocationGapKind;
  onSelectEvent: (event: PrdLifecycleEventView) => void;
  attemptDetails: PrdLifecycleDisplayProps["attemptDetails"];
  attemptDetailsStatus: PrdLifecycleDisplayProps["attemptDetailsStatus"];
}) {
  const [expanded, setExpanded] = useState(false);
  const invocationCount = attempt.invocations.length;
  const summary = attemptSummaryOf(attempt);
  return (
    <div
      className="ml-3 mt-2 rounded-lg border border-slate-200 bg-white px-3 dark:border-slate-800 dark:bg-slate-950"
      data-testid="prd-lifecycle-attempt-group"
      data-attempt-number={attempt.attemptNumber}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
            Agent 尝试 #{attempt.attemptNumber}
          </p>
          <span className="text-xs text-slate-500" data-testid="prd-lifecycle-attempt-summary">
            {attemptSummaryParts(summary)}
          </span>
        </div>
        <button
          type="button"
          aria-expanded={expanded}
          data-testid="prd-lifecycle-attempt-toggle"
          onClick={() => setExpanded((current) => !current)}
          className="rounded-md border border-violet-200 bg-violet-50 px-2.5 py-1.5 text-xs font-medium text-violet-800 transition-colors hover:bg-violet-100 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-200 dark:hover:bg-violet-900/50"
        >
          {expanded ? "收起调用序列" : `展开调用序列（${invocationCount} 次）`}
        </button>
      </div>
      <ul data-testid="prd-lifecycle-attempt-events">
        {attempt.eventRows.map((row, index) => (
          <EventRowItem
            key={eventRowKey(row, index)}
            row={row}
            onSelectEvent={onSelectEvent}
            isLast={index === attempt.eventRows.length - 1}
          />
        ))}
      </ul>
      {expanded ? (
        <div className="pb-3">
          {invocationCount > 0 ? (
            <InvocationRows
              invocations={attempt.invocations}
              lifecycleEvents={lifecycleEvents}
              attemptDetails={attemptDetails}
              attemptDetailsStatus={attemptDetailsStatus}
            />
          ) : (
            <p className="mt-2 rounded border border-dashed border-slate-300 p-2 text-xs text-slate-500 dark:border-slate-700" data-testid="prd-lifecycle-invocation-empty">
              {ATTEMPT_INVOCATION_EMPTY_TEXT[invocationGap]}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * 生命周期时间线：按执行会话分组，尝试可展开调用序列，同秒重复行折叠。
 *
 * @param props.events - 后端返回的生命周期事件（已按时间排序）。
 * @param props.invocations - 调用明细读取区；``null``/``undefined`` 表示响应不含该区域。
 * @param props.onSelectEvent - 打开事件抽屉的回调。
 * @returns 时间线区块，含三类缺口的显式披露。
 */
export function PrdLifecycleTimeline({
  events,
  invocations,
  attemptDetails,
  attemptDetailsStatus,
  onSelectEvent,
}: PrdLifecycleDisplayProps) {
  const invocationGap = invocationGapKindOf(invocations);
  const invocationRows = invocations?.rows ?? [];
  if (events.length === 0 && invocationRows.length === 0) {
    return (
      <p className="text-xs text-slate-500" data-testid="prd-lifecycle-timeline-empty">
        暂无生命周期事件。
      </p>
    );
  }

  const { sessions, unattributedRows } = groupSessions(events);
  const sessionRunIds = new Set(sessions.map((session) => session.runId));
  // 调用行带着会话标识，但对应执行会话的生命周期事件没有该标识（旧执行）：这类行
  // 不进任何分组，只披露条数——按时间窗口把它们塞进某个会话就是编造关联。
  const unmatchedInvocationCount = invocationRows.filter(
    (invocation) => !sessionRunIds.has(invocation.run_id),
  ).length;

  return (
    <div className="space-y-3">
      <InvocationGapNotice
        invocations={invocations}
        unattributedCount={unattributedRows.length}
        allUnattributed={sessions.length === 0}
        unmatchedInvocationCount={unmatchedInvocationCount}
      />
      {sessions.length === 0 ? (
        <ul
          data-testid="prd-lifecycle-flat-events"
          className="rounded-xl border border-slate-200 bg-white px-3 dark:border-slate-800 dark:bg-slate-950"
        >
          {unattributedRows.map((row, index) => (
            <EventRowItem
              key={eventRowKey(row, index)}
              row={row}
              onSelectEvent={onSelectEvent}
              isLast={index === unattributedRows.length - 1}
            />
          ))}
        </ul>
      ) : (
        <div className="space-y-3">
          {sessions.map((session) => {
            const sessionInvocationRows = invocationRows.filter(
              (invocation) => invocation.run_id === session.runId,
            );
            const attemptNumbers = new Set(
              session.attempts.map((attempt) => attempt.attemptNumber),
            );
            const otherInvocationRows = sessionInvocationRows.filter(
              (invocation) =>
                invocation.attempt_number === null ||
                !attemptNumbers.has(invocation.attempt_number),
            );
            return (
              <section
                key={session.runId ?? session.firstOccurredAt}
                data-testid="prd-lifecycle-session-group"
                className="rounded-xl border border-violet-200/80 bg-violet-50/30 px-4 pb-3 shadow-sm dark:border-violet-900/70 dark:bg-violet-950/10"
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-violet-100 py-2 text-xs text-slate-500 dark:border-violet-950">
                  <span className="font-semibold text-slate-800 dark:text-slate-100">执行会话</span>
                  <span className="break-all font-mono text-violet-700 dark:text-violet-300">
                    {session.runId}
                  </span>
                  <span>开始：{formatLocalDateTime(session.firstOccurredAt)}</span>
                </div>
                <ul data-testid="prd-lifecycle-session-events">
                  {session.eventRows.map((row, index) => (
                    <EventRowItem
                      key={eventRowKey(row, index)}
                      row={row}
                      onSelectEvent={onSelectEvent}
                      isLast={index === session.eventRows.length - 1}
                    />
                  ))}
                </ul>
                {session.attempts.map((attempt) => (
                  <AttemptGroupBlock
                    key={`${session.runId}-attempt-${attempt.attemptNumber}`}
                    attempt={{
                      ...attempt,
                      invocations: sessionInvocationRows.filter(
                        (invocation) => invocation.attempt_number === attempt.attemptNumber,
                      ),
                    }}
                    lifecycleEvents={events}
                    invocationGap={invocationGap}
                    onSelectEvent={onSelectEvent}
                    attemptDetails={attemptDetails}
                    attemptDetailsStatus={attemptDetailsStatus}
                  />
                ))}
                {otherInvocationRows.length > 0 ? (
                  <OtherInvocationBlock
                    invocations={otherInvocationRows}
                    lifecycleEvents={events}
                    attemptDetails={attemptDetails}
                    attemptDetailsStatus={attemptDetailsStatus}
                  />
                ) : null}
              </section>
            );
          })}
          {unattributedRows.length > 0 ? (
            <div data-testid="prd-lifecycle-unattributed-section">
              <p className="text-xs text-slate-500">以下事件没有执行会话标识，保持原顺序平铺：</p>
              <ul
                data-testid="prd-lifecycle-unattributed-events"
                className="mt-2 rounded-xl border border-slate-200 bg-white px-3 dark:border-slate-800 dark:bg-slate-950"
              >
                {unattributedRows.map((row, index) => (
                  <EventRowItem
                    key={`unattributed-${eventRowKey(row, index)}`}
                    row={row}
                    onSelectEvent={onSelectEvent}
                    isLast={index === unattributedRows.length - 1}
                  />
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/**
 * 调用明细区缺口披露：不可用 / 无归属 / 截断分别成句。
 *
 * 三类缺口说的是不同的事实（读不到、没法归属、只读到一部分），合并成一句会让
 * 读的人无法判断该去修哪里；任何一类都只披露，不吞掉时间线的其余渲染。
 *
 * @param props.invocations - 调用明细读取区；缺失表示响应未带该区域（旧响应或未关联 Issue）。
 * @param props.unattributedCount - 没有执行会话标识的事件行数（回落平铺的披露依据）。
 * @param props.allUnattributed - 是否整条时间线都没有会话标识（旧记录口径）。
 * @param props.unmatchedInvocationCount - 账本里有、但所属执行会话在时间线上没有标识的调用行数。
 * @returns 披露文案区块；数据齐备时返回 ``null``。
 */
function InvocationGapNotice({
  invocations,
  unattributedCount,
  allUnattributed,
  unmatchedInvocationCount,
}: {
  invocations: PrdInvocationDetail | null | undefined;
  unattributedCount: number;
  allUnattributed: boolean;
  unmatchedInvocationCount: number;
}) {
  const notices: string[] = [];
  if (invocations === null || invocations === undefined) {
    notices.push(
      "响应未携带调用明细区（旧记录，或该 PRD 没有关联 Issue）：不显示调用序列，也不按时间窗口猜测归属。",
    );
  } else if (!invocations.available) {
    notices.push("调用明细不可用：调用账本读取失败，展开项不含调用序列；时间线其余部分照常显示。");
  } else if (invocations.rows.length === 0) {
    notices.push("调用账本可读，但没有本 PRD 的调用记录（早于调用观测上线的旧执行）。");
  }
  if (invocations?.truncated) {
    notices.push("调用记录已截断：读取已达账本条数上限，展开项只显示其中一部分。");
  }
  if (unattributedCount > 0) {
    notices.push(
      allUnattributed
        ? "这些事件都没有执行会话标识（旧记录），已按原顺序平铺展示，未并入任何会话。"
        : `其中 ${unattributedCount} 条事件没有执行会话标识，已按原顺序平铺展示，未并入任何会话。`,
    );
  }
  if (unmatchedInvocationCount > 0) {
    notices.push(
      `另有 ${unmatchedInvocationCount} 条调用记录的执行会话没有对应事件标识；它们已按明确的调用 phase 列在上方阶段总览，不并入下方任何事件会话。`,
    );
  }
  if (notices.length === 0) {
    return null;
  }
  return (
    <div
      role="note"
      data-testid="prd-lifecycle-invocation-notice"
      className="rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
    >
      {notices.map((notice) => (
        <p key={notice}>{notice}</p>
      ))}
    </div>
  );
}
