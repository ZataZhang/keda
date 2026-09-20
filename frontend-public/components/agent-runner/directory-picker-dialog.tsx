"use client";
/* eslint-disable react-hooks/set-state-in-effect */

// 本机目录选择器。
//
// 浏览器拿不到真实绝对路径（showDirectoryPicker 只给不透明 handle，
// webkitdirectory 只有相对路径），所以目录列举由本机后端完成：本组件只负责
// 展示一层子目录、逐级下钻，并把当前所在目录作为选择结果回传给调用方。
//
// 单击目录行 = 进入该目录；「选择此目录」选的是当前所在目录，不引入选中态。

import { useEffect, useState } from "react";
import {
  ArrowUpIcon,
  ChevronRightIcon,
  FolderGit2Icon,
  FolderIcon,
  HomeIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import { browseDirectories } from "@/lib/api/console";
import type { BrowsableDirectoryEntry, DirectoryBrowseResult } from "@/lib/api/types";

/** 选择器回传的选定目录信息。 */
export interface DirectorySelection {
  /** 选定目录的绝对路径。 */
  path: string;
  /** 后端按目录名规范化的仓库 ID 建议值。 */
  suggestedRepoId: string;
  /** 显示名建议值（目录名）。 */
  suggestedDisplayName: string;
}

interface DirectoryPickerDialogProps {
  /** 弹窗开关状态。 */
  open: boolean;
  /** 开关状态变更回调。 */
  onOpenChange: (open: boolean) => void;
  /** 首次打开时的起始目录；省略则从用户主目录开始。 */
  initialPath?: string;
  /** 确认选择当前目录时的回调。 */
  onSelect: (selection: DirectorySelection) => void;
}

/** 把绝对路径拆成可点击的面包屑层级（非 POSIX 路径退化为单层）。 */
function buildBreadcrumbs(absolutePath: string): { label: string; path: string }[] {
  if (!absolutePath.startsWith("/")) {
    return [{ label: absolutePath, path: absolutePath }];
  }
  const crumbs = [{ label: "/", path: "/" }];
  let accumulatedPath = "";
  for (const segment of absolutePath.split("/").filter((item) => item.length > 0)) {
    accumulatedPath += `/${segment}`;
    crumbs.push({ label: segment, path: accumulatedPath });
  }
  return crumbs;
}

/** 渲染单个目录条目的状态徽标。 */
function DirectoryBadges({ entry }: { entry: BrowsableDirectoryEntry }) {
  return (
    <>
      {entry.is_git_repo ? (
        <Badge variant="supervising" className="text-[10px]">
          git 仓库
        </Badge>
      ) : null}
      {entry.has_iar_config ? (
        <Badge variant="ready" className="text-[10px]">
          IAR 已初始化
        </Badge>
      ) : null}
      {entry.already_registered ? (
        <Badge variant="default" className="text-[10px]">
          已注册
        </Badge>
      ) : null}
    </>
  );
}

/**
 * 本机目录选择器弹窗。
 *
 * @param props - 开关状态、起始目录与选择结果回调。
 * @returns 可选面包屑导航、逐级下钻与「选择此目录」的弹窗。
 */
export function DirectoryPickerDialog({
  open,
  onOpenChange,
  initialPath,
  onSelect,
}: DirectoryPickerDialogProps) {
  const [current, setCurrent] = useState<DirectoryBrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    let isCancelled = false;
    setLoading(true);
    setLoadError(null);
    // 清掉上一次打开残留的目录，避免闪现旧内容后又被新结果替换。
    setCurrent(null);
    browseDirectories(initialPath)
      .then((result) => {
        if (!isCancelled) {
          setCurrent(result);
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(error instanceof Error ? error.message : "无法读取该目录。");
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setLoading(false);
        }
      });
    return () => {
      isCancelled = true;
    };
  }, [open, initialPath]);

  /** 下钻到指定目录；读取失败时保留当前目录并提示。 */
  async function navigateTo(targetPath?: string) {
    setLoading(true);
    setLoadError(null);
    try {
      setCurrent(await browseDirectories(targetPath));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "无法读取该目录。");
    } finally {
      setLoading(false);
    }
  }

  /** 把当前所在目录作为选择结果回传，并关闭弹窗。 */
  function handleConfirm() {
    if (!current) {
      return;
    }
    onSelect({
      path: current.path,
      suggestedRepoId: current.suggested_repo_id,
      suggestedDisplayName: current.suggested_display_name,
    });
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-2xl"
        data-testid="directory-picker-dialog"
      >
        <DialogHeader>
          <DialogTitle>选择仓库目录</DialogTitle>
          <DialogDescription>
            逐级进入目标目录后点「选择此目录」；带 <code>git 仓库</code> 标记的目录才能通过校验。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void navigateTo(current?.parent ?? undefined)}
            disabled={loading || !current || current.parent === null}
            data-testid="directory-picker-up"
          >
            <ArrowUpIcon className="size-3.5" />
            上一级
          </Button>
          {/* 起始路径非法时 current 为空，此时仍要能逃到主目录，故不禁用。 */}
          <Button
            size="sm"
            variant="outline"
            onClick={() => void navigateTo(current?.home)}
            disabled={loading}
            data-testid="directory-picker-home"
          >
            <HomeIcon className="size-3.5" />
            主目录
          </Button>
        </div>

        {current ? (
          <nav
            className="flex flex-wrap items-center gap-1 font-mono text-xs text-slate-500"
            data-testid="directory-picker-breadcrumbs"
          >
            {buildBreadcrumbs(current.path).map((crumb, index) => (
              <span key={crumb.path} className="flex items-center gap-1">
                {index > 0 ? <ChevronRightIcon className="size-3" /> : null}
                <button
                  type="button"
                  className="rounded-xs px-1 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                  onClick={() => void navigateTo(crumb.path)}
                  disabled={crumb.path === current.path}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </nav>
        ) : null}

        <div className="h-64 overflow-y-auto rounded-md border border-slate-200 dark:border-slate-800">
          {loading ? (
            <p className="p-3 text-sm text-slate-500">读取中…</p>
          ) : loadError ? (
            <div className="p-3">
              <ResourceErrorAlert
                message={loadError}
                testId="directory-picker-error"
              />
            </div>
          ) : current && current.directories.length > 0 ? (
            <ul data-testid="directory-picker-list">
              {current.directories.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 border-b border-slate-100 px-3 py-2 text-left text-sm hover:bg-slate-100 dark:border-slate-800 dark:hover:bg-slate-800"
                    onClick={() => void navigateTo(entry.path)}
                    data-testid={`directory-picker-entry-${entry.name}`}
                  >
                    {entry.is_git_repo ? (
                      <FolderGit2Icon className="size-4 shrink-0 text-slate-400" />
                    ) : (
                      <FolderIcon className="size-4 shrink-0 text-slate-400" />
                    )}
                    <span className="flex-1 truncate">{entry.name}</span>
                    <DirectoryBadges entry={entry} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="p-3 text-sm text-slate-500">该目录下没有子目录。</p>
          )}
        </div>

        <p className="font-mono text-xs text-slate-500" data-testid="directory-picker-current-path">
          将选择：{current?.path ?? "—"}
        </p>

        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="directory-picker-cancel"
          >
            取消
          </Button>
          <Button
            size="sm"
            onClick={handleConfirm}
            disabled={loading || !current}
            data-testid="directory-picker-confirm"
          >
            选择此目录
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
