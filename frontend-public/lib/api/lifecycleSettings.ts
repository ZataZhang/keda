// 聚合生命周期设置的 API 封装（矩阵 + 模型预设 + 执行器回退候选）。
//
// 对应后端 `GET/PATCH /v1/agent-runner/lifecycle-settings` 与
// `GET/PUT /v1/agent-runner/agent-fallback-candidates`。矩阵、预设、回退三段
// 共用同一份 core 解析视图：页面读取聚合视图渲染九阶段最终生效值与来源，
// 写回只提交用户改动过的预设 / 阶段绑定（保留式），回退候选按完整期望数组写回。

import { get, patch, put } from "./client";
import type {
  AgentFallbackCandidatesView,
  AgentPresetPayload,
  FallbackCandidateWriteEntry,
  LifecycleAgentScope,
  LifecycleSettingsView,
} from "./types";

const BASE_PATH = "/v1/agent-runner";

/**
 * 读取某视角下的聚合生命周期设置视图（九阶段三元组 + 预设 + 回退候选）。
 *
 * @param params - 视角（global / repository）与仓库级视角所需的 repoId。
 * @returns 聚合视图。
 */
export async function fetchLifecycleSettings(params: {
  scope: LifecycleAgentScope;
  repoId?: string;
}): Promise<LifecycleSettingsView> {
  const searchParams = new URLSearchParams();
  searchParams.set("scope", params.scope);
  if (params.scope === "repository") {
    if (!params.repoId) {
      throw new Error("scope=repository 需要 repoId。");
    }
    searchParams.set("repo_id", params.repoId);
  }
  return get(`${BASE_PATH}/lifecycle-settings?${searchParams.toString()}`);
}

/**
 * 保留式写回预设与阶段绑定：只提交改动过的预设与绑定，`null` 表示删除该项。
 *
 * @param params - 视角、仓库 id、改动过的预设集合与阶段绑定集合。
 * @returns 写后重读的聚合视图。
 */
export async function updateLifecycleSettings(params: {
  scope: LifecycleAgentScope;
  repoId?: string;
  presets: Record<string, AgentPresetPayload | null>;
  bindings: Record<string, string | null>;
}): Promise<LifecycleSettingsView> {
  return patch(`${BASE_PATH}/lifecycle-settings`, {
    scope: params.scope,
    repo_id: params.repoId ?? null,
    presets: params.presets,
    bindings: params.bindings,
  });
}

/**
 * 读取有序执行器回退候选视图。
 *
 * @param repoId - 可选仓库 id；不传则读全局配置。
 * @returns 回退候选视图。
 */
export async function fetchAgentFallbackCandidates(
  repoId?: string,
): Promise<AgentFallbackCandidatesView> {
  const searchParams = new URLSearchParams();
  if (repoId) {
    searchParams.set("repo_id", repoId);
  }
  const queryString = searchParams.toString();
  return get(
    `${BASE_PATH}/agent-fallback-candidates${queryString ? `?${queryString}` : ""}`,
  );
}

/**
 * 整体替换有序回退候选数组并写回候选步数预算（候选为机器级配置）。
 *
 * @param params - 完整期望候选数组、切换预算与可选仓库 id（仅选取校验视角）。
 * @returns 写后重读的回退候选视图。
 */
export async function updateAgentFallbackCandidates(params: {
  candidates: FallbackCandidateWriteEntry[];
  maxAgentSwitches: number;
  repoId?: string;
}): Promise<AgentFallbackCandidatesView> {
  return put(`${BASE_PATH}/agent-fallback-candidates`, {
    candidates: params.candidates,
    max_agent_switches: params.maxAgentSwitches,
    repo_id: params.repoId ?? null,
  });
}
