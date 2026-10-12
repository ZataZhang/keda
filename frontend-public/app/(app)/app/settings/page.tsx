"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { AgentLabelsEditor } from "@/components/agent-runner/agent-labels-editor"
import { ModelPricingEditor } from "@/components/agent-runner/model-pricing-editor"
import { logout, getCurrentSession, type UserSession } from "@/lib/api/auth"

type SettingsTabId = "agents" | "cost" | "about"

const SETTINGS_TABS: { id: SettingsTabId; label: string }[] = [
  { id: "agents", label: "Agent 管理" },
  { id: "cost", label: "成本估算" },
  { id: "about", label: "关于与账户" },
]

/** Render the settings page. */
export default function SettingsPage() {
  const router = useRouter()
  const [user, setUser] = useState<UserSession | null>(null)
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTabId>("agents")
  const settingsTablistRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    getCurrentSession()
      .then(setUser)
      .catch(() => router.replace("/login"))
  }, [router])

  /** Handle logout and redirect to the home page. */
  async function handleLogout() {
    await logout()
    router.replace("/")
  }

  /** 切换设置区块并将页面滚动位置重置到顶部。 */
  function switchSettingsTab(nextTab: SettingsTabId) {
    if (nextTab === activeSettingsTab) {
      return
    }
    setActiveSettingsTab(nextTab)
    const pageScroller = settingsTablistRef.current?.closest("main")
    if (pageScroller instanceof HTMLElement) {
      pageScroller.scrollTo({ top: 0, behavior: "smooth" })
    }
  }

  if (!user) {
    return (
      <div className="flex h-64 items-center justify-center text-muted-foreground">
        加载中…
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6" data-testid="settings-page">
      <div>
        <h1 className="text-3xl font-bold">设置</h1>
        <p className="text-muted-foreground">
          {user.display_name} · {user.email}
        </p>
      </div>

      <nav
        ref={settingsTablistRef}
        role="tablist"
        aria-label="设置分类"
        className="sticky top-0 z-20 -mx-4 flex gap-1 overflow-x-auto whitespace-nowrap border-b bg-background/95 px-4 pt-2 backdrop-blur md:-mx-8 md:px-8"
        data-testid="settings-section-tabs"
      >
        {SETTINGS_TABS.map((tab) => (
          <button
            key={tab.id}
            id={`settings-tab-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={activeSettingsTab === tab.id}
            aria-controls={`settings-panel-${tab.id}`}
            data-testid={`settings-tab-${tab.id}`}
            onClick={() => switchSettingsTab(tab.id)}
            className={`-mb-px shrink-0 border-b-2 px-3 py-2 text-sm transition-colors ${
              activeSettingsTab === tab.id
                ? "border-foreground font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <section
        id="settings-panel-agents"
        role="tabpanel"
        aria-labelledby="settings-tab-agents"
        tabIndex={0}
        hidden={activeSettingsTab !== "agents"}
        className="rounded-2xl border p-6 focus:outline-none"
        data-testid="settings-agent-management"
      >
        <h2 className="mb-3 text-lg font-semibold">Agent 管理</h2>

        <div className="space-y-6">
          <AgentLabelsEditor />

          <Card data-testid="settings-lifecycle-entry">
            <CardHeader>
              <CardTitle>生命周期、模型与推理深度统一设置</CardTitle>
              <CardDescription>
                在同一页面切换全局 / 仓库范围，查看九阶段最终生效的
                Agent、模型、推理深度与来源，
                编辑命名预设并绑定阶段，或为执行器回退候选选择匹配的预设。原先分散在本页与
                Backlog 的生命周期矩阵、回退顺序已合并到此页面。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button asChild size="sm">
                <Link href="/app/settings/lifecycle/">打开统一设置页</Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </section>

      <section
        id="settings-panel-cost"
        role="tabpanel"
        aria-labelledby="settings-tab-cost"
        tabIndex={0}
        hidden={activeSettingsTab !== "cost"}
        className="rounded-2xl border p-6 focus:outline-none"
        data-testid="settings-cost-management"
      >
        <div className="mb-4">
          <h2 className="text-lg font-semibold">成本估算</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            价格只影响统计页的本机估算，不会更改 Agent 配置或供应商账单。
          </p>
        </div>
        <ModelPricingEditor />
      </section>

      <section
        id="settings-panel-about"
        role="tabpanel"
        aria-labelledby="settings-tab-about"
        tabIndex={0}
        hidden={activeSettingsTab !== "about"}
        className="space-y-4 focus:outline-none"
        data-testid="settings-about-account"
      >
        <div className="rounded-2xl border bg-muted/30 p-6">
          <h2 className="mb-2 text-lg font-semibold">关于 KedaCode 管理终端</h2>
          <p className="text-sm text-muted-foreground">
            这是 KedaCode 内置的 Agent Runner 管理终端（本机单用户模式，仅监听
            127.0.0.1）。仓库队列、托管进程与 backlog 数据均来自本机后端
            API；runner 的行为由各仓库的 config.toml 与 .kedacode.toml 决定。
            退出后可通过命令行重新执行 <code>kc console</code> 打开。
          </p>
        </div>
        <Button variant="destructive" onClick={handleLogout}>
          退出登录
        </Button>
      </section>
    </div>
  )
}
