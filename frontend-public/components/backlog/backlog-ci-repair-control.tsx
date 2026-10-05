"use client";

// Backlog 仓库级「全局自动修复 CI/CD」控制条：与 Autopilot 并列、语义独立。
//
// 本开关只控制 Supervisor Agent 选出的自动 repair 动作是否放行；它不跟随
// Autopilot 打开，也不把 checks 状态映射成动作。Autopilot 的合并队列等
// checks 全绿是合并门禁，与本开关互不联动。

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { BacklogCiRepairGlobalState } from "@/lib/api/types";

interface BacklogCiRepairControlProps {
  state: BacklogCiRepairGlobalState | null;
  loading: boolean;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
}

/**
 * 渲染当前仓库的全局 CI/CD 自动修复开关。
 *
 * @param props.state - 后端 fresh load 的全局状态；``null`` 表示尚未加载。
 * @param props.loading - 首次加载中为 ``true``。
 * @param props.saving - 正在写回配置时为 ``true``。
 * @param props.onToggle - 开关切换回调，参数是目标布尔值。
 * @returns 全局自动修复控制条。
 */
export function BacklogCiRepairControl({
  state,
  loading,
  saving,
  onToggle,
}: BacklogCiRepairControlProps) {
  if (loading || !state) {
    return (
      <div className="flex items-center gap-3 rounded-md border border-slate-200 px-3 py-2 dark:border-slate-800">
        <Skeleton className="h-4 w-44" />
        <Skeleton className="h-4 w-56" />
      </div>
    );
  }

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
      data-testid="backlog-ci-repair"
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={state.global_enabled}
          disabled={saving}
          onChange={(event) => onToggle(event.target.checked)}
          data-testid="backlog-ci-repair-toggle"
        />
        全局自动修复 CI/CD
      </label>
      <Badge variant={state.global_enabled ? "default" : "outline"}>
        {state.global_enabled ? "已开启" : "已关闭"}
      </Badge>
      <span className="text-slate-500">
        {state.global_enabled
          ? `检查失败后按策略自动修复并复检（上限 ${state.max_rounds} 轮）`
          : "检查失败只作为问题展示，不启动修复 Agent"}
      </span>
      <span className="text-slate-400">·</span>
      <span className="text-slate-500">当前仓库所有 PRD · 单个 PRD 可在详情中覆盖</span>
    </div>
  );
}
