"use client";

// agent 回退顺序编辑器。
//
// 编辑 `[agent_runner.runner]` 的 `agent_fallback_order` 与 `max_agent_switches`，
// 作用于 Issue 执行阶段的跨 agent 回退：第一个尝试的 agent 由生命周期矩阵 / 标签
// 路由决定，失败后按本列表顺序依次回退。

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  fetchAgentFallbackOrder,
  updateAgentFallbackOrder,
} from "@/lib/api/lifecycleAgents";

/**
 * agent 回退顺序编辑器。
 *
 * @returns 可排序的回退链与最大切换次数表单。
 */
export function AgentFallbackOrderEditor() {
  const [order, setOrder] = useState<string[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [maxSwitches, setMaxSwitches] = useState(2);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let isCancelled = false;
    fetchAgentFallbackOrder()
      .then((view) => {
        if (!isCancelled) {
          setOrder(view.agent_fallback_order);
          setMaxSwitches(view.max_agent_switches);
          setAgents(view.agents);
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(
            error instanceof Error ? error.message : "加载回退顺序失败。",
          );
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setLoading(false);
        }
      });
    return () => {
      isCancelled = true;
    };
  }, []);

  /** 上下移动回退链中的某一项。 */
  function moveAgent(index: number, offset: number) {
    setOrder((current) => {
      const nextIndex = index + offset;
      if (nextIndex < 0 || nextIndex >= current.length) {
        return current;
      }
      const nextOrder = [...current];
      [nextOrder[index], nextOrder[nextIndex]] = [
        nextOrder[nextIndex],
        nextOrder[index],
      ];
      return nextOrder;
    });
  }

  /** 保存回退顺序与最大切换次数到 [agent_runner.runner]。 */
  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const view = await updateAgentFallbackOrder({
        agentFallbackOrder: order,
        maxAgentSwitches: maxSwitches,
      });
      setOrder(view.agent_fallback_order);
      setMaxSwitches(view.max_agent_switches);
      toast.success("agent 回退顺序已保存。");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-500">加载中…</p>;
  }

  if (loadError) {
    return (
      <div
        role="alert"
        className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
        data-testid="fallback-order-error"
      >
        {loadError}
      </div>
    );
  }

  const remainingAgents = agents.filter((agentName) => !order.includes(agentName));

  return (
    <div className="space-y-3" data-testid="fallback-order-editor">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        作用于 Issue 执行阶段的跨 agent 回退；第一个尝试的 agent 由矩阵 / 标签路由决定，
        失败后按下面的顺序依次切换。
      </p>

      {order.length === 0 ? (
        <p className="text-sm text-slate-500">当前回退链为空。</p>
      ) : (
        <ol className="space-y-1">
          {order.map((agentName, index) => (
            <li
              key={agentName}
              data-testid={`fallback-order-item-${agentName}`}
              className="flex items-center gap-2 rounded-md border px-2 py-1"
            >
              <span className="w-5 text-xs text-slate-500">{index + 1}</span>
              <span className="flex-1 text-sm">{agentName}</span>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                disabled={index === 0}
                onClick={() => moveAgent(index, -1)}
                data-testid={`fallback-order-up-${agentName}`}
                aria-label={`${agentName} 上移`}
              >
                ↑
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                disabled={index === order.length - 1}
                onClick={() => moveAgent(index, 1)}
                data-testid={`fallback-order-down-${agentName}`}
                aria-label={`${agentName} 下移`}
              >
                ↓
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() =>
                  setOrder((current) =>
                    current.filter((name) => name !== agentName),
                  )
                }
                data-testid={`fallback-order-remove-${agentName}`}
                aria-label={`移除 ${agentName}`}
              >
                ×
              </Button>
            </li>
          ))}
        </ol>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            disabled={remainingAgents.length === 0}
            data-testid="fallback-order-append"
          >
            从剩余 agent 追加
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {remainingAgents.map((agentName) => (
            <DropdownMenuItem
              key={agentName}
              onSelect={() => setOrder((current) => [...current, agentName])}
              data-testid={`fallback-order-append-${agentName}`}
            >
              {agentName}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <div className="flex items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="fallback-order-max-switches">最大切换次数</Label>
          <Input
            id="fallback-order-max-switches"
            type="number"
            min={0}
            value={maxSwitches}
            onChange={(event) => {
              const parsed = Number.parseInt(event.target.value, 10);
              setMaxSwitches(Number.isNaN(parsed) ? 0 : Math.max(0, parsed));
            }}
            className="w-28"
            data-testid="fallback-order-max-switches"
          />
        </div>
        <p className="pb-2 text-xs text-slate-500 dark:text-slate-400">
          最多尝试 {maxSwitches + 1} 个 agent（max_agent_switches={maxSwitches}）
        </p>
      </div>

      {saveError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {saveError}
        </p>
      ) : null}

      <Button
        size="sm"
        onClick={() => void handleSave()}
        disabled={saving}
        data-testid="fallback-order-save"
      >
        {saving ? "保存中…" : "保存回退顺序"}
      </Button>
    </div>
  );
}
