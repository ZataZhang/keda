"use client";

// 仓库级生命周期 Agent 矩阵抽屉。
//
// 从 Roadmap 仓库行的齿轮按钮打开，编辑的是该仓库 `.iar.toml` 里的
// `[agent_runner.lifecycle_agents]` 声明（仓库层），不影响全局 config.toml。

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { LifecycleAgentMatrix } from "@/components/agent-runner/lifecycle-agent-matrix";

interface RepositoryAgentMatrixSheetProps {
  /** 目标仓库 id。 */
  repoId: string;
  /** 仓库展示名（用于标题）。 */
  repoLabel: string;
  /** 抽屉开关状态。 */
  open: boolean;
  /** 开关状态变更回调。 */
  onOpenChange: (open: boolean) => void;
}

/**
 * 仓库级生命周期 Agent 矩阵抽屉。
 *
 * @param props - 仓库 id / 展示名与抽屉开关状态。
 * @returns 右侧抽屉，内含仓库层矩阵编辑器。
 */
export function RepositoryAgentMatrixSheet({
  repoId,
  repoLabel,
  open,
  onOpenChange,
}: RepositoryAgentMatrixSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>仓库生命周期 Agent 矩阵</SheetTitle>
          <SheetDescription>
            {repoLabel} · 写回该仓库的 .iar.toml（仓库层）；未声明的键继续跟随全局
            config.toml。
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto px-4 pb-4">
          <LifecycleAgentMatrix
            scope="repository"
            repoId={repoId}
            testIdPrefix="repo-lifecycle-matrix"
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
