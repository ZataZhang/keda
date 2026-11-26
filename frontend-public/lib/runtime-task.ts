import type { RuntimeTaskEntry } from "@/lib/api/types";

/** 判断任务是否等待新一轮认领；此时运行字段属于历史，不代表本轮进度。 */
export function isQueuedRuntimeTask(task: RuntimeTaskEntry): boolean {
  return (
    task.status === "waiting" &&
    (task.waiting_reason === "queued" ||
      (!task.waiting_reason && task.labels.includes("agent/ready")))
  );
}
