"use client"

import { useEffect, useState } from "react"
import { ArrowUpCircle, X } from "lucide-react"

import { CopyableCommand } from "@/components/agent-runner/copyable-command"
import { Button } from "@/components/ui/button"
import { type ConsoleSourceVersion, fetchConsoleVersion, fetchLatestPyPiVersion, isNewerVersion, upgradeConsole } from "@/lib/api/version"
import {
  readSkippedUpdateVersion,
  writeSkippedUpdateVersion,
} from "@/lib/console-ui-prefs"

/** 发布页地址，用于「查看变更」。 */
const RELEASES_URL = "https://github.com/ZataZhang/keda/releases"

/** PyPI 查询的超时上限：超过就当作「查不到」，静默降级。 */
const PYPI_TIMEOUT_MS = 5000

const BANNER_CLASS_NAME = "mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100"

/** Top-of-page banner announcing an available KedaCode update. */
export function UpdateBanner() {
  const [source, setSource] = useState<ConsoleSourceVersion | null>(null)
  const [currentVersion, setCurrentVersion] = useState<string | null>(null)
  const [latestVersion, setLatestVersion] = useState<string | null>(null)
  // 本次会话内点过「×」后不再出现；「跳过此版本」则按版本号长期记忆。
  const [upgradeCommand, setUpgradeCommand] = useState<string | null>(null)
  const [upgrading, setUpgrading] = useState(false)
  const [installedVersion, setInstalledVersion] = useState<string | null>(null)
  const [upgradeError, setUpgradeError] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    let timeout: ReturnType<typeof setTimeout> | undefined

    void (async () => {
      try {
        const installation = await fetchConsoleVersion()
        if (cancelled) return
        if (installation.source) {
          setSource(installation.source)
          return
        }
        timeout = setTimeout(() => controller.abort(), PYPI_TIMEOUT_MS)
        const latest = await fetchLatestPyPiVersion(controller.signal)
        const current = installation.version
        // 拿不到任一侧、当前版本是占位值（源码树 / 未打包）、该版本已被跳过、
        // 或最新版并不更新时，一律不显示。
        if (cancelled || !latest || !current || current.includes("unknown")) {
          return
        }
        if (readSkippedUpdateVersion() === latest) {
          return
        }
        if (!isNewerVersion(current, latest)) {
          return
        }
        setUpgradeCommand(installation.upgrade_command ?? null)
        setCurrentVersion(current)
        setLatestVersion(latest)
      } catch {
        // 离线 / 超时 / 被墙 / 后端不可达：静默，不打扰用户。
      } finally {
        clearTimeout(timeout)
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
      clearTimeout(timeout)
    }
  }, [])

  async function handleUpgrade() {
    setUpgrading(true)
    setUpgradeError(null)
    try {
      const installation = await upgradeConsole()
      setInstalledVersion(installation.version)
    } catch (error) {
      setUpgradeError(error instanceof Error ? error.message : "升级失败，请手动升级。")
    } finally {
      setUpgrading(false)
    }
  }

  const checkoutCommit = source?.checkout_commit
  const frontendStale = process.env.NODE_ENV === "production"
    && process.env.KEDA_CONSOLE_BUILD_COMMIT !== checkoutCommit
  const backendStale = Boolean(source?.startup_commit && source.startup_commit !== checkoutCommit)

  if (checkoutCommit && (frontendStale || backendStale) && !dismissed) {
    return (
      <div role="status" className={BANNER_CLASS_NAME}>
        <ArrowUpCircle className="size-4 shrink-0" aria-hidden="true" />
        <span>
          当前源码提交为 <code>{checkoutCommit.slice(0, 8)}</code>。
          {frontendStale ? "请重新构建前端并重启 Console。" : "请重启 Console 以加载当前源码。"}
        </span>
        {frontendStale && <CopyableCommand command="just refresh-kc restart" className="max-w-xs" />}
        <div className="flex-1" />
        <Button type="button" variant="ghost" size="icon-sm" aria-label="关闭源码更新提示" onClick={() => setDismissed(true)}>
          <X className="size-4" />
        </Button>
      </div>
    )
  }

  if (!currentVersion || !latestVersion || dismissed) {
    return null
  }

  return (
    <div className={BANNER_CLASS_NAME}>
      <ArrowUpCircle className="size-4 shrink-0" aria-hidden="true" />
      <span className="font-medium">
        {installedVersion
          ? `已安装 v${installedVersion}，请重启 KedaCode 以使用新版本。`
          : `有新版本 v${latestVersion}（当前 v${currentVersion}）`}
      </span>
      {!installedVersion && (
        <>
          {upgradeCommand && (
            <Button type="button" size="sm" disabled={upgrading} onClick={handleUpgrade}>
              {upgrading ? "正在升级…" : "立即升级"}
            </Button>
          )}
          {upgradeCommand
            ? <CopyableCommand command={upgradeCommand} className="max-w-xs" />
            : <span>请按安装方式手动升级</span>}
        </>
      )}
      <a
        href={RELEASES_URL}
        target="_blank"
        rel="noreferrer"
        className="underline underline-offset-2"
      >
        查看变更
      </a>
      <div className="flex-1" />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        hidden={Boolean(installedVersion)}
        disabled={upgrading}
        onClick={() => {
          writeSkippedUpdateVersion(latestVersion)
          setLatestVersion(null)
        }}
      >
        跳过此版本
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="关闭升级提示"
        title="关闭"
        disabled={upgrading}
        onClick={() => setDismissed(true)}
      >
        <X className="size-4" />
      </Button>
      {upgradeError && <p role="alert" className="basis-full">{upgradeError}</p>}
    </div>
  )
}
