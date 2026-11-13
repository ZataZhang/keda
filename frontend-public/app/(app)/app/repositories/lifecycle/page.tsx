"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { LifecycleSettingsPage } from "@/components/agent-runner/lifecycle-settings-page";
import { RepositoriesWorkspaceTabs } from "@/components/agent-runner/repositories-workspace-tabs";
import type { LifecycleAgentScope } from "@/lib/api/types";

/** 读取 Repositories 生命周期入口的范围参数并渲染统一设置页。 */
function RepositoriesLifecycleRoute() {
  const searchParams = useSearchParams();
  const scope: LifecycleAgentScope =
    searchParams.get("scope") === "repository" ? "repository" : "global";
  const repoId = searchParams.get("repo_id") ?? undefined;

  return (
    <LifecycleSettingsPage
      initialScope={scope}
      initialRepoId={repoId}
      workspaceNavigation={<RepositoriesWorkspaceTabs activeTab="lifecycle" />}
    />
  );
}

/** 渲染 Repositories 工作区内的生命周期设置路由。 */
export default function RepositoriesLifecyclePage() {
  return (
    <Suspense
      fallback={
        <p className="text-sm text-slate-500" data-testid="lifecycle-settings-loading">
          加载中…
        </p>
      }
    >
      <RepositoriesLifecycleRoute />
    </Suspense>
  );
}
