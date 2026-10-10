"use client";

import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import type { RunRecordEntry } from "@/lib/api/types";
import { formatLocalDateTime } from "@/lib/utils";

type RecentRunGroupsProps = {
  recentRuns: RunRecordEntry[];
  onSelectRun: (run: RunRecordEntry) => void;
};

/** 按仓库和 Issue 分组呈现近期运行，并保留单次详情入口。 */
export function RecentRunGroups({
  recentRuns,
  onSelectRun,
}: RecentRunGroupsProps) {
  const recentRunGroups = useMemo(() => {
    const groupsByIssue = new Map<
      string,
      { repoId: string; issueNumber: number; runs: RunRecordEntry[] }
    >();
    for (const run of recentRuns) {
      const groupKey = JSON.stringify([run.repo_id, run.issue_number]);
      const group = groupsByIssue.get(groupKey);
      if (group) {
        group.runs.push(run);
      } else {
        groupsByIssue.set(groupKey, {
          repoId: run.repo_id,
          issueNumber: run.issue_number,
          runs: [run],
        });
      }
    }
    // API 按开始时间从新到旧返回，因此分组顺序和组内顺序都保留最近优先。
    return Array.from(groupsByIssue.values());
  }, [recentRuns]);

  return (
    <div className="space-y-2">
      {recentRunGroups.map((group) => {
        const latestRun = group.runs[0];
        const runWithIssueMetadata =
          group.runs.find((run) => run.issue_title || run.issue_url) ?? latestRun;
        const groupKey = JSON.stringify([group.repoId, group.issueNumber]);
        return (
          <details
            key={groupKey}
            className="rounded-md border border-slate-200 dark:border-slate-700"
          >
            <summary className="cursor-pointer px-3 py-3 text-sm marker:text-slate-400">
              <span className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <span className="font-medium">
                  {group.repoId} · Issue #{group.issueNumber}
                </span>
                <span className="basis-full pl-5 text-xs text-slate-600 dark:text-slate-300">
                  {runWithIssueMetadata.issue_title ? (
                    runWithIssueMetadata.issue_url ? (
                      <a
                        href={runWithIssueMetadata.issue_url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`在 GitHub 打开 Issue #${group.issueNumber}：${runWithIssueMetadata.issue_title}`}
                        title={runWithIssueMetadata.issue_title}
                        className="line-clamp-2 break-words underline underline-offset-2 hover:text-slate-900 dark:hover:text-slate-100"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {runWithIssueMetadata.issue_title} ↗
                      </a>
                    ) : (
                      <span className="line-clamp-2 break-words">
                        {runWithIssueMetadata.issue_title}
                      </span>
                    )
                  ) : (
                    <span>历史记录未保存任务标题</span>
                  )}
                </span>
                <span className="text-xs text-slate-500">
                  最近 {group.runs.length} 次运行
                </span>
                <OutcomeBadge outcome={latestRun.outcome} />
                <span className="text-xs text-slate-500">
                  最近运行：{formatLocalDateTime(latestRun.started_at)}
                </span>
              </span>
            </summary>
            <div className="overflow-x-auto border-t border-slate-100 px-3 dark:border-slate-800">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <th className="py-2 pr-3">运行时间</th>
                    <th className="py-2 pr-3">结果</th>
                    <th className="py-2 pr-3">触发</th>
                    <th className="py-2 pr-3">Agent</th>
                    <th className="py-2 pr-3">耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {group.runs.map((run, index) => (
                    <tr
                      key={`${run.started_at}-${index}`}
                      className="border-b border-slate-100 last:border-0 dark:border-slate-800"
                    >
                      <td className="py-2 pr-3 font-mono text-[11px] text-slate-500">
                        {formatLocalDateTime(run.started_at)}
                      </td>
                      <td className="py-2 pr-3">
                        <button
                          type="button"
                          aria-label={`查看 ${run.repo_id} Issue #${run.issue_number} 的运行详情`}
                          className="inline-flex items-center gap-2 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500"
                          onClick={() => onSelectRun(run)}
                        >
                          <OutcomeBadge outcome={run.outcome} />
                          <span className="text-xs text-slate-600 underline underline-offset-2 dark:text-slate-300">
                            查看详情
                          </span>
                        </button>
                      </td>
                      <td className="py-2 pr-3 text-xs">{run.trigger}</td>
                      <td className="py-2 pr-3 text-xs">{run.agent}</td>
                      <td className="py-2 pr-3 text-xs">
                        {run.duration_seconds < 60
                          ? `${Math.round(run.duration_seconds)}s`
                          : `${Math.round(run.duration_seconds / 60)}m`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}
    </div>
  );
}

/** 显示本地运行记录的完成、失败或阻塞状态。 */
function OutcomeBadge({ outcome }: { outcome: RunRecordEntry["outcome"] }) {
  const variant =
    outcome === "completed" ? "ready" : outcome === "failed" ? "warning" : "default";
  return <Badge variant={variant}>{outcome}</Badge>;
}
