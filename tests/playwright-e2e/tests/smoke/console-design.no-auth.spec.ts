/**
 * 管理终端视觉一致性的 production composition 验证。
 * 通过正式路由、AppShell、主题与区块导航操作，仅替换 HTTP 读取为本系统的空数据契约。
 * 覆盖九页的字号/边距、浅深色主题、窄屏，以及 Stats 在主内容内吸顶和滚动高亮。
 */
import { expect, test, type Page } from '@playwright/test'

const PAGES = [
  ['dashboard', 'Agent Runner 管理终端'], ['processes', '托管进程'],
  ['repositories', '项目接入'], ['stats', '统计与成本'],
  ['evaluations', '模型评测'], ['ideas', 'Idea Inbox'],
  ['settings', '设置'], ['settings/lifecycle', '生命周期与模型设置'], ['backlog', 'Backlog'],
] as const

/** 空数据足以验证生产布局，未知读接口显式报错，所有写操作都阻止落到真实服务。 */
async function mockDesignReads(page: Page) {
  page.on('pageerror', (error) => { throw error })
  await page.route('**/pypi/kedacode/json', (route) => route.fulfill({ json: { info: { version: '0.0.0' } } }))
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') throw new Error(`Unexpected write: ${pathname}`)
    let payload: object | undefined
    if (pathname === '/api/auth/me') payload = { user_id: 'operator', display_name: 'Operator', email: 'operator@localhost' }
    else if (pathname.endsWith('/repositories')) payload = { repositories: [] }
    else if (pathname.endsWith('/overview/snapshots')) payload = { repositories: [], missing_repo_ids: [], sync_status: 'ready', scanned_at: null }
    else if (pathname.endsWith('/runtime/tasks')) payload = { tasks: [], status_counts: {}, sync_status: 'ready', unavailable_sources: [], missing_repo_ids: [], truncated: false }
    else if (pathname.endsWith('/stats/overview')) payload = { repositories: [] }
    else if (pathname.endsWith('/stats/history')) payload = { trend: [] }
    else if (pathname.endsWith('/stats/agent-performance')) payload = { agents: [], presets: [], runs: [], unbound_preset_attempts: [] }
    else if (pathname.endsWith('/stats/prd-lifecycle')) payload = {
      completed_runs: 0, runs: [], token_usage: { by_flow: {}, by_agent: {} },
      token_usage_by_prd: [], unlinked_run_count: 0, incomplete_run_count: 0,
      average_end_to_end_seconds: null, median_end_to_end_seconds: null,
      p90_end_to_end_seconds: null, average_blocked_seconds: null,
      bottleneck_phase: null, bottleneck_phase_seconds: null,
    }
    else if (pathname.endsWith('/model-pricing')) payload = { models: [], updated_at: null }
    else if (pathname.endsWith('/processes')) payload = { processes: [], unmanaged_processes: [] }
    else if (pathname.endsWith('/runs')) payload = { runs: [] }
    else if (pathname.endsWith('/evaluation/suites')) payload = { suites: [] }
    else if (pathname.endsWith('/evaluation/presets')) payload = { execution_presets: [], judge_presets: [], default_judge_presets: [], max_concurrency: 1, agent_timeout_seconds: 0, judge_timeout_seconds: 0 }
    else if (pathname.endsWith('/evaluation/runs')) payload = { runs: [] }
    else if (pathname.endsWith('/agent-labels')) payload = { labels: [{ agent: 'codex', label: 'codex', label_color: '7C3AED', label_description: '代码执行器' }] }
    else if (pathname.endsWith('/lifecycle-settings')) payload = {
      scope: 'global', repo_id: null, agents: [], agent_availability: [], lifecycles: [], presets: [],
      fallback: { candidates: [], max_agent_switches: 0 }, field_source_vocabulary: {},
    }
    else if (pathname.endsWith('/version')) payload = { version: 'unknown' }
    await route.fulfill(payload ? { json: payload } : { status: 503, json: { detail: 'Read fixture unavailable' } })
  })
}

for (const theme of ['light', 'dark'] as const) {
  test(`${theme}: page titles and content align across the console`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.addInitScript((selectedTheme) => localStorage.setItem('theme', selectedTheme), theme)
    await mockDesignReads(page)
    for (const [path, title] of PAGES) {
      await page.goto(`/app/${path}/`)
      const heading = page.getByRole('heading', { name: title, level: 1, exact: true })
      await expect(heading).toBeVisible()
      await expect(heading).toHaveCSS('font-size', '20px')
      await expect(heading).toHaveCSS('font-weight', '600')
      const box = await heading.boundingBox()
      expect(box!.x).toBe(280)
      expect(box!.y).toBe(24)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
      await page.screenshot({ path: `/tmp/keda-design-${theme}-${path.replace('/', '-')}.png`, animations: 'disabled' })
    }
  })
}

test('theme controls share primary color and compact toolbar sizing', async ({ page }) => {
  await mockDesignReads(page)
  await page.goto('/app/stats/')
  const repositoryFilter = page.getByRole('combobox', { name: '统计仓库筛选' })
  await expect(repositoryFilter).toHaveCSS('height', '32px')
  await expect(repositoryFilter).toHaveCSS('border-radius', '8px')
  const firstTab = page.getByRole('tab', { name: '总览', exact: true })
  await expect(firstTab).toHaveAttribute('aria-selected', 'true')
  const activeColor = await firstTab.evaluate((element) => getComputedStyle(element).color)
  const primaryColor = await firstTab.evaluate((element) => {
    const probe = document.createElement('span')
    probe.style.color = 'var(--primary)'
    element.append(probe)
    const color = getComputedStyle(probe).color
    probe.remove()
    return color
  })
  expect(activeColor).toBe(primaryColor)
})

test('stats section tabs stay inside main and track its scroll position', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await mockDesignReads(page)
  await page.goto('/app/stats/')
  const navigation = page.getByTestId('stats-section-tabs')
  const main = page.getByRole('main')
  await page.getByRole('tab', { name: 'PRD 执行分析', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'PRD 执行分析', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  await expect.poll(async () => (await navigation.boundingBox())!.y).toBeCloseTo(0, 0)
  expect((await navigation.boundingBox())!.x).toBeGreaterThanOrEqual(256)
  await expect(page.getByRole('complementary').getByRole('link', { name: 'Dashboard', exact: true })).toBeInViewport()
  await main.hover()
  await page.mouse.wheel(0, -10000)
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBe(0)
  await expect(page.getByRole('tab', { name: '总览', exact: true })).toHaveAttribute('aria-selected', 'true')
})

test('compact page headers and contained content work on narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockDesignReads(page)
  for (const [path, title] of PAGES) {
    await page.goto(`/app/${path}/`)
    const heading = page.getByRole('heading', { name: title, level: 1, exact: true })
    await expect(heading).toBeVisible()
    expect((await heading.boundingBox())!.x).toBe(16)
    await expect(heading).toHaveCSS('font-size', '20px')
    expect.soft(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${path}: no horizontal overflow`).toBeTruthy()
    await page.screenshot({ path: `/tmp/keda-design-mobile-${path.replace('/', '-')}.png`, animations: 'disabled' })
  }
})

/** 窄屏沿文档滚动时，设置导航仍应在视口内吸顶。 */
test('settings tabs remain sticky during narrow document scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 640 })
  await mockDesignReads(page)
  await page.route('**/api/v1/agent-runner/agent-labels', (route) => route.fulfill({ json: { labels: ['codex', 'claude', 'qoder', 'kimi', 'codebuddy', 'gemini', 'cursor', 'custom'].map((agent) => ({ agent, label: agent, label_color: '7C3AED', label_description: '已配置执行器' })) } }))
  await page.goto('/app/settings/')
  await page.getByRole('tab', { name: 'Agent 管理', exact: true }).click()
  const labelTable = page.getByTestId('agent-labels-table-scroll')
  await expect.poll(() => labelTable.evaluate((element) => element.scrollWidth > element.clientWidth)).toBeTruthy()
  await page.mouse.wheel(0, 10000)
  await expect.poll(async () => (await page.getByTestId('settings-section-tabs').boundingBox())!.y).toBeCloseTo(0, 0)
  await page.getByRole('tab', { name: '关于与账户', exact: true }).click()
  await expect(page.getByTestId('settings-about-account')).toBeVisible()
})
