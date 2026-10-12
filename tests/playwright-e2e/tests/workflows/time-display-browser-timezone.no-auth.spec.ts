/**
 * Web 时间标签跟随浏览器时区（Issue #239 的真实入口验证）。
 *
 * 验证层级：**真实页面入口 + Backlog API stub**。Chromium 浏览器、Next.js 生产路由
 * `/app/backlog`、PRD 详情抽屉与「执行过程 / CI/CD」两个标签全部真实运行；只有
 * `/api/v1/agent-runner/backlog/**` 的读响应与本地会话（`/api/auth/me`，受保护子树的
 * 路由守卫需要）被替换成确定性 fixture。原因是被验证的对象是
 * 「同一个 UTC 瞬间在不同浏览器时区下的换算」，源时间必须是钉死的瞬间——真实 GitHub 同步
 * 时间每次运行都在漂移，无法做三时区对照；换算路径本身（组件 + `lib/utils` + 浏览器
 * 时区）没有被替换。
 *
 * 覆盖 Issue #239 明令的三件事：
 * 1. 同一瞬间在 UTC / Asia/Shanghai / America/New_York（夏令时区）下的标签；
 * 2. 跨日进位（同一瞬间在 New_York 落到前一天）；
 * 3. 夏令时回拨造成同一当地小时出现两次是合法行为，两个真实瞬间渲染成同一串墙钟；
 * 4. 全程无 hydration 报错（时间文本只在客户端数据到达后渲染）。
 *
 * 证据产物（每时区一张整页截图 + 判定明细 JSON）仅在设置 `TZ_EVIDENCE_DIR` 时写出，
 * 避免 CI 每次运行都产生二进制制品。
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import { devices, expect, test } from '@playwright/test'
import type { BrowserContext, Page, Route } from '@playwright/test'

const REPO_ID = 'keda-main'
const PRD_PATH = 'tasks/pending/P2-FEAT-20261012-000000-timezone-fixture.md'
const PRD_TITLE = '时区显示验证（E2E Fixture）'
const ENCODED_PRD_PATH = Buffer.from(PRD_PATH, 'utf-8')
  .toString('base64')
  .replace(/\+/g, '-')
  .replace(/\//g, '_')

/** 待换算的 UTC 瞬间：CI 交付卡的「最近同步」。 */
const SYNCED_INSTANT = '2026-06-23T00:00:00+00:00'
/** 夏令时回拨前后的两个真实瞬间（America/New_York 当地 01:30 出现两次）。 */
const FALLBACK_FIRST_INSTANT = '2026-11-01T05:30:00+00:00'
const FALLBACK_SECOND_INSTANT = '2026-11-01T06:30:00+00:00'

type TimezoneCase = {
  /** Playwright `timezoneId`，同时也是断言用的浏览器时区。 */
  timezoneId: string
  /** 三个瞬间在该时区下应当显示的墙钟文本（`yyyy-MM-dd HH:mm:ss`）。 */
  syncedLabel: string
  /** 页头「数据截至」走 `formatLocalClockTime`（只有时分秒），同一瞬间的本地读数。 */
  scannedClockLabel: string
  fallbackFirstLabel: string
  fallbackSecondLabel: string
  /** 该时区是否会因为这次回拨出现重复小时。 */
  duplicateHourExpected: boolean
}

const TIMEZONE_CASES: TimezoneCase[] = [
  {
    timezoneId: 'UTC',
    syncedLabel: '2026-06-23 00:00:00',
    scannedClockLabel: '00:00:00',
    fallbackFirstLabel: '2026-11-01 05:30:00',
    fallbackSecondLabel: '2026-11-01 06:30:00',
    duplicateHourExpected: false,
  },
  {
    timezoneId: 'Asia/Shanghai',
    // 东八区固定 +08:00：不随夏令时漂移，两个瞬间仍是不同的当地小时。
    syncedLabel: '2026-06-23 08:00:00',
    scannedClockLabel: '08:00:00',
    fallbackFirstLabel: '2026-11-01 13:30:00',
    fallbackSecondLabel: '2026-11-01 14:30:00',
    duplicateHourExpected: false,
  },
  {
    timezoneId: 'America/New_York',
    // 6 月为 EDT（-04:00）：同一 UTC 瞬间跨到前一天 20:00。
    syncedLabel: '2026-06-22 20:00:00',
    scannedClockLabel: '20:00:00',
    // 11-01 回拨后两个瞬间都落在当地 01:30 —— 合法重复小时，不是换算错误。
    fallbackFirstLabel: '2026-11-01 01:30:00',
    fallbackSecondLabel: '2026-11-01 01:30:00',
    duplicateHourExpected: true,
  },
]

function fulfillJson(route: Route, payload: unknown): void {
  void route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

/** 会话 stub：`/app/**` 是受保护子树，路由守卫拿不到会话会把页面踢回 `/login`。 */
const LOCAL_SESSION = {
  user_id: 'local-operator',
  display_name: 'tester',
  email: 'tester@localhost',
}

/** 时间线两行事件，occurred_at 分别是回拨前后的两个真实瞬间。 */
function buildLifecycleDetail() {
  return {
    repo_id: REPO_ID,
    prd_path: PRD_PATH,
    run_id: `${REPO_ID}#239`,
    issue_number: 239,
    trigger: 'manual',
    current_phase: 'executing',
    in_progress: true,
    outcome: null,
    history_complete: true,
    started_at: FALLBACK_FIRST_INSTANT,
    finished_at: null,
    durations: {
      end_to_end_seconds: 3600.0,
      active_seconds: 1200.0,
      waiting_seconds: 1800.0,
      blocked_seconds: 600.0,
    },
    events: [
      {
        event_type: 'started',
        phase: 'executing',
        status: 'started',
        actor: 'runner',
        occurred_at: FALLBACK_FIRST_INSTANT,
        detail: {},
      },
      {
        event_type: 'recovered',
        phase: 'executing',
        status: 'recovered',
        actor: 'runner',
        occurred_at: FALLBACK_SECOND_INSTANT,
        detail: { agent: 'codex', attempt_number: 2 },
      },
    ],
    has_data: true,
  }
}

function buildCiDelivery() {
  return {
    prd_path: PRD_PATH,
    issue_number: 239,
    status: 'success',
    checks_state: 'SUCCESS',
    checks_summary: [],
    pr_url: 'https://github.com/example/repo/pull/239',
    head_sha: 'abc123def456',
    round_count: 1,
    max_rounds: 2,
    problems: [],
    stored_policy: 'inherit',
    global_enabled: false,
    effective_enabled: false,
    exhausted: false,
    exhausted_reason: null,
    last_synced_at: SYNCED_INSTANT,
  }
}

/**
 * 挂载本用例需要的 Backlog 读端点 stub。
 *
 * @param page - 目标页面（已按 timezoneId 创建的 context 里）。
 */
async function mockBacklogApi(page: Page): Promise<void> {
  const lifecycleDetail = buildLifecycleDetail()
  await page.route('**/api/auth/me', (route) => fulfillJson(route, LOCAL_SESSION))
  await page.route('**/api/v1/agent-runner/repositories', (route) =>
    fulfillJson(route, {
      repositories: [{ repo_id: REPO_ID, display_name: 'Keda Main', enabled: true, path_exists: true }],
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/settings*', (route) =>
    fulfillJson(route, {
      repo_id: REPO_ID,
      max_parallel: 2,
      default_view: 'list',
      updated_at: SYNCED_INSTANT,
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/autopilot*', (route) =>
    fulfillJson(route, {
      repo_id: REPO_ID,
      enabled: false,
      auto_merge_enabled: false,
      daemon_running: false,
      max_parallel: 2,
      config_source: '.kedacode.toml',
      persisted_enabled: false,
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/ci-repair-global*', (route) =>
    fulfillJson(route, { repo_id: REPO_ID, global_enabled: false, max_rounds: 2 }),
  )
  await page.route('**/api/v1/agent-runner/backlog/prds?**', (route) =>
    fulfillJson(route, {
      prds: [
        {
          prd_path: PRD_PATH,
          title: PRD_TITLE,
          status: 'pending',
          priority: 'P2',
          issue_url: 'https://github.com/example/repo/issues/239',
          issue_number: 239,
          state: 'running',
          acceptance_total: 1,
          acceptance_checked: 1,
          delivery_dependencies: [],
          updated_at: SYNCED_INSTANT,
          block_reason: null,
          next_action: null,
        },
      ],
      skipped: [],
      repo_id: REPO_ID,
      include_archived: false,
      scanned_at: SYNCED_INSTANT,
    }),
  )
  await page.route(`**/api/v1/agent-runner/backlog/prds/${ENCODED_PRD_PATH}/ci*`, (route) =>
    fulfillJson(route, buildCiDelivery()),
  )
  await page.route('**/api/v1/agent-runner/backlog/prds/*/lifecycle*', (route) =>
    fulfillJson(route, lifecycleDetail),
  )
  await page.route('**/api/v1/agent-runner/backlog/prds/*/content*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: '# Fixture PRD\n' }),
  )
  // 详情页其余读端点也必须 stub：漏出去会代理到本机 8000 端口并返回 500，把与
  // 时区无关的资源错误混进「无 hydration / 运行时错误」的判定里。
  await page.route('**/api/v1/agent-runner/console/version', (route) =>
    fulfillJson(route, { version: '0.0.0-e2e' }),
  )
  await page.route('**/api/v1/agent-runner/console/context', (route) =>
    fulfillJson(route, {
      cwd: '/srv/keda',
      git_root: '/srv/keda',
      repo_id: REPO_ID,
      status: 'matched',
      candidates: [],
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/prds/*/progress*', (route) =>
    fulfillJson(route, {
      prd_path: PRD_PATH,
      prd_stem: 'P2-FEAT-20261012-000000-timezone-fixture',
      prd_source: 'main',
      worktree_status: 'none',
      worktree: { branch: null },
      checklist: { status: 'available', checked: 1, total: 1 },
      evidence: {
        planned: [],
        produced_ids: [],
        produced_planned_count: 0,
        file_count: 0,
        attribution_source: 'none',
        status: 'complete',
        reports: {
          verification_plan: false,
          evidence_report: false,
          verifier_report: false,
        },
      },
      impact_status: 'no_worktree',
      impact: null,
    }),
  )
  await page.route('**/api/v1/agent-runner/backlog/prds/*/evidence*', (route) =>
    fulfillJson(route, {
      prd_path: PRD_PATH,
      prd_stem: 'P2-FEAT-20261012-000000-timezone-fixture',
      evidence_dir: 'tasks/evidence/issue-239-web-timezone',
      exists: false,
      files: [],
    }),
  )
}

/** 打开 PRD 详情抽屉（与真实用户路径一致：列表卡片 → 原文按钮）。 */
async function openPrdDetail(page: Page): Promise<void> {
  await page.goto('/app/backlog')
  await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
  await page.getByText(PRD_TITLE).first().click()
  await expect(page.getByTestId('prd-detail')).toBeVisible()
}

/**
 * 收集 hydration / 运行时错误：React 的 hydration 失败以 console.error 落地，
 * 未捕获异常以 pageerror 落地，两者都必须为空。
 *
 * @param page - 目标页面。
 * @returns 错误文本列表（边跑边填充）。
 */
function collectErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(message.text())
    }
  })
  page.on('pageerror', (error) => errors.push(String(error)))
  return errors
}

/** 证据文件名主干：IANA 时区名里的 `/` 不能进路径（Asia/Shanghai 会写成子目录）。 */
function evidenceFileStem(caseData: TimezoneCase): string {
  return `browser-timezone-${caseData.timezoneId.replace(/[^A-Za-z0-9._-]/g, '_')}`
}

async function writeEvidence(caseData: TimezoneCase, record: unknown): Promise<void> {
  const evidenceDirectoryPath = process.env.TZ_EVIDENCE_DIR
  if (!evidenceDirectoryPath) {
    return
  }
  const targetDirectoryPath = resolve(evidenceDirectoryPath)
  await mkdir(targetDirectoryPath, { recursive: true })
  await writeFile(
    resolve(targetDirectoryPath, `${evidenceFileStem(caseData)}.json`),
    JSON.stringify(record, null, 2) + '\n',
    'utf-8',
  )
}

/**
 * 证据截图：仅在设置 `TZ_EVIDENCE_DIR` 时落盘（CI 每次跑都产二进制会挤爆制品配额）。
 *
 * @param page - 目标页面。
 * @param caseData - 当前时区用例，用于命名。
 * @param tabName - 被截图的详情标签名。
 * @returns 无（截图写入证据目录）。
 */
async function captureEvidenceScreenshot(
  page: Page,
  caseData: TimezoneCase,
  tabName: string,
): Promise<void> {
  const evidenceDirectoryPath = process.env.TZ_EVIDENCE_DIR
  if (!evidenceDirectoryPath) {
    return
  }
  const targetDirectoryPath = resolve(evidenceDirectoryPath)
  await mkdir(targetDirectoryPath, { recursive: true })
  await page.screenshot({
    path: resolve(targetDirectoryPath, `${evidenceFileStem(caseData)}-${tabName}.png`),
    fullPage: true,
  })
}

for (const caseData of TIMEZONE_CASES) {
  test(`同一瞬间在 ${caseData.timezoneId} 浏览器时区下按本地渲染`, async ({ browser }) => {
    const context: BrowserContext = await browser.newContext({
      ...devices['Desktop Chrome'],
      timezoneId: caseData.timezoneId,
      viewport: { width: 1440, height: 1200 },
    })
    const page = await context.newPage()
    const pageErrors = collectErrors(page)
    await mockBacklogApi(page)

    // 先证明 override 真的生效：页面内解析出的时区必须就是本用例声明的时区。
    const resolvedTimezone = await page.evaluate(
      () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    )
    expect(resolvedTimezone).toBe(caseData.timezoneId)

    await openPrdDetail(page)

    // 1) CI/CD 标签：项目生成的 last_synced_at（UTC）→ 浏览器本地墙钟。
    await page.getByTestId('prd-detail-tab-ci-delivery').click()
    const ciView = page.getByTestId('prd-ci-view')
    await expect(ciView).toBeVisible()
    const syncedSpan = ciView.getByText('最近同步')
    await expect(syncedSpan).toContainText(`最近同步 ${caseData.syncedLabel}`)
    // 页头的「数据截至」是同一瞬间经 formatLocalClockTime 的另一条本地渲染路径。
    await expect(page.getByText(/^数据截至/)).toContainText(
      `数据截至 ${caseData.scannedClockLabel}`,
    )
    // 标签文本就地取：切到下一个标签后面板会卸载，不能再 await 同一个 locator。
    const observedSyncedText = await syncedSpan.innerText()
    const observedScannedClockText = await page.getByText(/^数据截至/).innerText()
    await captureEvidenceScreenshot(page, caseData, 'ci-delivery')

    // 2) 执行过程标签：回拨前后两个真实瞬间各自的时间线行。
    await page.getByTestId('prd-detail-tab-lifecycle').click()
    const lifecycleView = page.getByTestId('prd-lifecycle-view')
    await expect(lifecycleView).toBeVisible()
    const firstRow = page.getByTestId('prd-lifecycle-event-started')
    const secondRow = page.getByTestId('prd-lifecycle-event-recovered')
    await expect(firstRow.locator('time')).toHaveText(caseData.fallbackFirstLabel)
    await expect(secondRow.locator('time')).toHaveText(caseData.fallbackSecondLabel)
    const observedFirstText = await firstRow.locator('time').innerText()
    const observedSecondText = await secondRow.locator('time').innerText()
    await captureEvidenceScreenshot(page, caseData, 'lifecycle')

    if (caseData.duplicateHourExpected) {
      // 合法重复小时：两行墙钟相同，但底层是两个不同的 UTC 瞬间。
      expect(caseData.fallbackFirstLabel).toBe(caseData.fallbackSecondLabel)
      expect(FALLBACK_FIRST_INSTANT).not.toBe(FALLBACK_SECOND_INSTANT)
    }

    // 3) 机器契约没有被展示层污染：页面任何角落都不得出现 UTC 原文串。
    const bodyText = await page.locator('body').innerText()
    expect(bodyText).not.toContain(SYNCED_INSTANT)
    expect(bodyText).not.toContain('2026-06-23T00:00:00')

    // 4) 无 hydration / 运行时错误，也没有漏 stub 造成的资源报错。
    expect(pageErrors).toEqual([])

    await writeEvidence(caseData, {
      verification_level: 'real page entry (Next.js /app/backlog) + Backlog API stub',
      timezone_id: caseData.timezoneId,
      resolved_timezone_in_page: resolvedTimezone,
      instants: {
        last_synced_at: SYNCED_INSTANT,
        fallback_first: FALLBACK_FIRST_INSTANT,
        fallback_second: FALLBACK_SECOND_INSTANT,
      },
      expected_labels: {
        synced: caseData.syncedLabel,
        scanned_clock: caseData.scannedClockLabel,
        fallback_first: caseData.fallbackFirstLabel,
        fallback_second: caseData.fallbackSecondLabel,
      },
      observed_labels: {
        synced: observedSyncedText,
        scanned_clock: observedScannedClockText,
        fallback_first: observedFirstText,
        fallback_second: observedSecondText,
      },
      duplicate_hour_expected: caseData.duplicateHourExpected,
      console_or_page_errors: pageErrors,
    })

    await context.close()
  })
}
