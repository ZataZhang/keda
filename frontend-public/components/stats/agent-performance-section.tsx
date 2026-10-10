"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatLifecycleDuration } from "@/components/backlog/prd-lifecycle-view";
import type {
  AgentPerformanceStats,
  AttemptPerformanceGroup,
  RunOutcomePerformanceGroup,
} from "@/lib/api/types";

type AgentPerformanceSectionProps = {
  stats: AgentPerformanceStats | null;
  error: string | null;
};

/**
 * 渲染 Stats 页新增的 Agent attempt、历史 preset 和整项 run 耗时汇总。
 *
 * @param props.stats - 后端聚合的执行表现数据；null 表示尚未取得结果。
 * @param props.error - 新统计端点的独立读取错误。
 * @returns 执行表现卡片及其加载、错误或空数据状态。
 */
export function AgentPerformanceSection({
  stats,
  error,
}: AgentPerformanceSectionProps) {
  const hasAttemptGroups = Boolean(stats?.agents.length || stats?.presets.length);
  const hasRunGroups = Boolean(stats?.runs.length);

  return (
    <Card data-testid="stats-agent-performance">
      <CardHeader>
        <CardTitle className="text-sm">Agent 与预设执行表现</CardTitle>
        <CardDescription>
          Agent / 预设按单次 attempt 统计；整项任务按最终结果独立统计。耗时包含该组全部有效样本。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {error ? (
          <p
            className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
            data-testid="stats-agent-performance-error"
            role="status"
          >
            新统计暂不可用：{error}
          </p>
        ) : null}

        {stats === null && !error ? <Skeleton className="h-24" /> : null}

        {stats ? (
          <>
            {!hasAttemptGroups && !hasRunGroups ? (
              <p className="text-sm text-slate-500" data-testid="stats-agent-performance-empty">
                所选仓库和时间范围内暂无 Agent attempt 或整项任务记录。
              </p>
            ) : null}

            <section aria-label="按 Agent 的 attempt 统计">
              <h3 className="mb-2 text-sm font-medium">按 Agent（attempt）</h3>
              <AttemptPerformanceTable
                groups={stats.agents}
                showRepository={stats.repo_id === null}
                showPreset={false}
                testId="stats-agent-performance-agent-table"
              />
            </section>

            <section aria-label="按历史预设的 attempt 统计">
              <h3 className="mb-2 text-sm font-medium">按历史预设与模型（attempt）</h3>
              <AttemptPerformanceTable
                groups={stats.presets}
                showRepository={stats.repo_id === null}
                showPreset
                testId="stats-agent-performance-preset-table"
              />
              <p className="mt-2 text-xs text-slate-500" data-testid="stats-agent-performance-unbound">
                未绑定 / 历史未记录预设：{stats.unbound_preset_attempt_count} 次
              </p>
            </section>

            <section aria-label="按最终结果的整项任务耗时">
              <h3 className="mb-2 text-sm font-medium">整项任务耗时（按最终结果）</h3>
              <RunOutcomeTable
                groups={stats.runs}
                showRepository={stats.repo_id === null}
              />
            </section>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AttemptPerformanceTable({
  groups,
  showRepository,
  showPreset,
  testId,
}: {
  groups: AttemptPerformanceGroup[];
  showRepository: boolean;
  showPreset: boolean;
  testId: string;
}) {
  if (groups.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid={`${testId}-empty`}>
        暂无可用样本，成功率与耗时显示为 —。
      </p>
    );
  }

  return (
    <div className="overflow-x-auto" data-testid={testId}>
      <table className="w-full min-w-[920px] text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
            {showRepository ? <th className="py-2 pr-3">仓库</th> : null}
            {showPreset ? <th className="py-2 pr-3">预设</th> : null}
            {showPreset ? <th className="py-2 pr-3">模型</th> : null}
            <th className="py-2 pr-3">Agent</th>
            <th className="py-2 pr-3">样本</th>
            <th className="py-2 pr-3">成功 / 非成功</th>
            <th className="py-2 pr-3">成功率 / 非成功率</th>
            <th className="py-2 pr-3">P50</th>
            <th className="py-2 pr-3">P90</th>
            <th className="py-2 pr-3">失败分类</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((attemptGroup) => (
            <tr
              key={`${attemptGroup.repo_id}|${attemptGroup.preset ?? ""}|${attemptGroup.agent}|${attemptGroup.model ?? ""}`}
              className="border-b border-slate-100 dark:border-slate-800"
            >
              {showRepository ? <td className="py-2 pr-3">{attemptGroup.repo_id}</td> : null}
              {showPreset ? <td className="py-2 pr-3 font-medium">{attemptGroup.preset}</td> : null}
              {showPreset ? <td className="py-2 pr-3">{attemptGroup.model ?? "未记录"}</td> : null}
              <td className="py-2 pr-3">{attemptGroup.agent}</td>
              <td className="py-2 pr-3">{attemptGroup.attempt_count}</td>
              <td className="py-2 pr-3">
                {attemptGroup.success_count} / {attemptGroup.non_success_count}
              </td>
              <td className="py-2 pr-3">
                {formatRate(attemptGroup.success_rate)} / {formatRate(attemptGroup.non_success_rate)}
              </td>
              <td className="py-2 pr-3">
                {formatLifecycleDuration(attemptGroup.p50_duration_seconds)}
              </td>
              <td className="py-2 pr-3">
                {formatLifecycleDuration(attemptGroup.p90_duration_seconds)}
              </td>
              <td className="py-2 pr-3 text-xs">
                {attemptGroup.failure_types.length === 0
                  ? "—"
                  : attemptGroup.failure_types
                      .map((failureType) => `${failureType.failure_type}: ${failureType.count}`)
                      .join("、")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunOutcomeTable({
  groups,
  showRepository,
}: {
  groups: RunOutcomePerformanceGroup[];
  showRepository: boolean;
}) {
  if (groups.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid="stats-agent-performance-runs-empty">
        暂无整项任务结果记录。
      </p>
    );
  }

  return (
    <div className="overflow-x-auto" data-testid="stats-agent-performance-run-table">
      <table className="w-full min-w-[520px] text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
            {showRepository ? <th className="py-2 pr-3">仓库</th> : null}
            <th className="py-2 pr-3">最终结果</th>
            <th className="py-2 pr-3">任务数</th>
            <th className="py-2 pr-3">P50</th>
            <th className="py-2 pr-3">P90</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((runGroup) => (
            <tr
              key={`${runGroup.repo_id}|${runGroup.outcome}`}
              className="border-b border-slate-100 dark:border-slate-800"
            >
              {showRepository ? <td className="py-2 pr-3">{runGroup.repo_id}</td> : null}
              <td className="py-2 pr-3">{runGroup.outcome}</td>
              <td className="py-2 pr-3">{runGroup.run_count}</td>
              <td className="py-2 pr-3">
                {formatLifecycleDuration(runGroup.p50_duration_seconds)}
              </td>
              <td className="py-2 pr-3">
                {formatLifecycleDuration(runGroup.p90_duration_seconds)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatRate(rate: number): string {
  return `${Math.round(rate * 1000) / 10}%`;
}
