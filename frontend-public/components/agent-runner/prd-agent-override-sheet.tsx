"use client";
/* eslint-disable react-hooks/set-state-in-effect */

// PRD 级生命周期 Agent 覆盖抽屉。
//
// 从 PRD 原文页的「Agent 覆盖」按钮打开，编辑的是该 PRD 文件头部的
// `lifecycle_agents` 覆盖块。写回采用「完整期望集合」语义：未勾选的键会被移除。

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  buildLifecycleOptions,
  LIFECYCLE_AGENT_AUTO,
  lifecycleBaselineValue,
  lifecycleDisplayName,
} from "@/components/agent-runner/lifecycle-agent-matrix";
import { LifecycleAgentSelect } from "@/components/agent-runner/lifecycle-agent-select";
import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import {
  fetchPrdAgentOverrides,
  updatePrdAgentOverrides,
} from "@/lib/api/lifecycleAgents";
import type { LifecycleAgentEntry, PrdAgentOverrideView } from "@/lib/api/types";

/** 单个生命周期键的本地草稿：是否覆盖 + 覆盖取值。 */
type OverrideDraft = {
  enabled: boolean;
  value: string;
};

/** 为某行计算「打开覆盖」时的默认取值（继承视角的生效值）。 */
function defaultOverrideValue(entry: LifecycleAgentEntry): string {
  const baseline = lifecycleBaselineValue(entry);
  if (baseline) {
    return baseline;
  }
  return entry.auto_allowed ? LIFECYCLE_AGENT_AUTO : "";
}

/** 按服务端视图初始化本地草稿。 */
function buildDrafts(view: PrdAgentOverrideView): Record<string, OverrideDraft> {
  const drafts: Record<string, OverrideDraft> = {};
  for (const entry of view.lifecycles) {
    const overrideValue = view.overrides[entry.key];
    drafts[entry.key] = {
      enabled: overrideValue !== undefined,
      value: overrideValue ?? defaultOverrideValue(entry),
    };
  }
  return drafts;
}

interface PrdAgentOverrideSheetProps {
  /** 仓库标识。 */
  repoId: string;
  /** PRD 相对路径。 */
  prdPath: string;
  /** 抽屉开关状态。 */
  open: boolean;
  /** 开关状态变更回调。 */
  onOpenChange: (open: boolean) => void;
}

/**
 * PRD 级生命周期 Agent 覆盖抽屉。
 *
 * @param props - 仓库 id、PRD 路径与抽屉开关状态。
 * @returns 右侧抽屉，逐行勾选并选择覆盖 agent。
 */
export function PrdAgentOverrideSheet({
  repoId,
  prdPath,
  open,
  onOpenChange,
}: PrdAgentOverrideSheetProps) {
  const [view, setView] = useState<PrdAgentOverrideView | null>(null);
  const [drafts, setDrafts] = useState<Record<string, OverrideDraft>>({});
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!open) {
      return;
    }
    let isCancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchPrdAgentOverrides({ repoId, prdPath })
      .then((loadedView) => {
        if (!isCancelled) {
          setView(loadedView);
          setDrafts(buildDrafts(loadedView));
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(
            error instanceof Error ? error.message : "加载 PRD 覆盖失败。",
          );
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setLoading(false);
        }
      });
    return () => {
      isCancelled = true;
    };
  }, [open, repoId, prdPath, reloadToken]);

  /** 把当前勾选状态作为完整期望集合写回 PRD 头部。 */
  async function handleSave() {
    if (!view) {
      return;
    }
    const overrides: Record<string, string | null> = {};
    for (const entry of view.lifecycles) {
      const draft = drafts[entry.key];
      overrides[entry.key] =
        draft?.enabled && draft.value ? draft.value : null;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await updatePrdAgentOverrides({ repoId, prdPath, overrides });
      setReloadToken((current) => current + 1);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>PRD Agent 覆盖</SheetTitle>
          <SheetDescription>
            {prdPath} · 覆盖写入该 PRD 文件头部；未勾选的生命周期沿用仓库 / 全局配置。
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {loading ? (
            <p className="text-sm text-slate-500">加载中…</p>
          ) : loadError ? (
            <ResourceErrorAlert
              message={loadError}
              testId="prd-agent-override-error"
            />
          ) : view ? (
            <div className="space-y-2" data-testid="prd-agent-override-list">
              {view.lifecycles.map((entry) => {
                const draft = drafts[entry.key] ?? {
                  enabled: false,
                  value: defaultOverrideValue(entry),
                };
                const options = buildLifecycleOptions(entry, view.agents);
                return (
                  <div
                    key={entry.key}
                    data-testid={`prd-agent-override-row-${entry.key}`}
                    className="flex items-center gap-3 rounded-md border px-3 py-2"
                  >
                    <Checkbox
                      checked={draft.enabled}
                      onCheckedChange={(checked: boolean) =>
                        setDrafts((current) => ({
                          ...current,
                          [entry.key]: {
                            enabled: checked,
                            // 取当前状态里的取值，不用渲染期快照，避免同一次
                            // 交互里勾选与选择相互覆盖。
                            value: current[entry.key]?.value ?? draft.value,
                          },
                        }))
                      }
                      data-testid={`prd-agent-override-toggle-${entry.key}`}
                      aria-label={`覆盖 ${lifecycleDisplayName(entry.key)}`}
                    />
                    <span className="w-20 text-sm">
                      {lifecycleDisplayName(entry.key)}
                    </span>
                    <LifecycleAgentSelect
                      lifecycleKey={entry.key}
                      options={options}
                      value={draft.value}
                      onValueChange={(value) =>
                        setDrafts((current) => ({
                          ...current,
                          [entry.key]: {
                            enabled: current[entry.key]?.enabled ?? draft.enabled,
                            value,
                          },
                        }))
                      }
                      placeholder="—"
                      testIdPrefix="prd-agent-override"
                      disabled={!draft.enabled}
                      className="flex-1"
                    />
                  </div>
                );
              })}
            </div>
          ) : null}

          {saveError ? (
            <p
              role="alert"
              className="mt-3 text-sm text-red-600 dark:text-red-400"
            >
              {saveError}
            </p>
          ) : null}

          <Button
            size="sm"
            className="mt-4"
            onClick={() => void handleSave()}
            disabled={saving || loading || !view}
            data-testid="prd-agent-override-save"
          >
            {saving ? "保存中…" : "保存覆盖"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
