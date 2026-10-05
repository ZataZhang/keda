/**
 * Backlog CI/CD 交付尾段验证（mock Backlog API，不依赖真实 GitHub / runner）。
 *
 * 验证层级：**UI 真实 + Backlog API stub**。浏览器、Next.js 页面、API client
 * 与 CI/CD 控制组件全部真实运行；`/api/v1/agent-runner/backlog/**` 的后端
 * 响应被 Playwright route 替换为确定性数据。写请求（PATCH / POST）不由测试
 * 伪造成功语义之外的副作用——请求 URL 与请求体由测试捕获作为判据，响应里的
 * stored/effective 值按「服务端 fresh 计算」契约构造。
 */

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const REPO_ID = 'keda-main'
const PRD_PATH = 'tasks/pending/P1-FEAT-20260101-cicd.md'
const PRD_TITLE = 'CI/CD 监控与自动修复'

/** base64url 编码 PRD 路径，与前端 encodePrdPath 同规则。 */
function encodePrdPath(prdPath: string): string {
  return Buffer.from(prdPath, 'utf-8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_')
}

const PRD_ENTRY = {
  prd_path: PRD_PATH,
  title: PRD_TITLE,
  status: 'pending',
  priority: 'P1',
  issue_url: 'https://github.com/example/repo/issues/42',
  issue_number: 42,
  state: 'review',
  acceptance_total: 3,
  acceptance_checked: 1,
  delivery_dependencies: [],
  updated_at: '2026-10-05T00:00:00+00:00',
  block_reason: null,
  next_action: null,
}

/** 仓库全局开关状态（默认关闭，与产品默认一致）。 */
const CI_GLOBAL_OFF = { repo_id: REPO_ID, global_enabled: false, max_rounds: 2 }

/** 单 PRD ci_delivery：failure + inherit（跟随全局关闭 → effective 关闭）。 */
function buildCiDelivery(overrides: Record<string, unknown> = {}) {
  return {
    prd_path: PRD_PATH,
    issue_number: 42,
    status: 'failure',
    checks_state: 'FAILURE',
    checks_summary: ['backend / typecheck: mypy failed'],
    pr_url: 'https://github.com/example/repo/pull/7',
    head_sha: 'abc123def456',
    round_count: 1,
    max_rounds: 2,
    problems: [
      {
        name: 'backend / typecheck: mypy failed',
        summary: 'backend / typecheck: mypy failed',
        url: 'https://github.com/example/repo/pull/7',
        round_number: 1,
      },
    ],
    stored_policy: 'inherit',
    global_enabled: false,
    effective_enabled: false,
    exhausted: false,
    exhausted_reason: null,
    last_synced_at: '2026-10-05T00:00:00+00:00',
    ...overrides,
  }
}

type CapturedRequest = { url: string; body: Record<string, unknown> }

/**
 * 挂载全部 Backlog 路由 mock；返回捕获到的写请求列表。
 *
 * @param page - Playwright 页面。
 * @returns 按 URL 关键字索引的写请求捕获器。
 */
async function mockBacklogApi(page: import('@playwright/test').Page) {
  const captured: Record<string, CapturedRequest[]> = {}

  function capture(key: string, url: string, body: Record<string, unknown>) {
    captured[key] ??= []
    captured[key].push({ url, body })
  }

  await page.route('**/api/v1/agent-runner/repositories', (route) =>
    route.fulfill({
      json: {
        repositories: [
          {
            repo_id: REPO_ID,
            display_name: 'Keda Main',
            enabled: true,
            path_exists: true,
          },
        ],
      },
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/settings*', (route) =>
    route.fulfill({
      json: {
        repo_id: REPO_ID,
        max_parallel: 2,
        default_view: 'list',
        updated_at: '2026-10-05T00:00:00+00:00',
      },
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/autopilot*', (route) =>
    route.fulfill({
      json: {
        repo_id: REPO_ID,
        enabled: false,
        auto_merge_enabled: false,
        daemon_running: false,
        max_parallel: 2,
        config_source: '.iar.toml',
        persisted_enabled: false,
      },
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/prds?**', (route) =>
    route.fulfill({
      json: {
        prds: [PRD_ENTRY],
        skipped: [],
        repo_id: REPO_ID,
        include_archived: false,
        scanned_at: '2026-10-05T00:00:00+00:00',
      },
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/ci-repair-global*', (route) => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      capture('ciGlobal', route.request().url(), body)
      return route.fulfill({
        json: { repo_id: REPO_ID, global_enabled: body.enabled, max_rounds: 2 },
      })
    }
    return route.fulfill({ json: CI_GLOBAL_OFF })
  })
  const encoded = encodePrdPath(PRD_PATH)
  let currentDelivery = buildCiDelivery()
  await page.route(`**/api/v1/agent-runner/backlog/prds/${encoded}/ci*`, (route) => {
    const path = route.request().url()
    if (path.endsWith('/ci-policy') && route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      capture('ciPolicy', path, body)
      // 模拟服务端 fresh 计算：显式 on/off 优先，否则继承全局（false）。
      const effective =
        body.value === 'on' ? true : body.value === 'off' ? false : currentDelivery.global_enabled
      currentDelivery = buildCiDelivery({ stored_policy: body.value, effective_enabled: effective })
      return route.fulfill({ json: currentDelivery })
    }
    if (path.endsWith('/ci-repair') && route.request().method() === 'POST') {
      capture('ciRepair', path, (route.request().postDataJSON() ?? {}) as Record<string, unknown>)
      return route.fulfill({
        json: { requested: true, detail: '已请求一次修复（第 2 轮）。', head_sha: 'abc123def456' },
      })
    }
    return route.fulfill({ json: currentDelivery })
  })
  return captured
}

/**
 * rv-3 证据截图：真实 /app/backlog 生产边界（Backlog API 为确定性 fake，
 * GitHub 边界不可达），保存到指定绝对路径（供证据目录采集）。
 *
 * @param page - 已打开 CI/CD 标签页的页面。
 * @param fileName - 目标截图文件名。
 */
async function captureRv3Screenshot(page: Page, fileName: string): Promise<void> {
  const target = process.env.RV3_SCREENSHOT_DIR
  if (!target) {
    return
  }
  const { mkdirSync, writeFileSync } = await import('node:fs')
  const { join } = await import('node:path')
  mkdirSync(target, { recursive: true })
  const buffer = await page.screenshot({ fullPage: true, animations: 'disabled' })
  writeFileSync(join(target, fileName), buffer)
}

test.describe('backlog CI/CD delivery (mocked API)', () => {
  test('页面显示全局开关默认关闭且与 Autopilot 分开', async ({ page }) => {
    await mockBacklogApi(page)
    await page.goto('/app/backlog')
    await expect(page.getByTestId('backlog-autopilot')).toBeVisible()
    await expect(page.getByTestId('backlog-ci-repair')).toBeVisible()
    await expect(page.getByTestId('backlog-ci-repair-toggle')).not.toBeChecked()
    await expect(page.getByTestId('backlog-ci-repair')).toContainText('不启动修复 Agent')
  })

  test('打开全局开关只改 auto_repair_ci（与 Autopilot 无联动）', async ({ page }) => {
    const captured = await mockBacklogApi(page)
    await page.goto('/app/backlog')
    await page.getByTestId('backlog-ci-repair-toggle').click()
    await expect(page.getByTestId('backlog-ci-repair')).toContainText('已开启')
    expect(captured.ciGlobal).toHaveLength(1)
    expect(captured.ciGlobal[0].body).toEqual({ repo_id: REPO_ID, enabled: true })
    // Autopilot 开关保持原样，没有被联动。
    await expect(page.getByTestId('backlog-autopilot-toggle')).not.toBeChecked()
  })

  test('CI/CD tab 分开显示原始状态与未验证说明，并展示三态与生效值', async ({ page }) => {
    const captured = await mockBacklogApi(page)
    await page.goto('/app/backlog')
    // 选中 PRD → 打开右侧详情 → 切到 CI/CD 标签。
    await page.getByText(PRD_TITLE).first().click()
    await page.getByTestId('prd-detail-tab-ci-delivery').click()
    await expect(page.getByTestId('prd-ci-status')).toContainText('检查未通过')
    await expect(page.getByTestId('prd-ci-view')).toContainText('mypy failed')
    await expect(page.getByTestId('prd-ci-effective')).toContainText('当前生效：关闭')
    // 切换为「强制开启」→ PATCH 请求体正确，effective 立即反映服务端回读。
    await page.getByTestId('prd-ci-policy-on').click()
    expect(captured.ciPolicy).toHaveLength(1)
    expect(captured.ciPolicy[0].body).toEqual({ repo_id: REPO_ID, value: 'on' })
    await expect(page.getByTestId('prd-ci-effective')).toContainText('当前生效：开启')
    await captureRv3Screenshot(page, 'rv-3-backlog-ci-problems.png')
  })

  test('问题卡可显式请求一次修复（POST 单次请求）', async ({ page }) => {
    const captured = await mockBacklogApi(page)
    await page.goto('/app/backlog')
    await page.getByText(PRD_TITLE).first().click()
    await page.getByTestId('prd-detail-tab-ci-delivery').click()
    await page.getByTestId('prd-ci-manual-repair').click()
    // 等待 POST 落到 mock（请求即判据），再确认按钮恢复可交互、无错误提示。
    await expect
      .poll(() => captured.ciRepair?.length ?? 0, { timeout: 5000 })
      .toBe(1)
    expect(captured.ciRepair[0].body).toEqual({ repo_id: REPO_ID })
    await expect(page.getByTestId('prd-ci-manual-repair')).toContainText('立即修复此问题')
    await expect(page.locator('[data-sonner-toast][data-type="error"]')).toHaveCount(0)
  })
})
