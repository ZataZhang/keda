"use client";

import { invoke, isTauri } from "@tauri-apps/api/core";
import { useCallback, useRef, useState, useSyncExternalStore } from "react";

import { DesktopCompanion, requestCompanionClose } from "@/components/dashboard/desktop-companion";
import { runtimeTaskKey } from "@/components/dashboard/runtime-task-list";
import type { RuntimeTaskEntry, RuntimeTaskSummary } from "@/lib/api/types";

// Tauri 宿主身份在当前页面生命周期内固定，无需注册变更事件。
const subscribeDesktopHost = () => () => {};

/** 需要提醒用户的任务时刻：阻塞、失败、等待人工审阅、Issue 已关闭。 */
type CompanionAttentionKind = "blocked" | "failed" | "human_review" | "closed";

/** 各提醒类别在通知标题里的状态词。“已关闭”不区分合并与手动关闭，不得写成“已合并”。 */
const ATTENTION_TITLE_WORDS: Record<CompanionAttentionKind, string> = {
  blocked: "已阻塞",
  failed: "已失败",
  human_review: "待你审阅",
  closed: "已关闭",
};

/**
 * 判断任务当前是否处在需要提醒用户的时刻。
 *
 * @param task - 看板上的一个任务。
 * @returns 提醒类别；不需要提醒时为 `null`。
 */
function companionAttentionKind(task: RuntimeTaskEntry): CompanionAttentionKind | null {
  if (task.status === "blocked" || task.status === "failed" || task.status === "closed") {
    return task.status;
  }
  if (task.status === "waiting" && task.waiting_reason === "human_review") {
    return "human_review";
  }
  return null;
}

/**
 * 把 Tauri 的 invoke 失败原因转成可读文案。
 *
 * Rust 侧返回的 Err 过 IPC 之后是**字符串**，不是 Error 实例；只判断
 * `instanceof Error` 会把真实原因吞成一句笼统的「失败」。
 *
 * @param error - catch 到的未知值。
 * @returns 可展示的错误文案。
 */
function describeInvokeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

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
  // 每个任务最近一次已提醒的类别；任务重新进入“运行中”时清空，首轮只建基线。
  const notifiedKindsRef = useRef(new Map<string, CompanionAttentionKind>());
  const baselineReadyRef = useRef(false);

  /** 请求关窗并把后端返回的状态如实展示；请求失败也说明失败。 */
  async function handleClose() {
    if (nativeDesktopApp) {
      try {
        await invoke("hide_panel");
      } catch (error: unknown) {
        setNotice(`隐藏面板失败：${describeInvokeError(error)}`);
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
      setNotice(`切换窗口模式失败：${describeInvokeError(error)}`);
    }
  }

  /** 打开完整控制台窗口；失败原因如实回显。 */
  async function handleOpenConsole() {
    try {
      await invoke("open_console_window");
    } catch (error: unknown) {
      setNotice(`打开控制台失败：${describeInvokeError(error)}`);
    }
  }

  /**
   * 任务进入需要人处理的时刻（阻塞、失败、等待人工审阅、Issue 已关闭）时发系统通知。
   *
   * 同一任务的同一类提醒，在任务重新进入“运行中”之前只发一次，避免“待审阅 ↔ 等待复查”
   * 来回切换时反复弹出；首轮读数只建立基线，不补发打开 App 前就已存在的状态。
   * 通知只含仓库名、编号、状态词和 Issue 标题，不带阻塞摘要、错误摘要或日志。
   */
  const handleSummary = useCallback(
    (next: RuntimeTaskSummary) => {
      if (!nativeDesktopApp) {
        return;
      }
      const notifiedKinds = notifiedKindsRef.current;
      const isBaselineRound = !baselineReadyRef.current;
      for (const task of next.tasks) {
        const key = runtimeTaskKey(task);
        if (task.status === "running") {
          notifiedKinds.delete(key);
          continue;
        }
        const attentionKind = companionAttentionKind(task);
        if (attentionKind === null || notifiedKinds.get(key) === attentionKind) {
          continue;
        }
        notifiedKinds.set(key, attentionKind);
        if (isBaselineRound) {
          continue;
        }
        void invoke("notify_task", {
          title: `${task.repo_display_name ?? task.repo_id}#${task.issue_number} ${ATTENTION_TITLE_WORDS[attentionKind]}`,
          body: task.issue_title ?? task.status_reason ?? "打开控制台查看详情",
        }).catch((error: unknown) => {
          setNotice(`发送通知失败：${describeInvokeError(error)}`);
        });
      }
      baselineReadyRef.current = true;
    },
    [nativeDesktopApp],
  );

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
        onOpenConsole={nativeDesktopApp ? () => void handleOpenConsole() : undefined}
        onSummary={handleSummary}
      />
      {notice ? (
        <p className="px-3 pb-3 text-[11px] text-slate-500" data-testid="companion-close-notice">
          {notice}
        </p>
      ) : null}
    </main>
  );
}
