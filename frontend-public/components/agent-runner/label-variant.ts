// Maps a workflow `agent/*` label to a UI badge variant.
import type { BadgeVariant } from "@/components/ui/badge";

const LABEL_TO_VARIANT: Record<string, BadgeVariant> = {
  "agent/ready": "ready",
  "agent/running": "running",
  "agent/supervising": "supervising",
  "agent/review": "review",
  "agent/automated-review-passed": "review",
  "human/awaiting-review": "warning",
  "agent/failed": "failed",
  "agent/blocked": "blocked",
};

/** 根据 GitHub 标签返回看板徽标样式。 */
export function variantForLabel(label: string): BadgeVariant {
  return LABEL_TO_VARIANT[label] ?? "default";
}

/** 去除 agent/ 或 human/ 前缀，返回标签的可读名称。 */
export function prettyLabel(label: string): string {
  if (label.startsWith("agent/") || label.startsWith("human/")) {
    return label.slice(label.indexOf("/") + 1);
  }
  return label;
}
