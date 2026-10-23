/** 用真实 Stats 页面与布局验证用量缺失提示；仅替换 HTTP 读取响应。 */
import { expect, test } from '@playwright/test'

const ZERO_TOTALS = {
  input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0, total_tokens: 0, usage_count: 249,
}

test('missing usage and zero reports cannot look like a complete free bill', async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    let payload: object = {}
    if (pathname === '/api/auth/me') {
      payload = { user_id: 'operator', display_name: 'operator', email: 'operator@localhost' }
    } else if (pathname.endsWith('/stats/prd-lifecycle')) {
      payload = {
        repo_id: null, window_days: 30, completed_runs: 0,
        average_end_to_end_seconds: null, median_end_to_end_seconds: null,
        p90_end_to_end_seconds: null, average_blocked_seconds: null,
        bottleneck_phase: null, bottleneck_phase_seconds: null,
        unlinked_run_count: 0, incomplete_run_count: 0, runs: [],
        token_usage: { by_flow: {}, by_agent: {} }, token_usage_by_prd: [],
        token_usage_by_model: {
          by_model: {
            'Qwen3.8-Flash': ZERO_TOTALS,
            'gpt-6-luna': { ...ZERO_TOTALS, input_tokens: 100, total_tokens: 100, usage_count: 2 },
            unknown: { ...ZERO_TOTALS, input_tokens: 50, total_tokens: 50, usage_count: 3 },
          }, by_agent: {}, by_prd: [],
          truncated: false, missing_usage_by_agent: { codex: 118 },
          zero_usage_by_model: { 'Qwen3.8-Flash': 249 },
          configuration_fallback_counts: { 'gpt-6-luna': 2 },
          unresolved_model_alias_counts: { sonnet: 3 },
        },
      }
    } else if (pathname.endsWith('/model-pricing')) {
      payload = { models: ['Qwen3.8-Flash', 'gpt-6-luna'].map((model_id) => ({
        model_id, input_usd_per_million: 1,
        output_usd_per_million: 1, cache_read_usd_per_million: 1,
        cache_creation_usd_per_million: 1,
      })), updated_at: '' }
    } else if (pathname.endsWith('/stats/overview')) {
      payload = { repositories: [] }
    } else if (pathname.endsWith('/stats/history')) {
      payload = { trend: [] }
    } else if (pathname.endsWith('/stats/agent-performance')) {
      payload = { repo_id: null, agents: [], presets: [], runs: [], unbound_preset_attempts: [] }
    } else if (pathname.endsWith('/runs')) {
      payload = { runs: [] }
    }
    await route.fulfill({ json: payload })
  })
  await page.goto('/app/stats/')
  const section = page.getByTestId('stats-token-usage')
  await expect(section).toBeVisible()
  await expect(page.getByTestId('stats-token-usage-gaps')).toContainText('codex 118 次')
  await expect(page.getByTestId('stats-token-usage-gaps')).toContainText('249 次调用上报用量为 0')
  const modelRow = section.getByRole('row').filter({ hasText: 'Qwen3.8-Flash' })
  await expect(modelRow).toContainText('用量上报为 0')
  await expect(section).not.toContainText('$0.000000')
  await expect(page.getByTestId('stats-model-config-fallback')).toContainText('gpt-6-luna 2 次')
  await expect(page.getByTestId('stats-model-config-aliases')).toContainText('sonnet 3 次')
  await expect(section.getByRole('row').filter({ hasText: 'gpt-6-luna' })).toContainText('含配置估价')
  await expect(section.getByRole('row').filter({ hasText: 'gpt-6-luna' })).toContainText('$0.000100')
  await section.scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'test-results/stats-usage-gaps-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByTestId('stats-token-usage-gaps')).toBeVisible()
  await page.screenshot({ path: 'test-results/stats-usage-gaps-mobile.png', fullPage: true })
})
