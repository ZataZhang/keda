import { invoke, isTauri } from "@tauri-apps/api/core";

/**
 * 在系统默认浏览器中打开外链。
 *
 * 浏览器里直接新开标签页；Tauri 控制台窗口里 `target="_blank"` 没有新窗口代理，
 * 会静默无反应，因此改走宿主命令 `open_external_url`。命令失败时退回浏览器
 * 默认行为，至少不会把点击吞掉。
 *
 * @param url - 仅接受 http/https 链接；调用方负责确认链接非空。
 */
export async function openExternalUrl(url: string): Promise<void> {
  if (isTauri()) {
    try {
      await invoke("open_external_url", { url });
      return;
    } catch {
      // 旧版宿主没有该命令：继续走浏览器默认行为。
    }
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
