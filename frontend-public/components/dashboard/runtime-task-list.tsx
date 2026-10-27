"use client";

// 统一运行时看板的任务列表：按真实生命周期分桶呈现 Issue Agent 运行。
//
// 数据只来自 Console 的注册仓库观测路径（`.../console/runtime/tasks`），
// 不掺托管进程日志。颜色永远伴随文字：状态色只是加速识别，读屏与色觉障碍
// 用户都能从文案得到同样信息。降级信息（来源不可用、首扫未完成、行数截断）
// 单独呈现，避免「列表为空」被读成「一切正常」。

import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";

import { ConsoleToolbar } from "@/components/layout/console-page";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import { formatLifecycleDuration } from "@/components/backlog/prd-lifecycle-format";
import { prettyLabel } from "@/components/agent-runner/label-variant";
import type { RuntimeTaskEntry, RuntimeTaskStatus } from "@/lib/api/types";
import { openExternalUrl } from "@/lib/open-external";
import { cn, formatLocalDateTime } from "@/lib/utils";

import { RuntimeTaskPhaseDescription, RuntimeTaskPhase } from "./runtime-task-phase";

/** 状态分桶的人读文案与配色（唯一事实源，列表/详情/小窗共用）。 */
const STATUS_PRESENTATION: Record<RuntimeTaskStatus, { label: string; variant: BadgeVariant }> = {
  running: { label: "运行中", variant: "running" },
  waiting: { label: "等待处理", variant: "review" },
  blocked: { label: "阻塞", variant: "blocked" },
  failed: { label: "失败", variant: "failed" },
  completed: { label: "已完成", variant: "ready" },
  closed: { label: "已关闭", variant: "default" },
  unknown: { label: "状态未知", variant: "default" },
};

/** 状态筛选选项：``all`` 加上后端可能给出的每个分桶。 */
const STATUS_FILTERS: RuntimeTaskStatus[] = [
  "running",
  "blocked",
  "failed",
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
export function runtimeStatusPresentation(
  status: string,
  waitingReason?: RuntimeTaskEntry["waiting_reason"]
): {
  label: string;
  variant: BadgeVariant;
} {
  if (status === "waiting") {
    const label =
      waitingReason === "queued"
        ? "待执行"
        : waitingReason === "human_review"
          ? "待人工评审"
          : "待再次检查";
    return { ...STATUS_PRESENTATION.waiting, label };
  }
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
  const [statusFilter, setStatusFilter] = useState<RuntimeTaskStatus | "all" | "active">("active");
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
      if (
        statusFilter === "active"
          ? !isActiveRuntimeTask(task)
          : statusFilter !== "all" && task.status !== statusFilter
      ) {
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
      <ConsoleToolbar>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="按状态筛选任务">
          <FilterChip
            active={statusFilter === "active"}
            label="活动任务"
            count={tasks.filter(isActiveRuntimeTask).length}
            onClick={() => setStatusFilter("active")}
            testId="runtime-filter-active"
          />
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
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索仓库 / Issue / 标题"
            className="h-8 w-56"
            data-testid="runtime-task-search"
          />
        </label>
      </ConsoleToolbar>

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
            : statusFilter === "active" && !query.trim()
              ? "当前没有活动任务；可切换「全部」「已完成」或「已关闭」查看历史。"
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
      className={`inline-flex h-8 items-center gap-1 rounded-md border px-2.5 text-xs transition-colors ${
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border text-muted-foreground hover:bg-muted hover:text-foreground"
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
  const presentation = runtimeStatusPresentation(task.status, task.waiting_reason);
  const identity = `${task.repo_display_name ?? task.repo_id}#${task.issue_number}`;
  const issueUrl = task.issue_url;
  const prUrl = task.pr_url;
  return (
    <li
      className={`rounded-md border text-sm transition-colors ${
        selected
          ? "border-slate-900 bg-slate-50 dark:border-slate-200 dark:bg-slate-900"
          : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900"
      }`}
    >
      {/* 行按钮与 Issue / PR 链接必须是兄弟节点：把 <a> 嵌进 button 或
          role=button 会被 WebKit 的 AX 压成整行一个按钮，链接既不可读也不可点。 */}
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => onSelect(task)}
          aria-current={selected ? "true" : undefined}
          className="min-w-0 flex-1 px-3 py-2 text-left"
          data-testid={`runtime-task-row-${task.repo_id}-${task.issue_number}`}
        >
        <span className="flex flex-wrap items-center gap-2">
          <Badge variant={presentation.variant}>{presentation.label}</Badge>
          <RuntimeTaskPhase task={task} />
          <RuntimeTaskWatch task={task} />
          <span className="font-medium text-slate-900 dark:text-slate-100">{identity}</span>
          <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300">
            {task.issue_title ?? "（账本未记录标题）"}
          </span>
        </span>
        <RuntimeTaskPhaseDescription task={task} className="mt-1 block text-xs text-slate-500" />
        <RuntimeTaskObservation task={task} />
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
          {task.agent ? (
            <span>
              最近执行：{task.agent}
              {task.preset ? ` · ${task.preset}` : ""}
            </span>
          ) : null}
          <RuntimeTaskDuration task={task} />
          {task.started_at ? <span>开始：{formatLocalDateTime(task.started_at)}</span> : null}
          {task.labels.length > 0 ? (
            <span className="truncate">标签：{task.labels.map(prettyLabel).join(" ")}</span>
          ) : null}
        </span>
        </button>
        <div className="flex shrink-0 flex-col items-end gap-1 px-3 py-2 text-[11px]">
          {issueUrl ? (
            <ExternalLinkPill
              href={issueUrl}
              label={`Issue #${task.issue_number}`}
              tone="issue"
              linkTestId="runtime-task-issue-link"
              copyTestId="runtime-task-issue-copy"
              description={`Issue #${task.issue_number}`}
            />
          ) : null}
          {prUrl ? (
            <ExternalLinkPill
              href={prUrl}
              label={task.pr_number ? `PR #${task.pr_number}` : "PR 链接"}
              tone="pr"
              linkTestId="runtime-task-pr-link"
              copyTestId="runtime-task-pr-copy"
              description={task.pr_number ? `PR #${task.pr_number}` : "PR"}
            />
          ) : null}
        </div>
      </div>
    </li>
  );
}

/** Issue / PR 胶囊的配色（左半是跳转链接，右半是复制按钮，共用同一个外壳）。 */
const LINK_PILL_TONES = {
  issue: {
    shell:
      "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-300",
    segment: "hover:bg-sky-100 dark:hover:bg-sky-900/60",
    divider: "border-sky-200 dark:border-sky-900",
  },
  pr: {
    shell:
      "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950/50 dark:text-violet-300",
    segment: "hover:bg-violet-100 dark:hover:bg-violet-900/60",
    divider: "border-violet-200 dark:border-violet-900",
  },
} as const;

/**
 * 一分为二的外链胶囊：左半跳转，右半只复制 URL（复制不触发跳转）。
 *
 * @param props - 目标地址、左半文案、配色语义、两段各自的 testid 与读屏用描述。
 * @returns 可跳转 + 可复制的外链胶囊。
 */
function ExternalLinkPill({
  href,
  label,
  tone,
  linkTestId,
  copyTestId,
  description,
}: {
  href: string;
  label: string;
  tone: keyof typeof LINK_PILL_TONES;
  linkTestId: string;
  copyTestId: string;
  description: string;
}) {
  const [copied, setCopied] = useState(false);
  const toneClass = LINK_PILL_TONES[tone];

  /** 复制 URL；失败时保持图标不变，用户仍可选中链接文本手动复制。 */
  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 复制失败是环境限制（非安全上下文/无权限），不该弹窗打断看板浏览。
    }
  }

  return (
    <span
      className={cn(
        "inline-flex items-stretch overflow-hidden rounded-md border leading-4",
        toneClass.shell,
      )}
    >
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
          }
          event.preventDefault();
          void openExternalUrl(href);
        }}
        className={cn("inline-flex items-center px-2 py-0.5", toneClass.segment)}
        data-testid={linkTestId}
      >
        {label}
      </a>
      <button
        type="button"
        onClick={() => void handleCopy()}
        className={cn("flex items-center border-l px-1.5", toneClass.divider, toneClass.segment)}
        aria-label={copied ? "已复制链接" : `复制 ${description} 链接`}
        title={copied ? "已复制" : `复制 ${description} 链接`}
        data-testid={copyTestId}
      >
        {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
      </button>
    </span>
  );
}

/** 展示本机 PR 值守探活；未知不能当作未启动。 */
export function RuntimeTaskWatch({ task }: { task: RuntimeTaskEntry }) {
  if (task.status !== "waiting" || task.waiting_reason === "queued") return null;
  const label =
    task.review_daemon_running === true
      ? "PR 值守运行中"
      : task.review_daemon_running === false
        ? "PR 值守未启动"
        : "PR 值守状态未知";
  return (
    <span
      className="text-xs text-slate-500"
      title="仅观测本机 PR 值守进程；不代表当前正在执行或已获自动合并授权。"
    >
      {label}
    </span>
  );
}

/** 共享运行耗时口径，区分历史运行和当前轮次。 */
export function RuntimeTaskDuration({ task }: { task: RuntimeTaskEntry }) {
  if (task.duration_seconds === null) return null;
  const label = task.finished_at ? "上次运行耗时" : task.status === "running" ? "本轮耗时" : "记录耗时";
  return <span>{label}：{formatLifecycleDuration(task.duration_seconds)}</span>;
}

/** 活动列表包含状态待核实的任务，避免未知状态被历史任务掩盖。 */
export function isActiveRuntimeTask(task: RuntimeTaskEntry): boolean {
  return task.status !== "completed" && task.status !== "closed";
}

/** 在列表、小窗和详情中直接披露运行依据及历史终态。 */
export function RuntimeTaskObservation({ task }: { task: RuntimeTaskEntry }) {
  const outcomeLabels: Record<string, string> = { completed: "成功", failed: "失败", blocked: "阻塞" };
  const reason = task.status === "running"
    ? `${task.status_reason ?? "监控标记运行"}；运行标记不代表执行进程已探活。`
    : task.status === "unknown" || task.status === "closed"
      ? task.status_reason
      : null;
  if (!reason && !task.last_outcome) return null;
  return (
    <span className="mt-1 block text-xs text-slate-500" data-testid="runtime-task-observation">
      {reason}
      {reason && task.last_outcome ? " · " : ""}
      {task.last_outcome ? `上次运行结果：${outcomeLabels[task.last_outcome] ?? task.last_outcome}` : ""}
    </span>
  );
}
