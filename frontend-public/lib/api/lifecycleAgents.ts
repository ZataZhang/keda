// 生命周期 Agent 矩阵 / agent 回退顺序 / agent 标签 / PRD 覆盖 的 API 封装。
//
// 三层各自只写自己的文件：
// - 全局层（Settings 页）-> 机器级 config.toml；
// - 仓库层（Roadmap 仓库行齿轮）-> 该仓库 .iar.toml；
// - PRD 层（PRD 原文页）-> 该 PRD 文件头部 lifecycle_agents 块。
// 所有写操作都采用保留式语义：只发送用户显式改动的键，`null` 表示删除该键。

import { get, patch, put } from "./client";
import { encodePrdPath } from "./roadmap";
import type {
  AgentFallbackOrderView,
  AgentLabelEntry,
  AgentLabelsView,
  LifecycleAgentScope,
  LifecycleAgentsView,
  PrdAgentOverrideUpdateResponse,
  PrdAgentOverrideView,
} from "./types";

const BASE_PATH = "/v1/agent-runner";

/**
 * 读取某视角下的生命周期矩阵生效视图。
 *
 * @param params - 视角（global / repository）与仓库级视角所需的 repoId。
 * @returns 含九键生效值、来源层与可选值的矩阵视图。
 */
export async function fetchLifecycleAgents(params: {
  scope: LifecycleAgentScope;
  repoId?: string;
}): Promise<LifecycleAgentsView> {
  const searchParams = new URLSearchParams();
  searchParams.set("scope", params.scope);
  if (params.scope === "repository") {
    if (!params.repoId) {
      throw new Error("scope=repository 需要 repoId。");
    }
    searchParams.set("repo_id", params.repoId);
  }
  return get(`${BASE_PATH}/lifecycle-agents?${searchParams.toString()}`);
}

/**
 * 保留式写回生命周期矩阵：只写 `values` 里显式给出的键，`null` 表示删除该键。
 *
 * @param params - 视角、仓库 id（仓库级）与显式改动的键值集合。
 * @returns 写后重读的矩阵视图。
 */
export async function updateLifecycleAgents(params: {
  scope: LifecycleAgentScope;
  repoId?: string;
  values: Record<string, string | null>;
}): Promise<LifecycleAgentsView> {
  return put(`${BASE_PATH}/lifecycle-agents`, {
    scope: params.scope,
    repo_id: params.repoId ?? null,
    values: params.values,
  });
}

/**
 * 读取 `[agent_runner.runner]` 的跨 agent 回退顺序与最大切换次数。
 *
 * @param repoId - 可选仓库 id；不传则读全局配置。
 * @returns 回退顺序、最大切换次数与可选 agent 列表。
 */
export async function fetchAgentFallbackOrder(
  repoId?: string,
): Promise<AgentFallbackOrderView> {
  const searchParams = new URLSearchParams();
  if (repoId) {
    searchParams.set("repo_id", repoId);
  }
  const queryString = searchParams.toString();
  return get(
    `${BASE_PATH}/agent-fallback-order${queryString ? `?${queryString}` : ""}`,
  );
}

/**
 * 写回跨 agent 回退顺序与最大切换次数。
 *
 * @param params - 回退顺序、最大切换次数与可选仓库 id。
 * @returns 写后重读的回退顺序视图。
 */
export async function updateAgentFallbackOrder(params: {
  agentFallbackOrder: string[];
  maxAgentSwitches: number;
  repoId?: string;
}): Promise<AgentFallbackOrderView> {
  return put(`${BASE_PATH}/agent-fallback-order`, {
    agent_fallback_order: params.agentFallbackOrder,
    max_agent_switches: params.maxAgentSwitches,
    repo_id: params.repoId ?? null,
  });
}

/**
 * 读取各已注册 agent 的路由标签配置。
 *
 * @param repoId - 可选仓库 id；不传则读全局配置。
 * @returns 标签列表视图。
 */
export async function fetchAgentLabels(repoId?: string): Promise<AgentLabelsView> {
  const searchParams = new URLSearchParams();
  if (repoId) {
    searchParams.set("repo_id", repoId);
  }
  const queryString = searchParams.toString();
  return get(`${BASE_PATH}/agent-labels${queryString ? `?${queryString}` : ""}`);
}

/**
 * 写回各 agent 的标签名 / 颜色 / 描述。
 *
 * @param params - 完整标签列表与可选仓库 id。
 * @returns 写后重读的标签列表。
 */
export async function updateAgentLabels(params: {
  labels: AgentLabelEntry[];
  repoId?: string;
}): Promise<AgentLabelsView> {
  return put(`${BASE_PATH}/agent-labels`, {
    labels: params.labels,
    repo_id: params.repoId ?? null,
  });
}

/**
 * 读取某个 PRD 文件头部的 `lifecycle_agents` 覆盖。
 *
 * @param params - 仓库 id 与 PRD 相对路径。
 * @returns 覆盖集合、可选 agent 列表与继承视角的矩阵。
 */
export async function fetchPrdAgentOverrides(params: {
  repoId: string;
  prdPath: string;
}): Promise<PrdAgentOverrideView> {
  const encodedPath = encodePrdPath(params.prdPath);
  const searchParams = new URLSearchParams();
  searchParams.set("repo_id", params.repoId);
  return get(
    `${BASE_PATH}/roadmap/prds/${encodedPath}/agent-overrides?${searchParams.toString()}`,
  );
}

/**
 * 把 PRD 头部覆盖块整体重写为完整期望集合（`null` 表示移除该项）。
 *
 * @param params - 仓库 id、PRD 相对路径与完整期望覆盖集合。
 * @returns 写后重读的覆盖集合。
 */
export async function updatePrdAgentOverrides(params: {
  repoId: string;
  prdPath: string;
  overrides: Record<string, string | null>;
}): Promise<PrdAgentOverrideUpdateResponse> {
  const encodedPath = encodePrdPath(params.prdPath);
  return patch(`${BASE_PATH}/roadmap/prds/${encodedPath}/agent-overrides`, {
    repo_id: params.repoId,
    overrides: params.overrides,
  });
}
