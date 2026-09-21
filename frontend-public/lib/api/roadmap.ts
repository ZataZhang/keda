// Roadmap API wrapper for `/api/v1/agent-runner/roadmap/*`.

import { get, patch, post } from "./client";
import type {
  PrdLifecycleDetail,
  RoadmapGlobalStartResult,
  RoadmapPrd,
  RoadmapSettings,
  RoadmapActionResult,
  RoadmapAutopilotState,
  RoadmapPrdEvidenceManifest,
} from "./types";

const BASE_PATH = "/v1/agent-runner/roadmap";

export async function fetchRoadmapPrds(params: {
  repoId: string;
  includeArchived?: boolean;
  signal?: AbortSignal;
}): Promise<{ prds: RoadmapPrd[]; repo_id: string; include_archived: boolean; scanned_at: string }> {
  const searchParams = new URLSearchParams();
  searchParams.set("repo_id", params.repoId);
  if (params.includeArchived) {
    searchParams.set("include_archived", "true");
  }
  return get(`${BASE_PATH}/prds?${searchParams.toString()}`, { signal: params.signal });
}

export async function fetchRoadmapSettings(repoId: string): Promise<RoadmapSettings> {
  return get(`${BASE_PATH}/settings?repo_id=${encodeURIComponent(repoId)}`);
}

export async function updateRoadmapSettings(params: {
  repoId: string;
  maxParallel: number;
  defaultView: "timeline" | "list";
}): Promise<RoadmapSettings> {
  return patch(`${BASE_PATH}/settings?repo_id=${encodeURIComponent(params.repoId)}`, {
    max_parallel: params.maxParallel,
    default_view: params.defaultView,
  });
}

/**
 * 把 PRD 相对路径编码为 URL-safe base64（后端按同样的方式解码）。
 *
 * @param prdPath - 相对仓库根目录的 PRD 路径。
 * @returns 不带 padding 的 URL-safe base64 字符串。
 */
export function encodePrdPath(prdPath: string): string {
  const bytes = new TextEncoder().encode(prdPath);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

export async function startRoadmapPrd(repoId: string, prdPath: string): Promise<RoadmapActionResult> {
  const encodedPath = encodePrdPath(prdPath);
  return post(`${BASE_PATH}/prds/${encodedPath}/start`, { repo_id: repoId });
}

/**
 * 读取单个 PRD 的 Markdown 原文。
 *
 * @param repoId - 仓库标识。
 * @param prdPath - 相对仓库根目录的 PRD 路径，取自列表响应。
 * @returns 与磁盘文件逐字节一致的 UTF-8 原文。
 */
export async function fetchPrdContent(repoId: string, prdPath: string): Promise<string> {
  const encodedPath = encodePrdPath(prdPath);
  const searchParams = new URLSearchParams();
  searchParams.set("repo_id", repoId);
  // 端点返回 text/plain，显式声明 responseType 避免 axios 把原文当 JSON 解析。
  return get<string>(`${BASE_PATH}/prds/${encodedPath}/content?${searchParams.toString()}`, {
    responseType: "text",
  });
}

export async function startGlobalRoadmap(params: {
  repoId: string;
  maxParallel: number;
}): Promise<RoadmapGlobalStartResult> {
  return post(`${BASE_PATH}/start-global`, {
    repo_id: params.repoId,
    max_parallel: params.maxParallel,
  });
}

export async function stopGlobalRoadmap(repoId: string): Promise<{ stopped: boolean; repo_id: string }> {
  return post(`${BASE_PATH}/stop-global`, { repo_id: repoId });
}

/**
 * 读取当前仓库的 Autopilot 状态（服务端每次 fresh load，前端也不缓存）。
 *
 * @param repoId - 仓库标识。
 * @returns 生效配置、自动合并门禁、daemon 运行态与并发上限的聚合快照。
 */
export async function fetchRoadmapAutopilot(repoId: string): Promise<RoadmapAutopilotState> {
  return get(`${BASE_PATH}/autopilot?repo_id=${encodeURIComponent(repoId)}`);
}

/**
 * 切换当前仓库的 Autopilot 自动推进（只改 agent_runner.autopilot.enabled）。
 *
 * @param params.repoId - 仓库标识。
 * @param params.enabled - 目标开关值；自动合并不受影响。
 * @returns 写后 fresh load 读回的状态快照（不是请求体回显）。
 */
export async function updateRoadmapAutopilot(params: {
  repoId: string;
  enabled: boolean;
}): Promise<RoadmapAutopilotState> {
  return patch(`${BASE_PATH}/autopilot?repo_id=${encodeURIComponent(params.repoId)}`, {
    repo_id: params.repoId,
    enabled: params.enabled,
  });
}

/**
 * 读取某个 PRD 的验收证据清单（每次调用重新读盘）。
 *
 * @param repoId - 仓库标识。
 * @param prdPath - 列表响应给出的 PRD 仓库相对路径。
 * @returns 该 PRD 证据目录内一层允许展示的文件清单。
 */
export async function fetchPrdEvidence(
  repoId: string,
  prdPath: string,
): Promise<RoadmapPrdEvidenceManifest> {
  const encodedPath = encodePrdPath(prdPath);
  return get(
    `${BASE_PATH}/prds/${encodedPath}/evidence?repo_id=${encodeURIComponent(repoId)}`,
  );
}

/**
 * 读取某个 PRD 的生命周期详情（当前阶段、耗时拆分与追加事件时间线）。
 *
 * 无任何 lifecycle run/event 时后端仍返回 200，由 `has_data=false` 表达空态。
 *
 * @param params.repoId - 仓库标识。
 * @param params.prdPath - 列表响应给出的 PRD 仓库相对路径。
 * @param params.signal - 可选的取消信号，用于组件卸载或切换 PRD 时中止请求。
 * @returns 该 PRD 的生命周期详情快照。
 */
export async function fetchPrdLifecycle(params: {
  repoId: string;
  prdPath: string;
  signal?: AbortSignal;
}): Promise<PrdLifecycleDetail> {
  const encodedPath = encodePrdPath(params.prdPath);
  const searchParams = new URLSearchParams();
  searchParams.set("repo_id", params.repoId);
  return get(`${BASE_PATH}/prds/${encodedPath}/lifecycle?${searchParams.toString()}`, {
    signal: params.signal,
  });
}

/**
 * 拼接单个证据文件的只读 URL。
 *
 * 文件名经后端 base64url token 传递；越界、符号链接逃逸与超限文件的拦截由
 * 服务端的 basename / containment / 大小校验负责。
 *
 * @param repoId - 仓库标识。
 * @param prdPath - PRD 仓库相对路径。
 * @param artifactToken - manifest 中给出的已编码文件名 token。
 * @returns 可直接用于预览或下载的 URL。
 */
export function buildPrdEvidenceArtifactUrl(
  repoId: string,
  prdPath: string,
  artifactToken: string,
): string {
  const encodedPath = encodePrdPath(prdPath);
  const searchParams = new URLSearchParams();
  searchParams.set("repo_id", repoId);
  return `/api${BASE_PATH}/prds/${encodedPath}/evidence/${artifactToken}?${searchParams.toString()}`;
}
