// 管理终端界面偏好（UI 偏好）的持久化工具。
//
// 折叠状态这类「纯展示偏好」只影响本机观感，与仓库数据无关，统一放在
// `iar.console.*` 命名空间的 localStorage 里，与 ConsoleLastRepo 的记忆互相独立。

import { useCallback, useEffect, useState } from "react";

/** 左侧全局导航栏是否收起为图标窄栏的 localStorage 键。 */
export const CONSOLE_NAV_COLLAPSED_KEY = "iar.console.navCollapsed";

/** Backlog「受管理仓库」栏是否收起的 localStorage 键。 */
export const CONSOLE_REPO_PANEL_COLLAPSED_KEY = "iar.console.repoPanelCollapsed";

/** 用户选择「跳过此版本」的更新版本号对应的 localStorage 键。 */
export const CONSOLE_SKIPPED_UPDATE_VERSION_KEY = "iar.console.skippedUpdateVersion";

/**
 * 读取用户选择跳过的更新版本号。
 *
 * 与布尔偏好同样做 SSR / 隐私模式兜底：不可读时视为「未跳过」。
 *
 * @returns 被跳过的版本号；没有记录或不可读时返回 null。
 */
export function readSkippedUpdateVersion(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage.getItem(CONSOLE_SKIPPED_UPDATE_VERSION_KEY);
  } catch {
    return null;
  }
}

/**
 * 记住用户跳过的更新版本号；写不进去只影响下次是否再提示。
 *
 * @param version - 被跳过的版本号。
 */
export function writeSkippedUpdateVersion(version: string): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(CONSOLE_SKIPPED_UPDATE_VERSION_KEY, version);
  } catch {
    // 忽略：记忆失败不影响本次交互。
  }
}

/**
 * 读取布尔偏好。
 *
 * 静态导出下本模块可能在没有 `window` 的环境被求值；隐私模式等场景下
 * `localStorage` 访问也会抛错，两种情况都退化为「用回退值」。
 *
 * @param key - 偏好对应的 localStorage 键。
 * @param fallback - 没有记录或不可读时的回退值。
 * @returns 记住的布尔偏好。
 */
function readBooleanPref(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") {
    return fallback;
  }
  try {
    const stored = window.localStorage.getItem(key);
    return stored === null ? fallback : stored === "1";
  } catch {
    return fallback;
  }
}

/**
 * 写回布尔偏好；写不进去只影响下次首屏偏好，不该打断当前操作。
 *
 * @param key - 偏好对应的 localStorage 键。
 * @param value - 要记住的布尔值。
 */
function writeBooleanPref(key: string, value: boolean): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // 忽略：偏好记忆失败不影响本次交互。
  }
}

/**
 * 持久化的布尔偏好 state。
 *
 * 初值固定用 `fallback`（服务端与首帧客户端一致，避免 hydration mismatch），
 * 挂载后从 `localStorage` 读回真实偏好；写入时同步更新 state 与存储。
 *
 * @param key - 偏好对应的 localStorage 键。
 * @param fallback - 没有记录时的回退值，默认 `false`。
 * @returns `[value, setValue]`，`setValue` 与 `useState` 同签名。
 */
export function usePersistedBoolean(
  key: string,
  fallback = false,
): [boolean, (next: boolean) => void] {
  const [value, setValue] = useState(fallback);

  useEffect(() => {
    // 挂载后再读 localStorage：服务端与首帧客户端都用 fallback，避免 hydration mismatch。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValue(readBooleanPref(key, fallback));
  }, [key, fallback]);

  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      writeBooleanPref(key, next);
    },
    [key],
  );

  return [value, update];
}
