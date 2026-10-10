/**
 * 生命周期时间线接入调用明细与展示去噪（Issue #273 / PRD rv-3、rv-4）。
 *
 * 与 `backlog-prd-lifecycle.spec.ts` 的分工：旧 spec 守住「响应里没有 `invocations`
 * 字段」这一兼容形态（前端必须容忍缺字段并按单层列表渲染），本 spec 守新增形态——
 * 会话分组、尝试展开调用序列、同秒去噪、三类降级披露。两者都必须保持绿色。
 *
 * 保真度层级：真实 Next.js 页面 + 真实路由 + 真实浏览器交互；只把 GitHub 与
 * 生命周期读端点的响应用确定性 fixture 顶替（PRD §7 契约，无随机数与定时器）。
 * 因此本 spec 不参与「真实 SQLite 穿越」判定（那是 rv-1 harness 的边界）。
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '../../fixtures/session.fixture'
import type { Page, Route } from '@playwright/test'

const currentDirectoryPath = dirname(fileURLToPath(import.meta.url))
const repositoryRootPath = resolve(currentDirectoryPath, '../../../..')
// 与 rv-1 harness 分目录：本 spec 的产物是浏览器流程证据（HTTP 用 fixture），
// 不能与真实 FastAPI + SQLite 的响应证据混在同一批文件里。
const evidenceDirectoryPath = resolve(
  repositoryRootPath,
  'tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/e2e',
)

const FIXTURE_PRD_PATH = 'tasks/pending/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md'
const SESSION_A_RUN_ID = 'keda-main#417#session-a'
const SESSION_B_RUN_ID = 'keda-main#417#session-b'

async function writeEvidence(filename: string, payload: unknown): Promise<void> {
  await mkdir(evidenceDirectoryPath, { recursive: true })
  await writeFile(
    resolve(evidenceDirectoryPath, filename),
    JSON.stringify(payload, null, 2) + '\n',
    'utf-8',
  )
}

async function screenshot(page: Page, filename: string): Promise<void> {
  await mkdir(evidenceDirectoryPath, { recursive: true })
  await page.screenshot({ path: resolve(evidenceDirectoryPath, filename), fullPage: true })
}

const MOCK_PRDS_RESPONSE = {
  prds: [
    {
      prd_path: FIXTURE_PRD_PATH,
      title: '生命周期时间线接入调用明细与展示去噪（E2E Fixture）',
      status: 'pending',
      priority: 'P1',
      issue_url: 'https://github.com/ZataZhang/keda/issues/273',
      issue_number: 417,
      state: 'running',
      acceptance_total: 12,
      acceptance_checked: 0,
      delivery_dependencies: [],
      updated_at: '2026-10-10T11:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
  ],
  repo_id: 'keda-main',
  include_archived: false,
  scanned_at: '2026-10-10T12:00:00+00:00',
}

/** 会话 A 的第一次尝试：一条正常退出、一条异常退出并带重试链。 */
const INVOCATION_A_IMPLEMENTATION = {
  invocation_id: 'inv-a-1-implementation',
  run_id: SESSION_A_RUN_ID,
  attempt_number: 1,
  phase: 'implementation',
  role: 'implementer',
  executor: 'codex',
  outcome: 'ok',
  failure_category: null,
  started_at: '2026-10-10T11:02:10+00:00',
  finished_at: '2026-10-10T11:04:10+00:00',
  duration_seconds: 120.0,
  requested_model: 'gpt-5-codex',
  reported_model: 'gpt-5-codex',
  model_source: 'preset',
  token_usage: { input_tokens: 4200, output_tokens: 800 },
  retry_of: null,
  retry_reason: null,
  exit_code: 0,
  log_locator: 'logs/inv-a-1-implementation.log',
}

const INVOCATION_A_VERIFICATION_FAILED = {
  invocation_id: 'inv-a-1-verification',
  run_id: SESSION_A_RUN_ID,
  attempt_number: 1,
  phase: 'verification',
  role: 'verifier',
  executor: 'codex',
  outcome: 'failed',
  failure_category: 'non_zero_exit',
  started_at: '2026-10-10T11:04:20+00:00',
  finished_at: '2026-10-10T11:05:20+00:00',
  duration_seconds: 60.0,
  requested_model: null,
  reported_model: 'gpt-5',
  model_source: 'executor_reported',
  token_usage: null,
  retry_of: 'inv-a-1-implementation',
  retry_reason: 'verification exited non-zero',
  exit_code: 1,
  log_locator: 'logs/inv-a-1-verification.log',
}

/** 会话 B 的第二次尝试：一条未闭合调用（不得显示估算耗时）。 */
const INVOCATION_B_UNCLOSED = {
  invocation_id: 'inv-b-2-fix',
  run_id: SESSION_B_RUN_ID,
  attempt_number: 2,
  phase: 'fix',
  role: 'fixer',
  executor: 'claude',
  outcome: 'unclosed',
  failure_category: null,
  started_at: '2026-10-10T11:06:00+00:00',
  finished_at: null,
  duration_seconds: null,
  requested_model: null,
  reported_model: null,
  model_source: null,
  token_usage: null,
  retry_of: null,
  retry_reason: null,
  exit_code: null,
  log_locator: null,
}

const MOCK_INVOCATIONS = {
  available: true,
  truncated: false,
  rows: [INVOCATION_A_IMPLEMENTATION, INVOCATION_A_VERIFICATION_FAILED, INVOCATION_B_UNCLOSED],
}

/**
 * 时间线 fixture：两个执行会话 + 一条无会话标识的旧事件。
 *
 * 刻意安排 `retry` 早于同秒的 `attempt` 落盘：折叠后的行首必须是「Agent 尝试」，
 * 不随写入顺序漂移。`started`/`claimed` 同秒，观测事件 `agent_token_usage` 中性徽章。
 */
const MOCK_LIFECYCLE_DETAIL = {
  repo_id: 'keda-main',
  prd_path: FIXTURE_PRD_PATH,
  run_id: 'keda-main#417',
  issue_number: 417,
  trigger: 'console_start',
  current_phase: 'executing',
  in_progress: true,
  outcome: null,
  history_complete: true,
  started_at: '2026-10-10T11:00:00+00:00',
  finished_at: null,
  durations: {
    end_to_end_seconds: 600.0,
    active_seconds: 300.0,
    waiting_seconds: 180.0,
    blocked_seconds: 120.0,
  },
  events: [
    {
      // 旧数据：没有 invocation_run，必须落到「未归属」而不是被猜进任何会话。
      event_type: 'queued',
      phase: 'queued',
      status: 'queued',
      actor: 'backlog',
      occurred_at: '2026-10-10T11:00:00+00:00',
      detail: { trigger: 'manual' },
    },
    {
      event_type: 'started',
      phase: 'executing',
      status: 'started',
      actor: 'runner',
      occurred_at: '2026-10-10T11:01:00+00:00',
      detail: { invocation_run: SESSION_A_RUN_ID },
    },
    {
      event_type: 'claimed',
      phase: 'executing',
      status: 'claimed',
      actor: 'runner',
      occurred_at: '2026-10-10T11:01:00+00:00',
      detail: { invocation_run: SESSION_A_RUN_ID },
    },
    {
      event_type: 'attempt',
      phase: 'executing',
      status: 'attempt',
      actor: 'runner',
      occurred_at: '2026-10-10T11:02:00+00:00',
      detail: {
        invocation_run: SESSION_A_RUN_ID,
        agent: 'codex',
        attempt_number: 1,
        failure_type: 'verification',
        recovered: false,
      },
    },
    {
      event_type: 'agent_token_usage',
      phase: 'none',
      status: 'none',
      actor: 'runner',
      occurred_at: '2026-10-10T11:05:30+00:00',
      detail: {
        invocation_run: SESSION_A_RUN_ID,
        flow: 'verify',
        agent: 'codex',
        token_usage: { input_tokens: 1200, output_tokens: 300 },
      },
    },
    {
      // 同秒但先写 retry：行首仍须是 attempt，retry 折叠成标记。
      event_type: 'retry',
      phase: 'executing',
      status: 'retry',
      actor: 'runner',
      occurred_at: '2026-10-10T11:06:00+00:00',
      detail: { invocation_run: SESSION_B_RUN_ID, agent: 'claude', attempt_number: 2 },
    },
    {
      event_type: 'attempt',
      phase: 'executing',
      status: 'attempt',
      actor: 'runner',
      occurred_at: '2026-10-10T11:06:00+00:00',
      detail: {
        invocation_run: SESSION_B_RUN_ID,
        agent: 'claude',
        attempt_number: 2,
        failure_type: 'none',
        recovered: true,
      },
    },
    {
      event_type: 'blocked',
      phase: 'blocked',
      status: 'blocked',
      actor: 'runner',
      occurred_at: '2026-10-10T11:07:00+00:00',
      detail: { invocation_run: SESSION_B_RUN_ID, error_summary: '等待人工输入 Spec 澄清' },
    },
  ],
  invocations: MOCK_INVOCATIONS,
  has_data: true,
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

/** 旧记录形态：事件里没有会话标识，响应里也没有调用明细区。 */
function toLegacyDetail(lifecycleDetail: Record<string, unknown>) {
  const legacyEvents = (lifecycleDetail.events as Record<string, unknown>[]).map((event) => {
    const eventDetail = { ...(event.detail as Record<string, unknown>) }
    delete eventDetail.invocation_run
    return { ...event, detail: eventDetail }
  })
  const legacyResponse = { ...lifecycleDetail }
  delete legacyResponse.invocations
  return { ...legacyResponse, events: legacyEvents }
}

/** 用给定 lifecycle detail 顶替端点并打开「执行过程」标签。 */
async function openLifecycleTab(page: Page, lifecyclePayload: unknown): Promise<void> {
  await page.route('/api/v1/agent-runner/backlog/prds/*/lifecycle*', (route) =>
    fulfillJson(route, lifecyclePayload),
  )
  await page.goto('/app/backlog')
  await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
  await page.getByRole('button', { name: /视图：/ }).click()
  await page.getByRole('menuitemradio', { name: '列表' }).click()
  await page.getByRole('button', { name: '查看原文' }).first().click()
  await expect(page.getByTestId('prd-detail')).toBeVisible()
  await page.getByTestId('prd-detail-tab-lifecycle').click()
  await expect(page.getByTestId('prd-lifecycle-view')).toBeVisible()
}

test.describe('PRD lifecycle invocation detail timeline', () => {
  test.beforeEach(async ({ page, api }) => {
    await page.route('/api/v1/agent-runner/backlog/prds*', (route) =>
      fulfillJson(route, MOCK_PRDS_RESPONSE),
    )
    await page.route('/api/v1/agent-runner/backlog/settings*', (route) =>
      fulfillJson(route, {
        repo_id: 'keda-main',
        max_parallel: 2,
        default_view: 'list',
        updated_at: '2026-10-10T00:00:00+00:00',
      }),
    )
    await page.route('/api/v1/agent-runner/backlog/prds/*/content*', (route) =>
      route.fulfill({ status: 200, contentType: 'text/plain', body: '# Fixture PRD\n' }),
    )
    await page.route('/api/v1/agent-runner/backlog/prds/*/evidence*', (route) =>
      fulfillJson(route, {
        prd_path: FIXTURE_PRD_PATH,
        prd_stem: 'P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline',
        evidence_dir:
          'tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline',
        exists: true,
        files: [],
      }),
    )
    // Keep the authenticated API client warm so the session fixture stays alive.
    await api.get('/api/auth/me')
  })

  test('rv-4 timeline groups by session, folds same-second rows and keeps badges neutral', async ({
    page,
  }) => {
    await openLifecycleTab(page, MOCK_LIFECYCLE_DETAIL)

    const sessionA = page
      .getByTestId('prd-lifecycle-session-group')
      .filter({ hasText: SESSION_A_RUN_ID })
    const sessionB = page
      .getByTestId('prd-lifecycle-session-group')
      .filter({ hasText: SESSION_B_RUN_ID })
    await expect(page.getByTestId('prd-lifecycle-session-group')).toHaveCount(2)
    await expect(sessionA).toHaveCount(1)
    await expect(sessionB).toHaveCount(1)

    // 同秒 started/claimed 折叠成一行：会话头部事件只剩「开始执行」一行 + 折叠标记。
    const sessionEventRows = sessionA.getByTestId('prd-lifecycle-session-events').locator('li')
    await expect(sessionEventRows).toHaveCount(2)
    const startedRow = sessionEventRows.filter({ has: page.getByTestId('prd-lifecycle-event-started') })
    await expect(startedRow.getByTestId('prd-lifecycle-event-claimed')).toHaveCount(1)

    // 观测事件徽章中性：显示「观测记录」，整条时间线不出现「未开始」。
    await expect(
      sessionA.getByTestId('prd-lifecycle-event-status-agent_token_usage'),
    ).toContainText('观测记录')
    await expect(page.getByTestId('prd-lifecycle-view').getByText('未开始', { exact: true })).toHaveCount(0)

    // attempt/retry 同秒折叠，且行首由声明顺序决定（retry 先落盘也不漂移）。
    const attemptTwoRows = sessionB
      .locator('[data-attempt-number="2"]')
      .getByTestId('prd-lifecycle-attempt-events')
      .locator('li')
    await expect(attemptTwoRows).toHaveCount(1)
    await expect(attemptTwoRows.getByTestId('prd-lifecycle-event-attempt')).toHaveCount(1)
    await expect(attemptTwoRows.getByTestId('prd-lifecycle-event-retry')).toHaveCount(1)

    // 无会话标识的旧事件保持原顺序平铺，并显式披露未归属。
    const unattributed = page.getByTestId('prd-lifecycle-unattributed-events')
    await expect(unattributed.getByTestId('prd-lifecycle-event-queued')).toHaveCount(1)
    await expect(page.getByTestId('prd-lifecycle-invocation-notice')).toContainText('没有执行会话标识')

    await screenshot(page, 'rv-4-timeline-sessions-folded.png')
    await writeEvidence('rv-4-lifecycle-detail.json', MOCK_LIFECYCLE_DETAIL)
  })

  test('rv-4 attempt expands invocation rows with honest duration and retry chain', async ({
    page,
  }) => {
    await openLifecycleTab(page, MOCK_LIFECYCLE_DETAIL)

    const sessionA = page
      .getByTestId('prd-lifecycle-session-group')
      .filter({ hasText: SESSION_A_RUN_ID })
    const attemptOne = sessionA.locator('[data-attempt-number="1"]')
    const toggle = attemptOne.getByTestId('prd-lifecycle-attempt-toggle')
    await expect(toggle).toHaveText('展开调用序列（2 次）')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(attemptOne.getByTestId('prd-lifecycle-invocation-row')).toHaveCount(0)

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const invocationRows = attemptOne.getByTestId('prd-lifecycle-invocation-row')
    await expect(invocationRows).toHaveCount(2)

    await expect(invocationRows.nth(0)).toContainText('编写代码')
    await expect(invocationRows.nth(0)).toContainText('负责修改代码')
    await expect(invocationRows.nth(0)).toContainText('正常退出')
    await expect(
      invocationRows.nth(0).getByTestId('prd-lifecycle-invocation-duration').locator('dd'),
    ).toHaveText('2m')
    await expect(invocationRows.nth(0)).toContainText('KC 下发且执行器报告：gpt-5-codex')
    await expect(invocationRows.nth(0)).toContainText('logs/inv-a-1-implementation.log')

    await expect(invocationRows.nth(1)).toContainText('运行验证')
    await expect(invocationRows.nth(1)).toContainText('异常退出')
    await expect(invocationRows.nth(1)).toContainText('执行器报告：gpt-5；KC 没有单独指定模型')
    await expect(invocationRows.nth(1)).toContainText('替代上一次调用（verification exited non-zero）')
    await expect(invocationRows.nth(1)).toContainText('non_zero_exit')
    await screenshot(page, 'rv-4-invocation-rows-expanded.png')

    // 未闭合调用：结束时间显示未闭合、耗时显示未记录，不估算。
    const sessionB = page
      .getByTestId('prd-lifecycle-session-group')
      .filter({ hasText: SESSION_B_RUN_ID })
    const unclosedToggle = sessionB
      .locator('[data-attempt-number="2"]')
      .getByTestId('prd-lifecycle-attempt-toggle')
    await unclosedToggle.click()
    const unclosedRow = sessionB
      .locator('[data-attempt-number="2"]')
      .getByTestId('prd-lifecycle-invocation-row')
    await expect(unclosedRow).toHaveCount(1)
    await expect(
      unclosedRow.getByTestId('prd-lifecycle-invocation-finished').locator('dd'),
    ).toHaveText('未闭合')
    await expect(
      unclosedRow.getByTestId('prd-lifecycle-invocation-duration').locator('dd'),
    ).toHaveText('未记录')

    // 事件抽屉仍显示原始 detail：被折叠的重试事件照样可点开，字段一条不少。
    const foldedRetry = sessionB
      .locator('[data-attempt-number="2"]')
      .getByTestId('prd-lifecycle-event-retry')
    await foldedRetry.click()
    const sheet = page.getByTestId('prd-lifecycle-event-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet).toContainText('claude')
    await expect(sheet).toContainText(SESSION_B_RUN_ID)
    await page.keyboard.press('Escape')
    await expect(sheet).not.toBeVisible()
  })

  test('rv-3 unavailable invocation ledger is disclosed without blocking the timeline', async ({
    page,
  }) => {
    await openLifecycleTab(page, {
      ...MOCK_LIFECYCLE_DETAIL,
      invocations: { available: false, truncated: false, rows: [] },
    })

    await expect(page.getByTestId('prd-lifecycle-invocation-notice')).toContainText(
      '调用明细不可用',
    )
    await expect(page.getByTestId('prd-lifecycle-session-group')).toHaveCount(2)
    const attemptOneToggle = page
      .getByTestId('prd-lifecycle-session-group')
      .filter({ hasText: SESSION_A_RUN_ID })
      .locator('[data-attempt-number="1"]')
      .getByTestId('prd-lifecycle-attempt-toggle')
    await attemptOneToggle.click()
    await expect(
      page
        .getByTestId('prd-lifecycle-session-group')
        .filter({ hasText: SESSION_A_RUN_ID })
        .getByTestId('prd-lifecycle-invocation-empty'),
    ).toContainText('调用明细不可用')
    await screenshot(page, 'rv-3-invocations-unavailable.png')
  })

  test('rv-3 truncated ledger discloses the read cap', async ({ page }) => {
    await openLifecycleTab(page, {
      ...MOCK_LIFECYCLE_DETAIL,
      invocations: {
        available: true,
        truncated: true,
        rows: [INVOCATION_A_IMPLEMENTATION],
      },
    })

    await expect(page.getByTestId('prd-lifecycle-invocation-notice')).toContainText('已截断')
    await expect(page.getByTestId('prd-lifecycle-session-group')).toHaveCount(2)
    await screenshot(page, 'rv-3-invocations-truncated.png')
  })

  test('rv-3 readable-but-empty ledger discloses legacy runs', async ({ page }) => {
    await openLifecycleTab(page, {
      ...MOCK_LIFECYCLE_DETAIL,
      invocations: { available: true, truncated: false, rows: [] },
    })

    await expect(page.getByTestId('prd-lifecycle-invocation-notice')).toContainText(
      '没有本 PRD 的调用记录',
    )
    await expect(page.getByTestId('prd-lifecycle-session-group')).toHaveCount(2)
  })

  test('rv-3 legacy response without the invocations field falls back to a flat timeline', async ({
    page,
  }) => {
    const legacyDetail = toLegacyDetail(MOCK_LIFECYCLE_DETAIL)
    await openLifecycleTab(page, legacyDetail)

    // 旧记录：没有会话标识就不虚构分组，单层列表 + 明确说明不显示调用序列。
    await expect(page.getByTestId('prd-lifecycle-session-group')).toHaveCount(0)
    await expect(page.getByTestId('prd-lifecycle-flat-events')).toBeVisible()
    // 8 条事件里同秒的 started/claimed 与 retry/attempt 各折叠成一行 → 6 行。
    await expect(page.getByTestId('prd-lifecycle-flat-events').locator('li')).toHaveCount(6)
    const notice = page.getByTestId('prd-lifecycle-invocation-notice')
    await expect(notice).toContainText('不显示调用序列')
    await expect(notice).toContainText('这些事件都没有执行会话标识')
    await screenshot(page, 'rv-3-legacy-flat-timeline.png')
    await writeEvidence('rv-3-legacy-detail.json', legacyDetail)
  })
})
