import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/** 保留原生下拉交互，并复用终端表单的尺寸、主题与焦点语义。 */
export function NativeSelect({
  className,
  ...props
}: ComponentProps<"select">) {
  return (
    <select
      data-slot="native-select"
      className={cn(
        "h-9 max-w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}
