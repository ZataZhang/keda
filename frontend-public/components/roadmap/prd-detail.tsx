"use client";

// 统一 PRD 详情：头部（标题 / 状态 / 路径 / 动作）+ 可扩展标签容器。
//
// 三种 Roadmap 视图只负责「选中哪个 PRD」，头部动作与标签阅读都收敛在这里，
// 避免每个视图各写一份启动规则或详情容器。标签容器按可扩展设计：
// P1-FEAT-20260916-134008 需要追加 CI/CD 标签时传 `additionalTabs` 即可，
// 不必重构容器或头部动作。

import { useState } from "react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { canStartRoadmapPrd, STATE_LABELS, STATE_VARIANTS } from "./prd-card";
import { PrdContentView } from "./prd-content-view";
import { PrdEvidenceView } from "./prd-evidence-view";
import { cn } from "@/lib/utils";
import type { RoadmapPrd } from "@/lib/api/types";

export const PRD_CONTENT_TAB_ID = "content";
export const PRD_EVIDENCE_TAB_ID = "evidence";

export type PrdDetailTab = {
  id: string;
  label: string;
  render: () => ReactNode;
};

interface PrdDetailProps {
  repoId: string;
  prd: RoadmapPrd;
  starting: boolean;
  onStart: (prd: RoadmapPrd) => void;
  additionalTabs?: PrdDetailTab[];
}

/**
 * 渲染单个 PRD 的统一详情面板。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prd - 被选中的 PRD（来自列表响应，保留 state 与 block_reason）。
 * @param props.starting - 该 PRD 是否正在启动。
 * @param props.onStart - 点击「开始此 PRD」的回调，继续走既有的单启动 API。
 * @param props.additionalTabs - 追加的标签页（供后续 PRD 扩展）。
 * @returns PRD 详情面板。
 */
export function PrdDetail({
  repoId,
  prd,
  starting,
  onStart,
  additionalTabs = [],
}: PrdDetailProps) {
  const tabs: PrdDetailTab[] = [
    {
      id: PRD_CONTENT_TAB_ID,
      label: "PRD 原文",
      render: () => (
        <PrdContentView repoId={repoId} prdPath={prd.prd_path} prdTitle={prd.title} />
      ),
    },
    {
      id: PRD_EVIDENCE_TAB_ID,
      label: "验收证据",
      render: () => <PrdEvidenceView key={prd.prd_path} repoId={repoId} prdPath={prd.prd_path} />,
    },
    ...additionalTabs,
  ];
  const [activeTabId, setActiveTabId] = useState<string>(tabs[0]?.id ?? PRD_CONTENT_TAB_ID);
  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? tabs[0];
  const startable = canStartRoadmapPrd(prd);

  return (
    <div
      className="flex min-h-0 flex-col gap-3"
      data-testid="prd-detail"
      data-prd-path={prd.prd_path}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Badge variant={STATE_VARIANTS[prd.state]}>{STATE_LABELS[prd.state]}</Badge>
            {prd.issue_number ? (
              <span className="text-xs text-slate-500">Issue #{prd.issue_number}</span>
            ) : null}
          </div>
          <p className="mt-1 truncate text-sm font-medium" title={prd.title}>
            {prd.title}
          </p>
          <p className="truncate text-xs text-slate-500" title={prd.prd_path}>
            {prd.prd_path}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => onStart(prd)}
            disabled={starting || !startable}
            data-testid="prd-detail-start"
            title={
              prd.block_reason
                ? `被依赖阻塞：${prd.block_reason}`
                : startable
                  ? "开始此 PRD"
                  : "当前状态不可启动"
            }
          >
            {starting ? "启动中…" : "开始此 PRD"}
          </Button>
          {prd.issue_url ? (
            <Button size="sm" variant="outline" asChild>
              <a href={prd.issue_url} target="_blank" rel="noreferrer" data-testid="prd-detail-issue">
                查看 Issue
              </a>
            </Button>
          ) : null}
          {prd.next_action?.url ? (
            <Button size="sm" variant="outline" asChild>
              <a href={prd.next_action.url} target="_blank" rel="noreferrer">
                {prd.next_action.label}
              </a>
            </Button>
          ) : prd.next_action ? (
            <span className="text-xs text-slate-500">{prd.next_action.label}</span>
          ) : null}
        </div>
      </div>

      {prd.block_reason ? (
        <p className="text-xs text-red-600" data-testid="prd-detail-block-reason">
          被依赖阻塞：{prd.block_reason}
        </p>
      ) : null}

      <div
        role="tablist"
        aria-label="PRD 详情"
        className="flex flex-wrap items-center gap-1 border-b border-slate-200 dark:border-slate-800"
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab?.id === tab.id}
            data-testid={`prd-detail-tab-${tab.id}`}
            onClick={() => setActiveTabId(tab.id)}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5 text-sm transition-colors",
              activeTab?.id === tab.id
                ? "border-slate-900 font-medium text-slate-900 dark:border-slate-100 dark:text-slate-50"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto" data-testid="prd-detail-panel">
        {activeTab ? activeTab.render() : null}
      </div>
    </div>
  );
}
