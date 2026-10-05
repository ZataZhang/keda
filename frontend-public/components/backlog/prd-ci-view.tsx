"use client";

// PRD 详情的 CI/CD 标签页：原始 checks 状态 + 策略三态 + 问题卡 + 单次手动修复。
//
// 显示原则（与本 PRD 的验收判据一致）：
// - 原始 checks 观察与 Agent 决定/未验证说明分开呈现；零 job / 不可达显示
//   「未验证」，绝不渲染成代码失败或 CI 通过。
// - stored / global / effective 三个策略值同时可见，effective 由服务端计算，
//   前端只回显，不自行推断。
// - 手动修复是显式单次请求：pending / 成功 / 失败反馈齐全，重复点击禁用，
//   不做前端乐观伪造新轮次。

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchPrdCiDelivery,
  requestPrdCiRepair,
  updatePrdCiPolicy,
} from "@/lib/api/backlog";
import type { BacklogCiDelivery, CiDeliveryStatus, CiRepairPolicy } from "@/lib/api/types";

export const PRD_CI_TAB_ID = "ci-delivery";

const STATUS_LABELS: Record<CiDeliveryStatus, string> = {
  no_pr: "暂无 PR",
  pending: "等待 CI/CD",
  success: "CI 通过",
  failure: "检查未通过",
  unavailable: "状态未知（未验证）",
};

const STATUS_VARIANTS: Record<CiDeliveryStatus, "default" | "secondary" | "destructive" | "outline"> = {
  no_pr: "outline",
  pending: "secondary",
  success: "default",
  failure: "destructive",
  unavailable: "outline",
};

const POLICY_LABELS: Record<CiRepairPolicy, string> = {
  inherit: "跟随全局",
  on: "强制开启",
  off: "强制关闭",
};

const POLICY_OPTIONS: CiRepairPolicy[] = ["inherit", "on", "off"];

interface PrdCiViewProps {
  repoId: string;
  prdPath: string;
  issueNumber: number | null;
}

/**
 * 渲染单个 PRD 的 CI/CD 交付尾段：状态、策略三态与问题卡。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 仓库相对路径。
 * @param props.issueNumber - 关联 GitHub Issue；无 Issue 时三态控制禁用。
 * @returns CI/CD 标签页内容。
 */
export function PrdCiView({ repoId, prdPath, issueNumber }: PrdCiViewProps) {
  const [delivery, setDelivery] = useState<BacklogCiDelivery | null>(null);
  const [loading, setLoading] = useState(true);
  const [policySaving, setPolicySaving] = useState(false);
  const [repairPending, setRepairPending] = useState(false);

  const loadDelivery = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const loaded = await fetchPrdCiDelivery({ repoId, prdPath, signal });
        setDelivery(loaded);
      } catch (error) {
        if (signal?.aborted) {
          return;
        }
        toast.error(error instanceof Error ? error.message : "加载 CI/CD 状态失败。");
      }
    },
    [repoId, prdPath],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void loadDelivery(controller.signal).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    });
    const timer = setInterval(() => void loadDelivery(controller.signal), 30000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [loadDelivery]);

  async function handlePolicyChange(next: CiRepairPolicy) {
    if (!delivery || next === delivery.stored_policy) {
      return;
    }
    setPolicySaving(true);
    try {
      // 响应体是写后 fresh 回读（marker + fresh comments），不做乐观覆盖。
      const updated = await updatePrdCiPolicy({ repoId, prdPath, value: next });
      setDelivery(updated);
      toast.success(`已保存：${POLICY_LABELS[next]}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存策略失败。");
      await loadDelivery();
    } finally {
      setPolicySaving(false);
    }
  }

  async function handleManualRepair() {
    setRepairPending(true);
    try {
      const result = await requestPrdCiRepair({ repoId, prdPath });
      if (result.requested) {
        toast.success(result.detail);
      } else {
        toast.warning(result.detail);
      }
      await loadDelivery();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "请求修复失败。");
    } finally {
      setRepairPending(false);
    }
  }

  if (loading || !delivery) {
    return (
      <div className="space-y-2" data-testid="prd-ci-loading">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  }

  return (
    <div className="space-y-3 text-sm" data-testid="prd-ci-view">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={STATUS_VARIANTS[delivery.status]} data-testid="prd-ci-status">
          {STATUS_LABELS[delivery.status]}
        </Badge>
        <span className="text-xs text-slate-500">
          第 {delivery.round_count}/{delivery.max_rounds} 轮 · 最近同步{" "}
          {delivery.last_synced_at ?? "—"}
        </span>
        {delivery.pr_url ? (
          <Button size="sm" variant="outline" asChild>
            <a href={delivery.pr_url} target="_blank" rel="noreferrer" data-testid="prd-ci-pr-link">
              查看 GitHub 检查
            </a>
          </Button>
        ) : null}
      </div>

      {delivery.exhausted ? (
        <p className="text-xs text-red-600" data-testid="prd-ci-exhausted">
          {delivery.exhausted_reason}
        </p>
      ) : null}

      <div
        className="flex flex-wrap items-center gap-2"
        data-testid="prd-ci-policy"
      >
        <span className="text-xs text-slate-500">此 PRD 的自动修复</span>
        {POLICY_OPTIONS.map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            variant={delivery.stored_policy === option ? "default" : "outline"}
            disabled={policySaving || issueNumber === null}
            onClick={() => void handlePolicyChange(option)}
            data-testid={`prd-ci-policy-${option}`}
            title={issueNumber === null ? "无关联 Issue 的 PRD 只能跟随全局" : undefined}
          >
            {POLICY_LABELS[option]}
          </Button>
        ))}
        <span className="text-xs text-slate-500" data-testid="prd-ci-effective">
          当前生效：{delivery.effective_enabled ? "开启" : "关闭"}
          {delivery.stored_policy === "inherit" ? " · 未单独设置，继承当前仓库全局策略" : ""}
        </span>
      </div>

      <div className="space-y-2" data-testid="prd-ci-problems">
        {delivery.problems.length === 0 ? (
          <p className="text-xs text-slate-500">
            {delivery.status === "failure"
              ? "GitHub 只返回了汇总状态；请到 GitHub 查看失败详情。"
              : "暂无失败检查。"}
          </p>
        ) : (
          delivery.problems.map((problem, index) => (
            <div
              key={`${problem.name}-${index}`}
              className="rounded-md border border-slate-200 p-2 dark:border-slate-800"
              data-testid="prd-ci-problem"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium" title={problem.name}>
                  {problem.name}
                </span>
                {problem.url ? (
                  <a
                    href={problem.url}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 text-xs text-slate-500 underline"
                  >
                    查看
                  </a>
                ) : null}
              </div>
              <p className="mt-1 text-xs text-slate-500">{problem.summary}</p>
            </div>
          ))
        )}
      </div>

      {delivery.status === "failure" || delivery.status === "unavailable" ? (
        <Button
          size="sm"
          variant="outline"
          disabled={repairPending || issueNumber === null}
          onClick={() => void handleManualRepair()}
          data-testid="prd-ci-manual-repair"
        >
          {repairPending ? "请求中…" : "立即修复此问题（一次）"}
        </Button>
      ) : null}
    </div>
  );
}
