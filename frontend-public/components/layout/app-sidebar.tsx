"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { CONSOLE_NAV_COLLAPSED_KEY, usePersistedBoolean } from "@/lib/console-ui-prefs"
import {
  Activity,
  BarChart3,
  GitBranch,
  LayoutDashboard,
  Lightbulb,
  Map,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
} from "lucide-react"

// 导航项对齐 keda/frontend 吸收进来的 agent-runner 监控页面；
// settings 保留以承载模板原有的登出入口。
const navItems = [
  { href: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/app/processes", label: "Processes", icon: Activity },
  { href: "/app/repositories", label: "Repositories", icon: GitBranch },
  { href: "/app/stats", label: "Stats", icon: BarChart3 },
  { href: "/app/backlog", label: "Backlog", icon: Map },
  { href: "/app/ideas", label: "Ideas", icon: Lightbulb },
  { href: "/app/settings", label: "Settings", icon: Settings },
]

/** Sidebar navigation for authenticated pages. */
export function AppSidebar() {
  const pathname = usePathname()
  // 收起偏好跨页面、跨刷新记忆：纯展示选择，不影响路由与数据。
  const [collapsed, setCollapsed] = usePersistedBoolean(CONSOLE_NAV_COLLAPSED_KEY)

  return (
    <aside
      className={cn(
        // 窄屏（<md）侧栏改为顶部整宽导航条，把横向空间让给内容；md 起恢复左侧栏。
        "flex flex-col border-b bg-sidebar transition-[width] duration-200 md:border-r md:border-b-0",
        collapsed ? "w-14" : "w-full md:w-64"
      )}
    >
      <div
        className={cn(
          "flex h-14 items-center border-b",
          collapsed ? "justify-center px-2" : "gap-2 px-4"
        )}
      >
        {collapsed ? null : (
          <>
            <span className="size-6 rounded-md bg-primary" />
            <span className="font-semibold text-sidebar-foreground">KedaCode</span>
            <div className="flex-1" />
          </>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "展开导航栏" : "收起导航栏"}
          title={collapsed ? "展开导航栏" : "收起导航栏"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
      </div>
      <nav className="flex-1 p-3">
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                // 收起态的图标对屏幕阅读器仍要可辨：title 同时充当原生悬停提示。
                title={collapsed ? item.label : undefined}
                className={cn(
                  "flex items-center rounded-lg py-2 text-sm font-medium transition-colors",
                  collapsed ? "justify-center px-0" : "gap-3 px-3",
                  pathname === item.href || pathname?.startsWith(`${item.href}/`)
                    ? "bg-sidebar-primary text-sidebar-primary-foreground"
                    : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                )}
              >
                <item.icon className="size-4" />
                {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  )
}
