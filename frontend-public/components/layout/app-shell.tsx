import { AppSidebar } from "./app-sidebar"
import { UpdateBanner } from "./update-banner"

interface AppShellProps {
  children: React.ReactNode
}

/** 管理终端桌面布局固定到视口，让导航与内容各自保持可访问。 */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex min-h-svh flex-col md:h-svh md:flex-row md:overflow-hidden">
      <AppSidebar />
      <main className="min-h-0 min-w-0 flex-1 md:overflow-auto">
        <div className="min-w-0 p-4 lg:p-6" data-slot="console-content">
          <UpdateBanner />
          {children}
        </div>
      </main>
    </div>
  )
}
