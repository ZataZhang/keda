import Link from "next/link";

import {
  ConsoleSectionTabs,
  consoleTabClassName,
} from "@/components/layout/console-page";

type RepositoriesWorkspaceTab = "list" | "lifecycle";

interface RepositoriesWorkspaceTabsProps {
  activeTab: RepositoriesWorkspaceTab;
}

/** 渲染仓库列表与执行器模型设置之间的 Repositories 顶层导航。 */
export function RepositoriesWorkspaceTabs({
  activeTab,
}: RepositoriesWorkspaceTabsProps) {
  return (
    <ConsoleSectionTabs
      aria-label="Repositories"
      data-testid="repositories-workspace-tabs"
    >
      <Link
        href="/app/repositories/"
        aria-current={activeTab === "list" ? "page" : undefined}
        data-testid="repositories-tab-list"
        className={consoleTabClassName(activeTab === "list")}
      >
        仓库列表
      </Link>
      <Link
        href="/app/repositories/lifecycle/"
        aria-current={activeTab === "lifecycle" ? "page" : undefined}
        data-testid="repositories-tab-lifecycle"
        className={consoleTabClassName(activeTab === "lifecycle")}
      >
        执行器与模型
      </Link>
    </ConsoleSectionTabs>
  );
}
