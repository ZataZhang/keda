import { test as setup, expect } from '@playwright/test'
import { ensureAuthDirectory, getAuthStorageStatePath } from '../../support/env'

/**
 * Auth setup step — runs once before all 'chromium' project tests.
 *
 * The backend uses local single-operator auth: any visitor is treated as the
 * local operator. We navigate to the dashboard, wait for it to render, and
 * persist the storage state so subsequent tests can reuse the session.
 */
setup('authenticate and persist storage state', async ({ page }) => {
  ensureAuthDirectory()

  // 控制台真实路由带 `app/` 段（见 frontend-public/components/layout/app-sidebar.tsx
  // 的 href），无前缀的 `/dashboard` 在静态导出与 dev server 上都是 404。
  await page.goto('/app/dashboard')
  await expect(page.getByRole('main')).toBeVisible()
  await expect(page).toHaveURL(/\/app\/dashboard/)

  await page.context().storageState({ path: getAuthStorageStatePath() })
})
