/**
 * `iar console` 静态托管冒烟（真实入口，不做 API mock）。
 *
 * 对着一个由 `iar console` 起的本机服务（FastAPI + 内置静态导出产物）
 * 直接访问深层路由，验证静态导出目录形态被 StaticFiles(html=True)
 * 正确解析：刷新 /app/roadmap、/app/stats 不 404，根路径落到 Dashboard。
 *
 * Run with (PRD rv-5):
 *   PLAYWRIGHT_SKIP_STACK_BOOT=1 PLAYWRIGHT_STACK_MODE=dev \
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:8765 \
 *   npm run test:no-auth -- console-served-static
 */
import { expect, test } from '@playwright/test'

test.describe('iar console 静态托管（真实入口）', () => {
  test('根路径打开后落到 Dashboard', async ({ page }) => {
    const response = await page.goto('/')
    expect(response?.status()).toBe(200)
    await expect(
      page.getByRole('heading', { name: 'Agent Runner 管理终端' }),
    ).toBeVisible()
  })

  test('直接刷新 /app/roadmap 不 404 且渲染标题', async ({ page }) => {
    const response = await page.goto('/app/roadmap/')
    expect(response?.status()).toBe(200)
    await expect(page.getByRole('heading', { name: '路线图' })).toBeVisible()
    await expect(page.getByText('404')).toHaveCount(0)
  })

  test('直接刷新 /app/stats 不 404 且渲染完成度统计', async ({ page }) => {
    const response = await page.goto('/app/stats/')
    expect(response?.status()).toBe(200)
    await expect(page.getByRole('heading', { name: '完成度统计' })).toBeVisible()
    await expect(page.getByText('404')).toHaveCount(0)
  })
})
