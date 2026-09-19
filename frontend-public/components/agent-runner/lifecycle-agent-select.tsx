"use client";

// 生命周期行的 agent 下拉选择器（全局矩阵与 PRD 覆盖抽屉共用）。
//
// 候选值由调用方经 `buildLifecycleOptions` 传入（只含真实可取的值：已注册 agent
// 以及该阶段允许的 auto / executor），组件只负责渲染与回传选中值。

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/** 下拉框里的一个候选值。 */
export type LifecycleOption = {
  value: string;
  label: string;
  description?: string;
};

interface LifecycleAgentSelectProps {
  /** 生命周期键（仅用于拼接 data-testid）。 */
  lifecycleKey: string;
  /** 候选值列表。 */
  options: LifecycleOption[];
  /** 当前选中值。 */
  value: string;
  /** 选中值变更回调。 */
  onValueChange: (value: string) => void;
  /** 为空时要显示的占位符。 */
  placeholder: string;
  /** data-testid 前缀，区分矩阵与 PRD 抽屉。 */
  testIdPrefix: string;
  /** 是否禁用。 */
  disabled?: boolean;
  /** 追加到触发按钮上的布局类名。 */
  className?: string;
}

/**
 * 生命周期行的 agent 下拉选择器。
 *
 * @param props - 生命周期键、候选值、当前值、变更回调与测试 id 前缀。
 * @returns 单选下拉菜单。
 */
export function LifecycleAgentSelect({
  lifecycleKey,
  options,
  value,
  onValueChange,
  placeholder,
  testIdPrefix,
  disabled = false,
  className,
}: LifecycleAgentSelectProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          className={cn("justify-between", className)}
          data-testid={`${testIdPrefix}-select-${lifecycleKey}`}
        >
          <span className="truncate">{value || placeholder}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-48">
        <DropdownMenuRadioGroup value={value} onValueChange={onValueChange}>
          {options.map((option) => (
            <DropdownMenuRadioItem
              key={option.value}
              value={option.value}
              data-testid={`${testIdPrefix}-option-${lifecycleKey}-${option.value}`}
            >
              <span className="flex flex-col">
                <span>{option.label}</span>
                {option.description ? (
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {option.description}
                  </span>
                ) : null}
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
