import { SessionProvider } from "@/components/auth/SessionProvider";

/**
 * (companion) 区根布局：桌面小窗用的无侧栏壳子。
 *
 * 与 (app) 区的差别只有一处——不套 `AppShell`，因为原生窗口只有几百像素宽，
 * 侧栏会把任务行挤掉。这里也**不做未登录跳转**：小窗是一个只读监控面，
 * 把窗口刷成登录页会让用户以为「进度没了」，而本机 Console 本来就是
 * 单操作者的回环信任边界（见 `backend/api/routes/local_auth.py`）。
 */
export default function CompanionLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <div className="min-h-svh bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        {children}
      </div>
    </SessionProvider>
  );
}
