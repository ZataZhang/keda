"use client";

// PRD 详情「实时输出」标签：按仓库 + Issue 号轮询该 Issue 的 Agent 可见输出。
//
// 复用进程日志抽屉的 offset 轮询模式（2.5s），但数据源换成 Issue 日志 API
// （`/console/repositories/<repo_id>/issues/<N>/logs`）。支持暂停/继续、
// 新尝试切换提示、截断/清理/无日志的明确空态，且关闭标签即停止轮询。

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetchIssueLog } from "@/lib/api/console";
import type { IssueLogChunk } from "@/lib/api/types";

const LOG_POLL_INTERVAL_MS = 2500;
// 防止超长日志拖垮页面，只保留尾部 ~200KB。
const MAX_LOG_CHARS = 200_000;

interface PrdIssueOutputViewProps {
  repoId: string;
  issueNumber: number;
}

/**
 * 渲染单个 Issue 的实时输出面板。
 *
 * @param props.repoId - 已注册仓库标识。
 * @param props.issueNumber - 要跟随的 Issue 编号。
 * @returns Issue 实时输出面板。
 */
export function PrdIssueOutputView({ repoId, issueNumber }: PrdIssueOutputViewProps) {
  const [logContent, setLogContent] = useState("");
  const [paused, setPaused] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const offsetRef = useRef(0);
  const attemptIdRef = useRef<string | null>(null);
  const containerRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    // 切换 PRD / Issue 时重置状态，避免把上一个 Issue 的偏移拼到当前内容。
    // ref 重置可以同步做；state 重置必须异步（React 禁止在 effect 体内同步
    // setState，会把渲染级联放大）。
    offsetRef.current = 0;
    attemptIdRef.current = null;
    let cancelled = false;

    /**
     * 拉取一次 Issue 日志块并按状态机更新视图。
     *
     * 首次请求（attempt 未知）走尾部窗口；之后按 ``offsetRef`` 增量续读。
     * 暂停、卸载或切换 Issue 时直接短路。
     */
    async function pollLog() {
      if (cancelled || paused) {
        return;
      }
      try {
        const chunk: IssueLogChunk = await fetchIssueLog({
          repoId,
          issueNumber,
          offset: offsetRef.current,
          attemptId: attemptIdRef.current,
          // 首次（attempt 未知）取尾部窗口，之后按字节偏移增量续读。
          tail: attemptIdRef.current === null,
        });
        if (cancelled) {
          return;
        }
        // 首次成功响应后才清旧 Issue 的显示状态：失败时保留上一屏内容，
        // 避免网络抖动把已读输出闪成空白。
        if (attemptIdRef.current === null) {
          setLogContent("");
          setNotice(null);
          setAttemptId(null);
        }

        if (chunk.status === "no_attempt") {
          setNotice("该 Issue 暂无可用输出（尚未开始或日志已清理）。");
          return;
        }
        if (chunk.status === "repo_not_found") {
          setNotice("目标仓库未注册或已禁用，无法读取 Issue 输出。");
          return;
        }
        if (chunk.status === "attempt_gone") {
          setNotice(
            chunk.latest_attempt_id && chunk.latest_attempt_id !== attemptIdRef.current
              ? `当前尝试已不可用，已切换到最新尝试 ${chunk.latest_attempt_id}。`
              : "当前尝试已不可用（日志已清理）。",
          );
          // 尝试被清理：重置到最新尝试，避免把旧偏移拼到新文件。
          attemptIdRef.current = chunk.latest_attempt_id;
          offsetRef.current = 0;
          if (chunk.latest_attempt_id) {
            setLogContent("");
          }
          return;
        }
        if (chunk.status === "truncated") {
          setNotice("日志已轮转或截断，已从最新位置续读。");
          offsetRef.current = chunk.next_offset;
          return;
        }

        // 检测是否出现了新尝试（重试/新执行）。
        if (
          chunk.latest_attempt_id &&
          chunk.attempt_id &&
          chunk.latest_attempt_id !== chunk.attempt_id
        ) {
          setNotice(`检测到新尝试：${chunk.attempt_id} → ${chunk.latest_attempt_id}。`);
          attemptIdRef.current = chunk.latest_attempt_id;
          offsetRef.current = 0;
          setLogContent("");
          return;
        }

        if (chunk.attempt_id && chunk.attempt_id !== attemptIdRef.current) {
          setAttemptId(chunk.attempt_id);
          attemptIdRef.current = chunk.attempt_id;
        }
        if (chunk.content) {
          setNotice(null);
          offsetRef.current = chunk.next_offset;
          setLogContent((current) => {
            const merged = current + chunk.content;
            return merged.length > MAX_LOG_CHARS ? merged.slice(-MAX_LOG_CHARS) : merged;
          });
        } else {
          offsetRef.current = chunk.next_offset;
        }
      } catch (error) {
        if (!cancelled) {
          setNotice(
            error instanceof Error ? `读取 Issue 输出失败：${error.message}` : "读取 Issue 输出失败。",
          );
        }
      }
    }

    void pollLog();
    const timer = setInterval(() => void pollLog(), LOG_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [repoId, issueNumber, paused]);

  useEffect(() => {
    const container = containerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [logContent]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="prd-issue-output">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <div className="flex flex-wrap items-center gap-2">
          <span data-testid="prd-issue-output-attempt">
            {attemptId ? `本次尝试：${attemptId}` : "等待第一次输出…"}
          </span>
          {notice ? (
            <span className="text-amber-600 dark:text-amber-400" data-testid="prd-issue-output-notice">
              {notice}
            </span>
          ) : null}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setPaused((current) => !current)}
          data-testid="prd-issue-output-toggle"
        >
          {paused ? "继续" : "暂停"}
        </Button>
      </div>
      <pre
        ref={containerRef}
        className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-all rounded-md border border-slate-200 bg-slate-50 p-3 text-xs dark:border-slate-800 dark:bg-slate-950"
        data-testid="prd-issue-output-content"
      >
        {logContent || "（暂无输出）"}
      </pre>
    </div>
  );
}
