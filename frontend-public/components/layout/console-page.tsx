import type { ComponentProps, ReactNode } from "react"

import { cn } from "@/lib/utils"

/** 页面只负责内容节奏，外边距由 AppShell 统一提供。 */
export function ConsolePage({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="console-page"
      className={cn("flex min-w-0 flex-col gap-4", className)}
      {...props}
    />
  )
}

interface ConsolePageHeaderProps {
  title: string
  description?: ReactNode
}

/** 管理终端各页面共用的标题与说明，保持相同字号和对齐起点。 */
export function ConsolePageHeader({
  title,
  description,
}: ConsolePageHeaderProps) {
  return (
    <header data-slot="console-page-header" className="min-w-0 shrink-0">
      <h1 className="text-xl leading-7 font-semibold text-foreground">
        {title}
      </h1>
      {description ? (
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {description}
        </p>
      ) : null}
    </header>
  )
}

/** 统一筛选、仓库选择与页面操作的排列，窄屏允许换行。 */
export function ConsoleToolbar({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="console-toolbar"
      className={cn("flex min-w-0 flex-wrap items-center gap-2", className)}
      {...props}
    />
  )
}

/** 区块导航跟随主内容吸顶，不覆盖全局侧栏。 */
export function ConsoleSectionTabs({
  className,
  ...props
}: ComponentProps<"nav">) {
  return (
    <nav
      data-slot="console-section-tabs"
      className={cn(
        "sticky top-0 z-20 -mx-4 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-border bg-background/95 px-4 pt-2 whitespace-nowrap backdrop-blur lg:-mx-6 lg:px-6",
        className
      )}
      {...props}
    />
  )
}

/** 各页面的区块 Tab 使用一致的选中、悬停和键盘焦点样式。 */
export function consoleTabClassName(active: boolean): string {
  return cn(
    "-mb-px shrink-0 border-b-2 px-3 py-2 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
    active
      ? "border-primary font-medium text-primary"
      : "border-transparent text-muted-foreground hover:text-foreground"
  )
}
