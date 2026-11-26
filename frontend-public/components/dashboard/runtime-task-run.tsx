"use client";

import { useEffect, useState } from "react";

import { PrdIssueOutputView } from "@/components/backlog/prd-issue-output-view";
import {
  describeInvocationModel,
  formatLifecycleDuration,
  invocationOutcomeBadge,
} from "@/components/backlog/prd-lifecycle-format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchRunExecutionDetails } from "@/lib/api/console";
import type { RuntimeTaskEntry, RunInvocationDetailEntry } from "@/lib/api/types";
import { formatLocalDateTime } from "@/lib/utils";
import { isQueuedRuntimeTask } from "@/lib/runtime-task";

import { RuntimeTaskLocalRunSuperseded } from "./runtime-task-executor";
import { RuntimeTaskDuration, RuntimeTaskObservation } from "./runtime-task-list";
import {
  RuntimeTaskPhase,
  RuntimeTaskPhaseDescription,
  runtimePhaseLabel,
} from "./runtime-task-phase";

const INVOCATION_REFRESH_MS = 5_000;

/** 按当前执行窗口隔离实时内容与历史记录；折叠历史时不读取调用或日志。 */
export function RuntimeTaskRun({ task }: { task: RuntimeTaskEntry }) {
  const [historyOpen, setHistoryOpen] = useState(
    task.status === "failed" || task.status === "blocked" || task.status === "deferred",
  );
  const historical = task.status !== "running" || Boolean(task.finished_at || task.last_outcome);
  const hasRunRecord = Boolean(task.started_at || task.last_outcome || task.last_error_summary);

  return (
    <>
      {isQueuedRuntimeTask(task) ? (
        <p className="text-sm text-muted-foreground" data-testid="runtime-task-queued-notice">
          等待 Runner 认领，本轮尚未开始。历史记录不代表本轮执行结果。
        </p>
      ) : null}
      {!hasRunRecord ? (
        <p className="text-sm text-muted-foreground">暂无可查询的运行记录。</p>
      ) : historical ? (
        <section className="rounded-md border border-border p-3" data-testid="runtime-task-history">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={historyOpen}
            onClick={() => setHistoryOpen((open) => !open)}
            data-testid="runtime-task-history-toggle"
          >
            {historyOpen ? "收起历史运行记录" : "查看历史运行记录"}
          </Button>
          {historyOpen ? (
            <>
              <RuntimeTaskObservation task={task} showLastOutcome />
              <RuntimeTaskRunContent task={task} historical />
            </>
          ) : null}
        </section>
      ) : (
        <RuntimeTaskRunContent task={task} historical={false} />
      )}
    </>
  );
}

/** 展示已选定运行窗口的错误、调用与输出；历史输出只读取一次。 */
function RuntimeTaskRunContent({
  task,
  historical,
}: {
  task: RuntimeTaskEntry;
  historical: boolean;
}) {
  const [invocationsOpen, setInvocationsOpen] = useState(true);
  const [invocationResult, setInvocationResult] = useState<{
    key: string;
    rows: RunInvocationDetailEntry[] | null;
    error: string | null;
  } | null>(null);
  const {
    started_at: startedAt,
    finished_at: finishedAt,
    invocation_run_id: invocationRunId,
  } = task;
  const invocationKey = JSON.stringify([
    task.repo_id,
    task.issue_number,
    invocationRunId ?? [startedAt, finishedAt],
  ]);
  const currentResult = invocationResult?.key === invocationKey ? invocationResult : null;
  const invocations = currentResult?.rows ?? null;
  const invocationError = currentResult?.error ?? null;
  const liveInvocations = !historical && task.status === "running" && !finishedAt;
  const hasRunWindow = Boolean(startedAt && (finishedAt || liveInvocations));

  useEffect(() => {
    if (!invocationsOpen || !startedAt || (!finishedAt && !liveInvocations)) {
      return;
    }
    const controller = new AbortController();
    let refreshTimer: number | undefined;
    const readInvocations = async () => {
      try {
        const details = await fetchRunExecutionDetails({
          repoId: task.repo_id,
          issueNumber: task.issue_number,
          startedAt,
          finishedAt: finishedAt ?? undefined,
          invocationRunId,
          signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          setInvocationResult({
            key: invocationKey,
            rows: details.invocations,
            error: null,
          });
        }
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setInvocationResult({
            key: invocationKey,
            rows: null,
            error: error instanceof Error ? error.message : "结构化调用读取失败",
          });
        }
      } finally {
        if (liveInvocations && !controller.signal.aborted) {
          refreshTimer = window.setTimeout(() => void readInvocations(), INVOCATION_REFRESH_MS);
        }
      }
    };
    void readInvocations();
    return () => {
      controller.abort();
      window.clearTimeout(refreshTimer);
    };
  }, [
    invocationsOpen,
    liveInvocations,
    task.repo_id,
    task.issue_number,
    startedAt,
    finishedAt,
    invocationRunId,
    invocationKey,
  ]);

  return (
    <div className="flex min-h-0 flex-col gap-3" data-testid="runtime-task-run-content">
      {historical ? (
        <>
          <RuntimeTaskPhase task={task} historical />
          <RuntimeTaskPhaseDescription task={task} historical />
        </>
      ) : null}
      {historical && task.last_error_summary ? (
        <p
          className="break-words whitespace-pre-wrap text-amber-800 dark:text-amber-300"
          data-testid="runtime-task-error-summary"
        >
          {task.last_outcome === "blocked"
            ? "上次运行阻塞原因"
            : task.last_outcome === "failed"
              ? "上次运行失败原因"
              : task.last_outcome === "deferred"
                ? "上次运行暂缓原因"
                : "最近运行错误"}
          ：{task.last_error_summary}
        </p>
      ) : null}
      {historical && task.failure_reason ? (
        <p
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          data-testid="runtime-task-diagnosed-failure"
        >
          上次运行失败阶段：
          {task.failure_stage ? runtimePhaseLabel(task.failure_stage) : "未知"}。
          {task.failure_reason}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <RuntimeTaskLocalRunSuperseded task={task} />
        {task.agent ? <span>执行器：{task.agent}</span> : null}
        {task.preset ? <span>Preset：{task.preset}</span> : null}
        {task.model ? <span>最近上报模型：{task.model}</span> : null}
        <span>尝试：{task.attempt_number ?? "—"}</span>
        <span>
          开始：
          {task.started_at ? formatLocalDateTime(task.started_at) : "未知"}
        </span>
        <span>
          结束：
          {task.finished_at
            ? formatLocalDateTime(task.finished_at)
            : task.started_at
              ? "未记录结束时间"
              : "无运行记录"}
        </span>
        <RuntimeTaskDuration task={task} historical={historical} />
        <span>
          运行记录终态：
          {task.last_outcome ?? (task.started_at ? "未收口" : "无运行记录")}
        </span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100">
            {historical ? "历史输出" : "实时输出"}
          </h3>
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
              <p className="text-xs text-slate-500">该任务暂无可查询的运行记录。</p>
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
                      <span className="min-w-0 break-words text-slate-600 dark:text-slate-300">
                        {describeInvocationModel(invocation)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ) : null}
        {/* 日志面板自带 offset 续读、暂停与新尝试切换，原始文本不做任何过滤。 */}
        <div className="flex min-h-[16rem] flex-1 flex-col">
          {startedAt ? (
            <PrdIssueOutputView
              repoId={task.repo_id}
              issueNumber={task.issue_number}
              mode={historical ? "history" : "live"}
            />
          ) : (
            <p className="text-xs text-muted-foreground">没有可关联的运行记录，暂不加载日志。</p>
          )}
        </div>
      </div>
    </div>
  );
}
