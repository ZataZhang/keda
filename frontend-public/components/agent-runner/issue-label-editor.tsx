"use client";
/* eslint-disable react-hooks/set-state-in-effect */

// Issue 标签查看与编辑面板（FR-5）：增删范围限于 kc labels sync 同源标准集。

import { IconPlus, IconX } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchIssueLabels, updateIssueLabels } from "@/lib/api/console";
import type { IssueLabelSnapshot } from "@/lib/api/types";

import { prettyLabel, variantForLabel } from "@/components/agent-runner/label-variant";

interface IssueLabelEditorProps {
  repoId: string;
  issueNumber: number;
  /** 标签写回成功后的回调，用于父级同步展示（如全量列表行的标签）。 */
  onLabelsChanged?: (labels: string[]) => void;
}

/**
 * 单个 Issue 的标签编辑面板：展示当前标签，允许在标准集合内增删。
 *
 * 集合外的既有标签只读展示；写回后以后端 fresh read 结果刷新界面，
 * 保证页面状态与 GitHub 实际状态一致。
 *
 * @param props.repoId - 仓库 ID。
 * @param props.issueNumber - Issue 编号。
 * @param props.onLabelsChanged - 写回成功后的标签变更回调。
 */
export function IssueLabelEditor({
  repoId,
  issueNumber,
  onLabelsChanged,
}: IssueLabelEditorProps) {
  const [snapshot, setSnapshot] = useState<IssueLabelSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await fetchIssueLabels(repoId, issueNumber);
      setSnapshot(result);
      setError(null);
      onLabelsChanged?.(result.labels);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "无法加载 Issue 标签。");
    }
  }, [repoId, issueNumber, onLabelsChanged]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(add: string[], remove: string[]) {
    setPending(true);
    try {
      const fresh = await updateIssueLabels(repoId, issueNumber, { add, remove });
      setSnapshot(fresh);
      onLabelsChanged?.(fresh.labels);
      toast.success(`Issue #${issueNumber} 标签已更新。`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "标签更新失败。");
    } finally {
      setPending(false);
    }
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 text-xs text-red-700 dark:text-red-300">
        标签加载失败：{error}
        <Button size="sm" variant="outline" onClick={() => void load()}>
          重试
        </Button>
      </div>
    );
  }
  if (!snapshot) {
    return <Skeleton className="h-6 w-full" />;
  }

  const allowed = new Set(snapshot.allowed_labels);
  const addable = snapshot.allowed_labels.filter(
    (label) => !snapshot.labels.includes(label),
  );

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="issue-label-editor">
      {snapshot.labels.map((label) => {
        const editable = allowed.has(label);
        return (
          <Badge key={label} variant={variantForLabel(label)} className="text-xs">
            <span>{prettyLabel(label)}</span>
            {editable ? (
              <button
                type="button"
                aria-label={`移除标签 ${label}`}
                className="ml-1 inline-flex cursor-pointer items-center opacity-70 hover:opacity-100"
                disabled={pending}
                onClick={() => void submit([], [label])}
              >
                <IconX className="size-3" />
              </button>
            ) : null}
          </Badge>
        );
      })}
      {snapshot.labels.length === 0 ? (
        <span className="text-xs text-slate-500">当前没有标签。</span>
      ) : null}
      {addable.length > 0 ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline" disabled={pending} data-testid="issue-label-add">
              <IconPlus className="size-3" />
              添加标签
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {addable.map((label) => (
              <DropdownMenuItem
                key={label}
                onSelect={() => void submit([label], [])}
              >
                {prettyLabel(label)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </div>
  );
}
