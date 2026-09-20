// Roadmap API wrapper for `/api/v1/agent-runner/roadmap/*`.

import { get, patch, post } from "./client";
import type {
  RoadmapGlobalStartResult,
  RoadmapPrd,
  RoadmapSettings,
  RoadmapActionResult,
} from "./types";

const BASE_PATH = "/v1/agent-runner/roadmap";

export async function fetchRoadmapPrds(params: {
  repoId: string;
  includeArchived?: boolean;
}): Promise<{ prds: RoadmapPrd[]; repo_id: string; include_archived: boolean; scanned_at: string }> {
  const searchParams = new URLSearchParams();
  searchParams.set("repo_id", params.repoId);
  if (params.includeArchived) {
    searchParams.set("include_archived", "true");
  }
  return get(`${BASE_PATH}/prds?${searchParams.toString()}`);
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
