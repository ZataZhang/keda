// 管理终端首屏「当前项目」解析。
//
// `iar console` 是多仓库面板，但用户在某个仓库目录敲下它时，期待打开就落在
// 这个仓库上，而不是 registry 声明顺序最靠前的那个。优先级：
//
//   1. console 进程 cwd 匹配到的仓库（`GET /console/context`）——「当前项目」
//   2. 上次手动选择的仓库（localStorage）——cwd 匹配不上时的记忆兜底
//   3. registry 里第一个 enabled 仓库——以上都拿不到时的最终兜底
//
// Backlog / Processes / Ideas 三个页面共用本模块，避免各自重复实现这套优先级。

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { fetchConsoleContext, fetchRegistryRepositories } from "@/lib/api/console";
import type { RegistryRepositoryEntry } from "@/lib/api/types";

/** 上次手动选择的仓库 id 的 localStorage 键。 */
export const CONSOLE_LAST_REPO_KEY = "iar.console.lastRepoId";

/**
 * 读取上次手动选择的仓库 id。
 *
 * 静态导出下本模块可能在没有 `window` 的环境被求值；隐私模式等场景下
 * `localStorage` 访问也会抛错，两种情况都退化为「无记忆」。
 *
 * @returns 记住的仓库 id；没有记录或不可读时返回 null。
 */
export function readRememberedRepoId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage.getItem(CONSOLE_LAST_REPO_KEY);
  } catch {
    return null;
  }
}

/**
 * 记住用户手动选择的仓库，供下次打开面板时兜底。
 *
 * @param repoId - 用户选中的仓库 id。
 */
export function writeRememberedRepoId(repoId: string): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(CONSOLE_LAST_REPO_KEY, repoId);
  } catch {
    // 记忆写不进去只影响下次首屏偏好，不该打断当前操作。
  }
}

/**
 * 按 cwd → 记忆 → 首个 enabled 的顺序挑出首屏仓库。
 *
 * 落在 enabled 列表之外的候选一律忽略：停用或已被移出 registry 的仓库不能作为
 * 首屏默认目标。
 *
 * @param options - 候选来源：enabled 仓库 id 列表、cwd 匹配结果、记忆的 id。
 * @returns 选中的仓库 id；一个 enabled 仓库都没有时返回空字符串。
 */
export function pickInitialRepoId({
  enabledIds,
  contextRepoId,
  rememberedRepoId,
}: {
  enabledIds: string[];
  contextRepoId: string | null;
  rememberedRepoId: string | null;
}): string {
  const candidates = [contextRepoId, rememberedRepoId, enabledIds[0]];
  for (const candidate of candidates) {
    if (candidate && enabledIds.includes(candidate)) {
      return candidate;
    }
  }
  return "";
}

/** 管理终端仓库选择状态。 */
export type RepositorySelection = {
  /** registry 全量条目（含停用），供需要展示停用态的页面使用。 */
  repositories: RegistryRepositoryEntry[];
  /** enabled 条目，供只需要可选项的页面使用。 */
  enabledRepositories: RegistryRepositoryEntry[];
  /** 当前选中的仓库 id；尚未解析出来时为空字符串。 */
  selectedRepoId: string;
  /** 切换仓库，同时记入 localStorage 供下次首屏兜底。 */
  selectRepoId: (repoId: string) => void;
  /** 仓库列表是否仍在加载。 */
  loading: boolean;
};

/**
 * 加载 registry 仓库列表并解析出首屏应选中的仓库。
 *
 * 仓库列表是硬依赖（拿不到就无从选择），失败时提示；cwd 上下文只是首屏偏好，
 * 失败时静默降级为「无提示」，不该让页面报错。
 *
 * @returns 仓库列表、当前选中 id、切换回调与加载态。
 */
export function useRepositorySelection(): RepositorySelection {
  const [repositories, setRepositories] = useState<RegistryRepositoryEntry[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([fetchRegistryRepositories(), fetchConsoleContext()])
      .then(([repositoriesResult, contextResult]) => {
        if (cancelled) {
          return;
        }
        if (repositoriesResult.status === "rejected") {
          throw repositoriesResult.reason;
        }
        const loaded = repositoriesResult.value;
        setRepositories(loaded);
        setSelectedRepoId(
          (current) =>
            current ||
            pickInitialRepoId({
              enabledIds: loaded
                .filter((entry) => entry.enabled)
                .map((entry) => entry.repo_id),
              contextRepoId:
                contextResult.status === "fulfilled"
                  ? contextResult.value.repo_id
                  : null,
              rememberedRepoId: readRememberedRepoId(),
            }),
        );
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        toast.error(error instanceof Error ? error.message : "加载仓库列表失败。");
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectRepoId = useCallback((repoId: string) => {
    setSelectedRepoId(repoId);
    writeRememberedRepoId(repoId);
  }, []);

  const enabledRepositories = useMemo(
    () => repositories.filter((entry) => entry.enabled),
    [repositories],
  );

  return {
    repositories,
    enabledRepositories,
    selectedRepoId,
    selectRepoId,
    loading,
  };
}
