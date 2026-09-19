"use client"

// PRD 原文详情视图：按需拉取只读端点并以 Markdown 渲染，含加载态与错误态。

import { useEffect, useState } from "react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { PrdAgentOverrideSheet } from "@/components/agent-runner/prd-agent-override-sheet"
import { fetchPrdContent } from "@/lib/api/roadmap"

interface PrdContentViewProps {
  repoId: string
  prdPath: string
  prdTitle: string
  onBack: () => void
}

type PrdContentState =
  | { status: "loading" }
  | { status: "ready"; markdown: string }
  | { status: "error"; message: string }

/**
 * 渲染单个 PRD 的 Markdown 原文。
 *
 * @param props - 仓库标识、PRD 路径与标题，以及返回列表的回调。
 * @returns PRD 详情视图。
 */
export function PrdContentView({ repoId, prdPath, prdTitle, onBack }: PrdContentViewProps) {
  const [contentState, setContentState] = useState<PrdContentState>({ status: "loading" })
  const [reloadToken, setReloadToken] = useState(0)
  const [overrideOpen, setOverrideOpen] = useState(false)

  useEffect(() => {
    let isCancelled = false
    fetchPrdContent(repoId, prdPath)
      .then((markdown) => {
        if (!isCancelled) {
          setContentState({ status: "ready", markdown })
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setContentState({
            status: "error",
            message: error instanceof Error ? error.message : "读取 PRD 原文失败。",
          })
        }
      })
    return () => {
      isCancelled = true
    }
  }, [repoId, prdPath, reloadToken])

  function handleRetry() {
    setContentState({ status: "loading" })
    setReloadToken((currentToken) => currentToken + 1)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={onBack} data-testid="prd-content-back">
          ← 返回列表
        </Button>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" title={prdTitle}>
            {prdTitle}
          </p>
          <p className="truncate text-xs text-slate-500" title={prdPath}>
            {prdPath}
          </p>
        </div>
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          onClick={() => setOverrideOpen(true)}
          data-testid="prd-agent-override-open"
        >
          Agent 覆盖
        </Button>
      </div>

      <PrdAgentOverrideSheet
        repoId={repoId}
        prdPath={prdPath}
        open={overrideOpen}
        onOpenChange={setOverrideOpen}
      />

      {contentState.status === "loading" ? (
        <div className="space-y-3" data-testid="prd-content-loading">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      ) : contentState.status === "error" ? (
        <div
          role="alert"
          data-testid="prd-content-error"
          className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
        >
          <p className="font-medium">读取 PRD 原文失败</p>
          <p className="mt-1">{contentState.message}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={handleRetry}>
            重试
          </Button>
        </div>
      ) : (
        <article className="prd-markdown" data-testid="prd-content-body">
          <Markdown remarkPlugins={[remarkGfm]}>{contentState.markdown}</Markdown>
        </article>
      )}
    </div>
  )
}
