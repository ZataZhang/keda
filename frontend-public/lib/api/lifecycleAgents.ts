// agent 标签 / PRD 覆盖 的 API 封装。
//
// 生命周期矩阵与执行器回退的编辑入口已并入统一设置页，其读写走
// lib/api/lifecycleSettings.ts 的聚合端点；本模块只保留仍被 UI 调用的两个面。
// PRD 层写该 PRD 文件头部的 lifecycle_agents 块，agent 标签写机器级注册块。
// 所有写操作都采用保留式语义：只发送用户显式改动的键，`null` 表示删除该键。

import { get, patch, put } from "./client";
import { encodePrdPath } from "./backlog";
import type {
  AgentLabelEntry,
  AgentLabelsView,
  PrdAgentOverrideUpdateResponse,
  PrdAgentOverrideView,
} from "./types";

const BASE_PATH = "/v1/agent-runner";

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
    `${BASE_PATH}/backlog/prds/${encodedPath}/agent-overrides?${searchParams.toString()}`,
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
  return patch(`${BASE_PATH}/backlog/prds/${encodedPath}/agent-overrides`, {
    repo_id: params.repoId,
    overrides: params.overrides,
  });
}
