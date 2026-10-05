"use client";

// PRD「CI/CD」标签页：原始 checks 事实 + 修复策略 + 一次性手动修复。
//
// 三条展示纪律：
// 1. **原始 checks 与结论分开**：`checks_state` / `problems` 是 GitHub 的事实，
//    `supervisor_action` / `last_decision` 是 Supervisor 与服务端策略的结论，两者
//    不混成一句话。`failing` 不等于"会自动修复"。
// 2. **effective 值只读不猜**：stored / global / effective 三个值都来自服务端，
//    前端不做 `override ?? global` 的合并，也不缓存推导结果。
// 3. **手动修复不提交 head**：修复请求只带 PRD 路径，head SHA 与 failure key 由
//    服务端 fresh 解析；请求在途时禁用按钮，避免重复提交。

import { useCallback, useEffect, useState } from "react";

import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchPrdCiDelivery, requestPrdCiRepair, updatePrdCiPolicy } from "@/lib/api/backlog";
import { formatLocalDateTime } from "@/lib/utils";
import type {
  BacklogCiRepairPolicy,
  CiDelivery,
  CiDeliveryStatus,
  CiProblemKind,
} from "@/lib/api/types";

/** CI 状态 -> 中文标签（`not_run` 与 `unavailable` 不得显示成通过或代码失败）。 */
export const CI_STATUS_LABELS: Record<CiDeliveryStatus, string> = {
  no_pr: "无关联 PR",
  unavailable: "PR 状态不可读",
  pending: "检查进行中",
  not_run: "未运行检查",
  failing: "存在未通过检查",
  passing: "检查全绿",
};

const CI_STATUS_VARIANTS: Record<CiDeliveryStatus, BadgeVariant> = {
  no_pr: "default",
  unavailable: "warning",
  pending: "supervising",
  not_run: "warning",
  failing: "failed",
  passing: "ready",
};

/** 问题类型 -> 中文标签。 */
export const CI_PROBLEM_KIND_LABELS: Record<CiProblemKind, string> = {
  check_failure: "检查未通过",
  check_pending: "检查未完成",
  aggregate_summary: "汇总失败",
  not_run: "未运行",
  unavailable: "无法读取",
};

/** 三态策略 -> 中文标签。 */
export const CI_POLICY_LABELS: Record<BacklogCiRepairPolicy, string> = {
  inherit: "跟随全局",
  on: "强制开启",
  off: "强制关闭",
};

const POLICY_OPTIONS: BacklogCiRepairPolicy[] = ["inherit", "on", "off"];

/** 服务端放行结论码 -> 中文说明（界面不再各自解释，只按码翻译）。 */
const DECISION_LABELS: Record<string, string> = {
  ci_repair_allowed: "已放行修复",
  ci_repair_duplicate: "同一失败已请求过修复（不再重复）",
  ci_failed_manual: "生效策略关闭：失败交人工处理",
  ci_repair_exhausted: "已达到修复上限：停止自动修复",
  ci_unresolved: "无法解析当前 PR/head/failure：拒绝修复",
};

type CiState =
  | { status: "loading" }
  | { status: "ready"; delivery: CiDelivery | null; reason: string }
  | { status: "error"; message: string };

interface PrdCiViewProps {
  repoId: string;
  prdPath: string;
  /** 列表响应里的投影，用于首屏即时展示；随后 fresh 读取覆盖。 */
  initialDelivery?: CiDelivery | null;
}

/**
 * 渲染某个 PRD 的「CI/CD」标签页。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 的仓库相对路径。
 * @param props.initialDelivery - 列表响应携带的投影（可能过期，仅用于首屏）。
 * @returns CI/CD 投影视图，含策略控制、问题列表与手动修复。
 */
export function PrdCiView({ repoId, prdPath, initialDelivery = null }: PrdCiViewProps) {
  const [ciState, setCiState] = useState<CiState>(() =>
    initialDelivery
      ? { status: "ready", delivery: initialDelivery, reason: "" }
      : { status: "loading" },
  );
  const [reloadToken, setReloadToken] = useState(0);
  const [policySaving, setPolicySaving] = useState<BacklogCiRepairPolicy | null>(null);
  const [repairState, setRepairState] = useState<
    { status: "idle" } | { status: "pending" } | { status: "done"; message: string }
  >({ status: "idle" });
  const [actionError, setActionError] = useState("");

  const load = useCallback(
    (signal?: AbortSignal) =>
      fetchPrdCiDelivery({ repoId, prdPath, signal })
        .then((result) => {
          setCiState({
            status: "ready",
            delivery: result.ci_delivery,
            reason: result.reason,
          });
        })
        .catch((error: unknown) => {
          if (signal?.aborted) {
            return;
          }
          setCiState({
            status: "error",
            message: error instanceof Error ? error.message : "读取 CI/CD 状态失败。",
          });
        }),
    [prdPath, repoId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
    };
  }, [load, reloadToken]);

  /** 用户主动刷新：回到加载态并重新读取（state 更新留在事件回调里）。 */
  function reload() {
    setCiState({ status: "loading" });
    setReloadToken((token) => token + 1);
  }

  const delivery = ciState.status === "ready" ? ciState.delivery : null;

  if (ciState.status === "loading") {
    return (
      <div className="space-y-3" data-testid="prd-ci-loading">
        <Skeleton className="h-6 w-1/3" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (ciState.status === "error") {
    return (
      <div className="space-y-3" data-testid="prd-ci-error">
        <ResourceErrorAlert message={ciState.message} testId="prd-ci-error-alert" />
        <Button variant="outline" size="sm" onClick={reload}>
          重试
        </Button>
      </div>
    );
  }

  if (delivery === null) {
    return (
      <div
        data-testid="prd-ci-empty"
        className="rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300"
      >
        <p className="font-medium">该 PRD 目前没有可观察的远端 PR</p>
        <p className="mt-1 text-xs text-slate-500">
          {ciState.reason || "CI/CD 监控只针对已发布 PR 的 PRD；未发布前保持原有流程。"}
        </p>
      </div>
    );
  }

  /**
   * 写入该 PRD 的三态策略并重新读取投影。
   *
   * @param policy - 目标策略（``inherit`` / ``on`` / ``off``）。
   */
  async function handlePolicyChange(policy: BacklogCiRepairPolicy) {
    setPolicySaving(policy);
    setActionError("");
    try {
      await updatePrdCiPolicy({ repoId, prdPath, policy });
      await load();
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : "策略写入失败。");
    } finally {
      setPolicySaving(null);
    }
  }

  /** 请求一次手动修复：只发送 PRD 路径，服务端自行判定 head/轮次/上限。 */
  async function handleRepair() {
    setRepairState({ status: "pending" });
    setActionError("");
    try {
      const result = await requestPrdCiRepair({ repoId, prdPath });
      setRepairState({ status: "done", message: result.detail || "修复请求已排队。" });
      await load();
    } catch (error: unknown) {
      setRepairState({ status: "idle" });
      setActionError(error instanceof Error ? error.message : "修复请求失败。");
    }
  }

  const showRepairButton =
    delivery.status === "failing" || delivery.status === "not_run" || delivery.status === "unavailable";

  return (
    <div className="space-y-4" data-testid="prd-ci-view" data-prd-ci-status={delivery.status}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={CI_STATUS_VARIANTS[delivery.status]} data-testid="prd-ci-status">
          {CI_STATUS_LABELS[delivery.status]}
        </Badge>
        <span className="text-xs text-slate-500" data-testid="prd-ci-checks-state">
          原始 checks：{delivery.checks_state ?? "无记录"}
        </span>
        {delivery.pr_url ? (
          <a
            className="text-xs text-sky-600 underline"
            href={delivery.pr_url}
            target="_blank"
            rel="noreferrer"
            data-testid="prd-ci-pr-link"
          >
            PR #{delivery.pr_number}
          </a>
        ) : null}
        {delivery.head_sha ? (
          <span className="font-mono text-xs text-slate-400" data-testid="prd-ci-head">
            {delivery.head_sha.slice(0, 8)}
          </span>
        ) : null}
        <Button size="sm" variant="ghost" onClick={reload} data-testid="prd-ci-refresh">
          刷新
        </Button>
      </div>

      <section
        className="rounded-md border border-slate-200 p-3 dark:border-slate-800"
        data-testid="prd-ci-policy"
      >
        <p className="text-xs font-medium text-slate-500">CI 自动修复策略</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {POLICY_OPTIONS.map((policy) => (
            <Button
              key={policy}
              size="sm"
              variant={delivery.stored_policy === policy ? "default" : "outline"}
              disabled={policySaving !== null}
              onClick={() => void handlePolicyChange(policy)}
              data-testid={`prd-ci-policy-${policy}`}
            >
              {CI_POLICY_LABELS[policy]}
            </Button>
          ))}
          {policySaving ? <span className="text-xs text-slate-500">写入中…</span> : null}
        </div>
        <p className="mt-2 text-xs text-slate-500" data-testid="prd-ci-policy-effective">
          已存储：{CI_POLICY_LABELS[delivery.stored_policy]}（来源 {delivery.policy_source}）· 仓库全局：
          {delivery.global_auto_repair ? "开启" : "关闭"} · 生效：
          <span data-testid="prd-ci-effective-value">
            {delivery.effective_auto_repair ? "自动修复开启" : "自动修复关闭"}
          </span>
        </p>
        {delivery.stored_policy === "inherit" ? (
          <p className="mt-1 text-xs text-slate-400">
            「跟随全局」不是关闭：仓库全局值变化时该 PRD 实时跟随。
          </p>
        ) : null}
      </section>

      <section className="space-y-2">
        <p className="text-xs font-medium text-slate-500">问题</p>
        {delivery.problems.length === 0 ? (
          <p className="text-xs text-slate-400" data-testid="prd-ci-no-problems">
            当前没有未通过或未完成的检查项。
          </p>
        ) : (
          <ul className="space-y-2" data-testid="prd-ci-problems">
            {delivery.problems.map((problem, index) => (
              <li
                // 同名 job 可能出现在多轮/多个 head 上，用内容组合键避免重复 key。
                key={`${problem.kind}-${problem.name}-${problem.head_sha ?? ""}-${index}`}
                className="rounded-md border border-slate-200 p-2 text-xs dark:border-slate-800"
                data-testid="prd-ci-problem"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={problem.kind === "check_failure" ? "failed" : "warning"}>
                    {CI_PROBLEM_KIND_LABELS[problem.kind] ?? problem.kind}
                  </Badge>
                  <span className="font-medium">{problem.name}</span>
                  {problem.url ? (
                    <a
                      className="text-sky-600 underline"
                      href={problem.url}
                      target="_blank"
                      rel="noreferrer"
                      data-testid="prd-ci-problem-link"
                    >
                      在 GitHub 查看
                    </a>
                  ) : null}
                </div>
                {problem.detail ? (
                  <p className="mt-1 text-slate-500" data-testid="prd-ci-problem-detail">
                    {problem.detail}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        className="rounded-md border border-slate-200 p-3 text-xs dark:border-slate-800"
        data-testid="prd-ci-decision"
      >
        <p className="font-medium text-slate-500">Supervisor 与修复轮次</p>
        <p className="mt-1">
          Supervisor 动作：{delivery.supervisor_action ?? "尚无结论"} · 修复轮次：
          <span data-testid="prd-ci-rounds">
            {delivery.repair_rounds}/{delivery.max_repair_attempts}
          </span>
          {delivery.repair_exhausted ? (
            <span className="ml-1 text-amber-600" data-testid="prd-ci-exhausted">
              已达上限
            </span>
          ) : null}
        </p>
        {delivery.last_decision ? (
          <p className="mt-1 text-slate-500" data-testid="prd-ci-last-decision">
            最近结论：{DECISION_LABELS[delivery.last_decision] ?? delivery.last_decision}
          </p>
        ) : null}
        {delivery.failure_key ? (
          <p className="mt-1 font-mono text-slate-400" data-testid="prd-ci-failure-key">
            failure key：{delivery.failure_key}
          </p>
        ) : null}
        {delivery.detail ? (
          <p className="mt-1 text-slate-500" data-testid="prd-ci-detail">
            {delivery.detail}
          </p>
        ) : null}
        <p className="mt-1 text-slate-400">
          最近同步：{formatLocalDateTime(delivery.last_synced_at)}
        </p>
      </section>

      {showRepairButton ? (
        <section className="space-y-2">
          <Button
            size="sm"
            variant="outline"
            disabled={repairState.status === "pending"}
            onClick={() => void handleRepair()}
            data-testid="prd-ci-repair"
          >
            {repairState.status === "pending" ? "修复请求中…" : "请求一次修复"}
          </Button>
          {repairState.status === "done" ? (
            <p className="text-xs text-emerald-600" data-testid="prd-ci-repair-success">
              {repairState.message}
            </p>
          ) : null}
        </section>
      ) : null}

      {actionError ? (
        <ResourceErrorAlert message={actionError} testId="prd-ci-action-error" />
      ) : null}
    </div>
  );
}
