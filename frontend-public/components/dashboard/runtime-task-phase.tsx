import { Badge } from "@/components/ui/badge";
import type { RuntimeTaskEntry } from "@/lib/api/types";
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
  deliberation_question_posted: "等待需求澄清",
  validation_passed: "验收通过",
  validation_reset: "需要重新验收",
  draft_pr_created: "Draft PR 已创建",
  publish_recovered: "发布已恢复",
  blocked_resolution_requested: "请求解除阻塞",
  post_pr_rework_requested: "PR 后返工请求",
  post_pr_rework_superseded: "处理请求已更新",
  rebase_repair_complete: "分支修复完成",
  auto_signed_off: "合并门禁通过",
  auto_merged: "自动合并完成",
};

/** 运行阶段说明；让紧凑任务卡也能说明当前环节在做什么。 */
const RUNTIME_PHASE_DESCRIPTIONS: Record<string, string> = {
  implement: "按 Issue 和 PRD 要求修改代码。",
  agent: "Agent 执行任务的环节；具体调用可在任务详情中查看。",
  implementation_complete:
    "记录了实现、PRD 交付检查、本地验证和独立复核通过；PR 审查与合并结果需另行确认。",
  pre_pr_review: "Reviewer 检查代码变更与修复后的验证结果的环节。",
  rv_reexec: "代码提交后重跑验收证据和仓库验证命令。",
  verifier: "独立核对已提交的代码树、需求符合度和验收证据。",
  prd_delivery: "检查 PRD 执行侧验收项、证据引用和归档状态。",
  evidence: "检查验收证据是否齐全、取证脚本是否放在正确位置。",
  commit: "Runner 处理提交请求并运行提交门禁。",
  publish: "推送代码分支，并创建或更新 Draft PR。",
  post_pr_supervisor: "检查 PR、CI 和讨论，决定等待、修复或请求人工处理。",
  deliberation_question_posted: "讨论 Agent 发布需求澄清问题的记录。",
  validation_passed: "本轮验收或验证检查已通过。",
  validation_reset: "代码变更使之前的验证签收失效的记录。",
  draft_pr_created: "Draft PR 创建或复用的记录。",
  publish_recovered: "发布恢复流程已补齐 PR 与 Issue 状态交接。",
  blocked_resolution_requested: "请求恢复阻塞任务的记录。",
  post_pr_rework_requested: "PR 检查后发现需要继续修改或同步分支，系统已请求返工。",
  post_pr_rework_superseded: "较早的 PR 后处理要求已被新状态取代。",
  rebase_repair_complete: "分支同步或冲突修复完成的记录。",
  auto_signed_off: "合并队列已完成合并前的签核检查。",
  auto_merged: "PR 已通过合并队列并完成自动合并。",
};

/** Agent 调用明细阶段的说明，复用详情页已有的阶段词汇。 */
const INVOCATION_PHASE_DESCRIPTIONS: Record<string, string> = {
  implementation: "按 Issue 和 PRD 要求编写实现代码。",
  fix: "根据验证失败或运行反馈修复代码。",
  review: "检查代码变更并形成审查结论。",
  review_repair: "按审查意见修复代码，之后重新运行验证。",
  verification: "运行仓库配置的验证命令检查改动。",
  verification_recovery: "修复验证失败后再次运行相关检查。",
  rebase_recovery: "同步目标分支并处理冲突，之后重新验证。",
  closeout: "补齐 PRD 验收、证据和归档等交付项。",
  supervisor: "检查 PR、CI 和讨论状态，并判断下一步。",
  supervisor_repair: "修复监督检查发现的问题，再交给下一轮检查。",
  content_generation: "生成或整理 Issue、PRD 或 PR 的内容。",
  unspecified: "Agent 调用已记录，但没有更细的阶段信息。",
};

/** 通用生命周期阶段和事件回退值的说明。 */
const FALLBACK_PHASE_DESCRIPTIONS: Record<string, string> = {
  none: "还没有可用的阶段记录。",
  queued: "任务已进入队列，等待 Runner 认领。",
  executing: "Agent 执行任务的环节。",
  validating: "运行验证或验收检查的环节。",
  reviewing: "审查代码或 PR 的环节。",
  merging: "执行合并门禁或合并操作的环节。",
  blocked: "等待人工决定或解除依赖的环节。",
  failed: "运行失败的记录；请结合状态来源和运行详情排查。",
  completed: "运行成功结束的记录；PR 合并或 Issue 关闭状态另行显示。",
  started: "Runner 开始处理任务的记录。",
  claimed: "Runner 认领 Issue 的记录。",
  attempt: "Agent 开始执行尝试的记录。",
  retry: "上一次尝试未完成后的重试环节。",
  recovered: "Runner 已从之前的失败中恢复。",
  implementation_completed: "实现阶段已完成并进入后续交付流程。",
  validation_started: "验收或验证检查已开始。",
  validation_failed: "验收或验证检查未通过。",
  review_started: "代码或 PR 审查已开始。",
  review_passed: "代码或 PR 审查已通过。",
  review_failed: "代码或 PR 审查未通过。",
  merge_started: "合并流程已开始。",
  merged: "变更已合并。",
  archived: "PRD 已归档。",
  unblocked: "阻塞已解除，任务可以继续处理。",
  agent_token_usage: "记录了 Agent 的 Token 用量，不表示执行阶段推进。",
};

const PHASE_CONTEXT_HINT =
  "阶段优先取最近失败环节；没有记录时回退到最后进度事件。它说明处理到了哪里，不一定是失败原因；当前状态以状态徽章为准。";

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

/**
 * 返回运行阶段的可见说明；未知阶段会明确显示未配置说明，不推测其含义。
 *
 * @param phase - 运行账本或 Issue 时间线记录的阶段标识。
 * @returns 当前环节的简短说明。
 */
export function runtimePhaseDescription(phase: string | null): string {
  if (!phase) return "尚未记录阶段，暂时无法判断任务进行到哪一步。";
  return (
    RUNTIME_PHASE_DESCRIPTIONS[phase] ??
    INVOCATION_PHASE_DESCRIPTIONS[phase] ??
    FALLBACK_PHASE_DESCRIPTIONS[phase] ??
    "系统记录了此阶段，但尚未配置对应说明；请查看任务详情和运行事件。"
  );
}

/** 在状态旁呈现最近记录的阶段；没有记录时不重复展示占位徽章。 */
export function RuntimeTaskPhase({ task }: { task: RuntimeTaskEntry }) {
  const phase = task.phase;
  if (!phase) return null;
  const phaseDescription = runtimePhaseDescription(phase);
  return (
    <Badge
      variant="review"
      className="break-words whitespace-normal"
      title={`${phase ?? "账本未记录阶段"}：${phaseDescription} ${PHASE_CONTEXT_HINT}`}
      aria-label={`最近阶段：${runtimePhaseLabel(phase)}。${phaseDescription}${PHASE_CONTEXT_HINT}`}
      data-testid="runtime-task-phase"
    >
      最近阶段：{runtimePhaseLabel(phase)}
    </Badge>
  );
}

/** 阶段解释始终描述记录的环节；历史任务缺少阶段时不铺重复的未知提示。 */
export function RuntimeTaskPhaseDescription({ task, className }: { task: RuntimeTaskEntry; className?: string }) {
  if (!task.phase && (task.status === "closed" || task.status === "completed")) return null;
  const description = task.phase
    ? `环节说明：${runtimePhaseDescription(task.phase)}`
    : task.status === "waiting" && task.waiting_reason === "queued"
      ? "等待 Runner 认领，尚无执行阶段记录。"
      : "暂无阶段记录；可在任务详情中查看运行记录。";
  return <span className={className} data-testid="runtime-task-phase-description">{description}</span>;
}
