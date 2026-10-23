"use client";

/**
 * Dashboard 表头的合并刷新控件：左侧 3 份是刷新（带自动刷新倒计时），
 * 右侧 1 份是齿轮（展开/收起同步设置）。
 *
 * 两块共用一个边框中缝，按 3:1 分列；倒计时只在本组件内每秒重绘，
 * 不会让整页（任务列表）跟着每秒重渲染。
 */

import { useEffect, useState } from "react";
import { RefreshCw, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * 把剩余秒数格式化成 m:ss。
 *
 * @param totalSeconds - 剩余秒数（非负）。
 * @returns 形如 `4:59` 的倒计时文案。
 */
function formatCountdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * 合并的「刷新 + 同步设置」控件。
 *
 * @param props - 刷新状态与两个动作回调。
 * @param props.nextRefreshAt - 下一次自动同步的毫秒时间戳；null 表示不显示倒计时
 *   （自动同步关闭、设置未加载或还没有可用快照）。
 * @param props.refreshStartedAt - 手动全量刷新的开始时间戳；null 表示当前没在刷新。
 * @param props.settingsOpen - 同步设置面板是否已展开。
 * @param props.onRefresh - 点击左侧立即刷新。
 * @param props.onToggleSettings - 点击右侧齿轮展开/收起同步设置。
 * @returns 表头右侧的合并控件。
 */
export function DashboardSyncControl(props: {
  nextRefreshAt: number | null;
  refreshStartedAt: number | null;
  settingsOpen: boolean;
  onRefresh: () => void;
  onToggleSettings: () => void;
}) {
  const { nextRefreshAt, refreshStartedAt, settingsOpen, onRefresh, onToggleSettings } =
    props;
  const [nowMillis, setNowMillis] = useState(() => Date.now());
  const refreshing = refreshStartedAt !== null;

  // 每秒推进一次时钟即可：倒计时与刷新计时都从时间戳算，不累加状态。
  useEffect(() => {
    const timer = setInterval(() => setNowMillis(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const remainingSeconds =
    nextRefreshAt === null
      ? null
      : Math.max(0, Math.ceil((nextRefreshAt - nowMillis) / 1000));
  const elapsedSeconds =
    refreshStartedAt === null
      ? 0
      : Math.max(0, Math.floor((nowMillis - refreshStartedAt) / 1000));

  const refreshLabel = refreshing
    ? `刷新中… ${elapsedSeconds}s`
    : remainingSeconds === null
      ? "刷新全部"
      : remainingSeconds === 0
        ? "刷新全部 · 即将刷新"
        : `刷新全部 · ${formatCountdown(remainingSeconds)}`;

  return (
    <div className="grid w-[196px] grid-cols-[3fr_1fr] items-stretch">
      <Button
        size="sm"
        variant="outline"
        className="gap-1 rounded-r-none border-r-0"
        disabled={refreshing}
        onClick={onRefresh}
        title={
          remainingSeconds === null
            ? "立即全量刷新"
            : `立即全量刷新（下一次自动同步还剩 ${formatCountdown(remainingSeconds)}）`
        }
        data-testid="dashboard-refresh-all"
      >
        <RefreshCw className={cn("size-3.5 shrink-0", refreshing && "animate-spin")} />
        <span className="truncate text-xs tabular-nums">{refreshLabel}</span>
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="rounded-l-none px-0"
        onClick={onToggleSettings}
        aria-label="同步设置"
        aria-expanded={settingsOpen}
        title="同步设置"
        data-testid="dashboard-settings-toggle"
      >
        <Settings className="size-3.5" />
      </Button>
    </div>
  );
}
