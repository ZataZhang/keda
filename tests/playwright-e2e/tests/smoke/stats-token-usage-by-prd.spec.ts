/**
 * Stats 页 Token 用量区三张表的浏览器流程验收（P1-FEAT-20261006-013227）。
 *
 * 页面、路由与交互为真实 Next.js 入口；仅把 prd-lifecycle 统计端点顶替为
 * 确定性 fixture（字段形状与真实响应逐字一致，见后端 `_serialize`），保证
 * CI 稳定。真实账本 + 真实 CLI 的端到端对照由 rv-2 / rv-4 harness 证据覆盖。
 *
 * 覆盖：按流程 / 按 agent / 按 PRD 三张表同屏、按 PRD 表按总量降序且首列
 * 显示 Issue 号、同一 PRD 多 run 合并为一行并显示执行次数、旧响应缺省新
 * 字段时显示明确空态而不是全零行、数值列（总量起）表头与单元格右对齐且
 * 文本列保持左对齐（issue-216）。
 */

import { expect, test } from '../../fixtures/session.fixture'
import type { Page, Route } from '@playwright/test'

const TOKEN_USAGE = {
  by_flow: {
    implement: {
      input_tokens: 1000,
      output_tokens: 200,
      cache_read_input_tokens: 300,
      cache_creation_input_tokens: 50,
      total_tokens: 1550,
      usage_count: 2,
    },
  },
  by_agent: {
    claude: {
      input_tokens: 1000,
      output_tokens: 200,
      cache_read_input_tokens: 300,
      cache_creation_input_tokens: 50,
      total_tokens: 1550,
      usage_count: 2,
    },
  },
}

/** 与后端 `asdict(PrdTokenUsageEntry)` 逐字对齐的按 PRD 条目 fixture。 */
const MOCK_STATS_WITH_BY_PRD = {
  repo_id: 'keda-main',
  window_days: 30,
  completed_runs: 2,
  average_end_to_end_seconds: 1200.0,
  median_end_to_end_seconds: 1200.0,
  p90_end_to_end_seconds: 1680.0,
  average_blocked_seconds: 120.0,
  bottleneck_phase: 'reviewing',
  bottleneck_phase_seconds: 2100.0,
  unlinked_run_count: 0,
  incomplete_run_count: 0,
  runs: [],
  token_usage: TOKEN_USAGE,
  token_usage_by_prd: [
    {
      repo_id: 'keda-main',
      prd_path: 'tasks/pending/P1-FEAT-20261001-200000-big-prd.md',
      issue_number: 207,
      run_count: 2,
      totals: {
        input_tokens: 800,
        output_tokens: 160,
        cache_read_input_tokens: 240,
        cache_creation_input_tokens: 40,
        total_tokens: 1240,
        usage_count: 3,
      },
    },
    {
      repo_id: 'keda-main',
      prd_path: 'tasks/pending/P1-FEAT-20261002-210000-small-prd.md',
      issue_number: 208,
      run_count: 1,
      totals: {
        input_tokens: 200,
        output_tokens: 40,
        cache_read_input_tokens: 60,
        cache_creation_input_tokens: 10,
        total_tokens: 310,
        usage_count: 1,
      },
    },
  ],
}

/** 旧后端响应形态：没有 token_usage_by_prd 字段。 */
const MOCK_STATS_WITHOUT_BY_PRD = {
  ...MOCK_STATS_WITH_BY_PRD,
  token_usage_by_prd: undefined,
}

/** 全空用量：页面必须给明确空态而不是全零行。 */
const MOCK_STATS_EMPTY_USAGE = {
  ...MOCK_STATS_WITH_BY_PRD,
  token_usage: { by_flow: {}, by_agent: {} },
  token_usage_by_prd: [],
}

async function fulfillJson(route: Route, payload: object): Promise<void> {
  // JSON.stringify 会丢弃值为 undefined 的键，正好复刻旧响应缺省新字段的形态。
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

async function openStatsPage(page: Page): Promise<void> {
  await page.goto('/app/stats')
  await expect(page.getByTestId('stats-prd-lifecycle')).toBeVisible()
}

test.describe('Stats token usage by PRD', () => {
  test('three tables share the token section; by-PRD sorted desc with issue prefix', async ({
    page,
  }) => {
    await page.route('/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) =>
      fulfillJson(route, MOCK_STATS_WITH_BY_PRD),
    )
    await openStatsPage(page)

    const section = page.getByTestId('stats-token-usage')
    await expect(section).toBeVisible()
    await expect(section).toContainText('按流程')
    await expect(section).toContainText('按 agent')

    const byPrd = page.getByTestId('stats-token-usage-by-prd')
    await expect(byPrd).toBeVisible()
    // 第一列可见 Issue 号；首行为消耗最大的 PRD（#207 1.2k 在 #208 310 之前）。
    const rows = byPrd.locator('tbody tr')
    await expect(rows).toHaveCount(2)
    await expect(rows.nth(0).locator('td').nth(0)).toHaveText('#207')
    await expect(rows.nth(0).locator('td').nth(1)).toHaveText(
      'P1-FEAT-20261001-200000-big-prd.md',
    )
    await expect(rows.nth(0).locator('td').nth(2)).toHaveText('1.2k')
    await expect(rows.nth(1).locator('td').nth(0)).toHaveText('#208')
    await expect(rows.nth(1).locator('td').nth(2)).toHaveText('310')

    // 同一 PRD 多 run 合并为一行并显示累计执行次数。
    await expect(rows.nth(0).locator('td').nth(9)).toHaveText('2')
  })

  test('numeric columns are right-aligned in header and body, label columns stay left', async ({
    page,
  }) => {
    await page.route('/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) =>
      fulfillJson(route, MOCK_STATS_WITH_BY_PRD),
    )
    await openStatsPage(page)

    // 按流程 / 按 agent 表：分组列左对齐，总量起的数值列右对齐。
    const flowTable = page.getByTestId('stats-token-usage').locator('table').first()
    const flowHeaderCells = flowTable.locator('thead th')
    await expect(flowHeaderCells.nth(0)).toHaveCSS('text-align', 'left')
    for (let index = 1; index <= 7; index += 1) {
      await expect(flowHeaderCells.nth(index)).toHaveCSS('text-align', 'right')
    }
    const flowRow = flowTable.locator('tbody tr').first().locator('td')
    await expect(flowRow.nth(0)).toHaveCSS('text-align', 'left')
    for (let index = 1; index <= 7; index += 1) {
      await expect(flowRow.nth(index)).toHaveCSS('text-align', 'right')
    }

    // 按 PRD 表：Issue / PRD 列左对齐，总量起的数值列右对齐。
    const prdTable = page.getByTestId('stats-token-usage-by-prd').locator('table')
    const prdHeaderCells = prdTable.locator('thead th')
    await expect(prdHeaderCells.nth(0)).toHaveCSS('text-align', 'left')
    await expect(prdHeaderCells.nth(1)).toHaveCSS('text-align', 'left')
    for (let index = 2; index <= 9; index += 1) {
      await expect(prdHeaderCells.nth(index)).toHaveCSS('text-align', 'right')
    }
    const prdRow = prdTable.locator('tbody tr').first().locator('td')
    await expect(prdRow.nth(0)).toHaveCSS('text-align', 'left')
    await expect(prdRow.nth(1)).toHaveCSS('text-align', 'left')
    for (let index = 2; index <= 9; index += 1) {
      await expect(prdRow.nth(index)).toHaveCSS('text-align', 'right')
    }
  })

  test('legacy response without by-PRD field shows explicit empty state', async ({ page }) => {
    await page.route('/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) =>
      fulfillJson(route, MOCK_STATS_WITHOUT_BY_PRD),
    )
    await openStatsPage(page)

    const byPrd = page.getByTestId('stats-token-usage-by-prd')
    await expect(byPrd).toBeVisible()
    await expect(byPrd.getByTestId('stats-token-usage-by-prd-empty')).toBeVisible()
    await expect(byPrd.locator('tbody tr')).toHaveCount(0)
  })

  test('no usage in window shows section-level empty state, not zero rows', async ({ page }) => {
    await page.route('/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) =>
      fulfillJson(route, MOCK_STATS_EMPTY_USAGE),
    )
    await openStatsPage(page)

    await expect(page.getByTestId('stats-token-usage-empty')).toBeVisible()
    await expect(page.getByTestId('stats-token-usage-by-prd')).toHaveCount(0)
  })
})
