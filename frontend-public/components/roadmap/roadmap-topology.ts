// Roadmap 拓扑分层工具：被时间轴视图与依赖图视图共享。

import type { RoadmapPrd } from "@/lib/api/types";

/**
 * 基于 Kahn 拓扑排序把 PRD 按依赖关系分层。
 *
 * 仅考虑 kind === "prd" 且目标在列表内的依赖；被依赖方位于更浅的层。
 * 无依赖的孤立节点自然落在第 0 层，无需特殊处理。
 *
 * @param prds - 待分层的 PRD 列表。
 * @returns 按层号升序排列的二维数组，每个内层数组为同一层的 PRD。
 */
export function buildTopologicalLevels(prds: RoadmapPrd[]): RoadmapPrd[][] {
  const prdMap = new Map(prds.map((prd) => [prd.prd_path, prd]));
  const inDegree = new Map<string, number>();
  const outgoing = new Map<string, string[]>();

  for (const prd of prds) {
    inDegree.set(prd.prd_path, 0);
    outgoing.set(prd.prd_path, []);
  }

  for (const prd of prds) {
    for (const dep of prd.delivery_dependencies) {
      if (dep.kind === "prd" && prdMap.has(dep.to_path)) {
        outgoing.get(dep.to_path)?.push(prd.prd_path);
        inDegree.set(prd.prd_path, (inDegree.get(prd.prd_path) ?? 0) + 1);
      }
    }
  }

  const queue: string[] = [];
  const levelMap = new Map<string, number>();
  for (const [path, degree] of inDegree) {
    if (degree === 0) {
      queue.push(path);
      levelMap.set(path, 0);
    }
  }

  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentLevel = levelMap.get(current) ?? 0;
    for (const next of outgoing.get(current) ?? []) {
      const nextLevel = levelMap.get(next) ?? 0;
      levelMap.set(next, Math.max(nextLevel, currentLevel + 1));
      const degree = (inDegree.get(next) ?? 0) - 1;
      inDegree.set(next, degree);
      if (degree === 0) {
        queue.push(next);
      }
    }
  }

  const maxLevel = Math.max(0, ...levelMap.values());
  const levels: RoadmapPrd[][] = [];
  for (let index = 0; index <= maxLevel; index++) {
    levels.push([]);
  }
  for (const prd of prds) {
    const level = levelMap.get(prd.prd_path) ?? 0;
    levels[level].push(prd);
  }
  return levels.filter((level) => level.length > 0);
}
