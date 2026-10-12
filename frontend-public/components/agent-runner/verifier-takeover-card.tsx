"use client";

// 单 Issue verifier 人工接管面板：仅在详情投影显示有受控独立 verifier 时出现，
// 请求只登记取消意图，交班与否以回读控制行状态为准，界面绝不自称"已停止"。

import { IconExternalLink, IconPlayerStop } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { fetchIssueDetail } from "@/lib/api/agentRunner";
import { takeOverVerifier } from "@/lib/api/console";
import type { ActiveVerifier, ActiveVerifierState } from "@/lib/api/types";

/** 交班回读轮询间隔：接管登记后每 1.5s 重读一次控制行投影。 */
const POLL_INTERVAL_MS = 1_500;
/** 超过该时长仍未确认交班，提示"仍在停止"而非静默等待。 */
const SLOW_HINT_MS = 5_000;
/** 空闲态刷新间隔：与 Dashboard 快照轮询同档，防接管按钮对着已结束的运行。 */
const IDLE_REFRESH_MS = 15_000;

const STATE_LABELS: Record<ActiveVerifierState, string> = {
  running: "独立 verifier 运行中",
  takeover_requested: "已请求接管，正在停止",
  taken_over: "已交给你审查 · verifier 未完成",
  takeover_failed: "接管未能停止进程",
  verifier_completed: "verifier 已完成",
  verifier_stale: "verifier 结果已过期（HEAD 变化）",
  pr_merged: "PR 已被外部合并",
  expired: "受控运行已过期",
};

interface VerifierTakeoverCardProps {
  repoId: string;
  issueNumber: number;
  /** 详情快照已带活动时用初值渲染，避免首帧空白；随后仍以 fresh read 校准。 */
  initialVerifier?: ActiveVerifier | null;
  /** Draft PR 链接（可缺省，缺省时不展示"打开 Draft PR"）。 */
  prUrl?: string | null;
  /** 交班确认后请求父级刷新监控快照（Issue 已翻到 review）。 */
  onTakeoverSettled?: () => void;
}

/**
 * 渲染 Issue 详情内的 verifier 接管卡片。
 *
 * 无受控独立 verifier（投影为空）时不渲染，避免误导操作员。
 *
 * @param props.repoId - 仓库 ID。
 * @param props.issueNumber - Issue 编号。
 * @param props.initialVerifier - 详情快照携带的活动 verifier 投影。
 * @param props.prUrl - Draft PR 链接。
 * @param props.onTakeoverSettled - 交班确认后的父级刷新回调。
 */
export function VerifierTakeoverCard({
  repoId,
  issueNumber,
  initialVerifier,
  prUrl,
  onTakeoverSettled,
}: VerifierTakeoverCardProps) {
  const [verifier, setVerifier] = useState<ActiveVerifier | null>(
    initialVerifier ?? null,
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [slowHint, setSlowHint] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 重读请求序号：只采用最新一次发起的响应，防止乱序回包覆盖新状态。 */
  const reloadSeqRef = useRef(0);

  /** 重读 Issue 详情，只取活动 verifier 投影。 */
  const reload = useCallback(async () => {
    const issuedSeq = ++reloadSeqRef.current;
    try {
      const detail = await fetchIssueDetail(issueNumber, { repoId });
      const projectedVerifier = detail.active_verifier ?? null;
      if (issuedSeq !== reloadSeqRef.current) {
        return null;
      }
      setVerifier(projectedVerifier);
      return projectedVerifier;
    } catch {
      // 详情读取失败不打断当前展示：保留上一帧，等下一轮轮询或手动刷新。
      return null;
    }
  }, [repoId, issueNumber]);

  useEffect(() => {
    // reload() 只在 fetch 完成后的回调里 setState，不是同步级联渲染；规则的静态
    // 分析看穿不了 `void reload()`，因此这里逐行豁免，而不是整文件关闭该规则。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  // Dashboard 的快照轮询不会重取已打开 Issue 的详情投影；空闲态按同档节奏
  // fresh-read，防止接管按钮长期对着一个已经结束的 verifier。
  useEffect(() => {
    if (requesting || verifier?.state !== "running") {
      return;
    }
    const idleTimer = setInterval(() => {
      void reload();
    }, IDLE_REFRESH_MS);
    return () => clearInterval(idleTimer);
  }, [requesting, verifier?.state, reload]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (slowTimerRef.current !== null) {
      clearTimeout(slowTimerRef.current);
      slowTimerRef.current = null;
    }
    setSlowHint(false);
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  /** 交班确认后：停止轮询、清除慢提示、通知父级刷新。 */
  const settleHandover = useCallback(() => {
    stopPolling();
    setRequesting(false);
    onTakeoverSettled?.();
  }, [stopPolling, onTakeoverSettled]);

  /** 接管登记后轮询控制行投影，直到离开 running / takeover_requested。 */
  const startPolling = useCallback(() => {
    stopPolling();
    slowTimerRef.current = setTimeout(() => setSlowHint(true), SLOW_HINT_MS);
    pollTimerRef.current = setInterval(async () => {
      const fresh = await reload();
      if (fresh && fresh.state !== "running" && fresh.state !== "takeover_requested") {
        if (fresh.state === "taken_over") {
          settleHandover();
        } else {
          stopPolling();
          setRequesting(false);
        }
      }
    }, POLL_INTERVAL_MS);
  }, [reload, settleHandover, stopPolling]);

  /** 提交接管：只发送详情读到的 run_id / head_sha，绝不自行构造。 */
  async function submitTakeover() {
    if (!verifier) return;
    setErrorText(null);
    setRequesting(true);
    const result = await takeOverVerifier(repoId, issueNumber, {
      runId: verifier.run_id,
      headSha: verifier.head_sha,
    });
    if (result.kind === "handed_over") {
      setConfirmOpen(false);
      await reload();
      settleHandover();
      return;
    }
    if (result.kind === "requested") {
      setConfirmOpen(false);
      await reload();
      startPolling();
      return;
    }
    setRequesting(false);
    // 冲突/失败也要关掉确认对话框：错误提示渲染在卡片里，模态遮罩会把它整个
    // 挡住，操作员只看到重新可用的「确认接管」按钮，看不到「状态已变化」。
    setConfirmOpen(false);
    if (result.kind === "conflict") {
      setErrorText(describeConflict(result.outcome));
    } else {
      setErrorText(`无法停止：${result.message}`);
    }
    await reload();
  }

  if (!verifier) {
    return null;
  }

  const canTakeOver =
    verifier.can_take_over && !requesting && verifier.state === "running";

  return (
    <div
      className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-800"
      data-testid="verifier-takeover-card"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 font-medium text-slate-700 dark:text-slate-200">
          <IconPlayerStop className="size-4" />
          <span>独立 verifier 人工接管</span>
          <Badge
            variant={
              verifier.state === "taken_over"
                ? "ready"
                : verifier.state === "takeover_failed"
                  ? "error"
                  : verifier.state === "running" ||
                      verifier.state === "takeover_requested"
                    ? "warning"
                    : "default"
            }
          >
            {STATE_LABELS[verifier.state]}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {verifier.state === "taken_over" && prUrl ? (
            <a
              href={prUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-800 dark:text-slate-300"
              data-testid="verifier-takeover-open-pr"
            >
              <IconExternalLink className="size-3" />
              打开 Draft PR
            </a>
          ) : null}
          {verifier.state === "running" ? (
            <Button
              size="sm"
              variant="outline"
              disabled={!canTakeOver}
              data-testid="verifier-takeover-request"
              onClick={() => setConfirmOpen(true)}
            >
              接管此 Issue
            </Button>
          ) : null}
          {requesting ? (
            <span
              className="text-xs text-slate-500"
              data-testid="verifier-takeover-status"
            >
              {slowHint ? "仍在停止，尚未交接" : "正在停止"}
            </span>
          ) : null}
        </div>
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-1 text-xs text-slate-600 dark:text-slate-300 md:grid-cols-2">
        <div>
          <dt className="inline text-slate-400">run: </dt>
          <dd className="inline font-mono break-all">{verifier.run_id || "—"}</dd>
        </div>
        <div>
          <dt className="inline text-slate-400">head: </dt>
          <dd className="inline font-mono">
            {verifier.head_sha?.slice(0, 7) || "—"}
          </dd>
        </div>
      </dl>
      {verifier.autopilot_hold && verifier.state !== "taken_over" ? (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          该 Issue 处于人工接管保持位：自动监督已冻结。
        </p>
      ) : null}
      {errorText ? (
        <p className="mt-2 text-xs text-red-700 dark:text-red-300" data-testid="verifier-takeover-error">
          {errorText}
        </p>
      ) : null}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>接管此 Issue 的 verifier？</DialogTitle>
            <DialogDescription>
              只停止此 Issue 当前 verifier。PR 会保持 Draft；取消不会被记为验证通过，也不会自动合并。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={requesting}
              onClick={() => setConfirmOpen(false)}
            >
              取消
            </Button>
            <Button
              disabled={requesting}
              data-testid="verifier-takeover-confirm"
              onClick={() => void submitTakeover()}
            >
              {requesting ? "正在停止" : "确认接管"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** 把 409 outcome 归类到操作员能读懂的三种失败原因。 */
function describeConflict(outcome: string): string {
  if (outcome === "completed_conflict") {
    return "运行已结束：verifier 已完成，无法再接管。";
  }
  if (outcome === "binding_mismatch") {
    return "HEAD 已变化：当前运行的提交与页面读取到的不一致，请刷新后重试。";
  }
  if (outcome === "not_found") {
    return "运行已结束：未找到该 Issue 的受控 verifier 运行。";
  }
  return `状态已变化（${outcome || "unknown"}）：请刷新后重试。`;
}
