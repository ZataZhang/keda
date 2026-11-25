/**
 * Stats Issue 账本真实路由验收。
 *
 * 验证层级：real user flow / production composition。真实 Next.js 页面、AppShell、
 * Tab、筛选与展开交互；仅替换 Console API 响应为字段形状固定的确定性 fixture，
 * 实际 SQLite 与 HTTP 聚合由 tests/test_agent_runner_console_api.py 覆盖。
 */

import { expect, test } from '../../fixtures/session.fixture'
import { mkdir, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page, Route } from '@playwright/test'

const EVIDENCE_DIRECTORY = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../tasks/evidence/P1-FEAT-20261125-175656-console-issue-cost-stats',
)

const ISSUE_STATS = {
  repo_id: null,
  start_date: '2026-11-01',
  end_date: '2026-11-25',
  unlinked_invocation_count: 0,
  truncated_run_history: false,
  repositories: [
    {
      repo_id: 'keda-main',
      display_name: 'Keda Main',
      github_available: true,
      github_error: null,
      github_truncated: false,
      issue_count: 4,
      status_counts: { blocked: 1, failed: 1, in_progress: 0, completed: 2 },
      issues: [
        {
          repo_id: 'keda-main',
          repository_name: 'Keda Main',
          issue_number: 318,
          title: '修复运行详情',
          issue_url: 'https://github.com/example/keda/issues/318',
          github_state: 'CLOSED',
          current_status: 'completed',
          status_filters: ['completed'],
          period_run_count: 3,
          totals: {
            input_tokens: 1_000_000,
            output_tokens: 0,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
            total_tokens: 3_000_000,
            usage_count: 2,
          },
          model_usage: [
            {
              model_id: 'model-a',
              totals: {
                input_tokens: 1_000_000,
                output_tokens: 0,
                cache_read_input_tokens: 0,
                cache_creation_input_tokens: 0,
                total_tokens: 1_000_000,
                usage_count: 1,
              },
              configuration_fallback_count: 0,
              unresolved_model_alias_count: 0,
            },
            {
              model_id: 'unknown',
              totals: {
                input_tokens: 2_000_000,
                output_tokens: 0,
                cache_read_input_tokens: 0,
                cache_creation_input_tokens: 0,
                total_tokens: 2_000_000,
                usage_count: 1,
              },
              configuration_fallback_count: 0,
              unresolved_model_alias_count: 0,
            },
          ],
          executions: [
            execution('run-1', 'completed', '2026-11-22T10:00:00+00:00', 'model-a', 1_000_000),
            { ...execution('run-2', 'failed', '2026-11-23T10:00:00+00:00', 'unknown', 2_000_000), missing_usage_count: 1 },
            execution('run-3', 'completed', '2026-11-24T10:00:00+00:00', null, 0),
          ],
          missing_usage_count: 2,
          period_data_available: true,
          truncated: false,
        },
        emptyIssue(309, '阻塞 Issue', 'blocked', ['blocked']),
        emptyIssue(295, '失败 Issue', 'failed', ['failed']),
        {
          ...emptyIssue(311, '确认免费模型', 'completed', ['completed']),
          period_run_count: 1,
          totals: {
            input_tokens: 100,
            output_tokens: 0,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
            total_tokens: 100,
            usage_count: 1,
          },
          model_usage: [
            {
              model_id: 'model-free',
              totals: {
                input_tokens: 100,
                output_tokens: 0,
                cache_read_input_tokens: 0,
                cache_creation_input_tokens: 0,
                total_tokens: 100,
                usage_count: 1,
              },
              configuration_fallback_count: 0,
              unresolved_model_alias_count: 0,
            },
          ],
          executions: [
            execution('free-run', 'completed', '2026-11-21T10:00:00+00:00', 'model-free', 100),
          ],
          period_data_available: true,
        },
        {
          ...emptyIssue(312, '未配置价格的模型', 'completed', ['completed']),
          period_run_count: 1,
          totals: {
            input_tokens: 250,
            output_tokens: 0,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
            total_tokens: 250,
            usage_count: 1,
          },
          model_usage: [
            {
              model_id: 'unknown',
              totals: {
                input_tokens: 250,
                output_tokens: 0,
                cache_read_input_tokens: 0,
                cache_creation_input_tokens: 0,
                total_tokens: 250,
                usage_count: 1,
              },
              configuration_fallback_count: 0,
              unresolved_model_alias_count: 0,
            },
          ],
          executions: [execution('unpriced-run', 'completed', '2026-11-20T10:00:00+00:00', 'unknown', 250)],
          period_data_available: true,
        },
      ],
    },
    {
      repo_id: 'freshai',
      display_name: 'FreshAI',
      github_available: true,
      github_error: null,
      github_truncated: false,
      issue_count: 1,
      status_counts: { blocked: 0, failed: 0, in_progress: 1, completed: 0 },
      issues: [
        {
          ...emptyIssue(318, '跨仓同号 Issue', 'in_progress', ['in_progress']),
          repo_id: 'freshai',
          repository_name: 'FreshAI',
          issue_url: 'https://github.com/example/freshai/issues/318',
          period_run_count: 1,
          totals: {
            input_tokens: 1_000,
            output_tokens: 0,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
            total_tokens: 1_000,
            usage_count: 1,
          },
          model_usage: [
            {
              model_id: 'model-b',
              totals: {
                input_tokens: 1_000,
                output_tokens: 0,
                cache_read_input_tokens: 0,
                cache_creation_input_tokens: 0,
                total_tokens: 1_000,
                usage_count: 1,
              },
              configuration_fallback_count: 0,
              unresolved_model_alias_count: 0,
            },
          ],
          executions: [execution('fresh-run', 'completed', '2026-11-24T11:00:00+00:00', 'model-b', 1_000)],
          period_data_available: true,
        },
      ],
    },
  ],
}

const ISSUE_STATS_FOR_NOVEMBER_23 = {
  ...ISSUE_STATS,
  start_date: '2026-11-23',
  end_date: '2026-11-23',
  repositories: [
    {
      ...ISSUE_STATS.repositories[0],
      issues: [
        {
          ...ISSUE_STATS.repositories[0].issues[0],
          period_run_count: 1,
          totals: {
            input_tokens: 2_000_000,
            output_tokens: 0,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
            total_tokens: 2_000_000,
            usage_count: 1,
          },
          model_usage: [ISSUE_STATS.repositories[0].issues[0].model_usage[1]],
          executions: [ISSUE_STATS.repositories[0].issues[0].executions[1]],
          missing_usage_count: 0,
          period_data_available: true,
        },
        emptyIssue(309, '阻塞 Issue', 'blocked', ['blocked']),
        emptyIssue(295, '失败 Issue', 'failed', ['failed']),
        emptyIssue(311, '确认免费模型', 'completed', ['completed']),
      ],
    },
    {
      ...ISSUE_STATS.repositories[1],
      issues: [
        {
          ...emptyIssue(318, '跨仓同号 Issue', 'in_progress', ['in_progress']),
          repo_id: 'freshai',
          repository_name: 'FreshAI',
          issue_url: 'https://github.com/example/freshai/issues/318',
        },
      ],
    },
  ],
}

const MODEL_PRICING = {
  models: [
    {
      model_id: 'model-a',
      input_usd_per_million: 2,
      output_usd_per_million: 0,
      cache_read_usd_per_million: 0,
      cache_creation_usd_per_million: 0,
    },
    {
      model_id: 'model-free',
      input_usd_per_million: 0,
      output_usd_per_million: 0,
      cache_read_usd_per_million: 0,
      cache_creation_usd_per_million: 0,
    },
  ],
  updated_at: '2026-11-25T00:00:00+00:00',
}

const AGENT_PERFORMANCE = {
  repo_id: null,
  window_days: 25,
  agents: [],
  presets: [],
  unbound_preset_attempts: [],
  runs: [],
}

const PRD_LIFECYCLE = {
  repo_id: null,
  window_days: 25,
  completed_runs: 0,
  average_end_to_end_seconds: null,
  median_end_to_end_seconds: null,
  p90_end_to_end_seconds: null,
  average_blocked_seconds: null,
  bottleneck_phase: null,
  bottleneck_phase_seconds: null,
  unlinked_run_count: 0,
  incomplete_run_count: 0,
  runs: [],
  token_usage: { by_flow: {}, by_agent: {} },
  token_usage_by_prd: [],
  token_usage_by_model: {
    by_model: {},
    by_agent: {},
    by_prd: [],
    truncated: false,
  },
}

async function fulfillJson(route: Route, responseBody: object): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(responseBody),
  })
}

async function routeStatsApi(
  page: Page,
  stats: object = ISSUE_STATS,
  pricing: object = MODEL_PRICING,
): Promise<void> {
  await page.route('**/api/v1/agent-runner/console/stats/issues*', (route) =>
    fulfillJson(route, stats),
  )
  await page.route('**/api/v1/agent-runner/console/stats/agent-performance*', (route) =>
    fulfillJson(route, AGENT_PERFORMANCE),
  )
  await page.route('**/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) =>
    fulfillJson(route, PRD_LIFECYCLE),
  )
  await page.route('**/api/v1/agent-runner/console/model-pricing', (route) =>
    fulfillJson(route, pricing),
  )
}

async function captureEvidenceScreenshot(page: Page, fileName: string): Promise<void> {
  await mkdir(EVIDENCE_DIRECTORY, { recursive: true })
  await page.screenshot({ path: resolve(EVIDENCE_DIRECTORY, fileName) })
}

function execution(
  executionId: string,
  outcome: string,
  startedAt: string,
  modelId: string | null,
  inputTokens: number,
) {
  return {
    execution_id: executionId,
    outcome,
    trigger: 'cli_run',
    agent: 'codex',
    started_at: startedAt,
    finished_at: startedAt,
    duration_seconds: 30,
    error_summary: outcome === 'failed' ? '验证失败后重试' : null,
    model_usage: modelId
      ? [
          {
            model_id: modelId,
            totals: {
              input_tokens: inputTokens,
              output_tokens: 0,
              cache_read_input_tokens: 0,
              cache_creation_input_tokens: 0,
              total_tokens: inputTokens,
              usage_count: 1,
            },
            configuration_fallback_count: 0,
            unresolved_model_alias_count: 0,
          },
        ]
      : [],
    missing_usage_count: modelId ? 0 : 1,
  }
}

function emptyIssue(
  issueNumber: number,
  title: string,
  currentStatus: string,
  statusFilters: string[],
) {
  return {
    repo_id: 'keda-main',
    repository_name: 'Keda Main',
    issue_number: issueNumber,
    title,
    issue_url: `https://github.com/example/keda/issues/${issueNumber}`,
    github_state: currentStatus === 'completed' ? 'CLOSED' : 'OPEN',
    current_status: currentStatus,
    status_filters: statusFilters,
    period_run_count: 0,
    totals: {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_input_tokens: 0,
      cache_creation_input_tokens: 0,
      total_tokens: 0,
      usage_count: 0,
    },
    model_usage: [],
    executions: [],
    missing_usage_count: 0,
    period_data_available: false,
    truncated: false,
  }
}

test.describe('Stats Issue cost ledger', () => {
  test('opens by Issue and expands executions and model usage', async ({ page }) => {
    await page.setViewportSize({ width: 1586, height: 992 })
    await routeStatsApi(page)
    await page.goto('/app/stats')

    await expect(page.getByRole('tab', { name: '按 Issue' })).toHaveAttribute('aria-selected', 'true')
    const issueLedger = page.getByTestId('stats-issue-ledger')
    await expect(issueLedger).toBeVisible()
    const firstIssueRow = page.getByTestId('stats-issue-row-keda-main-318')
    await expect(firstIssueRow).toContainText('修复运行详情')
    await expect(firstIssueRow).toContainText('部分计价')
    await expect(firstIssueRow).toContainText('$2.000000')
    await expect(page.getByTestId('stats-issue-row-freshai-318')).toBeVisible()
    await expect(page.getByTestId('stats-issue-row-keda-main-311')).toContainText('$0.000000')
    await expect(page.getByTestId('stats-issue-row-keda-main-312')).toContainText('暂无估价')
    await expect(page.locator('tr[data-testid^="stats-issue-row-"]').first()).toHaveAttribute(
      'data-testid',
      'stats-issue-row-freshai-318',
    )

    await page.getByTestId('stats-issue-expand-keda-main-318').click()
    const executionRows = page.getByTestId('stats-issue-execution-row')
    await expect(executionRows).toHaveCount(3)
    await expect(executionRows.nth(1)).toContainText('暂无估价')
    await expect(executionRows.nth(1)).not.toContainText('$0.000000')
    await expect(page.getByTestId('stats-issue-executions')).toContainText('失败')
    await expect(page.getByTestId('stats-issue-executions')).toContainText('用量缺失 1')
    await captureEvidenceScreenshot(page, 'rv-2-issue-stats.png')

    await page.getByTestId('stats-issue-detail-tab-models').click()
    const modelUsage = page.getByTestId('stats-issue-model-usage')
    await expect(modelUsage).toContainText('model-a')
    await expect(modelUsage).toContainText('未识别模型')
    await expect(modelUsage).toContainText('暂无估价')
    await captureEvidenceScreenshot(page, 'rv-2-issue-models.png')
  })

  test('searches, sorts, filters, and opens repository status rows with no period run', async ({ page }) => {
    await page.setViewportSize({ width: 1586, height: 992 })
    await routeStatsApi(page)
    await page.goto('/app/stats')
    await expect(page.getByTestId('stats-issue-ledger')).toBeVisible()

    const searchInput = page.getByRole('textbox', { name: '搜索 Issue 标题或编号' })
    await searchInput.fill('318')
    await expect(page.getByTestId('stats-issue-row-keda-main-318')).toBeVisible()
    await expect(page.getByTestId('stats-issue-row-freshai-318')).toBeVisible()
    await expect(page.getByTestId('stats-issue-row-keda-main-309')).toHaveCount(0)
    await searchInput.fill('运行详情')
    await expect(page.getByTestId('stats-issue-row-keda-main-318')).toBeVisible()
    await expect(page.getByTestId('stats-issue-row-freshai-318')).toHaveCount(0)
    await searchInput.clear()

    await page.getByLabel('Issue 排序方式').selectOption('cost')
    await expect(page.locator('tr[data-testid^="stats-issue-row-"]').first()).toHaveAttribute(
      'data-testid',
      'stats-issue-row-keda-main-318',
    )

    await page.getByRole('tab', { name: '仓库状态' }).click()
    await expect(page.getByTestId('stats-repository-status')).toBeVisible()
    await captureEvidenceScreenshot(page, 'rv-2-repository-status.png')
    await page.getByTestId('stats-repository-status-link-keda-main-blocked').click()
    await expect(page.getByRole('tab', { name: '按 Issue' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByLabel('统计仓库筛选')).toHaveValue('keda-main')
    await expect(page.getByLabel('Issue 状态筛选')).toHaveValue('blocked')
    const blockedIssueRow = page.getByTestId('stats-issue-row-keda-main-309')
    await expect(blockedIssueRow).toContainText('无期间数据')
    await expect(blockedIssueRow).toContainText('0')
  })

  test('uses the selected UTC date window for trends and token coverage', async ({ page }) => {
    await page.setViewportSize({ width: 1586, height: 992 })
    const requestedDateRanges: string[] = []
    await page.route('**/api/v1/agent-runner/console/stats/issues*', async (route) => {
      const requestUrl = new URL(route.request().url())
      const startDate = requestUrl.searchParams.get('start_date')
      const endDate = requestUrl.searchParams.get('end_date')
      requestedDateRanges.push(`${startDate}..${endDate}`)
      await fulfillJson(route, {
        ...(startDate === '2026-11-23' && endDate === '2026-11-23'
          ? ISSUE_STATS_FOR_NOVEMBER_23
          : ISSUE_STATS),
        start_date: startDate,
        end_date: endDate,
      })
    })
    await page.route('**/api/v1/agent-runner/console/stats/agent-performance*', (route) => fulfillJson(route, AGENT_PERFORMANCE))
    await page.route('**/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) => fulfillJson(route, PRD_LIFECYCLE))
    await page.route('**/api/v1/agent-runner/console/model-pricing', (route) => fulfillJson(route, MODEL_PRICING))
    await page.goto('/app/stats')
    await expect(page.getByTestId('stats-issue-ledger')).toBeVisible()

    await page.getByRole('tab', { name: '用量趋势' }).click()
    await page.getByRole('button', { name: /查看 2026-11-23 的 Issue/ }).click()
    await expect.poll(() => requestedDateRanges.at(-1)).toBe('2026-11-23..2026-11-23')
    await expect(page.getByTestId('stats-issue-row-keda-main-318')).toContainText('1')
    await expect(page.getByTestId('stats-issue-row-freshai-318')).toHaveCount(0)
    await expect(page.getByTestId('stats-usage-trend')).toContainText('2026-11-23 至 2026-11-23')
    await page.getByTestId('stats-trend-measure-tokens').click()
    await expect(page.getByTestId('stats-trend-measure-tokens')).toHaveAttribute('aria-pressed', 'true')
    await captureEvidenceScreenshot(page, 'rv-3-trend-and-coverage-trend.png')

    await page.getByRole('tab', { name: '模型与计价' }).click()
    await expect(page.getByTestId('stats-model-pricing')).toContainText('0%')
    await expect(page.getByTestId('stats-model-pricing')).toContainText('0 / 2000.0k 已配置价格')
    await expect(page.getByTestId('stats-model-pricing')).toContainText('暂无估价')
    await expect(
      page.getByTestId('stats-model-pricing').getByRole('link', { name: '设置模型价格' }),
    ).toHaveAttribute('href', '/app/settings/')
    await captureEvidenceScreenshot(page, 'rv-3-trend-and-coverage-pricing.png')
  })
  test('prices the SQLite-backed Console HTTP response in the production ledger', async ({ page }) => {
    const fixturePath = process.env.KC_ISSUE_COST_HTTP_FIXTURE
    test.skip(!fixturePath, 'Export the Console HTTP fixture from the SQLite integration test first.')
    const fixture = JSON.parse(await readFile(fixturePath!, 'utf8'))
    await routeStatsApi(page, fixture.stats, fixture.pricing)
    await page.goto('/app/stats')
    const issue = page.getByTestId('stats-issue-row-keda-main-7')
    await expect(issue).toContainText('320')
    await expect(issue).toContainText('$0.000750')
    await expect(issue).toContainText('部分计价')
    await page.getByTestId('stats-issue-expand-keda-main-7').click()
    const executions = page.getByTestId('stats-issue-execution-row')
    await expect(executions).toHaveCount(3)
    await expect(executions.filter({ hasText: '$0.000425' })).toHaveCount(1)
    await expect(executions.filter({ hasText: '$0.000325' })).toContainText('失败')
    await expect(executions.filter({ hasText: '暂无估价' })).toHaveCount(1)
    await page.getByTestId('stats-issue-detail-tab-models').click()
    const models = page.getByTestId('stats-issue-model-usage')
    await expect(models.getByRole('row').filter({ hasText: 'model-a' })).toContainText('$0.000750')
    await expect(models.getByRole('row').filter({ hasText: '未识别模型' })).toContainText('暂无估价')
  })

})
