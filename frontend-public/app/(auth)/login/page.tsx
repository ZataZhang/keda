import { Suspense } from "react"
import { LoginGate } from "./login-gate"

/** Render the login page (hosted-aware: bootstrap / password / local no-op). */
export default function LoginPage() {
  return (
    <Suspense
      fallback={<div className="py-8 text-center text-sm text-muted-foreground">加载中…</div>}
    >
      <LoginGate />
    </Suspense>
  )
}
