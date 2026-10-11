"use client";

// PRD 验收证据视图：按需拉取受限 manifest，并升级为「计划 vs 产出」分组视图。
//
// 事实源分两层：文件清单、预览、下载全部来自后端解析出的真实证据目录（manifest），
// 绝不用验收勾选数冒充文件数；计划条目、已产出/文件计数与影响/报告状态来自详情面板
// 一次拉取的聚合进度读数（progress），本视图复用而不重复解析。二者口径都是**弱信号**
// （文件归属 ≠ 验证通过），页面文案必须披露这一点。
//
// 降级铁律：读取失败/来源不可用显示「暂不可用」，与「确认无文件」的空态分开；无验证
// 计划时隐藏计划分组不伪造数字；同名文件的来源以 manifest 的 `source` 标注（工作区=未合并）。

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { buildPrdEvidenceArtifactUrl, fetchPrdEvidence } from "@/lib/api/backlog";
import type {
  BacklogEvidenceFile,
  BacklogPrdEvidenceManifest,
  BacklogPrdProgress,
} from "@/lib/api/types";

const ROLE_LABELS: Record<BacklogEvidenceFile["role"], string> = {
  evidence_report: "证据报告",
  verifier_report: "验收报告",
  verification_plan: "验证计划",
  artifact: "附件",
};

/** 归属来源 -> 中文说明（弱信号，仅表示文件如何对应到验证编号）。 */
const ATTRIBUTION_LABELS: Record<string, string> = {
  manifest: "结构化清单",
  filename: "文件名前缀",
  mixed: "清单+前缀",
  none: "无",
};

/** 计划条目 reviewer -> 人验/机验标签。 */
const REVIEWER_LABELS: Record<string, string> = {
  human: "人验",
  verifier: "机验",
};

type EvidenceState =
  | { status: "loading" }
  | { status: "ready"; manifest: BacklogPrdEvidenceManifest }
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
function isPreviewableImage(file: BacklogEvidenceFile): boolean {
  return file.media_type.startsWith("image/");
}

/**
 * 从证据文件名解析验证编号（仅用于展示分组，权威计数仍来自后端）。
 *
 * @param fileName - manifest 中的纯文件名。
 * @returns 命中 ``rv-<n>`` 前缀时返回 ``n``，否则 ``null``（报告/附件归入「其他」）。
 */
function rvNumberFromName(fileName: string): number | null {
  const match = /^rv-(\d+)[-.]/i.exec(fileName);
  return match === null ? null : Number(match[1]);
}

/**
 * 把报告三件套的「是否已产出」折算成简短文案。
 *
 * @param produced - 后端读到的布尔；``null`` 表示读取不完整、未知。
 * @returns ``已产出`` / ``未产出`` / ``未知``。
 */
function reportLabel(produced: boolean | null): string {
  if (produced === null) {
    return "未知";
  }
  return produced ? "已产出" : "未产出";
}

/**
 * 单个证据文件行的操作区（预览 / 图片缩略 / 下载 / 未合并徽标）。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 仓库相对路径。
 * @param props.file - 待渲染的证据文件。
 * @param props.onPreview - 文本类文件预览回调。
 * @returns 文件行右侧控件。
 */
function FileActions({
  repoId,
  prdPath,
  file,
  onPreview,
}: {
  repoId: string;
  prdPath: string;
  file: BacklogEvidenceFile;
  onPreview: (file: BacklogEvidenceFile) => void;
}) {
  const artifactUrl = buildPrdEvidenceArtifactUrl(repoId, prdPath, file.artifact_token);
  return (
    <>
      {file.source === "worktree" ? (
        <Badge variant="running" data-testid={`prd-evidence-unmerged-${file.name}`}>
          未合并
        </Badge>
      ) : null}
      <span className="text-xs text-slate-500">{formatSize(file.size_bytes)}</span>
      {file.media_type.startsWith("text/") ? (
        <Button variant="outline" size="sm" onClick={() => onPreview(file)}>
          预览
        </Button>
      ) : null}
      {isPreviewableImage(file) ? (
        <img
          src={artifactUrl}
          alt={file.name}
          className="max-h-32 rounded border border-slate-200 dark:border-slate-800"
        />
      ) : null}
      <Button variant="outline" size="sm" asChild>
        <a href={artifactUrl} target="_blank" rel="noreferrer" download={file.name}>
          下载
        </a>
      </Button>
    </>
  );
}

/**
 * 渲染某个证据文件行（角色标签 + 文件名 + 操作区）。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 仓库相对路径。
 * @param props.file - 待渲染的证据文件。
 * @param props.onPreview - 文本类文件预览回调。
 * @returns 列表项。
 */
function EvidenceFileRow({
  repoId,
  prdPath,
  file,
  onPreview,
}: {
  repoId: string;
  prdPath: string;
  file: BacklogEvidenceFile;
  onPreview: (file: BacklogEvidenceFile) => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-3 py-2">
      <span className="text-xs text-slate-400">{ROLE_LABELS[file.role]}</span>
      <span className="min-w-0 flex-1 truncate text-sm" title={file.name}>
        {file.name}
      </span>
      <FileActions repoId={repoId} prdPath={prdPath} file={file} onPreview={onPreview} />
    </li>
  );
}

/**
 * 渲染证据标签顶部的「计划 N 项 · 已产出 M 项（共 K 个文件）」汇总行。
 *
 * @param props.progress - 详情面板一次拉取的聚合进度读数。
 * @returns 汇总段落；来源不可用时显示「暂不可用」而非伪造计数。
 */
function EvidenceSummaryRow({ progress }: { progress: BacklogPrdProgress }) {
  const { evidence } = progress;
  if (evidence.status === "unavailable" || evidence.planned === null) {
    return (
      <p className="text-xs text-slate-500" data-testid="prd-evidence-summary">
        证据进度暂不可用（无法确定来源，不代表没有文件）。
      </p>
    );
  }
  const plannedCount = evidence.planned.length;
  const producedCount = evidence.produced_planned_count ?? 0;
  const fileCount = evidence.file_count ?? 0;
  const attribution = ATTRIBUTION_LABELS[evidence.attribution_source] ?? evidence.attribution_source;
  return (
    <div className="space-y-1" data-testid="prd-evidence-summary">
      <p className="text-sm">
        {plannedCount === 0 ? (
          <span className="text-slate-500">该 PRD 未声明验证计划</span>
        ) : (
          <span>
            计划 {plannedCount} 项 · 已产出 {producedCount} 项（共 {fileCount} 个文件）
          </span>
        )}
      </p>
      <p className="text-xs text-slate-500">
        报告三件套：验证计划 {reportLabel(evidence.reports.verification_plan)} · 证据报告{" "}
        {reportLabel(evidence.reports.evidence_report)} · 独立验证{" "}
        {reportLabel(evidence.reports.verifier_report)}
      </p>
      <p className="text-xs text-slate-400" title="文件归属 ≠ 验证通过；这里只回答有几份产物文件。">
        归属来源：{attribution}（弱信号，文件归属 ≠ 通过）
      </p>
    </div>
  );
}

/**
 * 按验证计划条目分组渲染证据文件，未产出条目占位，计划外/无编号文件归入「其他」。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 仓库相对路径。
 * @param props.files - 合并去重后的可展示文件清单。
 * @param props.progress - 聚合进度读数（提供计划条目）。
 * @param props.onPreview - 文本类文件预览回调。
 * @returns 分组列表；无验证计划时返回 ``null``（由上层回落到平铺列表）。
 */
function PlanGroupedList({
  repoId,
  prdPath,
  files,
  progress,
  onPreview,
}: {
  repoId: string;
  prdPath: string;
  files: BacklogEvidenceFile[];
  progress: BacklogPrdProgress;
  onPreview: (file: BacklogEvidenceFile) => void;
}) {
  const planned = progress.evidence.planned;
  if (planned === null || planned.length === 0) {
    return null;
  }
  const plannedNumbers = new Map<number, typeof planned[number]>();
  for (const item of planned) {
    const numberMatch = /^rv-(\d+)$/.exec(item.id);
    if (numberMatch !== null) {
      plannedNumbers.set(Number(numberMatch[1]), item);
    }
  }
  const grouped = new Map<number, BacklogEvidenceFile[]>();
  const others: BacklogEvidenceFile[] = [];
  for (const file of files) {
    const number = rvNumberFromName(file.name);
    if (number !== null && plannedNumbers.has(number)) {
      const bucket = grouped.get(number);
      if (bucket === undefined) {
        grouped.set(number, [file]);
      } else {
        bucket.push(file);
      }
    } else {
      others.push(file);
    }
  }

  return (
    <div className="space-y-4" data-testid="prd-evidence-plan-groups">
      {planned.map((item) => {
        const numberMatch = /^rv-(\d+)$/.exec(item.id);
        const number = numberMatch === null ? null : Number(numberMatch[1]);
        const itemFiles = number === null ? [] : grouped.get(number) ?? [];
        return (
          <div key={item.id} className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-500">{item.id}</span>
              <span className="text-sm font-medium" title={item.summary}>
                {item.summary || "（无摘要）"}
              </span>
              {item.reviewer ? (
                <span className="text-xs text-slate-400">{REVIEWER_LABELS[item.reviewer] ?? item.reviewer}</span>
              ) : null}
              <Badge variant={itemFiles.length > 0 ? "ready" : "default"}>
                {itemFiles.length > 0 ? `已产出 ${itemFiles.length} 个文件` : "待产出"}
              </Badge>
            </div>
            {itemFiles.length > 0 ? (
              <ul className="divide-y divide-slate-200 pl-1 dark:divide-slate-800">
                {itemFiles.map((file) => (
                  <EvidenceFileRow
                    key={file.name}
                    repoId={repoId}
                    prdPath={prdPath}
                    file={file}
                    onPreview={onPreview}
                  />
                ))}
              </ul>
            ) : (
              <p className="pl-1 text-xs text-slate-400">该条目暂无可读取的产物文件。</p>
            )}
          </div>
        );
      })}

      {others.length > 0 ? (
        <div className="space-y-1" data-testid="prd-evidence-others">
          <p className="text-sm font-medium text-slate-500">其他（报告 / 附件 / 计划外文件）</p>
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {others.map((file) => (
              <EvidenceFileRow
                key={file.name}
                repoId={repoId}
                prdPath={prdPath}
                file={file}
                onPreview={onPreview}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 渲染某个 PRD 的验收证据标签页内容。
 *
 * @param props.repoId - 仓库标识。
 * @param props.prdPath - PRD 的仓库相对路径（取自列表响应）。
 * @param props.progress - 详情面板一次拉取的聚合进度读数（用于分组与汇总）；为
 *   ``null``（进度读取中/失败）时本视图优雅回落到平铺文件列表。
 * @returns 证据清单视图，含计划分组、来源标注、空态、错误态与重试。
 */
export function PrdEvidenceView({
  repoId,
  prdPath,
  progress = null,
}: {
  repoId: string;
  prdPath: string;
  progress?: BacklogPrdProgress | null;
}) {
  const [evidenceState, setEvidenceState] = useState<EvidenceState>({ status: "loading" });
  const [previewFile, setPreviewFile] = useState<BacklogEvidenceFile | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let isCancelled = false;
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

  async function handlePreview(file: BacklogEvidenceFile) {
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

  if (manifest.status === "unavailable") {
    return (
      <div
        role="alert"
        data-testid="prd-evidence-unavailable"
        className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
      >
        <p className="font-medium">证据来源暂不可用</p>
        <p className="mt-1 text-xs">
          无法确定该 PRD 的执行工作区（发现失败或多重匹配），因此不能列出证据文件——这
          <strong>不代表</strong>磁盘上没有证据。请稍后重试或核对工作区是否唯一命中。
        </p>
      </div>
    );
  }

  if (!manifest.exists || manifest.files.length === 0) {
    return (
      <div className="space-y-4">
        {progress ? <EvidenceSummaryRow progress={progress} /> : null}
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
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="prd-evidence-body">
      {progress ? <EvidenceSummaryRow progress={progress} /> : null}
      <p className="text-xs text-slate-500">
        共 {manifest.files.length} 个文件 · 来自 <code>{manifest.evidence_dir}</code>
      </p>

      {progress && progress.evidence.planned && progress.evidence.planned.length > 0 ? (
        <PlanGroupedList
          repoId={repoId}
          prdPath={prdPath}
          files={manifest.files}
          progress={progress}
          onPreview={handlePreview}
        />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {manifest.files.map((file) => (
            <EvidenceFileRow
              key={file.name}
              repoId={repoId}
              prdPath={prdPath}
              file={file}
              onPreview={handlePreview}
            />
          ))}
        </ul>
      )}

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
