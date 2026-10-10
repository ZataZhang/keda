/**
 * Stats 页 Agent 与预设表现的真实页面入口验证。
 *
 * Next.js 页面、AppShell、筛选控件与 API client 保持真实；只用确定性响应
 * 替代新增的 agent-performance 统计端点。
 */

import { expect, test } from '../../fixtures/session.fixture'
import type { Page, Route } from '@playwright/test'

const PERFORMANCE_STATS = {
  repo_id: null,
  window_days: 30,
  agents: [
    {
      repo_id: 'keda-main',
      agent: 'codex',
      preset: null,
      model: null,
      attempt_count: 4,
      success_count: 3,
      non_success_count: 1,
      success_rate: 0.75,
      non_success_rate: 0.25,
      p50_duration_seconds: 90,
      p90_duration_seconds: 150,
      failure_types: [{ failure_type: 'verification_failed', count: 1 }],
    },
  ],
  presets: [
    {
      repo_id: 'keda-main',
      agent: 'codex',
      preset: 'removed-preset',
      model: 'provider/model-x',
      attempt_count: 4,
      success_count: 3,
      non_success_count: 1,
      success_rate: 0.75,
      non_success_rate: 0.25,
      p50_duration_seconds: 90,
      p90_duration_seconds: 150,
      failure_types: [{ failure_type: 'verification_failed', count: 1 }],
    },
  ],
  unbound_preset_attempt_count: 2,
  runs: [
    {
      repo_id: 'keda-main',
      outcome: 'completed',
      run_count: 2,
      p50_duration_seconds: 300,
      p90_duration_seconds: 420,
    },
    {
      repo_id: 'keda-main',
      outcome: 'failed',
      run_count: 1,
      p50_duration_seconds: 180,
      p90_duration_seconds: 180,
    },
    {
      repo_id: 'keda-main',
      outcome: 'blocked',
      run_count: 1,
      p50_duration_seconds: 600,
      p90_duration_seconds: 600,
    },
  ],
}

const EMPTY_PERFORMANCE_STATS = {
  repo_id: null,
  window_days: 30,
  agents: [],
  presets: [],
  unbound_preset_attempt_count: 0,
  runs: [],
}

async function mockPerformanceStats(
  page: Page,
  payload: object,
  status = 200,
  requests: Array<{ method: string; url: string }> = [],
): Promise<void> {
  await page.route(
    '**/stats/agent-performance*',
    async (route: Route) => {
      requests.push({
        method: route.request().method(),
        url: route.request().url(),
      })
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(payload),
      })
    },
  )
}

function expectCanonicalPerformanceRequests(
  requests: Array<{ method: string; url: string }>,
): void {
  expect(requests.length).toBeGreaterThan(0)
  for (const request of requests) {
    expect(request.method).toBe('GET')
    expect(new URL(request.url).pathname).toBe(
      '/api/v1/agent-runner/console/stats/agent-performance',
    )
  }
}

async function openStatsPage(page: Page): Promise<void> {
  await page.goto('/app/stats')
  await expect(page.getByTestId('stats-agent-performance')).toBeVisible()
}

test.describe('Stats Agent performance', () => {
  test('renders attempt groups, historical model snapshots and independent run outcomes', async ({
    page,
  }) => {
    await mockPerformanceStats(page, PERFORMANCE_STATS)
    await openStatsPage(page)

    const agentTable = page.getByTestId('stats-agent-performance-agent-table')
    await expect(agentTable).toContainText('codex')
    await expect(agentTable).toContainText('4')
    await expect(agentTable).toContainText('3 / 1')
    await expect(agentTable).toContainText('75% / 25%')
    await expect(agentTable).toContainText('verification_failed: 1')

    const presetTable = page.getByTestId('stats-agent-performance-preset-table')
    await expect(presetTable).toContainText('removed-preset')
    const historicalPresetRow = presetTable.getByRole('row').filter({ hasText: 'removed-preset' })
    await expect(historicalPresetRow).toContainText('keda-main')
    await expect(historicalPresetRow).toContainText('provider/model-x')
    await expect(historicalPresetRow).toContainText('4')
    await expect(page.getByTestId('stats-agent-performance-unbound')).toContainText('2 次')

    const runTable = page.getByTestId('stats-agent-performance-run-table')
    await expect(runTable).toContainText('keda-main')
    await expect(runTable).toContainText('completed')
    await expect(runTable).toContainText('failed')
    await expect(runTable).toContainText('blocked')
    await expect(runTable).toContainText('任务数')
    await expect(page.getByTestId('stats-prd-lifecycle')).toBeVisible()
  })

  test('shows an explicit empty state and applies the selected time window', async ({
    page,
  }) => {
    // 仓库下拉来自 GitHub 实时 overview（后端 TTL 30s）：冷重建可能超过半分钟，
    // 也可能本轮返回空。先耐心等选项出现，返回空时才整页刷新重试。
    test.setTimeout(240_000)
    const requests: Array<{ method: string; url: string }> = []
    await mockPerformanceStats(page, EMPTY_PERFORMANCE_STATS, 200, requests)
    await openStatsPage(page)
    await expect(page.getByTestId('stats-agent-performance-empty')).toBeVisible()
    await expect(page.getByText('暂无可用样本，成功率与耗时显示为 —。').first()).toBeVisible()
    expectCanonicalPerformanceRequests(requests)

    const repositoryFilter = page.getByLabel('趋势仓库筛选')
    const repositoryOption = repositoryFilter.locator('option').nth(1)
    try {
      await expect(repositoryOption).toHaveAttribute('value', /\S/, { timeout: 45_000 })
    } catch {
      await expect(async () => {
        await page.reload()
        await expect(page.getByTestId('stats-agent-performance')).toBeVisible()
        await expect(repositoryOption).toHaveAttribute('value', /\S/, { timeout: 45_000 })
      }).toPass({ timeout: 150_000 })
    }
    const repositoryId = await repositoryOption.getAttribute('value')
    expect(repositoryId).toBeTruthy()
    await repositoryFilter.selectOption(repositoryId)
    await expect
      .poll(() =>
        requests.some((request) => new URL(request.url).searchParams.get('repo_id') === repositoryId),
      )
      .toBe(true)

    await page.getByLabel('趋势天数').selectOption('7')
    await expect
      .poll(() => requests.some((request) => new URL(request.url).searchParams.get('days') === '7'))
      .toBe(true)
    expect(requests.some((request) => new URL(request.url).searchParams.get('days') === '30')).toBe(true)
    expectCanonicalPerformanceRequests(requests)
  })

  test('keeps the existing Stats areas available when this endpoint fails', async ({ page }) => {
    await mockPerformanceStats(page, { detail: 'temporary failure' }, 503)
    await openStatsPage(page)

    await expect(page.getByTestId('stats-agent-performance-error')).toContainText('新统计暂不可用')
    await expect(page.getByTestId('stats-prd-lifecycle')).toBeVisible()
    await expect(page.getByText('历史趋势（本地运行记录）')).toBeVisible()
  })

  test('keeps the tables horizontally scrollable at a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    const requests: Array<{ method: string; url: string }> = []
    await mockPerformanceStats(page, PERFORMANCE_STATS, 200, requests)
    await openStatsPage(page)

    // 统计请求由 hydration 后的客户端 effect 发出；先确认请求出现，
    // 避免冷编译下点击落在尚未挂 handler 的预渲染按钮上。
    await expect.poll(() => requests.length).toBeGreaterThan(0)

    // 375px 下先使用现有导航收起控件，为 Stats 内容留出可读宽度。
    await expect(async () => {
      await page.getByRole('button', { name: '收起导航栏' }).click()
      await expect(page.getByRole('button', { name: '展开导航栏' })).toBeVisible({
        timeout: 1_000,
      })
    }).toPass()

    const tableContainer = page.getByTestId('stats-agent-performance-preset-table')
    await expect(tableContainer).toBeVisible()
    const dimensions = await tableContainer.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
      overflowX: getComputedStyle(element).overflowX,
    }))
    expect(dimensions.scrollWidth).toBeGreaterThan(dimensions.clientWidth)
    expect(dimensions.overflowX).toBe('auto')
  })
})
