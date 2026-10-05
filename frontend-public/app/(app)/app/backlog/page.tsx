"use client"
/* eslint-disable react-hooks/set-state-in-effect */

// Backlog 页面：左侧受管理仓库栏 + 右侧 PRD 画布（依赖图/时间轴/列表）。

import { useCallback, useEffect, useState } from "react";
import { Settings } from "lucide-react";
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
import { BacklogAutopilotControl } from "@/components/backlog/backlog-autopilot-control";
import { BacklogCiControl } from "@/components/backlog/backlog-ci-control";
import { PrdCiView } from "@/components/backlog/prd-ci-view";
import { BacklogGraph } from "@/components/backlog/backlog-graph";
import { BacklogList } from "@/components/backlog/backlog-list";
import { BacklogTimeline } from "@/components/backlog/backlog-timeline";
import { RepositoryAgentMatrixSheet } from "@/components/agent-runner/repository-agent-matrix-sheet";
import { cn } from "@/lib/utils";
import { fetchRegistryRepositories } from "@/lib/api/console";
import {
  fetchBacklogAutopilot,
  fetchBacklogCiSettings,
  fetchBacklogPrds,
  fetchBacklogSettings,
  startGlobalBacklog,
  startBacklogPrd,
  stopGlobalBacklog,
  updateBacklogAutopilot,
  updateBacklogCiSettings,
  updateBacklogSettings,
} from "@/lib/api/backlog";
import type {
  RegistryRepositoryEntry,
  BacklogAutopilotState,
  BacklogCiAutoRepairState,
  BacklogPrd,
  BacklogSettings,
} from "@/lib/api/types";

const POLL_INTERVAL_MS = 30000;

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

export default function BacklogPage() {
  const [prds, setPrds] = useState<BacklogPrd[]>([]);
  const [loading, setLoading] = useState(true);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [repositories, setRepositories] = useState<RegistryRepositoryEntry[]>([]);
  const [reposLoading, setReposLoading] = useState(true);
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
  // CI 自动修复是独立于 Autopilot 的第 4 个开关，状态与 Autopilot 各读各的。
  const [ciSettings, setCiSettings] = useState<BacklogCiAutoRepairState | null>(null);
  const [ciLoading, setCiLoading] = useState(true);
  const [ciSaving, setCiSaving] = useState(false);
  // 打开仓库级生命周期 Agent 矩阵抽屉的仓库 id（null 表示关闭）。
  const [matrixRepoId, setMatrixRepoId] = useState<string | null>(null);

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
        setPrds(response.prds);
        return response.prds;
      } catch (error) {
        if (signal?.aborted) {
          return [];
        }
        toast.error(error instanceof Error ? error.message : "加载 Backlog 失败。");
        // 失败时清空列表：否则上一次成功的结果会一直挂在依赖图上，冒充当前仓库
        // 的 PRD（旧仓库停用后查询变 4xx，这个分支就会长期触发）。
        setPrds([]);
        return [];
      }
    },
    [selectedRepoId, includeArchived],
  );

  useEffect(() => {
    setReposLoading(true);
    fetchRegistryRepositories()
      .then((loadedRepositories) => {
        setRepositories(loadedRepositories);
        const enabledRepositories = loadedRepositories.filter((repo) => repo.enabled);
        if (enabledRepositories.length === 0) {
          return;
        }
        const preferred =
          enabledRepositories.find((repo) => repo.repo_id === "keda-main") ??
          enabledRepositories[0];
        setSelectedRepoId((current) => current || preferred.repo_id);
      })
      .catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "加载仓库列表失败。");
      })
      .finally(() => setReposLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedRepoId) {
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void loadData(controller.signal).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    });
    const timer = setInterval(() => void loadData(controller.signal), POLL_INTERVAL_MS);
    return () => {
      // 切换仓库或卸载时中止在途请求，旧仓库的慢响应不能再写回 prds。
      controller.abort();
      clearInterval(timer);
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

  const loadCiSettings = useCallback(async () => {
    if (!selectedRepoId) {
      return;
    }
    try {
      setCiSettings(await fetchBacklogCiSettings(selectedRepoId));
    } catch (error) {
      // 与 Autopilot 同理：CI 设置读不到不应该让整个页面失效，保留旧值并提示一次。
      toast.error(error instanceof Error ? error.message : "加载 CI 自动修复设置失败。");
    }
  }, [selectedRepoId]);

  useEffect(() => {
    if (!selectedRepoId) {
      return;
    }
    setCiLoading(true);
    void loadCiSettings().finally(() => setCiLoading(false));
    // 生效值可能被 CLI 或 daemon 改掉，必须按同一节奏 fresh 读取，不读前端缓存。
    const timer = setInterval(() => void loadCiSettings(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [loadCiSettings, selectedRepoId]);

  /**
   * 切换仓库级 CI 自动修复开关，并用服务端回写的生效快照覆盖本地状态。
   *
   * @param enabled - 目标开关值。
   */
  async function handleToggleCiAutoRepair(enabled: boolean) {
    setCiSaving(true);
    try {
      // 写后返回的是服务端 fresh load 的生效配置，不做乐观 UI 覆盖。
      setCiSettings(
        await updateBacklogCiSettings({ repoId: selectedRepoId, autoRepairCi: enabled }),
      );
      toast.success(
        enabled
          ? "已保存：CI 自动修复开启，仅作用于 Supervisor 选出的 repair_pr_branch。"
          : "已保存：CI 自动修复关闭，CI 问题只做展示与人工处理。",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存 CI 自动修复设置失败。");
      await loadCiSettings();
    } finally {
      setCiSaving(false);
    }
  }

  async function handleToggleAutopilot(enabled: boolean) {
    setAutopilotSaving(true);
    try {
      // 响应体是写后 fresh load 的生效配置，不做乐观 UI 覆盖。
      const updated = await updateBacklogAutopilot({ repoId: selectedRepoId, enabled });
      setAutopilot(updated);
      toast.success(
        enabled
          ? "已保存：Autopilot 开启，将在下一轮 daemon 生效。"
          : "已保存：Autopilot 关闭。",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存 Autopilot 设置失败。");
      await loadAutopilot();
    } finally {
      setAutopilotSaving(false);
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
      const updated = await updateBacklogSettings({
        repoId: selectedRepoId,
        maxParallel: settings.max_parallel,
        defaultView: nextView,
      });
      setSettings(updated);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存视图设置失败。");
    }
  }

  async function handleStart(prd: BacklogPrd) {
    setStartingPath(prd.prd_path);
    try {
      await startBacklogPrd(selectedRepoId, prd.prd_path);
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
    }
  }

  async function handleStartGlobal() {
    if (!settings) {
      toast.warning("设置尚未加载。");
      return;
    }
    setGlobalStarting(true);
    try {
      const result = await startGlobalBacklog({
        repoId: selectedRepoId,
        maxParallel: settings.max_parallel,
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
      <aside className="flex w-60 shrink-0 flex-col overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
        <div className="border-b border-slate-200 px-3 py-2 text-xs font-medium text-slate-500 dark:border-slate-800">
          受管理仓库
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
                    setSelectedRepoId(repo.repo_id);
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

      <section className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
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
          <div className="flex-1" />
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
          state={autopilot}
          loading={autopilotLoading}
          saving={autopilotSaving}
          onToggle={(enabled) => void handleToggleAutopilot(enabled)}
        />

        <BacklogCiControl
          state={ciSettings}
          loading={ciLoading}
          saving={ciSaving}
          onToggle={(enabled) => void handleToggleCiAutoRepair(enabled)}
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
            ) : view === "graph" ? (
              <BacklogGraph prds={visiblePrds} onOpenContent={setSelectedPrd} />
            ) : view === "timeline" ? (
              <BacklogTimeline
                prds={visiblePrds}
                onStart={(prd) => void handleStart(prd)}
                onOpenContent={setSelectedPrd}
                startingPath={startingPath}
              />
            ) : (
              <BacklogList
                prds={visiblePrds}
                onStart={(prd) => void handleStart(prd)}
                onOpenContent={setSelectedPrd}
                startingPath={startingPath}
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
                additionalTabs={
                  // CI/CD 只针对已有 Issue（因而可能有远端 PR）的 PRD；未启动的
                  // PRD 没有可观察的 checks，不显示这个标签。
                  selectedPrd.issue_number
                    ? [
                        {
                          id: "ci",
                          label: "CI/CD",
                          render: () => (
                            <PrdCiView
                              key={selectedPrd.prd_path}
                              repoId={selectedRepoId}
                              prdPath={selectedPrd.prd_path}
                              initialDelivery={selectedPrd.ci_delivery}
                            />
                          ),
                        },
                      ]
                    : []
                }
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
    </>
  );
}
