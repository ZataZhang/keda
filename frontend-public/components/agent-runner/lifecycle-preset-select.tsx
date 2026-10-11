"use client";

// 生命周期设置页共用的「预设单选下拉」：阶段绑定与回退候选绑定都用它，
// 候选项可包含本次新建、尚未落盘的预设（以 isNew 标注）。

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface PresetOption {
  value: string;
  label: string;
  isNew?: boolean;
}

export interface PresetSelectProps {
  /** 当前选中值（空串表示未绑定）。 */
  value: string;
  /** 候选预设（含本次新建、尚未落盘的预设）。 */
  options: PresetOption[];
  /** 选中值变更回调。 */
  onChange: (value: string) => void;
  /** data-testid 前缀。 */
  testId: string;
  /** 未绑定时的占位文案。 */
  placeholder?: string;
  /** 是否允许清空（绑定选择器用）。 */
  allowClear?: boolean;
}

/**
 * 预设单选下拉（绑定阶段与回退候选共用）。
 *
 * @param props - 当前值、候选、回调与测试 id。
 * @returns 下拉触发按钮 + 预设候选菜单。
 */
export function PresetSelect({
  value,
  options,
  onChange,
  testId,
  placeholder = "未绑定",
  allowClear = false,
}: PresetSelectProps) {
  const currentLabel = options.find((option) => option.value === value)?.label;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-full justify-between"
          data-testid={testId}
        >
          <span className="truncate">{currentLabel || placeholder}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-48">
        <DropdownMenuRadioGroup value={value} onValueChange={onChange}>
          {allowClear ? (
            <DropdownMenuRadioItem
              value=""
              data-testid={`${testId}-option-clear`}
            >
              {placeholder}
            </DropdownMenuRadioItem>
          ) : null}
          {options.map((option) => (
            <DropdownMenuRadioItem
              key={option.value}
              value={option.value}
              data-testid={`${testId}-option-${option.value}`}
            >
              <span className="flex flex-col">
                <span>{option.label}</span>
                {option.isNew ? (
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    本次新建（与绑定一同保存）
                  </span>
                ) : null}
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        {options.length === 0 ? (
          <DropdownMenuLabel className="text-xs text-slate-500">
            暂无可选预设
          </DropdownMenuLabel>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
