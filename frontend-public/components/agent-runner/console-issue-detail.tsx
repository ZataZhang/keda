// 「全部」视图选中未监控 Issue 时的轻量详情：基础信息 + 标签编辑（FR-4/FR-5）。

import { IconExternalLink } from "@tabler/icons-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ConsoleIssueEntry } from "@/lib/api/types";

import { IssueLabelEditor } from "@/components/agent-runner/issue-label-editor";
import { prettyLabel, variantForLabel } from "@/components/agent-runner/label-variant";

interface ConsoleIssueDetailProps {
  repoId: string;
  entry: ConsoleIssueEntry;
}

/**
 * 未收录进监控快照的 Issue 的详情面板。
 *
 * 监控详情端点只解析带队列标签的 Issue，这里改用全量列表条目 +
 * 标签编辑接口：入队即通过标签面板打 `agent/ready`（等价 CLI 打标）。
 *
 * @param props.repoId - 仓库 ID。
 * @param props.entry - 全量 Issue 列表行。
 */
export function ConsoleIssueDetail({ repoId, entry }: ConsoleIssueDetailProps) {
  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1" data-testid="console-issue-detail">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-1">
            <CardTitle className="text-base">
              <span className="font-mono text-xs text-slate-500">
                #{entry.number}
              </span>{" "}
              {entry.title}
            </CardTitle>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              {!entry.monitored ? (
                <Badge variant="default" data-testid="console-issue-unqueued-badge">
                  未入队
                </Badge>
              ) : null}
              <span>{entry.state === "OPEN" ? "open" : "closed"}</span>
              {entry.url ? (
                <a
                  href={entry.url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-auto inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"
                >
                  <IconExternalLink className="size-3" />
                  GitHub
                </a>
              ) : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {entry.labels.length > 0 ? (
              entry.labels.map((label) => (
                <Badge key={label} variant={variantForLabel(label)} className="text-xs">
                  {prettyLabel(label)}
                </Badge>
              ))
            ) : (
              <span className="text-xs text-slate-500">无标签。</span>
            )}
          </div>
          <p className="text-xs text-slate-500">
            该 Issue 尚未进入 agent 流水线；在下方标签面板打{" "}
            <code className="font-mono">agent/ready</code>{" "}
            即可加入就绪队列（不会直接启动 runner）。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">标签（可编辑，范围限标准集）</CardTitle>
        </CardHeader>
        <CardContent>
          <IssueLabelEditor repoId={repoId} issueNumber={entry.number} />
        </CardContent>
      </Card>
    </div>
  );
}
