"use client"

import Link from "next/link"
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
import { AgentLabelsEditor } from "@/components/agent-runner/agent-labels-editor"
import { logout, getCurrentSession, type UserSession } from "@/lib/api/auth"

/** Render the settings page. */
export default function SettingsPage() {
  const router = useRouter()
  const [user, setUser] = useState<UserSession | null>(null)

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

        <div className="space-y-6">
          <AgentLabelsEditor />

          <Card data-testid="settings-lifecycle-entry">
            <CardHeader>
              <CardTitle>生命周期、模型与推理深度统一设置</CardTitle>
              <CardDescription>
                在同一页面切换全局 / 仓库范围，查看九阶段最终生效的 Agent、模型、推理深度与来源，
                编辑命名预设并绑定阶段，或为执行器回退候选选择匹配的预设。原先分散在本页与 Backlog
                的生命周期矩阵、回退顺序已合并到此页面。
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
    </div>
  )
}
