"use client";

/**
 * Inline background-sync settings panel for the monitoring dashboard.
 *
 * Deliberately inline (no dialog component exists in `components/ui/`) and
 * auto-saving: every switch/interval change fires a PATCH immediately so the
 * backend scheduler can recompute its wait window right away.
 */

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchMonitorSettings,
  updateMonitorSettings,
} from "@/lib/api/console";
import type { MonitorSettings } from "@/lib/api/types";

/** Selectable sync intervals, in minutes (backend accepts 60–3600 seconds). */
export const SYNC_INTERVAL_OPTIONS_MINUTES = [1, 5, 15, 30, 60] as const;

const DEFAULT_INTERVAL_MINUTES = 5;

/** Convert seconds from the API into the nearest selectable minute option. */
function secondsToMinutesOption(seconds: number): number {
  const minutes = Math.round(seconds / 60);
  return (
    SYNC_INTERVAL_OPTIONS_MINUTES.find((option) => option === minutes) ??
    DEFAULT_INTERVAL_MINUTES
  );
}

/**
 * Inline sync settings panel.
 *
 * @param onClose - Callback invoked when the user collapses the panel.
 */
export function MonitorSettingsPanel({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<MonitorSettings | null>(null);
  const [intervalMinutes, setIntervalMinutes] = useState<number>(
    DEFAULT_INTERVAL_MINUTES,
  );
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  /** 用户是否已经动过面板：动过之后晚到的初始 GET 不得回滚其选择。 */
  const userEditedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetchMonitorSettings()
      .then((loaded) => {
        if (cancelled || userEditedRef.current) return;
        setSettings(loaded);
        setSyncEnabled(loaded.sync_enabled);
        setIntervalMinutes(secondsToMinutesOption(loaded.sync_interval_seconds));
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (cancelled || userEditedRef.current) return;
        setLoadError(
          error instanceof Error ? error.message : "无法加载同步设置。",
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Persist a settings change and surface failures instead of faking success. */
  async function persist(nextEnabled: boolean, nextMinutes: number) {
    userEditedRef.current = true;
    setSaving(true);
    try {
      const saved = await updateMonitorSettings({
        sync_enabled: nextEnabled,
        sync_interval_seconds: nextMinutes * 60,
      });
      setSettings(saved);
      setSavedAt(saved.updated_at);
      setLoadError(null);
      toast.success("同步设置已保存");
    } catch (error: unknown) {
      const message =
        error instanceof Error ? error.message : "保存同步设置失败。";
      setLoadError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card data-testid="monitor-settings-panel">
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-sm">后台自动同步</CardTitle>
        <Button size="sm" variant="ghost" onClick={onClose}>
          收起
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">自动同步</span>
          <Button
            size="sm"
            variant={syncEnabled ? "default" : "outline"}
            disabled={saving}
            data-testid="monitor-sync-toggle-enabled"
            onClick={() => {
              setSyncEnabled(true);
              void persist(true, intervalMinutes);
            }}
          >
            开启
          </Button>
          <Button
            size="sm"
            variant={syncEnabled ? "outline" : "default"}
            disabled={saving}
            data-testid="monitor-sync-toggle-disabled"
            onClick={() => {
              setSyncEnabled(false);
              void persist(false, intervalMinutes);
            }}
          >
            关闭
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500">同步间隔</span>
          {SYNC_INTERVAL_OPTIONS_MINUTES.map((minutes) => (
            <Button
              key={minutes}
              size="sm"
              variant={intervalMinutes === minutes ? "default" : "outline"}
              disabled={saving}
              data-testid={`monitor-sync-interval-${minutes}`}
              onClick={() => {
                setIntervalMinutes(minutes);
                void persist(syncEnabled, minutes);
              }}
            >
              {minutes} 分钟
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span data-testid="monitor-settings-status">
            {loadError
              ? `保存失败：${loadError}`
              : savedAt
                ? "已保存 ✓"
                : settings
                  ? `当前：${syncEnabled ? `每 ${intervalMinutes} 分钟` : "已关闭"}`
                  : "加载中…"}
          </span>
          {saving ? <span>保存中…</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
