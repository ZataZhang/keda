/**
 * Realistic validation tests for the backlog page.
 *
 * These tests exercise the full frontend path against the real backend, but
 * route the backlog read endpoint to deterministic data so the suite is stable
 * in CI. The data shape mirrors the real backend response and the repository
 * contains real test PRDs (P0-TEST-20260614-backlog-e2e-*) linked to real
 * GitHub issues (#78 review, #73 merged) for manual/sandbox validation.
 * Evidence is saved to `.iar/evidence/`.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '../../fixtures/session.fixture'
import type { Page } from '@playwright/test'

const currentDirectoryPath = dirname(fileURLToPath(import.meta.url))
const repositoryRootPath = resolve(currentDirectoryPath, '../../../..')
const evidenceDirectoryPath = resolve(repositoryRootPath, '.iar/evidence')

async function ensureEvidenceDirectory(): Promise<void> {
  await mkdir(evidenceDirectoryPath, { recursive: true })
}

async function saveJsonEvidence(filename: string, payload: unknown): Promise<void> {
  await ensureEvidenceDirectory()
  const filePath = resolve(evidenceDirectoryPath, filename)
  await writeFile(filePath, JSON.stringify(payload, null, 2) + '\n', 'utf-8')
}

async function saveScreenshot(page: import('@playwright/test').Page, filename: string): Promise<void> {
  await ensureEvidenceDirectory()
  const filePath = resolve(evidenceDirectoryPath, filename)
  await page.screenshot({ path: filePath, fullPage: true })
}

/**
 * 把验收证据截图写到 PRD 证据目录（`RV_EVIDENCE_DIR` 指定时）。
 *
 * 未设置该环境变量时静默跳过：普通 `just e2e smoke` 运行不产出 RV 证据，
 * 只有 rv-7 证据采集脚本显式注入目录时才落盘。
 *
 * @param page - Playwright 页面对象。
 * @param filename - 截图文件名（如 `rv-7-stale.png`）。
 */
async function saveRvEvidenceScreenshot(page: Page, filename: string): Promise<void> {
  const rvDirectoryPath = process.env.RV_EVIDENCE_DIR
  if (!rvDirectoryPath) {
    return
  }
  await mkdir(rvDirectoryPath, { recursive: true })
  await page.screenshot({ path: resolve(rvDirectoryPath, filename), fullPage: true })
}

async function waitForPrdCards(page: import('@playwright/test').Page): Promise<void> {
  await page.waitForSelector('[data-slot="card"]', { timeout: 15_000 })
}

/**
 * 切换到列表视图：页面默认视图已改为依赖图，卡片类断言需要先切到列表。
 *
 * @param page - Playwright 页面对象。
 */
async function switchToListView(page: Page): Promise<void> {
  await page.getByRole('button', { name: /视图：/ }).click()
  await page.getByRole('menuitemradio', { name: '列表' }).click()
}

const MOCK_PRDS_RESPONSE = {
  prds: [
    {
      prd_path: 'tasks/pending/P0-TEST-20260614-backlog-e2e-review.md',
      title: 'Backlog E2E Review Highlight Test',
      status: 'pending',
      priority: 'P0',
      issue_url: 'https://github.com/zata-zhangtao/keda/issues/78',
      issue_number: 78,
      state: 'review',
      acceptance_total: 1,
      acceptance_checked: 1,
      delivery_dependencies: [],
      updated_at: '2026-06-14T00:00:00+00:00',
      block_reason: null,
      next_action: { label: '去审阅 PR', url: 'https://github.com/zata-zhangtao/keda/pull/80' },
    },
    {
      prd_path: 'tasks/pending/P0-TEST-20260614-backlog-e2e-merged.md',
      title: 'Backlog E2E Merged Highlight Test',
      status: 'pending',
      priority: 'P0',
      issue_url: 'https://github.com/zata-zhangtao/keda/issues/73',
      issue_number: 73,
      state: 'merged',
      acceptance_total: 1,
      acceptance_checked: 1,
      delivery_dependencies: [],
      updated_at: '2026-06-14T00:00:00+00:00',
      block_reason: null,
      next_action: { label: '开始下一个', url: null },
    },
    {
      prd_path: 'tasks/pending/P1-FEAT-20260612120000-prd-iar-init-check-gate.md', // legacy-alias
      title: 'iar 命令仓库初始化门禁', // legacy-alias（历史归档 PRD 的真实标题）
      status: 'pending',
      priority: 'P1',
      issue_url: null,
      issue_number: null,
      state: 'not_started',
      acceptance_total: 0,
      acceptance_checked: 0,
      delivery_dependencies: [],
      updated_at: '2026-06-12T09:50:32+00:00',
      block_reason: null,
      next_action: { label: '开始', url: null },
    },
    {
      prd_path: 'tasks/archive/P1-FEAT-20260610-114529-issue-dependency-gate.md',
      title: 'Issue Dependency Gate',
      status: 'archived',
      priority: 'P1',
      issue_url: null,
      issue_number: null,
      state: 'archived',
      acceptance_total: 1,
      acceptance_checked: 1,
      delivery_dependencies: [],
      updated_at: '2026-06-10T00:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
  ],
  repo_id: 'keda-main',
  include_archived: true,
  scanned_at: '2026-06-14T00:00:00+00:00',
  stale: false,
}

test.describe('realistic: backlog page', () => {
  test.beforeEach(async ({ page, api }) => {
    await page.route('/api/v1/agent-runner/backlog/prds*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PRDS_RESPONSE),
      })
    })

    await page.route('/api/v1/agent-runner/backlog/settings*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          repo_id: 'keda-main',
          max_parallel: 2,
          default_view: 'list',
          updated_at: '2026-06-14T00:00:00+00:00',
        }),
      })
    })

    // Keep the authenticated API client warm so the fixture stays alive.
    await api.get('/api/auth/me')
  })

  test('E2E-1 backlog renders pending PRDs and archived switch works', async ({ page }) => {
    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await switchToListView(page)
    await waitForPrdCards(page)

    await saveJsonEvidence('backlog-prds-response.json', MOCK_PRDS_RESPONSE)
    await saveScreenshot(page, 'backlog-list.png')

    await expect(page.getByText('Backlog E2E Review Highlight Test')).toBeVisible()
    await expect(page.getByText('iar 命令仓库初始化门禁')).toBeVisible() // legacy-alias
    await expect(page.getByText('Issue Dependency Gate')).not.toBeVisible()

    await page.getByLabel('显示已归档').check()
    await waitForPrdCards(page)
    await expect(page.getByText('Issue Dependency Gate')).toBeVisible()
  })

  test('E2E-1 list sorting and timeline view', async ({ page }) => {
    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await switchToListView(page)
    await waitForPrdCards(page)

    await page.getByTestId('backlog-sort-trigger').click()
    await page.getByRole('menuitemradio', { name: '按状态' }).click()
    await page.waitForTimeout(300)
    await saveScreenshot(page, 'backlog-list-sorted.png')

    await page.getByRole('button', { name: /视图：/ }).click()
    await page.getByRole('menuitemradio', { name: '时间轴' }).click()
    await waitForPrdCards(page)
    await expect(page.getByText(/^阶段 /)).toBeVisible()
    await saveScreenshot(page, 'backlog-timeline.png')
  })

  test('E2E-5 review and merged highlight cards', async ({ page }) => {
    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await switchToListView(page)
    await waitForPrdCards(page)

    await expect(page.getByText('Backlog E2E Review Highlight Test')).toBeVisible()
    await expect(page.getByRole('link', { name: '去审阅 PR' })).toBeVisible()
    await saveScreenshot(page, 'backlog-review-highlight.png')

    await expect(page.getByText('Backlog E2E Merged Highlight Test')).toBeVisible()
    await expect(page.getByText('开始下一个')).toBeVisible()
    await saveScreenshot(page, 'backlog-merged-highlight.png')
  })

  // ── rv-7：快照新鲜度契约（stale 短轮询自动追平 / 无快照「正在同步」空态）──

  test('E2E-6 stale snapshot shows syncing hint and short polling auto-advances to fresh', async ({
    page,
  }) => {
    const staleResponse = { ...MOCK_PRDS_RESPONSE, include_archived: false, stale: true }
    const freshArrivalPrd = {
      ...MOCK_PRDS_RESPONSE.prds[0],
      prd_path: 'tasks/pending/P2-FEAT-20260614-backlog-e2e-fresh-arrival.md',
      title: 'Backlog E2E Fresh Arrival PRD',
    }
    const freshResponse = {
      ...staleResponse,
      prds: [...staleResponse.prds, freshArrivalPrd],
      scanned_at: '2026-06-14T00:05:00+00:00',
      stale: false,
    }
    const requestTimestamps: number[] = []
    let responseSeq = 0
    await page.route('/api/v1/agent-runner/backlog/prds*', async (route) => {
      requestTimestamps.push(Date.now())
      responseSeq += 1
      const nextBody = responseSeq === 1 ? staleResponse : freshResponse
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(nextBody),
      })
    })

    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()

    // stale 态：如实显示数据截至时间，并追加「后台更新中」提示
    await expect(page.getByText(/数据截至/)).toBeVisible()
    await expect(page.getByText(/后台更新中/)).toBeVisible()
    await saveRvEvidenceScreenshot(page, 'rv-7-stale.png')
    await expect(page.getByText('Backlog E2E Fresh Arrival PRD')).not.toBeVisible()

    // stale 时按 3 秒短节奏追平：第二次列表请求必须在 5 秒内发出。
    // 实现若仍是固定 30 秒轮询（改动前的 setInterval 节奏），这条断言必红。
    await expect
      .poll(() => requestTimestamps.length >= 2, { timeout: 6_000 })
      .toBe(true)
    expect(requestTimestamps[1] - requestTimestamps[0]).toBeLessThanOrEqual(5_000)

    // fresh 态：提示消失、列表反映新扫描结果、表头仅保留数据截至时间
    await expect(page.getByText('Backlog E2E Fresh Arrival PRD')).toBeVisible()
    await expect(page.getByText(/后台更新中/)).not.toBeVisible()
    await expect(page.getByText(/数据截至/)).toBeVisible()
    await saveRvEvidenceScreenshot(page, 'rv-7-fresh.png')
  })

  test('E2E-7 first visit without snapshot shows syncing empty state without error', async ({
    page,
  }) => {
    await page.route('/api/v1/agent-runner/backlog/prds*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          prds: [],
          skipped: [],
          repo_id: 'keda-main',
          include_archived: false,
          scanned_at: null,
          stale: true,
        }),
      })
    })

    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
    await expect(page.getByText('正在同步').first()).toBeVisible()
    await expect(page.getByText('加载 Backlog 失败')).not.toBeVisible()
    await saveRvEvidenceScreenshot(page, 'rv-7-no-snapshot.png')
  })
})
