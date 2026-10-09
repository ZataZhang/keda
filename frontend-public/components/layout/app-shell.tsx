import { AppSidebar } from "./app-sidebar"
import { UpdateBanner } from "./update-banner"

interface AppShellProps {
  children: React.ReactNode
}

/** Main application shell wrapping sidebar and content. */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex min-h-svh flex-col md:flex-row">
      <AppSidebar />
      <main className="flex-1 overflow-auto">
        <div className="container mx-auto p-4 md:p-8">
          <UpdateBanner />
          {children}
        </div>
      </main>
    </div>
  )
}
