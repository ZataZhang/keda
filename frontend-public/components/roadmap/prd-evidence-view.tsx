"use client";

// PRD 验收证据视图：按需拉取受限 manifest，并支持报告预览与附件下载。
//
// 事实源是仓库里仍然保留的证据目录——不是 Issue 关闭后会被清理的临时 orphan
// 分支。文件名、大小与数量全部来自后端解析出的真实目录，绝不用验收清单勾选数
// 推断；无目录时显示解析位置作为明确空态。

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { buildPrdEvidenceArtifactUrl, fetchPrdEvidence } from "@/lib/api/roadmap";
import type {
  RoadmapEvidenceFile,
  RoadmapPrdEvidenceManifest,
} from "@/lib/api/types";

const ROLE_LABELS: Record<RoadmapEvidenceFile["role"], string> = {
  evidence_report: "证据报告",
  verifier_report: "验收报告",
  verification_plan: "验证计划",
  artifact: "附件",
};

type EvidenceState =
  | { status: "loading" }
  | { status: "ready"; manifest: RoadmapPrdEvidenceManifest }
  | { status: "error"; message: string };

/**
 * 把字节数渲染为人类可读的大小。
 *
 * @param sizeBytes - 文件字节数（来自后端真实 stat）。
 * @returns 形如 ``12.3 KB`` 的字符串。
 */
function formatSize(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 判断该文件是否可以直接内联预览（文本类也可以，但 MARKDOWN 走独立预览页）。
 *
 * @param file - manifest 中的一个文件条目。
 * @returns 图片类型时为 ``true``。
 */
function isPreviewableImage(file: RoadmapEvidenceFile): boolean {
  return file.media_type.startsWith("image/");
}

/**
 * 渲染某个 PRD 的验收证据标签页内容。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 的仓库相对路径（取自列表响应）。
 * @returns 证据清单视图，含空态、错误态与重试。
 */
export function PrdEvidenceView({ repoId, prdPath }: { repoId: string; prdPath: string }) {
  const [evidenceState, setEvidenceState] = useState<EvidenceState>({ status: "loading" });
  const [previewFile, setPreviewFile] = useState<RoadmapEvidenceFile | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let isCancelled = false;
    setEvidenceState({ status: "loading" });
    fetchPrdEvidence(repoId, prdPath)
      .then((manifest) => {
        if (!isCancelled) {
          setEvidenceState({ status: "ready", manifest });
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setEvidenceState({
            status: "error",
            message: error instanceof Error ? error.message : "读取验收证据失败。",
          });
        }
      });
    return () => {
      isCancelled = true;
    };
  }, [repoId, prdPath, reloadToken]);

  async function handlePreview(file: RoadmapEvidenceFile) {
    setPreviewFile(file);
    setPreviewError(null);
    setPreviewText(null);
    const url = buildPrdEvidenceArtifactUrl(repoId, prdPath, file.artifact_token);
    try {
      const response = await fetch(url);
      if (!response.ok) {
        setPreviewError(`预览失败（HTTP ${response.status}）。`);
        return;
      }
      setPreviewText(await response.text());
    } catch (error: unknown) {
      setPreviewError(error instanceof Error ? error.message : "预览失败。");
    }
  }

  if (evidenceState.status === "loading") {
    return (
      <div className="space-y-3" data-testid="prd-evidence-loading">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    );
  }

  if (evidenceState.status === "error") {
    return (
      <div
        role="alert"
        data-testid="prd-evidence-error"
        className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
      >
        <p className="font-medium">读取验收证据失败</p>
        <p className="mt-1">{evidenceState.message}</p>
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={() => setReloadToken((token) => token + 1)}
        >
          重试
        </Button>
      </div>
    );
  }

  const manifest = evidenceState.manifest;

  if (!manifest.exists || manifest.files.length === 0) {
    return (
      <div
        data-testid="prd-evidence-empty"
        className="rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300"
      >
        <p className="font-medium">尚无可用证据</p>
        <p className="mt-1 text-xs text-slate-500">
          已查找位置：<code>{manifest.evidence_dir || "未配置证据目录"}</code>
        </p>
        <p className="mt-1 text-xs text-slate-500">
          归档后长期可见的是仓库保留的 evidence 目录；Issue 关闭后清理的临时证据分支不在范围内。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="prd-evidence-body">
      <p className="text-xs text-slate-500">
        共 {manifest.files.length} 个文件 · 来自 <code>{manifest.evidence_dir}</code>
      </p>

      <ul className="divide-y divide-slate-200 dark:divide-slate-800">
        {manifest.files.map((file) => (
          <li key={file.name} className="flex flex-wrap items-center gap-3 py-2">
            <span className="text-xs text-slate-400">{ROLE_LABELS[file.role]}</span>
            <span className="min-w-0 flex-1 truncate text-sm" title={file.name}>
              {file.name}
            </span>
            <span className="text-xs text-slate-500">{formatSize(file.size_bytes)}</span>
            {file.media_type.startsWith("text/") ? (
              <Button variant="outline" size="sm" onClick={() => void handlePreview(file)}>
                预览
              </Button>
            ) : null}
            {isPreviewableImage(file) ? (
              <img
                src={buildPrdEvidenceArtifactUrl(repoId, prdPath, file.artifact_token)}
                alt={file.name}
                className="max-h-32 rounded border border-slate-200 dark:border-slate-800"
              />
            ) : null}
            <Button variant="outline" size="sm" asChild>
              <a
                href={buildPrdEvidenceArtifactUrl(repoId, prdPath, file.artifact_token)}
                target="_blank"
                rel="noreferrer"
                download={file.name}
              >
                下载
              </a>
            </Button>
          </li>
        ))}
      </ul>

      {previewFile ? (
        <div className="space-y-2">
          <p className="text-sm font-medium">预览：{previewFile.name}</p>
          {previewError ? (
            <p role="alert" data-testid="prd-evidence-preview-error" className="text-sm text-red-600">
              {previewError}
            </p>
          ) : previewText === null ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <pre
              data-testid="prd-evidence-preview"
              className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 p-3 text-xs dark:border-slate-800"
            >
              {previewText}
            </pre>
          )}
        </div>
      ) : null}
    </div>
  );
}
