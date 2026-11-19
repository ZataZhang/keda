"use client"

import { useEffect, useState } from "react"
import { LoginForm } from "./login-form"
import { HostedBootstrapForm } from "./hosted-bootstrap-form"
import { HostedLoginForm } from "./hosted-login-form"
import { getHostedStatus, type HostedStatus } from "@/lib/api/hostedConsole"

/**
 * 登录页模式闸门：探测后端是否处于托管模式，据此渲染本机、引导或托管登录表单。
 *
 * 本机模式（后端未挂载 hosted 路由）保持原有 `identifier+password` no-op 表单；
 * 托管未引导时进入首次引导；已引导后进入口令登录。三者共用同一路由，避免为
 * 本机部署引入任何新对外接口。
 */
export function LoginGate() {
  const [status, setStatus] = useState<HostedStatus | null>(null)

  useEffect(() => {
    let active = true
    getHostedStatus().then((value) => {
      if (active) setStatus(value)
    })
    return () => {
      active = false
    }
  }, [])

  if (status === null) {
    return <div className="py-8 text-center text-sm text-muted-foreground">加载中…</div>
  }

  if (!status.enabled) {
    return <LoginForm />
  }

  return status.bootstrapped ? <HostedLoginForm /> : <HostedBootstrapForm />
}
