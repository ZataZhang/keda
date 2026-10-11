/**
 * rv-1 真实入口验证：真实 `kc console` + 真实 fixture 仓库，零 mock。
 *
 * 与 smoke/backlog-prd-progress.spec.ts 的分工：那份用 `page.route` 顶替读端点以稳定
 * 回归前端渲染；**本 spec 不打任何拦截**，页面走真 HTTP 端点，后端读的是
 * `rv1_fixture.py` 在 `$KCFX_ROOT` 下建好的真实 git 仓库、真实工作区与真实证据目录。
 * PRD §7.6 rv-1 的 forbidden_bypasses 只认这一种保真度。
 *
 * 四态各存一张一级 PNG 到交付证据目录，并转存浏览器实际拿到的 `/progress`、
 * `/evidence` 响应原文作为 provenance。
 *
 * Run with（见 scripts/rv1_real_entry.sh 的一键封装）:
 *   KCFX_ROOT=/tmp/keda-prd-progress-rv1 PLAYWRIGHT_SKIP_STACK_BOOT=1 \
 *   PLAYWRIGHT_STACK_MODE=dev PLAYWRIGHT_BASE_URL=http://127.0.0.1:8391 \
 *   npm run test:no-auth -- backlog-prd-progress-real-entry
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

/** 隔离 fixture 根目录；未设置时整个 spec 跳过（CI 无该环境）。 */
const kcfxCwd = process.env.KCFX_ROOT?.trim() ?? ''
/** 交付证据目录：截图与端点响应原文都写这里的一级路径。 */
const evidenceDirPath = resolve(
  import.meta.dirname,
  '../../../../tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals',
)

const REPO_RUNNING_ID = 'progress-fixture'
const REPO_NOBASE_ID = 'nobase-fixture'
const PRD_RUNNING_PATH = 'tasks/pending/P1-FEAT-20261011-000000-progress-running.md'
const PRD_ARCHIVED_PATH = 'tasks/archive/P1-FEAT-20261011-000001-progress-archived.md'
const PRD_NO_PLAN_PATH = 'tasks/pending/P1-FEAT-20261011-000002-progress-no-plan.md'
const PRD_NO_BASE_PATH = 'tasks/pending/P1-FEAT-20261011-000003-progress-no-base.md'

/**
 * 把文本写入交付证据目录（自动建目录）。
 *
 * @param fileName - 一级文件名。
 * @param text - 文件内容。
 * @returns 写入完成的 Promise。
 */
async function saveEvidenceText(fileName: string, text: string): Promise<void> {
  await mkdir(evidenceDirPath, { recursive: true })
  await writeFile(resolve(evidenceDirPath, fileName), text, 'utf-8')
}

/**
 * 按前端 `encodePrdPath` 的同一口径把 PRD 路径编码为 URL-safe base64。
 *
 * 必须保留 padding：后端用 `base64.urlsafe_b64decode` 解码，去掉 `=` 会对
 * 长度不是 3 的倍数的路径（例如归档路径）直接 400。
 *
 * @param prdPath - PRD 的仓库相对路径。
 * @returns 带 padding 的 URL-safe base64 字符串。
 */
function encodePrdPath(prdPath: string): string {
  return Buffer.from(prdPath, 'utf-8')
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
}

/**
 * 读取真实 `/progress` 端点响应原文并转存，用于比对页面读数与端点读数一致。
 *
 * @param page - Playwright 页面对象。
 * @param repoId - 目标仓库 ID。
 * @param prdPath - PRD 仓库相对路径。
 * @param evidencePrefix - 转存文件名前缀（`rv-<item>-…`）。
 * @returns 端点响应对象。
 */
async function fetchProgressPayload(
  page: Page,
  repoId: string,
  prdPath: string,
  evidencePrefix: string,
) {
  const encodedPath = encodePrdPath(prdPath)
  const response = await page.request.get(
    `/api/v1/agent-runner/backlog/prds/${encodedPath}/progress?repo_id=${encodeURIComponent(repoId)}`,
  )
  expect(response.ok(), `progress 端点非 200：${await response.text()}`).toBe(true)
  const payloadText = await response.text()
  await saveEvidenceText(`${evidencePrefix}-progress-endpoint.json`, payloadText + '\n')
  return JSON.parse(payloadText) as Record<string, unknown>
}

/**
 * 打开 Backlog 页并切到「列表」视图（默认是依赖图，列表里才有 PRD 卡片）。
 *
 * @param page - Playwright 页面对象。
 * @param options - 是否需要把已归档 PRD 纳入列表。
 * @returns 切好视图的 Promise。
 */
async function openBacklogList(page: Page, options: { includeArchived: boolean }): Promise<void> {
  await page.goto('/app/backlog')
  await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible({ timeout: 30_000 })
  if (options.includeArchived) {
    await page.getByRole('checkbox', { name: '显示已归档' }).check()
  }
  await page.getByRole('button', { name: /视图：/ }).click()
  await page.getByRole('menuitemradio', { name: '列表' }).click()
}

/**
 * 选中受管理仓库栏里的某个仓库。
 *
 * @param page - Playwright 页面对象。
 * @param repoDisplayName - 仓库展示名（fixture 里是 rv1-fixture / rv1-nobase）。
 * @returns 选中完成的 Promise。
 */
async function selectRepository(page: Page, repoDisplayName: string): Promise<void> {
  await page.getByRole('button', { name: repoDisplayName, exact: true }).click()
}

/**
 * 等待列表快照包含目标 PRD 并打开其详情面板。
 *
 * 列表读的是本地快照，冷启动首个请求会返回空骨架并在后台重扫，因此这里轮询而不是
 * 假定一次就有数据。
 *
 * @param page - Playwright 页面对象。
 * @param prdPath - 目标 PRD 的仓库相对路径。
 * @returns 详情面板可见后的 Promise。
 */
async function openPrdDetail(page: Page, prdPath: string): Promise<void> {
  const openButton = page.locator(`[data-testid="prd-open-content"][data-prd-path="${prdPath}"]`)
  await expect(openButton).toBeVisible({ timeout: 30_000 })
  await openButton.click()
  await expect(page.getByTestId('prd-detail')).toBeVisible()
}

/**
 * 断言详情头部三读数 chip 的字面文案。
 *
 * @param page - Playwright 页面对象。
 * @param expected - 期望的清单 / 影响树 / 证据文案。
 * @returns 断言通过的 Promise。
 */
async function expectReadingChips(
  page: Page,
  expected: { checklist: string; impact: string; evidence: string },
): Promise<void> {
  await expect(page.getByTestId('prd-progress-checklist')).toHaveText(expected.checklist, {
    timeout: 30_000,
  })
  await expect(page.getByTestId('prd-progress-impact')).toHaveText(expected.impact)
  await expect(page.getByTestId('prd-progress-evidence')).toHaveText(expected.evidence)
}

/**
 * 切到「验收证据」标签。
 *
 * @param page - Playwright 页面对象。
 * @returns 标签内容可见后的 Promise。
 */
async function openEvidenceTab(page: Page): Promise<void> {
  await page.getByTestId('prd-detail-tab-evidence').click()
  await expect(page.getByTestId('prd-evidence-body')).toBeVisible()
}

test.describe('真实入口：PRD 交付进度四态', () => {
  test.describe.configure({ mode: 'serial' })
  test.skip(kcfxCwd === '', '需要 KCFX_ROOT 指向 rv1_fixture.py 生成的隔离 fixture')

  test('隔离检查：console 只登记 fixture 仓库', async ({ page }) => {
    const response = await page.request.get('/api/v1/agent-runner/repositories')
    expect(response.ok()).toBe(true)
    const payload = (await response.json()) as { repositories: { repo_id: string }[] }
    expect(payload.repositories.map((item) => item.repo_id).sort()).toEqual([
      REPO_NOBASE_ID,
      REPO_RUNNING_ID,
    ])
  })

  test('运行中：分支优先读数、计划分组与未合并徽标', async ({ page }) => {
    await openBacklogList(page, { includeArchived: true })
    await selectRepository(page, 'rv1-fixture')
    await openPrdDetail(page, PRD_RUNNING_PATH)

    await expectReadingChips(page, { checklist: '3/12', impact: '~7/12 ?2', evidence: '2/3' })
    // 弱信号口径必须随读数一起呈现：三个 chip 各自的 title 提示都要写明「弱信号」。
    await expect(
      page.locator('[data-testid="prd-progress-strip"] span[title*="弱信号"]'),
    ).toHaveCount(3)
    await page.screenshot({ path: resolve(evidenceDirPath, 'rv-1-running-detail.png'), fullPage: true })

    const progressPayload = await fetchProgressPayload(page, REPO_RUNNING_ID, PRD_RUNNING_PATH, 'rv-1-running')
    expect(progressPayload.prd_source).toBe('worktree')
    expect(progressPayload.worktree_status).toBe('matched')

    await openEvidenceTab(page)
    await expect(page.getByText('计划 3 项 · 已产出 2 项（共 4 个文件）')).toBeVisible()
    await expect(page.getByTestId('prd-evidence-unmerged-rv-1-running-detail.png')).toBeVisible()
    await expect(page.getByTestId('prd-evidence-unmerged-rv-3-cli-parity.txt')).toBeVisible()
    await expect(page.getByText('该 PRD 未声明验证计划')).toHaveCount(0)

    // fresh-state 探针：改工作区副本的勾选数 → 重新走一遍真实入口 → 读数必须变（证明
    // /progress 现读磁盘、不是列表快照的缓存值），随后恢复原状并复核回到 3/12。
    const worktreePrdPath = resolve(
      kcfxCwd,
      'worktrees/progress-fixture/progress-running',
      PRD_RUNNING_PATH,
    )
    const originalText = await readFile(worktreePrdPath, 'utf-8')
    await writeFile(
      worktreePrdPath,
      originalText.replace('- [x] 验收项 3', '- [ ] 验收项 3'),
      'utf-8',
    )
    try {
      await openBacklogList(page, { includeArchived: true })
      await selectRepository(page, 'rv1-fixture')
      await openPrdDetail(page, PRD_RUNNING_PATH)
      await expect(page.getByTestId('prd-progress-checklist')).toHaveText('2/12', { timeout: 30_000 })
    } finally {
      await writeFile(worktreePrdPath, originalText, 'utf-8')
    }
    await openBacklogList(page, { includeArchived: true })
    await selectRepository(page, 'rv1-fixture')
    await openPrdDetail(page, PRD_RUNNING_PATH)
    await expect(page.getByTestId('prd-progress-checklist')).toHaveText('3/12', { timeout: 30_000 })
  })

  test('已归档：主仓库终态压过残留工作区', async ({ page }) => {
    await openBacklogList(page, { includeArchived: true })
    await selectRepository(page, 'rv1-fixture')
    await openPrdDetail(page, PRD_ARCHIVED_PATH)

    await expectReadingChips(page, { checklist: '12/12', impact: '—', evidence: '3/3' })
    await page.screenshot({
      path: resolve(evidenceDirPath, 'rv-1-archived-detail.png'),
      fullPage: true,
    })

    const progressPayload = await fetchProgressPayload(page, REPO_RUNNING_ID, PRD_ARCHIVED_PATH, 'rv-1-archived')
    expect(progressPayload.prd_source).toBe('main')
    expect(progressPayload.worktree_status).toBe('not_applicable')
    expect(progressPayload.impact_status).toBe('terminal_archive')
  })

  test('无计划旧 PRD：证据显示 - 但文件数为 2', async ({ page }) => {
    await openBacklogList(page, { includeArchived: true })
    await selectRepository(page, 'rv1-fixture')
    await openPrdDetail(page, PRD_NO_PLAN_PATH)

    await expectReadingChips(page, { checklist: '5/12', impact: '—', evidence: '—' })
    await page.screenshot({
      path: resolve(evidenceDirPath, 'rv-1-degraded-no-plan.png'),
      fullPage: true,
    })

    const progressPayload = await fetchProgressPayload(page, REPO_RUNNING_ID, PRD_NO_PLAN_PATH, 'rv-1-noplan')
    expect(progressPayload.worktree_status).toBe('none')
    await openEvidenceTab(page)
    await expect(page.getByText('该 PRD 未声明验证计划')).toBeVisible()
    await expect(page.getByText('共 2 个文件')).toBeVisible()
  })

  test('无基准：影响树 - 而非 ~0/12', async ({ page }) => {
    await openBacklogList(page, { includeArchived: false })
    await selectRepository(page, 'rv1-nobase')
    await openPrdDetail(page, PRD_NO_BASE_PATH)

    await expectReadingChips(page, { checklist: '4/12', impact: '—', evidence: '1/3' })
    await page.screenshot({
      path: resolve(evidenceDirPath, 'rv-1-no-base-detail.png'),
      fullPage: true,
    })

    const progressPayload = await fetchProgressPayload(page, REPO_NOBASE_ID, PRD_NO_BASE_PATH, 'rv-1-nobase')
    expect(progressPayload.impact_status).toBe('no_base')
    expect(progressPayload.impact).toBeNull()
  })
})
