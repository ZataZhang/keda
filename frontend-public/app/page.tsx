"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect } from "react"

/**
 * 站点根入口：`iar console` 打开的首页直接落到 Dashboard。
 *
 * 静态导出（output: "export"）下没有服务端重定向能力，这里用客户端
 * 跳转实现"打开即见仓库队列"，并保留一个真实链接兜底（禁用 JS 或
 * 跳转前的瞬间也不是空白页）。
 */
export default function HomePage() {
  const router = useRouter()

  useEffect(() => {
    router.replace("/app/dashboard/")
  }, [router])

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-4 text-muted-foreground">
      <p>正在打开 iar 管理终端…</p>
      <Link className="text-primary underline" href="/app/dashboard/">
        若未自动跳转，点此进入 Dashboard
      </Link>
    </div>
  )
}
