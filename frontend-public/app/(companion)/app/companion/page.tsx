"use client";

import { invoke, isTauri } from "@tauri-apps/api/core";
import { useState, useSyncExternalStore } from "react";

import { DesktopCompanion, requestCompanionClose } from "@/components/dashboard/desktop-companion";

// Tauri 宿主身份在当前页面生命周期内固定，无需注册变更事件。
const subscribeDesktopHost = () => () => {};

/**
 * `/app/companion/`：桌面小窗与浏览器窄页共用的紧凑看板。
 *
 * 关闭按钮请求的是**本机窗口**进程自行关窗（后端只递增工单，不 kill），
 * 因此这里把返回的状态文案如实显示出来，而不是假装标签页被关掉了。
 */
export default function CompanionPage() {
  const [notice, setNotice] = useState<string | null>(null);
  const nativeDesktopApp = useSyncExternalStore(subscribeDesktopHost, isTauri, () => false);
  const [pinned, setPinned] = useState(false);

  /** 请求关窗并把后端返回的状态如实展示；请求失败也说明失败。 */
  async function handleClose() {
    if (nativeDesktopApp) {
      try {
        await invoke("hide_panel");
      } catch (error: unknown) {
        setNotice(error instanceof Error ? `隐藏面板失败：${error.message}` : "隐藏面板失败。");
      }
      return;
    }

    try {
      const state = await requestCompanionClose();
      setNotice(state.hint);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? `关窗请求失败：${error.message}` : "关窗请求失败。");
    }
  }

  /** 切换菜单栏 popover 与原生悬浮窗模式。 */
  async function handleTogglePinned() {
    const nextPinned = !pinned;
    try {
      await invoke("set_panel_pinned", { pinned: nextPinned });
      setPinned(nextPinned);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? `切换窗口模式失败：${error.message}` : "切换窗口模式失败。");
    }
  }

  return (
    <main
      className="mx-auto flex h-svh min-h-0 w-full max-w-md flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl dark:border-slate-800 dark:bg-slate-950 dark:text-slate-100"
      data-testid="companion-page"
    >
      <DesktopCompanion
        onClose={() => void handleClose()}
        onTogglePinned={nativeDesktopApp ? () => void handleTogglePinned() : undefined}
        isPinned={pinned}
        closeLabel={nativeDesktopApp ? "隐藏" : "关闭"}
      />
      {notice ? (
        <p className="px-3 pb-3 text-[11px] text-slate-500" data-testid="companion-close-notice">
          {notice}
        </p>
      ) : null}
    </main>
  );
}
