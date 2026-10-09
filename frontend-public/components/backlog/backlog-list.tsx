import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PrdCard } from "./prd-card";
import type { BacklogPrd } from "@/lib/api/types";

type ListSortKey = "state" | "priority" | "updated_at";

interface BacklogListProps {
  prds: BacklogPrd[];
  onStart: (prd: BacklogPrd) => void;
  onOpenContent: (prd: BacklogPrd) => void;
  startingPath: string | null;
  /** 「加入就绪」回调（FR-3），透传到每张卡片。 */
  onEnqueueReady?: (prd: BacklogPrd) => void;
  /** 正在入队的 PRD 路径。 */
  enqueuingPath?: string | null;
}

const PRIORITY_ORDER: Record<string, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
  P3: 3,
  "": 4,
};

const STATE_ORDER: Record<string, number> = {
  running: 0,
  ready: 1,
  failed: 2,
  blocked: 3,
  review: 4,
  supervising: 5,
  waiting: 6,
  not_started: 7,
  merged: 8,
  archived: 9,
  unresolved_dependency: 10,
};

function sortPrds(prds: BacklogPrd[], sortKey: ListSortKey): BacklogPrd[] {
  return [...prds].sort((left, right) => {
    switch (sortKey) {
      case "priority": {
        const leftPriority = PRIORITY_ORDER[left.priority] ?? 99;
        const rightPriority = PRIORITY_ORDER[right.priority] ?? 99;
        if (leftPriority !== rightPriority) {
          return leftPriority - rightPriority;
        }
        break;
      }
      case "state": {
        const leftState = STATE_ORDER[left.state] ?? 99;
        const rightState = STATE_ORDER[right.state] ?? 99;
        if (leftState !== rightState) {
          return leftState - rightState;
        }
        break;
      }
      case "updated_at": {
        const leftDate = new Date(left.updated_at).getTime();
        const rightDate = new Date(right.updated_at).getTime();
        if (leftDate !== rightDate) {
          return rightDate - leftDate;
        }
        break;
      }
    }
    return left.title.localeCompare(right.title);
  });
}

const SORT_LABELS: Record<ListSortKey, string> = {
  state: "按状态",
  priority: "按优先级",
  updated_at: "按更新时间",
};

export function BacklogList({
  prds,
  onStart,
  onOpenContent,
  startingPath,
  onEnqueueReady,
  enqueuingPath,
}: BacklogListProps) {
  const [sortKey, setSortKey] = useState<ListSortKey>("priority");
  const sortedPrds = useMemo(() => sortPrds(prds, sortKey), [prds, sortKey]);

  if (prds.length === 0) {
    return <p className="text-sm text-slate-500">暂无 PRD。</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-2">
        <span className="text-xs text-slate-500">排序</span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" data-testid="backlog-sort-trigger">
              排序：{SORT_LABELS[sortKey]}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuRadioGroup
              value={sortKey}
              onValueChange={(value) => setSortKey(value as ListSortKey)}
            >
              <DropdownMenuRadioItem value="priority">按优先级</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="state">按状态</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="updated_at">按更新时间</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {sortedPrds.map((prd) => (
          <PrdCard
            key={prd.prd_path}
            prd={prd}
            onStart={() => onStart(prd)}
            onOpenContent={() => onOpenContent(prd)}
            starting={startingPath === prd.prd_path}
            onEnqueueReady={onEnqueueReady ? () => onEnqueueReady(prd) : undefined}
            enqueuing={enqueuingPath === prd.prd_path}
          />
        ))}
      </div>
    </div>
  );
}
