/**
 * 任务执行机器胶囊：标出最近一次认领该 Issue 的机器，以及它是不是本机。
 *
 * 任务卡、详情面板与桌面小窗共用这一份文案与颜色逻辑，避免三处各自判断后漂移。
 */
import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import type { RuntimeTaskEntry } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { isQueuedRuntimeTask } from "@/lib/runtime-task";

/**
 * 计算机器胶囊的文案、颜色与悬停说明。
 *
 * @param task - 当前任务摘要。
 * @returns 胶囊展示信息；没有可信认领时返回 ``null``。
 */
export function runtimeTaskExecutorPresentation(
  task: RuntimeTaskEntry,
): { label: string; variant: BadgeVariant; title: string } | null {
  const host = task.executor_host;
  if (!host || isQueuedRuntimeTask(task)) return null;
  const byHostname = task.executor_basis === "hostname";
  const marker = byHostname ? "?" : "";
  const role = task.executor_role === "running" ? "执行机器" : "最近认领";
  if (task.executor_is_local === true) {
    return {
      label: `本机${marker} · ${host}`,
      variant: "ready",
      title: `${role}：${host}${byHostname ? "（旧版认领，按主机名判断）" : ""}`,
    };
  }
  if (task.executor_is_local === false) {
    return {
      label: `其他机器${marker} · ${host}`,
      variant: "review",
      title: `${role}：${host}${byHostname ? "（旧版认领，按主机名判断）" : ""}`,
    };
  }
  return {
    label: `未知 · ${host}`,
    variant: "default",
    title: `${role}：${host}（无法判断是否本机）`,
  };
}

/**
 * 紧挨状态胶囊渲染的机器胶囊。
 *
 * @param props.task - 当前任务摘要。
 * @param props.className - 额外样式。
 * @returns 机器胶囊；没有可信认领时返回 ``null``。
 */
export function RuntimeTaskExecutor({
  task,
  className,
}: {
  task: RuntimeTaskEntry;
  className?: string;
}) {
  const presentation = runtimeTaskExecutorPresentation(task);
  if (!presentation) return null;
  return (
    <Badge
      variant={presentation.variant}
      title={presentation.title}
      className={cn("rounded-full whitespace-nowrap", className)}
      data-testid="runtime-task-executor"
    >
      {presentation.label}
    </Badge>
  );
}

/**
 * 本机运行记录被其他机器的认领取代时的提示。
 *
 * @param props.task - 当前任务摘要。
 * @returns 提示文字；本机记录未被取代时返回 ``null``。
 */
export function RuntimeTaskLocalRunSuperseded({ task }: { task: RuntimeTaskEntry }) {
  if (!task.local_run_superseded) return null;
  return (
    <span
      className="italic"
      title="这一轮由其他机器执行，本机账本里只有更早的运行；详情请看 Issue。"
      data-testid="runtime-task-local-run-superseded"
    >
      本机无这一轮运行记录
    </span>
  );
}
