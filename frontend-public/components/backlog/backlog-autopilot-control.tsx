"use client";

// Backlog 仓库级自动推进控制条：调度开关键 + 真实运行状态 + 并行上限。
//
// 页面分别显示 Backlog 自动推进、daemon 运行状态和全局自动合并双开关。
// 「并行上限」优先显示本机 daemon 实际在用的值与来源（含只有 daemon 知道的
// --concurrency）；daemon 没在跑时显示页面设置 / 配置文件解析出的值。页面设置只存
// 本机管理终端数据库，不修改 .kedacode.toml，也不影响其他电脑。
// 「恢复为配置文件值」= 删除设置行，已知副作用是默认视图偏好回落到列表（PRD §12），
// 按钮 title 上如实披露。
// 单 PRD 自动合并授权位于 PRD 启动选项，不会改动全局合并配置。

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  BacklogAutopilotState,
  BacklogCeilingSource,
  DaemonParallelLimitSource,
} from "@/lib/api/types";

interface BacklogAutopilotControlProps {
  state: BacklogAutopilotState | null;
  loading: boolean;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
  policySaving: boolean;
  onUpdatePolicy: (maxParallel: number | null) => Promise<boolean>;
}

/** 生效并行上限来源对应的说明文案。 */
const CEILING_SOURCE_LABELS: Record<BacklogCeilingSource, string> = {
  policy: "页面设置",
  config: "配置文件",
};

/** daemon 实际生效值的来源文案（多一个只有 daemon 进程知道的命令行覆盖）。 */
const DAEMON_LIMIT_SOURCE_LABELS: Record<DaemonParallelLimitSource, string> = {
  ...CEILING_SOURCE_LABELS,
  cli_override: "daemon 命令行 --concurrency",
};

/**
 * 生成并行上限的展示文案：daemon 在跑时以它实际在用的值为准。
 *
 * @param state - 后端返回的 Autopilot 快照。
 * @returns 例如「并行上限 5（daemon 命令行 --concurrency；页面设置 4 未生效）」。
 */
function describeParallelLimit(state: BacklogAutopilotState): string {
  const daemonLimit = state.daemon_parallel_limit;
  const daemonSource = state.daemon_parallel_limit_source;
  if (daemonLimit === null || daemonSource === null) {
    return `并行上限 ${state.effective_max_parallel}（${CEILING_SOURCE_LABELS[state.ceiling_source]}）`;
  }
  const overridden =
    daemonLimit !== state.effective_max_parallel
      ? `；${CEILING_SOURCE_LABELS[state.ceiling_source]} ${state.effective_max_parallel} 未生效`
      : "";
  return `并行上限 ${daemonLimit}（${DAEMON_LIMIT_SOURCE_LABELS[daemonSource]}${overridden}）`;
}

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
 * @param props.onUpdatePolicy - 并发策略写回回调：1–10 为设置，``null`` 为恢复继承；
 *   解析为 ``true`` 表示写入成功（调用方据此清空输入草稿），失败为 ``false``。
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
        <span
          className="text-slate-500"
          title="页面设置只保存在这台电脑的管理终端里，不修改 .kedacode.toml，其他电脑不受影响"
          data-testid="backlog-concurrency-label"
        >
          {describeParallelLimit(state)}
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
          aria-label="设置并行上限（1-10）"
          data-testid="backlog-concurrency-input"
        />
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2 text-xs"
          disabled={!canSave}
          onClick={() => {
            if (parsedDraft === null) {
              return;
            }
            // 草稿在写入成功后才清空；失败时保留供用户修正重试。
            void onUpdatePolicy(parsedDraft).then((saved) => {
              if (saved) {
                setDraft("");
              }
            });
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
              // 恢复为配置文件值＝删除设置行；成功后才丢弃未保存的输入草稿。
              void onUpdatePolicy(null).then((saved) => {
                if (saved) {
                  setDraft("");
                }
              });
            }}
            title={`恢复为配置文件值（${state.runner_capacity}）会删除本仓库的 Backlog 设置，默认视图偏好将回落到列表`}
            data-testid="backlog-concurrency-restore"
          >
            恢复为配置文件值：{state.runner_capacity}
          </Button>
        ) : null}
        <span className="text-xs text-slate-400" data-testid="backlog-concurrency-scope">
          页面设置仅本机生效，不改 .kedacode.toml
        </span>
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
          ? "全局自动合并已启用"
          : "全局自动合并未启用；Autopilot 开启时可为单个 PRD 显式授权"}
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
