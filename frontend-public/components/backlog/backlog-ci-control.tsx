"use client";

// Backlog 仓库级 CI 自动修复控制条：开关键 + 生效值/持久值来源。
//
// 这个开关与 Autopilot、自动合并、本地 Fix Agent 是四个语义独立的开关：本组件只
// 展示并写回 `post_pr_supervisor.auto_repair_ci`，不显示也不推断其它三个的值。
// 生效值来自服务端 fresh load，前端不自行合并三态策略。

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { BacklogCiAutoRepairState } from "@/lib/api/types";

interface BacklogCiControlProps {
  state: BacklogCiAutoRepairState | null;
  loading: boolean;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
}

/**
 * 渲染当前仓库的 CI 自动修复开关与修复上限。
 *
 * @param props.state - 后端聚合的设置快照；``null`` 表示尚未加载。
 * @param props.loading - 首次加载中为 ``true``。
 * @param props.saving - 正在写回配置时为 ``true``。
 * @param props.onToggle - 开关切换回调，参数是目标布尔值。
 * @returns CI 自动修复控制条。
 */
export function BacklogCiControl({
  state,
  loading,
  saving,
  onToggle,
}: BacklogCiControlProps) {
  if (loading || !state) {
    return (
      <div className="flex items-center gap-3 rounded-md border border-slate-200 px-3 py-2 dark:border-slate-800">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-56" />
      </div>
    );
  }

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
      data-testid="backlog-ci-control"
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={state.auto_repair_ci}
          disabled={saving}
          onChange={(event) => onToggle(event.target.checked)}
          data-testid="backlog-ci-auto-repair-toggle"
        />
        全局自动修复 CI/CD
      </label>

      <span className="text-slate-500" data-testid="backlog-ci-auto-repair-desc">
        {state.auto_repair_ci
          ? "已开启：Supervisor 选择 repair_pr_branch 且未超上限时自动执行"
          : "已关闭：CI 问题只做展示与人工处理，不自动修复"}
      </span>

      <span className="text-slate-400">·</span>
      <span className="text-slate-500" data-testid="backlog-ci-max-attempts">
        修复上限 {state.max_repair_attempts}（沿用 post_pr_supervisor.max_repair_attempts）
      </span>

      {state.persisted_auto_repair === null ? (
        <span className="text-slate-400" data-testid="backlog-ci-unpersisted">
          尚未写入 {state.config_source}（当前生效值来自全局配置）
        </span>
      ) : (
        <span className="text-slate-400">来源 {state.config_source}</span>
      )}

      {saving ? (
        <Button size="sm" variant="outline" disabled>
          保存中…
        </Button>
      ) : null}
    </div>
  );
}
