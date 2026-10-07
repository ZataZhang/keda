/**
 * 真实入口取证：对着真的 `iar console` 服务截图「首屏默认仓库」。
 *
 * 与 smoke/console-landing-repository.spec.ts 的 mock 版不同，这里不打任何
 * API 拦截——首屏选中哪个仓库完全由后端 `GET /console/context` 读到的进程
 * cwd 决定。截图写到仓库根的 `.iar/evidence/`（该目录被 gitignore）。
 *
 * Run with:
 *   PLAYWRIGHT_SKIP_STACK_BOOT=1 PLAYWRIGHT_STACK_MODE=dev \
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:8765 \
 *   npm run test:no-auth -- console-current-repo-real-entry
 */
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// tests/playwright-e2e/tests/workflows → 上溯四级到仓库根的 .iar/evidence。
const EVIDENCE_DIR = resolve(import.meta.dirname, '../../../../.iar/evidence')

/**
 * 读取 Backlog 页左侧「受管理仓库」栏当前高亮的仓库名。
 *
 * @param page - Playwright 页面对象。
 * @returns 高亮项的文本；没有高亮项时返回空字符串。
 */
async function readHighlightedRepo(page: Page): Promise<string> {
  const highlighted = page.locator('aside button.bg-slate-100').first()
  if ((await highlighted.count()) === 0) {
    return ''
  }
  return ((await highlighted.innerText()) ?? '').trim()
}

test.describe('真实入口：console 首屏落在当前仓库', () => {
  test('首屏选中 console 进程 cwd 对应的仓库并留存截图', async ({ page }) => {
    await mkdir(EVIDENCE_DIR, { recursive: true })

    const context = await page.request.get('/api/v1/agent-runner/console/context')
    expect(context.ok()).toBe(true)
    const payload = await context.json()

    await page.goto('/')
    // 冷启动的 console 首个请求要扫 GitHub，首屏渲染可能超过默认 5s 断言窗口。
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('button', { name: '依赖图' })).toBeVisible({ timeout: 30_000 })

    // 首屏高亮项必须就是后端按 cwd 推断出的那个仓库。仓库列表是异步拉的，
    // 轮询等它落到高亮态而不是只等一次。
    await expect
      .poll(() => readHighlightedRepo(page), { timeout: 30_000 })
      .toBe(payload.repo_id)
    await page.screenshot({
      path: resolve(EVIDENCE_DIR, 'console-landing-current-repo.png'),
      fullPage: true,
    })

    // eslint-disable-next-line no-console
    console.log(
      `cwd=${payload.cwd} repo_id=${payload.repo_id} status=${payload.status}`,
    )
  })
})
