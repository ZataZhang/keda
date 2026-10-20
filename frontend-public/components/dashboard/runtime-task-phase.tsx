import { Badge } from "@/components/ui/badge";
import {
  EVENT_TYPE_LABELS,
  INVOCATION_PHASE_LABELS,
  PHASE_LABELS,
} from "@/components/backlog/prd-lifecycle-format";

/** 运行阶段补充词汇；通用阶段复用生命周期视图的中文口径。 */
const RUNTIME_PHASE_LABELS: Record<string, string> = {
  implement: "编写代码",
  agent: "Agent 执行",
  implementation_complete: "实现完成",
  pre_pr_review: "PR 前审查",
  rv_reexec: "提交后验收重跑",
  verifier: "独立复核",
  prd_delivery: "PRD 交付检查",
  evidence: "交付证据检查",
  commit: "提交与推送",
  publish: "发布 PR",
  post_pr_supervisor: "PR 后检查",
};

/** 将真实阶段翻译为中文；未知值保留原文，缺失时明确说明。 */
export function runtimePhaseLabel(phase: string | null): string {
  if (!phase) return "未记录阶段";
  return (
    RUNTIME_PHASE_LABELS[phase] ??
    INVOCATION_PHASE_LABELS[phase] ??
    PHASE_LABELS[phase] ??
    EVENT_TYPE_LABELS[phase] ??
    phase
  );
}

/** 在状态旁醒目展示阶段，悬停可查看后端原始标识。 */
export function RuntimeTaskPhase({ phase }: { phase: string | null }) {
  return (
    <Badge
      variant="review"
      className="break-words whitespace-normal"
      title={phase ?? "账本未记录阶段"}
      data-testid="runtime-task-phase"
    >
      阶段：{runtimePhaseLabel(phase)}
    </Badge>
  );
}
