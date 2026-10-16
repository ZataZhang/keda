"use client";

// PRD 生命周期视图：展示当前阶段、耗时拆分与追加事件时间线。
//
// 事实源是后端按 `repo_id + prd_path` 聚合出的 lifecycle 详情——前端只做格式
// 化，不重新定义端到端/执行/等待/阻塞口径。`has_data=false` 表示尚无执行记录，
// 必须渲染明确空态；`history_complete=false` 表示观测账本自身有缺口，必须显式
// 告警而不是假装数据完整。时间线的分组、去噪与调用明细披露在
// `prd-lifecycle-timeline` 里，本文件只负责取数、指标区与事件抽屉。

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import {
  EVENT_TYPE_LABELS,
  eventStatusLabel,
  formatDetailValue,
  formatLifecycleDuration,
  PHASE_LABELS,
  phaseBadgeVariant,
} from "@/components/backlog/prd-lifecycle-format";
import { PrdLifecycleTimeline } from "@/components/backlog/prd-lifecycle-timeline";
import { PrdLifecycleStageTimeline } from "@/components/backlog/prd-lifecycle-stage-timeline";
import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchPrdLifecycle } from "@/lib/api/backlog";
import { fetchRunExecutionDetails } from "@/lib/api/console";
import { cn, formatLocalDateTime } from "@/lib/utils";
import type {
  PrdLifecycleDetail,
  PrdLifecycleEventView,
  RunAttemptDetailEntry,
} from "@/lib/api/types";

/** 生命周期 run 结果 -> 中文标签。 */
const OUTCOME_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  blocked: "阻塞",
};

type LifecycleState =
  | { status: "loading"; requestKey: string }
  | { status: "ready"; requestKey: string; detail: PrdLifecycleDetail }
  | { status: "error"; requestKey: string; message: string };

type AttemptDetailsState = {
  requestKey: string;
  status: "loading" | "available" | "unavailable" | "not-queryable";
  attempts: RunAttemptDetailEntry[];
};

/**
 * 渲染某个 PRD 的“执行过程”标签页内容。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 的仓库相对路径（取自列表响应）。
 * @returns 生命周期明细视图，含空态、错误态与重试。
 */
export function PrdLifecycleView({ repoId, prdPath }: { repoId: string; prdPath: string }) {
  const [reloadToken, setReloadToken] = useState(0);
  const lifecycleRequestKey = JSON.stringify([repoId, prdPath, reloadToken]);
  const [lifecycleState, setLifecycleState] = useState<LifecycleState>({
    status: "loading",
    requestKey: lifecycleRequestKey,
  });
  const [attemptDetailsState, setAttemptDetailsState] = useState<AttemptDetailsState>({
    requestKey: lifecycleRequestKey,
    status: "loading",
    attempts: [],
  });
  const [selectedEvent, setSelectedEvent] = useState<PrdLifecycleEventView | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrentRequest = true;
    fetchPrdLifecycle({ repoId, prdPath, signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) {
          return;
        }
        setLifecycleState({ status: "ready", detail, requestKey: lifecycleRequestKey });
        if (detail.issue_number === null || detail.started_at === null) {
          setAttemptDetailsState({
            requestKey: lifecycleRequestKey,
            status: "not-queryable",
            attempts: [],
          });
          return;
        }
        fetchRunExecutionDetails({
          repoId: detail.repo_id,
          issueNumber: detail.issue_number,
          startedAt: detail.started_at,
          finishedAt: detail.finished_at ?? new Date().toISOString(),
        })
          .then((executionDetails) => {
            if (isCurrentRequest) {
              setAttemptDetailsState({
                requestKey: lifecycleRequestKey,
                status: "available",
                attempts: executionDetails.attempts,
              });
            }
          })
          .catch(() => {
            if (isCurrentRequest) {
              setAttemptDetailsState({
                requestKey: lifecycleRequestKey,
                status: "unavailable",
                attempts: [],
              });
            }
          });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setLifecycleState({
          status: "error",
          requestKey: lifecycleRequestKey,
          message: error instanceof Error ? error.message : "读取执行过程失败。",
        });
      });
    return () => {
      isCurrentRequest = false;
      controller.abort();
    };
  }, [repoId, prdPath, reloadToken, lifecycleRequestKey]);

  const currentLifecycleState = lifecycleState.requestKey === lifecycleRequestKey
    ? lifecycleState
    : { status: "loading" as const, requestKey: lifecycleRequestKey };

  if (currentLifecycleState.status === "loading") {
    return (
      <div className="space-y-3" data-testid="prd-lifecycle-loading">
        <Skeleton className="h-6 w-1/3" />
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,10rem),1fr))] gap-2">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    );
  }

  if (currentLifecycleState.status === "error") {
    return (
      <div className="space-y-3" data-testid="prd-lifecycle-error">
        <ResourceErrorAlert message={currentLifecycleState.message} testId="prd-lifecycle-error-alert" />
        <Button
          variant="outline"
          size="sm"
          onClick={() => setReloadToken((token) => token + 1)}
        >
          重试
        </Button>
      </div>
    );
  }

  const detail = currentLifecycleState.detail;

  if (!detail.has_data) {
    return (
      <div
        data-testid="prd-lifecycle-empty"
        className="rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300"
      >
        <p className="font-medium">该 PRD 尚无执行记录</p>
        <p className="mt-1 text-xs text-slate-500">
          通过 Backlog 启动或 runner 执行该 PRD 后，这里会出现当前阶段、耗时拆分与事件时间线。
        </p>
      </div>
    );
  }

  const orderedEvents = [...detail.events].sort((left, right) =>
    left.occurred_at.localeCompare(right.occurred_at),
  );
  const currentAttemptDetailsState = attemptDetailsState.requestKey === lifecycleRequestKey
    ? attemptDetailsState
    : { status: "loading" as const, attempts: [] };
  const statusLabel = resolveStatusLabel(detail);

  return (
    <div className="@container/lifecycle min-w-0 space-y-3" data-testid="prd-lifecycle-view">
      <div
        role="group"
        className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,11rem),1fr))] gap-2"
        aria-label="生命周期状态概览"
      >
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs text-slate-500">最近记录阶段</p>
          <Badge
            className="mt-1"
            variant={phaseBadgeVariant(detail.current_phase)}
            data-testid="prd-lifecycle-current-phase"
          >
            {PHASE_LABELS[detail.current_phase] ?? detail.current_phase}
          </Badge>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs text-slate-500">执行状态</p>
          <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-slate-100">
            {statusLabel}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-800 dark:bg-slate-900/60">
          <p className="text-xs text-slate-500">记录完整性</p>
          <p
            className={cn(
              "mt-1 text-sm font-semibold",
              detail.history_complete
                ? "text-emerald-700 dark:text-emerald-300"
                : "text-amber-700 dark:text-amber-300",
            )}
          >
            {detail.history_complete ? "历史完整" : "存在记录缺口"}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
        {detail.run_id ? (
          <span data-testid="prd-lifecycle-run-id">
            会话：
            <span className="font-mono text-slate-700 dark:text-slate-300">{detail.run_id}</span>
          </span>
        ) : null}
        {detail.issue_number ? <span>Issue #{detail.issue_number}</span> : null}
        <span>开始：{formatLocalDateTime(detail.started_at)}</span>
        <span>
          结束：{detail.finished_at ? formatLocalDateTime(detail.finished_at) : "进行中"}
        </span>
        {detail.trigger ? <span>触发：{detail.trigger}</span> : null}
      </div>

      {!detail.history_complete ? (
        <div
          role="alert"
          data-testid="prd-lifecycle-incomplete"
          className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
        >
          观测数据不完整：部分生命周期事件写入失败，当前阶段与耗时可能只反映了已记录的部分。
        </div>
      ) : null}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,10rem),1fr))] gap-2">
        <MetricTile
          label="端到端"
          value={formatLifecycleDuration(detail.durations.end_to_end_seconds)}
          testId="prd-lifecycle-metric-e2e"
          accent
        />
        <MetricTile
          label="有效执行"
          value={formatLifecycleDuration(detail.durations.active_seconds)}
          testId="prd-lifecycle-metric-active"
        />
        <MetricTile
          label="等待"
          value={formatLifecycleDuration(detail.durations.waiting_seconds)}
          testId="prd-lifecycle-metric-waiting"
        />
        <MetricTile
          label="阻塞"
          value={formatLifecycleDuration(detail.durations.blocked_seconds)}
          testId="prd-lifecycle-metric-blocked"
        />
      </div>

      <div className="space-y-2">
        <div>
          <p className="text-base font-semibold tracking-tight">生命周期执行过程</p>
          <p className="mt-1 text-xs text-slate-500">
            先按业务阶段查看实现、修复、收尾、校验、审核和监督；原始事件与 Agent 尝试轨迹可展开核对。
          </p>
        </div>
        <PrdLifecycleStageTimeline
          events={orderedEvents}
          invocations={detail.invocations}
          attemptDetails={currentAttemptDetailsState.attempts}
          attemptDetailsStatus={currentAttemptDetailsState.status}
          currentPhase={detail.current_phase}
          inProgress={detail.in_progress}
          startedAt={detail.started_at}
          onSelectEvent={setSelectedEvent}
        />
        <details
          data-testid="prd-lifecycle-raw-timeline-toggle"
          className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 dark:border-slate-800 dark:bg-slate-900/50"
        >
          <summary className="cursor-pointer py-2 text-sm font-medium text-slate-700 dark:text-slate-200">
            完整事件轨迹与 Agent 尝试（{orderedEvents.length} 条事件 · {orderedEvents.filter((event) => event.event_type === "attempt").length} 次尝试）
          </summary>
          <div className="pb-3">
            <PrdLifecycleTimeline
              events={orderedEvents}
              invocations={detail.invocations}
              attemptDetails={currentAttemptDetailsState.attempts}
              attemptDetailsStatus={currentAttemptDetailsState.status}
              onSelectEvent={setSelectedEvent}
            />
          </div>
        </details>
      </div>

      <Sheet
        open={selectedEvent !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedEvent(null);
          }
        }}
      >
        <SheetContent
          side="right"
          data-testid="prd-lifecycle-event-sheet"
          className="w-full sm:max-w-md"
        >
          <SheetHeader>
            <SheetTitle>
              {selectedEvent
                ? (EVENT_TYPE_LABELS[selectedEvent.event_type] ?? selectedEvent.event_type)
                : "事件详情"}
            </SheetTitle>
            <SheetDescription>
              {selectedEvent ? formatLocalDateTime(selectedEvent.occurred_at) : ""}
            </SheetDescription>
          </SheetHeader>
          {selectedEvent ? (
            <div className="flex-1 overflow-y-auto px-4 pb-4">
              <EventDetailRows event={selectedEvent} runId={detail.run_id} />
            </div>
          ) : null}
          <div className="px-4 pb-4">
            <SheetClose asChild>
              <Button
                variant="outline"
                size="sm"
                data-testid="prd-lifecycle-event-sheet-close"
              >
                关闭
              </Button>
            </SheetClose>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

/**
 * 计算头部集成的运行态文案。
 *
 * @param detail - 生命周期详情。
 * @returns ``进行中`` / ``阻塞中`` / ``已完成`` / ``失败`` 之一或原始 outcome。
 */
function resolveStatusLabel(detail: PrdLifecycleDetail): string {
  if (detail.in_progress) {
    return detail.current_phase === "blocked" ? "阻塞中" : "进行中";
  }
  if (detail.outcome === null) {
    return "—";
  }
  return OUTCOME_LABELS[detail.outcome] ?? detail.outcome;
}

/**
 * 展示一项生命周期耗时指标。
 *
 * @param props.label - 指标名称。
 * @param props.value - 格式化后的指标值。
 * @param props.testId - 自动化定位标识。
 * @param props.accent - 是否使用重点强调样式。
 * @returns 生命周期指标卡片。
 */
function MetricTile({
  label,
  value,
  testId,
  accent = false,
}: {
  label: string;
  value: string;
  testId: string;
  accent?: boolean;
}) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "min-w-0 rounded-lg border p-2",
        accent
          ? "border-violet-200 bg-violet-50/80 dark:border-violet-900 dark:bg-violet-950/30"
          : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950",
      )}
    >
      <p className="text-xs text-slate-500">{label}</p>
      <p
        className={cn(
          "mt-1 text-lg font-semibold tracking-tight",
          accent
            ? "text-violet-700 dark:text-violet-300"
            : "text-slate-900 dark:text-slate-100",
        )}
      >
        {value}
      </p>
    </div>
  );
}

/**
 * 展示生命周期事件的结构化原始详情。
 *
 * @param props.event - 当前选中的生命周期事件。
 * @param props.runId - 当前 PRD 生命周期标识；为空时不显示。
 * @returns 事件详情字段列表。
 */
function EventDetailRows({
  event,
  runId,
}: {
  event: PrdLifecycleEventView;
  runId: string | null;
}) {
  const detailEntries = Object.entries(event.detail ?? {});
  // 抽屉展示的是**原始事实**：status 为空（旧后端响应）或观测事件（none）时回落
  // 到粗粒度 phase 标签，避免渲染原始枚举（"none"）或空白行。时间线徽章的中性
  // 文案是展示层判断，不改写这里。
  const statusLabelForDrawer =
    event.status && event.status !== "none"
      ? eventStatusLabel(event.status)
      : (PHASE_LABELS[event.phase] ?? event.phase);
  return (
    <dl className="divide-y divide-slate-200 text-sm dark:divide-slate-800">
      {runId ? (
        <DetailRow label="Run">
          <span className="font-mono text-xs">{runId}</span>
        </DetailRow>
      ) : null}
      <DetailRow label="事件类型">
        {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}（{event.event_type}）
      </DetailRow>
      <DetailRow label="状态">{statusLabelForDrawer}</DetailRow>
      <DetailRow label="阶段">{PHASE_LABELS[event.phase] ?? event.phase}</DetailRow>
      <DetailRow label="执行者">{event.actor}</DetailRow>
      <DetailRow label="发生时间">{formatLocalDateTime(event.occurred_at)}</DetailRow>
      <DetailRow label="原始时间">
        <span className="font-mono text-xs">{event.occurred_at}</span>
      </DetailRow>
      {detailEntries.map(([key, value]) => (
        <DetailRow key={key} label={key}>
          <span className="break-all">{formatDetailValue(value)}</span>
        </DetailRow>
      ))}
    </dl>
  );
}

/**
 * 渲染事件详情抽屉中的单个字段。
 *
 * @param props.label - 字段名称。
 * @param props.children - 字段值内容。
 * @returns 两列对齐的详情行。
 */
function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 py-2">
      <dt className="w-24 shrink-0 text-xs text-slate-500">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}
