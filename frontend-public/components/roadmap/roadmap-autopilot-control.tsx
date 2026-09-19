"use client";

// Roadmap 仓库级 Autopilot 控制条：开关键 + 真实闭环状态。
//
// 页面必须把三件事分开显示：Autopilot 是否开启、daemon 是否在运行、自动合并
// 是否启用。任一条件缺失时给出明确降级文案，绝不能把「开关已开」显示成
// 「正在全自动推进」。

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { RoadmapAutopilotState } from "@/lib/api/types";

interface RoadmapAutopilotControlProps {
  state: RoadmapAutopilotState | null;
  loading: boolean;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
}

/**
 * 渲染当前仓库的 Autopilot 开关与闭环状态。
 *
 * @param props.state - 后端聚合的状态快照；``null`` 表示尚未加载。
 * @param props.loading - 首次加载中为 ``true``。
 * @param props.saving - 正在写回配置时为 ``true``。
 * @param props.onToggle - 开关切换回调，参数是目标布尔值。
 * @returns Autopilot 控制条。
 */
export function RoadmapAutopilotControl({
  state,
  loading,
  saving,
  onToggle,
}: RoadmapAutopilotControlProps) {
  if (loading || !state) {
    return (
      <div className="flex items-center gap-3 rounded-md border border-slate-200 px-3 py-2 dark:border-slate-800">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-64" />
      </div>
    );
  }

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
      data-testid="roadmap-autopilot"
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={state.enabled}
          disabled={saving}
          onChange={(event) => onToggle(event.target.checked)}
          data-testid="roadmap-autopilot-toggle"
        />
        Autopilot 自动推进
      </label>

      <span className="text-slate-500">
        {state.enabled ? "已开启：上游完成后自动解锁并启动下游 PRD" : "已关闭：不再自动补位"}
      </span>

      <span className="text-slate-400">·</span>
      <span className="text-slate-500">并发 {state.max_parallel}</span>

      <span
        className={
          state.daemon_running
            ? "text-emerald-600"
            : "text-amber-600"
        }
        data-testid="roadmap-autopilot-daemon"
      >
        {state.daemon_running ? "● Daemon 运行中" : "○ Daemon 未运行，自动推进暂不执行"}
      </span>

      <span
        className={state.auto_merge_enabled ? "text-emerald-600" : "text-amber-600"}
        data-testid="roadmap-autopilot-auto-merge"
      >
        {state.auto_merge_enabled
          ? "自动合并已启用"
          : "自动合并未启用，流程会停在待审阅"}
      </span>

      {state.persisted_enabled === null ? (
        <span className="text-slate-400">
          尚未写入 {state.config_source}（当前生效值来自全局配置）
        </span>
      ) : null}

      {saving ? (
        <Button size="sm" variant="outline" disabled>
          保存中…
        </Button>
      ) : null}
    </div>
  );
}
