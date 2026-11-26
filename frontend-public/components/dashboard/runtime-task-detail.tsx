"use client";

// 运行看板单任务详情：当前状态 + 当前运行输出或按需展开的历史运行。
//
// 三条口径：
// 1. 日志走**既有** Issue 增量日志端点（复用 `PrdIssueOutputView` 的 offset 续读
//    状态机），本组件不新造日志通道，也不过滤/截断原始文本；
// 2. 结构化调用来自既有 invocation ledger；历史未展开时不读取，当前运行才轮询；
// 3. 改动树触达只读现有 progress 契约：可比时画真实比例条，不可比时逐条说明
//    原因，绝不把「读不到」画成 0% 或空条。
//
// 任一来源读失败只影响它自己的面板，不会把整页打成错误态。

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { explainImpactStatus } from "@/components/backlog/prd-progress-strip";
import { fetchLifecycleSettings } from "@/lib/api/lifecycleSettings";
import { fetchPrdProgress } from "@/lib/api/backlog";
import { openExternalUrl } from "@/lib/open-external";
import type {
  BacklogPrdProgress,
  AgentAvailabilityEntry,
  AnomalyType,
  RuntimeTaskEntry,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { isQueuedRuntimeTask } from "@/lib/runtime-task";

import { RuntimeTaskPhaseDescription, RuntimeTaskPhase } from "./runtime-task-phase";
import { RuntimeTaskExecutor } from "./runtime-task-executor";
import { RuntimeTaskRun } from "./runtime-task-run";
import {
  RuntimeTaskBlockingReason,
  RuntimeTaskObservation,
  RuntimeTaskWatch,
  runtimeStatusPresentation,
  runtimeTaskPrLabel,
} from "./runtime-task-list";

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

const ANOMALY_DESCRIPTIONS: Record<AnomalyType, string> = {
  label_pr_mismatch: "关联 PR 已转为 Ready，Issue 标签尚未切到对应阶段",
  pr_dirty_in_review: "Issue 正在等待审核，但关联 PR 存在合并冲突",
  dirty_worktree_mismatch: "执行工作区有未提交改动，Issue 标签显示未在运行",
  event_label_mismatch: "最近一次流程记录与 Issue 当前标签不一致",
};

interface RuntimeTaskAnomaliesProps {
  /** 当前监控快照中的任务摘要。 */
  task: RuntimeTaskEntry;
  /** 紧凑布局（桌面小窗）使用更小字号并允许换行。 */
  compact?: boolean;
}

/**
 * 独立呈现监控异常，避免把状态不一致告警显示成任务失败原因。
 *
 * @param props - 任务摘要与展示密度。
 * @returns 有异常时返回告警说明，否则返回 ``null``。
 */
export function RuntimeTaskAnomalies({ task, compact = false }: RuntimeTaskAnomaliesProps) {
  const descriptions = (task.anomaly_types ?? []).map((anomalyType) =>
    anomalyType === "label_pr_mismatch"
      ? (task.pr_label_warning ?? ANOMALY_DESCRIPTIONS[anomalyType])
      : (ANOMALY_DESCRIPTIONS[anomalyType] ?? "监控发现状态不一致，请查看仓库监控"),
  );
  if (descriptions.length === 0) {
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
      监控告警：{descriptions.join(" ")}
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
  const archiveInfo =
    !compact && progress.prd_source && progress.prd_path.startsWith("tasks/archive/") ? (
      <p className="text-xs break-words text-slate-500">
        PRD 已归档（
        {progress.prd_source === "worktree" ? "执行工作区" : "主仓库"}）：
        <code>{progress.prd_path}</code>
      </p>
    ) : null;
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
        {archiveInfo}
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
    <>
      {archiveInfo}
      <p
        className="text-xs text-slate-500"
        data-testid="runtime-impact-reason"
        title={IMPACT_WEAK_SIGNAL_HINT}
      >
        改动树触达：{reason ?? `无可比节点（${progress.impact_status}）`}
        {progress.impact_status === "available" ? "，judgeable=0 不计为 0%" : ""}
      </p>
    </>
  );
}

/** 触达读数 tooltip：与 PRD 详情页同一口径的弱信号声明。 */
const IMPACT_WEAK_SIGNAL_HINT =
  "影响树触达：分支实际改动 ∩ 计划节点（弱信号，触碰≠改对）。无法判定的节点单独计数，不计入分母。";

interface RuntimeTaskDetailProps {
  /** 当前选中任务。 */
  task: RuntimeTaskEntry;
  /** 用户选择新执行器后请求后端按该生命周期阶段续跑。 */
  onRetryWithExecutor?: (stage: string, executor: string) => void;
  /** 阶段执行器续跑请求是否正在提交。 */
  retryPending?: boolean;
  /** 本任务已提交的「切换执行器续跑」（阶段与执行器）；已排队时不再重复提交。 */
  executorSwitchQueued?: { stage: string; executor: string } | null;
  /** 追加到根容器的布局类；抽屉等受高度约束的宿主用它撑满可用空间。 */
  className?: string;
}

/**
 * 渲染选中任务的详情面板。
 *
 * @param props - 选中任务。
 * @returns 详情面板（当前状态、触达弱信号、实时输出或历史运行记录）。
 */
export function RuntimeTaskDetail({
  task,
  onRetryWithExecutor,
  retryPending = false,
  executorSwitchQueued = null,
  className,
}: RuntimeTaskDetailProps) {
  const presentation = runtimeStatusPresentation(task.status, task.waiting_reason);
  const [executorChooserOpen, setExecutorChooserOpen] = useState(false);
  const [executorChoices, setExecutorChoices] = useState<AgentAvailabilityEntry[] | null>(null);
  const [executorChoice, setExecutorChoice] = useState("");
  const [executorChoiceError, setExecutorChoiceError] = useState<string | null>(null);
  const [executorChoicesLoading, setExecutorChoicesLoading] = useState(false);
  const identity = `${task.repo_display_name ?? task.repo_id}#${task.issue_number}`;
  const issueUrl = task.issue_url;
  const prUrl = task.pr_url;

  /** 读取该阶段可切换且尚未失败过的执行器。 */
  async function openExecutorChooser() {
    setExecutorChooserOpen(true);
    setExecutorChoices(null);
    setExecutorChoiceError(null);
    setExecutorChoicesLoading(true);
    try {
      const settings = await fetchLifecycleSettings({
        scope: "repository",
        repoId: task.repo_id,
      });
      const failedExecutors = new Set(task.failed_executors ?? []);
      const choices = settings.agent_availability.filter(
        (entry) =>
          entry.available &&
          entry.declared_profiles.includes("run") &&
          !failedExecutors.has(entry.agent) &&
          !(task.failure_stage === "verifier" && entry.agent === task.agent),
      );
      setExecutorChoices(choices);
      setExecutorChoice(choices[0]?.agent ?? "");
    } catch (error: unknown) {
      setExecutorChoiceError(
        error instanceof Error ? error.message : "无法读取当前仓库的可用执行器。",
      );
    } finally {
      setExecutorChoicesLoading(false);
    }
  }

  return (
    <div className={cn("flex min-h-0 flex-col gap-3", className)} data-testid="runtime-task-detail">
      <Card data-testid="runtime-task-current">
        <CardHeader className="pb-2">
          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant={presentation.variant}>{presentation.label}</Badge>
            <RuntimeTaskExecutor task={task} />
            <RuntimeTaskPhase task={task} />
            {!isQueuedRuntimeTask(task) ? (
              <Badge
                variant="outline"
                className="break-all whitespace-normal"
                data-testid="runtime-detail-agent"
                title={
                  task.unclosed_invocation_executor
                    ? "调用账本尚未记录结束事件，进程存活状态需另行确认。"
                    : undefined
                }
              >
                {task.unclosed_invocation_executor
                  ? `未结束调用：${task.unclosed_invocation_executor}`
                  : `最近执行：${task.agent ?? "未知"}`}
                {task.unclosed_invocation_executor && task.unclosed_invocation_model
                  ? ` · ${task.unclosed_invocation_model}`
                  : null}
              </Badge>
            ) : null}
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
                {runtimeTaskPrLabel(task)}
              </a>
            ) : null}
          </CardTitle>
          {task.issue_title ? (
            <p className="text-xs text-slate-600 dark:text-slate-300">{task.issue_title}</p>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-xs text-slate-600 dark:text-slate-300">
          <p data-testid="runtime-task-status-reason">状态来源：{task.status_reason ?? "未提供"}</p>
          <RuntimeTaskBlockingReason task={task} />
          <RuntimeTaskPhaseDescription task={task} />
          <RuntimeTaskObservation task={task} showLastOutcome={false} />
          {task.status === "failed" && task.failure_stage && onRetryWithExecutor ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950">
              <p className="text-sm text-amber-900 dark:text-amber-100">
                这是执行器调用失败，系统没有收到本阶段可用的完成结果。选择一个本阶段尚未尝试的执行器后，
                Runner 会重新进入任务流程并复用已有提交或检查点。
              </p>
              {executorSwitchQueued ? (
                <p
                  className="mt-2 text-sm text-emerald-800 dark:text-emerald-200"
                  data-testid="runtime-task-executor-switch-queued"
                >
                  已排入续跑：{executorSwitchQueued.stage} 阶段改用执行器{" "}
                  {executorSwitchQueued.executor}，等待 Runner 认领后生效，无需重复提交。
                </p>
              ) : !executorChooserOpen ? (
                <Button
                  className="mt-2"
                  variant="outline"
                  size="sm"
                  disabled={retryPending}
                  onClick={() => void openExecutorChooser()}
                  data-testid="runtime-task-switch-executor"
                >
                  切换执行器续跑
                </Button>
              ) : (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {executorChoicesLoading ? (
                    <span className="text-sm text-amber-800 dark:text-amber-200">
                      读取可用执行器…
                    </span>
                  ) : executorChoices && executorChoices.length > 0 ? (
                    <>
                      <label className="sr-only" htmlFor="runtime-retry-executor">
                        选择续跑执行器
                      </label>
                      <select
                        id="runtime-retry-executor"
                        value={executorChoice}
                        onChange={(event) => setExecutorChoice(event.target.value)}
                        className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                        data-testid="runtime-task-retry-executor-select"
                      >
                        {executorChoices.map((entry) => (
                          <option key={entry.agent} value={entry.agent}>
                            {entry.agent}
                          </option>
                        ))}
                      </select>
                      <Button
                        size="sm"
                        disabled={retryPending || !executorChoice}
                        onClick={() =>
                          onRetryWithExecutor(task.failure_stage as string, executorChoice)
                        }
                        data-testid="runtime-task-retry-with-executor"
                      >
                        {retryPending ? "排入续跑…" : "使用该执行器续跑"}
                      </Button>
                    </>
                  ) : (
                    <span className="text-sm text-amber-800 dark:text-amber-200">
                      没有未尝试且已安装的 run 执行器；请先在执行器设置中注册或安装其他执行器。
                    </span>
                  )}
                  {executorChoiceError ? (
                    <span className="text-sm text-red-700 dark:text-red-300" role="alert">
                      {executorChoiceError}
                    </span>
                  ) : null}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={retryPending}
                    onClick={() => setExecutorChooserOpen(false)}
                  >
                    取消
                  </Button>
                </div>
              )}
            </div>
          ) : null}
          <RuntimeTaskAnomalies task={task} />
          {task.prd_path ? (
            <p className="text-slate-500">
              PRD 原始路径：<code>{task.prd_path}</code>
            </p>
          ) : null}
          <RuntimeTaskImpact repoId={task.repo_id} prdPath={task.prd_path} />
        </CardContent>
      </Card>

      <RuntimeTaskRun
        key={JSON.stringify([
          task.task_key,
          task.invocation_run_id,
          task.started_at,
          task.finished_at,
          task.status,
        ])}
        task={task}
      />
    </div>
  );
}
