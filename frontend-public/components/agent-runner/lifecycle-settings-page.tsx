"use client";

// 生命周期、模型预设与执行器回退的统一设置页（全局层 / 仓库层共用）。
//
// 一个页面同时承载三块，全部读自同一份聚合视图 `GET /lifecycle-settings`
// （core 层把矩阵 / 预设 / 回退折叠成同一解析结果，页面与 CLI 不再各算各的）：
// - 生命周期矩阵：九阶段各自的最终生效 agent / 模型 / 推理深度 + 逐字段来源，
//   通过「绑定预设」设定显式值（预设是原子三元组，遮蔽矩阵同键声明）；
// - 模型预设：新建 / 编辑 / 删除命名预设，编辑前提示共享该预设的受影响阶段；
// - 执行器回退候选：为每个回退候选绑定同一执行器的命名预设。
//
// 写回保持保留式语义：矩阵只提交改动过的预设与绑定（一次 PATCH），回退候选按
// 完整期望数组写回（PUT）。二者目标文件不同，互不牵连。

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ResourceErrorAlert } from "@/components/agent-runner/resource-error-alert";
import { cn } from "@/lib/utils";
import { fetchRegistryRepositories } from "@/lib/api/console";
import {
  fetchLifecycleSettings,
  updateAgentFallbackCandidates,
  updateLifecycleSettings,
} from "@/lib/api/lifecycleSettings";
import type { RegistryRepositoryEntry } from "@/lib/api/types";
import type {
  AgentPresetPayload,
  FallbackCandidateWriteEntry,
  LifecycleAgentScope,
  LifecycleSettingsRow,
  LifecycleSettingsView,
} from "@/lib/api/types";

/** 配置层来源的中文标签（agent 字段来源取层名时用于可读展示）。 */
const LAYER_SOURCE_LABELS: Record<string, string> = {
  global: "全局 config.toml",
  repository: "仓库 .kedacode.toml",
  legacy: "既有配置键",
  builtin: "内置默认",
};

/** 预设定义来源层的中文标签。 */
const PRESET_SOURCE_LABELS: Record<string, string> = {
  global: "全局",
  repository_only: "本仓库独有",
  repository_overrides: "本仓库覆盖全局",
};

/**
 * 把一个逐字段来源值翻译成中文说明：先查后端词表，再查层名标签，最后原样返回。
 *
 * @param value - 字段来源值（预设 / 继承 / 默认 / 不支持，或配置层名）。
 * @param vocabulary - 后端下发的来源标签词表。
 * @returns 可读的中文来源说明。
 */
function describeFieldSource(
  value: string | null,
  vocabulary: Record<string, string>,
): string {
  if (!value) {
    return "—";
  }
  return vocabulary[value] ?? LAYER_SOURCE_LABELS[value] ?? value;
}

/** 展示某个可选值：有值显示值，否则按来源显示占位说明。 */
function displayOptionalField(
  value: string | null,
  source: string,
  vocabulary: Record<string, string>,
): string {
  if (value) {
    return value;
  }
  if (source === "not_supported") {
    return "该 Agent 不支持";
  }
  return describeFieldSource(source, vocabulary);
}

interface PresetOption {
  value: string;
  label: string;
  isNew?: boolean;
}

interface PresetSelectProps {
  /** 当前选中值（空串表示未绑定）。 */
  value: string;
  /** 候选预设（含本次新建、尚未落盘的预设）。 */
  options: PresetOption[];
  /** 选中值变更回调。 */
  onChange: (value: string) => void;
  /** data-testid 前缀。 */
  testId: string;
  /** 未绑定时的占位文案。 */
  placeholder?: string;
  /** 是否允许清空（绑定选择器用）。 */
  allowClear?: boolean;
}

/**
 * 预设单选下拉（绑定阶段与回退候选共用）。
 *
 * @param props - 当前值、候选、回调与测试 id。
 * @returns 下拉触发按钮 + 预设候选菜单。
 */
function PresetSelect({
  value,
  options,
  onChange,
  testId,
  placeholder = "未绑定",
  allowClear = false,
}: PresetSelectProps) {
  const currentLabel = options.find((option) => option.value === value)?.label;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-full justify-between"
          data-testid={testId}
        >
          <span className="truncate">{currentLabel || placeholder}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-48">
        <DropdownMenuRadioGroup value={value} onValueChange={onChange}>
          {allowClear ? (
            <DropdownMenuRadioItem
              value=""
              data-testid={`${testId}-option-clear`}
            >
              {placeholder}
            </DropdownMenuRadioItem>
          ) : null}
          {options.map((option) => (
            <DropdownMenuRadioItem
              key={option.value}
              value={option.value}
              data-testid={`${testId}-option-${option.value}`}
            >
              <span className="flex flex-col">
                <span>{option.label}</span>
                {option.isNew ? (
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    本次新建（与绑定一同保存）
                  </span>
                ) : null}
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        {options.length === 0 ? (
          <DropdownMenuLabel className="text-xs text-slate-500">
            暂无可选预设
          </DropdownMenuLabel>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** 单个生命周期阶段的矩阵行（展示生效值 + 绑定预设）。 */
function MatrixRow({
  row,
  presetOptions,
  bindingValue,
  bindingChanged,
  vocabulary,
  onBindingChange,
}: {
  row: LifecycleSettingsRow;
  /** 可供绑定的预设（含本次新建）。 */
  presetOptions: PresetOption[];
  /** 当前绑定的预设名（空串=未绑定）。 */
  bindingValue: string;
  /** 本行绑定是否被改动（用于高亮）。 */
  bindingChanged: boolean;
  /** 后端下发的逐字段来源词表。 */
  vocabulary: Record<string, string>;
  /** 绑定变更回调。 */
  onBindingChange: (presetName: string) => void;
}) {
  const vocabularySafe = vocabulary;
  const affected = row.affected_stages.filter((stage) => stage !== row.key);
  const agentDisplay =
    row.effective_agent ?? (row.follows_implementation ? "跟随实现阶段" : "—");
  return (
    <div
      data-testid={`matrix-row-${row.key}`}
      className={cn(
        "grid grid-cols-1 gap-3 rounded-lg border p-3 lg:grid-cols-[minmax(7rem,1fr)_minmax(6rem,0.8fr)_repeat(2,minmax(6rem,1fr))_minmax(9rem,1.2fr)] lg:items-center",
        bindingChanged && "border-amber-400 bg-amber-50 dark:bg-amber-950/30",
      )}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium">{row.label}</p>
        {row.preset_name ? (
          <Badge variant="ready" className="mt-1 text-[10px]">
            已绑定 {row.preset_name}
          </Badge>
        ) : null}
        {row.preset_name ? (
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            绑定来源：{describeFieldSource(row.field_sources.preset, vocabularySafe)}
          </span>
        ) : null}
        {row.is_inherited ? (
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            继承实现阶段
          </span>
        ) : null}
      </div>

      <div className="min-w-0">
        <p className="text-[11px] uppercase text-slate-400">生效 Agent</p>
        <p
          className="truncate text-sm"
          data-testid={`matrix-agent-${row.key}`}
          title={agentDisplay}
        >
          {agentDisplay}
        </p>
        <p className="text-[11px] text-slate-500 dark:text-slate-400">
          {describeFieldSource(row.field_sources.agent, vocabularySafe)}
        </p>
      </div>

      <div className="min-w-0">
        <p className="text-[11px] uppercase text-slate-400">模型</p>
        <p
          className="truncate text-sm"
          data-testid={`matrix-model-${row.key}`}
        >
          {displayOptionalField(
            row.model,
            row.field_sources.model,
            vocabularySafe,
          )}
        </p>
        {row.model ? (
          <p
            className={cn(
              "text-[11px] text-slate-500 dark:text-slate-400",
              row.field_sources.model === "not_supported" &&
                "font-medium text-amber-600 dark:text-amber-400",
            )}
          >
            {describeFieldSource(row.field_sources.model, vocabularySafe)}
          </p>
        ) : null}
      </div>

      <div className="min-w-0">
        <p className="text-[11px] uppercase text-slate-400">推理深度</p>
        <p
          className="truncate text-sm"
          data-testid={`matrix-effort-${row.key}`}
        >
          {displayOptionalField(
            row.reasoning_effort,
            row.field_sources.reasoning_effort,
            vocabularySafe,
          )}
        </p>
        {row.reasoning_effort ? (
          <p
            className={cn(
              "text-[11px] text-slate-500 dark:text-slate-400",
              row.field_sources.reasoning_effort === "not_supported" &&
                "font-medium text-amber-600 dark:text-amber-400",
            )}
          >
            {describeFieldSource(row.field_sources.reasoning_effort, vocabularySafe)}
          </p>
        ) : null}
      </div>

      <div className="min-w-0">
        <Label className="text-[11px] text-slate-500">绑定预设</Label>
        <PresetSelect
          value={bindingValue}
          options={presetOptions}
          allowClear
          placeholder="未绑定（跟随既有配置）"
          onChange={onBindingChange}
          testId={`matrix-binding-${row.key}`}
        />
        {affected.length > 0 ? (
          <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
            该预设同时用于：
            {affected.join("、")}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/**
 * 模型预设编辑卡片（编辑已有预设的 agent / model / effort）。
 *
 * model / effort 是否会被注入由后端校验兜底（给值但目标 agent 缺参数模板会在保存时
 * fail-fast 并回显错误），因此这里不预判可注入性、也不禁用输入框。
 */
function PresetCard({
  name,
  agents,
  values,
  boundStages,
  sourceLabel,
  onFieldChange,
  onDelete,
}: {
  name: string;
  agents: string[];
  values: AgentPresetPayload;
  boundStages: string[];
  sourceLabel: string;
  onFieldChange: (next: AgentPresetPayload) => void;
  onDelete: () => void;
}) {
  return (
    <Card data-testid={`preset-card-${name}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{name}</CardTitle>
          <Badge variant="default" className="text-[10px]">
            {sourceLabel}
          </Badge>
        </div>
        <CardDescription className="text-xs">
          {boundStages.length > 0
            ? `绑定阶段：${boundStages.join("、")}`
            : "尚未绑定任何阶段"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div className="space-y-1">
            <Label className="text-[11px]">Agent</Label>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full justify-between"
                  data-testid={`preset-agent-${name}`}
                >
                  <span className="truncate">{values.agent}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="min-w-40">
                <DropdownMenuRadioGroup
                  value={values.agent}
                  onValueChange={(agentName) =>
                    onFieldChange({ ...values, agent: agentName })
                  }
                >
                  {agents.map((agentName) => (
                    <DropdownMenuRadioItem
                      key={agentName}
                      value={agentName}
                      data-testid={`preset-agent-${name}-${agentName}`}
                    >
                      {agentName}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <div className="space-y-1">
            <Label className="text-[11px]">模型</Label>
            <Input
              value={values.model ?? ""}
              placeholder="留空=沿用 CLI 默认"
              onChange={(event) =>
                onFieldChange({
                  ...values,
                  model: event.target.value || null,
                })
              }
              data-testid={`preset-model-${name}`}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-[11px]">推理深度</Label>
            <Input
              value={values.reasoning_effort ?? ""}
              placeholder="留空=沿用 CLI 默认"
              onChange={(event) =>
                onFieldChange({
                  ...values,
                  reasoning_effort: event.target.value || null,
                })
              }
              data-testid={`preset-effort-${name}`}
            />
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-red-600 dark:text-red-400"
          onClick={onDelete}
          data-testid={`preset-delete-${name}`}
        >
          删除预设（同时解绑其阶段）
        </Button>
      </CardContent>
    </Card>
  );
}

interface NewPresetDraft {
  name: string;
  agent: string;
  model: string;
  reasoningEffort: string;
}

/** 新建预设表单。 */
function NewPresetForm({
  agents,
  existingNames,
  onAdd,
}: {
  agents: string[];
  existingNames: string[];
  onAdd: (draft: NewPresetDraft) => void;
}) {
  const [draft, setDraft] = useState<NewPresetDraft>({
    name: "",
    agent: agents[0] ?? "",
    model: "",
    reasoningEffort: "",
  });

  /** 校验非空并提交新建预设。 */
  function handleSubmit() {
    const name = draft.name.trim();
    if (!name) {
      toast.error("请填写预设名称。");
      return;
    }
    if (existingNames.includes(name)) {
      toast.error(`预设「${name}」已存在，请直接编辑它。`);
      return;
    }
    if (!draft.agent) {
      toast.error("请选择预设的 Agent。");
      return;
    }
    onAdd(draft);
    setDraft({ name: "", agent: agents[0] ?? "", model: "", reasoningEffort: "" });
  }

  return (
    <div
      className="space-y-2 rounded-lg border border-dashed p-3"
      data-testid="new-preset-form"
    >
      <p className="text-sm font-medium">新建预设</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-1">
          <Label className="text-[11px]" htmlFor="new-preset-name">
            名称
          </Label>
          <Input
            id="new-preset-name"
            value={draft.name}
            placeholder="如 sonnet-5.5-max"
            onChange={(event) =>
              setDraft((c) => ({ ...c, name: event.target.value }))
            }
            data-testid="new-preset-name"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]">Agent</Label>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full justify-between"
                data-testid="new-preset-agent"
              >
                <span className="truncate">{draft.agent || "选择 Agent"}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="min-w-40">
              <DropdownMenuRadioGroup
                value={draft.agent}
                onValueChange={(agentName) =>
                  setDraft((c) => ({ ...c, agent: agentName }))
                }
              >
                {agents.map((agentName) => (
                  <DropdownMenuRadioItem
                    key={agentName}
                    value={agentName}
                    data-testid={`new-preset-agent-${agentName}`}
                  >
                    {agentName}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]" htmlFor="new-preset-model">
            模型
          </Label>
          <Input
            id="new-preset-model"
            value={draft.model}
            placeholder="留空=不设"
            onChange={(event) =>
              setDraft((c) => ({ ...c, model: event.target.value }))
            }
            data-testid="new-preset-model"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]" htmlFor="new-preset-effort">
            推理深度
          </Label>
          <Input
            id="new-preset-effort"
            value={draft.reasoningEffort}
            placeholder="留空=不设"
            onChange={(event) =>
              setDraft((c) => ({ ...c, reasoningEffort: event.target.value }))
            }
            data-testid="new-preset-effort"
          />
        </div>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={handleSubmit}
        data-testid="new-preset-add"
      >
        加入待保存预设
      </Button>
    </div>
  );
}

/** 回退候选行编辑器的草稿态：完整期望数组 + 预算。 */
function baselineCandidates(
  view: LifecycleSettingsView,
): FallbackCandidateWriteEntry[] {
  return view.fallback.candidates.map((candidate) => ({
    agent: candidate.agent,
    preset: candidate.preset,
  }));
}

interface LifecycleSettingsPageProps {
  /** 初始视角（来自查询参数 scope）。 */
  initialScope: LifecycleAgentScope;
  /** 初始仓库 id（来自查询参数 repo_id）。 */
  initialRepoId?: string;
}

/**
 * 生命周期、模型预设与执行器回退的统一设置页。
 *
 * @param props - 初始视角与仓库 id（用于 Backlog 快捷入口预选）。
 * @returns 统一设置页。
 */
export function LifecycleSettingsPage({
  initialScope,
  initialRepoId,
}: LifecycleSettingsPageProps) {
  const [scope, setScope] = useState<LifecycleAgentScope>(initialScope);
  const [repoId, setRepoId] = useState<string | undefined>(initialRepoId);
  const [repositories, setRepositories] = useState<RegistryRepositoryEntry[]>(
    [],
  );

  const [view, setView] = useState<LifecycleSettingsView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 视角请求是否仍在途：从切换选择起为 true，直到对应视角请求落定（成功或失败）。
  const [isLoading, setIsLoading] = useState(true);

  // 矩阵草稿：本次改动过的预设与阶段绑定（保留式，只提交差异）。
  const [presetDraft, setPresetDraft] = useState<
    Record<string, AgentPresetPayload | null>
  >({});
  const [bindingDraft, setBindingDraft] = useState<Record<string, string | null>>(
    {},
  );
  const [matrixSaving, setMatrixSaving] = useState(false);
  const [matrixError, setMatrixError] = useState<string | null>(null);

  // 回退候选草稿：null 表示未改动（用基线）。
  const [candidateDraft, setCandidateDraft] =
    useState<FallbackCandidateWriteEntry[] | null>(null);
  const [switchesDraft, setSwitchesDraft] = useState<number | null>(null);
  const [fallbackSaving, setFallbackSaving] = useState(false);
  const [fallbackError, setFallbackError] = useState<string | null>(null);

  // 候选新增下拉的临时 agent 选择。
  const [newCandidateAgent, setNewCandidateAgent] = useState<string>("");

  /** 清空全部编辑草稿（切换视角时调用，避免旧视角草稿跨视角泄漏）。 */
  const clearAllDrafts = useCallback(() => {
    setPresetDraft({});
    setBindingDraft({});
    setCandidateDraft(null);
    setSwitchesDraft(null);
  }, []);

  const applyView = useCallback(
    (nextView: LifecycleSettingsView) => {
      setView(nextView);
      clearAllDrafts();
    },
    [clearAllDrafts],
  );

  useEffect(() => {
    fetchRegistryRepositories()
      .then((repos) => setRepositories(repos.filter((repo) => repo.enabled)))
      .catch(() => setRepositories([]));
  }, []);

  useEffect(() => {
    let isCancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoadError(null);
    if (scope === "repository" && !repoId) {
      setView(null);
      return () => {
        isCancelled = true;
      };
    }
    fetchLifecycleSettings({ scope, repoId })
      .then((loaded) => {
        if (!isCancelled) {
          applyView(loaded);
          setIsLoading(false);
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(
            error instanceof Error ? error.message : "加载生命周期设置失败。",
          );
          setIsLoading(false);
        }
      });
    return () => {
      isCancelled = true;
    };
  }, [scope, repoId, applyView]);

  const agents = view?.agents ?? [];

  // 视图身份必须与当前选择一致：切换后、新视角请求落定前，内存里的视图还属于旧选择，
  // 此时按加载态渲染并禁止保存，防止把旧视角的编辑写进新视角的目标文件。
  const viewMatchesSelection =
    view !== null &&
    view.scope === scope &&
    (view.repo_id ?? null) === (repoId ?? null);

  /** 切换全局 / 仓库范围：先同步清草稿、清错误、置加载态，再切选择。 */
  function switchScope(nextScope: LifecycleAgentScope) {
    if (nextScope === scope) {
      return;
    }
    clearAllDrafts();
    setLoadError(null);
    setIsLoading(true);
    // 全局视角的请求不带 repo_id、响应里 repo_id 恒为 null；残留的仓库选择会让
    // 视图身份比对永不成立，页面卡在加载态。仓库选择只在仓库视角有意义。
    if (nextScope === "global") {
      setRepoId(undefined);
    }
    setScope(nextScope);
  }

  /** 切换目标仓库：与切范围同口径（清草稿 + 加载态）。 */
  function switchRepo(nextRepoId: string) {
    if (nextRepoId === repoId) {
      return;
    }
    clearAllDrafts();
    setLoadError(null);
    setIsLoading(true);
    setRepoId(nextRepoId);
  }

  /** 计算某预设当前应显示的三元组（草稿优先于已存）。 */
  function presetValues(name: string): AgentPresetPayload {
    const draft = presetDraft[name];
    if (draft) {
      return draft;
    }
    const saved = view?.presets.find((preset) => preset.name === name);
    return {
      agent: saved?.agent ?? "",
      model: saved?.model ?? null,
      reasoning_effort: saved?.reasoning_effort ?? null,
    };
  }

  /** 本次可绑定的预设名（已存 + 新建未落盘），排除待删除的。 */
  const bindablePresetNames = useMemo(() => {
    const names = new Set<string>();
    for (const preset of view?.presets ?? []) {
      if (presetDraft[preset.name] !== null) {
        names.add(preset.name);
      }
    }
    for (const [name, payload] of Object.entries(presetDraft)) {
      if (payload !== null) {
        names.add(name);
      }
    }
    return Array.from(names);
  }, [view, presetDraft]);

  const presetOptions: PresetOption[] = useMemo(
    () =>
      bindablePresetNames.map((name) => ({
        value: name,
        label: name,
        isNew: !(view?.presets ?? []).some((preset) => preset.name === name),
      })),
    [bindablePresetNames, view],
  );

  /** 绑定当前显示值：草稿优先，否则已存绑定名（空串=未绑定）。 */
  function bindingValueFor(key: string, row: LifecycleSettingsRow): string {
    if (key in bindingDraft) {
      return bindingDraft[key] ?? "";
    }
    return row.preset_name ?? "";
  }

  const changedBindings = Object.entries(bindingDraft).filter(
    ([stage, value]) => {
      const row = view?.lifecycles.find((item) => item.key === stage);
      return (row?.preset_name ?? null) !== value;
    },
  );
  const changedPresets = Object.entries(presetDraft);
  const matrixDirty = changedBindings.length > 0 || changedPresets.length > 0;

  /** 矩阵预览：将写入的预设与绑定（供保存前确认）。 */
  const matrixPreviewLines = useMemo(() => {
    const lines: string[] = [];
    for (const [name, payload] of changedPresets) {
      if (payload === null) {
        lines.push(`删除预设 ${name}`);
      } else {
        const modelPart = payload.model ? ` / 模型 ${payload.model}` : "";
        const effortPart = payload.reasoning_effort
          ? ` / 推理 ${payload.reasoning_effort}`
          : "";
        lines.push(
          `写入预设 ${name} = ${payload.agent}${modelPart}${effortPart}`,
        );
      }
    }
    for (const [stage, value] of changedBindings) {
      lines.push(
        `阶段 ${stage} → ${value ? `绑定 ${value}` : "解绑（清除本层绑定）"}`,
      );
    }
    return lines;
  }, [changedPresets, changedBindings]);

  /** 提交改动过的预设与绑定（一次 PATCH）。 */
  async function handleSaveMatrix() {
    if (!view || !matrixDirty || !viewMatchesSelection) {
      return;
    }
    setMatrixSaving(true);
    setMatrixError(null);
    try {
      const bindings: Record<string, string | null> = {};
      for (const [stage, value] of changedBindings) {
        bindings[stage] = value;
      }
      const presets: Record<string, AgentPresetPayload | null> = {};
      for (const [name, payload] of changedPresets) {
        presets[name] = payload;
      }
      const nextView = await updateLifecycleSettings({
        scope,
        repoId,
        presets,
        bindings,
      });
      setView(nextView);
      // 只重置本区块（矩阵 / 预设）草稿；回退候选与预算草稿保留——两块写回互不牵连，
      // 避免一次矩阵保存静默丢弃未保存的回退编辑。
      setPresetDraft({});
      setBindingDraft({});
      toast.success("生命周期设置已保存。");
    } catch (error) {
      setMatrixError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setMatrixSaving(false);
    }
  }

  /** 编辑已有预设的字段（写入草稿）。 */
  function editPreset(name: string, next: AgentPresetPayload) {
    setPresetDraft((current) => ({ ...current, [name]: next }));
  }

  /** 删除预设并连带解绑其绑定阶段。 */
  function deletePreset(name: string, boundStages: string[]) {
    setPresetDraft((current) => ({ ...current, [name]: null }));
    setBindingDraft((current) => {
      const next = { ...current };
      for (const stage of boundStages) {
        // 只解绑仍指向被删预设的阶段：未触碰（undefined）的阶段沿用基线绑定（即被删
        // 预设），需显式解绑；本会话已改绑其它预设的草稿保持不动。
        if (next[stage] === undefined || next[stage] === name) {
          next[stage] = null;
        }
      }
      return next;
    });
  }

  /** 加入一个新建预设到草稿。 */
  function addPreset(draft: NewPresetDraft) {
    const name = draft.name.trim();
    setPresetDraft((current) => ({
      ...current,
      [name]: {
        agent: draft.agent,
        model: draft.model.trim() || null,
        reasoning_effort: draft.reasoningEffort.trim() || null,
      },
    }));
    toast.success(`预设「${name}」已加入待保存。`);
  }

  const baseline = view ? baselineCandidates(view) : [];
  const workingCandidates = candidateDraft ?? baseline;
  const workingSwitches = switchesDraft ?? view?.fallback.max_agent_switches ?? 2;
  const fallbackDirty =
    candidateDraft !== null || switchesDraft !== null;

  /** 修改某候选绑定的预设（候选身份 = agent + preset）。 */
  function updateCandidate(index: number, patchValue: Partial<FallbackCandidateWriteEntry>) {
    setCandidateDraft((current) => {
      const list = [...(current ?? baseline)];
      list[index] = { ...list[index], ...patchValue };
      return list;
    });
  }

  /** 追加一个回退候选（可选带预设）。 */
  function addCandidate(agentName: string, presetName: string | null) {
    if (!agentName) {
      return;
    }
    setCandidateDraft((current) => [
      ...(current ?? baseline),
      { agent: agentName, preset: presetName },
    ]);
    setNewCandidateAgent("");
  }

  /** 删除某位置的候选。 */
  function removeCandidate(index: number) {
    setCandidateDraft((current) => {
      const list = [...(current ?? baseline)];
      list.splice(index, 1);
      return list;
    });
  }

  /** 上下移动候选。 */
  function moveCandidate(index: number, offset: number) {
    setCandidateDraft((current) => {
      const list = [...(current ?? baseline)];
      const nextIndex = index + offset;
      if (nextIndex < 0 || nextIndex >= list.length) {
        return current ?? list;
      }
      [list[index], list[nextIndex]] = [list[nextIndex], list[index]];
      return list;
    });
  }

  /** 写回完整期望候选数组与预算（候选为机器级配置）。 */
  async function handleSaveFallback() {
    if (!view || !fallbackDirty || !viewMatchesSelection) {
      return;
    }
    setFallbackSaving(true);
    setFallbackError(null);
    try {
      const nextFallback = await updateAgentFallbackCandidates({
        candidates: workingCandidates,
        maxAgentSwitches: workingSwitches,
        repoId,
      });
      setView((current) =>
        current ? { ...current, fallback: nextFallback } : current,
      );
      setCandidateDraft(null);
      setSwitchesDraft(null);
      toast.success("执行器回退候选已保存。");
    } catch (error) {
      setFallbackError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setFallbackSaving(false);
    }
  }

  /** 本次新建、尚未保存落盘的预设名（保存矩阵后才可被回退候选绑定）。 */
  const pendingNewPresetNames = useMemo(() => {
    const savedNames = new Set((view?.presets ?? []).map((preset) => preset.name));
    return Object.entries(presetDraft)
      .filter(([name, payload]) => payload !== null && !savedNames.has(name))
      .map(([name]) => name);
  }, [view, presetDraft]);

  /**
   * 某候选可绑定的预设（预设声明的 agent 必须与候选一致）。
   *
   * 只列已落盘的预设：候选写接口不接受预设 upsert，绑定"本次新建"预设会在写前被
   * 拒绝；页面上方对未落盘预设给出提示（见回退区块的 pendingNewPresetNames 提示）。
   */
  function candidatePresetOptions(agentName: string): PresetOption[] {
    return (view?.presets ?? [])
      .filter((preset) => preset.agent === agentName && presetDraft[preset.name] !== null)
      .map((preset) => ({ value: preset.name, label: preset.name }));
  }

  /** 渲染全局 / 仓库范围选择条（仓库范围附下拉选仓库）。 */
  function renderScopeBar() {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-lg border p-0.5">
          <button
            type="button"
            onClick={() => switchScope("global")}
            data-testid="scope-global"
            className={cn(
              "rounded-md px-3 py-1 text-sm transition-colors",
              scope === "global"
                ? "bg-slate-900 text-slate-50 dark:bg-slate-50 dark:text-slate-900"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-400",
            )}
          >
            全局
          </button>
          <button
            type="button"
            onClick={() => switchScope("repository")}
            data-testid="scope-repository"
            className={cn(
              "rounded-md px-3 py-1 text-sm transition-colors",
              scope === "repository"
                ? "bg-slate-900 text-slate-50 dark:bg-slate-50 dark:text-slate-900"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-400",
            )}
          >
            仓库
          </button>
        </div>
        {scope === "repository" ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="outline" size="sm" data-testid="scope-repo-select">
                {repositories.find((repo) => repo.repo_id === repoId)?.display_name ??
                  repoId ??
                  "选择仓库"}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="min-w-56">
              {repositories.map((repo) => (
                <DropdownMenuItem
                  key={repo.repo_id}
                  onSelect={() => switchRepo(repo.repo_id)}
                  data-testid={`scope-repo-option-${repo.repo_id}`}
                >
                  {repo.display_name ?? repo.repo_id}
                </DropdownMenuItem>
              ))}
              {repositories.length === 0 ? (
                <DropdownMenuLabel className="text-xs text-slate-500">
                  暂无启用的仓库
                </DropdownMenuLabel>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
    );
  }

  /** 页首区块锚点导航（矩阵 / 预设 / 回退）。 */
  function renderBlockNav() {
    return (
      <nav className="flex flex-wrap gap-2 text-xs" data-testid="block-nav">
        <a className="text-slate-600 underline-offset-4 hover:underline dark:text-slate-400" href="#lifecycle-matrix">
          生命周期矩阵
        </a>
        <span className="text-slate-300">·</span>
        <a className="text-slate-600 underline-offset-4 hover:underline dark:text-slate-400" href="#model-presets">
          模型预设
        </a>
        <span className="text-slate-300">·</span>
        <a className="text-slate-600 underline-offset-4 hover:underline dark:text-slate-400" href="#executor-fallback">
          执行器回退
        </a>
      </nav>
    );
  }

  /** 渲染生命周期矩阵区块（九阶段生效值 + 绑定预设 + 保存）。 */
  function renderMatrixSection(currentView: LifecycleSettingsView) {
    return (
      <Card id="lifecycle-matrix">
        <CardHeader>
          <CardTitle>生命周期矩阵</CardTitle>
          <CardDescription>
            九阶段各自的最终生效 Agent / 模型 / 推理深度与来源。显式值通过绑定命名预设设定；
            fix / closeout 未绑定时继承实现阶段。本表为全局 / 仓库基线，PRD 头部声明与
            单次运行的 CLI 旗标对单个 PRD / 运行优先生效。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {currentView.lifecycles.map((row) => (
            <MatrixRow
              key={row.key}
              row={row}
              presetOptions={presetOptions}
              bindingValue={bindingValueFor(row.key, row)}
              bindingChanged={
                row.key in bindingDraft &&
                bindingDraft[row.key] !== (row.preset_name ?? null)
              }
              vocabulary={currentView.field_source_vocabulary}
              onBindingChange={(presetName) =>
                setBindingDraft((current) => ({
                  ...current,
                  [row.key]: presetName || null,
                }))
              }
            />
          ))}

          {matrixPreviewLines.length > 0 ? (
            <div
              className="rounded-md border bg-slate-50 p-2 text-xs dark:bg-slate-900"
              data-testid="matrix-preview"
            >
              <p className="font-medium">将写入：</p>
              <ul className="mt-1 space-y-0.5">
                {matrixPreviewLines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {matrixError ? (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {matrixError}
            </p>
          ) : null}

          <Button
            size="sm"
            onClick={() => void handleSaveMatrix()}
            disabled={matrixSaving || !matrixDirty || !viewMatchesSelection}
            data-testid="matrix-save"
          >
            {matrixSaving ? "保存中…" : "保存矩阵与预设"}
          </Button>
        </CardContent>
      </Card>
    );
  }

  /** 渲染模型预设区块（编辑已有预设 + 待新建列表 + 新建表单）。 */
  function renderPresetSection(currentView: LifecycleSettingsView) {
    return (
      <Card id="model-presets">
        <CardHeader>
          <CardTitle>模型预设</CardTitle>
          <CardDescription>
            命名预设是 (Agent, 模型, 推理深度) 的原子三元组，可绑定到任意阶段。编辑共享预设前
            会看到它牵动的阶段；删除预设会连带解绑。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {currentView.presets.length === 0 ? (
            <p className="text-sm text-slate-500" data-testid="preset-empty">
              当前视角还没有定义任何预设。
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {currentView.presets
                .filter((preset) => presetDraft[preset.name] !== null)
                .map((preset) => (
                  <PresetCard
                    key={preset.name}
                    name={preset.name}
                    agents={agents}
                    values={presetValues(preset.name)}
                    boundStages={preset.bound_stages}
                    sourceLabel={PRESET_SOURCE_LABELS[preset.source] ?? preset.source}
                    onFieldChange={(next) => editPreset(preset.name, next)}
                    onDelete={() => deletePreset(preset.name, preset.bound_stages)}
                  />
                ))}
            </div>
          )}

          {bindablePresetNames
            .filter((name) => !currentView.presets.some((p) => p.name === name))
            .map((name) => (
              <div
                key={name}
                className="flex items-center justify-between rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:bg-amber-950/30"
                data-testid={`preset-pending-${name}`}
              >
                <span>
                  待新建：{name} ={" "}
                  {presetDraft[name]?.agent}
                  {presetDraft[name]?.model ? ` / ${presetDraft[name]?.model}` : ""}
                  {presetDraft[name]?.reasoning_effort
                    ? ` / ${presetDraft[name]?.reasoning_effort}`
                    : ""}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="text-red-600 dark:text-red-400"
                  onClick={() =>
                    setPresetDraft((current) => {
                      const next = { ...current };
                      delete next[name];
                      return next;
                    })
                  }
                  data-testid={`preset-pending-cancel-${name}`}
                >
                  撤销
                </Button>
              </div>
            ))}

          <NewPresetForm
            agents={agents}
            existingNames={Array.from(
              new Set([
                ...currentView.presets.map((preset) => preset.name),
                ...bindablePresetNames,
              ]),
            )}
            onAdd={addPreset}
          />
        </CardContent>
      </Card>
    );
  }

  /** 渲染执行器回退候选区块（有序候选 + 预设绑定 + 预算 + 保存）。 */
  function renderFallbackSection(currentView: LifecycleSettingsView) {
    return (
      <Card id="executor-fallback">
        <CardHeader>
          <CardTitle>执行器回退候选</CardTitle>
          <CardDescription>
            Issue 执行阶段按顺序尝试的候选队列。每个候选可选绑定「同一执行器」的命名预设；
            未绑定时沿用执行器默认。候选是机器级配置，写入全局 config.toml。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {pendingNewPresetNames.length > 0 ? (
            <p
              className="text-xs text-slate-500 dark:text-slate-400"
              data-testid="fallback-pending-presets-note"
            >
              本次新建、尚未保存的预设（{pendingNewPresetNames.join("、")}
              ）需先在「模型预设」区保存后，才能绑定为回退候选。
            </p>
          ) : null}
          {workingCandidates.length === 0 ? (
            <p className="text-sm text-slate-500" data-testid="fallback-empty">
              当前回退候选为空。
            </p>
          ) : (
            <ol className="space-y-2">
              {workingCandidates.map((candidate, index) => {
                // 摘要按行自身 (agent, preset) 身份匹配基线候选：按位置索引在增删 /
                // 移动 / 换绑后会错配；身份对不上基线的行不显示摘要。
                const rowView = currentView.fallback.candidates.find(
                  (baselineRow) =>
                    baselineRow.agent === candidate.agent &&
                    baselineRow.preset === candidate.preset,
                );
                const presetLabel = candidate.preset ?? "跟随执行器默认";
                return (
                  <li
                    key={`${candidate.agent}|${candidate.preset ?? ""}`}
                    className="grid grid-cols-1 gap-2 rounded-md border p-2 sm:grid-cols-[auto_minmax(7rem,1fr)_minmax(9rem,1fr)_auto] sm:items-center"
                    data-testid={`fallback-row-${index + 1}`}
                  >
                    <span className="w-5 text-xs text-slate-500">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <Label className="text-[11px]">执行器</Label>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="w-full justify-between"
                            data-testid={`fallback-agent-${index + 1}`}
                          >
                            <span className="truncate">{candidate.agent}</span>
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="min-w-40">
                          <DropdownMenuRadioGroup
                            value={candidate.agent}
                            onValueChange={(agentName) =>
                              updateCandidate(index, {
                                agent: agentName,
                                // 换执行器时清掉旧预设（预设与执行器必须匹配）。
                                preset: null,
                              })
                            }
                          >
                            {agents.map((agentName) => (
                              <DropdownMenuRadioItem
                                key={agentName}
                                value={agentName}
                                data-testid={`fallback-agent-${index + 1}-${agentName}`}
                              >
                                {agentName}
                              </DropdownMenuRadioItem>
                            ))}
                          </DropdownMenuRadioGroup>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                    <div className="min-w-0">
                      <Label className="text-[11px]">
                        预设（{presetLabel}）
                      </Label>
                      <PresetSelect
                        value={candidate.preset ?? ""}
                        options={candidatePresetOptions(candidate.agent)}
                        allowClear
                        placeholder="跟随执行器默认"
                        onChange={(presetName) =>
                          updateCandidate(index, {
                            preset: presetName || null,
                          })
                        }
                        testId={`fallback-preset-${index + 1}`}
                      />
                      {rowView ? (
                        <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                          模型：
                          {displayOptionalField(
                            rowView.model,
                            rowView.field_sources.model,
                            currentView.field_source_vocabulary,
                          )}
                          {" · "}
                          推理：
                          {displayOptionalField(
                            rowView.reasoning_effort,
                            rowView.field_sources.reasoning_effort,
                            currentView.field_source_vocabulary,
                          )}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="icon-sm"
                        disabled={index === 0}
                        onClick={() => moveCandidate(index, -1)}
                        data-testid={`fallback-up-${index + 1}`}
                        aria-label="上移候选"
                      >
                        ↑
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="icon-sm"
                        disabled={index === workingCandidates.length - 1}
                        onClick={() => moveCandidate(index, 1)}
                        data-testid={`fallback-down-${index + 1}`}
                        aria-label="下移候选"
                      >
                        ↓
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => removeCandidate(index)}
                        data-testid={`fallback-remove-${index + 1}`}
                        aria-label="移除候选"
                      >
                        ×
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}

          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label className="text-[11px]">新增候选执行器</Label>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    data-testid="fallback-add"
                  >
                    {newCandidateAgent || "选择执行器追加"}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="min-w-40">
                  {agents.map((agentName) => (
                    <DropdownMenuItem
                      key={agentName}
                      onSelect={() => addCandidate(agentName, null)}
                      data-testid={`fallback-add-${agentName}`}
                    >
                      {agentName}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <div className="space-y-1">
              <Label className="text-[11px]" htmlFor="fallback-switches">
                最大切换次数
              </Label>
              <Input
                id="fallback-switches"
                type="number"
                min={0}
                value={workingSwitches}
                onChange={(event) => {
                  const parsed = Number.parseInt(event.target.value, 10);
                  setSwitchesDraft(Number.isNaN(parsed) ? 0 : Math.max(0, parsed));
                }}
                className="w-28"
                data-testid="fallback-switches"
              />
            </div>
            <p className="pb-2 text-xs text-slate-500 dark:text-slate-400">
              最多尝试 {workingSwitches + 1} 个候选（max_agent_switches=
              {workingSwitches}，按候选步数计）
            </p>
          </div>

          {fallbackDirty ? (
            <div
              className="rounded-md border bg-slate-50 p-2 text-xs dark:bg-slate-900"
              data-testid="fallback-preview"
            >
              <p className="font-medium">将写入候选队列：</p>
              <ol className="mt-1 space-y-0.5">
                {workingCandidates.map((candidate, index) => (
                  <li key={`${candidate.agent}|${candidate.preset ?? ""}`}>
                    {index + 1}. {candidate.agent}
                    {candidate.preset ? `（预设 ${candidate.preset}）` : ""}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          {fallbackError ? (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {fallbackError}
            </p>
          ) : null}

          <Button
            size="sm"
            onClick={() => void handleSaveFallback()}
            disabled={fallbackSaving || !fallbackDirty || !viewMatchesSelection}
            data-testid="fallback-save"
          >
            {fallbackSaving ? "保存中…" : "保存回退候选"}
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (loadError) {
    return (
      <div className="space-y-4">
        {renderScopeBar()}
        <ResourceErrorAlert message={loadError} testId="lifecycle-settings-error" />
      </div>
    );
  }

  if (scope === "repository" && !repoId) {
    return (
      <div className="space-y-4" data-testid="lifecycle-settings-page">
        {renderScopeBar()}
        <p className="text-sm text-slate-500" data-testid="lifecycle-settings-pick-repo">
          请选择一个仓库以编辑其生命周期设置。
        </p>
      </div>
    );
  }

  if (isLoading || !viewMatchesSelection) {
    return (
      <div className="space-y-4" data-testid="lifecycle-settings-page">
        {renderScopeBar()}
        <p className="text-sm text-slate-500" data-testid="lifecycle-settings-loading">
          加载中…
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="lifecycle-settings-page">
      {renderScopeBar()}
      {renderBlockNav()}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {view.scope === "global"
          ? "正在编辑：全局 config.toml。"
          : `正在编辑：仓库 ${view.repo_id ?? ""}。生命周期矩阵与预设绑定写入该仓库的 .kedacode.toml；仅下方「执行器回退」固定写入机器级 config.toml。`}
      </p>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        本页展示全局 / 仓库基线；PRD 头部声明与单次运行的 CLI 旗标（--preset / --model
        / --reasoning-effort）对单个 PRD / 运行优先生效。
      </p>
      {renderMatrixSection(view)}
      {renderPresetSection(view)}
      {renderFallbackSection(view)}
    </div>
  );
}
