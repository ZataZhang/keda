"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { updateContentGenerationTimeouts } from "@/lib/api/lifecycleSettings";
import type {
  GeneratedContentTimeoutTargetKey,
  LifecycleSettingsView,
} from "@/lib/api/types";

type TimeoutDraft = Partial<
  Record<GeneratedContentTimeoutTargetKey, string | null>
>;

interface LifecycleContentGenerationTimeoutsProps {
  view: LifecycleSettingsView;
  onSaved: (view: LifecycleSettingsView) => void;
}

/** 生命周期设置页的内容生成超时编辑区。 */
export function LifecycleContentGenerationTimeouts({
  view,
  onSaved,
}: LifecycleContentGenerationTimeoutsProps) {
  const [timeoutDraft, setTimeoutDraft] = useState<TimeoutDraft>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const maxTimeoutSeconds = view.content_generation_timeouts.max_seconds;

  const timeoutChanges = useMemo(() => {
    const values: Partial<
      Record<GeneratedContentTimeoutTargetKey, number | null>
    > = {};
    let hasInvalidValue = false;

    for (const target of view.content_generation_timeouts.targets) {
      const draftedValue = timeoutDraft[target.key];
      if (draftedValue === undefined) {
        continue;
      }
      if (draftedValue === null) {
        if (view.scope === "repository") {
          values[target.key] = null;
        }
        continue;
      }

      const timeoutSeconds = Number(draftedValue);
      if (
        draftedValue.trim() === "" ||
        !Number.isSafeInteger(timeoutSeconds) ||
        timeoutSeconds < 1 ||
        timeoutSeconds > maxTimeoutSeconds
      ) {
        hasInvalidValue = true;
        continue;
      }
      if (timeoutSeconds !== target.timeout_seconds) {
        values[target.key] = timeoutSeconds;
      }
    }

    return { values, hasInvalidValue };
  }, [maxTimeoutSeconds, timeoutDraft, view]);

  const hasChanges = Object.keys(timeoutChanges.values).length > 0;

  /** 保存本次调整的目标超时。 */
  async function handleSave() {
    if (!hasChanges || timeoutChanges.hasInvalidValue) {
      return;
    }
    setIsSaving(true);
    setSaveError(null);
    try {
      const nextView = await updateContentGenerationTimeouts({
        scope: view.scope,
        repoId: view.repo_id ?? undefined,
        values: timeoutChanges.values,
      });
      onSaved(nextView);
      setTimeoutDraft({});
      toast.success("内容生成时限已保存；已启动的任务继续使用启动时配置。");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <Card id="content-generation-timeouts" data-testid="content-generation-timeouts">
      <CardHeader>
        <CardTitle>内容生成时限</CardTitle>
        <CardDescription>
          这些超时独立于实现 Agent 的运行时限。超时后会继续使用模板兜底；延长时限会让无响应的生成任务等待更久。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="divide-y">
          {view.content_generation_timeouts.targets.map((target) => {
            const inputId = `content-generation-timeout-${target.key}`;
            const draftedValue = timeoutDraft[target.key];
            const inputValue =
              draftedValue === null
                ? String(target.global_timeout_seconds ?? target.timeout_seconds)
                : (draftedValue ?? String(target.timeout_seconds));
            const globalTimeout =
              target.global_timeout_seconds ?? target.timeout_seconds;
            const inputNumber = Number(inputValue);
            const inputIsInvalid =
              inputValue.trim() === "" ||
              !Number.isSafeInteger(inputNumber) ||
              inputNumber < 1 ||
              inputNumber > maxTimeoutSeconds;

            return (
              <div
                key={target.key}
                className="grid grid-cols-1 gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_12rem_auto] sm:items-end"
                data-testid={`content-generation-timeout-row-${target.key}`}
              >
                <div className="min-w-0">
                  <Label htmlFor={inputId}>{target.label}</Label>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {view.scope === "repository"
                      ? `全局基线：${globalTimeout} 秒`
                      : "全局生效；仓库可单独覆盖。"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    id={inputId}
                    type="number"
                    min={1}
                    max={maxTimeoutSeconds}
                    step={1}
                    inputMode="numeric"
                    value={inputValue}
                    aria-invalid={inputIsInvalid}
                    data-testid={`content-generation-timeout-${target.key}`}
                    onChange={(event) => {
                      setTimeoutDraft((current) => ({
                        ...current,
                        [target.key]: event.target.value,
                      }));
                    }}
                  />
                  <span className="shrink-0 text-xs text-muted-foreground">秒</span>
                </div>
                {view.scope === "repository" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={draftedValue === null}
                    data-testid={`content-generation-timeout-reset-${target.key}`}
                    onClick={() => {
                      setTimeoutDraft((current) => ({
                        ...current,
                        [target.key]: null,
                      }));
                    }}
                  >
                    跟随全局
                  </Button>
                ) : (
                  <span />
                )}
              </div>
            );
          })}
        </div>

        {timeoutChanges.hasInvalidValue ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            时限必须是 1–{maxTimeoutSeconds} 秒之间的整数。
          </p>
        ) : null}
        {saveError ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {saveError}
          </p>
        ) : null}
        <Button
          type="button"
          size="sm"
          onClick={() => void handleSave()}
          disabled={
            isSaving ||
            !hasChanges ||
            timeoutChanges.hasInvalidValue
          }
          data-testid="content-generation-timeouts-save"
        >
          {isSaving ? "保存中…" : "保存内容生成时限"}
        </Button>
      </CardContent>
    </Card>
  );
}
