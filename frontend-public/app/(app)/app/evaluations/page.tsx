"use client";

// 模型评测页：校准候选、重复执行与可复现基线观察。
//
// 关键红线（与后端一致）：创建 run 只做校验与快照冻结；启动执行不触发评审；
// 评审 payload 必须先预览、后显式授权；失败/拒绝/未授权状态原样展示，
// 不折算为 0 分。

import { ConsolePage, ConsolePageHeader, ConsoleToolbar } from "@/components/layout/console-page";
import { NativeSelect } from "@/components/ui/native-select";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ConsentPreviewDialog } from "@/components/evaluations/consent-preview-dialog";
import { RunDetailPanel } from "@/components/evaluations/run-detail-panel";
import { RUN_STATUS_META } from "@/components/evaluations/evaluation-format";
import { confirmAction } from "@/lib/confirm-action";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  cancelEvaluationRun,
  createEvaluationRun,
  deleteEvaluationRun,
  fetchEvaluationPresetOptions,
  fetchEvaluationRunDetail,
  fetchEvaluationRuns,
  fetchEvaluationSuites,
  startEvaluationRun,
} from "@/lib/api/console";
import type {
  EvaluationPresetOptions,
  EvaluationRun,
  EvaluationRunDetail,
  EvaluationSuiteEntry,
} from "@/lib/api/types";
import { formatLocalDateTime } from "@/lib/utils";

/** 活跃状态需要持续轮询刷新（awaiting_judge_consent 等授权期间也要跟踪后端变化）。 */
const POLLING_STATUSES = new Set([
  "queued",
  "running",
  "awaiting_judge_consent",
  "judging",
]);

/** 后端保留的校准候选名；其他以 @ 开头的预设仍是普通 Agent 预设。 */
const CALIBRATION_PRESET_NAMES = new Set(["@noop", "@gold"]);

/** 评测页：suite/预设选择、run 列表与详情、外发授权入口。 */
export default function EvaluationsPage() {
  const [suites, setSuites] = useState<EvaluationSuiteEntry[] | null>(null);
  const [presetOptions, setPresetOptions] =
    useState<EvaluationPresetOptions | null>(null);
  const [runs, setRuns] = useState<EvaluationRun[] | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EvaluationRunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  // 详情加载中 = 已选择 run 但尚未拿到该 run 的明细（避免 effect 内同步 setState）。
  const detailLoading =
    selectedRunId !== null && detail?.run.run_id !== selectedRunId;

  const [suiteId, setSuiteId] = useState("");
  const [executionPresetNames, setExecutionPresetNames] = useState<string[]>([]);
  const [judgePresetNames, setJudgePresetNames] = useState<string[]>([]);
  const [repeats, setRepeats] = useState(1);
  const [includeHoldout, setIncludeHoldout] = useState(false);

  // ── 数据加载 ────────────────────────────────────────────────────────────

  useEffect(() => {
    fetchEvaluationSuites()
      .then(setSuites)
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载评测集清单。"
        );
        setSuites([]);
      });
    fetchEvaluationPresetOptions()
      .then((options) => {
        setPresetOptions(options);
        // 默认评审预设只预选「目录在场且凭据可用」的项：不可用预设的勾选框是
        // 禁用的，全量预选会留下一个既无法提交也无法取消的死锁选项。
        const selectableJudges = new Set(
          options.judge_presets
            .filter((preset) => preset.available)
            .map((preset) => preset.name)
        );
        setJudgePresetNames(
          options.default_judge_presets.filter((name) => selectableJudges.has(name))
        );
      })
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载评测预设目录。"
        );
        setPresetOptions({
          execution_presets: [],
          judge_presets: [],
          default_judge_presets: [],
          max_concurrency: 1,
          agent_timeout_seconds: 0,
          judge_timeout_seconds: 0,
        });
      });
  }, []);

  const refreshRuns = useCallback((): Promise<EvaluationRun[]> => {
    return fetchEvaluationRuns()
      .then((nextRuns) => {
        setRuns(nextRuns);
        return nextRuns;
      })
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载评测 run 列表。"
        );
        return [] as EvaluationRun[];
      });
  }, []);

  useEffect(() => {
    refreshRuns().then((nextRuns) => {
      if (nextRuns.length > 0) {
        setSelectedRunId((current) => current ?? nextRuns[0].run_id);
      }
    });
  }, [refreshRuns]);

  const loadDetail = useCallback((runId: string) => {
    fetchEvaluationRunDetail(runId)
      .then((nextDetail) => {
        // 只接受仍与当前选择一致的响应，避免快速切换 run 时的乱序覆盖。
        if (nextDetail.run.run_id === runId) {
          setDetail(nextDetail);
        }
      })
      .catch((error: unknown) => {
        toast.error(
          error instanceof Error ? error.message : "无法加载 run 详情。"
        );
      });
  }, []);

  useEffect(() => {
    if (selectedRunId) {
      loadDetail(selectedRunId);
    }
  }, [selectedRunId, loadDetail]);

  // 活跃 run 轮询：列表与详情一起刷新，状态收敛后自动停止。
  useEffect(() => {
    const hasActive = (runs ?? []).some((run) =>
      POLLING_STATUSES.has(run.status)
    );
    if (!hasActive) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshRuns();
      if (selectedRunId) {
        loadDetail(selectedRunId);
      }
    }, 5000);
    return () => {
      window.clearInterval(timer);
    };
  }, [runs, selectedRunId, refreshRuns, loadDetail]);

  // ── 动作 ────────────────────────────────────────────────────────────────

  const validSuites = useMemo(
    () => (suites ?? []).filter((entry) => entry.valid),
    [suites]
  );
  const selectedSuite = validSuites.find((entry) => entry.suite_id === suiteId);
  const isCalibrationRun = executionPresetNames.some((name) =>
    CALIBRATION_PRESET_NAMES.has(name),
  );
  const selectedCaseCount = (selectedSuite?.cases ?? []).filter(
    (evaluationCase) => isCalibrationRun || includeHoldout || evaluationCase.split !== "holdout",
  ).length;
  const estimatedCandidateExecutions =
    selectedCaseCount * executionPresetNames.length * repeats;
  const executionLimit = isCalibrationRun ? 144 : 48;

  const toggle = (list: string[], name: string): string[] =>
    list.includes(name) ? list.filter((item) => item !== name) : [...list, name];

  /** 更新候选选择；校准模式始终成对选择并排除真实 Agent。 */
  const toggleExecutionPreset = (presetName: string) => {
    const calibrationPresetNames = [...CALIBRATION_PRESET_NAMES];
    const nextPresetNames = CALIBRATION_PRESET_NAMES.has(presetName)
      ? executionPresetNames.includes(presetName)
        ? executionPresetNames.filter((name) => !calibrationPresetNames.includes(name))
        : [...calibrationPresetNames]
      : toggle(
          executionPresetNames.filter((name) => !calibrationPresetNames.includes(name)),
          presetName,
        );
    setExecutionPresetNames(nextPresetNames);
    if (calibrationPresetNames.some((name) => nextPresetNames.includes(name))) {
      setJudgePresetNames([]);
      setRepeats((currentRepeats) => Math.max(3, currentRepeats));
    }
  };

  const create = async () => {
    if (!suiteId || executionPresetNames.length === 0) {
      toast.error("请选择评测集并至少勾选一个执行预设。");
      return;
    }
    setBusy(true);
    try {
      const run = await createEvaluationRun({
        suite_id: suiteId,
        execution_presets: executionPresetNames,
        judge_presets: isCalibrationRun ? [] : judgePresetNames,
        repeats,
        include_holdout: includeHoldout || isCalibrationRun,
      });
      toast.success(
        `run ${run.run_id} 已创建，预计执行 ${run.estimated_candidate_executions ?? estimatedCandidateExecutions} 次（尚未启动）。`,
      );
      await refreshRuns();
      setSelectedRunId(run.run_id);
    } catch (error: unknown) {
      toast.error(
        error instanceof Error ? error.message : "创建评测 run 失败。"
      );
    } finally {
      setBusy(false);
    }
  };

  // detailRunId 显式传入而非读闭包：删除当前选中 run 时，闭包里的 selectedRunId
  // 仍是删除前的值，照它刷新会对已消失的 run 发一次注定 404 的详情请求。
  const runAction = async (
    action: () => Promise<unknown>,
    successText: string,
    detailRunId: string | null = selectedRunId
  ) => {
    setBusy(true);
    try {
      await action();
      toast.success(successText);
    } catch (error: unknown) {
      toast.error(
        error instanceof Error ? error.message : "操作失败（状态可能已变化）。"
      );
    } finally {
      setBusy(false);
      await refreshRuns();
      if (detailRunId) {
        loadDetail(detailRunId);
      }
    }
  };

  const removeRun = async (runId: string) => {
    // 删除不可逆（DB 行 + 本机产物），先确认再动手。
    const confirmed = await confirmAction(
      `确认删除 run ${runId}？其 DB 记录与本机暂存产物将一并清除。`,
    );
    if (!confirmed) {
      return;
    }
    if (selectedRunId === runId) {
      setSelectedRunId(null);
      // 详情面板也要一起清空：否则页面上会继续显示一条已不存在的 run。
      setDetail(null);
    }
    await runAction(
      () => deleteEvaluationRun(runId),
      `run ${runId} 已删除。`,
      selectedRunId === runId ? null : selectedRunId
    );
  };

  // ── 渲染 ────────────────────────────────────────────────────────────────

  return (
    <ConsolePage>
      <ConsolePageHeader title="模型评测" description="在隔离快照中校准并重复运行任务，逐题查看判分结果与稳定性。外发评审仍需先预览 payload 并显式授权。" />

      <Card className="gap-4">
        <CardHeader>
          <CardTitle className="text-sm">新建评测 run</CardTitle>
          <CardDescription>
            创建只做校验与快照冻结；启动执行也不会发起任何评审调用，评审在
            候选完成后由你显式授权。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ConsoleToolbar>
            <label className="text-sm font-medium" htmlFor="evaluation-suite-select">
              评测集
            </label>
            <NativeSelect
              id="evaluation-suite-select"
              className="min-w-56"
              value={suiteId}
              onChange={(event) => setSuiteId(event.target.value)}
            >
              <option value="">选择 suite…</option>
              {validSuites.map((entry) => (
                <option key={entry.suite_id} value={entry.suite_id}>
                  {entry.suite_id} · v{entry.version} ·{" "}
                  {entry.cases?.length ?? 0} cases
                </option>
              ))}
            </NativeSelect>
            {suites === null ? <Skeleton className="h-5 w-24" /> : null}
            {suites !== null && validSuites.length === 0 ? (
              <Badge variant="warning">
                registry 内没有校验通过的 suite（检查 tasks/evaluation-suites/）
              </Badge>
            ) : null}
          </ConsoleToolbar>
          {selectedSuite ? (
            <p className="rounded-lg bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              {selectedSuite.title} · 仓库 {selectedSuite.repo_path} · 基线{" "}
              {selectedSuite.base_ref} · 内容 hash{" "}
              <code>{selectedSuite.content_hash?.slice(0, 12)}…</code>
              {selectedSuite.calibration?.required ? (
                <span className="ml-2 font-medium text-foreground">
                  · {selectedSuite.calibration.calibrated_count}/{selectedSuite.calibration.case_count} 已校准
                </span>
              ) : null}
              {selectedSuite.calibration?.required ? (
                <span className="mt-2 block">
                  {selectedSuite.calibration.cases
                    .map((caseStatus) => `${caseStatus.case_id}：${caseStatus.calibrated ? "校准通过" : "未校准"}`)
                    .join("；")}
                </span>
              ) : null}
              {selectedSuite.calibration?.required ? (
                <span className="mt-1 block" data-testid="evaluation-suite-calibration-summary">
                  校准状态：{selectedSuite.calibration.calibrated_count}/{selectedSuite.calibration.case_count} 已校准
                </span>
              ) : null}
              {(suites ?? [])
                .filter((entry) => !entry.valid)
                .map((entry) => (
                  <span key={entry.file} className="mt-1 block text-red-700 dark:text-red-400">
                    {entry.file} 校验失败：{entry.errors.join("；")}
                  </span>
                ))}
            </p>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-sm font-medium">
                执行预设（候选，勾选 {executionPresetNames.length} 个）
              </p>
              {presetOptions === null ? (
                <Skeleton className="h-20" />
              ) : presetOptions.execution_presets.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  未定义 [agent_runner.presets.*]，先在 config.toml 声明执行预设。
                </p>
              ) : (
                <ul className="space-y-2">
                  {presetOptions.execution_presets.map((preset) => (
                    <li key={preset.name} className="flex items-start gap-2 text-sm">
                      <Checkbox
                        className="mt-0.5"
                        checked={executionPresetNames.includes(preset.name)}
                        onCheckedChange={() => toggleExecutionPreset(preset.name)}
                        aria-label={`执行预设 ${preset.name}`}
                      />
                      <span>
                        {preset.name}
                        {preset.kind === "calibration" ? (
                          <Badge variant="supervising" className="ml-2 text-[10px]">
                            校准用 · 不调用模型
                          </Badge>
                        ) : null}
                        <span className="block text-xs text-muted-foreground">
                          {preset.kind === "calibration"
                            ? "不改动 / 应用原始修复"
                            : `agent ${preset.agent ?? "?"} · model ${preset.model ?? "跟随默认"}`}
                          {preset.reasoning_effort
                            ? ` · effort ${preset.reasoning_effort}`
                            : ""}
                          {preset.kind !== "calibration" ? " · 费用 unknown（未接费用源）" : ""}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <p className="mb-2 text-sm font-medium">
                评审预设（judge，勾选 {judgePresetNames.length} 个）
              </p>
              {presetOptions === null ? (
                <Skeleton className="h-20" />
              ) : presetOptions.judge_presets.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  未定义任何预设（[agent_runner.presets.*]）；本轮只做
                  确定性检查对照。
                </p>
              ) : (
                <ul className="space-y-2">
                  {presetOptions.judge_presets.map((preset) => (
                    <li key={preset.name} className="flex items-start gap-2 text-sm">
                      <Checkbox
                        className="mt-0.5"
                        checked={judgePresetNames.includes(preset.name)}
                        disabled={!preset.available || isCalibrationRun}
                        onCheckedChange={() =>
                          setJudgePresetNames((current) =>
                            toggle(current, preset.name)
                          )
                        }
                        aria-label={`评审预设 ${preset.name}`}
                      />
                      <span>
                        {preset.name}
                        {!preset.available ? (
                          <Badge variant="warning" className="ml-2 text-[10px]">
                            不可用
                          </Badge>
                        ) : null}
                        <span className="block text-xs text-muted-foreground">
                          {preset.agent}
                          {preset.model ? ` · ${preset.model}` : ""}
                          {preset.reasoning_effort
                            ? ` · ${preset.reasoning_effort}`
                            : ""}
                          {preset.reason ? ` · ${preset.reason}` : ""}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm" htmlFor="evaluation-repeats">
              重复次数
              <NativeSelect
                id="evaluation-repeats"
                className="h-8 min-w-20"
                value={repeats}
                onChange={(event) => setRepeats(Number(event.target.value))}
              >
                {[1, 2, 3, 4, 5]
                  .filter((count) => !isCalibrationRun || count >= 3)
                  .map((count) => (
                    <option key={count} value={count}>{count}</option>
                  ))}
              </NativeSelect>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={includeHoldout || isCalibrationRun}
                disabled={isCalibrationRun}
                onCheckedChange={(checked) => setIncludeHoldout(checked === true)}
                aria-label="包含留出集"
              />
              包含留出集
            </label>
            {isCalibrationRun && (
              <span className="text-xs text-muted-foreground">
                校准自动包含开发集与留出集
              </span>
            )}
            <p className="text-xs text-muted-foreground">
              预计候选执行 {estimatedCandidateExecutions} 次（上限 {executionLimit}）；agent 超时{" "}
              {presetOptions?.agent_timeout_seconds ?? "—"}s，评审超时{" "}
              {presetOptions?.judge_timeout_seconds ?? "—"}s。
            </p>
            <Button
              data-testid="evaluation-create"
              disabled={
                busy ||
                !suiteId ||
                executionPresetNames.length === 0 ||
                (isCalibrationRun &&
                  (!executionPresetNames.includes("@noop") ||
                    !executionPresetNames.includes("@gold")))
              }
              onClick={() => void create()}
            >
              创建 run
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="gap-4">
        <CardHeader>
          <CardTitle className="text-sm">评测 run</CardTitle>
          <CardDescription>
            点击行选择查看下方详情；活跃 run 每 5 秒自动刷新。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {runs === null ? (
            <Skeleton className="h-24" />
          ) : runs.length === 0 ? (
            <p className="text-sm text-slate-500">还没有评测 run。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="rounded-l-lg px-3 py-2 font-medium">Run</th>
                    <th className="px-3 py-2 font-medium">Suite</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                    <th className="px-3 py-2 font-medium">执行 / 评审</th>
                    <th className="rounded-r-lg px-3 py-2 font-medium">创建时间</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => {
                    const meta = RUN_STATUS_META[run.status];
                    return (
                      <tr
                        key={run.run_id}
                        onClick={() => setSelectedRunId(run.run_id)}
                        className={`cursor-pointer border-b last:border-b-0 ${
                          run.run_id === selectedRunId ? "bg-violet-50/60 dark:bg-violet-950/30" : ""
                        }`}
                      >
                        <td className="px-3 py-2 font-mono text-xs">
                          {run.run_id}
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {run.suite_id} v{run.suite_version}
                        </td>
                        <td className="px-3 py-2">
                          {meta ? (
                            <Badge variant={meta.variant}>{meta.label}</Badge>
                          ) : (
                            <Badge>{run.status}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {run.execution_preset_snapshots
                            .map((preset) => preset.preset_name)
                            .join(", ")}
                          {" · "}
                          {run.judge_preset_snapshots.length === 0
                            ? "无评审"
                            : run.judge_preset_snapshots
                                .map((preset) => preset.preset_name)
                                .join(", ")}
                        </td>
                        <td className="px-3 py-2 text-xs tabular-nums">
                          {formatLocalDateTime(run.created_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {selectedRunId ? (
        <RunDetailPanel
          detail={detail}
          loading={detailLoading}
          busy={busy}
          onStart={() =>
            void runAction(
              () => startEvaluationRun(selectedRunId),
              "候选执行已启动。"
            )
          }
          onCancel={() =>
            void runAction(
              () => cancelEvaluationRun(selectedRunId),
              "run 已取消。"
            )
          }
          onDelete={() => void removeRun(selectedRunId)}
          onOpenConsent={() => setConsentOpen(true)}
          onRefresh={() => {
            void refreshRuns();
            loadDetail(selectedRunId);
          }}
        />
      ) : null}

      <ConsentPreviewDialog
        runId={selectedRunId}
        open={consentOpen}
        onOpenChange={setConsentOpen}
        onDecided={() => {
          void refreshRuns();
          if (selectedRunId) {
            loadDetail(selectedRunId);
          }
        }}
      />
    </ConsolePage>
  );
}
