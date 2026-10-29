import { isTauri } from "@tauri-apps/api/core";
import { confirm as confirmTauri } from "@tauri-apps/plugin-dialog";

/**
 * 根据运行环境显示操作确认框。
 *
 * @param message - 需要用户确认的操作说明。
 * @returns 用户是否确认继续。
 */
export async function confirmAction(message: string): Promise<boolean> {
  if (isTauri()) {
    return confirmTauri(message, {
      title: "KedaCode",
      kind: "warning",
      okLabel: "确认",
      cancelLabel: "取消",
    });
  }

  return window.confirm(message);
}
