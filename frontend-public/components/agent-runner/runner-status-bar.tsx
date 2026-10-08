// Dashboard 顶部 runner 状态条：配置摘要 + gh CLI 健康探测（只读，FR-2）。

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchRunnerHealth, fetchRunnerStatus } from "@/lib/api/agentRunner";
import type { RunnerHealthStatus, RunnerStatusSummary } from "@/lib/api/types";

type StatusState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; status: RunnerStatusSummary; health: RunnerHealthStatus | null };

/**
 * runner 状态条：数据来自既有 `/agent-runner/status` 与 `/health` 端点。
 *
 * 读取失败时降级为一行提示而非白屏，不阻塞下方监控主视图。
 */
export function RunnerStatusBar() {
  const [state, setState] = useState<StatusState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const status = await fetchRunnerStatus();
        if (controller.signal.aborted) return;
        let health: RunnerHealthStatus | null = null;
        try {
          health = await fetchRunnerHealth();
        } catch {
          // 健康探测失败按降级展示处理，不掩盖 status 主体。
        }
        if (controller.signal.aborted) return;
        setState({ kind: "ready", status, health });
      } catch (error: unknown) {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message: error instanceof Error ? error.message : "无法读取 runner 状态。",
        });
      }
    })();
    return () => {
      controller.abort();
    };
  }, []);

  if (state.kind === "loading") {
    return <Skeleton className="h-7 w-full" data-testid="runner-status-bar" />;
  }
  if (state.kind === "error") {
    return (
      <div
        className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
        data-testid="runner-status-bar"
      >
        <span>runner 状态不可用：{state.message}</span>
      </div>
    );
  }

  const { status, health } = state;
  const ghOk = health?.gh_cli_available ?? false;
  const degraded = health !== null && health.status === "degraded";
  const enabledRepos = status.repositories.filter((repo) => repo.enabled);

  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300"
      data-testid="runner-status-bar"
    >
      <span className="font-medium text-slate-700 dark:text-slate-200">Runner</span>
      {health === null ? (
        <Badge variant="warning">健康探测失败</Badge>
      ) : (
        <Badge variant={ghOk && !degraded ? "ready" : "warning"}>
          {ghOk && !degraded ? "gh 正常" : "gh 降级"}
        </Badge>
      )}
      <Badge variant={status.daemon_mode ? "running" : "default"}>
        {status.daemon_mode ? "daemon 模式" : "前台模式"}
      </Badge>
      <span>仓库 {enabledRepos.length}</span>
      <span className="text-slate-400">|</span>
      <span>
        队列上限 {status.config.max_issues} · 默认 agent{" "}
        <code className="font-mono">{status.config.default_agent}</code>
      </span>
      <span>
        标签 <code className="font-mono">{status.config.ready_label}</code> →{" "}
        <code className="font-mono">{status.config.running_label}</code>
      </span>
      <span>
        自动合并 {status.config.auto_merge ? "开" : "关"} · autopilot{" "}
        {status.config.autopilot_enabled ? "开" : "关"}
      </span>
      <span className="ml-auto font-mono text-[11px] text-slate-400">
        {status.config.remote}:{status.config.base_branch}
      </span>
    </div>
  );
}
