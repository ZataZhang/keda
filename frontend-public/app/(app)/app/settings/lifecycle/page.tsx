"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

const REPOSITORIES_LIFECYCLE_PATH = "/app/repositories/lifecycle/";

/** 保留旧生命周期地址并将其查询参数带到 Repositories 规范入口。 */
function LegacyLifecycleSettingsRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryString = searchParams.toString();

  useEffect(() => {
    const targetPath = queryString
      ? `${REPOSITORIES_LIFECYCLE_PATH}?${queryString}`
      : REPOSITORIES_LIFECYCLE_PATH;
    router.replace(targetPath);
  }, [queryString, router]);

  return (
    <p className="text-sm text-slate-500" data-testid="lifecycle-settings-redirecting">
      正在打开 Repositories 的执行器与模型设置…
    </p>
  );
}

/** Redirect saved lifecycle settings links to the Repositories workspace. */
export default function LegacyLifecycleSettingsPage() {
  return (
    <Suspense
      fallback={
        <p className="text-sm text-slate-500" data-testid="lifecycle-settings-loading">
          加载中…
        </p>
      }
    >
      <LegacyLifecycleSettingsRedirect />
    </Suspense>
  );
}
