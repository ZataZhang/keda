"use client"

import { useEffect, useRef, useState } from "react"

import {
  ConsoleSectionTabs,
  consoleTabClassName,
} from "@/components/layout/console-page"

/** 统计页各区块锚点的 DOM id，页面区块与顶部导航共用，避免两边键名漂移。 */
export const STATS_SECTION_IDS = {
  overview: "stats-section-overview",
  tokenCost: "stats-section-token-cost",
  agentPerformance: "stats-section-agent-performance",
  prdExecution: "stats-section-prd-execution",
} as const

type StatsSectionTab = {
  key: keyof typeof STATS_SECTION_IDS
  label: string
}

/** 顶部 Tab 的展示顺序与文案；与页面区块自上而下的排列一致。 */
const STATS_SECTION_TABS: readonly StatsSectionTab[] = [
  { key: "overview", label: "总览" },
  { key: "tokenCost", label: "Token 成本" },
  { key: "agentPerformance", label: "Agent 表现" },
  { key: "prdExecution", label: "PRD 执行分析" },
]

/**
 * 统计区块顶部导航：点击平滑滚动到对应区块，页面滚动时同步高亮，
 * 方向键切换，窄屏可横向浏览。
 *
 * 桌面监听主内容滚动，窄屏兼容文档滚动；导航沿用公共 CSS sticky，
 * 保持在当前内容区域内，避免固定定位遮住全局侧栏。
 *
 * @returns 顶部 Tab 导航。
 */
export function StatsSectionTabs() {
  const [activeKey, setActiveKey] = useState<StatsSectionTab["key"]>("overview")
  const navRef = useRef<HTMLElement | null>(null)
  const tabRefs = useRef(new Map<StatsSectionTab["key"], HTMLButtonElement>())

  useEffect(() => {
    const navNode = navRef.current
    if (navNode === null) {
      return
    }
    const sections = STATS_SECTION_TABS.map((tab) => ({
      key: tab.key,
      element: document.getElementById(STATS_SECTION_IDS[tab.key]),
    })).filter(
      (entry): entry is { key: StatsSectionTab["key"]; element: HTMLElement } =>
        entry.element !== null
    )
    if (sections.length === 0) {
      return
    }

    let rafHandle = 0

    const update = () => {
      const navBottom = navNode.getBoundingClientRect().bottom
      const main = navNode.closest("main")
      const scrollRoot =
        main && main.scrollHeight > main.clientHeight
          ? main
          : document.documentElement
      const reachedBottom =
        scrollRoot.scrollTop > 0 &&
        scrollRoot.scrollTop + scrollRoot.clientHeight >=
          scrollRoot.scrollHeight - 2
      if (reachedBottom) {
        setActiveKey(sections[sections.length - 1].key)
        return
      }
      let currentKey = sections[0].key
      for (const section of sections) {
        // 用区块自身的 scrollMarginTop（计算值，单位 px）作为阈值，
        // 而非固定像素常量，避免大根字号时阈值与实际留白不匹配。
        const scrollMargin =
          parseFloat(getComputedStyle(section.element).scrollMarginTop) || 0
        if (
          section.element.getBoundingClientRect().top <=
          navBottom + scrollMargin
        ) {
          currentKey = section.key
        }
      }
      setActiveKey(currentKey)
    }

    const requestUpdate = () => {
      window.cancelAnimationFrame(rafHandle)
      rafHandle = window.requestAnimationFrame(update)
    }

    const handleResize = () => {
      requestUpdate()
    }

    update()
    document.addEventListener("scroll", requestUpdate, {
      passive: true,
      capture: true,
    })
    window.addEventListener("resize", handleResize)
    return () => {
      window.cancelAnimationFrame(rafHandle)
      document.removeEventListener("scroll", requestUpdate, true)
      window.removeEventListener("resize", handleResize)
    }
  }, [])

  const scrollToSection = (key: StatsSectionTab["key"]) => {
    setActiveKey(key)
    document
      .getElementById(STATS_SECTION_IDS[key])
      ?.scrollIntoView({ behavior: "smooth", block: "start" })
  }

  const handleKeyDown = (
    event: React.KeyboardEvent<HTMLDivElement>,
    currentKey: StatsSectionTab["key"]
  ) => {
    const currentIndex = STATS_SECTION_TABS.findIndex(
      (tab) => tab.key === currentKey
    )
    let nextIndex: number | null = null
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % STATS_SECTION_TABS.length
    } else if (event.key === "ArrowLeft") {
      nextIndex =
        (currentIndex - 1 + STATS_SECTION_TABS.length) %
        STATS_SECTION_TABS.length
    } else if (event.key === "Home") {
      nextIndex = 0
    } else if (event.key === "End") {
      nextIndex = STATS_SECTION_TABS.length - 1
    }
    if (nextIndex === null) {
      return
    }
    event.preventDefault()
    const nextTab = STATS_SECTION_TABS[nextIndex]
    scrollToSection(nextTab.key)
    tabRefs.current.get(nextTab.key)?.focus()
  }

  return (
    <ConsoleSectionTabs
      ref={navRef}
      aria-label="统计区块导航"
      data-testid="stats-section-tabs"
    >
      <div
        role="tablist"
        aria-label="统计区块导航"
        aria-orientation="horizontal"
        data-testid="stats-section-tablist"
        onKeyDown={(event) => handleKeyDown(event, activeKey)}
        className="flex min-w-0 flex-1 flex-nowrap gap-1 overflow-x-auto overflow-y-hidden"
      >
        {STATS_SECTION_TABS.map((tab) => {
          const isActive = activeKey === tab.key
          return (
            <button
              key={tab.key}
              ref={(buttonNode) => {
                if (buttonNode === null) {
                  tabRefs.current.delete(tab.key)
                  return
                }
                tabRefs.current.set(tab.key, buttonNode)
              }}
              type="button"
              role="tab"
              id={`stats-section-tab-${tab.key}`}
              data-testid={`stats-section-tab-${tab.key}`}
              aria-selected={isActive}
              aria-controls={STATS_SECTION_IDS[tab.key]}
              tabIndex={isActive ? 0 : -1}
              onClick={() => scrollToSection(tab.key)}
              className={consoleTabClassName(isActive)}
            >
              {tab.label}
            </button>
          )
        })}
      </div>
    </ConsoleSectionTabs>
  )
}
