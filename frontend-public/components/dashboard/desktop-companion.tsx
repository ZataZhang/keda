"use client";

// 桌面 companion 小窗内容：紧凑呈现活动任务，默认四行可见、其余在窗口内滚动。
//
// 这个组件既是 `/app/companion/` route 的全部内容，也是原生窗口的可见面：
// 数据仍走网页看板同一批只读端点（任务摘要 + PRD progress），不另开数据源。
// 与网页看板的差别只在密度与常驻性——窗口不假装自己知道得更多。
//
// 触达读数一律标注「改动树触达 · 弱信号」，且只在可比时画真实比例条；
// 每轮最多读取前四行，滚动到的行显式写「本轮未取数」，不画空条、不写 0%。

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatLifecycleDuration } from "@/components/backlog/prd-lifecycle-format";
import { closeDesktopCompanionWindow, fetchRuntimeTasks } from "@/lib/api/console";
import type { RuntimeTaskEntry, RuntimeTaskSummary } from "@/lib/api/types";
import { formatLocalDateTime } from "@/lib/utils";

import { RuntimeTaskPhase } from "./runtime-task-phase";
import { RuntimeTaskImpact } from "./runtime-task-detail";
import { runtimeStatusPresentation, runtimeTaskKey } from "./runtime-task-list";

/** 小窗轮询间隔：与网页看板同一节奏（PRD §7.1.5「沿用现有 Dashboard 监控刷新」）。 */
const COMPANION_POLL_MS = 15_000;

/** 紧凑高度里默认呈现的活动任务数（其余留在同一列表内可滚动看到）。 */
const MAX_ACTIVE_TASKS = 4;

/** 每轮读取触达读数的条数上限：滚动到的行不擅自增加取数量。 */
const MAX_IMPACT_READS_PER_ROUND = 4;

/** 活动分桶：值守者离开浏览器后仍需要盯的三类状态。 */
const ACTIVE_STATUSES = new Set(["running", "blocked", "waiting"]);

interface DesktopCompanionProps {
  /** 关闭按钮的目标动作；在原生窗口里由 helper 关窗，在浏览器里退化为隐藏。 */
  onClose?: () => void;
  /** 菜单栏 App 中切换 popover 与悬浮窗模式的动作。 */
  onTogglePinned?: () => void;
  /** 当前是否已固定为悬浮窗。 */
  isPinned?: boolean;
  /** 关闭按钮文案；菜单栏 App 中使用「隐藏」。 */
  closeLabel?: string;
}

/**
 * 渲染桌面小窗的任务面板。
 *
 * @param props.onClose - 关闭动作回调；缺省时不渲染关闭按钮。
 * @returns 紧凑任务面板，附带来源可用性与断连披露。
 */
export function DesktopCompanion({
  onClose,
  onTogglePinned,
  isPinned = false,
  closeLabel = "关闭",
}: DesktopCompanionProps) {
  const [summary, setSummary] = useState<RuntimeTaskSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    let cancelled = false;

    /** 拉取一次摘要：状态只在 await 之后更新，卸载或切换暂停时短路。 */
    async function poll() {
      try {
        const next = await fetchRuntimeTasks();
        if (cancelled) {
          return;
        }
        setSummary(next);
        setError(null);
      } catch (caught: unknown) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "无法读取本机 Console");
        }
      }
    }

    void poll();
    if (paused) {
      return;
    }
    const timer = window.setInterval(() => void poll(), COMPANION_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [paused]);

  const activeTasks = (summary?.tasks ?? []).filter((task) => ACTIVE_STATUSES.has(task.status));
  const overflowCount = Math.max(0, activeTasks.length - MAX_ACTIVE_TASKS);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-3" data-testid="desktop-companion">
      <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
        <span className="min-w-0">
          {onTogglePinned ? (
            <span className="mr-2 font-semibold text-slate-900 dark:text-slate-100">
              KedaCode 运行时
            </span>
          ) : null}
          <span data-testid="desktop-companion-sync">
            {summary && !error
              ? `更新于 ${formatLocalDateTime(summary.generated_at)}`
              : "等待读数…"}
          </span>
        </span>
        <span className="flex items-center gap-1">
          {onTogglePinned ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              onClick={onTogglePinned}
              data-testid="desktop-companion-pin"
              aria-label={isPinned ? "取消固定悬浮窗" : "固定为悬浮窗"}
            >
              {isPinned ? "取消固定" : "固定悬浮"}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[11px]"
            onClick={() => setPaused((current) => !current)}
            data-testid="desktop-companion-pause"
          >
            {paused ? "继续" : "暂停"}
          </Button>
          {onClose ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              onClick={onClose}
              data-testid="desktop-companion-close"
            >
              {closeLabel}
            </Button>
          ) : null}
        </span>
      </div>

      {error ? (
        <p
          className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          data-testid="desktop-companion-error"
        >
          读不到本机 Console：{error}。窗口不会用缓存内容冒充最新进度； Console 恢复后自动跟上。
        </p>
      ) : null}

      {summary?.sync_status === "pending_first_sync" && !error ? (
        <p
          className="text-xs text-amber-700 dark:text-amber-300"
          data-testid="desktop-companion-pending"
        >
          尚未完成首次同步，暂无可信任务。
        </p>
      ) : null}

      {summary && summary.unavailable_sources.length > 0 && !error ? (
        <p className="text-xs text-slate-500" data-testid="desktop-companion-partial">
          部分来源本轮不可读：{summary.unavailable_sources.join("、")}
        </p>
      ) : null}

      {activeTasks.length === 0 && !error ? (
        <p
          className="rounded-md border border-dashed border-slate-200 p-4 text-center text-xs text-slate-500 dark:border-slate-800"
          data-testid="desktop-companion-empty"
        >
          {summary ? "当前没有运行中、阻塞或等待值守的任务。" : "还没有读到任务。"}
        </p>
      ) : null}

      {error && activeTasks.length > 0 ? (
        <p
          className="text-[11px] text-amber-700 dark:text-amber-300"
          data-testid="desktop-companion-stale"
        >
          以下条目是断连前的最后一次读数，不代表当前状态。
        </p>
      ) : null}

      <ul
        className={`flex min-h-0 flex-1 flex-col gap-2 overflow-auto ${error ? "opacity-60" : ""}`}
        data-testid="desktop-companion-list"
        data-stale={error ? "true" : "false"}
      >
        {activeTasks.map((task, index) => (
          <CompanionTaskRow
            key={runtimeTaskKey(task)}
            task={task}
            impactRequested={index < MAX_IMPACT_READS_PER_ROUND}
          />
        ))}
      </ul>

      {overflowCount > 0 ? (
        <p className="text-[11px] text-slate-500" data-testid="desktop-companion-hidden">
          紧凑高度先呈现前 {MAX_ACTIVE_TASKS} 项，向下滚动可看其余 {overflowCount}{" "}
          项；完整列表与详情见网页看板。
        </p>
      ) : null}
    </div>
  );
}

/**
 * 小窗里的单行任务：状态、身份、阶段与触达弱信号（紧凑模式）。
 *
 * @param props.task - 摘要里的一条任务。
 * @param props.impactRequested - 本轮是否为该行读取触达读数（每轮有数量上限）。
 * @returns 紧凑任务行。
 */
function CompanionTaskRow({
  task,
  impactRequested,
}: {
  task: RuntimeTaskEntry;
  impactRequested: boolean;
}) {
  const presentation = runtimeStatusPresentation(task.status);
  return (
    <li
      className="flex flex-col gap-1 rounded-md border border-slate-200 p-2 dark:border-slate-800"
      data-testid={`desktop-companion-row-${task.repo_id}-${task.issue_number}`}
    >
      <span className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge variant={presentation.variant}>{presentation.label}</Badge>
        <RuntimeTaskPhase phase={task.phase} />
        <span className="font-medium text-slate-900 dark:text-slate-100">
          {task.repo_display_name ?? task.repo_id}#{task.issue_number}
        </span>
      </span>
      <span className="truncate text-[11px] text-slate-600 dark:text-slate-300">
        {task.issue_title ?? "（账本未记录标题）"}
      </span>
      <span className="flex flex-wrap gap-x-2 text-[11px] text-slate-500">
        {task.agent ? (
          <span>
            {task.agent}
            {task.preset ? ` · ${task.preset}` : ""}
          </span>
        ) : null}
        <span>{formatLifecycleDuration(task.duration_seconds)}</span>
      </span>
      {task.status === "blocked" ? (
        <p
          className="text-[11px] break-words text-red-700 dark:text-red-300"
          data-testid="runtime-task-blocked-reason"
        >
          状态依据：{task.status_reason ?? "未提供，请查看任务详情"}
        </p>
      ) : null}
      <RuntimeTaskImpact
        repoId={task.repo_id}
        prdPath={task.prd_path}
        enabled={impactRequested}
        compact
      />
    </li>
  );
}

/**
 * 请求关闭原生 companion 窗口。
 *
 * 在浏览器标签里打开 `/app/companion/` 时同样可用：动作对象是本机窗口，
 * 不是当前标签，因此调用方要把结果如实呈现（例如「已请求关窗」）。
 *
 * @returns 关窗动作后的 companion 状态。
 */
export async function requestCompanionClose() {
  return closeDesktopCompanionWindow();
}
