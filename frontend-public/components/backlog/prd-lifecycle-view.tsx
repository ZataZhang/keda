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
import { formatLocalDateTime } from "@/lib/utils";
import type { PrdLifecycleDetail, PrdLifecycleEventView } from "@/lib/api/types";

/** 生命周期 run 结果 -> 中文标签。 */
const OUTCOME_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  blocked: "阻塞",
};

type LifecycleState =
  | { status: "loading" }
  | { status: "ready"; detail: PrdLifecycleDetail }
  | { status: "error"; message: string };

/**
 * 渲染某个 PRD 的“执行过程”标签页内容。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 的仓库相对路径（取自列表响应）。
 * @returns 生命周期明细视图，含空态、错误态与重试。
 */
export function PrdLifecycleView({ repoId, prdPath }: { repoId: string; prdPath: string }) {
  const [lifecycleState, setLifecycleState] = useState<LifecycleState>({ status: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [selectedEvent, setSelectedEvent] = useState<PrdLifecycleEventView | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLifecycleState({ status: "loading" });
    fetchPrdLifecycle({ repoId, prdPath, signal: controller.signal })
      .then((detail) => {
        setLifecycleState({ status: "ready", detail });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setLifecycleState({
          status: "error",
          message: error instanceof Error ? error.message : "读取执行过程失败。",
        });
      });
    return () => {
      controller.abort();
    };
  }, [repoId, prdPath, reloadToken]);

  if (lifecycleState.status === "loading") {
    return (
      <div className="space-y-3" data-testid="prd-lifecycle-loading">
        <Skeleton className="h-6 w-1/3" />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
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

  if (lifecycleState.status === "error") {
    return (
      <div className="space-y-3" data-testid="prd-lifecycle-error">
        <ResourceErrorAlert message={lifecycleState.message} testId="prd-lifecycle-error-alert" />
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

  const detail = lifecycleState.detail;

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
  const statusLabel = resolveStatusLabel(detail);

  return (
    <div className="space-y-4" data-testid="prd-lifecycle-view">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge variant={phaseBadgeVariant(detail.current_phase)} data-testid="prd-lifecycle-current-phase">
          {PHASE_LABELS[detail.current_phase] ?? detail.current_phase}
        </Badge>
        <span className="text-sm font-medium">{statusLabel}</span>
        {detail.run_id ? (
          <span className="font-mono text-xs text-slate-500" data-testid="prd-lifecycle-run-id">
            {detail.run_id}
          </span>
        ) : null}
        {detail.issue_number ? (
          <span className="text-xs text-slate-500">Issue #{detail.issue_number}</span>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
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

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MetricTile
          label="端到端"
          value={formatLifecycleDuration(detail.durations.end_to_end_seconds)}
          testId="prd-lifecycle-metric-e2e"
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
        <p className="text-sm font-medium">生命周期时间线</p>
        <PrdLifecycleTimeline
          events={orderedEvents}
          invocations={detail.invocations}
          onSelectEvent={setSelectedEvent}
        />
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

function MetricTile({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}) {
  return (
    <div
      data-testid={testId}
      className="rounded-md border border-slate-200 p-3 dark:border-slate-800"
    >
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

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

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 py-2">
      <dt className="w-24 shrink-0 text-xs text-slate-500">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}
