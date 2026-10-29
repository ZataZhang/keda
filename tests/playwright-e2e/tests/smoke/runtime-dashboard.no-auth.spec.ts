/**
 * 值守看板与桌面小窗的前端回归（mocked API）。
 *
 * 分工：`workflows/*-real-entry.no-auth.spec.ts` 走真实后端与真实入口交付验收证据；
 * **本 spec 用 `page.route` 顶替读端点**，只为稳定锁住前端渲染契约——默认视图是任务看板、
 * 状态文字与颜色同现、筛选/搜索可用、结构化调用默认展开、来源不可用如实披露。
 * 验证层级：production composition；从 /app/dashboard 与 /app/companion 真实页面入口操作，
 * 保留生产布局、主题、Dialog/Portal 与筛选交互，仅替换后端读响应和本地会话。
 * Chromium，默认 1440×1200 浅色，另覆盖 440×900 深色与 380×760 小窗、长文本和历史筛选。
 * 截图断言活动默认、历史缺失阶段降噪与详情边界；这里断言的都不是真实运行数据。
 */
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const TASKS_ENDPOINT = '**/api/v1/agent-runner/console/runtime/tasks'
const COMPANION_STATUS_ENDPOINT =
  '**/api/v1/agent-runner/console/desktop-companion/status'
const PROGRESS_ENDPOINT =
  '**/api/v1/agent-runner/backlog/prds/*/progress'
const ATTEMPTS_ENDPOINT = '**/api/v1/agent-runner/console/runs/attempts**'

const RUNNING_TASK = {
  repo_id: 'keda-main',
  issue_number: 21,
  task_key: 'keda-main#21',
  repo_display_name: 'Keda Main',
  issue_title: 'Running Issue',
  issue_url: 'https://example.test/21',
  pr_url: 'https://example.test/pr/21',
  pr_number: 21,
  status: 'running',
  status_reason: '当前监控标签：agent/running',
  anomaly_types: [],
  phase: 'implement',
  agent: 'codex',
  preset: 'default',
  model: 'gpt-5.1-codex',
  attempt_number: 1,
  prd_path: 'tasks/pending/P1-FEAT-20260611-000000-demo.md',
  started_at: '2026-06-11T09:35:00+00:00',
  finished_at: null,
  duration_seconds: 1800,
  last_outcome: null,
  labels: ['agent/running'],
}

const FAILED_TASK = {
  repo_id: 'keda-main',
  issue_number: 19,
  task_key: 'keda-main#19',
  repo_display_name: 'Keda Main',
  issue_title: 'Broken Issue',
  issue_url: 'https://example.test/19',
  status: 'failed',
  status_reason: '当前监控标签：agent/failed',
  anomaly_types: ['label_pr_mismatch'],
  phase: 'publish',
  agent: 'claude',
  preset: null,
  model: null,
  attempt_number: 2,
  prd_path: null,
  started_at: '2026-06-11T08:00:00+00:00',
  finished_at: '2026-06-11T08:10:00+00:00',
  duration_seconds: 600,
  last_outcome: 'failed',
  labels: ['agent/failed'],
}

/** 组装任务摘要响应体；默认两个分桶各一条，便于同时验证筛选与计数。 */
function tasksPayload(overrides: Record<string, unknown> = {}) {
  return {
    generated_at: '2026-06-11T10:05:00+00:00',
    tasks: [RUNNING_TASK, FAILED_TASK],
    status_counts: {
      running: 1,
      waiting: 0,
      blocked: 0,
      failed: 1,
      completed: 0,
      closed: 0,
      unknown: 0,
    },
    unavailable_sources: [],
    missing_repo_ids: [],
    sync_status: 'ready',
    scanned_at: '2026-06-11T10:00:00+00:00',
    truncated: false,
    ...overrides,
  }
}

/** 组装 companion 快照响应体；hint 与后端映射表同源，这里按状态给出手工副本。 */
function companionPayload(overrides: Record<string, unknown> = {}) {
  return {
    status: 'idle',
    console_origin: 'http://127.0.0.1:8765',
    console_session: 'fixture-session',
    focus_ticket: 0,
    close_ticket: 0,
    detail: null,
    hint: '桌面小窗未开启。',
    window_exists: false,
    companion_url: null,
    ...overrides,
  }
}

/**
 * 顶替看板与小窗的全部读端点。
 *
 * @param page - Playwright 页面对象。
 * @param options.tasks - 任务摘要覆盖项。
 * @param options.companion - companion 快照覆盖项。
 * @param options.progress - PRD progress 响应；不传则返回不可比较的终态。
 * @returns 路由注册完成的 Promise。
 */
async function mockBoardApi(
  page: Page,
  options: {
    tasks?: Record<string, unknown>
    companion?: Record<string, unknown>
    progress?: Record<string, unknown>
  } = {},
): Promise<void> {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { user_id: 'local-operator', display_name: 'operator', email: 'operator@localhost' },
    }),
  )
  await page.route(TASKS_ENDPOINT, (route) =>
    route.fulfill({ json: tasksPayload(options.tasks) }),
  )
  await page.route(COMPANION_STATUS_ENDPOINT, (route) =>
    route.fulfill({ json: companionPayload(options.companion) }),
  )
  await page.route(PROGRESS_ENDPOINT, (route) =>
    route.fulfill({
      json:
        options.progress ??
        {
          prd_path: RUNNING_TASK.prd_path,
          worktree_status: 'not_applicable',
          impact_status: 'terminal_archive',
          impact: null,
        },
    }),
  )
  await page.route(ATTEMPTS_ENDPOINT, (route) =>
    route.fulfill({
      json: {
        attempts: [],
        invocations: [
          {
            invocation_id: 'inv-1',
            phase: 'implement',
            role: 'executor',
            executor: 'codex',
            outcome: 'completed',
            duration_seconds: 120,
            started_at: '2026-06-11T08:01:00+00:00',
            finished_at: '2026-06-11T08:03:00+00:00',
          },
        ],
      },
    }),
  )
  // 小窗页面复用的日志端点：给一个空窗口，避免真实后端被打穿。
  await page.route('**/api/v1/agent-runner/console/repositories/*/issues/*/logs*', (route) =>
    route.fulfill({ json: { status: 'no_attempt', content: '', next_offset: 0, attempt_id: null, truncated: false } }),
  )
}

test.describe('runtime board (mocked API)', () => {
  test('desktop sidebar stays visible while the long task list scrolls', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    const longTasks = Array.from({ length: 40 }, (_, index) => ({
      ...RUNNING_TASK, issue_number: 500 + index, task_key: `keda-main#${500 + index}`,
      issue_title: `Long list task ${index + 1}`,
    }))
    await mockBoardApi(page, { tasks: { tasks: longTasks } })
    await page.goto('/app/dashboard')
    await expect(page.getByTestId(/runtime-task-row-keda-main-/)).toHaveCount(40)
    const sidebar = page.getByRole('complementary')
    const main = page.getByRole('main')
    const sidebarBefore = await sidebar.boundingBox()
    await main.hover()
    await page.mouse.wheel(0, 10000)
    await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeGreaterThan(1000)
    await expect(page.getByTestId('runtime-task-row-keda-main-539')).toBeInViewport()
    await expect(sidebar.getByRole('link', { name: 'Dashboard', exact: true })).toBeInViewport()
    await expect(sidebar.getByRole('link', { name: 'Settings', exact: true })).toBeInViewport()
    const sidebarAfter = await sidebar.boundingBox()
    expect(sidebarAfter!.y).toBe(sidebarBefore!.y)
    expect(sidebarAfter!.height).toBe(720)
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
    await page.screenshot({ path: '/tmp/keda-sidebar-long-list-fixed.png', animations: 'disabled' })

    await sidebar.getByRole('button', { name: '收起导航栏', exact: true }).click()
    await expect(sidebar).toHaveCSS('width', '56px')
    await expect(sidebar.getByRole('link', { name: 'Settings', exact: true })).toBeInViewport()
    await expect(page.getByTestId('runtime-task-row-keda-main-539')).toBeInViewport()
    await page.screenshot({ path: '/tmp/keda-sidebar-collapsed-fixed.png', animations: 'disabled' })
  })

  test('short desktop windows scroll only the sidebar menu and keep its controls visible', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 320 })
    await mockBoardApi(page)
    await page.goto('/app/dashboard')
    const sidebar = page.getByRole('complementary')
    const menu = sidebar.getByRole('navigation')
    const main = page.getByRole('main')
    await expect(menu).toHaveCSS('overflow-y', 'auto')
    await menu.hover()
    await page.mouse.wheel(0, 1000)
    await expect.poll(() => menu.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
    await expect(sidebar.getByRole('link', { name: 'Settings', exact: true })).toBeInViewport()
    await expect(sidebar.getByRole('button', { name: '选择配色风格', exact: true })).toBeInViewport()
    await expect(sidebar.getByRole('button', { name: '收起导航栏', exact: true })).toBeInViewport()
    expect(await main.evaluate((element) => element.scrollTop)).toBe(0)
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
    await page.screenshot({ path: '/tmp/keda-sidebar-short-window-fixed.png', animations: 'disabled' })
  })

  test('manual refresh reconciles closed tasks before the next periodic poll', async ({ page }) => {
    await page.clock.install()
    await page.clock.pauseAt(new Date())
    await mockBoardApi(page)
    let scanCompleted = false
    let runtimeReads = 0
    await page.route(TASKS_ENDPOINT, (route) => {
      runtimeReads += 1
      return route.fulfill({ json: tasksPayload({ tasks: [{ ...FAILED_TASK,
        status: scanCompleted ? 'closed' : 'failed', phase: null }] }) })
    })
    await page.route('**/api/v1/agent-runner/overview/snapshots', (route) => route.fulfill({ json: {
      repositories: [], missing_repo_ids: [], sync_status: 'ready',
      scanned_at: scanCompleted ? '2026-10-26T02:47:54+00:00' : '2026-10-26T02:03:19+00:00',
    } }))
    await page.route('**/api/v1/agent-runner/overview/per-repo', (route) => route.fulfill({
      json: { jobs_by_repo: { 'keda-main': 'scan-closed' } },
    }))
    await page.route('**/api/v1/agent-runner/overview/jobs/scan-closed', (route) => {
      scanCompleted = true
      return route.fulfill({ json: { job_id: 'scan-closed', status: 'completed', error: null } })
    })
    await page.goto('/app/dashboard')
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toBeVisible()
    const readsBeforeRefresh = runtimeReads
    await page.getByRole('button', { name: /^刷新全部/ }).click()
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toHaveCount(0)
    await expect(page.getByTestId('runtime-filter-closed')).toContainText('1')
    expect(runtimeReads).toBe(readsBeforeRefresh + 1)
    await page.getByTestId('runtime-filter-closed').click()
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toContainText('已关闭')
  })

  test('active default keeps unknown tasks and makes history explicitly available', async ({ page }) => {
    const closed = { ...FAILED_TASK, issue_number: 18, status: 'closed', phase: null,
      status_reason: 'GitHub Issue 已关闭；关闭原因未记录', labels: ['agent/running'] }
    const completed = { ...FAILED_TASK, issue_number: 17, status: 'completed', phase: null,
      last_outcome: 'completed', status_reason: '运行账本终态：completed' }
    const unknown = { ...RUNNING_TASK, issue_number: 16, status: 'unknown', phase: 'implementation_complete',
      finished_at: '2026-06-11T10:00:00+00:00', duration_seconds: 1500, last_outcome: 'completed',
      status_reason: '监控标签仍标记运行，但最近运行已结束；当前执行状态待核实' }
    await mockBoardApi(page, { tasks: { tasks: [RUNNING_TASK, { ...FAILED_TASK, phase: 'agent' }, unknown, closed, completed] } })
    await page.goto('/app/dashboard')
    await expect(page.getByTestId('runtime-filter-active')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByTestId('runtime-filter-active')).toContainText('3')
    await expect(page.getByTestId('runtime-task-row-keda-main-18')).toHaveCount(0)
    const unknownRow = page.getByTestId('runtime-task-row-keda-main-16')
    await expect(unknownRow).toContainText('最近运行已结束')
    await expect(unknownRow).toContainText('上次运行耗时：25m')
    await expect(unknownRow).not.toContainText('接下来进入')
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).not.toContainText('正在处理')
    await page.screenshot({ path: '/tmp/keda-runtime-active-fixed.png', fullPage: true, animations: 'disabled' })

    await page.getByTestId('runtime-filter-all').click()
    await expect(page.getByTestId(/runtime-task-row-keda-main-/)).toHaveCount(5)
    const closedRow = page.getByTestId('runtime-task-row-keda-main-18')
    await expect(closedRow).toContainText('关闭原因未记录')
    await expect(closedRow).toContainText('上次运行结果：失败')
    await expect(closedRow.getByTestId('runtime-task-phase')).toHaveCount(0)
    await expect(closedRow.getByTestId('runtime-task-phase-description')).toHaveCount(0)
    await page.getByTestId('runtime-filter-closed').click()
    await expect(closedRow).toBeVisible()
    await closedRow.click()
    await expect(page.getByTestId('runtime-task-detail')).toContainText('关闭原因未记录')
    await page.screenshot({ path: '/tmp/keda-runtime-history-detail-fixed.png', fullPage: true, animations: 'disabled' })
    const sheet = page.getByTestId('runtime-task-detail-sheet')
    await expect(sheet).toBeVisible()
    const sheetBounds = await sheet.boundingBox()
    expect(sheetBounds!.x + sheetBounds!.width).toBeLessThanOrEqual(1440)
  })

  test('history-only board explains the empty active view and filters search together', async ({ page }) => {
    await mockBoardApi(page, { tasks: { tasks: [{ ...FAILED_TASK, status: 'closed', phase: null }] } })
    await page.goto('/app/dashboard')
    await expect(page.getByTestId('runtime-task-empty')).toContainText('当前没有活动任务')
    await page.getByTestId('runtime-filter-closed').click()
    await page.getByTestId('runtime-task-search').fill('Broken')
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toBeVisible()
    await page.getByTestId('runtime-task-search').fill('no-match')
    await expect(page.getByTestId('runtime-task-empty')).toContainText('没有符合筛选条件')
  })

  test('long recent phase and status remain readable on a narrow dark board', async ({ page }) => {
    await page.setViewportSize({ width: 440, height: 900 })
    await page.addInitScript(() => localStorage.setItem('theme', 'dark'))
    await mockBoardApi(page, { tasks: { tasks: [{ ...FAILED_TASK, phase: 'implementation_complete',
      issue_title: '很长的任务标题，用于验证窄屏布局中的阶段说明、状态来源与外链布局' }] } })
    await page.goto('/app/dashboard')
    const row = page.getByTestId('runtime-task-row-keda-main-19')
    await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(row.getByTestId('runtime-task-phase')).toContainText('最近阶段：实现完成')
    await expect(row).not.toContainText('接下来进入')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    const pillHeight = await page.getByTestId('runtime-task-issue-link').evaluate((link) => link.getBoundingClientRect().height)
    expect(pillHeight).toBeGreaterThan(0)
    await page.screenshot({ path: '/tmp/keda-runtime-narrow-dark-fixed.png', fullPage: true, animations: 'disabled' })
  })

  test('default view is the task board with readable status labels', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await expect(page.getByTestId('runtime-task-list')).toBeVisible()
    await expect(
      page.getByTestId('runtime-task-row-keda-main-21'),
    ).toContainText('运行中')
    await expect(
      page.getByTestId('runtime-task-row-keda-main-19'),
    ).toContainText('失败')
    // 行内直接给出 Issue / PR 入口：有 PR 时两个都在，无 PR 时只有 Issue。
    await expect(page.getByTestId('runtime-task-issue-link')).toHaveCount(2)
    await expect(page.getByTestId('runtime-task-pr-link')).toHaveCount(1)
    await expect(page.getByTestId('runtime-task-pr-link')).toHaveAttribute(
      'href',
      'https://example.test/pr/21',
    )
    const issuePill = page.getByTestId('runtime-task-issue-link').first()
    const prPill = page.getByTestId('runtime-task-pr-link')
    const issueStyle = await issuePill.evaluate((element) => ({ height: element.getBoundingClientRect().height,
      color: getComputedStyle(element).color, border: getComputedStyle(element.parentElement!).borderColor }))
    const prHeight = await prPill.evaluate((element) => element.getBoundingClientRect().height)
    expect(issueStyle.height).toBe(prHeight)
    expect(issueStyle.color).not.toBe('')
    expect(issueStyle.border).not.toBe('')
    // 默认视图不再铺仓库卡片：仓库级动作收在「仓库监控」一侧。
    await expect(page.getByTestId('dashboard-repo-header')).toHaveCount(0)
  })

  test('status filter narrows rows to the chosen bucket', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await page.getByTestId('runtime-filter-failed').click()
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toBeVisible()
    await expect(page.getByTestId('runtime-task-row-keda-main-21')).toHaveCount(0)
  })

  test('search matches issue number, repo and title', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await page.getByTestId('runtime-task-search').fill('21')
    await expect(page.getByTestId('runtime-task-row-keda-main-21')).toBeVisible()
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toHaveCount(0)

    await page.getByTestId('runtime-task-search').fill('Broken')
    await expect(page.getByTestId('runtime-task-row-keda-main-19')).toBeVisible()
    await expect(page.getByTestId('runtime-task-row-keda-main-21')).toHaveCount(0)

    await page.getByTestId('runtime-task-search').fill('nomatch')
    await expect(page.getByTestId('runtime-task-empty')).toContainText('没有符合筛选条件')
  })

  test('structured tool calls are expanded by default and load automatically', async ({
    page,
  }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await page.getByTestId('runtime-task-row-keda-main-19').click()
    const toggle = page.getByTestId('runtime-invocation-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByTestId('runtime-invocation-panel')).toBeVisible()
    await expect(page.getByTestId('runtime-invocation-row')).toHaveCount(1)
  })

  test('unreadable PRD link says so instead of drawing an empty bar', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    // 默认选中运行任务；这里显式点开无 PRD 的失败任务。
    await page.getByTestId('runtime-task-row-keda-main-19').click()
    await expect(page.getByTestId('runtime-impact-no-prd')).toContainText('未关联 PRD')
    await expect(page.getByTestId('runtime-impact-bar')).toHaveCount(0)
  })

  test('degraded sources and pending first sync are disclosed, not folded into empty', async ({
    page,
  }) => {
    await mockBoardApi(page, {
      tasks: {
        tasks: [],
        status_counts: {
          running: 0,
          waiting: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          closed: 0,
          unknown: 0,
        },
        unavailable_sources: ['run_history'],
        sync_status: 'pending_first_sync',
        scanned_at: null,
      },
    })
    await page.goto('/app/dashboard')

    await expect(page.getByTestId('runtime-degraded')).toBeVisible()
    await expect(page.getByTestId('runtime-source-partial')).toContainText('run_history')
    await expect(page.getByTestId('runtime-task-empty')).toBeVisible()
  })

  test('missing helper keeps the web board usable', async ({ page }) => {
    await mockBoardApi(page, {
      companion: {
        status: 'not_installed',
        hint: '桌面运行时缺失：源码环境执行 `uv sync` 并重启 Console，网页看板不受影响。',
      },
    })
    await page.goto('/app/dashboard')

    await expect(page.getByTestId('dashboard-companion-hint')).toContainText('桌面运行时缺失')
    await expect(page.getByTestId('runtime-task-list')).toBeVisible()
  })

  test('switching to the monitor view hides the task board', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await expect(page.getByTestId('runtime-task-list')).toBeVisible()
    await page.getByTestId('dashboard-view-monitor').click()
    // 仓库卡片这一侧读的是快照端点（本 spec 不顶替），因此只断言看板确实让位。
    await expect(page.getByTestId('runtime-task-list')).toHaveCount(0)
    await expect(page.getByTestId('dashboard-open-companion')).toHaveCount(0)
  })
})

test.describe('desktop companion page (mocked API)', () => {
  test('stale running labels remain visible as unknown in the companion', async ({ page }) => {
    await mockBoardApi(page, { tasks: { tasks: [{ ...FAILED_TASK, status: 'unknown',
      status_reason: '监控标签仍标记运行，但最近运行已结束；当前执行状态待核实' }],
      status_counts: { running: 0, waiting: 0, blocked: 0, failed: 0, completed: 0, closed: 0, unknown: 1 } } })
    await page.goto('/app/companion')
    await expect(page.getByTestId('desktop-companion-count-unknown')).toHaveText('状态未知 1')
    await expect(page.getByTestId('desktop-companion-row-keda-main-19')).toContainText('当前执行状态待核实')
  })

  test('shows readable stages beside blocked status in a narrow window', async ({ page }) => {
    await page.setViewportSize({ width: 380, height: 760 })
    const phases = ['rv_reexec', 'implementation_complete', 'pre_pr_review', null, 'future_phase']
    await mockBoardApi(page, {
      tasks: {
        tasks: phases.map((phase, index) => ({
          ...RUNNING_TASK,
          issue_number: 294 + index,
          task_key: `keda-main#${294 + index}`,
          status: 'blocked',
          status_reason: '当前监控标签：agent/blocked',
          anomaly_types: [],
          labels: ['agent/blocked'],
          phase,
          prd_path: null,
        })),
        status_counts: {
          running: 0,
          waiting: 0,
          blocked: phases.length,
          failed: 0,
          completed: 0,
          closed: 0,
          unknown: 0,
        },
      },
    })
    await page.goto('/app/companion')
    const labels = ['提交后验收重跑', '实现完成', 'PR 前审查', null, 'future_phase']
    for (const [index, label] of labels.entries()) {
      const row = page.getByTestId(`desktop-companion-row-keda-main-${294 + index}`)
      if (label) {
        await expect(row.getByTestId('runtime-task-phase')).toHaveText(`最近阶段：${label}`)
      } else {
        await expect(row.getByTestId('runtime-task-phase')).toHaveCount(0)
        await expect(row).toContainText('暂无阶段记录')
      }
      await expect(row.getByTestId('runtime-task-status-reason')).toContainText('agent/blocked')
    }
    await expect(page.getByTestId('desktop-companion-list')).toBeVisible()
    await expect(page.getByTestId('desktop-companion-count-blocked')).toHaveText('阻塞 5')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await page.screenshot({ path: '/tmp/keda-runtime-phase-narrow.png', fullPage: true })
  })

  test('lists active tasks compactly and keeps the overflow scrollable', async ({ page }) => {
    const activeTasks = [1, 2, 3, 4, 5].map((index) => ({
      ...RUNNING_TASK,
      issue_number: 100 + index,
      task_key: `keda-main#${100 + index}`,
      issue_title: `Active Issue ${index}`,
    }))
    await mockBoardApi(page, {
      tasks: {
        tasks: activeTasks,
        status_counts: {
          running: activeTasks.length,
          waiting: 0,
          blocked: 0,
          failed: 0,
          completed: 0,
          closed: 0,
          unknown: 0,
        },
      },
    })
    await page.goto('/app/companion')

    await expect(page.getByTestId('desktop-companion')).toBeVisible()
    // 计数条把溢出任务的数量提前给出，不必滚到底数行。
    await expect(page.getByTestId('desktop-companion-count-running')).toHaveText('运行中 5')
    await expect(page.getByTestId('desktop-companion-count-blocked')).toHaveCount(0)
    // 超出紧凑高度的任务不丢弃：仍在同一列表里，靠滚动看到。
    await expect(page.getByTestId(/desktop-companion-row-/)).toHaveCount(5)
    await expect(page.getByTestId('desktop-companion-list')).toHaveCSS('overflow-y', 'auto')
    await expect(page.getByTestId('desktop-companion-hidden')).toContainText('滚动')
    // 每轮只为前四项读取触达读数，第五项显式说明「本轮未取数」，不伪造比例条。
    await expect(page.getByTestId('runtime-impact-skipped')).toHaveCount(1)
  })

  test('disconnected Console tells the operator instead of showing stale rows', async ({
    page,
  }) => {
    await mockBoardApi(page, {
      tasks: {
        unavailable_sources: ['monitor_snapshot', 'run_history', 'prd_lifecycle'],
      },
    })
    await page.goto('/app/companion')

    await expect(page.getByTestId('desktop-companion-partial')).toBeVisible()
  })
})
