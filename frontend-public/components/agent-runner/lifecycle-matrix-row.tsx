"use client";

// 生命周期矩阵的单行：一行只负责如实呈递后端下发的生效值与逐字段来源，
// 不做任何解析或推断（缺省、继承、不支持都由 core 层判定后随视图下发）。

import { Badge } from "@/components/ui/badge";
import {
  PresetSelect,
  type PresetOption,
} from "@/components/agent-runner/lifecycle-preset-select";
import {
  describeFieldSource,
  displayOptionalField,
} from "@/components/agent-runner/lifecycle-settings-source-labels";
import { Label } from "@/components/ui/label";
import type { LifecycleSettingsRow } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export interface MatrixRowProps {
  row: LifecycleSettingsRow;
  /** 可供绑定的预设（含本次新建）。 */
  presetOptions: PresetOption[];
  /** 当前绑定的预设名（空串=未绑定）。 */
  bindingValue: string;
  /** 本行绑定是否被改动（用于高亮）。 */
  bindingChanged: boolean;
  /** 后端下发的逐字段来源词表。 */
  vocabulary: Record<string, string>;
  /** 绑定变更回调。 */
  onBindingChange: (presetName: string) => void;
}

/**
 * 单个生命周期阶段的矩阵行（展示生效值 + 绑定预设）。
 *
 * @param props - 行数据、可选预设、当前绑定与改动高亮、来源词表、绑定回调。
 * @returns 一行九阶段矩阵（窄屏纵向堆叠，字段无需横向滚动即可读）。
 */
export function MatrixRow({
  row,
  presetOptions,
  bindingValue,
  bindingChanged,
  vocabulary,
  onBindingChange,
}: MatrixRowProps) {
  const vocabularySafe = vocabulary;
  const affected = row.affected_stages.filter((stage) => stage !== row.key);
  const bindingPlaceholder = row.follows_implementation
    ? "未绑定（生效值继承实现阶段）"
    : "未绑定（跟随既有配置）";
  const agentDisplay =
    row.effective_agent ?? (row.follows_implementation ? "跟随实现阶段" : "—");
  return (
    <div
      data-testid={`matrix-row-${row.key}`}
      className={cn(
        "grid grid-cols-1 gap-3 rounded-lg border p-3 lg:grid-cols-[minmax(7rem,1fr)_minmax(6rem,0.8fr)_repeat(2,minmax(6rem,1fr))_minmax(9rem,1.2fr)] lg:items-center",
        bindingChanged && "border-amber-400 bg-amber-50 dark:bg-amber-950/30",
      )}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium">{row.label}</p>
        {row.preset_name ? (
          <Badge variant="ready" className="mt-1 text-[10px]">
            已绑定 {row.preset_name}
          </Badge>
        ) : null}
        {row.preset_name ? (
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            绑定来源：{describeFieldSource(row.field_sources.preset, vocabularySafe)}
          </span>
        ) : null}
        {row.is_inherited ? (
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            继承实现阶段
          </span>
        ) : null}
      </div>

      <div className="min-w-0">
        <p className="text-[11px] uppercase text-slate-400">生效 Agent</p>
        <p
          className="truncate text-sm"
          data-testid={`matrix-agent-${row.key}`}
          title={agentDisplay}
        >
          {agentDisplay}
        </p>
        <p className="text-[11px] text-slate-500 dark:text-slate-400">
          {describeFieldSource(row.field_sources.agent, vocabularySafe)}
        </p>
      </div>

      <div className="min-w-0">
        <p className="text-[11px] uppercase text-slate-400">模型</p>
        <p
          className="truncate text-sm"
          data-testid={`matrix-model-${row.key}`}
        >
          {displayOptionalField(
            row.model,
            row.field_sources.model,
            vocabularySafe,
          )}
        </p>
        {row.model ? (
          <p
            className={cn(
              "text-[11px] text-slate-500 dark:text-slate-400",
              row.field_sources.model === "not_supported" &&
                "font-medium text-amber-600 dark:text-amber-400",
            )}
          >
            {describeFieldSource(row.field_sources.model, vocabularySafe)}
          </p>
        ) : null}
      </div>

      <div className="min-w-0">
        <p className="text-[11px] uppercase text-slate-400">推理深度</p>
        <p
          className="truncate text-sm"
          data-testid={`matrix-effort-${row.key}`}
        >
          {displayOptionalField(
            row.reasoning_effort,
            row.field_sources.reasoning_effort,
            vocabularySafe,
          )}
        </p>
        {row.reasoning_effort ? (
          <p
            className={cn(
              "text-[11px] text-slate-500 dark:text-slate-400",
              row.field_sources.reasoning_effort === "not_supported" &&
                "font-medium text-amber-600 dark:text-amber-400",
            )}
          >
            {describeFieldSource(row.field_sources.reasoning_effort, vocabularySafe)}
          </p>
        ) : null}
      </div>

      <div className="min-w-0">
        <Label className="text-[11px] text-slate-500">本阶段绑定预设</Label>
        <PresetSelect
          value={bindingValue}
          options={presetOptions}
          allowClear
          placeholder={bindingPlaceholder}
          onChange={onBindingChange}
          testId={`matrix-binding-${row.key}`}
        />
        {affected.length > 0 ? (
          <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
            该预设同时用于：
            {affected.join("、")}
          </p>
        ) : null}
      </div>
    </div>
  );
}
