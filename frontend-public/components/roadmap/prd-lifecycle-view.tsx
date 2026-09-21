"use client";

// PRD 生命周期视图：展示当前阶段、耗时拆分与追加事件时间线。
//
// 事实源是后端按 `repo_id + prd_path` 聚合出的 lifecycle 详情——前端只做格式
// 化，不重新定义端到端/执行/等待/阻塞口径。`has_data=false` 表示尚无执行记录，
// 必须渲染明确空态；`history_complete=false` 表示观测账本自身有缺口，必须显式
// 告警而不是假装数据完整。

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
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
import { fetchPrdLifecycle } from "@/lib/api/roadmap";
import { formatLocalDateTime } from "@/lib/utils";
import type { PrdLifecycleDetail, PrdLifecycleEventView } from "@/lib/api/types";

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
};

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
};

/** 生命周期 run 结果 -> 中文标签。 */
const OUTCOME_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  blocked: "阻塞",
};

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
};

/**
 * 把秒数格式化为紧凑时长文案。
 *
 * @param seconds - 秒数；null 表示无数据。
 * @returns 形如 ``45s`` / ``12m`` / ``2h 18m`` 的字符串；无数据时为 ``—``。
 */
export function formatLifecycleDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
}

/**
 * 取得阶段对应的 Badge 变体。
 *
 * @param phase - 后端阶段枚举值。
 * @returns 该阶段的 Badge 变体，未知阶段回落到 ``default``。
 */
export function phaseBadgeVariant(phase: string): BadgeVariant {
  return PHASE_BADGE_VARIANTS[phase] ?? "default";
}

/**
 * 把事件 detail 渲染为一行短摘要。
 *
 * attempt 事件按 ``#N agent=... failure=... recovered=...`` 排列关键字段，
 * 其余事件退化为 ``key=value`` 拼接，便于在不打开抽屉时快速扫读。
 *
 * @param event - 单条生命周期事件。
 * @returns 供时间线行展示的摘要文本；detail 为空时为 ``—``。
 */
function summarizeEventDetail(event: PrdLifecycleEventView): string {
  const detail = event.detail ?? {};
  if (event.event_type === "attempt") {
    const attemptNumber = detail.attempt_number;
    const parts = [
      attemptNumber === undefined ? "#?" : `#${String(attemptNumber)}`,
      `agent=${formatDetailValue(detail.agent)}`,
      `failure=${formatDetailValue(detail.failure_type)}`,
      `recovered=${formatDetailValue(detail.recovered)}`,
    ];
    return parts.join(" ");
  }
  const entries = Object.entries(detail);
  if (entries.length === 0) {
    return "—";
  }
  return entries
    .map(([key, value]) => `${key}=${formatDetailValue(value)}`)
    .join(" ");
}

/**
 * 把 detail 中的单个值渲染为可读文本。
 *
 * @param value - detail 中的原始值。
 * @returns 字符串/数字/布尔直接转文本，对象转 JSON，缺失时 ``—``。
 */
function formatDetailValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "—";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

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
          通过 Roadmap 启动或 runner 执行该 PRD 后，这里会出现当前阶段、耗时拆分与事件时间线。
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
        {orderedEvents.length === 0 ? (
          <p className="text-xs text-slate-500" data-testid="prd-lifecycle-timeline-empty">
            暂无生命周期事件。
          </p>
        ) : (
          <ol className="divide-y divide-slate-200 dark:divide-slate-800">
            {orderedEvents.map((event, index) => (
              <li key={`${event.event_type}-${event.occurred_at}-${index}`}>
                <button
                  type="button"
                  data-testid={`prd-lifecycle-event-${event.event_type}`}
                  onClick={() => setSelectedEvent(event)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 py-2 text-left text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-900"
                >
                  <time className="font-mono text-[11px] text-slate-500">
                    {formatLocalDateTime(event.occurred_at)}
                  </time>
                  <span className="font-medium">
                    {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
                  </span>
                  <Badge variant={phaseBadgeVariant(event.phase)}>
                    {PHASE_LABELS[event.phase] ?? event.phase}
                  </Badge>
                  <span className="text-xs text-slate-500">{event.actor}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-slate-500">
                    {summarizeEventDetail(event)}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        )}
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
