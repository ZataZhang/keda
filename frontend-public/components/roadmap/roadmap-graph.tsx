// Roadmap 依赖图视图：绝对定位的精简节点卡片 + SVG 贝塞尔连线，不依赖第三方图库。

import { Badge } from "@/components/ui/badge";
import { STATE_LABELS, STATE_VARIANTS } from "./prd-card";
import { buildTopologicalLevels } from "./roadmap-topology";
import type { RoadmapPrd } from "@/lib/api/types";

const NODE_WIDTH = 220;
const NODE_HEIGHT = 64;
const COLUMN_GAP = 80;
const ROW_GAP = 24;
const COLUMN_WIDTH = NODE_WIDTH + COLUMN_GAP;
const ROW_HEIGHT = NODE_HEIGHT + ROW_GAP;

interface GraphNodePlacement {
  prd: RoadmapPrd;
  x: number;
  y: number;
}

interface GraphEdge {
  key: string;
  path: string;
}

interface RoadmapGraphProps {
  prds: RoadmapPrd[];
  onOpenContent: (prd: RoadmapPrd) => void;
}

/**
 * 计算每个 PRD 节点的画布坐标：第 N 层放第 N 列，层内节点纵向均布。
 *
 * @param levels - 拓扑分层结果，层号即列号。
 * @returns 节点定位信息列表。
 */
function buildNodePlacements(levels: RoadmapPrd[][]): GraphNodePlacement[] {
  const placements: GraphNodePlacement[] = [];
  levels.forEach((levelPrds, levelIndex) => {
    levelPrds.forEach((prd, rowIndex) => {
      placements.push({
        prd,
        x: levelIndex * COLUMN_WIDTH,
        y: rowIndex * ROW_HEIGHT,
      });
    });
  });
  return placements;
}

/**
 * 依赖图视图：左侧为被依赖方，依赖沿箭头从左向右流动。
 *
 * 节点是精简卡片（标题 + 状态 Badge），点击行为与列表视图的「查看原文」一致；
 * 连线只画 kind === "prd" 且两端都在当前列表内的依赖。画布超出容器时滚动查看。
 *
 * @param props.prds - 当前仓库可见的 PRD 列表。
 * @param props.onOpenContent - 点击节点时打开 PRD 原文的回调。
 */
export function RoadmapGraph({ prds, onOpenContent }: RoadmapGraphProps) {
  if (prds.length === 0) {
    return <p className="text-sm text-slate-500">暂无 PRD。</p>;
  }

  const levels = buildTopologicalLevels(prds);
  const placements = buildNodePlacements(levels);
  const placementByPath = new Map(
    placements.map((placement) => [placement.prd.prd_path, placement]),
  );

  const edges: GraphEdge[] = [];
  for (const prd of prds) {
    for (const dep of prd.delivery_dependencies) {
      if (dep.kind !== "prd") {
        continue;
      }
      const source = placementByPath.get(dep.to_path);
      const target = placementByPath.get(dep.from_path);
      if (!source || !target) {
        continue;
      }
      const x1 = source.x + NODE_WIDTH;
      const y1 = source.y + NODE_HEIGHT / 2;
      const x2 = target.x;
      const y2 = target.y + NODE_HEIGHT / 2;
      const control = Math.max(32, (x2 - x1) / 2);
      edges.push({
        key: `${dep.to_path}->${dep.from_path}`,
        path: `M ${x1} ${y1} C ${x1 + control} ${y1}, ${x2 - control} ${y2}, ${x2} ${y2}`,
      });
    }
  }

  const maxLevelSize = Math.max(...levels.map((level) => level.length));
  const canvasWidth = levels.length * COLUMN_WIDTH - COLUMN_GAP;
  const canvasHeight = maxLevelSize * ROW_HEIGHT - ROW_GAP;

  return (
    <div className="relative" style={{ width: canvasWidth, height: canvasHeight }}>
      <svg
        className="absolute inset-0"
        width={canvasWidth}
        height={canvasHeight}
        aria-hidden="true"
      >
        <defs>
          <marker
            id="roadmap-graph-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-slate-400 dark:fill-slate-500" />
          </marker>
        </defs>
        {edges.map((edge) => (
          <path
            key={edge.key}
            d={edge.path}
            className="fill-none stroke-slate-300 dark:stroke-slate-600"
            strokeWidth={1.5}
            markerEnd="url(#roadmap-graph-arrow)"
          />
        ))}
      </svg>
      {placements.map((placement) => (
        <button
          key={placement.prd.prd_path}
          type="button"
          data-testid="prd-open-content"
          data-prd-path={placement.prd.prd_path}
          className="absolute flex flex-col justify-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-2 text-left shadow-sm transition-colors hover:border-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-slate-500"
          style={{
            left: placement.x,
            top: placement.y,
            width: NODE_WIDTH,
            height: NODE_HEIGHT,
          }}
          onClick={() => onOpenContent(placement.prd)}
        >
          <span className="truncate text-sm font-medium" title={placement.prd.title}>
            {placement.prd.title}
          </span>
          <Badge variant={STATE_VARIANTS[placement.prd.state]} className="w-fit">
            {STATE_LABELS[placement.prd.state]}
          </Badge>
        </button>
      ))}
    </div>
  );
}
