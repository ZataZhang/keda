"use client";

// 模型预设的两块编辑 UI：已有预设的卡片（agent / model / 推理深度 + 删除）与
// 新建预设表单（草稿态由本页的待保存队列承接）。
//
// model / effort 是否会被注入由后端校验兜底（给值但目标 agent 缺参数模板会在保存时
// fail-fast 并回显错误），因此这里不预判可注入性、也不禁用输入框。

import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { AgentPresetPayload } from "@/lib/api/types";

export interface PresetCardProps {
  name: string;
  agents: string[];
  values: AgentPresetPayload;
  boundStages: string[];
  sourceLabel: string;
  onFieldChange: (next: AgentPresetPayload) => void;
  onDelete: () => void;
}

/**
 * 模型预设编辑卡片（编辑已有预设的 agent / model / effort）。
 *
 * @param props - 预设名、可选 agent 名单、当前值、绑定阶段、来源标签与回调。
 * @returns 一张预设卡片，含删除入口。
 */
export function PresetCard({
  name,
  agents,
  values,
  boundStages,
  sourceLabel,
  onFieldChange,
  onDelete,
}: PresetCardProps) {
  return (
    <Card data-testid={`preset-card-${name}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{name}</CardTitle>
          <Badge variant="default" className="text-[10px]">
            {sourceLabel}
          </Badge>
        </div>
        <CardDescription className="text-xs">
          {boundStages.length > 0
            ? `绑定阶段：${boundStages.join("、")}`
            : "尚未绑定任何阶段"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div className="space-y-1">
            <Label className="text-[11px]">Agent</Label>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full justify-between"
                  data-testid={`preset-agent-${name}`}
                >
                  <span className="truncate">{values.agent}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="min-w-40">
                <DropdownMenuRadioGroup
                  value={values.agent}
                  onValueChange={(agentName) =>
                    onFieldChange({ ...values, agent: agentName })
                  }
                >
                  {agents.map((agentName) => (
                    <DropdownMenuRadioItem
                      key={agentName}
                      value={agentName}
                      data-testid={`preset-agent-${name}-${agentName}`}
                    >
                      {agentName}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <div className="space-y-1">
            <Label className="text-[11px]">模型</Label>
            <Input
              value={values.model ?? ""}
              placeholder="留空=沿用 CLI 默认"
              onChange={(event) =>
                onFieldChange({
                  ...values,
                  model: event.target.value || null,
                })
              }
              data-testid={`preset-model-${name}`}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-[11px]">推理深度</Label>
            <Input
              value={values.reasoning_effort ?? ""}
              placeholder="留空=沿用 CLI 默认"
              onChange={(event) =>
                onFieldChange({
                  ...values,
                  reasoning_effort: event.target.value || null,
                })
              }
              data-testid={`preset-effort-${name}`}
            />
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-red-600 dark:text-red-400"
          onClick={onDelete}
          data-testid={`preset-delete-${name}`}
        >
          删除预设（同时解绑其阶段）
        </Button>
      </CardContent>
    </Card>
  );
}

export interface NewPresetDraft {
  name: string;
  agent: string;
  model: string;
  reasoningEffort: string;
}

export interface NewPresetFormProps {
  agents: string[];
  existingNames: string[];
  onAdd: (draft: NewPresetDraft) => void;
}

/**
 * 新建预设表单。
 *
 * @param props - 可选 agent 名单、已存在的预设名、提交回调。
 * @returns 新建预设的草稿表单（提交后由页面并入待保存队列）。
 */
export function NewPresetForm({
  agents,
  existingNames,
  onAdd,
}: NewPresetFormProps) {
  const [draft, setDraft] = useState<NewPresetDraft>({
    name: "",
    agent: agents[0] ?? "",
    model: "",
    reasoningEffort: "",
  });

  /** 校验非空并提交新建预设。 */
  function handleSubmit() {
    const name = draft.name.trim();
    if (!name) {
      toast.error("请填写预设名称。");
      return;
    }
    if (existingNames.includes(name)) {
      toast.error(`预设「${name}」已存在，请直接编辑它。`);
      return;
    }
    if (!draft.agent) {
      toast.error("请选择预设的 Agent。");
      return;
    }
    onAdd(draft);
    setDraft({ name: "", agent: agents[0] ?? "", model: "", reasoningEffort: "" });
  }

  return (
    <div
      className="space-y-2 rounded-lg border border-dashed p-3"
      data-testid="new-preset-form"
    >
      <p className="text-sm font-medium">新建预设</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-1">
          <Label className="text-[11px]" htmlFor="new-preset-name">
            名称
          </Label>
          <Input
            id="new-preset-name"
            value={draft.name}
            placeholder="如 sonnet-5.5-max"
            onChange={(event) =>
              setDraft((c) => ({ ...c, name: event.target.value }))
            }
            data-testid="new-preset-name"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]">Agent</Label>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full justify-between"
                data-testid="new-preset-agent"
              >
                <span className="truncate">{draft.agent || "选择 Agent"}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="min-w-40">
              <DropdownMenuRadioGroup
                value={draft.agent}
                onValueChange={(agentName) =>
                  setDraft((c) => ({ ...c, agent: agentName }))
                }
              >
                {agents.map((agentName) => (
                  <DropdownMenuRadioItem
                    key={agentName}
                    value={agentName}
                    data-testid={`new-preset-agent-${agentName}`}
                  >
                    {agentName}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]" htmlFor="new-preset-model">
            模型
          </Label>
          <Input
            id="new-preset-model"
            value={draft.model}
            placeholder="留空=沿用 CLI 默认"
            onChange={(event) =>
              setDraft((c) => ({ ...c, model: event.target.value }))
            }
            data-testid="new-preset-model"
          />
          <p className="text-xs leading-4 text-muted-foreground">
            不确定模型名？可让 AI 助手按所选 Agent 推荐模型并帮你补全预设。
          </p>
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]" htmlFor="new-preset-effort">
            推理深度
          </Label>
          <Input
            id="new-preset-effort"
            value={draft.reasoningEffort}
            placeholder="留空=不设"
            onChange={(event) =>
              setDraft((c) => ({ ...c, reasoningEffort: event.target.value }))
            }
            data-testid="new-preset-effort"
          />
        </div>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={handleSubmit}
        data-testid="new-preset-add"
      >
        加入待保存预设
      </Button>
    </div>
  );
}
