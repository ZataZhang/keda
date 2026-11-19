"use client";

import { useRef } from "react";

import {
  ConsoleSectionTabs,
  consoleTabClassName,
} from "@/components/layout/console-page";

export const LIFECYCLE_SETTINGS_TABS = [
  { id: "matrix", label: "生命周期矩阵" },
  { id: "presets", label: "模型预设" },
  { id: "fallback", label: "执行器回退" },
] as const;

export type LifecycleSettingsTab =
  (typeof LIFECYCLE_SETTINGS_TABS)[number]["id"];

interface LifecycleSettingsSectionTabsProps {
  activeTab: LifecycleSettingsTab;
  onTabChange: (tab: LifecycleSettingsTab) => void;
}

/**
 * 渲染生命周期设置区块导航，并在切换时将主内容区滚动到顶部。
 *
 * @param props - 当前选中区块与选择回调。
 * @returns 生命周期矩阵、模型预设和执行器回退的吸顶导航。
 */
export function LifecycleSettingsSectionTabs({
  activeTab,
  onTabChange,
}: LifecycleSettingsSectionTabsProps) {
  const tablistRef = useRef<HTMLElement | null>(null);

  /** 切换选中区块，并将主内容区滚动到顶部。 */
  function handleTabChange(nextTab: LifecycleSettingsTab) {
    if (nextTab === activeTab) {
      return;
    }
    onTabChange(nextTab);
    const pageScroller = tablistRef.current?.closest("main");
    if (pageScroller instanceof HTMLElement) {
      pageScroller.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  return (
    <ConsoleSectionTabs
      ref={tablistRef}
      role="tablist"
      aria-label="生命周期设置区块"
      data-testid="block-nav"
    >
      {LIFECYCLE_SETTINGS_TABS.map((tab) => (
        <button
          key={tab.id}
          id={`lifecycle-settings-tab-${tab.id}`}
          type="button"
          role="tab"
          aria-selected={activeTab === tab.id}
          aria-controls={`lifecycle-settings-panel-${tab.id}`}
          data-testid={`settings-tab-${tab.id}`}
          onClick={() => handleTabChange(tab.id)}
          className={consoleTabClassName(activeTab === tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </ConsoleSectionTabs>
  );
}
