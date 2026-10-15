/**
 * 值守看板与桌面小窗的前端回归（mocked API）。
 *
 * 分工：`workflows/*-real-entry.no-auth.spec.ts` 走真实后端与真实入口交付验收证据；
 * **本 spec 用 `page.route` 顶替读端点**，只为稳定锁住前端渲染契约——默认视图是任务看板、
 * 状态文字与颜色同现、筛选/搜索可用、结构化调用默认折叠、来源不可用如实披露。
 * 这里断言的都不是真实运行数据。
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
  status: 'running',
  status_reason: 'agent/running',
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

const BLOCKED_TASK = {
  repo_id: 'keda-main',
  issue_number: 19,
  task_key: 'keda-main#19',
  repo_display_name: 'Keda Main',
  issue_title: 'Broken Issue',
  issue_url: 'https://example.test/19',
  status: 'blocked',
  status_reason: '检测到异常：label_pr_mismatch',
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
    tasks: [RUNNING_TASK, BLOCKED_TASK],
    status_counts: {
      running: 1,
      waiting: 0,
      blocked: 1,
      completed: 0,
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
  await page.route('**/api/v1/agent-runner/issues/*/logs*', (route) =>
    route.fulfill({ json: { lines: [], next_offset: 0, eof: true } }),
  )
}

test.describe('runtime board (mocked API)', () => {
  test('default view is the task board with readable status labels', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await expect(page.getByTestId('runtime-task-list')).toBeVisible()
    await expect(
      page.getByTestId('runtime-task-row-keda-main-21'),
    ).toContainText('运行中')
    await expect(
      page.getByTestId('runtime-task-row-keda-main-19'),
    ).toContainText('阻塞')
    // 默认视图不再铺仓库卡片：仓库级动作收在「仓库监控」一侧。
    await expect(page.getByTestId('dashboard-repo-header')).toHaveCount(0)
  })

  test('status filter narrows rows to the chosen bucket', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await page.getByTestId('runtime-filter-blocked').click()
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

  test('structured tool calls are collapsed by default and load on expand', async ({
    page,
  }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    await page.getByTestId('runtime-task-row-keda-main-19').click()
    const toggle = page.getByTestId('runtime-invocation-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('runtime-invocation-panel')).toHaveCount(0)

    await toggle.click()
    await expect(page.getByTestId('runtime-invocation-panel')).toBeVisible()
    await expect(page.getByTestId('runtime-invocation-row')).toHaveCount(1)
  })

  test('unreadable PRD link says so instead of drawing an empty bar', async ({ page }) => {
    await mockBoardApi(page)
    await page.goto('/app/dashboard')

    // 自动选中的是无 PRD 的阻塞任务以外的第一条：这里显式点未关联 PRD 的行。
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
          completed: 0,
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
        hint: '桌面运行时未安装：安装可选依赖 `kedacode[desktop]` 后可开启小窗，网页看板不受影响。',
      },
    })
    await page.goto('/app/dashboard')

    await expect(page.getByTestId('dashboard-companion-hint')).toContainText('桌面运行时未安装')
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
          completed: 0,
          unknown: 0,
        },
      },
    })
    await page.goto('/app/companion')

    await expect(page.getByTestId('desktop-companion')).toBeVisible()
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
