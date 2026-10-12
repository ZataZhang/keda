"use client"

import { Plus, Save, Trash2 } from "lucide-react"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  fetchModelPricingSettings,
  fetchPrdLifecycleStats,
  saveModelPricingSettings,
} from "@/lib/api/console"
import type { ModelTokenPricing } from "@/lib/api/types"

type ModelPriceField = Exclude<keyof ModelTokenPricing, "model_id">
const CUSTOM_MODEL_OPTION = "__custom_model_id__"

const PRICE_FIELDS: { field: ModelPriceField; label: string }[] = [
  { field: "input_usd_per_million", label: "输入" },
  { field: "output_usd_per_million", label: "输出" },
  { field: "cache_read_usd_per_million", label: "缓存读" },
  { field: "cache_creation_usd_per_million", label: "缓存写" },
]

function validateModelPrices(models: ModelTokenPricing[]): string | null {
  const seenModelIds = new Set<string>()
  for (const modelPrice of models) {
    const normalizedModelId = modelPrice.model_id.trim()
    if (!normalizedModelId) {
      return "请填写每一行的模型 ID。"
    }
    if (seenModelIds.has(normalizedModelId)) {
      return `模型「${normalizedModelId}」重复配置。`
    }
    seenModelIds.add(normalizedModelId)
    for (const { field } of PRICE_FIELDS) {
      const rate = modelPrice[field]
      if (!Number.isFinite(rate) || rate < 0) {
        return `模型「${normalizedModelId}」的价格必须是非负数字。`
      }
    }
  }
  return null
}

/**
 * 编辑和保存本机 Console 的模型 token 价格。
 *
 * @returns 支持增删模型并编辑四类单价的设置卡片。
 */
export function ModelPricingEditor() {
  const [models, setModels] = useState<ModelTokenPricing[]>([])
  const [observedModelIds, setObservedModelIds] = useState<string[]>([])
  const [customModelRows, setCustomModelRows] = useState<Set<number>>(
    () => new Set()
  )
  const [updatedAt, setUpdatedAt] = useState("")
  const [loadError, setLoadError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isLoadingModelIds, setIsLoadingModelIds] = useState(true)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    let isCancelled = false
    fetchModelPricingSettings()
      .then((settings) => {
        if (!isCancelled) {
          setModels(settings.models)
          setUpdatedAt(settings.updated_at)
        }
      })
      .catch((error: unknown) => {
        if (!isCancelled) {
          setLoadError(
            error instanceof Error ? error.message : "加载模型价格失败。"
          )
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoading(false)
        }
      })
    fetchPrdLifecycleStats({ days: 365 })
      .then((stats) => {
        if (!isCancelled) {
          setObservedModelIds(
            Object.keys(stats.token_usage_by_model?.by_model ?? {})
              .filter((modelId) => modelId !== "unknown")
              .sort((leftModelId, rightModelId) =>
                leftModelId.localeCompare(rightModelId)
              )
          )
        }
      })
      .catch(() => {
        if (!isCancelled) {
          setObservedModelIds([])
        }
      })
      .finally(() => {
        if (!isCancelled) {
          setIsLoadingModelIds(false)
        }
      })
    return () => {
      isCancelled = true
    }
  }, [])

  const selectableModelIds = Array.from(
    new Set([
      ...observedModelIds,
      ...models.map((modelPrice) => modelPrice.model_id.trim()).filter(Boolean),
    ])
  ).sort((leftModelId, rightModelId) => leftModelId.localeCompare(rightModelId))

  function updateModelId(modelIndex: number, modelId: string) {
    setModels((currentModels) =>
      currentModels.map((modelPrice, index) =>
        index === modelIndex ? { ...modelPrice, model_id: modelId } : modelPrice
      )
    )
    setValidationError(null)
  }

  function updatePrice(
    modelIndex: number,
    field: ModelPriceField,
    rawRate: string
  ) {
    const parsedRate = rawRate === "" ? 0 : Number(rawRate)
    setModels((currentModels) =>
      currentModels.map((modelPrice, index) =>
        index === modelIndex
          ? { ...modelPrice, [field]: parsedRate }
          : modelPrice
      )
    )
    setValidationError(null)
  }

  async function handleSave() {
    const validationMessage = validateModelPrices(models)
    if (validationMessage) {
      setValidationError(validationMessage)
      return
    }
    setIsSaving(true)
    try {
      const settings = await saveModelPricingSettings(
        models.map((modelPrice) => ({
          ...modelPrice,
          model_id: modelPrice.model_id.trim(),
        }))
      )
      setModels(settings.models)
      setUpdatedAt(settings.updated_at)
      setValidationError(null)
      toast.success("模型价格已保存。")
    } catch (error) {
      setValidationError(
        error instanceof Error ? error.message : "保存模型价格失败。"
      )
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Card data-testid="settings-model-pricing">
      <CardHeader>
        <CardTitle>模型 Token 价格</CardTitle>
        <CardDescription>
          选择近一年实际调用过的模型，或手动输入新模型 ID。单价单位为 USD / 百万
          token；输入价格只针对未缓存输入，缓存读写单独计价。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loadError ? (
          <p
            className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
            role="alert"
          >
            {loadError}
          </p>
        ) : null}
        {validationError ? (
          <p
            className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
            role="alert"
          >
            {validationError}
          </p>
        ) : null}

        {isLoading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            加载价格配置…
          </p>
        ) : (
          <>
            <div className="overflow-x-auto rounded-xl border">
              <div className="min-w-[820px]">
                <div className="grid grid-cols-[minmax(220px,1.6fr)_repeat(4,minmax(120px,1fr))_44px] gap-3 border-b bg-muted/40 px-4 py-3 text-xs font-medium text-muted-foreground">
                  <span>
                    模型 ID
                    {isLoadingModelIds ? (
                      <span className="ml-1 font-normal">（加载中）</span>
                    ) : null}
                  </span>
                  {PRICE_FIELDS.map(({ field, label }) => (
                    <span key={field}>{label} · USD / 1M</span>
                  ))}
                  <span className="sr-only">操作</span>
                </div>
                {models.length === 0 ? (
                  <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                    还没有价格项。点击“添加模型”开始配置。
                  </p>
                ) : (
                  models.map((modelPrice, modelIndex) => (
                    <div
                      key={`${modelPrice.model_id || "new-model"}-${modelIndex}`}
                      className="grid grid-cols-[minmax(220px,1.6fr)_repeat(4,minmax(120px,1fr))_44px] items-center gap-3 border-b px-4 py-3 last:border-b-0"
                    >
                      <div className="space-y-2">
                        <select
                          aria-label={`选择模型 ${modelIndex + 1}`}
                          className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                          value={
                            customModelRows.has(modelIndex)
                              ? CUSTOM_MODEL_OPTION
                              : modelPrice.model_id
                          }
                          onChange={(event) => {
                            const selectedModelId = event.target.value
                            if (selectedModelId === CUSTOM_MODEL_OPTION) {
                              setCustomModelRows((currentRows) =>
                                new Set(currentRows).add(modelIndex)
                              )
                              return
                            }
                            setCustomModelRows((currentRows) => {
                              const nextRows = new Set(currentRows)
                              nextRows.delete(modelIndex)
                              return nextRows
                            })
                            updateModelId(modelIndex, selectedModelId)
                          }}
                        >
                          <option value="">
                            {isLoadingModelIds ? "加载模型…" : "选择模型…"}
                          </option>
                          {selectableModelIds.map((modelId) => (
                            <option key={modelId} value={modelId}>
                              {modelId}
                            </option>
                          ))}
                          <option value={CUSTOM_MODEL_OPTION}>
                            自定义模型 ID…
                          </option>
                        </select>
                        {customModelRows.has(modelIndex) ? (
                          <Input
                            aria-label={`自定义模型 ${modelIndex + 1} ID`}
                            autoComplete="off"
                            placeholder="输入模型 ID"
                            value={modelPrice.model_id}
                            onChange={(event) =>
                              updateModelId(modelIndex, event.target.value)
                            }
                          />
                        ) : null}
                      </div>
                      {PRICE_FIELDS.map(({ field, label }) => (
                        <Input
                          key={field}
                          aria-label={`${modelPrice.model_id || `模型 ${modelIndex + 1}`} ${label}价格`}
                          type="number"
                          min="0"
                          step="0.000001"
                          value={modelPrice[field]}
                          onChange={(event) =>
                            updatePrice(modelIndex, field, event.target.value)
                          }
                        />
                      ))}
                      <Button
                        aria-label={`删除模型价格 ${modelPrice.model_id || modelIndex + 1}`}
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => {
                          setModels((currentModels) =>
                            currentModels.filter(
                              (_, index) => index !== modelIndex
                            )
                          )
                          setCustomModelRows(
                            (currentRows) =>
                              new Set(
                                [...currentRows]
                                  .filter((index) => index !== modelIndex)
                                  .map((index) =>
                                    index > modelIndex ? index - 1 : index
                                  )
                              )
                          )
                        }}
                      >
                        <Trash2 aria-hidden="true" className="size-4" />
                      </Button>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setModels((currentModels) => [
                      ...currentModels,
                      {
                        model_id: "",
                        input_usd_per_million: 0,
                        output_usd_per_million: 0,
                        cache_read_usd_per_million: 0,
                        cache_creation_usd_per_million: 0,
                      },
                    ])
                  }
                >
                  <Plus aria-hidden="true" className="size-4" />
                  添加模型
                </Button>
                {updatedAt ? (
                  <span className="text-xs text-muted-foreground">
                    上次保存 {new Date(updatedAt).toLocaleString()}
                  </span>
                ) : null}
              </div>
              <Button
                size="sm"
                onClick={handleSave}
                disabled={isSaving || Boolean(loadError)}
              >
                <Save aria-hidden="true" className="size-4" />
                {isSaving ? "保存中…" : "保存价格"}
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
