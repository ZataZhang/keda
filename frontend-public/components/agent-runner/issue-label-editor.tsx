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
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchIssueLabels, updateIssueLabels } from "@/lib/api/console";
import type { IssueLabelSnapshot } from "@/lib/api/types";

import { prettyLabel, variantForLabel } from "@/components/agent-runner/label-variant";

/** 标签面板向父级汇报的标签状态：打开加载与写入提交后的 fresh read 都会触发。 */
export interface IssueLabelsChangedInfo {
  repoId: string;
  issueNumber: number;
  labels: string[];
  /** ``load`` = 打开加载；``write`` = 用户增删标签后的写回。 */
  source: "load" | "write";
}

interface IssueLabelEditorProps {
  repoId: string;
  issueNumber: number;
  /** 标签读取/写回后的回调，用于父级同步展示（如全量列表行与详情头的标签）。 */
  onLabelsChanged?: (info: IssueLabelsChangedInfo) => void;
}

/**
 * 单个 Issue 的标签编辑面板：展示当前标签，允许在标准集合内增删。
 *
 * 集合外的既有标签只读展示；写回后以后端 fresh read 结果刷新界面，
 * 保证页面状态与 GitHub 实际状态一致。
 *
 * @param props.repoId - 仓库 ID。
 * @param props.issueNumber - Issue 编号。
 * @param props.onLabelsChanged - 标签读取/写回后的状态汇报（load/write）。
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
      onLabelsChanged?.({
        repoId,
        issueNumber,
        labels: result.labels,
        source: "load",
      });
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
      onLabelsChanged?.({
        repoId,
        issueNumber,
        labels: fresh.labels,
        source: "write",
      });
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
            <DropdownMenuLabel className="max-w-64 text-xs font-normal text-muted-foreground">
              候选项与 `kc labels sync` 同源；其中在途状态、直发档位与 validation
              签收标签会被 daemon、依赖判定和验证门禁立即消费，点击即写入 GitHub。
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
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
