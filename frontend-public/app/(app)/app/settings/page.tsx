"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { AgentFallbackOrderEditor } from "@/components/agent-runner/agent-fallback-order-editor"
import { AgentLabelsEditor } from "@/components/agent-runner/agent-labels-editor"
import { LifecycleAgentMatrix } from "@/components/agent-runner/lifecycle-agent-matrix"
import { cn } from "@/lib/utils"
import { logout, getCurrentSession, type UserSession } from "@/lib/api/auth"

/** 「Agent 管理」区块的两个标签页。 */
type AgentSettingsTab = "labels" | "lifecycles"

/** Render the settings page. */
export default function SettingsPage() {
  const router = useRouter()
  const [user, setUser] = useState<UserSession | null>(null)
  const [activeTab, setActiveTab] = useState<AgentSettingsTab>("labels")

  useEffect(() => {
    getCurrentSession().then(setUser).catch(() => router.replace("/login"))
  }, [router])

  /** Handle logout and redirect to the home page. */
  async function handleLogout() {
    await logout()
    router.replace("/")
  }

  if (!user) {
    return (
      <div className="flex h-64 items-center justify-center text-muted-foreground">
        加载中…
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-3xl font-bold">设置</h1>
        <p className="text-muted-foreground">
          {user.display_name} · {user.email}
        </p>
      </div>

      <section className="rounded-2xl border p-6" data-testid="settings-agent-management">
        <h2 className="mb-3 text-lg font-semibold">Agent 管理</h2>

        <div className="sticky top-0 z-10 mb-4 flex gap-6 border-b bg-background">
          <button
            type="button"
            onClick={() => setActiveTab("labels")}
            data-testid="settings-agent-tab-labels"
            className={cn(
              "-mb-px border-b-2 px-1 py-2 text-sm transition-colors",
              activeTab === "labels"
                ? "border-slate-900 font-medium text-slate-900 dark:border-slate-50 dark:text-slate-50"
                : "border-transparent text-slate-500 hover:text-slate-900 dark:hover:text-slate-50",
            )}
          >
            Agent 标签设置
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("lifecycles")}
            data-testid="settings-agent-tab-lifecycles"
            className={cn(
              "-mb-px border-b-2 px-1 py-2 text-sm transition-colors",
              activeTab === "lifecycles"
                ? "border-slate-900 font-medium text-slate-900 dark:border-slate-50 dark:text-slate-50"
                : "border-transparent text-slate-500 hover:text-slate-900 dark:hover:text-slate-50",
            )}
          >
            生命周期 Agent 设置
          </button>
        </div>

        {activeTab === "labels" ? (
          <AgentLabelsEditor />
        ) : (
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>生命周期 Agent 矩阵 · 全局</CardTitle>
                <CardDescription>
                  决定流水线九个生命周期阶段各自使用哪个 agent；写回机器级
                  config.toml 的 [agent_runner.lifecycle_agents]。
                </CardDescription>
              </CardHeader>
              <CardContent>
                <LifecycleAgentMatrix
                  scope="global"
                  testIdPrefix="global-lifecycle-matrix"
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>agent 回退顺序</CardTitle>
                <CardDescription>
                  Issue 执行阶段的跨 agent 回退顺序与最大切换次数。
                </CardDescription>
              </CardHeader>
              <CardContent>
                <AgentFallbackOrderEditor />
              </CardContent>
            </Card>
          </div>
        )}
      </section>

      <div className="rounded-2xl border bg-muted/30 p-6">
        <h2 className="mb-2 text-lg font-semibold">关于 iar 管理终端</h2>
        <p className="text-sm text-muted-foreground">
          这是 iar 内置的 Agent Runner 管理终端（本机单用户模式，仅监听
          127.0.0.1）。仓库队列、托管进程与 roadmap 数据均来自本机后端
          API；runner 的行为由各仓库的 config.toml 与 .iar.toml 决定。
          退出后可通过命令行重新执行 <code>iar console</code> 打开。
        </p>
      </div>
      <Button variant="destructive" onClick={handleLogout}>
        退出登录
      </Button>
    </div>
  )
}
