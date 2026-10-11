"use client";

// 详情头部交付进度摘要：清单 / 影响树 / 证据三个常驻读数。
//
// 事实源是后端 `.../progress` 聚合读数——本组件只做格式化，不解析 PRD 原文、
// 不重新定义任何口径。所有读数都是**弱信号**（文件归属 ≠ 验证通过；影响树触碰
// ≠ 改对），一律带 `title` 提示文案披露这一点，不用进度条或完成色暗示「已完成」。
//
// 三条铁律，贯穿每个 chip：
// 1. 「确认没有」（无计划 / 无工作区 / 无基准 / 无清单区块）显示 `-`，不伪造分母；
// 2. 「读取失败 / 来源不可用」显示「暂不可用」，绝不混成 `-` 或 `0`；
// 3. 只有成功读取且确有可比数据时，才显示精确读数。

import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import type { BacklogPrdProgress } from "@/lib/api/types";

/** 弱信号提示：影响树触碰不代表改对。 */
const IMPACT_HINT = "影响树触达：分支实际改动 ∩ 计划节点（弱信号，触碰≠改对）。?n 是无法判定节点数，不计入分母。";
/** 弱信号提示：清单勾选不代表验证通过。 */
const CHECKLIST_HINT = "验收清单已勾选 / 总项数（弱信号，勾选数不代表验证通过）。";
/** 弱信号提示：有产物文件不代表验证通过。 */
const EVIDENCE_HINT = "证据已产出 / 计划条目（弱信号，文件归属≠验证通过）。";

/**
 * 影响树触达读数的紧凑文案。
 *
 * @param progress - 聚合进度读数。
 * @returns 可计算时形如 ``~7/12 ?2``（无 `?` 段时省略），不可比较时 ``—``，
 *   来源不可用时 ``暂不可用``。
 */
export function formatImpactReading(progress: BacklogPrdProgress): string {
  if (progress.impact_status === "unavailable") {
    return "暂不可用";
  }
  if (progress.impact_status !== "available" || progress.impact === null) {
    return "—";
  }
  const base = `~${progress.impact.touched}/${progress.impact.judgeable}`;
  return progress.impact.unresolvable > 0
    ? `${base} ?${progress.impact.unresolvable}`
    : base;
}

/**
 * 验收清单勾选读数的紧凑文案。
 *
 * @param progress - 聚合进度读数。
 * @returns 可用且有清单区块时形如 ``3/12``；``0/0``（无清单区块）显示 ``—``；
 *   读取失败显示 ``暂不可用``。
 */
export function formatChecklistReading(progress: BacklogPrdProgress): string {
  const { checklist } = progress;
  if (checklist.status === "unavailable") {
    return "暂不可用";
  }
  if (checklist.checked === null || checklist.total === null) {
    return "暂不可用";
  }
  if (checklist.total === 0) {
    return "—";
  }
  return `${checklist.checked}/${checklist.total}`;
}

/**
 * 证据计划 vs 产出读数的紧凑文案。
 *
 * @param progress - 聚合进度读数。
 * @returns 有验证计划时形如 ``2/3``（已产出的不同计划条目 / 计划条目）；确认无计划
 *   显示 ``—``；来源不可用或读取失败显示 ``暂不可用``。
 */
export function formatEvidenceReading(progress: BacklogPrdProgress): string {
  const { evidence } = progress;
  if (evidence.status === "unavailable" || evidence.planned === null) {
    return "暂不可用";
  }
  if (evidence.planned.length === 0) {
    return "—";
  }
  if (evidence.produced_planned_count === null) {
    return "暂不可用";
  }
  return `${evidence.produced_planned_count}/${evidence.planned.length}`;
}

/**
 * 判断某个读数是否处于「不可比较 / 暂不可用」态（用于弱化配色而非完成色）。
 *
 * @param text - 该读数的展示文案。
 * @returns 文案为 ``—`` 或 ``暂不可用`` 时为 ``true``。
 */
function isWeakOrUnavailable(text: string): boolean {
  return text === "—" || text === "暂不可用";
}

type Reading = {
  key: string;
  label: string;
  text: string;
  hint: string;
  testId: string;
};

/**
 * 把聚合读数折算成三条 chip 的展示数据。
 *
 * @param progress - 后端聚合进度读数。
 * @returns 清单 / 影响树 / 证据三条 chip 的文案、标签与提示。
 */
export function buildProgressReadings(progress: BacklogPrdProgress): Reading[] {
  return [
    {
      key: "checklist",
      label: "清单",
      text: formatChecklistReading(progress),
      hint: CHECKLIST_HINT,
      testId: "prd-progress-checklist",
    },
    {
      key: "impact",
      label: "影响树",
      text: formatImpactReading(progress),
      hint: IMPACT_HINT,
      testId: "prd-progress-impact",
    },
    {
      key: "evidence",
      label: "证据",
      text: formatEvidenceReading(progress),
      hint: EVIDENCE_HINT,
      testId: "prd-progress-evidence",
    },
  ];
}

/**
 * 按读数是否处于弱信号 / 不可用态选择 chip 配色（永不使用完成色暗示「已完成」）。
 *
 * @param text - 该读数的展示文案。
 * @returns 不可比较或暂不可用时用中性 ``default``，否则用 ``supervising`` 强调可关注。
 */
function chipVariant(text: string): BadgeVariant {
  return isWeakOrUnavailable(text) ? "default" : "supervising";
}

/**
 * 渲染详情头部的交付进度摘要（三个弱信号读数）。
 *
 * @param props.progress - 后端聚合进度读数（由详情面板一次拉取）。
 * @returns 常驻的三读数 chip；组件本身不发起请求。
 */
export function PrdProgressStrip({ progress }: { progress: BacklogPrdProgress }) {
  const readings = buildProgressReadings(progress);
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      data-testid="prd-progress-strip"
      title="交付进度为弱信号：帮助定位，不代表验收通过或改对。"
    >
      {readings.map((reading) => (
        <span key={reading.key} className="inline-flex items-center gap-1" title={reading.hint}>
          <span className="text-xs text-slate-500">{reading.label}</span>
          <Badge variant={chipVariant(reading.text)} data-testid={reading.testId}>
            {reading.text}
          </Badge>
        </span>
      ))}
    </div>
  );
}
