"use client";

import { useState } from "react";

import { DesktopCompanion, requestCompanionClose } from "@/components/dashboard/desktop-companion";

/**
 * `/app/companion/`：桌面小窗与浏览器窄页共用的紧凑看板。
 *
 * 关闭按钮请求的是**本机窗口**进程自行关窗（后端只递增工单，不 kill），
 * 因此这里把返回的状态文案如实显示出来，而不是假装标签页被关掉了。
 */
export default function CompanionPage() {
  const [notice, setNotice] = useState<string | null>(null);

  /** 请求关窗并把后端返回的状态如实展示；请求失败也说明失败。 */
  async function handleClose() {
    try {
      const state = await requestCompanionClose();
      setNotice(state.hint);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? `关窗请求失败：${error.message}` : "关窗请求失败。");
    }
  }

  return (
    <main
      className="mx-auto flex h-svh min-h-0 w-full max-w-md flex-col"
      data-testid="companion-page"
    >
      <DesktopCompanion onClose={() => void handleClose()} />
      {notice ? (
        <p className="px-3 pb-3 text-[11px] text-slate-500" data-testid="companion-close-notice">
          {notice}
        </p>
      ) : null}
    </main>
  );
}
