"use client"
/* eslint-disable react-hooks/set-state-in-effect */

// 托管进程页：启动/停止各仓库 runner 进程，实时查看日志。
//
// 日志通过 offset 轮询续读（2.5s），仅在日志抽屉打开时轮询。

import { ConsolePage, ConsolePageHeader, ConsoleToolbar } from "@/components/layout/console-page";
import { NativeSelect } from "@/components/ui/native-select";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { formatLocalDateTime } from "@/lib/utils";
import { useRepositorySelection } from "@/lib/console-repository-selection";
import {
  fetchProcessLog,
  fetchProcesses,
  startProcess,
  stopProcess,
} from "@/lib/api/console";
import type {
  RunnerProcessKind,
  RunnerProcessRecord,
} from "@/lib/api/types";

const PROCESS_POLL_INTERVAL_MS = 5000;
const LOG_POLL_INTERVAL_MS = 2500;

const KIND_LABELS: Record<RunnerProcessKind, string> = {
  daemon: "daemon（实现队列）",
  review_daemon: "supervise-daemon（PR 后监督）",
  run_once: "run（单轮）",
  review_once: "supervise（单轮）",
  blocked_continue: "blocked-continue",
};

const STARTABLE_KINDS: RunnerProcessKind[] = [
  "daemon",
  "review_daemon",
  "run_once",
  "review_once",
];

export default function ProcessesPage() {
  const [processes, setProcesses] = useState<RunnerProcessRecord[]>([]);
  const {
    enabledRepositories: repositories,
    selectedRepoId,
    selectRepoId,
  } = useRepositorySelection();
  const [selectedKind, setSelectedKind] = useState<RunnerProcessKind>("daemon");
  const [starting, setStarting] = useState(false);
  const [startingAll, setStartingAll] = useState(false);
  const [logProcess, setLogProcess] = useState<RunnerProcessRecord | null>(null);

  // 已有在跑 daemon 的仓库：全局启动要跳过它们，否则后端会按同类常驻进程去重报错。
  const daemonRepoIds = new Set(
    processes
      .filter((record) => record.kind === "daemon" && record.status === "running")
      .map((record) => record.repo_id),
  );

  const refreshProcesses = useCallback(async () => {
    try {
      setProcesses(await fetchProcesses());
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "无法加载进程列表。",
      );
    }
  }, []);

  useEffect(() => {
    void refreshProcesses();
    const timer = setInterval(() => void refreshProcesses(), PROCESS_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refreshProcesses]);

  async function handleStart() {
    if (!selectedRepoId) {
      toast.warning("请先选择目标仓库。");
      return;
    }
    const confirmed = window.confirm(
      `确认为仓库 ${selectedRepoId} 启动 ${KIND_LABELS[selectedKind]} 进程？`,
    );
    if (!confirmed) {
      return;
    }
    setStarting(true);
    try {
      const record = await startProcess({
        repo_id: selectedRepoId,
        kind: selectedKind,
      });
      toast.success(`已启动进程 ${record.process_id}（pid ${record.pid}）。`);
      await refreshProcesses();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动进程失败。");
    } finally {
      setStarting(false);
    }
  }

  /** 为所有启用仓库各启动一个 daemon，已在运行的仓库跳过。 */
  async function handleStartAll() {
    if (repositories.length === 0) {
      toast.warning("没有可用的启用仓库。");
      return;
    }
    const targets = repositories.filter(
      (entry) => !daemonRepoIds.has(entry.repo_id),
    );
    if (targets.length === 0) {
      toast.info("所有启用仓库都已有运行中的 daemon。");
      return;
    }
    const confirmed = window.confirm(
      `确认给 ${targets.length} 个仓库启动 daemon？已在运行的 ${daemonRepoIds.size} 个仓库会跳过。`,
    );
    if (!confirmed) {
      return;
    }
    setStartingAll(true);
    const started: string[] = [];
    const failures: string[] = [];
    // 串行启动：当前仓库数量是个位数，串行能给出稳定的顺序与逐条错误归属。
    for (const entry of targets) {
      try {
        const record = await startProcess({
          repo_id: entry.repo_id,
          kind: "daemon",
        });
        started.push(`${entry.repo_id}（pid ${record.pid}）`);
      } catch (error) {
        failures.push(
          `${entry.repo_id}：${error instanceof Error ? error.message : "启动失败。"}`,
        );
      }
    }
    try {
      if (started.length > 0) {
        toast.success(`已启动 ${started.length} 个 daemon：${started.join("、")}`);
      }
      if (failures.length > 0) {
        toast.error(`${failures.length} 个仓库启动失败：${failures.join("；")}`);
      }
    } finally {
      setStartingAll(false);
      await refreshProcesses();
    }
  }

  async function handleStop(record: RunnerProcessRecord) {
    const confirmed = window.confirm(
      `确认停止进程 ${record.process_id}（${record.repo_id} / ${KIND_LABELS[record.kind]}）？`,
    );
    if (!confirmed) {
      return;
    }
    try {
      const stopped = await stopProcess(record.process_id);
      toast.success(`进程 ${record.process_id} 已 ${stopped.status}。`);
      await refreshProcesses();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "停止进程失败。");
    }
  }

  return (
    <ConsolePage>
      <ConsolePageHeader title="托管进程" description={<> 每个仓库一个 daemon 进程即可获得多项目并发。进程脱离后端运行，
          后端重启不影响执行中的 runner。 </>} />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">启动新进程</CardTitle>
        </CardHeader>
        <CardContent>
          <ConsoleToolbar>
          <NativeSelect

            value={selectedRepoId}
            onChange={(event) => selectRepoId(event.target.value)}
            aria-label="目标仓库"
          >
            {repositories.length === 0 ? (
              <option value="">（无可用仓库）</option>
            ) : null}
            {repositories.map((entry) => (
              <option key={entry.repo_id} value={entry.repo_id}>
                {entry.display_name || entry.repo_id}
              </option>
            ))}
          </NativeSelect>
          <NativeSelect

            value={selectedKind}
            onChange={(event) =>
              setSelectedKind(event.target.value as RunnerProcessKind)
            }
            aria-label="进程类型"
          >
            {STARTABLE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABELS[kind]}
              </option>
            ))}
          </NativeSelect>
          <Button size="sm" onClick={() => void handleStart()} disabled={starting}>
            {starting ? "启动中…" : "启动"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleStartAll()}
            disabled={startingAll || repositories.length === 0}
            title="为所有启用仓库各启动一个 daemon；已在运行的仓库自动跳过"
          >
            {startingAll ? "批量启动中…" : "全部仓库启动 daemon"}
          </Button>
          </ConsoleToolbar>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">进程列表</CardTitle>
        </CardHeader>
        <CardContent>
          {processes.length === 0 ? (
            <p className="text-sm text-slate-500">暂无托管进程。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <th className="py-2 pr-3">仓库</th>
                    <th className="py-2 pr-3">类型</th>
                    <th className="py-2 pr-3">PID</th>
                    <th className="py-2 pr-3">状态</th>
                    <th className="py-2 pr-3">退出码</th>
                    <th className="py-2 pr-3">启动时间</th>
                    <th className="py-2 pr-3">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {processes.map((record) => (
                    <tr
                      key={record.process_id}
                      className="border-b border-slate-100 dark:border-slate-800"
                    >
                      <td className="py-2 pr-3 font-medium">{record.repo_id}</td>
                      <td className="py-2 pr-3">
                        <code className="text-xs">{KIND_LABELS[record.kind]}</code>
                      </td>
                      <td className="py-2 pr-3 font-mono text-xs">{record.pid}</td>
                      <td className="py-2 pr-3">
                        <ProcessStatusBadge status={record.status} />
                      </td>
                      <td className="py-2 pr-3 font-mono text-xs">
                        {record.exit_code ?? "—"}
                      </td>
                      <td className="py-2 pr-3 font-mono text-[11px] text-slate-500">
                        {formatLocalDateTime(record.started_at)}
                      </td>
                      <td className="py-2 pr-3">
                        <div className="flex gap-1">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setLogProcess(record)}
                          >
                            日志
                          </Button>
                          {record.status === "running" ? (
                            <Button
                              size="sm"
                              variant="destructive"
                              onClick={() => void handleStop(record)}
                            >
                              停止
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <ProcessLogSheet
        record={logProcess}
        onClose={() => setLogProcess(null)}
      />
    </ConsolePage>
  );
}

function ProcessStatusBadge({ status }: { status: RunnerProcessRecord["status"] }) {
  const variant =
    status === "running" ? "ready" : status === "killed" ? "warning" : "default";
  return <Badge variant={variant}>{status}</Badge>;
}

function ProcessLogSheet({
  record,
  onClose,
}: {
  record: RunnerProcessRecord | null;
  onClose: () => void;
}) {
  const [logContent, setLogContent] = useState("");
  const offsetRef = useRef(0);
  const containerRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    if (!record) {
      setLogContent("");
      offsetRef.current = 0;
      return;
    }
    let cancelled = false;

    async function pollLog() {
      if (!record || cancelled) {
        return;
      }
      try {
        const chunk = await fetchProcessLog(record.process_id, offsetRef.current);
        if (cancelled) {
          return;
        }
        if (chunk.content) {
          offsetRef.current = chunk.next_offset;
          setLogContent((current) => {
            const merged = current + chunk.content;
            // 防止超长日志拖垮页面，只保留尾部 ~200KB。
            return merged.length > 200_000 ? merged.slice(-200_000) : merged;
          });
        }
      } catch (error) {
        if (!cancelled) {
          toast.error(
            error instanceof Error ? error.message : "读取日志失败。",
          );
        }
      }
    }

    void pollLog();
    const timer = setInterval(() => void pollLog(), LOG_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [record]);

  useEffect(() => {
    const container = containerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [logContent]);

  return (
    <Sheet open={record !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-sm">
            {record
              ? `日志：${record.repo_id} / ${KIND_LABELS[record.kind]}（${record.process_id}）`
              : "日志"}
          </SheetTitle>
        </SheetHeader>
        {/* 行首 [HH:MM:SS] 由 runner 机器写入，不含日期/时区，浏览器无法可靠
            换算，原样展示不做转换（见 docs/guides/agent-runner.md）。 */}
        <p className="px-4 pb-2 text-xs text-slate-400">
          行首时间 [HH:MM:SS] 为 runner 机器的本地墙钟，不含时区，未做浏览器时区换算。
        </p>
        <pre
          ref={containerRef}
          className="mx-4 mb-4 h-[80vh] overflow-auto rounded-md bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100"
        >
          {logContent || "（暂无输出）"}
        </pre>
      </SheetContent>
    </Sheet>
  );
}
