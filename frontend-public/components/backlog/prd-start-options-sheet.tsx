"use client";

// 「开始此 PRD」的高级选项抽屉（FR-7）。
//
// 每一项都对应 `kc run` 的同名旗标，全部默认关闭：缺省提交产生空 options，
// 后端折算成空 argv，与不打开本抽屉的普通启动逐字节一致。

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchLaunchOptions } from "@/lib/api/console";
import type { LaunchOptionsView, StartPrdLaunchOptions } from "@/lib/api/types";

interface PrdStartOptionsSheetProps {
  repoId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 提交启动：把当前选项交给页面的启动处理（等价点「开始此 PRD」带选项）。 */
  onSubmit: (options: StartPrdLaunchOptions) => void;
  submitting: boolean;
}

const REASONING_EFFORTS = ["low", "medium", "high"] as const;

/**
 * PRD 启动高级选项抽屉。
 *
 * @param props.repoId - 目标仓库，用于拉取 agent / preset 候选。
 * @param props.open - 是否展开。
 * @param props.onOpenChange - 开合回调。
 * @param props.onSubmit - 携带选项的启动回调。
 * @param props.submitting - 是否正在提交。
 */
export function PrdStartOptionsSheet({
  repoId,
  open,
  onOpenChange,
  onSubmit,
  submitting,
}: PrdStartOptionsSheetProps) {
  const [options, setOptions] = useState<StartPrdLaunchOptions>({});
  const [catalog, setCatalog] = useState<LaunchOptionsView | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  useEffect(() => {
    // 父级只在打开时挂载本抽屉，组件内状态随重挂载自然重置。
    if (!open) return;
    const controller = new AbortController();
    fetchLaunchOptions(repoId)
      .then((view) => {
        if (!controller.signal.aborted) setCatalog(view);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setCatalogError(
            error instanceof Error ? error.message : "无法加载候选清单。",
          );
        }
      });
    return () => controller.abort();
  }, [open, repoId]);

  const fastMerge = options.fast_merge ?? false;
  const directPr = options.direct_pr ?? false;
  const preset = options.preset ?? "";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[420px] overflow-y-auto sm:w-[460px]">
        <SheetHeader>
          <SheetTitle>启动高级选项</SheetTitle>
          <SheetDescription>
            全部默认关闭，对应 CLI 同名旗标；保持默认即与普通「开始此 PRD」完全一致。
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-5 px-4 pb-4">
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">发布档位（互斥）</legend>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={fastMerge}
                disabled={directPr}
                onCheckedChange={(checked) =>
                  setOptions((prev) => ({ ...prev, fast_merge: checked === true }))
                }
              />
              快合（--fast-merge）
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={directPr}
                disabled={fastMerge}
                onCheckedChange={(checked) =>
                  setOptions((prev) => ({ ...prev, direct_pr: checked === true }))
                }
              />
              直出 PR（--direct-pr）
            </label>
          </fieldset>

          <div className="space-y-2">
            <Label htmlFor="run-agent">Agent</Label>
            {catalog === null && catalogError === null ? (
              <Skeleton className="h-8 w-full" />
            ) : catalogError ? (
              <p className="text-xs text-amber-700">{catalogError}</p>
            ) : (
              <select
                id="run-agent"
                className="h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-800 dark:bg-slate-950"
                value={options.agent ?? ""}
                onChange={(event) =>
                  setOptions((prev) => ({
                    ...prev,
                    agent: event.target.value || null,
                  }))
                }
              >
                <option value="">自动（默认）</option>
                {catalog?.agents.map((agent) => (
                  <option key={agent} value={agent}>
                    {agent}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="run-preset">模型预设（--preset）</Label>
            <select
              id="run-preset"
              className="h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-800 dark:bg-slate-950"
              value={preset}
              onChange={(event) =>
                setOptions((prev) => ({
                  ...prev,
                  preset: event.target.value || null,
                  // 取消 preset 时，依赖它的单次覆盖一并清空。
                  model: event.target.value ? prev.model : null,
                  reasoning_effort: event.target.value ? prev.reasoning_effort : null,
                }))
              }
            >
              <option value="">不指定</option>
              {catalog?.presets.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor="run-model">单次覆盖 model</Label>
                <Input
                  id="run-model"
                  disabled={!preset}
                  value={options.model ?? ""}
                  onChange={(event) =>
                    setOptions((prev) => ({
                      ...prev,
                      model: event.target.value || null,
                    }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="run-effort">reasoning_effort</Label>
                <select
                  id="run-effort"
                  className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-800 dark:bg-slate-950"
                  disabled={!preset}
                  value={options.reasoning_effort ?? ""}
                  onChange={(event) =>
                    setOptions((prev) => ({
                      ...prev,
                      reasoning_effort: event.target.value || null,
                    }))
                  }
                >
                  <option value="">不覆盖</option>
                  {REASONING_EFFORTS.map((effort) => (
                    <option key={effort} value={effort}>
                      {effort}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-4 pb-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            data-testid="prd-start-options-submit"
            disabled={submitting}
            onClick={() => onSubmit(options)}
          >
            {submitting ? "启动中…" : "带选项开始"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
