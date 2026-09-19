"use client";
/* eslint-disable react-hooks/set-state-in-effect */

// 生命周期 Agent 矩阵编辑器（全局层 / 仓库层共用）。
//
// 只渲染九个生命周期键 → agent 的映射，并负责：
// - 只把「用户改动的行」放进写回载荷（保留式写回，未改动的键不发送）；
// - 为「本层显式声明的键」提供恢复动作（写成 null，即删除本键、回落到继承层）；
// - 保存前展示将要写入的键，避免误改配置。
//
// 组件自己拉取与保存数据；父级只需给出视角（global / repository）。

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import {
  fetchLifecycleAgents,
  updateLifecycleAgents,
} from "@/lib/api/lifecycleAgents";
import type {
  LifecycleAgentEntry,
  LifecycleAgentScope,
  LifecycleAgentsView,
} from "@/lib/api/types";

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

/** 下拉框里的一个候选值。 */
export type LifecycleOption = {
  value: string;
  label: string;
  description?: string;
};

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

/** 渲染单个候选值标签（auto 时附带语义说明）。 */
function optionLabel(option: LifecycleOption) {
  return (
    <span className="flex flex-col">
      <span>{option.label}</span>
      {option.description ? (
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {option.description}
        </span>
      ) : null}
    </span>
  );
}

interface LifecycleAgentMatrixProps {
  /** 编辑视角：全局层（config.toml）或仓库层（.iar.toml）。 */
  scope: LifecycleAgentScope;
  /** 仓库级视角必填。 */
  repoId?: string;
  /** data-testid 前缀，用于区分全局与仓库级矩阵。 */
  testIdPrefix: string;
  /** 保存成功后的回调。 */
  onSaved?: (view: LifecycleAgentsView) => void;
}

/**
 * 生命周期 Agent 矩阵编辑器。
 *
 * @param props - 视角、仓库 id、测试 id 前缀与保存回调。
 * @returns 九行矩阵编辑器。
 */
export function LifecycleAgentMatrix({
  scope,
  repoId,
  testIdPrefix,
  onSaved,
}: LifecycleAgentMatrixProps) {
  const [view, setView] = useState<LifecycleAgentsView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draftValues, setDraftValues] = useState<Record<string, string>>({});
  const [pendingDeletes, setPendingDeletes] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  /** 用服务端视图重置本地草稿（仅在加载完成或保存返回后调用）。 */
  const applyView = useCallback((nextView: LifecycleAgentsView) => {
    const nextDraft: Record<string, string> = {};
    for (const entry of nextView.lifecycles) {
      nextDraft[entry.key] = lifecycleBaselineValue(entry);
    }
    setView(nextView);
    setDraftValues(nextDraft);
    setPendingDeletes({});
  }, []);

  useEffect(() => {
    let isCancelled = false;
    setLoadError(null);
    fetchLifecycleAgents({ scope, repoId })
      .then((loadedView) => {
        if (!isCancelled) {
          applyView(loadedView);
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(
            error instanceof Error ? error.message : "加载生命周期矩阵失败。",
          );
        }
      });
    return () => {
      isCancelled = true;
    };
  }, [scope, repoId, applyView]);

  /** 计算本次保存的载荷：只含改动行与待删除键。 */
  function buildPayload(): Record<string, string | null> {
    if (!view) {
      return {};
    }
    const payload: Record<string, string | null> = {};
    for (const entry of view.lifecycles) {
      if (pendingDeletes[entry.key]) {
        if (entry.declared_in_scope) {
          payload[entry.key] = null;
        }
        continue;
      }
      if (draftValues[entry.key] !== lifecycleBaselineValue(entry)) {
        payload[entry.key] = draftValues[entry.key];
      }
    }
    return payload;
  }

  const payload = buildPayload();
  const changedKeys = Object.keys(payload);

  /** 提交本次改动的矩阵键（保留式写回，只发送改动行）。 */
  async function handleSave() {
    if (changedKeys.length === 0) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const updatedView = await updateLifecycleAgents({
        scope,
        repoId,
        values: payload,
      });
      applyView(updatedView);
      onSaved?.(updatedView);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div
        role="alert"
        className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
        data-testid={`${testIdPrefix}-error`}
      >
        {loadError}
      </div>
    );
  }

  if (!view) {
    return (
      <p className="text-sm text-slate-500" data-testid={`${testIdPrefix}-loading`}>
        加载中…
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid={`${testIdPrefix}-matrix`}>
      <div className="grid grid-cols-[minmax(5rem,0.8fr)_minmax(9rem,1.2fr)_minmax(9rem,1.4fr)] gap-2 border-b pb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
        <span>生命周期</span>
        <span>当前值</span>
        <span>当前值来源</span>
      </div>

      {view.lifecycles.map((entry) => {
        const options = buildLifecycleOptions(entry, view.agents);
        const pendingDelete = Boolean(pendingDeletes[entry.key]);
        const selectedValue = draftValues[entry.key] ?? lifecycleBaselineValue(entry);
        const sourceLabel = view.source_layers[entry.source] ?? entry.source;
        const rowChanged =
          pendingDelete ||
          selectedValue !== lifecycleBaselineValue(entry);
        return (
          <div
            key={entry.key}
            data-testid={`lifecycle-matrix-row-${entry.key}`}
            className={cn(
              "grid grid-cols-[minmax(5rem,0.8fr)_minmax(9rem,1.2fr)_minmax(9rem,1.4fr)] items-center gap-2 rounded-md px-1 py-1",
              rowChanged && "bg-amber-50 dark:bg-amber-950/30",
            )}
          >
            <span className="text-sm">{lifecycleDisplayName(entry.key)}</span>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pendingDelete}
                  className="justify-between"
                  data-testid={`lifecycle-matrix-select-${entry.key}`}
                >
                  <span className="truncate">
                    {pendingDelete ? "待删除本层键" : selectedValue || "—"}
                  </span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="min-w-48">
                <DropdownMenuRadioGroup
                  value={selectedValue}
                  onValueChange={(value) => {
                    setDraftValues((current) => ({
                      ...current,
                      [entry.key]: value,
                    }));
                  }}
                >
                  {options.map((option) => (
                    <DropdownMenuRadioItem
                      key={option.value}
                      value={option.value}
                      data-testid={`lifecycle-matrix-option-${entry.key}-${option.value}`}
                    >
                      {optionLabel(option)}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <span data-testid={`lifecycle-matrix-source-${entry.key}`}>
                {sourceLabel}
              </span>
              {entry.declared_in_scope ? (
                <Badge variant="ready" className="text-[10px]">
                  本层设置
                </Badge>
              ) : null}
              {entry.declared_in_scope ? (
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="h-auto px-0 text-xs"
                  onClick={() => {
                    setPendingDeletes((current) => ({
                      ...current,
                      [entry.key]: !current[entry.key],
                    }));
                  }}
                  data-testid={`lifecycle-matrix-restore-${entry.key}`}
                >
                  {pendingDelete ? "取消删除" : view.restore_hint}
                </Button>
              ) : null}
            </span>
          </div>
        );
      })}

      {changedKeys.length > 0 ? (
        <div
          className="rounded-md border bg-slate-50 p-2 text-xs dark:bg-slate-900"
          data-testid={`${testIdPrefix}-preview`}
        >
          <p className="font-medium">将写入以下键：</p>
          <ul className="mt-1 space-y-0.5">
            {changedKeys.map((key) => (
              <li key={key}>
                {lifecycleDisplayName(key)} →{" "}
                {payload[key] === null ? "删除本层键（回落到继承层）" : payload[key]}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="text-xs text-slate-500 dark:text-slate-400">
        保存只修改本层对应的配置文件，其它层级与文件不受影响。
      </p>

      {saveError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {saveError}
        </p>
      ) : null}

      <Button
        size="sm"
        onClick={() => void handleSave()}
        disabled={saving || changedKeys.length === 0}
        data-testid={`${testIdPrefix}-save`}
      >
        {saving ? "保存中…" : "保存矩阵"}
      </Button>
    </div>
  );
}
