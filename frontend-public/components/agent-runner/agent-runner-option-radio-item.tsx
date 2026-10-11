"use client"

// 生命周期阶段与预设选择器共用单项呈现，避免重复维护选项 JSX。

import { DropdownMenuRadioItem } from "@/components/ui/dropdown-menu"

interface AgentRunnerOptionRadioItemProps {
  /** 候选项值。 */
  value: string
  /** 候选项主标签。 */
  label: string
  /** 主标签下方的补充说明。 */
  description?: string
  /** 候选项 data-testid。 */
  testId: string
}

/**
 * 呈现 Agent Runner 下拉菜单中的一个候选项。
 *
 * @param props - 候选值、标签、补充说明与测试 id。
 * @returns 带可选补充说明的单选菜单项。
 */
export function AgentRunnerOptionRadioItem({
  value,
  label,
  description,
  testId,
}: AgentRunnerOptionRadioItemProps) {
  return (
    <DropdownMenuRadioItem value={value} data-testid={testId}>
      <span className="flex flex-col">
        <span>{label}</span>
        {description ? (
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {description}
          </span>
        ) : null}
      </span>
    </DropdownMenuRadioItem>
  )
}
