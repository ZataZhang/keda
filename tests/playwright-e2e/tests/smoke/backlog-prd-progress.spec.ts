/**
 * PRD 交付进度信号的浏览器流程验收（前端可见口径的 rv-1 类真实入口）。
 *
 * 本 spec 走真实 Next.js 页面与真实浏览器交互，只把详情面板依赖的读端点（列表 /
 * 设置 / 原文 / 生命周期 / 证据 manifest / 进度聚合）用确定性 fixture 顶替，使 CI
 * 稳定。fixture 字段与真实 `/api/v1/agent-runner/backlog/prds/{path}/progress`、
 * `/evidence` 响应逐字一致（见 PRD §7.1 契约）。
 *
 * 覆盖：详情头部三读数（清单 / 影响树 / 证据）与弱信号文案、证据标签的「计划 vs
 * 产出」分组、未合并来源徽标、待产出占位与「其他」归组，以及无计划 / 无基准 /
 * 来源不可用三种降级态——确认无数据用 `-`、读取不可用用「暂不可用」，都不混为零。
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '../../fixtures/session.fixture'
import type { Page, Route } from '@playwright/test'

const currentDirectoryPath = dirname(fileURLToPath(import.meta.url))
const repositoryRootPath = resolve(currentDirectoryPath, '../../../..')
// spec 产物是浏览器流程证据（HTTP 用 fixture），与 harness 的真实端点响应分开存放，
// provenance 可区分。
const evidenceDirectoryPath = resolve(
  repositoryRootPath,
  'tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/e2e',
)

const FIXTURE_PRD_PATH = 'tasks/pending/P1-FEAT-20261010-093054-progress-demo.md'
const FIXTURE_PRD_STEM = 'P1-FEAT-20261010-093054-progress-demo'

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
      title: 'PRD 交付进度信号（E2E Fixture）',
      status: 'pending',
      priority: 'P1',
      issue_url: 'https://github.com/zata-zhangtao/keda/issues/274',
      issue_number: 274,
      state: 'running',
      acceptance_total: 12,
      acceptance_checked: 3,
      delivery_dependencies: [],
      updated_at: '2026-10-10T10:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
  ],
  repo_id: 'keda-main',
  include_archived: false,
  scanned_at: '2026-10-10T12:00:00+00:00',
}

/** 运行中进度读数：清单 3/12、影响树 ~7/12 ?2、证据 2/3。 */
const MOCK_PROGRESS_RUNNING = {
  prd_path: FIXTURE_PRD_PATH,
  prd_stem: FIXTURE_PRD_STEM,
  prd_source: 'worktree',
  worktree_status: 'matched',
  worktree: { branch: 'issue-274' },
  checklist: { status: 'available', checked: 3, total: 12 },
  evidence: {
    planned: [
      { id: 'rv-1', summary: '运行中详情显示交付读数', reviewer: 'human' },
      { id: 'rv-2', summary: '读取边界与工作区优先级正确', reviewer: 'verifier' },
      { id: 'rv-3', summary: '影响树读数与 CLI 一致', reviewer: 'verifier' },
    ],
    produced_ids: ['rv-1', 'rv-3'],
    produced_planned_count: 2,
    file_count: 4,
    attribution_source: 'manifest',
    status: 'complete',
    reports: { verification_plan: true, evidence_report: false, verifier_report: false },
  },
  impact_status: 'available',
  impact: { touched: 7, judgeable: 12, unresolvable: 2 },
}

/** 运行中证据 manifest：三个工作区文件（未合并）+ 一个主仓库报告。 */
const MOCK_EVIDENCE_RUNNING = {
  prd_path: FIXTURE_PRD_PATH,
  prd_stem: FIXTURE_PRD_STEM,
  evidence_dir: `tasks/evidence/${FIXTURE_PRD_STEM}`,
  exists: true,
  status: 'complete',
  files: [
    {
      name: 'rv-1-running-detail.png',
      size_bytes: 20480,
      media_type: 'image/png',
      role: 'artifact',
      artifact_token: 'cnYtMS1ydW5uaW5nLWRldGFpbC5wbmc',
      source: 'worktree',
    },
    {
      name: 'rv-1-verification-plan.md',
      size_bytes: 512,
      media_type: 'text/markdown',
      role: 'verification_plan',
      artifact_token: 'cnYtMS12ZXJpZmljYXRpb24tcGxhbi5tZA',
      source: 'worktree',
    },
    {
      name: 'rv-3-cli-parity.txt',
      size_bytes: 1024,
      media_type: 'text/plain',
      role: 'artifact',
      artifact_token: 'cnYtMy1jbGktcGFyaXR5LnR4dA',
      source: 'worktree',
    },
    {
      name: 'evidence-report.md',
      size_bytes: 800,
      media_type: 'text/markdown',
      role: 'evidence_report',
      artifact_token: 'ZXZpZGVuY2UtcmVwb3J0Lm1k',
      source: 'main',
    },
  ],
}

/** 无计划、无工作区的旧 PRD：三读数全部 `-`，但仍有 2 个文件。 */
const MOCK_PROGRESS_NO_PLAN = {
  prd_path: FIXTURE_PRD_PATH,
  prd_stem: FIXTURE_PRD_STEM,
  prd_source: 'main',
  worktree_status: 'none',
  worktree: null,
  checklist: { status: 'available', checked: 0, total: 0 },
  evidence: {
    planned: [],
    produced_ids: [],
    produced_planned_count: 0,
    file_count: 2,
    attribution_source: 'none',
    status: 'complete',
    reports: { verification_plan: false, evidence_report: true, verifier_report: false },
  },
  impact_status: 'no_worktree',
  impact: null,
}

const MOCK_EVIDENCE_NO_PLAN = {
  ...MOCK_EVIDENCE_RUNNING,
  status: 'complete',
  files: [
    {
      name: 'evidence-report.md',
      size_bytes: 800,
      media_type: 'text/markdown',
      role: 'evidence_report',
      artifact_token: 'ZXZpZGVuY2UtcmVwb3J0Lm1k',
      source: 'main',
    },
    {
      name: 'verification-plan.md',
      size_bytes: 400,
      media_type: 'text/markdown',
      role: 'verification_plan',
      artifact_token: 'dmVyaWZpY2F0aW9uLXBsYW4ubWQ',
      source: 'main',
    },
  ],
}

/** 有工作区、有清单但无有效 merge-base：影响树显示 `-`，其余读数正常。 */
const MOCK_PROGRESS_NO_BASE = {
  ...MOCK_PROGRESS_RUNNING,
  impact_status: 'no_base',
  impact: null,
}

/** 工作区发现失败（不可用）：来源相关读数全部「暂不可用」，证据 tab 显式披露。 */
const MOCK_PROGRESS_UNAVAILABLE = {
  prd_path: FIXTURE_PRD_PATH,
  prd_stem: FIXTURE_PRD_STEM,
  prd_source: null,
  worktree_status: 'unavailable',
  worktree: null,
  checklist: { status: 'unavailable', checked: null, total: null },
  evidence: {
    planned: null,
    produced_ids: [],
    produced_planned_count: null,
    file_count: null,
    attribution_source: 'none',
    status: 'unavailable',
    reports: { verification_plan: null, evidence_report: null, verifier_report: null },
  },
  impact_status: 'unavailable',
  impact: null,
}

const MOCK_EVIDENCE_UNAVAILABLE = {
  prd_path: FIXTURE_PRD_PATH,
  prd_stem: FIXTURE_PRD_STEM,
  evidence_dir: `tasks/evidence/${FIXTURE_PRD_STEM}`,
  exists: false,
  status: 'unavailable',
  files: [],
}

const MOCK_LIFECYCLE_DETAIL = {
  repo_id: 'keda-main',
  prd_path: FIXTURE_PRD_PATH,
  run_id: 'keda-main#274',
  issue_number: 274,
  trigger: 'console_start',
  current_phase: 'executing',
  in_progress: true,
  outcome: null,
  history_complete: true,
  started_at: '2026-10-10T10:00:00+00:00',
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
      actor: 'backlog',
      occurred_at: '2026-10-10T10:01:00+00:00',
      detail: {},
    },
  ],
  has_data: true,
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

async function openBacklogDetail(page: Page): Promise<void> {
  await page.goto('/app/backlog')
  await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()
  await page.getByRole('button', { name: /视图：/ }).click()
  await page.getByRole('menuitemradio', { name: '列表' }).click()
  await page.getByRole('button', { name: '查看原文' }).first().click()
  await expect(page.getByTestId('prd-detail')).toBeVisible()
}

test.describe('PRD progress signals', () => {
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
    await page.route('/api/v1/agent-runner/backlog/prds/*/lifecycle*', (route) =>
      fulfillJson(route, MOCK_LIFECYCLE_DETAIL),
    )
    await page.route('/api/v1/agent-runner/backlog/prds/*/progress*', (route) =>
      fulfillJson(route, MOCK_PROGRESS_RUNNING),
    )
    await page.route('/api/v1/agent-runner/backlog/prds/*/evidence*', (route) =>
      fulfillJson(route, MOCK_EVIDENCE_RUNNING),
    )
    await api.get('/api/auth/me')
  })

  test('RV-1 header shows three readings and evidence tab groups by plan', async ({ page }) => {
    await openBacklogDetail(page)

    // 头部三读数：清单 3/12、影响树 ~7/12 ?2、证据 2/3（精确文案）。
    const strip = page.getByTestId('prd-progress-strip')
    await expect(strip).toBeVisible()
    await expect(page.getByTestId('prd-progress-checklist')).toHaveText('3/12')
    await expect(page.getByTestId('prd-progress-impact')).toHaveText('~7/12 ?2')
    await expect(page.getByTestId('prd-progress-evidence')).toHaveText('2/3')

    // 证据标签：计划分组 + 汇总行 + 未合并徽标 + 待产出占位 + 其他归组。
    await page.getByTestId('prd-detail-tab-evidence').click()
    await expect(page.getByTestId('prd-evidence-summary')).toContainText(
      '计划 3 项 · 已产出 2 项（共 4 个文件）',
    )
    await expect(page.getByTestId('prd-evidence-plan-groups')).toBeVisible()

    // rv-2 无产物 → 待产出占位。
    await expect(page.getByText('待产出')).toBeVisible()
    // rv-1 / rv-3 分组有工作区文件并标注未合并。
    await expect(page.getByTestId('prd-evidence-unmerged-rv-1-running-detail.png')).toContainText(
      '未合并',
    )
    await expect(page.getByTestId('prd-evidence-unmerged-rv-3-cli-parity.txt')).toContainText('未合并')
    // 主仓库来源文件不带未合并徽标，且归入「其他」（无 rv 前缀的报告）。
    await expect(page.getByTestId('prd-evidence-others')).toContainText('evidence-report.md')
    await expect(page.getByTestId('prd-evidence-unmerged-evidence-report.md')).toHaveCount(0)

    await screenshot(page, 'rv-1-e2e-running.png')
    await writeEvidence('rv-1-progress-running.json', MOCK_PROGRESS_RUNNING)
    await writeEvidence('rv-1-evidence-running.json', MOCK_EVIDENCE_RUNNING)
  })

  test('RV-1 no-plan PRD shows dash readings, not fabricated zero', async ({ page }) => {
    await page.route('/api/v1/agent-runner/backlog/prds/*/progress*', (route) =>
      fulfillJson(route, MOCK_PROGRESS_NO_PLAN),
    )
    await page.route('/api/v1/agent-runner/backlog/prds/*/evidence*', (route) =>
      fulfillJson(route, MOCK_EVIDENCE_NO_PLAN),
    )
    await openBacklogDetail(page)

    await expect(page.getByTestId('prd-progress-checklist')).toHaveText('—')
    await expect(page.getByTestId('prd-progress-impact')).toHaveText('—')
    await expect(page.getByTestId('prd-progress-evidence')).toHaveText('—')

    await page.getByTestId('prd-detail-tab-evidence').click()
    await expect(page.getByTestId('prd-evidence-summary')).toContainText('未声明验证计划')
    // 无计划时仍如实列出 2 个文件，不伪造分组。
    await expect(page.getByTestId('prd-evidence-plan-groups')).toHaveCount(0)
    await expect(page.getByTestId('prd-evidence-body')).toContainText('共 2 个文件')

    await screenshot(page, 'rv-1-e2e-no-plan.png')
  })

  test('RV-1 no merge-base shows dash for impact while other readings hold', async ({ page }) => {
    await page.route('/api/v1/agent-runner/backlog/prds/*/progress*', (route) =>
      fulfillJson(route, MOCK_PROGRESS_NO_BASE),
    )
    await openBacklogDetail(page)

    await expect(page.getByTestId('prd-progress-impact')).toHaveText('—')
    await expect(page.getByTestId('prd-progress-checklist')).toHaveText('3/12')
    await expect(page.getByTestId('prd-progress-evidence')).toHaveText('2/3')

    await screenshot(page, 'rv-1-e2e-no-base.png')
  })

  test('RV-1 unavailable source surfaces 暂不可用 rather than empty', async ({ page }) => {
    await page.route('/api/v1/agent-runner/backlog/prds/*/progress*', (route) =>
      fulfillJson(route, MOCK_PROGRESS_UNAVAILABLE),
    )
    await page.route('/api/v1/agent-runner/backlog/prds/*/evidence*', (route) =>
      fulfillJson(route, MOCK_EVIDENCE_UNAVAILABLE),
    )
    await openBacklogDetail(page)

    // 三读数都显示「暂不可用」，不混成 `-` 或 `0`。
    for (const metric of ['checklist', 'impact', 'evidence']) {
      await expect(page.getByTestId(`prd-progress-${metric}`)).toHaveText('暂不可用')
    }

    await page.getByTestId('prd-detail-tab-evidence').click()
    await expect(page.getByTestId('prd-evidence-unavailable')).toBeVisible()

    await screenshot(page, 'rv-1-e2e-unavailable.png')
  })
})
