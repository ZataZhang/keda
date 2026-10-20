"use client";

// 统一运行时看板的任务列表：按真实生命周期分桶呈现 Issue Agent 运行。
//
// 数据只来自 Console 的注册仓库观测路径（`.../console/runtime/tasks`），
// 不掺托管进程日志。颜色永远伴随文字：状态色只是加速识别，读屏与色觉障碍
// 用户都能从文案得到同样信息。降级信息（来源不可用、首扫未完成、行数截断）
// 单独呈现，避免「列表为空」被读成「一切正常」。

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import { formatLifecycleDuration } from "@/components/backlog/prd-lifecycle-format";
import { prettyLabel } from "@/components/agent-runner/label-variant";
import type { RuntimeTaskEntry, RuntimeTaskStatus } from "@/lib/api/types";
import { formatLocalDateTime } from "@/lib/utils";

import { RuntimeTaskPhase } from "./runtime-task-phase";

/** 状态分桶的人读文案与配色（唯一事实源，列表/详情/小窗共用）。 */
const STATUS_PRESENTATION: Record<RuntimeTaskStatus, { label: string; variant: BadgeVariant }> = {
  running: { label: "运行中", variant: "running" },
  waiting: { label: "等待值守", variant: "review" },
  blocked: { label: "阻塞", variant: "error" },
  completed: { label: "已完成", variant: "ready" },
  closed: { label: "已关闭", variant: "default" },
  unknown: { label: "状态未知", variant: "default" },
};

/** 状态筛选选项：``all`` 加上后端可能给出的每个分桶。 */
const STATUS_FILTERS: RuntimeTaskStatus[] = [
  "running",
  "blocked",
  "waiting",
  "completed",
  "closed",
  "unknown",
];

/**
 * 取得生命周期分桶的展示定义。
 *
 * @param status - 后端分桶值；未知取值回落到「状态未知」。
 * @returns 徽章文案与配色。
 */
export function runtimeStatusPresentation(status: string): {
  label: string;
  variant: BadgeVariant;
} {
  return STATUS_PRESENTATION[status as RuntimeTaskStatus] ?? STATUS_PRESENTATION.unknown;
}

/** 任务身份文案：``keda#283``，前端选路与展示共用。 */
export function runtimeTaskKey(task: RuntimeTaskEntry): string {
  return `${task.repo_id}#${task.issue_number}`;
}

interface RuntimeTaskListProps {
  /** 摘要任务列表（已按后端顺序呈现：状态优先级 → 新近度）。 */
  tasks: RuntimeTaskEntry[];
  /** 当前选中任务键；``null`` 表示尚未选中。 */
  selectedKey: string | null;
  /** 点击某一行时回调选中任务。 */
  onSelect: (task: RuntimeTaskEntry) => void;
  /** 来源不可用的标识（``monitor_snapshot`` 等），用于降级横幅。 */
  unavailableSources?: string[];
  /** 快照同步状态，用于区分「没有任务」与「尚未同步」。 */
  syncStatus?: string;
  /** 启用但还没有快照的仓库标识。 */
  missingRepoIds?: string[];
  /** 摘要是否因行数上限被截断。 */
  truncated?: boolean;
}

/**
 * 渲染运行看板任务列表（含筛选、搜索与空态/降级态）。
 *
 * @param props - 任务数据与选中回调。
 * @returns 任务列表面板。
 */
export function RuntimeTaskList({
  tasks,
  selectedKey,
  onSelect,
  unavailableSources = [],
  syncStatus = "ready",
  missingRepoIds = [],
  truncated = false,
}: RuntimeTaskListProps) {
  const [statusFilter, setStatusFilter] = useState<RuntimeTaskStatus | "all">("all");
  const [query, setQuery] = useState("");

  const counts = useMemo(() => {
    const bucket: Record<string, number> = {};
    for (const task of tasks) {
      bucket[task.status] = (bucket[task.status] ?? 0) + 1;
    }
    return bucket;
  }, [tasks]);

  const visibleTasks = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tasks.filter((task) => {
      if (statusFilter !== "all" && task.status !== statusFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      const haystack = [
        task.repo_id,
        task.repo_display_name ?? "",
        task.issue_title ?? "",
        `#${task.issue_number}`,
        runtimeTaskKey(task),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [tasks, statusFilter, query]);

  return (
    <div className="flex min-h-0 flex-col gap-3" data-testid="runtime-task-list">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="按状态筛选任务">
          <FilterChip
            active={statusFilter === "all"}
            label="全部"
            count={tasks.length}
            onClick={() => setStatusFilter("all")}
            testId="runtime-filter-all"
          />
          {STATUS_FILTERS.map((status) => (
            <FilterChip
              key={status}
              active={statusFilter === status}
              label={STATUS_PRESENTATION[status].label}
              count={counts[status] ?? 0}
              onClick={() => setStatusFilter(status)}
              testId={`runtime-filter-${status}`}
            />
          ))}
        </div>
        <label className="ml-auto flex items-center gap-2 text-xs text-slate-500">
          <span className="sr-only">搜索仓库、Issue 号或标题</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索仓库 / Issue / 标题"
            className="w-56 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-900 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-100"
            data-testid="runtime-task-search"
          />
        </label>
      </div>

      {syncStatus === "pending_first_sync" ? (
        <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="runtime-degraded">
          尚未完成首次同步，任务列表还不能反映真实运行；后台扫描完成后自动跟上。
        </p>
      ) : null}
      {unavailableSources.filter((source) => source !== "monitor_snapshot").length > 0 ? (
        <p
          className="text-xs text-amber-700 dark:text-amber-300"
          data-testid="runtime-source-partial"
        >
          部分来源本轮不可读：{unavailableSources.join("、")}
          。受影响字段留空，不会折算成零。
        </p>
      ) : null}
      {missingRepoIds.length > 0 ? (
        <p className="text-xs text-slate-500" data-testid="runtime-missing-repos">
          尚无监控快照的仓库：{missingRepoIds.join("、")}。
        </p>
      ) : null}

      {visibleTasks.length === 0 ? (
        <div
          className="rounded-md border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500 dark:border-slate-800"
          data-testid="runtime-task-empty"
        >
          {tasks.length === 0
            ? syncStatus === "pending_first_sync"
              ? "等待首次同步后显示任务。"
              : "当前没有已注册仓库的运行任务。"
            : "没有符合筛选条件的任务。"}
        </div>
      ) : (
        <ul className="flex min-h-0 flex-col gap-1 overflow-auto">
          {visibleTasks.map((task) => (
            <RuntimeTaskRow
              key={runtimeTaskKey(task)}
              task={task}
              selected={runtimeTaskKey(task) === selectedKey}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
      {truncated ? (
        <p className="text-xs text-slate-500" data-testid="runtime-truncated">
          任务数超出看板上限，仅显示排序靠前的部分；历史复盘请看统计页。
        </p>
      ) : null}
    </div>
  );
}

/**
 * 状态筛选胶囊：文案 + 计数，选中态同时靠 `aria-pressed` 表达。
 *
 * @param props.active - 是否为当前筛选。
 * @param props.label - 分桶人读名。
 * @param props.count - 该分桶的任务数。
 * @param props.onClick - 切换筛选。
 * @param props.testId - 测试锚点。
 * @returns 可点击的筛选胶囊。
 */
function FilterChip({
  active,
  label,
  count,
  onClick,
  testId,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
        active
          ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
          : "border-slate-200 text-slate-600 hover:bg-slate-100 dark:border-slate-800 dark:text-slate-300 dark:hover:bg-slate-900"
      }`}
      data-testid={testId}
    >
      {label} <span className="tabular-nums">{count}</span>
    </button>
  );
}

/**
 * 看板单行任务：状态徽章 + 身份 + 阶段 + 执行者 + 耗时。
 *
 * 状态色永远与文字同现，颜色只是加速识别，不单独承担含义。
 *
 * @param props.task - 摘要里的一条任务。
 * @param props.selected - 是否为当前选中行。
 * @param props.onSelect - 点击行时回调。
 * @returns 可点击的任务行。
 */
function RuntimeTaskRow({
  task,
  selected,
  onSelect,
}: {
  task: RuntimeTaskEntry;
  selected: boolean;
  onSelect: (task: RuntimeTaskEntry) => void;
}) {
  const presentation = runtimeStatusPresentation(task.status);
  const identity = `${task.repo_display_name ?? task.repo_id}#${task.issue_number}`;
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(task)}
        aria-current={selected ? "true" : undefined}
        className={`w-full rounded-md border px-3 py-2 text-left text-sm transition-colors ${
          selected
            ? "border-slate-900 bg-slate-50 dark:border-slate-200 dark:bg-slate-900"
            : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900"
        }`}
        data-testid={`runtime-task-row-${task.repo_id}-${task.issue_number}`}
      >
        <span className="flex flex-wrap items-center gap-2">
          <Badge variant={presentation.variant}>{presentation.label}</Badge>
          <RuntimeTaskPhase phase={task.phase} />
          <span className="font-medium text-slate-900 dark:text-slate-100">{identity}</span>
          <span className="truncate text-slate-600 dark:text-slate-300">
            {task.issue_title ?? "（账本未记录标题）"}
          </span>
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
          {task.agent ? (
            <span>
              执行：{task.agent}
              {task.preset ? ` · ${task.preset}` : ""}
            </span>
          ) : null}
          <span>耗时：{formatLifecycleDuration(task.duration_seconds)}</span>
          {task.started_at ? <span>开始：{formatLocalDateTime(task.started_at)}</span> : null}
          {task.labels.length > 0 ? (
            <span className="truncate">标签：{task.labels.map(prettyLabel).join(" ")}</span>
          ) : null}
        </span>
      </button>
    </li>
  );
}
