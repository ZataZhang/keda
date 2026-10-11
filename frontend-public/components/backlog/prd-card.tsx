import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { BacklogPrd } from "@/lib/api/types";

export const STATE_LABELS: Record<BacklogPrd["state"], string> = {
  not_started: "未开始",
  ready: "就绪",
  running: "运行中",
  supervising: "监督中",
  review: "待审阅",
  failed: "失败",
  blocked: "阻塞",
  merged: "已合并",
  archived: "已归档",
  unresolved_dependency: "依赖未解析",
  waiting: "等待中",
};

export const STATE_VARIANTS: Record<BacklogPrd["state"], BadgeVariant> = {
  not_started: "default",
  ready: "ready",
  running: "running",
  supervising: "supervising",
  review: "review",
  failed: "failed",
  blocked: "blocked",
  merged: "warning",
  archived: "default",
  unresolved_dependency: "error",
  waiting: "warning",
};

interface PrdCardProps {
  prd: BacklogPrd;
  onStart: () => void;
  onOpenContent: () => void;
  starting: boolean;
  /** 「加入就绪」回调：建 Issue + 打就绪标签但不启动 runner（FR-3）。 */
  onEnqueueReady?: () => void;
  /** 该 PRD 是否正在执行「加入就绪」。 */
  enqueuing?: boolean;
}

/**
 * 统一的「能否开始此 PRD」判定：卡片、依赖图详情头部共用同一份规则。
 *
 * 允许开始的状态为 not_started / ready / failed / waiting，且 block_reason
 * 为空。``ready``（agent/ready 就绪态）表示 Issue 已在队列等待 daemon 领取；
 * 队列没有消费者（daemon 未运行、autopilot 关闭）时，点击是唯一的显式启动
 * 入口——后端会定向执行该 Issue（``kc run --issue N``），不重复打标签。
 * 运行中 / 监督中 / 待审阅 / 已合并 / 已归档等仍不可启动。任何视图都不得
 * 再复制一份判断，否则默认值（依赖图）会与列表行为漂移。
 *
 * @param prd - 列表响应中的 PRD 条目。
 * @returns 允许开始该 PRD 时为 true。
 */
export function canStartBacklogPrd(prd: BacklogPrd): boolean {
  const stateAllowsStart =
    prd.state === "not_started" ||
    prd.state === "ready" ||
    prd.state === "failed" ||
    prd.state === "waiting";
  return stateAllowsStart && !prd.block_reason;
}

/**
 * 「开始」操作的悬停提示：卡片与详情头部共用，区分依赖阻塞、不可启动与
 * 已就绪（点击 = 立即定向执行本轮，不等队列）。
 *
 * @param prd - 列表响应中的 PRD 条目。
 * @returns 按钮 title 文案。
 */
export function startBacklogPrdHint(prd: BacklogPrd): string {
  if (prd.block_reason) {
    return `被依赖阻塞：${prd.block_reason}`;
  }
  if (!canStartBacklogPrd(prd)) {
    return "当前状态不可启动";
  }
  if (prd.state === "ready") {
    return "已就绪：立即定向执行本轮，不再等待队列领取";
  }
  return "开始此 PRD";
}

export function PrdCard({
  prd,
  onStart,
  onOpenContent,
  starting,
  onEnqueueReady,
  enqueuing,
}: PrdCardProps) {
  const progress =
    prd.acceptance_total > 0
      ? Math.round((prd.acceptance_checked / prd.acceptance_total) * 100)
      : 0;

  const isStartable = canStartBacklogPrd(prd);

  const highlightClass =
    prd.state === "review"
      ? "border-amber-400 dark:border-amber-600"
      : prd.state === "merged"
        ? "border-emerald-400 dark:border-emerald-600"
        : undefined;

  return (
    <Card className={highlightClass}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="truncate text-base" title={prd.title}>
              {prd.title}
            </CardTitle>
            <p className="mt-0.5 truncate text-xs text-slate-500" title={prd.prd_path}>
              {prd.prd_path}
            </p>
          </div>
          <Badge variant={STATE_VARIANTS[prd.state]} className="shrink-0">
            {STATE_LABELS[prd.state]}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-500">进度</span>
          <Progress value={progress} className="h-2 flex-1" />
          <span className="text-xs text-slate-500">{progress}%</span>
        </div>

        {prd.issue_number ? (
          <p className="text-xs text-slate-500">
            Issue #{prd.issue_number}
            {prd.issue_url ? (
              <a
                href={prd.issue_url}
                target="_blank"
                rel="noreferrer"
                className="ml-1 text-blue-600 hover:underline"
              >
                查看
              </a>
            ) : null}
          </p>
        ) : null}

        {prd.block_reason ? (
          <p className="text-xs text-red-600">{prd.block_reason}</p>
        ) : null}

        {prd.delivery_dependencies.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {prd.delivery_dependencies.map((dep) => (
              <span
                key={`${dep.from_path}-${dep.to_path}`}
                className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-400"
                title={dep.detail}
              >
                {dep.kind === "prd" ? "→" : dep.kind === "issue" ? "#" : "?"} {dep.to_path}
              </span>
            ))}
          </div>
        ) : null}

        <div className="flex items-center gap-2 pt-1">
          <Button
            size="sm"
            variant="outline"
            onClick={onOpenContent}
            data-testid="prd-open-content"
            data-prd-path={prd.prd_path}
          >
            查看原文
          </Button>
          {isStartable ? (
            <Button
              size="sm"
              onClick={onStart}
              disabled={starting || !!prd.block_reason}
              data-testid="prd-card-start"
              data-prd-path={prd.prd_path}
              title={startBacklogPrdHint(prd)}
            >
              {starting ? "启动中…" : "开始"}
            </Button>
          ) : null}
          {onEnqueueReady && prd.state === "not_started" ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onEnqueueReady}
              disabled={enqueuing || !!prd.block_reason}
              data-testid="prd-enqueue-ready"
              data-prd-path={prd.prd_path}
              title="建 Issue（若无）并打就绪标签，但不启动 runner"
            >
              {enqueuing ? "入队中…" : "加入就绪"}
            </Button>
          ) : null}
          {prd.next_action?.url ? (
            <Button size="sm" variant="outline" asChild>
              <a href={prd.next_action.url} target="_blank" rel="noreferrer">
                {prd.next_action.label}
              </a>
            </Button>
          ) : prd.next_action ? (
            <span className="text-xs text-slate-500">{prd.next_action.label}</span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
