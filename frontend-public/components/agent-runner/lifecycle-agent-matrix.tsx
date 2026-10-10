"use client";

// 生命周期矩阵的共享呈现件：分组标题行、展示名、基线取值与下拉候选构造。
//
// 九阶段的编辑 UI 已统一到 /app/settings/lifecycle/（lifecycle-settings-page.tsx）
// 与 PRD 覆盖抽屉（prd-agent-override-sheet.tsx），两处共用本模块的分组与取值口径，
// 前端不持有第二份生命周期映射。

import type {
  LifecycleAgentEntry,
  LifecycleAgentEntryGroup,
} from "@/lib/api/types";
import type { LifecycleOption } from "@/components/agent-runner/lifecycle-agent-select";

export type { LifecycleOption } from "@/components/agent-runner/lifecycle-agent-select";

/** `auto` 取值常量（与后端 `LIFECYCLE_AGENT_AUTO` 一致）。 */
export const LIFECYCLE_AGENT_AUTO = "auto";
/** `executor` 取值常量（与后端 `LIFECYCLE_AGENT_EXECUTOR` 一致）。 */
export const LIFECYCLE_AGENT_EXECUTOR = "executor";

/** 九个生命周期键的中文展示名（顺序即后端 `LIFECYCLE_AGENT_KEYS`）。 */
export const LIFECYCLE_DISPLAY_NAMES: Record<string, string> = {
  implementation: "实现",
  fix: "修复",
  closeout: "收尾",
  verifier: "校验",
  review: "审核",
  supervisor: "监督",
  planner: "决策",
  content_generation: "内容生成",
  deliberate: "辩论",
};

/**
 * 返回生命周期键的中文展示名，未知键原样返回。
 *
 * @param key - 生命周期键。
 * @returns 中文展示名。
 */
export function lifecycleDisplayName(key: string): string {
  return LIFECYCLE_DISPLAY_NAMES[key] ?? key;
}

/** 一个触发入口分组的渲染块：组元信息（可能为 null 表示兜底未分组块）+ 组内行。 */
export type LifecycleEntryGroupBlock = {
  group: LifecycleAgentEntryGroup | null;
  rows: LifecycleAgentEntry[];
};

/**
 * 把矩阵行按后端下发的触发入口分组切成渲染块。
 *
 * 分组事实的唯一来源是后端 `entry_groups`（前端不持有第二份 key→group 映射）；
 * 组内行保持 `lifecycles` 数组的相对顺序（即 `LIFECYCLE_AGENT_KEYS` 键序）。
 * 当后端未下发分组（或下发的组没有匹配到任何行）时，退化为单个未分组块，
 * 保证行始终渲染、不会因分组缺失而丢行。
 *
 * @param lifecycles - 矩阵行（按 `LIFECYCLE_AGENT_KEYS` 键序）。
 * @param entryGroups - 后端下发的触发入口分组（按展示顺序）。
 * @returns 按展示顺序排列的分组渲染块。
 */
export function groupLifecycleEntriesByEntry(
  lifecycles: LifecycleAgentEntry[],
  entryGroups: LifecycleAgentEntryGroup[] | undefined,
): LifecycleEntryGroupBlock[] {
  if (!entryGroups || entryGroups.length === 0) {
    return lifecycles.length > 0 ? [{ group: null, rows: lifecycles }] : [];
  }
  const blocks: LifecycleEntryGroupBlock[] = [];
  const matchedKeys = new Set<string>();
  for (const group of entryGroups) {
    const rows = lifecycles.filter((entry) => entry.entry === group.entry);
    if (rows.length > 0) {
      blocks.push({ group, rows });
      for (const row of rows) {
        matchedKeys.add(row.key);
      }
    }
  }
  const unmatched = lifecycles.filter((entry) => !matchedKeys.has(entry.key));
  if (unmatched.length > 0) {
    blocks.push({ group: null, rows: unmatched });
  }
  return blocks;
}

/**
 * 触发入口分组的标题行（组中文名 + 一行组说明），三处矩阵共用。
 *
 * @param props - 组元信息。
 * @returns 组标题行；`group` 为 null（兜底未分组块）时不渲染。
 */
export function LifecycleEntryGroupHeader({
  group,
}: {
  group: LifecycleAgentEntryGroup;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 border-b border-slate-200 pb-1 pt-1 dark:border-slate-700">
      <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">
        {group.label}
      </span>
      <span className="text-[11px] text-slate-400 dark:text-slate-500">
        {group.summary}
      </span>
    </div>
  );
}

/**
 * 计算某行的「当前选择」：本层已声明则用声明值，否则用生效值。
 *
 * @param entry - 矩阵行视图。
 * @returns 用于初始化下拉框的取值。
 */
export function lifecycleBaselineValue(entry: LifecycleAgentEntry): string {
  if (entry.declared_in_scope && entry.declared_value) {
    return entry.declared_value;
  }
  if (entry.follows_executor) {
    return LIFECYCLE_AGENT_EXECUTOR;
  }
  return entry.effective_agent ?? "";
}

/**
 * 构建某行下拉框的候选值：只包含真实可取的值（已注册 agent + 允许的 auto/executor）。
 *
 * @param entry - 矩阵行视图。
 * @param agents - 已注册 agent 列表。
 * @returns 候选值列表。
 */
export function buildLifecycleOptions(
  entry: LifecycleAgentEntry,
  agents: string[],
): LifecycleOption[] {
  const options: LifecycleOption[] = agents.map((agentName) => ({
    value: agentName,
    label: agentName,
  }));
  if (entry.auto_allowed) {
    options.push({
      value: LIFECYCLE_AGENT_AUTO,
      label: "自动（auto）",
      description: entry.auto_description ?? undefined,
    });
  }
  if (entry.executor_allowed) {
    options.push({
      value: LIFECYCLE_AGENT_EXECUTOR,
      label: "跟随实现（executor）",
      description: "跟随实现阶段选中的 agent",
    });
  }
  // 兜底：当前取值不在候选里（理论上不应发生）时补进来，避免下拉框显示为空。
  const current = lifecycleBaselineValue(entry);
  if (current && !options.some((option) => option.value === current)) {
    options.unshift({ value: current, label: current });
  }
  return options;
}
