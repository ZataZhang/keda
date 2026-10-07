/**
 * `iar console` 首屏落点与默认仓库（mocked API）。
 *
 * 验证两件事：
 * 1. 根路径 `/` 落到 Backlog 而不是 Dashboard —— Backlog 是「当前仓库的 PRD
 *    队列」，面板的默认工作视角。
 * 2. 首屏选中的仓库来自 console 进程 cwd（`GET /console/context`），而不是
 *    registry 声明顺序最靠前的那个；cwd 匹配不上时才回退到上次手动选择的记忆。
 *
 * 用 mock 而不是真实后端：真实 context 端点读的是跑 e2e 那份 stack 的进程 cwd，
 * 不可控；这里要断言的正是「前端拿到 repo_id 后如何选」的优先级逻辑。
 *
 * 直接用 `@playwright/test` 而非 session fixture：console 的认证是本机单用户
 * 空实现（任何访问者都是 local operator），不需要注入凭证。
 */

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

/** registry 列表：`alpha` 故意排在前面，用来证明「首个 enabled」不是正确答案。 */
const REPOSITORIES = [
  { repo_id: 'alpha', path: '/tmp/alpha', enabled: true, display_name: 'Alpha', path_exists: true },
  { repo_id: 'beta', path: '/tmp/beta', enabled: true, display_name: 'Beta', path_exists: true },
  { repo_id: 'gamma', path: '/tmp/gamma', enabled: false, display_name: 'Gamma', path_exists: true },
]

/**
 * 拦截 registry 列表、console 上下文与 Backlog 读接口。
 *
 * @param page - Playwright 页面对象。
 * @param contextRepoId - 模拟 console 进程 cwd 匹配到的仓库 id；null 表示未匹配上。
 * @returns 记录 Backlog 读请求实际使用的 repo_id 的数组引用。
 */
async function mockConsoleApi(page: Page, contextRepoId: string | null) {
  const requestedRepoIds: string[] = []

  await page.route('/api/v1/agent-runner/repositories', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ repositories: REPOSITORIES }),
    }),
  )

  await page.route('/api/v1/agent-runner/console/context', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        cwd: '/tmp/beta',
        git_root: contextRepoId ? '/tmp/beta' : null,
        repo_id: contextRepoId,
        status: contextRepoId ? 'matched' : 'not_git_repo',
        candidates: [],
      }),
    }),
  )

  await page.route('/api/v1/agent-runner/backlog/prds**', (route) => {
    const url = new URL(route.request().url())
    requestedRepoIds.push(url.searchParams.get('repo_id') ?? '')
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        prds: [],
        repo_id: url.searchParams.get('repo_id'),
        include_archived: false,
        scanned_at: '2026-10-07T00:00:00+00:00',
      }),
    })
  })

  await page.route('/api/v1/agent-runner/backlog/settings**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repo_id: contextRepoId ?? 'alpha',
        max_parallel: 2,
        default_view: 'graph',
        updated_at: '2026-10-07T00:00:00+00:00',
      }),
    }),
  )

  await page.route('/api/v1/agent-runner/backlog/autopilot**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repo_id: contextRepoId ?? 'alpha',
        enabled: false,
        updated_at: '2026-10-07T00:00:00+00:00',
      }),
    }),
  )

  await page.route('/api/v1/agent-runner/backlog/ci-repair-global**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repo_id: contextRepoId ?? 'alpha',
        value: 'inherit',
        updated_at: '2026-10-07T00:00:00+00:00',
      }),
    }),
  )

  return requestedRepoIds
}

test.describe('console 首屏落点与默认仓库', () => {
  test.beforeEach(async ({ page }) => {
    // 每个用例从干净记忆起步，否则 localStorage 会跨用例泄漏。
    await page.addInitScript(() => window.localStorage.clear())
  })

  test('根路径落到 Backlog 而不是 Dashboard', async ({ page }) => {
    await mockConsoleApi(page, 'beta')

    await page.goto('/')

    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Agent Runner 管理终端' })).toHaveCount(0)
  })

  test('首屏选中 cwd 对应的仓库，而不是 registry 首个 enabled', async ({ page }) => {
    const requestedRepoIds = await mockConsoleApi(page, 'beta')

    await page.goto('/app/backlog')

    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    // `beta` 在列表里排第二；选中它才说明用的是 cwd 上下文而非列表顺序。
    await expect(page.getByRole('button', { name: 'Beta', exact: true })).toHaveClass(/bg-slate-100/)
    await expect(requestedRepoIds[0]).toBe('beta')
  })

  test('cwd 匹配不上时回退到上次手动选择的仓库', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('iar.console.lastRepoId', 'alpha')
    })
    const requestedRepoIds = await mockConsoleApi(page, null)

    await page.goto('/app/backlog')

    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await expect(requestedRepoIds[0]).toBe('alpha')
  })

  test('手动切换仓库后刷新仍停在该仓库', async ({ page }) => {
    await mockConsoleApi(page, null)

    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await page.getByRole('button', { name: 'Beta', exact: true }).click()

    await expect(page.getByRole('button', { name: 'Beta', exact: true })).toHaveClass(/bg-slate-100/)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Beta', exact: true })).toHaveClass(/bg-slate-100/)
  })
})
