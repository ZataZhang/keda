"use client";

import "@xyflow/react/dist/style.css";
import { MarkerType, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";
import type { LifecycleAgentKey, LifecycleSettingsRow } from "@/lib/api/types";

type WorkflowNode = {
  id: string;
  x: number;
  y: number;
} & ({ role: LifecycleAgentKey } | { label: string });

type WorkflowGraph = {
  title: string;
  nodes: WorkflowNode[];
  edges: Edge[];
};

const delivery: WorkflowGraph = {
  title: "代码交付",
  nodes: [
    { id: "issue", label: "认领 Issue", x: 0, y: 0 },
    { id: "implement", role: "implementation", x: 180, y: 0 },
    { id: "check", label: "执行检查", x: 360, y: 0 },
    { id: "verify", role: "verifier", x: 540, y: 0 },
    { id: "gate", label: "交付门禁", x: 720, y: 0 },
    { id: "review", role: "review", x: 900, y: 0 },
    { id: "pr", label: "Draft PR", x: 1080, y: 0 },
    { id: "supervise", role: "supervisor", x: 1280, y: 0 },
    { id: "fix", role: "fix", x: 415, y: 125 },
    { id: "closeout", role: "closeout", x: 775, y: 125 },
  ],
  edges: [
    { id: "claim", source: "issue", target: "implement" },
    { id: "check", source: "implement", target: "check" },
    { id: "verify", source: "check", target: "verify" },
    { id: "gate", source: "verify", target: "gate" },
    { id: "review", source: "gate", target: "review" },
    { id: "pr", source: "review", target: "pr" },
    { id: "supervise", source: "pr", target: "supervise", label: "按配置" },
    { id: "fix", source: "check", target: "fix", label: "失败" },
    { id: "recheck", source: "fix", target: "check", label: "复查" },
    { id: "closeout", source: "gate", target: "closeout", label: "材料缺失" },
    { id: "regate", source: "closeout", target: "gate", label: "复查" },
  ],
};

const discussion: WorkflowGraph = {
  title: "讨论与内容生成",
  nodes: [
    { id: "issue", label: "待讨论 Issue", x: 0, y: 0 },
    { id: "debate", role: "deliberate", x: 230, y: 0 },
    { id: "prompt", label: "从提示词创建 Issue", x: 230, y: 125 },
    { id: "generate", role: "content_generation", x: 530, y: 0 },
    { id: "prd", label: "PRD", x: 800, y: 0 },
    { id: "body", label: "Issue 正文", x: 800, y: 125 },
  ],
  edges: [
    { id: "debate", source: "issue", target: "debate" },
    { id: "rework", source: "debate", target: "generate", label: "人工标记后" },
    { id: "prompt", source: "prompt", target: "generate" },
    { id: "prd", source: "generate", target: "prd", label: "整理需求" },
    { id: "body", source: "generate", target: "body", label: "创建 Issue" },
  ],
};

const workflows = [delivery, discussion];

interface LifecycleWorkflowGraphsProps {
  rows: LifecycleSettingsRow[];
  bindings: Partial<Record<LifecycleAgentKey, string | null>>;
  onRoleSelect: (role: LifecycleAgentKey, trigger: HTMLButtonElement) => void;
}

/** 按工作流图定义渲染流程，并从矩阵的生效行读取角色绑定。 */
export function LifecycleWorkflowGraphs({ rows, bindings, onRoleSelect }: LifecycleWorkflowGraphsProps) {
  const roles = new Map(rows.map((row) => [row.key, row]));
  const assigned = new Set(workflows.flatMap(({ nodes }) =>
    nodes.flatMap((node) => "role" in node ? [node.role] : []),
  ));

  return (
    <div className="space-y-4">
      {workflows.map((workflow) => {
        const nodes: Node[] = workflow.nodes.map((node) => {
          const row = "role" in node ? roles.get(node.role) : undefined;
          const draft = row && row.key in bindings ? bindings[row.key] : undefined;
          return {
            id: node.id,
            position: { x: node.x, y: node.y },
            sourcePosition: Position.Right,
            targetPosition: Position.Left,
            selectable: false,
            data: {
              label: row ? (
                <button
                  type="button"
                  className="nodrag nopan pointer-events-auto block w-full cursor-pointer rounded-md bg-violet-50 px-2.5 py-2 text-left hover:bg-violet-100 focus-visible:outline-2 dark:bg-violet-950 dark:hover:bg-violet-900"
                  onClick={(event) => onRoleSelect(row.key, event.currentTarget)}
                >
                  <strong className="block">{row.label} ›</strong>
                  <span className="block break-all">
                    {draft !== undefined
                      ? `${draft ?? "解除绑定"} · 待保存`
                      : row.follows_implementation ? "继承实现" : row.preset_name ?? row.effective_agent ?? "Agent 默认"}
                  </span>
                </button>
              ) : "label" in node ? node.label : node.role,
            },
            className: row
              ? "!border-border !bg-card !p-0 !text-card-foreground"
              : "!border-border !bg-card !text-card-foreground",
          };
        });
        return (
          <section key={workflow.title} className="rounded-lg border bg-muted/20 p-3">
            <h3 className="mb-2 text-sm font-semibold">{workflow.title}</h3>
            <div aria-label={workflow.title} tabIndex={0} className="overflow-x-auto">
              <div className="h-64" style={{ minWidth: Math.max(...nodes.map((node) => node.position.x)) + 200 }}>
                <ReactFlow
                  nodes={nodes}
                  edges={workflow.edges}
                  defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed }, selectable: false }}
                  defaultViewport={{ x: 12, y: 26, zoom: 1 }}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  panOnDrag={false}
                  zoomOnScroll={false}
                  zoomOnPinch={false}
                  zoomOnDoubleClick={false}
                  preventScrolling={false}
                />
              </div>
            </div>
          </section>
        );
      })}
      {rows.filter((row) => !assigned.has(row.key)).map((row) => (
        <button
          key={row.key}
          type="button"
          className="rounded-md px-2 py-1 text-sm text-primary underline-offset-4 hover:underline"
          onClick={(event) => onRoleSelect(row.key, event.currentTarget)}
        >
          {row.key === "planner" ? "独立问答配置 · kc ask" : row.label}
        </button>
      ))}
    </div>
  );
}
