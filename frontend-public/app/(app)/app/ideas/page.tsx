"use client"
/* eslint-disable react-hooks/set-state-in-effect */

// Idea Inbox 页面：按收件箱、摘要、PRD 草稿分开展示，避免长内容同时挤在多列中。

import { useCallback, useEffect, useState } from "react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  ConsolePage,
  ConsolePageHeader,
  ConsoleSectionTabs,
  ConsoleToolbar,
  consoleTabClassName,
} from "@/components/layout/console-page"
import { confirmAction } from "@/lib/confirm-action"
import { useRepositorySelection } from "@/lib/console-repository-selection"
import {
  appendIdea,
  approvePrdDraft,
  createPrdDraft,
  fetchIdeaInboxMetadata,
  fetchIdeaInboxSnapshot,
  refreshIdeaSummary,
  updateIdeaStatus,
} from "@/lib/api/ideaInbox"
import type {
  IdeaEntry,
  IdeaInboxMetadata,
  IdeaInboxSnapshot,
  IdeaStatus,
  PrdDraftSummary,
} from "@/lib/api/types"

const POLL_INTERVAL_MS = 30000
const IDEA_STATUS_LABELS: Record<IdeaStatus, string> = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
  abandoned: "已放弃",
}

type IdeasPageView = "inbox" | "summary" | "drafts"

/** 展示想法收件箱、AI 总结和 PRD 草稿。 */
export default function IdeasPage() {
  const [metadata, setMetadata] = useState<IdeaInboxMetadata | null>(null)
  const {
    enabledRepositories: enabledRepos,
    selectedRepoId,
    selectRepoId,
  } = useRepositorySelection()
  const [snapshot, setSnapshot] = useState<IdeaInboxSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [activeView, setActiveView] = useState<IdeasPageView>("inbox")
  const [selectedIdeaId, setSelectedIdeaId] = useState<string | null>(null)
  const [selectedIdeaIds, setSelectedIdeaIds] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState("")
  const [captureOpen, setCaptureOpen] = useState(false)
  const [summaryEditing, setSummaryEditing] = useState(false)
  const [newIdeaText, setNewIdeaText] = useState("")
  const [newIdeaAuthor, setNewIdeaAuthor] = useState("operator")
  const [summaryDraft, setSummaryDraft] = useState("")
  const [priority, setPriority] = useState("P2")
  const [prdType, setPrdType] = useState("FEAT")
  const [submittingIdea, setSubmittingIdea] = useState(false)
  const [savingSummary, setSavingSummary] = useState(false)
  const [creatingDraft, setCreatingDraft] = useState(false)
  const [approvingPath, setApprovingPath] = useState<string | null>(null)
  const [updatingEntryId, setUpdatingEntryId] = useState<string | null>(null)

  const loadMetadata = useCallback(async () => {
    try {
      setMetadata(await fetchIdeaInboxMetadata())
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "无法加载 idea inbox 元数据。"
      )
    }
  }, [])

  const loadSnapshot = useCallback(async (repoId: string) => {
    if (!repoId) {
      setSnapshot(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      setSnapshot(await fetchIdeaInboxSnapshot(repoId))
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "无法加载 inbox 快照。"
      )
      setSnapshot(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadMetadata()
  }, [loadMetadata])

  useEffect(() => {
    setSnapshot(null)
    setSelectedIdeaId(null)
    setSelectedIdeaIds(new Set())
    setSearchQuery("")
    setSummaryEditing(false)
    setCaptureOpen(false)
  }, [selectedRepoId])

  useEffect(() => {
    if (!selectedRepoId) {
      return
    }
    void loadSnapshot(selectedRepoId)
    const timer = setInterval(
      () => void loadSnapshot(selectedRepoId),
      POLL_INTERVAL_MS
    )
    return () => clearInterval(timer)
  }, [loadSnapshot, selectedRepoId])

  /** 将记录框里的原话追加到当前仓库。 */
  async function handleAppendIdea() {
    if (!selectedRepoId) {
      toast.warning("请先选择仓库。")
      return
    }
    if (!newIdeaText.trim()) {
      toast.warning("想法原文不能为空。")
      return
    }
    setSubmittingIdea(true)
    try {
      const result = await appendIdea({
        repoId: selectedRepoId,
        text: newIdeaText,
        author: newIdeaAuthor || "operator",
      })
      setNewIdeaText("")
      setCaptureOpen(false)
      setActiveView("inbox")
      setSelectedIdeaId(result.entry.entry_id)
      setSelectedIdeaIds(new Set())
      toast.success("已追加到想法原话流。")
      await loadSnapshot(selectedRepoId)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "追加想法失败。")
    } finally {
      setSubmittingIdea(false)
    }
  }

  /** 更新想法生命周期，并从草稿选择集中移除该条目。 */
  async function handleUpdateIdeaStatus(
    entry: IdeaEntry,
    nextStatus: "pending" | "completed" | "abandoned"
  ) {
    if (!selectedRepoId) {
      return
    }
    setUpdatingEntryId(entry.entry_id)
    try {
      await updateIdeaStatus({
        repoId: selectedRepoId,
        entryId: entry.entry_id,
        status: nextStatus,
      })
      setSelectedIdeaIds((current) => {
        const next = new Set(current)
        next.delete(entry.entry_id)
        return next
      })
      const statusMessages = {
        pending: "已恢复想法跟踪。",
        completed: "已将想法标记为已完成。",
        abandoned: "已将想法标记为放弃。",
      }
      toast.success(statusMessages[nextStatus])
      await loadSnapshot(selectedRepoId)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新想法状态失败。")
    } finally {
      setUpdatingEntryId(null)
    }
  }

  /** 保存用户编辑的摘要文本，不触碰原话日志。 */
  async function handleSaveSummary() {
    if (!selectedRepoId) {
      return
    }
    if (!summaryDraft.trim()) {
      toast.warning("摘要内容不能为空。")
      return
    }
    setSavingSummary(true)
    try {
      await refreshIdeaSummary({
        repoId: selectedRepoId,
        summaryText: summaryDraft,
        sourceLabel: "manual",
      })
      setSummaryEditing(false)
      toast.success("已保存 summary.md；原话日志未改动。")
      await loadSnapshot(selectedRepoId)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存摘要失败。")
    } finally {
      setSavingSummary(false)
    }
  }

  /** 用勾选的待处理想法创建 PRD 草稿。 */
  async function handleCreateDraft() {
    if (!selectedRepoId) {
      return
    }
    if (selectedIdeaIds.size === 0) {
      toast.warning("请先勾选至少一条想法。")
      return
    }
    setCreatingDraft(true)
    try {
      await createPrdDraft({
        repoId: selectedRepoId,
        ideaRefs: Array.from(selectedIdeaIds),
        priority,
        prdType,
      })
      setSelectedIdeaIds(new Set())
      toast.success("已生成 PRD 草稿。")
      setActiveView("drafts")
      await loadSnapshot(selectedRepoId)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "生成草稿失败。")
    } finally {
      setCreatingDraft(false)
    }
  }

  /** 将用户确认的草稿复制到 tasks/pending/。 */
  async function handleApproveDraft(draft: PrdDraftSummary) {
    if (!selectedRepoId) {
      return
    }
    const confirmed = await confirmAction(
      `确认将草稿「${draft.title}」写入 tasks/pending/？\n\n草稿 ID: ${draft.metadata.draft_id}\n目标文件: tasks/pending/<PRIORITY>-<TYPE>-<draft_id>-<slug>.md`
    )
    if (!confirmed) {
      return
    }
    setApprovingPath(draft.draft_path)
    try {
      const result = await approvePrdDraft({
        repoId: selectedRepoId,
        draftPath: draft.draft_path,
        priority: draft.metadata.priority,
        prdType: draft.metadata.prd_type,
      })
      toast.success(`草稿已确认，目标：${result.pending_path}`)
      await loadSnapshot(selectedRepoId)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "确认草稿失败。")
    } finally {
      setApprovingPath(null)
    }
  }

  /** 切换想法的草稿选择状态。 */
  function toggleIdeaSelection(entry: IdeaEntry) {
    setSelectedIdeaIds((current) => {
      const next = new Set(current)
      if (next.has(entry.entry_id)) {
        next.delete(entry.entry_id)
      } else {
        next.add(entry.entry_id)
      }
      return next
    })
  }

  const entries = snapshot?.entries ?? []
  const activeIdeaCount = entries.filter(
    (entry) => entry.status === "pending" || entry.status === "in_progress"
  ).length
  const historyIdeaCount = entries.length - activeIdeaCount
  const visibleIdeaEntries = entries
    .filter(
      (entry) =>
        showHistory ||
        entry.status === "pending" ||
        entry.status === "in_progress"
    )
    .filter((entry) =>
      entry.text
        .toLocaleLowerCase()
        .includes(searchQuery.trim().toLocaleLowerCase())
    )
    .reverse()
  const selectedIdea =
    visibleIdeaEntries.find((entry) => entry.entry_id === selectedIdeaId) ??
    visibleIdeaEntries[0] ??
    null
  const draftCount = snapshot?.drafts.length ?? 0
  const pendingReviewDraftCount =
    snapshot?.drafts.filter(
      (draft) => draft.metadata.status === "pending-review"
    ).length ?? 0

  return (
    <ConsolePage>
      <ConsolePageHeader
        title="Idea Inbox"
        description={
          <>
            收集原始想法，按需查看总结或生成 PRD
            草稿；原话记录只追加，不会被摘要编辑覆盖。
          </>
        }
      />

      <ConsoleToolbar className="justify-between">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              仓库：{selectedRepoId || "未选择"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuRadioGroup
              value={selectedRepoId}
              onValueChange={selectRepoId}
            >
              {enabledRepos.length === 0 && (
                <DropdownMenuRadioItem value="" disabled>
                  （无可用仓库）
                </DropdownMenuRadioItem>
              )}
              {enabledRepos.map((entry) => (
                <DropdownMenuRadioItem
                  key={entry.repo_id}
                  value={entry.repo_id}
                >
                  {entry.display_name || entry.repo_id}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <p className="text-xs text-muted-foreground">
          当前想法 {activeIdeaCount} 条 · 历史 {historyIdeaCount} 条 · 草稿待审{" "}
          {pendingReviewDraftCount} 份
        </p>
      </ConsoleToolbar>

      <ConsoleSectionTabs aria-label="Idea Inbox 页面视图">
        <button
          type="button"
          aria-current={activeView === "inbox" ? "page" : undefined}
          className={consoleTabClassName(activeView === "inbox")}
          onClick={() => setActiveView("inbox")}
        >
          收件箱 <span className="ml-1 text-xs">{activeIdeaCount}</span>
        </button>
        <button
          type="button"
          aria-current={activeView === "summary" ? "page" : undefined}
          className={consoleTabClassName(activeView === "summary")}
          onClick={() => setActiveView("summary")}
        >
          AI 总结
        </button>
        <button
          type="button"
          aria-current={activeView === "drafts" ? "page" : undefined}
          className={consoleTabClassName(activeView === "drafts")}
          onClick={() => setActiveView("drafts")}
        >
          PRD 草稿 <span className="ml-1 text-xs">{draftCount}</span>
        </button>
      </ConsoleSectionTabs>

      {activeView === "inbox" && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3">
            <div>
              <p className="text-sm font-medium">记录一条想法</p>
              <p className="text-xs text-muted-foreground">
                原话会保存在 ideas.md；稍后可从列表选择并生成 PRD 草稿。
              </p>
            </div>
            <Button
              size="sm"
              variant={captureOpen ? "outline" : "default"}
              onClick={() => setCaptureOpen((current) => !current)}
              disabled={!selectedRepoId}
            >
              {captureOpen ? "收起记录框" : "记录新想法"}
            </Button>
          </div>

          {captureOpen && (
            <Card>
              <CardHeader>
                <CardTitle>新想法原文</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_13rem]">
                <Textarea
                  aria-label="新想法原文"
                  className="min-h-28"
                  placeholder="写下你希望保留的原话…"
                  value={newIdeaText}
                  onChange={(event) => setNewIdeaText(event.target.value)}
                />
                <div className="flex flex-col gap-2">
                  <label
                    htmlFor="idea-author"
                    className="text-xs font-medium text-muted-foreground"
                  >
                    记录人
                  </label>
                  <Input
                    id="idea-author"
                    value={newIdeaAuthor}
                    onChange={(event) => setNewIdeaAuthor(event.target.value)}
                    placeholder="operator"
                  />
                  <Button
                    className="mt-auto"
                    size="sm"
                    onClick={() => void handleAppendIdea()}
                    disabled={submittingIdea || !selectedRepoId}
                  >
                    {submittingIdea ? "追加中…" : "追加到 inbox"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          <ConsoleToolbar className="justify-between">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <Input
                aria-label="搜索想法"
                className="h-8 w-full sm:max-w-sm"
                placeholder="搜索想法原文…"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowHistory((current) => !current)}
                disabled={!snapshot || entries.length === 0}
              >
                {showHistory ? "隐藏历史" : `显示历史 ${historyIdeaCount}`}
              </Button>
            </div>
            {selectedIdeaIds.size > 0 && (
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/40 px-2 py-1.5">
                <span className="text-xs text-muted-foreground">
                  已选 {selectedIdeaIds.size} 条
                </span>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm">
                      {priority}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start">
                    <DropdownMenuRadioGroup
                      value={priority}
                      onValueChange={setPriority}
                    >
                      {(metadata?.priorities ?? ["P0", "P1", "P2", "P3"]).map(
                        (value) => (
                          <DropdownMenuRadioItem key={value} value={value}>
                            {value}
                          </DropdownMenuRadioItem>
                        )
                      )}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm">
                      {prdType}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start">
                    <DropdownMenuRadioGroup
                      value={prdType}
                      onValueChange={setPrdType}
                    >
                      {(metadata?.prd_types ?? ["FEAT", "BUG", "CHORE"]).map(
                        (value) => (
                          <DropdownMenuRadioItem key={value} value={value}>
                            {value}
                          </DropdownMenuRadioItem>
                        )
                      )}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button
                  size="sm"
                  onClick={() => void handleCreateDraft()}
                  disabled={creatingDraft || !selectedRepoId}
                >
                  {creatingDraft ? "生成中…" : "生成 PRD 草稿"}
                </Button>
              </div>
            )}
          </ConsoleToolbar>

          <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(20rem,0.9fr)_minmax(0,1.1fr)]">
            <Card className="min-h-[28rem] gap-4">
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>想法列表</CardTitle>
                <span className="text-xs text-muted-foreground">
                  {visibleIdeaEntries.length} 条
                </span>
              </CardHeader>
              <CardContent className="min-h-0 flex-1">
                {loading && !snapshot ? (
                  <div className="space-y-2">
                    <Skeleton className="h-20" />
                    <Skeleton className="h-20" />
                    <Skeleton className="h-20" />
                  </div>
                ) : !selectedRepoId ? (
                  <p className="text-sm text-muted-foreground">
                    请先选择仓库。
                  </p>
                ) : entries.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    这个仓库还没有想法。可以从“记录新想法”开始。
                  </p>
                ) : visibleIdeaEntries.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    没有匹配的想法。试试其他关键词，或显示历史记录。
                  </p>
                ) : (
                  <div className="flex max-h-[68vh] flex-col gap-1 overflow-y-auto pr-1">
                    {visibleIdeaEntries.map((entry) => {
                      const isSelected =
                        selectedIdea?.entry_id === entry.entry_id
                      return (
                        <div
                          key={entry.entry_id}
                          className={`flex items-start gap-2 rounded-md border p-2 transition-colors ${
                            isSelected
                              ? "border-primary bg-primary/5"
                              : "border-transparent hover:border-border hover:bg-muted/40"
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="mt-1.5 accent-primary"
                            checked={selectedIdeaIds.has(entry.entry_id)}
                            disabled={
                              entry.status !== "pending" ||
                              updatingEntryId !== null
                            }
                            aria-label={`选择想法 ${entry.occurred_at}`}
                            onChange={() => toggleIdeaSelection(entry)}
                          />
                          <button
                            type="button"
                            className="min-w-0 flex-1 text-left focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-ring"
                            aria-pressed={isSelected}
                            onClick={() => setSelectedIdeaId(entry.entry_id)}
                          >
                            <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <span className="font-mono">
                                {entry.occurred_at}
                              </span>
                              <Badge variant="outline">
                                {IDEA_STATUS_LABELS[entry.status]}
                              </Badge>
                            </span>
                            <span className="mt-1 line-clamp-2 block text-sm leading-5 whitespace-pre-wrap text-foreground">
                              {entry.text}
                            </span>
                          </button>
                        </div>
                      )
                    })}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="min-h-[28rem] gap-4">
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>想法详情</CardTitle>
                {selectedIdea && (
                  <Badge variant="outline">
                    {IDEA_STATUS_LABELS[selectedIdea.status]}
                  </Badge>
                )}
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-4">
                {!selectedIdea ? (
                  <div className="flex min-h-64 flex-1 items-center justify-center rounded-md border border-dashed border-border px-6 text-center text-sm text-muted-foreground">
                    选择一条想法，查看完整原文并管理状态。
                  </div>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      <span>记录时间：{selectedIdea.occurred_at}</span>
                      <span>来源：{selectedIdea.source}</span>
                      <span>记录人：{selectedIdea.author}</span>
                    </div>
                    <div className="max-h-[48vh] min-h-40 overflow-y-auto rounded-md border border-border bg-muted/30 p-4 text-sm leading-6 text-foreground">
                      <p className="whitespace-pre-wrap">{selectedIdea.text}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                      {selectedIdea.status === "abandoned" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            void handleUpdateIdeaStatus(selectedIdea, "pending")
                          }
                          disabled={updatingEntryId !== null}
                        >
                          {updatingEntryId === selectedIdea.entry_id
                            ? "保存中…"
                            : "恢复跟踪"}
                        </Button>
                      ) : selectedIdea.status !== "completed" ? (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              void handleUpdateIdeaStatus(
                                selectedIdea,
                                "completed"
                              )
                            }
                            disabled={updatingEntryId !== null}
                          >
                            {updatingEntryId === selectedIdea.entry_id
                              ? "保存中…"
                              : "标记完成"}
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              void handleUpdateIdeaStatus(
                                selectedIdea,
                                "abandoned"
                              )
                            }
                            disabled={updatingEntryId !== null}
                          >
                            {updatingEntryId === selectedIdea.entry_id
                              ? "保存中…"
                              : "放弃跟踪"}
                          </Button>
                        </>
                      ) : null}
                      <span className="ml-auto text-xs text-muted-foreground">
                        勾选待处理想法后，可批量生成 PRD 草稿。
                      </span>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {activeView === "summary" && (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
          <Card className="min-w-0">
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle>AI 总结</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  来自 tasks/inbox/summary.md；这里显示格式化后的内容。
                </p>
              </div>
              {!summaryEditing && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setSummaryDraft(snapshot?.summary_raw ?? "")
                    setSummaryEditing(true)
                  }}
                  disabled={!selectedRepoId}
                >
                  编辑总结
                </Button>
              )}
            </CardHeader>
            <CardContent>
              {summaryEditing ? (
                <div className="space-y-3">
                  <Textarea
                    aria-label="编辑 AI 总结 Markdown"
                    className="min-h-[60vh] font-mono text-xs leading-5"
                    value={summaryDraft}
                    onChange={(event) => setSummaryDraft(event.target.value)}
                    placeholder="编辑 summary.md 的内容…"
                  />
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setSummaryDraft(snapshot?.summary_raw ?? "")
                        setSummaryEditing(false)
                      }}
                    >
                      取消
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => void handleSaveSummary()}
                      disabled={savingSummary || !selectedRepoId}
                    >
                      {savingSummary ? "保存中…" : "保存 summary.md"}
                    </Button>
                  </div>
                </div>
              ) : snapshot?.summary_raw ? (
                <article className="prd-markdown max-h-[68vh] min-h-64 overflow-y-auto rounded-md border border-border bg-background p-5">
                  <Markdown remarkPlugins={[remarkGfm]}>
                    {snapshot.summary_raw}
                  </Markdown>
                </article>
              ) : (
                <div className="flex min-h-64 items-center justify-center rounded-md border border-dashed border-border px-6 text-center text-sm text-muted-foreground">
                  {loading ? "正在加载摘要…" : "这个仓库还没有 summary.md。"}
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>事实来源</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
              <p>
                <code>tasks/inbox/ideas.md</code> 保存原话、AI
                背景和生命周期标记，是事实来源。
              </p>
              <p>
                <code>summary.md</code>{" "}
                是可重写的派生摘要。编辑或保存摘要不会改写想法原文。
              </p>
              <p>当前页面负责编辑和保存，不会自动调用模型重新生成摘要。</p>
            </CardContent>
          </Card>
        </div>
      )}

      {activeView === "drafts" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>PRD 草稿</CardTitle>
            <span className="text-xs text-muted-foreground">
              {draftCount} 份 · {pendingReviewDraftCount} 份待审阅
            </span>
          </CardHeader>
          <CardContent>
            {loading && !snapshot ? (
              <div className="space-y-2">
                <Skeleton className="h-28" />
                <Skeleton className="h-28" />
              </div>
            ) : !selectedRepoId ? (
              <p className="text-sm text-muted-foreground">请先选择仓库。</p>
            ) : !snapshot || snapshot.drafts.length === 0 ? (
              <div className="rounded-md border border-dashed border-border px-6 py-12 text-center">
                <p className="text-sm font-medium">还没有 PRD 草稿</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  在收件箱中勾选想法并选择“生成 PRD 草稿”。
                </p>
                <Button
                  className="mt-4"
                  size="sm"
                  variant="outline"
                  onClick={() => setActiveView("inbox")}
                >
                  返回收件箱
                </Button>
              </div>
            ) : (
              <div className="grid gap-3 xl:grid-cols-2">
                {snapshot.drafts.map((draft) => (
                  <div
                    key={draft.draft_path}
                    className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-background p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant={
                          draft.metadata.status === "approved"
                            ? "default"
                            : "outline"
                        }
                      >
                        {draft.metadata.status === "pending-review"
                          ? "待审阅"
                          : draft.metadata.status === "approved"
                            ? "已确认"
                            : "已拒绝"}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {draft.metadata.priority} · {draft.metadata.prd_type}
                      </span>
                      <span className="ml-auto font-mono text-xs text-muted-foreground">
                        {draft.metadata.draft_id}
                      </span>
                    </div>
                    <div>
                      <p className="leading-5 font-medium">{draft.title}</p>
                      <p className="mt-2 max-h-40 overflow-y-auto text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
                        {draft.body_excerpt}
                      </p>
                    </div>
                    {draft.metadata.approved_pending_path && (
                      <p className="text-xs break-all text-muted-foreground">
                        目标：
                        <code>{draft.metadata.approved_pending_path}</code>
                      </p>
                    )}
                    {draft.metadata.status === "pending-review" && (
                      <div className="flex justify-end border-t border-border pt-3">
                        <Button
                          size="sm"
                          onClick={() => void handleApproveDraft(draft)}
                          disabled={approvingPath === draft.draft_path}
                        >
                          {approvingPath === draft.draft_path
                            ? "确认中…"
                            : "确认入 pending"}
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </ConsolePage>
  )
}
