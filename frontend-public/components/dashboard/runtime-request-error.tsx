"use client";

import { Button } from "@/components/ui/button";
import { formatLocalDateTime } from "@/lib/utils";

export type RuntimeBoardLoadError =
  | { kind: "connection"; detail: string }
  | { kind: "http"; status: number; detail: string }
  | { kind: "unknown"; detail: string };

interface RuntimeRequestErrorNoticeProps {
  error: RuntimeBoardLoadError;
  generatedAt: string | null;
  retryPending: boolean;
  onRetry: () => void | Promise<void>;
}

/**
 * 显示摘要请求失败原因、保留数据的新鲜度和重试入口。
 *
 * @param props.error - 已分类的连接、HTTP 或其他请求错误。
 * @param props.generatedAt - 上次成功读取摘要的生成时间；没有缓存时为 `null`。
 * @param props.retryPending - 用户主动重试是否仍在进行。
 * @param props.onRetry - 重新读取任务摘要的动作。
 * @returns 可访问的错误说明、技术详情与重试控件。
 */
export function RuntimeRequestErrorNotice({
  error,
  generatedAt,
  retryPending,
  onRetry,
}: RuntimeRequestErrorNoticeProps) {
  return (
    <div
      className="flex flex-col gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-200 sm:flex-row sm:items-start sm:justify-between"
      role="alert"
      data-testid="runtime-board-error"
    >
      <div className="min-w-0 space-y-1">
        <p className="font-medium">
          {error.kind === "connection"
            ? "没有收到本机 Console 的响应"
            : error.kind === "http"
              ? `运行看板请求失败（HTTP ${error.status}）`
              : "无法读取运行看板数据"}
        </p>
        <p className="text-xs">
          {error.kind === "connection"
            ? "请确认 KedaCode Console 仍在运行，然后重试。"
            : error.kind === "http"
              ? "Console 已返回错误状态；展开技术详情查看服务说明。"
              : "请重试；如果问题持续，请展开技术详情。"}
        </p>
        <p className="text-xs">
          {generatedAt
            ? `下方保留上次成功读取的数据（${formatLocalDateTime(generatedAt)}），可能已经过期。`
            : "尚无成功读取的数据，当前无法显示任务列表。"}
        </p>
        <details className="text-xs">
          <summary className="cursor-pointer select-none">技术详情</summary>
          <p className="mt-1 break-all font-mono">
            {error.kind === "http" ? `HTTP ${error.status} · ` : ""}
            {error.detail || "没有附加错误信息。"}
          </p>
        </details>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="shrink-0"
        onClick={() => void onRetry()}
        disabled={retryPending}
        data-testid="runtime-board-retry"
      >
        {retryPending ? "重试中…" : "重试"}
      </Button>
    </div>
  );
}
