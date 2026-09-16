import { PrdCard } from "./prd-card";
import { buildTopologicalLevels } from "./roadmap-topology";
import type { RoadmapPrd } from "@/lib/api/types";

interface RoadmapTimelineProps {
  prds: RoadmapPrd[];
  onStart: (prd: RoadmapPrd) => void;
  onOpenContent: (prd: RoadmapPrd) => void;
  startingPath: string | null;
}

export function RoadmapTimeline({ prds, onStart, onOpenContent, startingPath }: RoadmapTimelineProps) {
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
