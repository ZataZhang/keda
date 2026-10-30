"use client";

// 评测 run 详情面板：候选结果、逐评审评分与派生聚合（聚合值由明细实时计算）。

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatLocalDateTime } from "@/lib/utils";
import type {
  EvaluationDimensionAggregate,
  EvaluationResult,
  EvaluationRunDetail,
} from "@/lib/api/types";
import {
  CHECK_STATUS_META,
  DIMENSION_LABELS,
  RESULT_STATUS_META,
  RUN_STATUS_META,
  SCORE_STATUS_META,
  formatAggregateMedian,
  formatEvaluationDuration,
} from "./evaluation-format";

type RunDetailPanelProps = {
  detail: EvaluationRunDetail | null;
  loading: boolean;
  busy: boolean;
  onStart: () => void;
  onCancel: () => void;
  onDelete: () => void;
  onOpenConsent: () => void;
  onRefresh: () => void;
};

/** 展示所选 run 的动作区、快照信息、汇总表、候选结果表与评分明细表。 */
export function RunDetailPanel({
  detail,
  loading,
  busy,
  onStart,
  onCancel,
  onDelete,
  onOpenConsent,
  onRefresh,
}: RunDetailPanelProps) {
  if (loading || detail === null) {
    return (
      <Card className="gap-4 border-border/70 shadow-sm">
        <CardHeader>
          <CardTitle className="text-sm">Run 详情</CardTitle>
          <CardDescription>正在读取评测明细…</CardDescription>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-40" />
        </CardContent>
      </Card>
    );
  }

  const { run, results, scores, summary, payload_state } = detail;
  const calibrationRun = run.execution_preset_snapshots.some(
    (preset) => preset.kind === "calibration",
  );
  const statusMeta = RUN_STATUS_META[run.status];
  const cancellable = [
    "created",
    "queued",
    "running",
    "awaiting_judge_consent",
  ].includes(run.status);

  return (
    <Card className="gap-4 border-border/70 shadow-sm" data-testid="evaluation-run-detail">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm">
              <code className="text-sm">{run.run_id}</code>
              {statusMeta ? (
                <Badge variant={statusMeta.variant}>{statusMeta.label}</Badge>
              ) : (
                <Badge>{run.status}</Badge>
              )}
            </CardTitle>
            <CardDescription className="mt-1">
              suite {run.suite_id} v{run.suite_version} · 基线 {run.base_ref}（
              {run.base_commit.slice(0, 10)}）· rubric {run.rubric_version} ·
              创建于 {formatLocalDateTime(run.created_at)}
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={onRefresh}>
              刷新
            </Button>
            {run.status === "created" ? (
              <Button size="sm" disabled={busy} onClick={onStart}>
                启动执行
              </Button>
            ) : null}
            {run.status === "awaiting_judge_consent" ? (
              <Button size="sm" disabled={busy} onClick={onOpenConsent}>
                查看 payload 并授权
              </Button>
            ) : null}
            {cancellable ? (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={onCancel}
              >
                取消
              </Button>
            ) : null}
            {!payload_state.is_active ? (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={onDelete}
              >
                删除
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={onDelete}
              >
                强制删除
              </Button>
            )}
          </div>
        </div>
        {run.error ? (
          <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-800 dark:bg-red-950 dark:text-red-200">
            {run.error}
          </p>
        ) : null}
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>
            执行预设：
            {run.execution_preset_snapshots
              .map(
                (preset) =>
                  `${preset.preset_name}（${preset.agent ?? "?"} · ${preset.model ?? "跟随 agent 默认"}）`,
              )
              .join("；")}
          </span>
          <span>
            评审预设：
            {run.judge_preset_snapshots.length === 0
              ? "未选择（仅确定性对照）"
              : run.judge_preset_snapshots
                  .map(
                    (preset) =>
                      `${preset.preset_name}（${preset.agent ?? "?"} · ${preset.model ?? "默认模型"}）`,
                  )
                  .join("；")}
          </span>
          <span>
            费用口径：cost unknown（未接费用源，仅记 token usage）
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {scores.length > 0 ? (
          <section aria-label="评审维度汇总">
            <h3 className="mb-2 text-sm font-semibold">
              评审维度汇总 · 仅由 valid 评分派生（有效评审 × {summary.valid_judge_count}）
            </h3>
            <AggregateTable title="全部执行预设" aggregates={summary.aggregates} />
            {Object.entries(summary.by_execution_preset).map(([preset, aggregates]) => (
              <AggregateTable
                key={preset}
                title={`执行预设：${preset}`}
                aggregates={aggregates}
              />
            ))}
            {Object.keys(summary.status_counts).length > 0 ? (
              <p className="mt-2 rounded-lg bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                非评分状态计数：
                {Object.entries(summary.status_counts)
                  .map(([bucket, count]) => `${bucket} × ${count}`)
                  .join("；")}
                （失败/拒绝/未授权不进入评分汇总）
              </p>
            ) : null}
          </section>
        ) : null}

        {calibrationRun ? (
          <CalibrationResults
            caseIds={run.case_ids}
            results={results}
            repeats={run.repeats}
          />
        ) : null}

        <section aria-label="重复结果稳定性" data-testid="evaluation-stability">
          <h3 className="mb-2 text-sm font-semibold">重复结果</h3>
          {detail.stability.length === 0 ? (
            <p className="text-sm text-muted-foreground">候选尚未开始。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="rounded-l-lg px-3 py-2 font-medium">题目</th>
                    <th className="px-3 py-2 font-medium">执行预设</th>
                    <th className="px-3 py-2 font-medium">判分通过</th>
                    <th className="px-3 py-2 font-medium">稳定性</th>
                    <th className="rounded-r-lg px-3 py-2 font-medium">执行故障</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.stability.map((entry) => (
                    <tr
                      key={`${entry.case_id}-${entry.execution_preset_id}`}
                      className="border-b last:border-b-0"
                    >
                      <td className="px-3 py-2 font-mono text-xs">{entry.case_id}</td>
                      <td className="px-3 py-2 text-xs">{entry.execution_preset_id}</td>
                      <td className="px-3 py-2 text-xs">
                        {entry.passed_count}/{entry.repeat_count} 次通过
                        {entry.check_failure_count > 0
                          ? ` · ${entry.check_failure_count} 次判分失败`
                          : ""}
                      </td>
                      <td className="px-3 py-2">
                        {entry.stable === null ? (
                          <Badge>判分未齐</Badge>
                        ) : (
                          <Badge variant={entry.stable ? "ready" : "warning"}>
                            {entry.stable ? "稳定" : "不稳定"}
                          </Badge>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs">{entry.execution_fault_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {detail.unstable_cases.length > 0 ? (
            <div className="mt-3 rounded-lg border border-amber-300/70 bg-amber-50/60 p-3 dark:border-amber-900 dark:bg-amber-950/30">
              <h4 className="mb-2 text-xs font-semibold">不稳定的题（{detail.unstable_cases.length}）</h4>
              <ul className="space-y-1 text-xs">
                {detail.unstable_cases.map((entry) => (
                  <li key={`${entry.case_id}-${entry.execution_preset_id}`}>
                    {entry.case_id} · {entry.execution_preset_id} · {entry.passed_count}/{entry.repeat_count} 次通过
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>

        <section aria-label="候选结果">
          <h3 className="mb-2 text-sm font-semibold">候选结果（{results.length}）</h3>
          {results.length === 0 ? (
            <p className="text-sm text-slate-500">候选尚未开始。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-left text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="rounded-l-lg px-3 py-2 font-medium">Case</th>
                    <th className="px-3 py-2 font-medium">执行预设</th>
                    <th className="px-3 py-2 font-medium">结果</th>
                    <th className="px-3 py-2 font-medium">检查</th>
                    <th className="px-3 py-2 text-right font-medium">耗时</th>
                    <th className="px-3 py-2 text-right font-medium">Tokens</th>
                    <th className="rounded-r-lg px-3 py-2 font-medium">备注</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((result) => {
                    const resultMeta = RESULT_STATUS_META[result.result_status];
                    const checkMeta = CHECK_STATUS_META[result.check_status];
                    const usage = result.usage_summary as
                      | { total_tokens?: number | null }
                      | null;
                    return (
                      <tr key={result.result_id} className="border-b last:border-b-0 align-top">
                        <td className="px-3 py-2 font-mono text-xs">
                          {result.case_id}
                          {run.repeats > 1 ? (
                            <span className="block text-muted-foreground">第 {result.repeat_index + 1} 次</span>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 text-xs">{result.execution_preset_id}</td>
                        <td className="px-3 py-2">
                          {resultMeta ? (
                            <Badge variant={resultMeta.variant}>
                              {resultMeta.label}
                            </Badge>
                          ) : (
                            <Badge>{result.result_status}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          {checkMeta ? (
                            <Badge variant={checkMeta.variant}>
                              {checkMeta.label}
                              {result.check_exit_code !== null
                                ? ` (exit ${result.check_exit_code})`
                                : ""}
                            </Badge>
                          ) : (
                            <Badge>{result.check_status}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs">
                          {formatEvaluationDuration(result.duration_seconds)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs">
                          {usage?.total_tokens ?? "—"}
                        </td>
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {result.error ?? "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section aria-label="逐评审评分明细">
          <h3 className="mb-2 text-sm font-semibold">
            逐评审评分明细（{scores.length}）
          </h3>
          {scores.length === 0 ? (
            <p className="text-sm text-slate-500">
              {payload_state.awaiting_consent
                ? "候选已完成，等待外发授权；授权后此处出现评分。"
                : "暂无评分记录。"}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-left text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="rounded-l-lg px-3 py-2 font-medium">Case</th>
                    <th className="px-3 py-2 font-medium">执行 / 评审</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                    <th className="px-3 py-2 font-medium">
                      {[
                        "correctness",
                        "completeness",
                        "implementation_quality",
                        "verification_evidence",
                      ].map((key) => (
                        <span key={key}>
                          {DIMENSION_LABELS[key]?.split(" ")[0] ?? key}{" "}
                        </span>
                      ))}
                    </th>
                    <th className="rounded-r-lg px-3 py-2 font-medium">理由 / 错误</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((score) => {
                    const meta = SCORE_STATUS_META[score.score_status];
                    return (
                      <tr key={score.score_id} className="border-b last:border-b-0 align-top">
                        <td className="px-3 py-2 font-mono text-xs">
                          {score.case_id}
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {score.execution_preset_id}
                          <br />
                          <span className="text-muted-foreground">
                            judge: {score.judge_preset_id}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          {meta ? (
                            <Badge variant={meta.variant}>{meta.label}</Badge>
                          ) : (
                            <Badge>{score.score_status}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs tabular-nums">
                          {Object.entries(score.dimension_scores)
                            .map(([dimension, value]) => (
                              <span key={dimension} className="mr-2">
                                {DIMENSION_LABELS[dimension]?.slice(0, 2) ??
                                  dimension.slice(0, 2)}{" "}
                                {value}
                              </span>
                            ))}
                          {Object.keys(score.dimension_scores).length === 0
                            ? "—"
                            : ""}
                        </td>
                        <td className="max-w-[320px] px-3 py-2 text-xs text-muted-foreground">
                          {score.rationale_summary ?? score.error ?? "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

/** 单组维度聚合的小表格（median / min / max / N）。 */
function CalibrationResults({
  caseIds,
  results,
  repeats,
}: {
  caseIds: string[];
  results: EvaluationResult[];
  repeats: number;
}) {
  const caseStates = caseIds.map((caseId) => {
    const noopResults = results.filter(
      (result) => result.case_id === caseId && result.execution_preset_id === "@noop",
    );
    const goldResults = results.filter(
      (result) => result.case_id === caseId && result.execution_preset_id === "@gold",
    );
    const noopValid =
      noopResults.length === repeats &&
      noopResults.every(
        (result) => result.result_status === "succeeded" && result.check_status === "failed",
      );
    const goldValid =
      goldResults.length === repeats &&
      goldResults.every(
        (result) => result.result_status === "succeeded" && result.check_status === "passed",
      );
    return { caseId, noopResults, goldResults, calibrated: noopValid && goldValid };
  });

  return (
    <section aria-label="逐题校准结果" data-testid="evaluation-calibration-results">
      <h3 className="mb-2 text-sm font-semibold">逐题校准结果</h3>
      <div className="space-y-1 text-xs">
        {caseStates.map((caseState) => (
          <p key={caseState.caseId}>
            <span className="font-mono">{caseState.caseId}</span>
            {" · "}@noop {caseState.noopResults.filter((result) => result.check_status === "failed").length}/{repeats} 次判分失败
            {" · "}@gold {caseState.goldResults.filter((result) => result.check_status === "passed").length}/{repeats} 次判分通过
            {" · "}
            <Badge variant={caseState.calibrated ? "ready" : "warning"}>
              {caseState.calibrated ? "校准通过" : "校准中 / 未通过"}
            </Badge>
          </p>
        ))}
      </div>
    </section>
  );
}

/** 单组维度聚合的小表格（median / min / max / N）。 */
function AggregateTable({
  title,
  aggregates,
}: {
  title: string;
  aggregates: EvaluationDimensionAggregate[];
}) {
  return (
    <div className="mb-3">
      <p className="mb-1 text-xs font-medium text-muted-foreground">{title}</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {aggregates.map((aggregate) => (
          <div
            key={`${title}-${aggregate.dimension}`}
            data-testid={`evaluation-aggregate-${aggregate.dimension}`}
            className="rounded-xl border bg-muted/20 p-3"
          >
            <p className="text-xs text-muted-foreground">
              {DIMENSION_LABELS[aggregate.dimension] ?? aggregate.dimension}
            </p>
            <p className="mt-1 text-lg font-semibold tabular-nums">
              {formatAggregateMedian(aggregate.median, aggregate.valid_count)}
            </p>
            <p className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
              min {aggregate.minimum ?? "—"} · max {aggregate.maximum ?? "—"} · N{" "}
              {aggregate.valid_count}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
