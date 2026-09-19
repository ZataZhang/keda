import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// 原型底图采集：把真实 frontend-public 页面按固定画布截图，供
// docs/prototypes/lifecycle-agent-matrix.html 作为「真实截图 + HTML 覆盖层」
// 原型的底图使用。带 @visual 标签，默认 `pnpm test`（--grep-invert @visual）
// 不跑；需要重采时执行 `just e2e @visual`。
//
// 画布尺寸必须与原型 HTML 中的 stage 尺寸一致，改动时两边同步。

const currentDirectoryPath = dirname(fileURLToPath(import.meta.url))
const outputDirectoryPath = resolve(
  currentDirectoryPath,
  '../../../../docs/prototypes/assets/lifecycle-agent-matrix'
)

const VIEWPORT = { width: 1440, height: 1200 }
const LIFECYCLE_PRD_TITLE = '生命周期 Agent 矩阵'

/** Roadmap 依赖图中本原型使用的样例 PRD 卡片。 */
function lifecyclePrdCard(page: import('@playwright/test').Page) {
  return page.locator('[data-testid="prd-open-content"]').filter({ hasText: LIFECYCLE_PRD_TITLE })
}

test.describe('@visual 生命周期 Agent 矩阵原型底图', () => {
  test.use({ viewport: VIEWPORT, deviceScaleFactor: 2 })

  test.beforeAll(() => {
    mkdirSync(outputDirectoryPath, { recursive: true })
  })

  test('采集设置页底图', async ({ page }) => {
    await page.goto('/app/settings/')
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()
    await expect(page.getByText('关于 iar 管理终端')).toBeVisible()
    await page.screenshot({ path: resolve(outputDirectoryPath, 'settings-real.png') })
  })

  test('采集 Roadmap 依赖图底图', async ({ page }) => {
    await page.goto('/app/roadmap/')
    await expect(page.getByText('受管理仓库')).toBeVisible()
    await expect(lifecyclePrdCard(page)).toBeVisible({ timeout: 30_000 })
    await page.screenshot({ path: resolve(outputDirectoryPath, 'roadmap-real.png') })
  })

  test('采集 PRD 原文底图', async ({ page }) => {
    await page.goto('/app/roadmap/')
    await expect(lifecyclePrdCard(page)).toBeVisible({ timeout: 30_000 })
    await lifecyclePrdCard(page).click()
    await expect(page.getByTestId('prd-content-body')).toBeVisible()
    await expect(page.getByText('Feature Overview', { exact: false })).toBeVisible()
    await page.screenshot({ path: resolve(outputDirectoryPath, 'prd-content-real.png') })
  })
})
