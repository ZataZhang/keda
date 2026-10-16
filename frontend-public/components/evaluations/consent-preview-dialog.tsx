"use client";

// 评审授权对话框：预览即实际发送内容（逐字节一致），approve/deny 必须携带
// 预览返回的 manifest hash；hash 缺失或过期时后端 409，绝不发起任何评审调用。

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchEvaluationJudgePreview,
  submitEvaluationConsent,
} from "@/lib/api/console";
import type { EvaluationJudgePreview } from "@/lib/api/types";

type ConsentPreviewDialogProps = {
  runId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 授权动作完成后回调（页面据此刷新 run 状态）。 */
  onDecided: () => void;
};

/** 展示待外发评审 payload 全貌并收集显式授权决定。 */
export function ConsentPreviewDialog({
  runId,
  open,
  onOpenChange,
  onDecided,
}: ConsentPreviewDialogProps) {
  const [preview, setPreview] = useState<EvaluationJudgePreview | null>(null);
  const [loadError, setLoadError] = useState<{
    runId: string;
    message: string;
  } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(() => {
    if (!runId) {
      return;
    }
    const targetRunId = runId;
    // 状态只在异步回调里写，effect 体内保持零同步 setState。
    fetchEvaluationJudgePreview(targetRunId)
      .then((next) => {
        if (next.run_id === targetRunId) {
          setPreview(next);
        }
      })
      .catch((error: unknown) => {
        setLoadError({
          runId: targetRunId,
          message:
            error instanceof Error ? error.message : "无法加载评审 payload 预览。",
        });
      });
  }, [runId]);

  useEffect(() => {
    if (open) {
      load();
    }
  }, [open, load]);

  // 只展示与当前 run 对应的预览/错误，切换 run 时自动回到骨架屏。
  const currentPreview = preview?.run_id === runId ? preview : null;
  const currentError = loadError?.runId === runId ? loadError.message : null;

  const decide = async (decision: "approve" | "deny") => {
    if (!runId || !currentPreview?.manifest_hash) {
      return;
    }
    setSubmitting(true);
    try {
      await submitEvaluationConsent(runId, {
        decision,
        manifest_hash: currentPreview.manifest_hash,
      });
      toast.success(
        decision === "approve" ? "已授权，评审调用已进入队列。" : "已拒绝外发，暂存内容已清除。"
      );
      onOpenChange(false);
      onDecided();
    } catch (error: unknown) {
      toast.error(
        error instanceof Error
          ? `授权提交失败：${error.message}`
          : "授权提交失败。"
      );
      // hash 过期等冲突后重新预览，让用户看到最新状态而不是拿着旧 hash 重试。
      load();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>评审外发授权</DialogTitle>
          <DialogDescription>
            以下 payload 将【原样】作为 prompt 交给所选评审预设对应的 agent CLI
            （空目录、禁用工具运行，CLI 会联系其自己的模型服务）；未授权前不会发起
            任何评审调用。发送后无法由本机撤回。
          </DialogDescription>
        </DialogHeader>
        {currentError ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
            {currentError}
          </p>
        ) : currentPreview === null ? (
          <Skeleton className="h-48" />
        ) : (
          <div className="space-y-3">
            <div className="grid gap-2 rounded-xl border bg-muted/20 p-3 text-xs sm:grid-cols-2">
              <p className="break-all">
                <span className="text-muted-foreground">manifest hash：</span>
                <code>{currentPreview.manifest_hash ?? "—"}</code>
              </p>
              <p className="break-all">
                <span className="text-muted-foreground">冻结 hash：</span>
                <code>{currentPreview.frozen_manifest_hash ?? "—"}</code>
              </p>
              <p>
                <span className="text-muted-foreground">评审目标：</span>
                {currentPreview.judge_targets.map((target) => (
                  <Badge key={target.preset_name} variant="review" className="mr-1">
                    {target.preset_name}
                    {target.agent ? ` · ${target.agent}` : ""}
                    {target.model ? ` · ${target.model}` : ""}
                  </Badge>
                ))}
              </p>
              <p>
                <span className="text-muted-foreground">预计请求数：</span>
                {currentPreview.estimated_requests}
                （payload {currentPreview.entries.length} 条 × 评审{" "}
                {currentPreview.judge_targets.length} 个）
              </p>
            </div>
            {currentPreview.entries.map((entry) => (
              <details
                key={entry.result_id}
                className="rounded-xl border bg-card"
              >
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
                  {entry.case_id} / {entry.execution_preset_id}
                  <span className="ml-2 text-xs text-muted-foreground">
                    {entry.size_bytes} B · sha256 {entry.payload_sha256.slice(0, 12)}…
                    {!entry.payload_available ? " · 暂存缺失" : ""}
                  </span>
                </summary>
                <pre className="max-h-72 overflow-auto border-t bg-muted/20 px-3 py-2 text-xs whitespace-pre-wrap">
                  {entry.payload_preview || "（payload 不可用）"}
                  {entry.payload_truncated
                    ? "\n…[预览截断；实际发送为完整暂存文件]"
                    : ""}
                </pre>
              </details>
            ))}
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
              {currentPreview.sensitive_notice}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                disabled={submitting || !currentPreview.manifest_hash}
                onClick={() => void decide("deny")}
              >
                拒绝外发
              </Button>
              <Button
                disabled={submitting || !currentPreview.manifest_hash}
                onClick={() => void decide("approve")}
              >
                {submitting ? "提交中…" : "确认外发并评审"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
