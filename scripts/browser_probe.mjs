/** 最小浏览器探针：验证 Playwright Chromium 能否在本环境启动。 */
import { createRequire } from 'node:module'

const e2eRequire = createRequire(
  '/Users/zata/code/keda/.iar-worktrees/issue-263/tests/playwright-e2e/package.json',
)
const { chromium } = e2eRequire('@playwright/test')

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage()
await page.goto('about:blank')
await page.setContent('<h1 id="probe-ok">probe-ok</h1>')
const text = await page.locator('#probe-ok').textContent()
console.log(`PROBE_RESULT=${text}`)
await browser.close()
