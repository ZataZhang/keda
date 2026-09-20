"use client";

// Agent 标签设置编辑器。
//
// 每个已注册 agent 一行，可编辑「标签名 / 标签颜色 / 标签描述」。标签名是 issue
// 路由标签（如 agent/codex）的名字部分，必须非空且全局唯一；保存前在客户端先做
// 一次校验，避免把明显冲突的输入发给后端。

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetchAgentLabels, updateAgentLabels } from "@/lib/api/lifecycleAgents";
import type { AgentLabelEntry } from "@/lib/api/types";

/** 校验标签颜色是否为 6 位十六进制（不带 #）。 */
const LABEL_COLOR_PATTERN = /^[0-9a-fA-F]{6}$/;

/** 客户端校验标签草稿，返回错误信息；通过时返回 null。 */
function validateLabels(labels: AgentLabelEntry[]): string | null {
  const seenLabelNames = new Map<string, string>();
  for (const entry of labels) {
    const labelName = entry.label.trim();
    if (!labelName) {
      return `agent「${entry.agent}」的标签名不能为空。`;
    }
    const existingAgent = seenLabelNames.get(labelName);
    if (existingAgent) {
      return `标签名「${labelName}」同时被 agent「${existingAgent}」与「${entry.agent}」使用，请改成唯一名称。`;
    }
    seenLabelNames.set(labelName, entry.agent);
    const labelColor = entry.label_color.trim();
    if (labelColor && !LABEL_COLOR_PATTERN.test(labelColor)) {
      return `agent「${entry.agent}」的标签颜色必须是 6 位十六进制（不带 #）。`;
    }
  }
  return null;
}

/**
 * Agent 标签设置编辑器。
 *
 * @returns 每个已注册 agent 一行的标签编辑表单。
 */
export function AgentLabelsEditor() {
  const [labels, setLabels] = useState<AgentLabelEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let isCancelled = false;
    fetchAgentLabels()
      .then((view) => {
        if (!isCancelled) {
          setLabels(view.labels);
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(error instanceof Error ? error.message : "加载标签失败。");
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

  /** 更新某个 agent 的单个字段。 */
  function updateField(
    agentName: string,
    field: keyof AgentLabelEntry,
    value: string,
  ) {
    setLabels((current) =>
      current.map((entry) =>
        entry.agent === agentName ? { ...entry, [field]: value } : entry,
      ),
    );
  }

  /** 校验并保存各 agent 的标签配置。 */
  async function handleSave() {
    const error = validateLabels(labels);
    if (error) {
      setValidationError(error);
      return;
    }
    setValidationError(null);
    setSaving(true);
    try {
      const view = await updateAgentLabels({ labels });
      setLabels(view.labels);
      toast.success("Agent 标签已保存。");
    } catch (saveFailure) {
      setValidationError(
        saveFailure instanceof Error ? saveFailure.message : "保存标签失败。",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-500">加载中…</p>;
  }

  if (loadError) {
    return <ResourceErrorAlert message={loadError} testId="agent-labels-error" />;
  }

  return (
    <div className="space-y-3" data-testid="agent-labels-editor">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        这里配置的是各 agent 在 issue 路由标签（如 agent/codex）中使用的名字、颜色与描述。
        工作流标签（agent/ready、agent/running 等）不在本表范围。
      </p>

      <div className="grid grid-cols-[minmax(5rem,0.7fr)_minmax(7rem,1fr)_minmax(6rem,0.7fr)_minmax(10rem,1.6fr)] gap-2 border-b pb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
        <span>agent</span>
        <span>标签名</span>
        <span>标签颜色</span>
        <span>标签描述</span>
      </div>

      {labels.map((entry) => (
        <div
          key={entry.agent}
          data-testid={`agent-label-row-${entry.agent}`}
          className="grid grid-cols-[minmax(5rem,0.7fr)_minmax(7rem,1fr)_minmax(6rem,0.7fr)_minmax(10rem,1.6fr)] items-center gap-2"
        >
          <span className="text-sm font-medium">{entry.agent}</span>
          <Label className="sr-only" htmlFor={`agent-label-name-${entry.agent}`}>
            {entry.agent} 标签名
          </Label>
          <Input
            id={`agent-label-name-${entry.agent}`}
            value={entry.label}
            onChange={(event) =>
              updateField(entry.agent, "label", event.target.value)
            }
            data-testid={`agent-label-name-${entry.agent}`}
          />
          <Input
            value={entry.label_color}
            placeholder="RRGGBB"
            onChange={(event) =>
              updateField(entry.agent, "label_color", event.target.value)
            }
            data-testid={`agent-label-color-${entry.agent}`}
          />
          <Input
            value={entry.label_description}
            onChange={(event) =>
              updateField(entry.agent, "label_description", event.target.value)
            }
            data-testid={`agent-label-description-${entry.agent}`}
          />
        </div>
      ))}

      {validationError ? (
        <p
          role="alert"
          className="text-sm text-red-600 dark:text-red-400"
          data-testid="agent-labels-validation-error"
        >
          {validationError}
        </p>
      ) : null}

      <Button
        size="sm"
        onClick={() => void handleSave()}
        disabled={saving || labels.length === 0}
        data-testid="agent-label-save"
      >
        {saving ? "保存中…" : "保存标签"}
      </Button>
    </div>
  );
}
