"use client";

// 人审页模态框：把证据目录里的 human-review-checklist.md 就地渲染出来。
//
// 只看 Markdown 正文，不执行仓库里的 HTML——证据文件不能拿到 Console 同源上下文
// 的脚本能力。页面内的相对图片路径改写到证据取图端点，否则在弹窗里必然是坏图。
// 图片可点开放大，放大层支持方向键在页面内所有图片之间切换。

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

type HumanReviewPageState =
  | { status: "loading" }
  | { status: "ready"; markdown: string; imageBase: string; path: string; source: string }
  | { status: "error"; message: string };

/** 人审页正文接口的响应形状。 */
type HumanReviewPagePayload = {
  path: string;
  markdown: string;
  image_base: string;
  /** 证据来源：local 表示仓库本地目录，其他值是证据分支名。 */
  source?: string;
};

/**
 * 把人审页里的图片地址补成可取图的绝对路径。
 *
 * @param src - Markdown 里写的地址，可能是相对文件名。
 * @param imageBase - 后端给的人审页取图前缀。
 * @returns 可直接交给 img 的地址；地址不合法时返回空串（渲染成空图而不是请求错地址）。
 */
function resolveImageSrc(src: unknown, imageBase: string): string {
  if (typeof src !== "string" || src === "") {
    return "";
  }
  return /^https?:/.test(src) ? src : `${imageBase}${src}`;
}

/**
 * 按出现顺序取出正文里全部图片地址，作为放大层的切换顺序。
 *
 * @param markdown - 人审页正文。
 * @param imageBase - 后端给的人审页取图前缀。
 * @returns 去重后的图片地址列表。
 */
function collectImageSources(markdown: string, imageBase: string): string[] {
  const pattern = /!\[[^\]]*\]\(([^)\s]+)\)/g;
  const sources: string[] = [];
  for (const match of markdown.matchAll(pattern)) {
    const resolved = resolveImageSrc(match[1], imageBase);
    if (resolved && !sources.includes(resolved)) {
      sources.push(resolved);
    }
  }
  return sources;
}

/**
 * 读取某个任务的人审页正文。
 *
 * @param repoId - 注册仓库标识。
 * @param issueNumber - Issue 编号。
 * @param prdPath - 该 Issue 关联的 PRD 相对路径。
 * @returns 人审页接口响应。
 */
async function fetchHumanReviewPage(
  repoId: string,
  issueNumber: number,
  prdPath: string | null,
): Promise<HumanReviewPagePayload> {
  const params = new URLSearchParams({ repo_id: repoId, issue: String(issueNumber) });
  if (prdPath) params.set("prd_path", prdPath);
  const response = await fetch(
    `/api/v1/agent-runner/console/runtime/evidence/page?${params.toString()}`,
    { credentials: "include" },
  );
  if (!response.ok) {
    throw new Error(`读取人审页失败（HTTP ${response.status}）`);
  }
  return (await response.json()) as HumanReviewPagePayload;
}

/**
 * 人审页模态框：加载中、失败与正文三种状态各自可读，失败不阻塞看板。
 *
 * @param props - 任务身份、证据定位参数与关闭回调。
 * @returns 覆盖全屏的阅读层。
 */
export function RuntimeTaskHumanReviewPage({
  repoId,
  issueNumber,
  prdPath,
  identity,
  onClose,
}: {
  repoId: string;
  issueNumber: number;
  prdPath: string | null;
  identity: string;
  onClose: () => void;
}) {
  const [state, setState] = useState<HumanReviewPageState>({ status: "loading" });
  const [zoomIndex, setZoomIndex] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchHumanReviewPage(repoId, issueNumber, prdPath)
      .then((payload) => {
        if (!cancelled) {
          setState({
            status: "ready",
            markdown: payload.markdown,
            imageBase: payload.image_base,
            path: payload.path,
            source: payload.source ?? "local",
          });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : "读取人审页失败。",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [repoId, issueNumber, prdPath]);

  const images = useMemo(
    () => (state.status === "ready" ? collectImageSources(state.markdown, state.imageBase) : []),
    [state],
  );

  // 放大层打开时才挂键盘监听：左右切图、Esc 只退出放大，不关掉人审页本身。
  useEffect(() => {
    if (zoomIndex === null) {
      return;
    }
    /**
     * 放大层键盘操作：左右切图、Esc 只退出放大层。
     *
     * @param event - 浏览器键盘事件。
     */
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        setZoomIndex(null);
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        setZoomIndex((current) => {
          if (current === null || images.length === 0) return current;
          const delta = event.key === "ArrowLeft" ? -1 : 1;
          return (current + delta + images.length) % images.length;
        });
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [zoomIndex, images.length]);

  const zoomedSrc = zoomIndex === null ? null : images[zoomIndex];

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/70 p-6"
      role="dialog"
      aria-modal="true"
      aria-label={`${identity} 人审页`}
      data-testid="runtime-task-evidence-page-dialog"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl rounded-lg bg-white p-4 shadow-xl dark:bg-slate-900"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between gap-3 text-sm">
          <span className="font-medium text-slate-900 dark:text-slate-100">
            {identity} 人审页
            {state.status === "ready" ? (
              <span className="ml-2 text-xs font-normal text-slate-500">
                {state.path}
                {state.source !== "local" ? ` · 来自 ${state.source}` : ""}
              </span>
            ) : null}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-200 px-2 py-0.5 text-xs dark:border-slate-700"
          >
            关闭
          </button>
        </div>
        {state.status === "loading" ? (
          <p className="py-8 text-center text-sm text-slate-500">正在读取人审页…</p>
        ) : null}
        {state.status === "error" ? (
          <p className="py-8 text-center text-sm text-red-600">{state.message}</p>
        ) : null}
        {state.status === "ready" ? (
          <div
            className="prd-markdown max-w-none text-sm"
            data-testid="runtime-task-evidence-page-body"
          >
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                img: ({ src, alt }) => {
                  const resolved = resolveImageSrc(src, state.imageBase);
                  const index = images.indexOf(resolved);
                  return (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={resolved}
                      alt={alt ?? ""}
                      className="max-w-full cursor-zoom-in rounded-md border border-slate-200"
                      onClick={(event) => {
                        event.stopPropagation();
                        setZoomIndex(index >= 0 ? index : 0);
                      }}
                      data-testid="runtime-task-evidence-page-image"
                    />
                  );
                },
              }}
            >
              {state.markdown}
            </Markdown>
          </div>
        ) : null}
      </div>
      {zoomedSrc ? (
        <div
          className="fixed inset-0 z-[60] flex cursor-zoom-out items-center justify-center bg-slate-950/85 p-6"
          role="dialog"
          aria-modal="true"
          aria-label="查看大图"
          data-testid="runtime-task-evidence-page-zoom"
          onClick={(event) => {
            // 只退出放大层：事件不能再冒泡到人审页遮罩，否则会连页面一起关掉。
            event.stopPropagation();
            setZoomIndex(null);
          }}
        >
          {images.length > 1 && zoomIndex !== null ? (
            <button
              type="button"
              aria-label="上一张"
              className="absolute left-6 rounded-full bg-white/90 p-2 text-slate-900 shadow hover:bg-white"
              onClick={(event) => {
                event.stopPropagation();
                setZoomIndex((zoomIndex - 1 + images.length) % images.length);
              }}
            >
              <ChevronLeft size={18} aria-hidden />
            </button>
          ) : null}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={zoomedSrc}
            alt=""
            className="max-h-[90vh] max-w-full cursor-zoom-out rounded-md bg-white shadow-2xl"
          />
          {images.length > 1 && zoomIndex !== null ? (
            <button
              type="button"
              aria-label="下一张"
              className="absolute right-6 rounded-full bg-white/90 p-2 text-slate-900 shadow hover:bg-white"
              onClick={(event) => {
                event.stopPropagation();
                setZoomIndex((zoomIndex + 1) % images.length);
              }}
            >
              <ChevronRight size={18} aria-hidden />
            </button>
          ) : null}
          {images.length > 1 && zoomIndex !== null ? (
            <span className="absolute bottom-6 rounded-full bg-slate-900/80 px-3 py-1 text-xs text-white">
              {zoomIndex + 1} / {images.length} · ←/→ 切换 · Esc 关闭
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
