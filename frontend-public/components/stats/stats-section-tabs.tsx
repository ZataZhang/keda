"use client"

import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"

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

/** 区块顶部越过导航下沿该距离（px）内即视为当前区块；需覆盖锚点 scroll-mt-20 的留白。 */
const ACTIVE_SECTION_OFFSET_PX = 24

/**
 * 统计区块顶部导航：点击平滑滚动到对应区块，页面滚动时同步高亮，
 * 方向键切换，窄屏可横向浏览。
 *
 * 页面滚动实际发生在 window（AppShell 的 main 高度随内容增长），
 * 因此导航用 JS 吸顶（越过自然位置后 fixed）而非纯 CSS sticky；
 * 外层占位盒保持原高度，避免吸顶切换时内容跳动。
 *
 * @returns 顶部 Tab 导航。
 */
export function StatsSectionTabs() {
  const [activeKey, setActiveKey] = useState<StatsSectionTab["key"]>("overview")
  const [isPinned, setIsPinned] = useState(false)
  const [shellHeight, setShellHeight] = useState<number | null>(null)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const navRef = useRef<HTMLElement | null>(null)
  const pinnedTopRef = useRef(0)
  const tabRefs = useRef(new Map<StatsSectionTab["key"], HTMLButtonElement>())

  useEffect(() => {
    const shellNode = shellRef.current
    const navNode = navRef.current
    if (shellNode === null || navNode === null) {
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

    const measureLayout = () => {
      setShellHeight(navNode.offsetHeight)
      // 占位 shell 始终处于文档流中（无论导航是否已 fixed 吸顶），
      // 其文档坐标即吸顶阈值。
      pinnedTopRef.current =
        shellNode.getBoundingClientRect().top + window.scrollY
    }

    let rafHandle = 0

    const update = () => {
      const pinnedNow = window.scrollY >= pinnedTopRef.current
      setIsPinned(pinnedNow)
      // 吸顶生效的那一帧导航尚未按 fixed 重排，直接取吸顶后的高度，
      // 避免用旧的 relative 位置算出错误的判定阈值。
      const navBottom = pinnedNow
        ? navNode.offsetHeight
        : navNode.getBoundingClientRect().bottom
      const documentElement = document.documentElement
      const reachedBottom =
        window.scrollY + window.innerHeight >= documentElement.scrollHeight - 2
      if (reachedBottom) {
        setActiveKey(sections[sections.length - 1].key)
        return
      }
      let currentKey = sections[0].key
      for (const section of sections) {
        if (
          section.element.getBoundingClientRect().top <=
          navBottom + ACTIVE_SECTION_OFFSET_PX
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
      measureLayout()
      requestUpdate()
    }

    measureLayout()
    update()
    window.addEventListener("scroll", requestUpdate, { passive: true })
    window.addEventListener("resize", handleResize)
    return () => {
      window.cancelAnimationFrame(rafHandle)
      window.removeEventListener("scroll", requestUpdate)
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
    <div
      ref={shellRef}
      data-testid="stats-section-tabs-shell"
      style={shellHeight === null ? undefined : { height: shellHeight }}
    >
      <nav
        ref={navRef}
        aria-label="统计区块导航"
        data-testid="stats-section-tabs"
        className={cn(
          "border-y border-border/70 bg-background/95 py-2 shadow-sm backdrop-blur",
          isPinned ? "fixed inset-x-0 top-0 z-20" : "relative"
        )}
      >
        <div
          role="tablist"
          aria-label="统计区块导航"
          aria-orientation="horizontal"
          data-testid="stats-section-tablist"
          onKeyDown={(event) => handleKeyDown(event, activeKey)}
          className="flex flex-nowrap gap-1 overflow-x-auto rounded-xl border border-border/70 bg-background p-1"
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
                className={cn(
                  "shrink-0 cursor-pointer rounded-lg px-3 py-1.5 text-sm whitespace-nowrap transition-colors outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40",
                  isActive
                    ? "bg-slate-900 font-medium text-slate-50 dark:bg-slate-100 dark:text-slate-900"
                    : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
                )}
              >
                {tab.label}
              </button>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
