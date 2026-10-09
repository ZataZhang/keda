"use client";

// Backlog 仓库级自动推进控制条：调度开关键 + 真实运行状态 + 生效并发上限。
//
// 页面分别显示 Backlog 自动推进、daemon 运行状态和自动合并是否通过双开关。
// 「并发」显示的是后端解析出的生效值与来源（继承 / 设置 / 受容量限制），
// 并提供最小设置入口（1–10 与恢复继承）；数字与 daemon 认领、补位闸门同源。

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { BacklogAutopilotState, BacklogCeilingSource } from "@/lib/api/types";

interface BacklogAutopilotControlProps {
  state: BacklogAutopilotState | null;
  loading: boolean;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
  policySaving: boolean;
  onUpdatePolicy: (maxParallel: number | null) => void;
}

/** 生效并发上限来源对应的说明文案。 */
const CEILING_SOURCE_LABELS: Record<BacklogCeilingSource, string> = {
  inherited: "继承 runner 配置",
  policy: "Backlog 设置",
  capped_by_capacity: "受 runner 容量限制",
};

/**
 * 把草稿字符串解析为合法策略值。
 *
 * @param draft - 数字输入框里的原始字符串。
 * @returns 1–10 的整数；非法时为 ``null``。
 */
function parsePolicyDraft(draft: string): number | null {
  const trimmed = draft.trim();
  if (trimmed === "") {
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 10) {
    return null;
  }
  return parsed;
}

/**
 * 渲染当前仓库的 Backlog 自动推进开关、生效并发上限与运行状态。
 *
 * @param props.state - 后端聚合的状态快照；``null`` 表示尚未加载。
 * @param props.loading - 首次加载中为 ``true``。
 * @param props.saving - 正在写回自动推进开关时为 ``true``。
 * @param props.onToggle - 开关切换回调，参数是目标布尔值。
 * @param props.policySaving - 正在保存 / 清除并发策略时为 ``true``。
 * @param props.onUpdatePolicy - 并发策略写回回调：1–10 为设置，``null`` 为恢复继承。
 * @returns Backlog 自动推进控制条。
 */
export function BacklogAutopilotControl({
  state,
  loading,
  saving,
  onToggle,
  policySaving,
  onUpdatePolicy,
}: BacklogAutopilotControlProps) {
  const [draft, setDraft] = useState("");
  if (loading || !state) {
    return (
      <div className="flex items-center gap-3 rounded-md border border-slate-200 px-3 py-2 dark:border-slate-800">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-64" />
      </div>
    );
  }

  const parsedDraft = parsePolicyDraft(draft);
  const canSave = parsedDraft !== null && !policySaving;

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
      data-testid="backlog-autopilot"
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={state.enabled}
          disabled={saving}
          onChange={(event) => onToggle(event.target.checked)}
          data-testid="backlog-autopilot-toggle"
        />
        Backlog 自动推进
      </label>

      <span className="text-slate-500">
        {state.enabled ? "已开启：daemon 会自动解锁并补位 pending PRD" : "已关闭：pending PRD 不会自动补位"}
      </span>

      <span className="text-slate-400">·</span>

      <span className="flex items-center gap-2" data-testid="backlog-concurrency">
        <span className="text-slate-500">
          并发 {state.effective_max_parallel}（{CEILING_SOURCE_LABELS[state.ceiling_source]}）
          {state.ceiling_source === "capped_by_capacity" ? `，容量 ${state.runner_capacity}` : ""}
        </span>
        <Input
          type="number"
          min={1}
          max={10}
          step={1}
          value={draft}
          placeholder={state.max_parallel === null ? "未设置" : String(state.max_parallel)}
          onChange={(event) => setDraft(event.target.value)}
          className="h-7 w-20 text-xs"
          aria-label="设置并发上限（1-10）"
          data-testid="backlog-concurrency-input"
        />
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2 text-xs"
          disabled={!canSave}
          onClick={() => {
            if (parsedDraft !== null) {
              onUpdatePolicy(parsedDraft);
              setDraft("");
            }
          }}
          data-testid="backlog-concurrency-save"
        >
          {policySaving ? "保存中…" : "保存"}
        </Button>
        {state.max_parallel !== null ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            disabled={policySaving}
            onClick={() => {
              onUpdatePolicy(null);
              setDraft("");
            }}
            data-testid="backlog-concurrency-restore"
          >
            恢复继承
          </Button>
        ) : null}
      </span>

      <span
        className={
          state.daemon_running
            ? "text-emerald-600"
            : "text-amber-600"
        }
        data-testid="backlog-autopilot-daemon"
      >
        {state.daemon_running ? "● Daemon 运行中" : "○ Daemon 未运行，自动推进暂不执行"}
      </span>

      <span
        className={state.auto_merge_enabled ? "text-emerald-600" : "text-amber-600"}
        data-testid="backlog-autopilot-auto-merge"
      >
        {state.auto_merge_enabled
          ? "自动合并已启用"
          : "自动合并未启用（需 autopilot.enabled 与 safety.auto_merge 同时开启）"}
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
