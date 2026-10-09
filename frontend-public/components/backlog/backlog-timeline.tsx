import { PrdCard } from "./prd-card";
import { buildTopologicalLevels } from "./backlog-topology";
import type { BacklogPrd } from "@/lib/api/types";

interface BacklogTimelineProps {
  prds: BacklogPrd[];
  onStart: (prd: BacklogPrd) => void;
  onOpenContent: (prd: BacklogPrd) => void;
  startingPath: string | null;
  /** 「加入就绪」回调（FR-3）。 */
  onEnqueueReady?: (prd: BacklogPrd) => void;
  /** 正在入队的 PRD 路径。 */
  enqueuingPath?: string | null;
}

export function BacklogTimeline({
  prds,
  onStart,
  onOpenContent,
  startingPath,
  onEnqueueReady,
  enqueuingPath,
}: BacklogTimelineProps) {
  if (prds.length === 0) {
    return <p className="text-sm text-slate-500">暂无 PRD。</p>;
  }

  const levels = buildTopologicalLevels(prds);

  return (
    <div className="space-y-8">
      {levels.map((level, levelIndex) => (
        <div key={levelIndex} className="relative">
          <div className="mb-2 text-xs font-medium text-slate-400">
            阶段 {levelIndex + 1}
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {level.map((prd) => (
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
          {levelIndex < levels.length - 1 ? (
            <div className="mt-4 flex justify-center">
              <div className="h-6 w-px bg-slate-300 dark:bg-slate-700" />
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
