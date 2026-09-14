/**
 * Realistic validation for the PRD full-text reader (PRD rv-2).
 *
 * 验证层级：**真实入口**。列表与原文都走真实后端与真实 `tasks/` 数据，PRD 原文
 * 接口不做任何 stub；判据取自磁盘上该 PRD 文件的首行一级标题。
 *
 * 唯一注入的失败是第三个用例：用 `page.route` 中断原文请求，用来验证错误态
 * （验证层级：`error-state injection`，界面真实、失败注入）。
 */

import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import { expect, test } from '../../fixtures/session.fixture'
import { getApiBaseUrl } from '../../support/env'

type RegistryRepositoryEntry = {
  repo_id: string
  path: string
  enabled: boolean
  display_name: string | null
  path_exists: boolean
}

type RoadmapPrdEntry = {
  prd_path: string
  title: string
  status: string
}

const TARGET_REPO_ID = 'keda-main'

/**
 * 从 Markdown 原文里取出首个一级标题，作为与界面 H1 比对的判据。
 *
 * @param markdownText - PRD 文件原文。
 * @returns 首个一级标题文本。
 */
function extractFirstHeading(markdownText: string): string {
  for (const rawLine of markdownText.split('\n')) {
    const headingMatch = /^#\s+(.*\S)\s*$/.exec(rawLine.trim())
    if (headingMatch) {
      return headingMatch[1]
    }
  }
  throw new Error('PRD 文件中没有一级标题，无法作为比对判据。')
}

/**
 * 读取真实后端返回的仓库 registry，拿到后端眼中的仓库根目录。
 *
 * 后端与测试进程不一定指向同一个 checkout，因此磁盘比对必须以 registry 里的
 * `path` 为准，而不是测试文件自身推断出的仓库根。
 *
 * @param requestContext - Playwright 的页面级请求上下文。
 * @returns 目标仓库在后端视角下的绝对路径。
 */
async function resolveBackendRepositoryPath(
  requestContext: import('@playwright/test').APIRequestContext,
): Promise<string> {
  const registryResponse = await requestContext.get(
    `${getApiBaseUrl()}/api/v1/agent-runner/repositories`,
  )
  expect(registryResponse.ok()).toBeTruthy()
  const registryPayload = (await registryResponse.json()) as {
    repositories: RegistryRepositoryEntry[]
  }
  const targetEntry = registryPayload.repositories.find(
    (entry) => entry.repo_id === TARGET_REPO_ID,
  )
  expect(targetEntry, `registry 中缺少 ${TARGET_REPO_ID}`).toBeTruthy()
  return targetEntry!.path
}

/**
 * 取真实后端列表里第一条 pending PRD。
 *
 * @param requestContext - Playwright 的页面级请求上下文。
 * @returns 被选中的 PRD 条目。
 */
async function fetchFirstPendingPrd(
  requestContext: import('@playwright/test').APIRequestContext,
): Promise<RoadmapPrdEntry> {
  const listResponse = await requestContext.get(
    `${getApiBaseUrl()}/api/v1/agent-runner/roadmap/prds?repo_id=${TARGET_REPO_ID}`,
  )
  expect(listResponse.ok()).toBeTruthy()
  const listPayload = (await listResponse.json()) as { prds: RoadmapPrdEntry[] }
  const pendingPrd = listPayload.prds.find((prd) => prd.status === 'pending')
  expect(pendingPrd, '真实列表里没有 pending PRD').toBeTruthy()
  return pendingPrd!
}

test.describe('realistic: PRD content reader', () => {
  test('E2E-PRD-CONTENT-1 详情视图渲染的 H1 与磁盘文件首行标题一致', async ({ page }) => {
    const backendRepositoryPath = await resolveBackendRepositoryPath(page.request)
    const targetPrd = await fetchFirstPendingPrd(page.request)

    const diskMarkdownText = await readFile(
      resolve(backendRepositoryPath, targetPrd.prd_path),
      'utf-8',
    )
    const diskHeading = extractFirstHeading(diskMarkdownText)

    await page.goto('/app/roadmap')
    await expect(page.getByRole('heading', { name: '路线图' })).toBeVisible()

    const openContentButton = page.locator(
      `[data-testid="prd-open-content"][data-prd-path="${targetPrd.prd_path}"]`,
    )
    await expect(openContentButton).toBeVisible()
    await openContentButton.click()

    const contentBody = page.getByTestId('prd-content-body')
    await expect(contentBody).toBeVisible()
    // 个别 PRD 正文里还有别的一级标题，因此固定取渲染出的第一个 h1 与磁盘首个标题比对。
    await expect(contentBody.getByRole('heading', { level: 1 }).first()).toHaveText(diskHeading)

    // 表格与验收清单的渲染形态也要在真实视图里出现（PRD §1 行为样例）。
    await expect(contentBody.locator('table').first()).toBeVisible()
    await expect(contentBody.locator('input[type="checkbox"]').first()).toBeVisible()

    // 返回列表导航必须保留。
    await page.getByTestId('prd-content-back').click()
    await expect(page.getByTestId('prd-content-body')).not.toBeVisible()
    await expect(openContentButton).toBeVisible()
  })

  test('E2E-PRD-CONTENT-2 接口原文与磁盘文件逐字节一致', async ({ page }) => {
    const backendRepositoryPath = await resolveBackendRepositoryPath(page.request)
    const targetPrd = await fetchFirstPendingPrd(page.request)
    const encodedPath = Buffer.from(targetPrd.prd_path, 'utf-8')
      .toString('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')

    const contentResponse = await page.request.get(
      `${getApiBaseUrl()}/api/v1/agent-runner/roadmap/prds/${encodedPath}/content?repo_id=${TARGET_REPO_ID}`,
    )

    expect(contentResponse.status()).toBe(200)
    const diskBytes = await readFile(resolve(backendRepositoryPath, targetPrd.prd_path))
    expect(Buffer.from(await contentResponse.body()).equals(diskBytes)).toBeTruthy()
  })

  test('E2E-PRD-CONTENT-3 原文接口不可达时显示错误态而非白屏', async ({ page }) => {
    const targetPrd = await fetchFirstPendingPrd(page.request)

    // 注入失败：只中断原文接口，其余请求（列表、设置）仍走真实后端。
    await page.route('**/roadmap/prds/*/content*', async (route) => {
      await route.abort('connectionrefused')
    })

    await page.goto('/app/roadmap')
    await expect(page.getByRole('heading', { name: '路线图' })).toBeVisible()
    await page
      .locator(`[data-testid="prd-open-content"][data-prd-path="${targetPrd.prd_path}"]`)
      .click()

    const errorState = page.getByTestId('prd-content-error')
    await expect(errorState).toBeVisible()
    await expect(errorState).toContainText('读取 PRD 原文失败')
    await expect(page.getByTestId('prd-content-loading')).not.toBeVisible()
  })
})
