"use client"
/* eslint-disable react-hooks/set-state-in-effect */

// Backlog 页面：左侧受管理仓库栏 + 右侧 PRD 画布（依赖图/时间轴/列表）。

import { useCallback, useEffect, useRef, useState } from "react";
import { PanelLeftClose, PanelLeftOpen, Settings } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { PrdDetail } from "@/components/backlog/prd-detail";
import { PRD_CI_TAB_ID, PrdCiView } from "@/components/backlog/prd-ci-view";
import { PrdStartOptionsSheet } from "@/components/backlog/prd-start-options-sheet";
import { CreateIssueDialog } from "@/components/backlog/create-issue-dialog";
import { BacklogAutopilotControl } from "@/components/backlog/backlog-autopilot-control";
import { BacklogCiRepairControl } from "@/components/backlog/backlog-ci-repair-control";
import { BacklogGraph } from "@/components/backlog/backlog-graph";
import { BacklogList } from "@/components/backlog/backlog-list";
import { BacklogTimeline } from "@/components/backlog/backlog-timeline";
import { RepositoryAgentMatrixSheet } from "@/components/agent-runner/repository-agent-matrix-sheet";
import { cn, formatLocalClockTime } from "@/lib/utils";
import { useRepositorySelection } from "@/lib/console-repository-selection";
import {
  CONSOLE_REPO_PANEL_COLLAPSED_KEY,
  usePersistedBoolean,
} from "@/lib/console-ui-prefs";
import {
  enqueueBacklogPrdReady,
  fetchBacklogAutopilot,
  fetchBacklogCiRepairGlobal,
  fetchBacklogPrds,
  fetchBacklogSettings,
  startGlobalBacklog,
  startBacklogPrd,
  stopGlobalBacklog,
  updateBacklogAutopilot,
  updateBacklogCiRepairGlobal,
  updateBacklogSettings,
} from "@/lib/api/backlog";
import type {
  RegistryRepositoryEntry,
  BacklogAutopilotState,
  BacklogCiRepairGlobalState,
  BacklogPrd,
  BacklogSettings,
  StartPrdLaunchOptions,
} from "@/lib/api/types";

const POLL_INTERVAL_MS = 30000;
// 快照过期（后端 stale=true）时的追平节奏：后台重扫通常几秒内落库，30 秒要等太久。
const STALE_POLL_INTERVAL_MS = 3000;

type BacklogView = "graph" | "timeline" | "list";

const VIEW_LABELS: Record<BacklogView, string> = {
  graph: "依赖图",
  timeline: "时间轴",
  list: "列表",
};

/**
 * 仓库状态点的颜色：绿 = 启用且路径存在，黄 = 启用但路径缺失，灰 = 未启用。
 *
 * @param repo - 注册表中的仓库条目。
 * @returns 状态点的 Tailwind 背景色类名。
 */
function repoStatusDotClass(repo: RegistryRepositoryEntry): string {
  if (!repo.enabled) {
    return "bg-slate-400";
  }
  return repo.path_exists ? "bg-emerald-500" : "bg-amber-500";
}

/**
 * 快照新鲜度提示：数据截至时间，过期时追加「后台更新中」。
 *
 * @param props - 快照时间与新鲜度标记。
 * @param props.scannedAt - 快照构建时间；null 表示还没有快照。
 * @param props.stale - 快照是否已超过后端 TTL 并正在后台重扫。
 * @param props.loadFailed - 最近一次列表读取是否失败。
 * @returns 表头右侧的新鲜度文案。
 */
function SnapshotFreshnessLabel(props: {
  scannedAt: string | null;
  stale: boolean;
  loadFailed: boolean;
}) {
  const { scannedAt, stale, loadFailed } = props;
  if (loadFailed) {
    return <span className="text-xs text-rose-500">加载失败</span>;
  }
  if (!scannedAt) {
    return <span className="text-xs text-slate-400 dark:text-slate-500">正在同步…</span>;
  }
  return (
    <span className="text-xs text-slate-400 dark:text-slate-500">
      {`数据截至 ${formatLocalClockTime(scannedAt)}`}
      {stale ? " · 后台更新中…" : ""}
    </span>
  );
}

export default function BacklogPage() {
  const [prds, setPrds] = useState<BacklogPrd[]>([]);
  const [loading, setLoading] = useState(true);
  // 列表数据来自后端本地快照：scannedAt 为 null 表示还没有任何快照（首屏「正在同步」），
  // stale 为 true 表示这份数据已过期且后台正在重扫，需要按短节奏追平。
  const [snapshotScannedAt, setSnapshotScannedAt] = useState<string | null>(null);
  const [snapshotStale, setSnapshotStale] = useState(false);
  const [snapshotLoadFailed, setSnapshotLoadFailed] = useState(false);
  // 轮询节奏用 ref 传递：把 stale 放进 effect 依赖会让每次新鲜度翻转都重建整个
  // 加载流程，表现为列表反复闪骨架屏。
  const snapshotStaleRef = useRef(false);
  // 入队成功后，快照重扫完成前保留动作接口确认的状态，避免旧快照把 UI 改回未开始。
  const optimisticEnqueueUpdatesRef = useRef(
    new Map<
      string,
      {
        state: BacklogPrd["state"];
        issueNumber: number | null;
      }
    >(),
  );
  const [includeArchived, setIncludeArchived] = useState(false);
  const {
    repositories,
    selectedRepoId,
    selectRepoId,
    loading: reposLoading,
  } = useRepositorySelection();
  const [settings, setSettings] = useState<BacklogSettings | null>(null);
  const [view, setView] = useState<BacklogView>("graph");
  const [startingPath, setStartingPath] = useState<string | null>(null);
  const [globalStarting, setGlobalStarting] = useState(false);
  // 选中 PRD 只在右侧开详情；三种视图共用同一份详情与启动规则，
  // 选择动作不再替换整个画布（依赖图上下文得以保留）。
  const [selectedPrd, setSelectedPrd] = useState<BacklogPrd | null>(null);
  const [autopilot, setAutopilot] = useState<BacklogAutopilotState | null>(null);
  const [autopilotLoading, setAutopilotLoading] = useState(true);
  const [autopilotSaving, setAutopilotSaving] = useState(false);
  const [policySaving, setPolicySaving] = useState(false);
  // 全局 CI/CD 自动修复开关：与 Autopilot 并列、语义独立的仓库级偏好。
  const [ciRepair, setCiRepair] = useState<BacklogCiRepairGlobalState | null>(null);
  const [ciRepairLoading, setCiRepairLoading] = useState(true);
  const [ciRepairSaving, setCiRepairSaving] = useState(false);
  // 打开仓库级生命周期 Agent 矩阵抽屉的仓库 id（null 表示关闭）。
  const [matrixRepoId, setMatrixRepoId] = useState<string | null>(null);
  // 「受管理仓库」栏收起偏好：默认展开，收起后记住选择（跨刷新）。
  const [repoPanelCollapsed, setRepoPanelCollapsed] = usePersistedBoolean(
    CONSOLE_REPO_PANEL_COLLAPSED_KEY,
  );
  // 「加入就绪」进行中的 PRD 路径（FR-3）。
  const [enqueuingPath, setEnqueuingPath] = useState<string | null>(null);
  // 「启动高级选项」抽屉的目标 PRD（null 表示关闭，FR-7）。
  const [startOptionsPrd, setStartOptionsPrd] = useState<BacklogPrd | null>(null);
  // 「一句话建 Issue」对话框开关（FR-8）。
  const [createIssueOpen, setCreateIssueOpen] = useState(false);

  const loadData = useCallback(
    async (signal?: AbortSignal): Promise<BacklogPrd[]> => {
      if (!selectedRepoId) {
        return [];
      }
      try {
        const response = await fetchBacklogPrds({
          repoId: selectedRepoId,
          includeArchived,
          signal,
        });
        // 仓库级扫描要逐个 PRD 查 GitHub，慢响应可能在新仓库的响应之后才落地；
        // 已中止说明这次结果属于上一个仓库，必须丢弃，否则依赖图会留下过期数据。
        if (signal?.aborted) {
          return [];
        }
        const refreshedPrds = response.prds.map((serverPrd) => {
          const optimisticUpdateKey = `${selectedRepoId}\u0000${serverPrd.prd_path}`;
          const optimisticUpdate = optimisticEnqueueUpdatesRef.current.get(optimisticUpdateKey);
          if (!optimisticUpdate) {
            return serverPrd;
          }
          if (serverPrd.state !== "not_started") {
            // 后端快照已经观察到入队或更后的状态，结束本地覆盖。
            optimisticEnqueueUpdatesRef.current.delete(optimisticUpdateKey);
            return serverPrd;
          }
          return {
            ...serverPrd,
            state: optimisticUpdate.state,
            issue_number: optimisticUpdate.issueNumber,
          };
        });
        const refreshedPrdPaths = new Set(response.prds.map((serverPrd) => serverPrd.prd_path));
        const optimisticUpdateKeyPrefix = `${selectedRepoId}\u0000`;
        for (const optimisticUpdateKey of optimisticEnqueueUpdatesRef.current.keys()) {
          const optimisticPrdPath = optimisticUpdateKey.slice(optimisticUpdateKeyPrefix.length);
          if (
            optimisticUpdateKey.startsWith(optimisticUpdateKeyPrefix) &&
            !refreshedPrdPaths.has(optimisticPrdPath)
          ) {
            // PRD 已离开当前视图（例如被归档），清理对应的本地覆盖状态。
            optimisticEnqueueUpdatesRef.current.delete(optimisticUpdateKey);
          }
        }
        setPrds(refreshedPrds);
        setSnapshotScannedAt(response.scanned_at);
        setSnapshotStale(response.stale);
        setSnapshotLoadFailed(false);
        snapshotStaleRef.current = response.stale;
        return refreshedPrds;
      } catch (error) {
        if (signal?.aborted) {
          return [];
        }
        toast.error(error instanceof Error ? error.message : "加载 Backlog 失败。");
        // 失败时清空列表：否则上一次成功的结果会一直挂在依赖图上，冒充当前仓库
        // 的 PRD（旧仓库停用后查询变 4xx，这个分支就会长期触发）。新鲜度一并清空，
        // 不能继续报一个无人认领的「数据截至」时间。
        setPrds([]);
        setSnapshotScannedAt(null);
        setSnapshotStale(false);
        setSnapshotLoadFailed(true);
        snapshotStaleRef.current = false;
        return [];
      }
    },
    [selectedRepoId, includeArchived],
  );

  useEffect(() => {
    if (!selectedRepoId) {
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setSnapshotLoadFailed(false);
    let timer: ReturnType<typeof setTimeout> | undefined;
    // 用自排的 setTimeout 而不是固定 setInterval：节奏跟着最近一次响应的 stale
    // 走（3 秒追平 / 30 秒保活），而依赖数组保持不变，避免每次翻转都重建加载。
    const poll = async () => {
      await loadData(controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      setLoading(false);
      timer = setTimeout(
        () => void poll(),
        snapshotStaleRef.current ? STALE_POLL_INTERVAL_MS : POLL_INTERVAL_MS,
      );
    };
    void poll();
    return () => {
      // 切换仓库或卸载时中止在途请求，旧仓库的慢响应不能再写回 prds。
      controller.abort();
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [loadData, selectedRepoId]);

  const loadAutopilot = useCallback(async () => {
    if (!selectedRepoId) {
      return;
    }
    try {
      const loaded = await fetchBacklogAutopilot(selectedRepoId);
      setAutopilot(loaded);
    } catch (error) {
      // Autopilot 状态失败不应该让整个 Backlog 页不可用：保留旧值并提示一次。
      toast.error(error instanceof Error ? error.message : "加载 Autopilot 状态失败。");
    }
  }, [selectedRepoId]);

  const loadCiRepair = useCallback(async () => {
    if (!selectedRepoId) {
      return;
    }
    try {
      const loaded = await fetchBacklogCiRepairGlobal(selectedRepoId);
      setCiRepair(loaded);
    } catch (error) {
      // 全局修复开关读取失败不阻塞 Backlog 页：保留旧值并提示一次。
      toast.error(error instanceof Error ? error.message : "加载 CI/CD 自动修复状态失败。");
    }
  }, [selectedRepoId]);

  useEffect(() => {
    if (!selectedRepoId) {
      return;
    }
    setCiRepairLoading(true);
    void loadCiRepair().finally(() => setCiRepairLoading(false));
    // 与 PRD 列表共用 30 秒轮询节奏：配置在别处（CLI / 其他会话）被改动后，
    // 页面必须在下一轮刷新里真实出现新值。
    const timer = setInterval(() => void loadCiRepair(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [loadCiRepair, selectedRepoId]);

  useEffect(() => {
    if (!selectedRepoId) {
      return;
    }
    fetchBacklogSettings(selectedRepoId)
      .then((loadedSettings) => {
        setSettings(loadedSettings);
      })
      .catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "加载设置失败。");
      });
  }, [selectedRepoId]);

  useEffect(() => {
    if (!selectedRepoId) {
      return;
    }
    setAutopilotLoading(true);
    void loadAutopilot().finally(() => setAutopilotLoading(false));
    // Autopilot 状态与 PRD 列表共用 30 秒轮询节奏；daemon / 生效配置的变化
    // 必须在下一轮刷新里真实出现，而不是读前端缓存。
    const timer = setInterval(() => void loadAutopilot(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [loadAutopilot, selectedRepoId]);

  async function handleToggleAutopilot(enabled: boolean) {
    setAutopilotSaving(true);
    try {
      // 响应体是写后 fresh load 的生效配置，不做乐观 UI 覆盖。
      const updated = await updateBacklogAutopilot({ repoId: selectedRepoId, enabled });
      setAutopilot(updated);
      toast.success(
        enabled
          ? "已保存：Backlog 自动推进开启，将在下一轮 daemon 生效。"
          : "已保存：Backlog 自动推进关闭。",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存 Backlog 自动推进设置失败。");
      await loadAutopilot();
    } finally {
      setAutopilotSaving(false);
    }
  }

  /** 保存 Backlog「并发」策略（1–10），或以 ``null`` 恢复继承（清除设置行）。 */
  async function handleUpdatePolicy(maxParallel: number | null): Promise<boolean> {
    setPolicySaving(true);
    try {
      // 响应体是写后 fresh 读回的设置快照，不做乐观 UI 覆盖。
      const updated = await updateBacklogSettings({ repoId: selectedRepoId, maxParallel });
      setSettings(updated);
      // 控制条三态来自 Autopilot 快照，写后一并 fresh 回读保持同源。
      await loadAutopilot();
      toast.success(
        maxParallel === null
          ? "已恢复继承：并发上限跟随 runner 配置。"
          : `已保存：生效并发上限 ${updated.effective_max_parallel}。`,
      );
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存并发设置失败。");
      await loadAutopilot();
      return false;
    } finally {
      setPolicySaving(false);
    }
  }

  async function handleToggleCiRepair(enabled: boolean) {
    setCiRepairSaving(true);
    try {
      // 响应体是写后 fresh load 的生效配置，不做乐观 UI 覆盖。
      const updated = await updateBacklogCiRepairGlobal({ repoId: selectedRepoId, enabled });
      setCiRepair(updated);
      toast.success(
        enabled
          ? "已保存：全局自动修复 CI/CD 开启，失败检查将按策略自动修复。"
          : "已保存：全局自动修复 CI/CD 关闭，失败检查只作为问题展示。",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存 CI/CD 自动修复设置失败。");
      await loadCiRepair();
    } finally {
      setCiRepairSaving(false);
    }
  }

  async function handleViewChange(nextView: BacklogView) {
    setView(nextView);
    // 后端 PATCH /backlog/settings 的 default_view 仅接受 timeline/list（路由层
    // pattern 校验），「依赖图」是纯前端默认视图，不向后端回写该值。
    if (nextView === "graph" || !settings || settings.default_view === nextView) {
      return;
    }
    try {
      // 视图切换只回写 default_view；并发策略是独立入口（控制条编辑器），
      // 不再被视图切换顺带改写。
      const updated = await updateBacklogSettings({
        repoId: selectedRepoId,
        defaultView: nextView,
      });
      setSettings(updated);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存视图设置失败。");
    }
  }

  async function handleStart(prd: BacklogPrd, options?: StartPrdLaunchOptions) {
    setStartingPath(prd.prd_path);
    try {
      await startBacklogPrd(selectedRepoId, prd.prd_path, options);
      toast.success(`${prd.title} 已开始。`);
      // fresh-state probe：启动成功后重新拉取列表，详情里的状态必须来自服务端
      // 而不是本地乐观值——否则队列/合并状态下的按钮可用性会说谎。
      const refreshedPrds = await loadData();
      const refreshedPrd = refreshedPrds.find((item) => item.prd_path === prd.prd_path);
      if (refreshedPrd) {
        setSelectedPrd(refreshedPrd);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动 PRD 失败。");
    } finally {
      setStartingPath(null);
      setStartOptionsPrd(null);
    }
  }

  /** 「加入就绪」：建 Issue + 打就绪标签但不启动 runner（FR-3）。 */
  async function handleEnqueueReady(prd: BacklogPrd) {
    setEnqueuingPath(prd.prd_path);
    try {
      const enqueueResult = await enqueueBacklogPrdReady(selectedRepoId, prd.prd_path);
      const optimisticUpdateKey = `${selectedRepoId}\u0000${prd.prd_path}`;
      optimisticEnqueueUpdatesRef.current.set(optimisticUpdateKey, {
        state: enqueueResult.state,
        issueNumber: enqueueResult.issue_number,
      });
      setPrds((currentPrds) =>
        currentPrds.map((currentPrd) =>
          currentPrd.prd_path === prd.prd_path
            ? {
                ...currentPrd,
                state: enqueueResult.state,
                issue_number: enqueueResult.issue_number,
              }
            : currentPrd,
        ),
      );
      setSelectedPrd((currentPrd) =>
        currentPrd?.prd_path === prd.prd_path
          ? {
              ...currentPrd,
              state: enqueueResult.state,
              issue_number: enqueueResult.issue_number,
            }
          : currentPrd,
      );
      toast.success(`${prd.title} 已加入就绪队列（未启动）。`);
      const refreshedPrds = await loadData();
      const refreshedPrd = refreshedPrds.find((item) => item.prd_path === prd.prd_path);
      if (refreshedPrd) {
        setSelectedPrd(refreshedPrd);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加入就绪失败。");
    } finally {
      setEnqueuingPath(null);
    }
  }

  async function handleStartGlobal() {
    setGlobalStarting(true);
    try {
      // 批量上限由服务端按「策略与容量」的生效值解析，请求体不再携带并发数。
      const result = await startGlobalBacklog({
        repoId: selectedRepoId,
      });
      toast.success(
        `全局开始完成：启动 ${result.started.length} 个，排队 ${result.queued.length} 个。`,
      );
      await loadData();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "全局开始失败。");
    } finally {
      setGlobalStarting(false);
    }
  }

  async function handleStopGlobal() {
    try {
      await stopGlobalBacklog(selectedRepoId);
      toast.success("已停止全局调度。");
      await loadData();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "停止全局调度失败。");
    }
  }

  const visiblePrds = includeArchived
    ? prds
    : prds.filter((prd) => prd.status === "pending");

  const matrixRepo = repositories.find((repo) => repo.repo_id === matrixRepoId);

  return (
    <>
    <div className="flex h-[calc(100svh-4rem)] gap-4">
      {repoPanelCollapsed ? null : (
        <aside className="flex w-60 shrink-0 flex-col overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5 dark:border-slate-800">
            <span className="text-xs font-medium text-slate-500">受管理仓库</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => setRepoPanelCollapsed(true)}
              aria-label="收起仓库栏"
              title="收起仓库栏"
            >
              <PanelLeftClose className="size-4" />
            </Button>
          </div>
          <div className="flex-1 space-y-0.5 overflow-y-auto p-1.5">
            {reposLoading ? (
              <div className="space-y-1.5 p-1">
                <Skeleton className="h-8" />
                <Skeleton className="h-8" />
                <Skeleton className="h-8" />
              </div>
            ) : repositories.length === 0 ? (
              <p className="p-2 text-xs text-slate-500">无可用仓库。</p>
            ) : (
              repositories.map((repo) => (
                <div key={repo.repo_id} className="flex items-center gap-1">
                  <button
                    type="button"
                    disabled={!repo.enabled}
                    onClick={() => {
                      selectRepoId(repo.repo_id);
                      setSelectedPrd(null);
                    }}
                    className={cn(
                      "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                      repo.repo_id === selectedRepoId
                        ? "bg-slate-100 font-medium dark:bg-slate-800"
                        : "hover:bg-slate-100 dark:hover:bg-slate-800",
                      !repo.enabled && "cursor-not-allowed opacity-50",
                    )}
                  >
                    <span
                      className={cn("h-2 w-2 shrink-0 rounded-full", repoStatusDotClass(repo))}
                      aria-hidden="true"
                    />
                    <span className="truncate" title={repo.display_name ?? repo.repo_id}>
                      {repo.display_name ?? repo.repo_id}
                    </span>
                  </button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="shrink-0"
                    onClick={() => setMatrixRepoId(repo.repo_id)}
                    data-testid={`repo-agent-gear-${repo.repo_id}`}
                    aria-label={`${repo.display_name ?? repo.repo_id} 生命周期 Agent 设置`}
                  >
                    <Settings className="size-4" />
                  </Button>
                </div>
              ))
            )}
          </div>
        </aside>
      )}

      <section className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {repoPanelCollapsed ? (
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              onClick={() => setRepoPanelCollapsed(false)}
              aria-label="展开仓库栏"
              title="展开仓库栏"
              data-testid="repo-panel-expand"
            >
              <PanelLeftOpen className="size-4" />
            </Button>
          ) : null}
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Backlog</h2>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(event) => setIncludeArchived(event.target.checked)}
            />
            显示已归档
          </label>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                视图：{VIEW_LABELS[view]}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuRadioGroup
                value={view}
                onValueChange={(value) => void handleViewChange(value as BacklogView)}
              >
                <DropdownMenuRadioItem value="graph">依赖图</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="timeline">时间轴</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="list">列表</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <span className="text-xs text-slate-500">
            {selectedPrd ? "PRD 详情" : `${visiblePrds.length} 个 PRD`}
          </span>
          <SnapshotFreshnessLabel
            scannedAt={snapshotScannedAt}
            stale={snapshotStale}
            loadFailed={snapshotLoadFailed}
          />
          <div className="flex-1" />
          <Button
            size="sm"
            variant="outline"
            onClick={() => setCreateIssueOpen(true)}
            data-testid="backlog-create-issue"
          >
            一句话建 Issue
          </Button>
          <Button
            size="sm"
            onClick={() => void handleStartGlobal()}
            disabled={globalStarting || !settings}
          >
            {globalStarting ? "全局启动中…" : "全局开始"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleStopGlobal()}
            disabled={globalStarting}
          >
            停止全局调度
          </Button>
        </div>

        <BacklogAutopilotControl
          // 按仓库重建本地组件，避免并发输入草稿跨仓库切换残留、被写到新选中仓库。
          key={selectedRepoId}
          state={autopilot}
          loading={autopilotLoading}
          saving={autopilotSaving}
          onToggle={(enabled) => void handleToggleAutopilot(enabled)}
          policySaving={policySaving}
          onUpdatePolicy={(maxParallel) => handleUpdatePolicy(maxParallel)}
        />

        <BacklogCiRepairControl
          state={ciRepair}
          loading={ciRepairLoading}
          saving={ciRepairSaving}
          onToggle={(enabled) => void handleToggleCiRepair(enabled)}
        />

        {/* master-detail：左侧保留当前 Backlog 视图（含依赖图上下文），右侧是
            统一的 PRD 详情；窄屏自动退化为上下堆叠，不引入 modal 或新路由。 */}
        <div
          className={cn(
            "grid min-h-0 flex-1 gap-3",
            selectedPrd && "lg:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_440px]",
          )}
        >
          <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            {loading ? (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
                <Skeleton className="h-40" />
                <Skeleton className="h-40" />
                <Skeleton className="h-40" />
              </div>
            ) : snapshotLoadFailed && visiblePrds.length === 0 ? (
              <div className="flex h-40 items-center justify-center text-sm text-rose-500">
                加载失败，请稍后重试。
              </div>
            ) : snapshotScannedAt === null && visiblePrds.length === 0 ? (
              // 还没有任何快照：这是后台首次扫描在途，不是"这个仓库没有 PRD"，
              // 也不是错误，因此用中性的同步空态而不是报错样式。
              <div className="flex h-40 items-center justify-center text-sm text-slate-500">
                正在同步…
              </div>
            ) : view === "graph" ? (
              <BacklogGraph prds={visiblePrds} onOpenContent={setSelectedPrd} />
            ) : view === "timeline" ? (
              <BacklogTimeline
                prds={visiblePrds}
                onStart={(prd) => void handleStart(prd)}
                onOpenContent={setSelectedPrd}
                startingPath={startingPath}
                onEnqueueReady={(prd) => void handleEnqueueReady(prd)}
                enqueuingPath={enqueuingPath}
              />
            ) : (
              <BacklogList
                prds={visiblePrds}
                onStart={(prd) => void handleStart(prd)}
                onOpenContent={setSelectedPrd}
                startingPath={startingPath}
                onEnqueueReady={(prd) => void handleEnqueueReady(prd)}
                enqueuingPath={enqueuingPath}
              />
            )}
          </div>

          {selectedPrd ? (
            <aside className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <div className="mb-2 flex shrink-0 items-center justify-between">
                <span className="text-xs font-medium text-slate-500">PRD 详情</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelectedPrd(null)}
                  data-testid="prd-detail-close"
                >
                  关闭
                </Button>
              </div>
              <PrdDetail
                key={selectedPrd.prd_path}
                repoId={selectedRepoId}
                prd={selectedPrd}
                starting={startingPath === selectedPrd.prd_path}
                onStart={(prd) => void handleStart(prd)}
                onEnqueueReady={(prd) => void handleEnqueueReady(prd)}
                enqueuing={enqueuingPath === selectedPrd.prd_path}
                onOpenStartOptions={(prd) => setStartOptionsPrd(prd)}
                additionalTabs={[
                  {
                    id: PRD_CI_TAB_ID,
                    label: "CI/CD",
                    render: () => (
                      <PrdCiView
                        repoId={selectedRepoId}
                        prdPath={selectedPrd.prd_path}
                        issueNumber={selectedPrd.issue_number}
                      />
                    ),
                  },
                ]}
              />
            </aside>
          ) : null}
        </div>
      </section>
    </div>

      {matrixRepoId ? (
        <RepositoryAgentMatrixSheet
          repoId={matrixRepoId}
          repoLabel={matrixRepo?.display_name ?? matrixRepoId}
          open
          onOpenChange={(nextOpen) => {
            if (!nextOpen) {
              setMatrixRepoId(null);
            }
          }}
        />
      ) : null}

      {startOptionsPrd ? (
        <PrdStartOptionsSheet
          repoId={selectedRepoId}
          open
          onOpenChange={(nextOpen) => {
            if (!nextOpen) {
              setStartOptionsPrd(null);
            }
          }}
          submitting={startingPath === startOptionsPrd.prd_path}
          onSubmit={(options) => void handleStart(startOptionsPrd, options)}
        />
      ) : null}

      <CreateIssueDialog
        repoId={selectedRepoId}
        open={createIssueOpen}
        onOpenChange={setCreateIssueOpen}
      />
    </>
  );
}
