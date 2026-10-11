/**
 * Realistic validation for Backlog single-PRD controls, archived evidence and
 * the repository-level Backlog auto-advance toggle.
 *
 * 验证层级：**UI 真实 + Backlog API stub**。浏览器、Next.js 页面、API client 与
 * 详情/证据/Backlog 自动推进组件全部真实运行；只有 `/backlog/**` 的后端响应与
 * 仓库选择端点（`console/context`、`repositories`）被 Playwright route 替换为
 * 确定性数据，用于在 CI 里稳定复现「未开始 / 阻塞 / 已归档」等形态。仓库选择
 * 也必须固定：不固定时首屏目标仓库由 console 进程 cwd 与真实 registry 决定
 * （本地与 CI 的 repo_id 不同），start 请求体的 repo_id 会随之漂移。
 * start 请求不由测试伪造成功结果——它由测试捕获 URL 与请求体作为判据。
 *
 * 证据文件系统链（已归档 PRD 的真实证据目录读取）不在本文件 mock：那部分由
 * `tests/test_backlog_prd_evidence.py` 用真实文件系统与真实 FastAPI 覆盖。
 */

import { expect, test } from '../../fixtures/session.fixture'
import type { Page } from '@playwright/test'

type StartCapture = {
  url: string
  body: string | null
}

const REPO_ID = 'keda-main'
const STARTABLE_PRD_PATH = 'tasks/pending/P1-FEAT-20260101-startable.md'
const READY_PRD_PATH = 'tasks/pending/P1-FEAT-20260101-ready.md'
const BLOCKED_PRD_PATH = 'tasks/pending/P1-FEAT-20260101-blocked.md'
const ARCHIVED_PRD_PATH = 'tasks/archive/P1-FEAT-20260101-archived.md'

/**
 * base64url 编码 PRD 路径，与前端 `encodePrdPath` 同规则。
 *
 * @param prdPath - PRD 仓库相对路径。
 * @returns URL 安全片段。
 */
function encodePrdPath(prdPath: string): string {
  return Buffer.from(prdPath, 'utf-8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_')
}

/**
 * 构造 Backlog 列表响应：可启动 / 已就绪 / 阻塞 / 已归档各一条。
 *
 * @param overrides - 需要覆盖 PRD 状态时传入。
 * @returns mimic 真实后端的列表 payload。
 */
function buildPrdsPayload(overrides: Record<string, string> = {}) {
  const prds = [
    {
      prd_path: STARTABLE_PRD_PATH,
      title: '可单启动 PRD',
      status: 'pending',
      priority: 'P1',
      issue_url: null,
      issue_number: null,
      state: 'not_started',
      acceptance_total: 2,
      acceptance_checked: 0,
      delivery_dependencies: [],
      updated_at: '2026-09-16T00:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
    {
      prd_path: READY_PRD_PATH,
      title: '已就绪 PRD',
      status: 'pending',
      priority: 'P1',
      issue_url: 'https://github.com/example/keda/issues/42',
      issue_number: 42,
      state: 'ready',
      acceptance_total: 2,
      acceptance_checked: 0,
      delivery_dependencies: [],
      updated_at: '2026-09-16T00:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
    {
      prd_path: BLOCKED_PRD_PATH,
      title: '被上游阻塞 PRD',
      status: 'pending',
      priority: 'P1',
      issue_url: null,
      issue_number: null,
      state: 'waiting',
      acceptance_total: 2,
      acceptance_checked: 0,
      delivery_dependencies: [
        {
          from_path: BLOCKED_PRD_PATH,
          to_path: STARTABLE_PRD_PATH,
          kind: 'prd',
          detail: STARTABLE_PRD_PATH,
        },
      ],
      updated_at: '2026-09-16T00:00:00+00:00',
      block_reason: `等待上游 PRD 合并：${STARTABLE_PRD_PATH}`,
      next_action: null,
    },
    {
      prd_path: ARCHIVED_PRD_PATH,
      title: '已归档 PRD',
      status: 'archived',
      priority: 'P1',
      issue_url: null,
      issue_number: null,
      state: 'archived',
      acceptance_total: 3,
      acceptance_checked: 3,
      delivery_dependencies: [],
      updated_at: '2026-09-16T00:00:00+00:00',
      block_reason: null,
      next_action: null,
    },
  ]
  return {
    prds: prds.map((prd) =>
      overrides[prd.prd_path] ? { ...prd, state: overrides[prd.prd_path] } : prd,
    ),
    repo_id: REPO_ID,
    include_archived: true,
    scanned_at: '2026-09-16T00:00:00+00:00',
  }
}

/**
 * 构造 Backlog 自动推进状态响应。
 *
 * @param state - 覆盖字段（enabled / daemon_running / auto_merge_enabled）。
 * @returns 与后端 Backlog 自动推进状态同构的 payload。
 */
function buildAutopilotPayload(
  state: Partial<{ enabled: boolean; auto_merge_enabled: boolean; daemon_running: boolean }> = {},
) {
  return {
    repo_id: REPO_ID,
    enabled: false,
    auto_merge_enabled: false,
    daemon_running: false,
    max_parallel: 2,
    config_source: '.kedacode.toml',
    persisted_enabled: false,
    ...state,
  }
}

/**
 * 构造证据 manifest 响应（文件名与数量必须与后端受限目录逐项一致）。
 *
 * @param options.exists - 证据目录是否存在。
 * @returns manifest payload。
 */
function buildEvidencePayload(options: { exists: boolean } = { exists: true }) {
  if (!options.exists) {
    return {
      prd_path: ARCHIVED_PRD_PATH,
      prd_stem: 'P1-FEAT-20260101-archived',
      evidence_dir: 'tasks/evidence/P1-FEAT-20260101-archived',
      exists: false,
      files: [],
    }
  }
  return {
    prd_path: ARCHIVED_PRD_PATH,
    prd_stem: 'P1-FEAT-20260101-archived',
    evidence_dir: 'tasks/evidence/P1-FEAT-20260101-archived',
    exists: true,
    files: [
      {
        name: 'P1-FEAT-20260101-archived.evidence-report.md',
        size_bytes: 2048,
        media_type: 'text/markdown',
        role: 'evidence_report',
        artifact_token: encodePrdPath('P1-FEAT-20260101-archived.evidence-report.md'),
      },
      {
        name: 'P1-FEAT-20260101-archived.verifier-report.md',
        size_bytes: 1024,
        media_type: 'text/markdown',
        role: 'verifier_report',
        artifact_token: encodePrdPath('P1-FEAT-20260101-archived.verifier-report.md'),
      },
    ],
  }
}

/**
 * 装上 Backlog 相关 route stub，并记录写请求。
 *
 * @param page - Playwright 页面。
 * @param captures - 用于收集 start / PATCH 请求的容器。
 * @param options.prds - 列表响应（可为 none 时用默认）。
 */
async function installBacklogStubs(
  page: Page,
  captures: { starts: StartCapture[]; patches: StartCapture[] },
  options: { startedState?: Record<string, string> } = {},
) {
  const prdsPayloads = [buildPrdsPayload(), buildPrdsPayload(options.startedState)]
  let listCallCount = 0

  // 首屏仓库固定为 keda-main：cwd 匹配与 registry 兜底都指向同一个 repo，
  // 否则本地（console cwd = keda 仓库）与 CI 会选到不同的 repo_id。
  await page.route('**/console/context', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        cwd: '/Users/me/code/keda',
        git_root: '/Users/me/code/keda',
        repo_id: REPO_ID,
        status: 'matched',
        candidates: [],
      }),
    })
  })

  await page.route('**/api/v1/agent-runner/repositories', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repositories: [
          {
            repo_id: REPO_ID,
            path: '/Users/me/code/keda',
            enabled: true,
            display_name: 'Keda Main',
            path_exists: true,
          },
        ],
      }),
    })
  })

  await page.route('**/backlog/prds?*', async (route) => {
    const payload = prdsPayloads[Math.min(listCallCount, prdsPayloads.length - 1)]
    listCallCount += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    })
  })

  await page.route('**/backlog/settings?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repo_id: REPO_ID,
        max_parallel: 2,
        default_view: 'list',
        updated_at: '2026-09-16T00:00:00+00:00',
      }),
    })
  })

  await page.route('**/backlog/autopilot?*', async (route) => {
    const request = route.request()
    if (request.method() === 'PATCH') {
      captures.patches.push({ url: request.url(), body: request.postData() })
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          buildAutopilotPayload({ enabled: true, daemon_running: true, auto_merge_enabled: false }),
        ),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildAutopilotPayload()),
    })
  })

  await page.route('**/backlog/prds/*/evidence?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildEvidencePayload()),
    })
  })

  await page.route('**/backlog/prds/*/start', async (route) => {
    const request = route.request()
    captures.starts.push({ url: request.url(), body: request.postData() })
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        prd_path: decodeURIComponent(request.url().split('/prds/')[1] ?? '').split('/start')[0],
        issue_number: 101,
        state: 'ready',
        detail: '已建立 Issue 并加入队列。',
      }),
    })
  })
}

test.describe('realistic: backlog controls, evidence and autopilot', () => {
  test('rv-1 默认依赖图选中可启动 PRD 后走规范 start API', async ({ page }) => {
    const captures = { starts: [] as StartCapture[], patches: [] as StartCapture[] }
    await installBacklogStubs(page, captures, { startedState: { [STARTABLE_PRD_PATH]: 'running' } })

    await page.goto('/app/backlog')
    await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible()

    // 默认视图是依赖图，节点本身就应选入详情，不再替换整块画布。
    await page
      .locator(`[data-testid="prd-open-content"][data-prd-path="${STARTABLE_PRD_PATH}"]`)
      .click()

    const startButton = page.getByTestId('prd-detail-start')
    await expect(startButton).toBeVisible()
    await expect(startButton).toBeEnabled()

    await startButton.click()
    await expect(page.getByTestId('prd-detail-start')).toBeDisabled()

    // 唯一的启动请求必须打到既有 canonical 端点（路径由响应里的 prd_path 编码而来）。
    expect(captures.starts).toHaveLength(1)
    expect(captures.starts[0].url).toContain(
      `/api/v1/agent-runner/backlog/prds/${encodePrdPath(STARTABLE_PRD_PATH)}/start`,
    )
    expect(captures.starts[0].body).toContain(REPO_ID)

    // fresh-state probe：启动后必须重新拉取列表，而不是只弹本地 toast。
    await expect(page.getByTestId('prd-detail').locator('text=运行中')).toBeVisible()
  })

  test('已就绪 PRD 允许单点启动（定向执行，不再被就绪态禁用）', async ({ page }) => {
    const captures = { starts: [] as StartCapture[], patches: [] as StartCapture[] }
    await installBacklogStubs(page, captures, { startedState: { [READY_PRD_PATH]: 'running' } })

    await page.goto('/app/backlog')
    await page
      .locator(`[data-testid="prd-open-content"][data-prd-path="${READY_PRD_PATH}"]`)
      .first()
      .click()

    const startButton = page.getByTestId('prd-detail-start')
    await expect(startButton).toBeVisible()
    await expect(startButton).toBeEnabled()
    // 就绪态的可点理由是「立即定向执行」，而不是重复入队；悬停提示必须说清这一点。
    await expect(startButton).toHaveAttribute('title', /定向执行/)
    // 已在队列中的 PRD 不再提供「加入就绪」入口。
    await expect(page.getByTestId('prd-detail-enqueue-ready')).toHaveCount(0)

    await startButton.click()
    expect(captures.starts).toHaveLength(1)
    expect(captures.starts[0].url).toContain(
      `/api/v1/agent-runner/backlog/prds/${encodePrdPath(READY_PRD_PATH)}/start`,
    )
    expect(captures.starts[0].body).toContain(REPO_ID)

    // fresh-state probe：启动后按服务端最新状态刷新为运行中，按钮随之禁用。
    await expect(page.getByTestId('prd-detail').locator('text=运行中')).toBeVisible()
    await expect(page.getByTestId('prd-detail-start')).toBeDisabled()
  })

  test('rv-1 被依赖阻塞的 PRD 不允许绕过依赖门禁', async ({ page }) => {
    const captures = { starts: [] as StartCapture[], patches: [] as StartCapture[] }
    await installBacklogStubs(page, captures)

    await page.goto('/app/backlog')
    await page
      .locator(`[data-testid="prd-open-content"][data-prd-path="${BLOCKED_PRD_PATH}"]`)
      .click()

    await expect(page.getByTestId('prd-detail-block-reason')).toBeVisible()
    await expect(page.getByTestId('prd-detail-start')).toBeDisabled()
    expect(captures.starts).toHaveLength(0)
  })

  test('rv-1 三种视图共享同一详情与启动规则', async ({ page }) => {
    const captures = { starts: [] as StartCapture[], patches: [] as StartCapture[] }
    await installBacklogStubs(page, captures)

    await page.goto('/app/backlog')

    for (const viewName of ['依赖图', '时间轴', '列表'] as const) {
      await page.getByRole('button', { name: /视图：/ }).click()
      await page.getByRole('menuitemradio', { name: viewName }).click()
      await page
        .locator(`[data-testid="prd-open-content"][data-prd-path="${STARTABLE_PRD_PATH}"]`)
        .first()
        .click()
      await expect(page.getByTestId('prd-detail-start')).toBeVisible()
      await page.getByTestId('prd-detail-close').click()
      await expect(page.getByTestId('prd-detail')).toHaveCount(0)
    }
    expect(captures.starts).toHaveLength(0)
  })

  test('rv-3 已归档 PRD 的验收证据标签展示受限清单', async ({ page }) => {
    const captures = { starts: [] as StartCapture[], patches: [] as StartCapture[] }
    await installBacklogStubs(page, captures)

    await page.goto('/app/backlog')
    await page.getByLabel('显示已归档').check()
    await page
      .locator(`[data-testid="prd-open-content"][data-prd-path="${ARCHIVED_PRD_PATH}"]`)
      .first()
      .click()

    await page.getByTestId('prd-detail-tab-evidence').click()
    const evidenceBody = page.getByTestId('prd-evidence-body')
    await expect(evidenceBody).toBeVisible()
    // 数量与文件名来自 manifest；已归档 PRD 不能被当成「不可启动」之外的空白态。
    await expect(evidenceBody).toContainText('P1-FEAT-20260101-archived.evidence-report.md')
    await expect(evidenceBody).toContainText('P1-FEAT-20260101-archived.verifier-report.md')
    await expect(evidenceBody).toContainText('共 2 个文件')
  })

  test('rv-2 Backlog 自动推进开关走 PATCH 并如实显示运行条件', async ({ page }) => {
    const captures = { starts: [] as StartCapture[], patches: [] as StartCapture[] }
    await installBacklogStubs(page, captures)

    await page.goto('/app/backlog')
    const autopilotBar = page.getByTestId('backlog-autopilot')
    await expect(autopilotBar).toBeVisible()
    await expect(autopilotBar).toContainText('Backlog 自动推进')
    // 初始状态：daemon 未运行 + 自动合并未启用 → 必须显示降级文案而不是「全自动」。
    await expect(page.getByTestId('backlog-autopilot-daemon')).toContainText('Daemon 未运行')
    await expect(page.getByTestId('backlog-autopilot-auto-merge')).toContainText('自动合并未启用')

    // 开关是受控组件：勾选状态由写后读回的响应驱动，而不是浏览器本地切换。
    // 因此用 click() + toBeChecked() 断言，不用 check()——check() 会在点击后
    // 立刻校验 DOM 状态，异步响应还没回来时会误报「state 未改变」。
    const autopilotToggle = page.getByTestId('backlog-autopilot-toggle')
    await autopilotToggle.click()

    // 响应体来自写后读回：这里紧接着显示为已开启且 daemon 运行中。
    await expect(autopilotToggle).toBeChecked()
    await expect(page.getByTestId('backlog-autopilot-daemon')).toContainText('Daemon 运行中')
    expect(captures.patches).toHaveLength(1)
    expect(captures.patches[0].body).toContain('"enabled":true')
    // 自动合并状态不能被本开关连带改变。
    await expect(page.getByTestId('backlog-autopilot-auto-merge')).toContainText('自动合并未启用')
  })
})
