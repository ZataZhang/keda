"use client";

// 一句话建 Issue 对话框（FR-8）：等价 CLI `kc issue create --from-prompt`。
//
// 建完即停在未入队态，不自动打就绪标签——入队时机留给用户在标签面板显式决定。

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { createIssueFromPrompt } from "@/lib/api/console";

interface CreateIssueDialogProps {
  repoId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 建 Issue 成功后的回调，用于刷新相关列表。 */
  onCreated?: (number: number, url: string) => void;
}

const ISSUE_TYPES = ["feature", "bug"] as const;

/**
 * 一句话建 Issue 对话框。
 *
 * @param props.repoId - 目标仓库。
 * @param props.open - 是否展开。
 * @param props.onOpenChange - 开合回调。
 * @param props.onCreated - 创建成功回调。
 */
export function CreateIssueDialog({
  repoId,
  open,
  onOpenChange,
  onCreated,
}: CreateIssueDialogProps) {
  const [prompt, setPrompt] = useState("");
  const [issueType, setIssueType] = useState<(typeof ISSUE_TYPES)[number]>("feature");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    const text = prompt.trim();
    if (!text) {
      toast.warning("请输入一句话需求。");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createIssueFromPrompt(repoId, {
        prompt_text: text,
        issue_type: issueType,
      });
      toast.success(`已创建 Issue #${created.number}（未入队）。`);
      onCreated?.(created.number, created.issue_url);
      setPrompt("");
      setIssueType("feature");
      onOpenChange(false);
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "创建 Issue 失败。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>一句话建 Issue</DialogTitle>
          <DialogDescription>
            用一句需求描述创建 GitHub Issue；建完停在未入队态，可稍后在标签面板打
            <code className="mx-1 font-mono text-xs">agent/ready</code>
            入队。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="issue-prompt">需求描述</Label>
            <Textarea
              id="issue-prompt"
              rows={4}
              value={prompt}
              placeholder="例如：给监控面板加一个导出 CSV 的按钮"
              onChange={(event) => setPrompt(event.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="issue-type">类型</Label>
            <select
              id="issue-type"
              className="h-8 rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-800 dark:bg-slate-950"
              value={issueType}
              onChange={(event) =>
                setIssueType(event.target.value as (typeof ISSUE_TYPES)[number])
              }
            >
              {ISSUE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            取消
          </Button>
          <Button
            data-testid="create-issue-submit"
            onClick={() => void submit()}
            disabled={submitting}
          >
            {submitting ? "创建中…" : "创建 Issue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
