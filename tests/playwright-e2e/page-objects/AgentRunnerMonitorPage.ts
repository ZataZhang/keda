/**
 * Page object for the Agent Runner monitoring dashboard.
 *
 * Renders `/app/dashboard` and surfaces selectors and convenience helpers used by
 * Playwright smoke and workflow tests.
 */

import { expect, type Page } from '@playwright/test'

const SNAPSHOTS_ENDPOINT = /\/api\/v1\/agent-runner\/overview\/snapshots$/
const ISSUE_DETAIL_ENDPOINT = /\/api\/v1\/agent-runner\/issues\/\d+$/
const MONITOR_SETTINGS_ENDPOINT =
  /\/api\/v1\/agent-runner\/console\/monitor\/settings$/
const SCAN_REQUEST_PATTERN =
  /\/api\/v1\/agent-runner\/overview\/(per-repo|jobs\/)/

/** 单个仓库的 overview payload（与 `/overview` 的 repositories[] 元素同构）。 */
export type MonitoringRepositoryOverview = {
  repo_id: string
  display_name: string
  enabled: boolean
  base_branch: string
  remote: string
  health: {
    gh_available: boolean
    repo_path_exists: boolean
    publish_remote_exists: boolean
  }
  queue_counts: Record<string, number>
  labels: Record<string, string>
  issues: Array<{
    number: number
    title: string
    url: string
    labels: string[]
    state: string
    primary_label: string
    pr: {
      number: number | null
      url: string
      branch: string
      head_sha: string
      base_sha: string
      mergeable: boolean | null
      checks_state: string | null
      checks_summary: string[]
    } | null
    worktree: {
      exists: boolean
      path: string
      branch: string
      head_sha: string
      is_clean: boolean
      dirty_files: string[]
    }
    timeline: Array<{
      phase: string
      cycle: number
      comment_index: number
      action: string | null
      head_sha: string | null
      pr_branch: string | null
      checks_state: string | null
      mergeable: boolean | null
      raw_marker: string
    }>
    latest_event: {
      version: number
      phase: string
      cycle: number
      head_sha: string | null
      base_sha: string | null
      pr_branch: string | null
      action: string | null
      checks_state: string | null
      mergeable: boolean | null
      issue_comments_count: number | null
      pr_comments_count: number | null
    } | null
    anomalies: Array<{
      type: string
      severity: string
      message: string
      suggested_cli: string[]
    }>
    suggested_cli_commands: string[]
    has_anomaly: boolean
    anomaly_types: string[]
  }>
  anomaly_count: number
  anomaly_summary: { warning: number; error: number }
  scanned_at: string
}

/** registry 中路径失效、已从监控跳过的仓库。 */
export type UnreachableRepositoryEntry = {
  repo_id: string
  display_name: string
  configured_path: string
  error: string
}

/** `GET /agent-runner/overview/snapshots` 的响应体。 */
export type MonitorSnapshotsResponse = {
  repositories: Array<{
    repo_id: string
    scanned_at: string
    overview: MonitoringRepositoryOverview
  }>
  missing_repo_ids: string[]
  sync_status: 'ready' | 'partial' | 'pending_first_sync'
  scanned_at: string | null
  unreachable_repositories: UnreachableRepositoryEntry[]
}

/** `GET|PATCH /agent-runner/console/monitor/settings` 的响应体。 */
export type MonitorSettings = {
  sync_enabled: boolean
  sync_interval_seconds: number
  updated_at: string
}

/** 设置接口 stub 记录的请求，供断言 canonical path 与请求体。 */
export type MonitorSettingsTracker = {
  patchUrls: string[]
  patchBodies: Array<{ sync_enabled: boolean; sync_interval_seconds: number }>
  current: MonitorSettings
}

export class AgentRunnerMonitorPage {
  constructor(public readonly page: Page) {}

  /**
   * Stub the dashboard's snapshot endpoint with deterministic JSON.
   *
   * 快照接口是首屏与 15 秒轮询的唯一数据源，stub 之后页面完全不依赖真实
   * GitHub 或后端扫描。
   */
  async mockSnapshots(snapshots: MonitorSnapshotsResponse): Promise<void> {
    await this.page.route(SNAPSHOTS_ENDPOINT, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(snapshots),
      })
    })
  }

  /**
   * Stub the global sync settings endpoint for both GET and PATCH.
   *
   * Returns a tracker so specs can assert the canonical path and payload of
   * every save without touching the real backend.
   */
  async mockMonitorSettings(initial: MonitorSettings): Promise<MonitorSettingsTracker> {
    const tracker: MonitorSettingsTracker = {
      patchUrls: [],
      patchBodies: [],
      current: initial,
    }
    await this.page.route(MONITOR_SETTINGS_ENDPOINT, async (route) => {
      if (route.request().method() === 'PATCH') {
        const body = route.request().postDataJSON() as {
          sync_enabled: boolean
          sync_interval_seconds: number
        }
        tracker.patchUrls.push(route.request().url())
        tracker.patchBodies.push(body)
        tracker.current = {
          ...body,
          updated_at: '2026-05-24T12:05:00+00:00',
        }
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(tracker.current),
      })
    })
    return tracker
  }

  /**
   * Record every manual-refresh scan request (`per-repo` job creation and job
   * polling) so specs can assert the dashboard never creates scans on its own.
   */
  trackScanRequests(): string[] {
    const requestedUrls: string[] = []
    this.page.on('request', (request) => {
      if (SCAN_REQUEST_PATTERN.test(request.url())) {
        requestedUrls.push(request.url())
      }
    })
    return requestedUrls
  }

  async mockIssueDetail(
    issueNumber: number,
    detail: MonitoringRepositoryOverview['issues'][number],
  ): Promise<void> {
    await this.page.route(ISSUE_DETAIL_ENDPOINT, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(detail),
      })
    })
  }

  async goto(): Promise<void> {
    await this.page.goto('/app/dashboard')
  }

  async expectHeading(): Promise<void> {
    await expect(
      this.page.getByRole('heading', { name: 'Agent Runner 管理终端' }),
    ).toBeVisible()
  }

  async expectRepositoryVisible(displayName: string): Promise<void> {
    await expect(
      this.page.getByText(displayName, { exact: false }).first(),
    ).toBeVisible()
  }

  async expectAnomalyCount(count: number): Promise<void> {
    await expect(
      this.page.getByText(new RegExp(`异常\\s+${count}\\b`)).first(),
    ).toBeVisible()
  }

  /** 断言 header 显示了来自快照 `scanned_at` 的"上次同步"时间。 */
  async expectLastSyncVisible(): Promise<void> {
    const lastSync = this.page.getByTestId('dashboard-last-sync')
    await expect(lastSync).toBeVisible()
    await expect(lastSync).toContainText('上次同步')
  }

  /** 断言无快照时显示后端 sync_status 驱动的空态。 */
  async expectEmptyState(): Promise<void> {
    await expect(this.page.getByTestId('dashboard-empty-state')).toBeVisible()
    await expect(
      this.page.getByTestId('dashboard-last-sync'),
    ).toContainText('尚未同步')
  }

  async openIssue(issueNumber: number): Promise<void> {
    await this.page
      .getByRole('button', { name: new RegExp(`#${issueNumber}\\b`) })
      .first()
      .click()
  }

  async expectAnomalyCard(anomalyType: string): Promise<void> {
    await expect(this.page.getByText(anomalyType, { exact: true })).toBeVisible()
  }

  async expectSuggestedCommand(command: string): Promise<void> {
    await expect(this.page.getByText(command, { exact: true })).toBeVisible()
  }

  /** 展开 header 的内联同步设置面板。 */
  async openSettings(): Promise<void> {
    await this.page.getByTestId('dashboard-settings-toggle').click()
    await expect(this.page.getByTestId('monitor-settings-panel')).toBeVisible()
  }

  /** 在设置面板里选择一个同步间隔（分钟），会立即触发一次 PATCH。 */
  async selectInterval(minutes: number): Promise<void> {
    await this.page.getByTestId(`monitor-sync-interval-${minutes}`).click()
  }

  /** 断言设置面板里唯一处于选中态的同步间隔（分钟）。 */
  async expectSelectedInterval(minutes: number): Promise<void> {
    await this.page.mouse.move(5, 5) // 移开鼠标，避免 hover 影响底色判定
    await expect
      .poll(
        async () =>
          this.page.evaluate(() =>
            [1, 5, 15, 30, 60].filter((option) => {
              const element = document.querySelector(
                `[data-testid="monitor-sync-interval-${option}"]`,
              )
              return (
                element !== null &&
                getComputedStyle(element).backgroundColor !== 'rgb(255, 255, 255)'
              )
            }),
          ),
        { timeout: 5_000 },
      )
      .toEqual([minutes])
  }

  /** 断言设置面板显示"已保存"状态。 */
  async expectSettingsSaved(): Promise<void> {
    await expect(this.page.getByTestId('monitor-settings-status')).toContainText(
      '已保存',
    )
  }
}
