"use client"

// 全站顶部的「有新版本」提示条。
//
// 打开 / 刷新管理终端时后台静默比对一次（会话内只查一次），发现后端版本落后于
// PyPI 最新版且该版本未被用户跳过时出现。刻意不提供「一键升级」：console 只展示
// 命令、不替用户执行 CLI 动作（见 copyable-command.tsx 的设计约定）。

import { useEffect, useState } from "react"
import { ArrowUpCircle, X } from "lucide-react"

import { CopyableCommand } from "@/components/agent-runner/copyable-command"
import { Button } from "@/components/ui/button"
import { fetchConsoleVersion, fetchLatestPyPiVersion, isNewerVersion } from "@/lib/api/version"
import {
  readSkippedUpdateVersion,
  writeSkippedUpdateVersion,
} from "@/lib/console-ui-prefs"

/** 发布页地址，用于「查看变更」。 */
const RELEASES_URL = "https://github.com/ZataZhang/keda/releases"

/** 推荐的升级命令（`uv tool install` 安装路径下最省事；brew 用户可自行替换）。 */
const UPGRADE_COMMAND = "uv tool upgrade kedacode"

/** PyPI 查询的超时上限：超过就当作「查不到」，静默降级。 */
const PYPI_TIMEOUT_MS = 5000

/** Top-of-page banner announcing an available KedaCode update. */
export function UpdateBanner() {
  const [currentVersion, setCurrentVersion] = useState<string | null>(null)
  const [latestVersion, setLatestVersion] = useState<string | null>(null)
  // 本次会话内点过「×」后不再出现；「跳过此版本」则按版本号长期记忆。
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), PYPI_TIMEOUT_MS)

    void (async () => {
      try {
        const [current, latest] = await Promise.all([
          fetchConsoleVersion(),
          fetchLatestPyPiVersion(controller.signal),
        ])
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

  if (!currentVersion || !latestVersion || dismissed) {
    return null
  }

  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100">
      <ArrowUpCircle className="size-4 shrink-0" aria-hidden="true" />
      <span className="font-medium">
        有新版本 v{latestVersion}（当前 v{currentVersion}）
      </span>
      <CopyableCommand command={UPGRADE_COMMAND} className="max-w-xs" />
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
        onClick={() => setDismissed(true)}
      >
        <X className="size-4" />
      </Button>
    </div>
  )
}
