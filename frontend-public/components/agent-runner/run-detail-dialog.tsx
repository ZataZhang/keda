"use client";

import { useEffect, useState } from "react";

import { formatLifecycleDuration } from "@/components/backlog/prd-lifecycle-view";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { fetchRunExecutionDetails } from "@/lib/api/console";
import type {
  RunAttemptDetailEntry,
  RunInvocationDetailEntry,
  RunRecordEntry,
} from "@/lib/api/types";
import { formatLocalDateTime } from "@/lib/utils";

type RunDetailDialogProps = {
  run: RunRecordEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type RunFailureExplanation = {
  stage: string;
  title: string;
  explanation: string;
  nextStep: string | null;
};

const RUN_OUTCOME_LABELS: Record<RunRecordEntry["outcome"], string> = {
  completed: "成功",
  failed: "失败",
  blocked: "被阻塞",
};

const LIFECYCLE_PHASE_LABELS: Record<string, string> = {
  agent: "Agent 执行",
  verification: "代码验证",
  prd_delivery: "PRD 交付检查",
  evidence: "交付证据检查",
  commit: "提交与推送",
  closeout: "交付收尾",
  rv_reexec: "提交后验收重跑",
  verifier: "独立复核",
};

const INVOCATION_PHASE_LABELS: Record<string, string> = {
  implementation: "编写代码",
  fix: "修复问题",
  review: "代码审查",
  review_repair: "按审查意见修复",
  verification: "运行验证",
  verification_recovery: "修复验证问题",
  rebase_recovery: "同步分支并处理冲突",
  closeout: "交付收尾",
  supervisor: "自动检查进度",
  supervisor_repair: "检查发现问题后的修复",
  content_generation: "生成任务内容",
  unspecified: "Agent 工作",
};

const INVOCATION_ROLE_LABELS: Record<string, string> = {
  implementer: "负责修改代码",
  fixer: "负责修复问题",
  reviewer: "负责审查代码",
  verifier: "负责运行验证",
  supervisor: "负责检查进度",
  content_generator: "负责生成内容",
  unspecified: "角色未记录",
};

/** 将运行结果的机器错误摘要整理成先给人看的失败结论。 */
function getRunFailureExplanation(
  run: RunRecordEntry,
  attemptDetails: RunAttemptDetailEntry[],
  invocationDetails: RunInvocationDetailEntry[],
): RunFailureExplanation | null {
  if (run.outcome !== "failed") {
    return null;
  }

  const errorSummary = run.error_summary ?? "";
  if (
    /failed to push some refs|non-fast-forward|tip of your current branch is behind/i.test(
      errorSummary,
    )
  ) {
    const invocationSummary =
      invocationDetails.length > 0 &&
      invocationDetails.every((invocationDetail) => invocationDetail.outcome === "ok")
        ? `${invocationDetails.length} 次 Agent 调用均正常退出`
        : "Agent 调用状态见下方时间线";

    return {
      stage: "推送到 GitHub",
      title: "远端分支比本地更新，GitHub 拒绝了推送",
      explanation:
        `Issue #${run.issue_number} 对应的远端分支上有本地没有的提交。` +
        `${invocationSummary}。之后运行器推送代码时被 GitHub 拒绝，所以整次运行结果是失败。`,
      nextStep: "先把远端新提交整合进本地分支，再重新推送。",
    };
  }

  if (/BrokenPipeError|broken pipe/i.test(errorSummary)) {
    return {
      stage: "Agent 启动",
      title: "Agent 启动命令的通信管道提前关闭",
      explanation:
        "运行器向 Agent 进程发送数据时连接已经关闭，因此 Agent 没能进入后续验证。" +
        "这条日志没有记录管道关闭的更深层原因。",
      nextStep: null,
    };
  }

  if (/acceptance checklist|验收清单/i.test(errorSummary)) {
    return {
      stage: "PRD 交付检查",
      title: "PRD 验收清单未通过",
      explanation: "交付检查发现验收清单仍有未勾选项，因此运行被拦下。",
      nextStep: "补齐有证据支持的验收项后再运行。",
    };
  }

  const failedAttempt = [...attemptDetails]
    .reverse()
    .find((attemptDetail) => attemptDetail.failure_phase);
  const failureStage = failedAttempt?.failure_phase
    ? (LIFECYCLE_PHASE_LABELS[failedAttempt.failure_phase] ??
      failedAttempt.failure_phase)
    : null;

  return {
    stage: failureStage ?? "阶段未记录",
    title: "本次运行未完成",
    explanation: failureStage
      ? `记录显示运行在“${failureStage}”阶段失败。`
      : "没有保存更具体的失败阶段；可以展开下方原始错误信息查看运行器记录。",
    nextStep: null,
  };
}

/** 用准确但易读的文字说明模型名称来自哪里。 */
function describeInvocationModel(invocationDetail: RunInvocationDetailEntry): string {
  const requestedModel = invocationDetail.requested_model;
  const reportedModel = invocationDetail.reported_model;

  if (requestedModel && reportedModel && requestedModel !== reportedModel) {
    return `KC 下发：${requestedModel}；执行器报告：${reportedModel}`;
  }
  if (requestedModel && reportedModel) {
    return `KC 下发且执行器报告：${requestedModel}`;
  }
  if (requestedModel) {
    return `KC 下发：${requestedModel}；执行器没有报告模型名称`;
  }
  if (reportedModel) {
    return `执行器报告：${reportedModel}；KC 没有单独指定模型`;
  }
  return "无法确认具体模型：执行器没有上报，KC 也没有单独指定。";
}

/** 显示一次运行的失败结论、实际 Agent 调用时间线和详细记录。 */
export function RunDetailDialog({
  run,
  open,
  onOpenChange,
}: RunDetailDialogProps) {
  const [attemptDetails, setAttemptDetails] = useState<RunAttemptDetailEntry[]>([]);
  const [invocationDetails, setInvocationDetails] = useState<RunInvocationDetailEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || run === null) {
      return;
    }

    let isCurrentRequest = true;
    setAttemptDetails([]);
    setInvocationDetails([]);
    setLoadError(null);
    setIsLoading(true);
    fetchRunExecutionDetails({
      repoId: run.repo_id,
      issueNumber: run.issue_number,
      startedAt: run.started_at,
      finishedAt: run.finished_at,
    })
      .then((loadedRunDetails) => {
        if (isCurrentRequest) {
          setAttemptDetails(loadedRunDetails.attempts);
          setInvocationDetails(loadedRunDetails.invocations);
        }
      })
      .catch((error: unknown) => {
        if (isCurrentRequest) {
          setLoadError(
            error instanceof Error ? error.message : "无法加载运行详情。",
          );
        }
      })
      .finally(() => {
        if (isCurrentRequest) {
          setIsLoading(false);
        }
      });

    return () => {
      isCurrentRequest = false;
    };
  }, [open, run]);

  const failureExplanation = run
    ? getRunFailureExplanation(run, attemptDetails, invocationDetails)
    : null;
  const chronologicalInvocations = [...invocationDetails].sort((first, second) =>
    (first.started_at ?? "").localeCompare(second.started_at ?? ""),
  );
  const recordedInvocationSeconds = chronologicalInvocations.reduce(
    (totalSeconds, invocationDetail) =>
      totalSeconds + (invocationDetail.duration_seconds ?? 0),
    0,
  );
  const hasUnrecordedInvocationDuration = chronologicalInvocations.some(
    (invocationDetail) => invocationDetail.duration_seconds === null,
  );
  const hasRecordedInvocationDuration = chronologicalInvocations.some(
    (invocationDetail) => invocationDetail.duration_seconds !== null,
  );

  return (
    <Dialog open={open && run !== null} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="run-details-dialog"
        className="max-h-[85vh] max-w-3xl overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle>
            {run === null
              ? "运行详情"
              : `Issue #${run.issue_number} · ${RUN_OUTCOME_LABELS[run.outcome]}`}
          </DialogTitle>
          <DialogDescription>
            {run === null
              ? "查看本次运行结果和 Agent 执行时间线。"
              : `${run.repo_id} · ${formatLocalDateTime(run.started_at)}`}
          </DialogDescription>
          {run !== null && (
            run.issue_title || run.issue_url ? (
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <p className="font-medium text-slate-800 dark:text-slate-100">
                  {run.issue_title || "任务标题未记录"}
                </p>
                {run.issue_url && (
                  <a
                    href={run.issue_url}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`在 GitHub 打开 Issue #${run.issue_number}`}
                    className="text-xs text-blue-700 underline underline-offset-2 hover:text-blue-900 dark:text-blue-300 dark:hover:text-blue-200"
                  >
                    在 GitHub 查看 Issue ↗
                  </a>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-500">
                这条历史记录生成时未保存任务标题和 GitHub 链接。
              </p>
            )
          )}
        </DialogHeader>

        {run !== null && (
          <div className="space-y-4">
            <section className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Badge
                  variant={
                    run.outcome === "completed"
                      ? "ready"
                      : run.outcome === "failed"
                        ? "warning"
                        : "default"
                  }
                >
                  {RUN_OUTCOME_LABELS[run.outcome]}
                </Badge>
                <span className="text-xs text-slate-600 dark:text-slate-300">
                  触发方式：{run.trigger}
                </span>
                <span className="text-xs text-slate-600 dark:text-slate-300">
                  Agent：{run.agent || "未记录"}
                </span>
                <span className="text-xs text-slate-600 dark:text-slate-300">
                  总耗时：{formatLifecycleDuration(run.duration_seconds)}
                </span>
              </div>

              <dl className="mb-3 grid gap-2 text-xs sm:grid-cols-2">
                <div>
                  <dt className="text-slate-500">开始时间</dt>
                  <dd className="mt-0.5 font-mono">
                    {formatLocalDateTime(run.started_at)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">结束时间</dt>
                  <dd className="mt-0.5 font-mono">
                    {formatLocalDateTime(run.finished_at)}
                  </dd>
                </div>
              </dl>

              {failureExplanation ? (
                <div
                  role="alert"
                  className="rounded-md border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40"
                >
                  <p className="text-xs font-medium text-amber-900 dark:text-amber-200">
                    失败阶段：{failureExplanation.stage}
                  </p>
                  <h3 className="mt-1 text-sm font-semibold text-slate-950 dark:text-slate-50">
                    {failureExplanation.title}
                  </h3>
                  <p className="mt-1 text-sm leading-6 text-slate-700 dark:text-slate-200">
                    {failureExplanation.explanation}
                  </p>
                  {failureExplanation.nextStep && (
                    <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
                      建议处理：{failureExplanation.nextStep}
                    </p>
                  )}
                </div>
              ) : (
                <div>
                  <h3 className="mb-1 text-xs font-semibold text-slate-500">
                    运行结果
                  </h3>
                  <p className="whitespace-pre-wrap break-words text-sm">
                    {run.error_summary ||
                      (run.outcome === "completed"
                        ? "本次运行已完成。"
                        : "本次运行被阻塞。")}
                  </p>
                </div>
              )}

              {run.error_summary && (
                <details className="mt-3 rounded border border-slate-200 px-3 py-2 dark:border-slate-700">
                  <summary className="cursor-pointer text-xs font-medium text-slate-600 dark:text-slate-300">
                    查看系统原始错误记录
                  </summary>
                  <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600 dark:text-slate-300">
                    {run.error_summary}
                  </pre>
                </details>
              )}
            </section>

            <section className="space-y-2">
              <div>
                <h3 className="text-sm font-semibold">Agent 执行时间线</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  按开始时间排列。每张卡片只表示该次 Agent 进程是否正常退出；整次运行结果和失败原因请看上方。
                </p>
                {chronologicalInvocations.length > 0 && (
                  <p className="mt-1 text-xs font-medium text-slate-600 dark:text-slate-300">
                    已记录 {chronologicalInvocations.length} 次调用
                    {hasRecordedInvocationDuration
                      ? ` · Agent 调用累计耗时${hasUnrecordedInvocationDuration ? "至少" : ""} ${formatLifecycleDuration(recordedInvocationSeconds)}`
                      : " · 调用耗时未记录"}
                  </p>
                )}
              </div>
              {isLoading && (
                <p className="text-sm text-slate-500">正在加载 Agent 调用…</p>
              )}
              {loadError !== null && (
                <p role="alert" className="text-sm text-red-700 dark:text-red-300">
                  {loadError}
                </p>
              )}
              {!isLoading && loadError === null && chronologicalInvocations.length === 0 && (
                <p className="rounded-md border border-slate-200 p-3 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">
                  没有保存 Agent 调用明细。若任务在 Agent 启动前失败，这是正常情况；否则可能是历史记录不完整。
                </p>
              )}
              {chronologicalInvocations.map((invocationDetail, index) => {
                const invocationFinishedNormally = invocationDetail.outcome === "ok";
                const invocationOutcomeLabel = invocationFinishedNormally
                  ? "Agent 进程正常退出"
                  : ["failed", "error", "timeout", "incomplete"].includes(
                        invocationDetail.outcome,
                      )
                    ? "Agent 进程异常结束"
                    : `状态：${invocationDetail.outcome}`;

                return (
                  <article
                    key={invocationDetail.invocation_id}
                    className="rounded-md border border-slate-200 p-3 dark:border-slate-800"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <p className="text-xs text-slate-500">第 {index + 1} 次调用</p>
                        <h4 className="mt-0.5 text-sm font-semibold">
                          {INVOCATION_PHASE_LABELS[invocationDetail.phase] ??
                            invocationDetail.phase}
                        </h4>
                        <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                          {invocationDetail.executor || "Agent 未记录"}
                          {invocationDetail.role
                            ? ` · ${INVOCATION_ROLE_LABELS[invocationDetail.role] ?? invocationDetail.role}`
                            : ""}
                        </p>
                      </div>
                      <Badge variant={invocationFinishedNormally ? "ready" : "warning"}>
                        {invocationOutcomeLabel}
                      </Badge>
                    </div>

                    <dl className="mt-3 grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
                      <div>
                        <dt className="text-slate-500">这次调用耗时</dt>
                        <dd className="mt-0.5 font-medium">
                          {formatLifecycleDuration(invocationDetail.duration_seconds)}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate-500">模型</dt>
                        <dd className="mt-0.5 break-words font-medium">
                          {describeInvocationModel(invocationDetail)}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate-500">开始时间</dt>
                        <dd className="mt-0.5 font-mono">
                          {invocationDetail.started_at
                            ? formatLocalDateTime(invocationDetail.started_at)
                            : "未记录"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate-500">结束时间</dt>
                        <dd className="mt-0.5 font-mono">
                          {invocationDetail.finished_at
                            ? formatLocalDateTime(invocationDetail.finished_at)
                            : "尚未结束"}
                        </dd>
                      </div>
                    </dl>
                  </article>
                );
              })}
              {!isLoading &&
                loadError === null &&
                chronologicalInvocations.length > 0 &&
                attemptDetails.length === 0 && (
                  <p className="text-xs text-slate-500">
                    本次没有保存完整的生命周期阶段统计；上方只列出了已记录的 Agent 调用，不能据此拆分运行总耗时。
                  </p>
                )}
            </section>

            {attemptDetails.length > 0 && (
              <details className="rounded-md border border-slate-200 dark:border-slate-800">
                <summary className="cursor-pointer px-3 py-3 text-sm font-medium">
                  重试与生命周期阶段明细（技术记录）
                </summary>
                <div className="space-y-2 border-t border-slate-200 p-3 dark:border-slate-800">
                  {attemptDetails.map((attemptDetail, index) => (
                    <article
                      key={`${attemptDetail.agent}-${attemptDetail.attempt_number}-${attemptDetail.started_at}-${index}`}
                      className="rounded-md bg-slate-50 p-3 dark:bg-slate-900"
                    >
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <Badge
                          variant={
                            attemptDetail.failure_type === "success"
                              ? "ready"
                              : "warning"
                          }
                        >
                          {attemptDetail.agent || "未知 Agent"} · {attemptDetail.failure_type}
                        </Badge>
                        <span className="text-xs text-slate-500">
                          第 {attemptDetail.attempt_number} 次 · 耗时：
                          {formatLifecycleDuration(attemptDetail.duration_seconds)}
                        </span>
                        <span className="text-xs text-slate-500">
                          {attemptDetail.failure_type === "success" ? "完成时间" : "结束时间"}：
                          {formatLocalDateTime(attemptDetail.finished_at)}
                        </span>
                        {attemptDetail.failure_type !== "success" && (
                          <span className="text-xs text-slate-500">
                            失败阶段：
                            {attemptDetail.failure_phase
                              ? (LIFECYCLE_PHASE_LABELS[attemptDetail.failure_phase] ??
                                attemptDetail.failure_phase)
                              : "未记录"}
                          </span>
                        )}
                      </div>

                      <dl className="mb-3 grid gap-2 rounded bg-white p-2 text-xs dark:bg-slate-950 sm:grid-cols-2">
                        <div>
                          <dt className="text-slate-500">生效模型</dt>
                          <dd className="mt-0.5 break-all font-medium">
                            {attemptDetail.model || "未记录"}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">模型预设</dt>
                          <dd className="mt-0.5 break-all font-medium">
                            {attemptDetail.preset || "未使用或未记录"}
                          </dd>
                        </div>
                      </dl>

                      {attemptDetail.phase_durations?.length ? (
                        <div className="mb-3 divide-y divide-slate-200 rounded border border-slate-200 px-2 dark:divide-slate-700 dark:border-slate-700">
                          {attemptDetail.phase_durations.map((phaseDuration) => (
                            <div
                              key={phaseDuration.name}
                              className="flex items-center justify-between gap-3 py-1.5 text-xs"
                            >
                              <span>
                                {LIFECYCLE_PHASE_LABELS[phaseDuration.name] ??
                                  phaseDuration.name}
                              </span>
                              <span className="shrink-0 font-mono text-slate-600 dark:text-slate-300">
                                {formatLifecycleDuration(phaseDuration.seconds)}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="mb-3 text-xs text-slate-500">
                          这条尝试没有保存阶段耗时。
                        </p>
                      )}

                      <p className="whitespace-pre-wrap break-words text-sm">
                        {attemptDetail.detail || "这次尝试没有保存详细说明。"}
                      </p>
                    </article>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
