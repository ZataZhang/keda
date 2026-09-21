/**
 * PRD 生命周期观测的浏览器流程验收（rv-1 / rv-2 的真实前端入口）。
 *
 * 本 spec 走真实 Next.js 页面、真实路由与真实浏览器交互；只把 GitHub 与
 * 生命周期/统计两个读端点的响应用确定性 fixture 顶替，使 CI 稳定。fixture
 * 的字段与真实 `/api/v1/agent-runner/...` 响应逐字一致（见 PRD §7 契约）。
 *
 * 覆盖：Roadmap 详情的“执行过程”标签（当前阶段 + 四项耗时 + 有序时间线 +
 * 事件抽屉）、失败与重试历史不被成功覆盖、观测不完整告警、Stats 的 PRD 端到端
 * 统计与明细、以及未关联旧记录的披露。
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '../../fixtures/session.fixture'
import type { Page, Route } from '@playwright/test'

const currentDirectoryPath = dirname(fileURLToPath(import.meta.url))
const repositoryRootPath = resolve(currentDirectoryPath, '../../../..')
// 与真实入口 harness 分目录存放：spec 的产物是浏览器流程证据（HTTP 用 fixture），
// 不能覆盖 harness 产出的真实 FastAPI + SQLite 响应；两者 provenance 必须可区分。
const evidenceDirectoryPath = resolve(
  repositoryRootPath,
  'tasks/evidence/P1-FEAT-20260921-161621-prd-lifecycle-observability/e2e',
)

const FIXTURE_PRD_PATH = 'tasks/pending/P1-FEAT-20260921-161621-demo-lifecycle.md'

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
      title: 'PRD 生命周期观测与执行分析（E2E Fixture）',
      status: 'pending',
      priority: 'P1',
      issue_url: 'https://github.com/zata-zhangtao/keda/issues/161',
      issue_number: 161,
      state: 'running',
      acceptance_total: 19,
      acceptance_checked: 0,
      delivery_dependencies: [],
      updated_at: '2026-09-21T10:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
  ],
  repo_id: 'keda-main',
  include_archived: false,
  scanned_at: '2026-09-21T12:00:00+00:00',
}

/** 细节 fixture：含一次失败 attempt、一次重试与恢复，用于验证历史不被覆盖。 */
const MOCK_LIFECYCLE_DETAIL = {
  repo_id: 'keda-main',
  prd_path: FIXTURE_PRD_PATH,
  run_id: 'keda-main#161',
  issue_number: 161,
  trigger: 'console_start',
  current_phase: 'executing',
  in_progress: true,
  outcome: null,
  history_complete: true,
  started_at: '2026-09-21T10:00:00+00:00',
  finished_at: null,
  durations: {
    end_to_end_seconds: 3600.0,
    active_seconds: 1200.0,
    waiting_seconds: 1800.0,
    blocked_seconds: 600.0,
  },
  events: [
    {
      event_type: 'queued',
      phase: 'queued',
      actor: 'roadmap',
      occurred_at: '2026-09-21T10:00:00+00:00',
      detail: { trigger: 'manual' },
    },
    {
      event_type: 'started',
      phase: 'executing',
      actor: 'roadmap',
      occurred_at: '2026-09-21T10:01:00+00:00',
      detail: {},
    },
    {
      event_type: 'attempt',
      phase: 'executing',
      actor: 'runner',
      occurred_at: '2026-09-21T10:03:00+00:00',
      detail: {
        agent: 'codex',
        attempt_number: 1,
        failure_type: 'verification',
        recovered: false,
        duration_seconds: 90.0,
      },
    },
    {
      event_type: 'retry',
      phase: 'executing',
      actor: 'runner',
      occurred_at: '2026-09-21T10:05:00+00:00',
      detail: { agent: 'codex', attempt_number: 2 },
    },
    {
      event_type: 'recovered',
      phase: 'executing',
      actor: 'runner',
      occurred_at: '2026-09-21T10:12:00+00:00',
      detail: { agent: 'codex', attempt_number: 2 },
    },
    {
      event_type: 'blocked',
      phase: 'blocked',
      actor: 'runner',
      occurred_at: '2026-09-21T10:20:00+00:00',
      detail: { error_summary: '等待人工输入 Spec 澄清' },
    },
  ],
  has_data: true,
}

/** 不完整账本 fixture：history_complete=false，页面必须显示告警。 */
const MOCK_LIFECYCLE_INCOMPLETE = {
  ...MOCK_LIFECYCLE_DETAIL,
  history_complete: false,
  events: MOCK_LIFECYCLE_DETAIL.events.slice(0, 2),
}

const MOCK_STATS = {
  repo_id: 'keda-main',
  window_days: 30,
  completed_runs: 3,
  average_end_to_end_seconds: 1200.0,
  median_end_to_end_seconds: 1200.0,
  p90_end_to_end_seconds: 1680.0,
  average_blocked_seconds: 120.0,
  bottleneck_phase: 'reviewing',
  bottleneck_phase_seconds: 2100.0,
  unlinked_run_count: 4,
  incomplete_run_count: 1,
  runs: [
    {
      run_id: 'keda-main#161',
      prd_path: FIXTURE_PRD_PATH,
      issue_number: 161,
      outcome: 'completed',
      current_phase: 'completed',
      in_progress: false,
      history_complete: true,
      started_at: '2026-09-21T10:00:00+00:00',
      finished_at: '2026-09-21T10:30:00+00:00',
      durations: {
        end_to_end_seconds: 1800.0,
        active_seconds: 840.0,
        waiting_seconds: 960.0,
        blocked_seconds: 0.0,
      },
    },
    {
      run_id: 'keda-main#162',
      prd_path: 'tasks/pending/P1-FEAT-20260921-161622-second.md',
      issue_number: 162,
      outcome: null,
      current_phase: 'validating',
      in_progress: true,
      history_complete: true,
      started_at: '2026-09-21T10:05:00+00:00',
      finished_at: null,
      durations: {
        end_to_end_seconds: 900.0,
        active_seconds: 300.0,
        waiting_seconds: 600.0,
        blocked_seconds: 0.0,
      },
    },
  ],
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

async function openRoadmapDetail(page: Page): Promise<void> {
  await page.goto('/app/roadmap')
  await expect(page.getByRole('heading', { name: '路线图' })).toBeVisible()
  // 默认依赖图视图没有卡片按钮；切到列表视图后打开详情。
  await page.getByRole('button', { name: /视图：/ }).click()
  await page.getByRole('menuitemradio', { name: '列表' }).click()
  await page.getByRole('button', { name: '查看原文' }).first().click()
  await expect(page.getByTestId('prd-detail')).toBeVisible()
}

test.describe('PRD lifecycle observability', () => {
  test.beforeEach(async ({ page, api }) => {
    await page.route('/api/v1/agent-runner/roadmap/prds*', (route) =>
      fulfillJson(route, MOCK_PRDS_RESPONSE),
    )
    await page.route('/api/v1/agent-runner/roadmap/settings*', (route) =>
      fulfillJson(route, {
        repo_id: 'keda-main',
        max_parallel: 2,
        default_view: 'list',
        updated_at: '2026-09-21T00:00:00+00:00',
      }),
    )
    await page.route('/api/v1/agent-runner/roadmap/prds/*/content*', (route) =>
      route.fulfill({ status: 200, contentType: 'text/plain', body: '# Fixture PRD\n' }),
    )
    await page.route('/api/v1/agent-runner/roadmap/prds/*/evidence*', (route) =>
      fulfillJson(route, {
        prd_path: FIXTURE_PRD_PATH,
        prd_stem: 'P1-FEAT-20260921-161621-demo-lifecycle',
        evidence_dir: 'tasks/evidence/P1-FEAT-20260921-161621-demo-lifecycle',
        exists: false,
        files: [],
      }),
    )
    await page.route('/api/v1/agent-runner/roadmap/prds/*/lifecycle*', (route) =>
      fulfillJson(route, MOCK_LIFECYCLE_DETAIL),
    )
    await page.route('/api/v1/agent-runner/console/stats/prd-lifecycle*', (route) =>
      fulfillJson(route, MOCK_STATS),
    )
    // Keep the authenticated API client warm so the session fixture stays alive.
    await api.get('/api/auth/me')
  })

  test('RV-1 roadmap detail shows phase, duration split and ordered timeline', async ({ page }) => {
    await openRoadmapDetail(page)
    await page.getByTestId('prd-detail-tab-lifecycle').click()

    const view = page.getByTestId('prd-lifecycle-view')
    await expect(view).toBeVisible()
    await expect(page.getByTestId('prd-lifecycle-current-phase')).toContainText('执行')

    // 四项耗时拆分可见（端到端 / 执行 / 等待 / 阻塞）
    for (const metric of ['e2e', 'active', 'waiting', 'blocked']) {
      await expect(page.getByTestId(`prd-lifecycle-metric-${metric}`)).toBeVisible()
    }

    // 时间线按发生时间排序：queued 在 blocked 之前
    const queuedRow = page.getByTestId('prd-lifecycle-event-queued')
    const blockedRow = page.getByTestId('prd-lifecycle-event-blocked')
    await expect(queuedRow).toBeVisible()
    await expect(blockedRow).toBeVisible()
    const queuedBox = await queuedRow.boundingBox()
    const blockedBox = await blockedRow.boundingBox()
    expect(queuedBox!.y).toBeLessThan(blockedBox!.y)

    // 点击一个事件打开抽屉，显示 run id / 时间 / Agent / 原因
    await page.getByTestId('prd-lifecycle-event-attempt').first().click()
    const sheet = page.getByTestId('prd-lifecycle-event-sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet).toContainText('keda-main#161')
    await expect(sheet).toContainText('codex')
    await screenshot(page, 'rv-1-roadmap-lifecycle.png')
    await page.getByTestId('prd-lifecycle-event-sheet-close').click()
    await expect(sheet).not.toBeVisible()

    await writeEvidence('rv-1-lifecycle-detail.json', MOCK_LIFECYCLE_DETAIL)
  })

  test('RV-1b failed attempt and retry history is preserved', async ({ page }) => {
    await openRoadmapDetail(page)
    await page.getByTestId('prd-detail-tab-lifecycle').click()

    // 失败 attempt、重试与恢复三条事件同时在场，成功不覆盖失败历史
    await expect(page.getByTestId('prd-lifecycle-event-attempt').first()).toBeVisible()
    await expect(page.getByTestId('prd-lifecycle-event-retry').first()).toBeVisible()
    await expect(page.getByTestId('prd-lifecycle-event-recovered').first()).toBeVisible()
    await screenshot(page, 'rv-1b-roadmap-failure-retry.png')
  })

  test('RV-3b incomplete ledger shows an explicit warning', async ({ page }) => {
    await page.route('/api/v1/agent-runner/roadmap/prds/*/lifecycle*', (route) =>
      fulfillJson(route, MOCK_LIFECYCLE_INCOMPLETE),
    )
    await openRoadmapDetail(page)
    await page.getByTestId('prd-detail-tab-lifecycle').click()

    await expect(page.getByTestId('prd-lifecycle-incomplete')).toBeVisible()
    await screenshot(page, 'rv-3b-incomplete-ledger-warning.png')
  })

  test('RV-2 stats shows PRD lifecycle percentiles, bottleneck and detail rows', async ({ page }) => {
    await page.goto('/app/stats')
    const card = page.getByTestId('stats-prd-lifecycle')
    await expect(card).toBeVisible()

    const metrics = page.getByTestId('stats-prd-lifecycle-metrics')
    await expect(metrics).toContainText('平均')
    await expect(metrics).toContainText('中位数')
    await expect(metrics).toContainText('P90')
    await expect(metrics).toContainText('阻塞')
    // 阶段瓶颈与未关联旧记录必须显式披露
    await expect(card).toContainText('审阅')
    await expect(card).toContainText('未关联')

    await screenshot(page, 'rv-2-prd-stats.png')
    await writeEvidence('rv-2-stats-response.json', MOCK_STATS)
  })
})
