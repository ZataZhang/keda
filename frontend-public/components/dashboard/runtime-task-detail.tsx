"use client";

// 运行看板单任务详情：身份与运行观测 + Issue 实时输出 + 默认折叠的结构化调用。
//
// 三条口径：
// 1. 日志走**既有** Issue 增量日志端点（复用 `PrdIssueOutputView` 的 offset 续读
//    状态机），本组件不新造日志通道，也不过滤/截断原始文本；
// 2. 结构化调用来自既有 invocation ledger，**默认折叠**——展开才请求，收起不轮询；
// 3. 改动树触达只读现有 progress 契约：可比时画真实比例条，不可比时逐条说明
//    原因，绝不把「读不到」画成 0% 或空条。
//
// 任一来源读失败只影响它自己的面板，不会把整页打成错误态。

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PrdIssueOutputView } from "@/components/backlog/prd-issue-output-view";
import {
  formatLifecycleDuration,
  invocationOutcomeBadge,
} from "@/components/backlog/prd-lifecycle-format";
import { explainImpactStatus } from "@/components/backlog/prd-progress-strip";
import { fetchRunExecutionDetails } from "@/lib/api/console";
import { fetchPrdProgress } from "@/lib/api/backlog";
import { openExternalUrl } from "@/lib/open-external";
import type {
  BacklogPrdProgress,
  AnomalyType,
  RuntimeTaskEntry,
  RunInvocationDetailEntry,
} from "@/lib/api/types";
import { cn, formatLocalDateTime } from "@/lib/utils";

import { runtimePhaseDescription, RuntimeTaskPhase } from "./runtime-task-phase";
import { RuntimeTaskDuration, RuntimeTaskWatch, runtimeStatusPresentation } from "./runtime-task-list";

/** `worktree_status` 独有原因：`impact_status` 里没有对应取值，只能在这里补。 */
const WORKTREE_REASON: Record<string, string> = {
  ambiguous: "多个工作区候选，无法确定比较对象",
  not_applicable: "归档终态，不参与分支比较",
  none: "未发现执行工作区",
};

/**
 * 说明改动树读数为什么不可比。
 *
 * @param progress - 聚合进度读数。
 * @returns 不可比时的人读原因；可比时返回 ``undefined``。
 */
function describeImpactUnavailability(progress: BacklogPrdProgress): string | undefined {
  if (progress.impact_status === "available") {
    return undefined;
  }
  return (
    WORKTREE_REASON[progress.worktree_status] ??
    explainImpactStatus(progress) ??
    `影响树暂不可用（${progress.impact_status}）`
  );
}

interface RuntimeTaskAnomaliesProps {
  /** 当前监控快照检测到的异常类型。 */
  anomalyTypes: AnomalyType[];
  /** 紧凑布局（桌面小窗）使用更小字号并允许长标识换行。 */
  compact?: boolean;
}

/**
 * 独立呈现监控异常，避免把状态不一致告警显示成任务失败原因。
 *
 * @param props - 监控快照提供的异常类型与展示密度。
 * @returns 有异常时返回告警说明，否则返回 ``null``。
 */
export function RuntimeTaskAnomalies({ anomalyTypes, compact = false }: RuntimeTaskAnomaliesProps) {
  if (anomalyTypes.length === 0) {
    return null;
  }
  return (
    <p
      className={
        compact
          ? "text-[11px] break-words text-amber-700 dark:text-amber-300"
          : "text-amber-700 dark:text-amber-300"
      }
      data-testid="runtime-task-anomalies"
    >
      监控告警（与任务状态分开）：{anomalyTypes.join("、")}
    </p>
  );
}

interface RuntimeTaskImpactProps {
  /** 仓库标识。 */
  repoId: string;
  /** 该 Issue 关联的 PRD 相对路径；未关联时为 ``null``。 */
  prdPath: string | null;
  /** 紧凑模式（桌面小窗）：只留一行读数。 */
  compact?: boolean;
  /** 本轮是否取数：小窗每轮只读前几项，未取数的行要显式说明，不能静默空着。 */
  enabled?: boolean;
}

/** 触达读数刷新节奏：与看板摘要用同一 15 秒轮询，旧读数留到新一轮落地。 */
const IMPACT_REFRESH_MS = 15_000;

/**
 * 呈递一个任务的改动树触达弱信号（真实读数或逐条原因，二选一）。
 *
 * @param props - 取数键与展示密度。
 * @returns 触达读数面板。
 */
export function RuntimeTaskImpact({
  repoId,
  prdPath,
  compact = false,
  enabled = true,
}: RuntimeTaskImpactProps) {
  // 读数与它的取数键绑在一起：换任务时旧结果天然失效，不需要在 effect 里
  // 同步 setState 去清空（React 19 会把这类重置放大成额外渲染）。
  const requestKey = prdPath ? `${repoId}::${prdPath}` : "";
  const [reading, setReading] = useState<{
    key: string;
    progress: BacklogPrdProgress | null;
    error: string | null;
  } | null>(null);
  const current = reading && reading.key === requestKey ? reading : null;

  useEffect(() => {
    if (!prdPath || !enabled) {
      return;
    }
    const controller = new AbortController();
    const requestedPrdPath = prdPath;

    /** 读取一次；卸载或换任务后到达的响应由 aborted 拦住。 */
    async function read() {
      try {
        const progress = await fetchPrdProgress({
          repoId,
          prdPath: requestedPrdPath,
          signal: controller.signal,
        });
        if (controller.signal.aborted) {
          return;
        }
        setReading({ key: requestKey, progress, error: null });
      } catch (caught: unknown) {
        if (controller.signal.aborted) {
          return;
        }
        setReading({
          key: requestKey,
          progress: null,
          error: caught instanceof Error ? caught.message : "影响树读数读取失败",
        });
      }
    }

    void read();
    // 刷新期间不清空：上一轮读数继续显示，直到这一轮真的落地或失败。
    const timer = window.setInterval(() => void read(), IMPACT_REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [repoId, prdPath, requestKey, enabled]);

  if (!prdPath) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="runtime-impact-no-prd"
        title="未关联 PRD 时没有可比的影响树，这不是「零触达」。"
      >
        未关联 PRD · 改动树触达不适用
      </p>
    );
  }
  if (!enabled) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="runtime-impact-skipped"
        title="小窗每轮只读取前几项触达读数；这里没有条，是因为本轮没取数，不是读不到。"
      >
        改动树触达：本轮未取数（小窗每轮最多读取 4 项）
      </p>
    );
  }
  if (!current) {
    return <p className="text-xs text-slate-500">改动树触达：读取中…</p>;
  }
  if (current.error) {
    return (
      <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="runtime-impact-error">
        改动树触达暂不可用：{current.error}
      </p>
    );
  }
  if (!current.progress) {
    return <p className="text-xs text-slate-500">改动树触达：暂无读数</p>;
  }

  const progress = current.progress;
  const reason = describeImpactUnavailability(progress);
  const impact = progress.impact;
  // 可比且分母非零才画条：judgeable=0 属于「无法判定」，画成 0% 会被读成「没改对」。
  if (!reason && impact && impact.judgeable > 0) {
    const percent = Math.min(100, Math.round((impact.touched / impact.judgeable) * 100));
    const label = `改动树触达 · 弱信号：${impact.touched}/${impact.judgeable}${
      impact.unresolvable > 0 ? `，另有 ${impact.unresolvable} 个无法判定节点` : ""
    }`;
    return (
      <div
        className="flex flex-col gap-1"
        title={IMPACT_WEAK_SIGNAL_HINT}
        data-testid="runtime-impact-bar"
      >
        <span
          className={`text-xs text-slate-600 dark:text-slate-300 ${compact ? "" : "font-medium"}`}
        >
          改动树触达 · 弱信号{" "}
          <span className="tabular-nums">
            {impact.touched}/{impact.judgeable}
          </span>
          {impact.unresolvable > 0 ? (
            <span className="text-slate-500 tabular-nums"> · ?{impact.unresolvable}</span>
          ) : null}
        </span>
        <div
          role="progressbar"
          aria-label={label}
          aria-valuenow={impact.touched}
          aria-valuemin={0}
          aria-valuemax={impact.judgeable}
          className="h-1.5 w-full overflow-hidden rounded bg-slate-200 dark:bg-slate-800"
        >
          <div className="h-full bg-slate-500 dark:bg-slate-400" style={{ width: `${percent}%` }} />
        </div>
        <span className="text-[11px] text-slate-500">触碰计划节点 ≠ 改对，不代表任务完成度</span>
      </div>
    );
  }
  return (
    <p
      className="text-xs text-slate-500"
      data-testid="runtime-impact-reason"
      title={IMPACT_WEAK_SIGNAL_HINT}
    >
      改动树触达：{reason ?? `无可比节点（${progress.impact_status}）`}
      {progress.impact_status === "available" ? "，judgeable=0 不计为 0%" : ""}
    </p>
  );
}

/** 触达读数 tooltip：与 PRD 详情页同一口径的弱信号声明。 */
const IMPACT_WEAK_SIGNAL_HINT =
  "影响树触达：分支实际改动 ∩ 计划节点（弱信号，触碰≠改对）。无法判定的节点单独计数，不计入分母。";

interface RuntimeTaskDetailProps {
  /** 当前选中任务。 */
  task: RuntimeTaskEntry;
  /** 追加到根容器的布局类；抽屉等受高度约束的宿主用它撑满可用空间。 */
  className?: string;
}

/**
 * 渲染选中任务的详情面板。
 *
 * @param props - 选中任务。
 * @returns 详情面板（观测摘要、触达弱信号、实时输出、折叠的结构化调用）。
 */
export function RuntimeTaskDetail({ task, className }: RuntimeTaskDetailProps) {
  const presentation = runtimeStatusPresentation(task.status, task.waiting_reason);
  const [invocationsOpen, setInvocationsOpen] = useState(false);
  const [invocations, setInvocations] = useState<RunInvocationDetailEntry[] | null>(null);
  const [invocationError, setInvocationError] = useState<string | null>(null);
  // 调用账本要有一段收口的运行窗口才能按时间窗查询；没窗口不是读取失败，
  // 而是「现在还没法读」，所以留在渲染层说明，不进状态机。
  const hasRunWindow = Boolean(task.started_at && task.finished_at);

  // 换任务由父级用 task_key 重新挂载本组件完成：展开态与上一次读数天然归零，
  // 不需要在 effect 里同步 setState 清空（React 19 会把重置放大成额外渲染）。
  useEffect(() => {
    if (!invocationsOpen || invocations !== null || invocationError) {
      return;
    }
    if (!hasRunWindow) {
      return;
    }
    const controller = new AbortController();
    void (async () => {
      try {
        const details = await fetchRunExecutionDetails({
          repoId: task.repo_id,
          issueNumber: task.issue_number,
          startedAt: task.started_at as string,
          finishedAt: task.finished_at as string,
          signal: controller.signal,
        });
        if (controller.signal.aborted) {
          return;
        }
        setInvocations(details.invocations);
      } catch (error: unknown) {
        if (controller.signal.aborted) {
          return;
        }
        setInvocationError(error instanceof Error ? error.message : "结构化调用读取失败");
      }
    })();
    return () => controller.abort();
  }, [
    invocationsOpen,
    invocations,
    invocationError,
    hasRunWindow,
    task.repo_id,
    task.issue_number,
    task.started_at,
    task.finished_at,
  ]);

  const identity = `${task.repo_display_name ?? task.repo_id}#${task.issue_number}`;
  const issueUrl = task.issue_url;
  const prUrl = task.pr_url;

  return (
    <div className={cn("flex min-h-0 flex-col gap-3", className)} data-testid="runtime-task-detail">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant={presentation.variant}>{presentation.label}</Badge>
            <RuntimeTaskPhase phase={task.phase} />
            <RuntimeTaskWatch task={task} />
            <span>{identity}</span>
            {issueUrl ? (
              <a
                href={issueUrl}
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl(issueUrl);
                }}
                className="text-xs font-normal text-sky-700 underline dark:text-sky-300"
                data-testid="runtime-detail-issue-link"
              >
                Issue 原文
              </a>
            ) : null}
            {prUrl ? (
              <a
                href={prUrl}
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl(prUrl);
                }}
                className="text-xs font-normal text-sky-700 underline dark:text-sky-300"
                data-testid="runtime-detail-pr-link"
              >
                PR {task.pr_number ? `#${task.pr_number}` : "原文"}
              </a>
            ) : null}
          </CardTitle>
          {task.issue_title ? (
            <p className="text-xs text-slate-600 dark:text-slate-300">{task.issue_title}</p>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-xs text-slate-600 dark:text-slate-300">
          <p data-testid="runtime-task-status-reason">状态来源：{task.status_reason ?? "未提供"}</p>
          <p data-testid="runtime-task-phase-description">
            阶段说明：{runtimePhaseDescription(task.phase)}
          </p>
          <RuntimeTaskAnomalies anomalyTypes={task.anomaly_types ?? []} />
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>
              {task.finished_at ? "上次执行" : "执行"}：{task.agent ?? "未知"}
              {task.preset ? ` · preset ${task.preset}` : ""}
              {task.model ? ` · 模型 ${task.model}` : ""}
            </span>
            <span>尝试：{task.attempt_number ?? "—"}</span>
            <span>
              开始：
              {task.started_at ? formatLocalDateTime(task.started_at) : "未知"}
            </span>
            <span>
              结束：
              {task.finished_at ? formatLocalDateTime(task.finished_at) : "尚未结束"}
            </span>
            <RuntimeTaskDuration task={task} />
            <span>终态：{task.last_outcome ?? "未收口"}</span>
          </div>
          {task.prd_path ? (
            <p className="text-slate-500">
              PRD：<code>{task.prd_path}</code>
            </p>
          ) : null}
          <RuntimeTaskImpact repoId={task.repo_id} prdPath={task.prd_path} />
        </CardContent>
      </Card>

      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100">实时输出</h3>
          <Button
            size="sm"
            variant={invocationsOpen ? "default" : "outline"}
            aria-expanded={invocationsOpen}
            onClick={() => setInvocationsOpen((open) => !open)}
            data-testid="runtime-invocation-toggle"
          >
            {invocationsOpen ? "收起结构化调用" : "展开结构化调用"}
          </Button>
        </div>
        {invocationsOpen ? (
          <div
            className="rounded-md border border-slate-200 p-3 dark:border-slate-800"
            data-testid="runtime-invocation-panel"
          >
            {invocationError ? (
              <p className="text-xs text-amber-700 dark:text-amber-300">
                结构化调用读取失败：{invocationError}
              </p>
            ) : !hasRunWindow ? (
              <p className="text-xs text-slate-500">
                该任务尚无收口的运行窗口，结构化调用要等本次运行结束后可读。
              </p>
            ) : invocations === null ? (
              <p className="text-xs text-slate-500">读取中…</p>
            ) : invocations.length === 0 ? (
              <p className="text-xs text-slate-500" data-testid="runtime-invocation-empty">
                本次运行窗口内没有持久化的结构化调用记录。
              </p>
            ) : (
              <ul className="flex flex-col gap-1">
                {invocations.map((invocation) => {
                  const outcome = invocationOutcomeBadge(invocation.outcome);
                  return (
                    <li
                      key={invocation.invocation_id}
                      className="flex flex-wrap items-center gap-2 text-xs"
                      data-testid="runtime-invocation-row"
                    >
                      <Badge variant={outcome.variant}>{outcome.label}</Badge>
                      <span className="text-slate-600 dark:text-slate-300">
                        {invocation.phase} · {invocation.role} · {invocation.executor}
                      </span>
                      <span className="text-slate-500">
                        {formatLifecycleDuration(invocation.duration_seconds)}
                      </span>
                      {invocation.retry_of ? (
                        <span className="text-slate-500">重试自 {invocation.retry_of}</span>
                      ) : null}
                      {invocation.reported_model ? (
                        <span className="text-slate-500">{invocation.reported_model}</span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ) : null}
        {/* 日志面板自带 offset 续读、暂停与新尝试切换，原始文本不做任何过滤。 */}
        <div className="flex min-h-[16rem] flex-1 flex-col">
          <PrdIssueOutputView repoId={task.repo_id} issueNumber={task.issue_number} />
        </div>
      </div>
    </div>
  );
}
