"use client";

import type { ReactNode } from "react";

import {
  ConsolePageHeader,
  ConsoleToolbar,
} from "@/components/layout/console-page";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type {
  LifecycleAgentScope,
  RegistryRepositoryEntry,
} from "@/lib/api/types";

interface LifecycleSettingsScopeBarProps {
  scope: LifecycleAgentScope;
  repoId?: string;
  repositories: RegistryRepositoryEntry[];
  workspaceNavigation?: ReactNode;
  onScopeChange: (scope: LifecycleAgentScope) => void;
  onRepoChange: (repoId: string) => void;
}

/**
 * 渲染生命周期设置页的全局 / 仓库范围选择条。
 *
 * @param props - 当前范围、可选仓库、导航内容与选择回调。
 * @returns 页面标题、顶层导航和范围选择控件。
 */
export function LifecycleSettingsScopeBar({
  scope,
  repoId,
  repositories,
  workspaceNavigation,
  onScopeChange,
  onRepoChange,
}: LifecycleSettingsScopeBarProps) {
  return (
    <>
      <ConsolePageHeader
        title="生命周期与模型设置"
        description="配置全局或仓库的阶段 Agent、模型预设与执行器回退。"
      />
      {workspaceNavigation}
      <ConsoleToolbar>
        <div className="inline-flex rounded-md border border-border p-0.5">
          <button
            type="button"
            onClick={() => onScopeChange("global")}
            data-testid="scope-global"
            className={cn(
              "h-8 rounded-md px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              scope === "global"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            全局
          </button>
          <button
            type="button"
            onClick={() => onScopeChange("repository")}
            data-testid="scope-repository"
            className={cn(
              "h-8 rounded-md px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              scope === "repository"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            仓库
          </button>
        </div>
        {scope === "repository" ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="scope-repo-select"
              >
                {repositories.find((repo) => repo.repo_id === repoId)?.display_name ??
                  repoId ??
                  "选择仓库"}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="min-w-56">
              {repositories.map((repo) => (
                <DropdownMenuItem
                  key={repo.repo_id}
                  onSelect={() => onRepoChange(repo.repo_id)}
                  data-testid={`scope-repo-option-${repo.repo_id}`}
                >
                  {repo.display_name ?? repo.repo_id}
                </DropdownMenuItem>
              ))}
              {repositories.length === 0 ? (
                <DropdownMenuLabel className="text-xs text-slate-500">
                  暂无启用的仓库
                </DropdownMenuLabel>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </ConsoleToolbar>
    </>
  );
}
