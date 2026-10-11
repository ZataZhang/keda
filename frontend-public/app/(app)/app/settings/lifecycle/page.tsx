"use client";

// Settings 生命周期统一设置页路由。
//
// 静态导出下 `useSearchParams` 会把所在 Client 树退化为客户端渲染，因此用
// `<Suspense>` 边界包裹读取查询参数的部分：从 Backlog 仓库齿轮带过来的
// `?scope=repository&repo_id=<id>` 用于预选仓库范围，无参数时默认全局视角。

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { LifecycleSettingsPage } from "@/components/agent-runner/lifecycle-settings-page";
import type { LifecycleAgentScope } from "@/lib/api/types";

/** 读取查询参数并渲染统一设置页。 */
function LifecycleSettingsRoute() {
  const searchParams = useSearchParams();
  const scope: LifecycleAgentScope =
    searchParams.get("scope") === "repository" ? "repository" : "global";
  const repoId = searchParams.get("repo_id") ?? undefined;
  return <LifecycleSettingsPage initialScope={scope} initialRepoId={repoId} />;
}

/** Render the unified lifecycle settings page. */
export default function LifecycleSettingsPageRoute() {
  return (
    <Suspense
      fallback={
        <p className="text-sm text-slate-500" data-testid="lifecycle-settings-loading">
          加载中…
        </p>
      }
    >
      <LifecycleSettingsRoute />
    </Suspense>
  );
}
