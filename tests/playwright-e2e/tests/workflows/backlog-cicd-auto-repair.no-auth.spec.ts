/**
 * Backlog CI/CD 观察与自动修复控制（UI 真实 + Backlog API stub）。
 *
 * 验证层级：**UI 真实 + Backlog API stub**。浏览器、Next.js 页面、API client、
 * PRD 详情的 CI/CD 标签与仓库级 CI 控件全部真实运行；只有 `/backlog/**` 的后端响应
 * 被 Playwright route 替换为确定性数据，用来稳定复现「CI 失败 / 策略关闭 / 上限用尽」
 * 三种形态。policy 与 repair 请求不由测试伪造成功结果——测试捕获 URL 与请求体本身
 * 作为判据（前端只提交 PRD 路径与三态策略，绝不提交 head SHA 或轮次）。
 *
 * effective 策略、marker 解析、轮次计数与修复门禁都在服务端计算，浏览器侧不重复实现：
 * 那条链路分别由 tests/test_backlog_ci_delivery.py（core）与
 * tests/test_backlog_api.py（真实 FastAPI + 真实 .iar.toml）覆盖。
 *
 * Run with:
 *   playwright test --project=no-auth backlog-cicd-auto-repair
 */
import { expect, test, type Page, type Route } from '@playwright/test'

const REPO_ID = 'keda-main'
const PRD_PATH = 'tasks/pending/P1-FEAT-20260101-ci.md'
const HEAD_A = 'a'.repeat(40)
const BACKLOG_BASE = '**/api/v1/agent-runner/backlog'

/**
 * base64url 编码 PRD 路径，与前端 `encodePrdPath` 同规则。
 *
 * @param prdPath - PRD 仓库相对路径。
 * @returns URL 安全片段。
 */
function encodePrdPath(prdPath: string): string {
  return Buffer.from(prdPath, 'utf-8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_')
}

const ENCODED_PRD_PATH = encodePrdPath(PRD_PATH)

/** 仓库级 CI 自动修复状态；`persisted_auto_repair` 为 null 表示没写进 .iar.toml。 */
function ciState(overrides: Record<string, unknown> = {}) {
  return {
    repo_id: REPO_ID,
    auto_repair_ci: false,
    max_repair_attempts: 2,
    config_source: '.iar.toml',
    persisted_auto_repair: null,
    ...overrides,
  }
}

/** 单个 PRD 的 CI/CD 投影：失败态 + 两条问题 + 策略三值分开。 */
function ciDelivery(overrides: Record<string, unknown> = {}) {
  return {
    prd_path: PRD_PATH,
    status: 'failing',
    pr_number: 7,
    pr_url: 'https://example.test/pull/7',
    pr_branch: 'issue-7',
    head_sha: HEAD_A,
    checks_state: 'FAILURE',
    problems: [
      {
        name: 'unit-tests',
        detail: 'status=COMPLETED, conclusion=FAILURE',
        kind: 'check_failure',
        url: 'https://example.test/runs/1',
        head_sha: HEAD_A,
        round_index: null,
      },
      {
        name: 'lint',
        detail: 'status=IN_PROGRESS',
        kind: 'check_pending',
        url: null,
        head_sha: HEAD_A,
        round_index: null,
      },
    ],
    repair_rounds: 1,
    max_repair_attempts: 2,
    repair_exhausted: false,
    stored_policy: 'inherit',
    global_auto_repair: false,
    effective_auto_repair: false,
    policy_source: '仓库全局',
    last_decision: 'ci_repair_policy_off',
    failure_key: '806efbaeea1d0a7e',
    supervisor_action: null,
    supervisor_summary: null,
    last_synced_at: '2026-10-05T10:00:00+00:00',
    detail: '自动修复未开启；问题保留在详情中，不会启动修复 Agent。',
    ...overrides,
  }
}

/** 列表项：CI/CD 标签只对已关联 Issue 的 PRD 出现。 */
function backlogPrd(delivery: Record<string, unknown>) {
  return {
    prd_path: PRD_PATH,
    title: 'CI 监控 PRD',
    status: 'pending',
    priority: 'P1',
    issue_url: 'https://example.test/issues/7',
    issue_number: 7,
    state: 'supervising',
    acceptance_total: 2,
    acceptance_checked: 1,
    delivery_dependencies: [],
    updated_at: '2026-10-05T10:00:00+00:00',
    block_reason: null,
    next_action: null,
    ci_delivery: delivery,
  }
}

type CapturedRequest = {
  method: string
  url: string
  body: string | null
}

/**
 * 装好 Backlog 页所需的全部 stub，并记录 policy / repair / 仓库级开关的写请求。
 *
 * @param page - Playwright 页面对象。
 * @param options - 投影与状态的可控变体。
 * @returns 写请求捕获列表。
 */
async function mockBacklogSurface(
  page: Page,
  options: {
    delivery?: Record<string, unknown>
    ciSettings?: Record<string, unknown>
    policyResponse?: Record<string, unknown>
    repairStatus?: number
    repairResponse?: Record<string, unknown>
  } = {}
): Promise<CapturedRequest[]> {
  const delivery = ciDelivery(options.delivery ?? {})
  const settings = ciState(options.ciSettings ?? {})
  const captures: CapturedRequest[] = []

  const record = (route: Route): CapturedRequest => {
    const captured = {
      method: route.request().method(),
      url: route.request().url(),
      body: route.request().postData(),
    }
    captures.push(captured)
    return captured
  }

  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { user_id: 'local-operator', display_name: 'tester', email: 'tester@localhost' },
    }),
  )
  await page.route('**/api/v1/agent-runner/repositories', (route) =>
    route.fulfill({
      json: {
        repositories: [
          {
            repo_id: REPO_ID,
            path: '/repo',
            enabled: true,
            display_name: 'Keda Main',
            path_exists: true,
          },
        ],
        discovered: [],
      },
    }),
  )
  await page.route(`${BACKLOG_BASE}/prds?**`, (route) =>
    route.fulfill({
      json: {
        prds: [backlogPrd(delivery)],
        repo_id: REPO_ID,
        include_archived: false,
        scanned_at: '2026-10-05T10:00:00+00:00',
      },
    }),
  )
  await page.route(`${BACKLOG_BASE}/settings**`, (route) =>
    route.fulfill({
      json: {
        repo_id: REPO_ID,
        max_parallel: 2,
        default_view: 'list',
        updated_at: '2026-10-05T10:00:00+00:00',
      },
    }),
  )
  await page.route(`${BACKLOG_BASE}/autopilot**`, (route) =>
    route.fulfill({
      json: {
        repo_id: REPO_ID,
        enabled: false,
        auto_merge_enabled: false,
        daemon_running: false,
        max_parallel: 2,
        config_source: '.iar.toml',
        persisted_enabled: false,
      },
    }),
  )

  // 仓库级 CI 开关：GET 读初始态，PATCH 捕获写请求后按服务端语义回写新状态。
  await page.route(`${BACKLOG_BASE}/ci**`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: settings })
      return
    }
    const captured = record(route)
    const requestedBody = captured.body ? JSON.parse(captured.body) : {}
    await route.fulfill({
      json: ciState({
        ...settings,
        auto_repair_ci: Boolean(requestedBody.auto_repair_ci),
        persisted_auto_repair: Boolean(requestedBody.auto_repair_ci),
      }),
    })
  })

  // 单 PRD：GET 读投影，PATCH 写三态策略，POST 发起一次手动修复。
  await page.route(`${BACKLOG_BASE}/prds/${ENCODED_PRD_PATH}/ci**`, async (route) => {
    const method = route.request().method()
    const requestUrl = route.request().url()

    if (method === 'GET') {
      await route.fulfill({ json: { ci_delivery: delivery, reason: '' } })
      return
    }
    if (method === 'PATCH' || requestUrl.includes('/ci-policy')) {
      const captured = record(route)
      const requestedBody = captured.body ? JSON.parse(captured.body) : {}
      await route.fulfill({
        json:
          options.policyResponse ??
          {
            stored_policy: requestedBody.policy,
            ci_delivery: ciDelivery({
              ...delivery,
              stored_policy: requestedBody.policy,
              effective_auto_repair:
                requestedBody.policy === 'on'
                  ? true
                  : requestedBody.policy === 'off'
                    ? false
                    : Boolean(settings.auto_repair_ci),
            }),
          },
      })
      return
    }
    record(route)
    if (options.repairStatus && options.repairStatus >= 400) {
      await route.fulfill({
        status: options.repairStatus,
        json: options.repairResponse ?? {
          detail: '已达修复上限 max_repair_attempts=2，需要人工处理。',
        },
      })
      return
    }
    await route.fulfill({
      status: 200,
      json: options.repairResponse ?? {
        prd_path: PRD_PATH,
        accepted: true,
        decision: 'ci_repair_allowed',
        failure_key: '806efbaeea1d0a7e',
        head_sha: HEAD_A,
        detail: '已请求第 2 轮修复。',
      },
    })
  })

  return captures
}

/** 打开 CI 监控 PRD 的详情并切到 CI/CD 标签。 */
async function openCiTab(page: Page): Promise<void> {
  await page.goto('/app/backlog')
  await page.getByText('CI 监控 PRD').first().click()
  await expect(page.getByTestId('prd-detail')).toBeVisible()
  await page.getByTestId('prd-detail-tab-ci').click()
  await expect(page.getByTestId('prd-ci-view')).toBeVisible()
}

test.describe('backlog CI/CD 观察与自动修复控制（mocked Backlog API）', () => {
  test('失败态把原始 checks、轮次与三态策略分开呈现，且明说不会自动修复', async ({ page }) => {
    await mockBacklogSurface(page)
    await openCiTab(page)

    const view = page.getByTestId('prd-ci-view')
    await expect(view).toHaveAttribute('data-prd-ci-status', 'failing')
    await expect(page.getByTestId('prd-ci-head')).toContainText(HEAD_A.slice(0, 8))
    await expect(page.getByTestId('prd-ci-checks-state')).toContainText('FAILURE')

    // 原始问题逐条列出（含 GitHub 链接），不塌缩成"CI 失败"一句话。
    const problems = page.getByTestId('prd-ci-problem')
    await expect(problems).toHaveCount(2)
    await expect(problems.first()).toContainText('unit-tests')
    await expect(page.getByTestId('prd-ci-problem-link')).toBeVisible()

    // 三值并列：持久=跟随全局、全局=关闭 → 生效=关闭。前端不自行推断 effective。
    await expect(page.getByTestId('prd-ci-policy-effective')).toContainText('仓库全局：关闭')
    await expect(page.getByTestId('prd-ci-effective-value')).toContainText('自动修复关闭')
    await expect(page.getByTestId('prd-ci-rounds')).toContainText('1')
    await expect(page.getByTestId('prd-ci-detail')).toContainText('自动修复未开启')

    // 未开启时仍然给一次性手动修复入口（显式动作不受全局开关约束）。
    await expect(page.getByTestId('prd-ci-repair')).toBeVisible()
  })

  test('跟随全局不等于关闭：切到 off 只提交策略三态', async ({ page }) => {
    // 起点是"跟随全局 + 全局开启"→ 生效开启，切到 off 才会看到生效翻转。
    const captures = await mockBacklogSurface(page, {
      delivery: ciDelivery({ global_auto_repair: true, effective_auto_repair: true }),
      ciSettings: ciState({ auto_repair_ci: true, persisted_auto_repair: true }),
    })
    await openCiTab(page)
    await expect(page.getByTestId('prd-ci-effective-value')).toContainText('自动修复开启')

    await page.getByTestId('prd-ci-policy-off').click()
    await expect(page.getByTestId('prd-ci-effective-value')).toContainText('自动修复关闭')

    const policyWrite = captures.find((entry) => entry.url.includes('/ci-policy'))
    expect(policyWrite).toBeDefined()
    expect(policyWrite?.method).toBe('PATCH')
    const body = JSON.parse(policyWrite?.body ?? '{}')
    expect(body).toEqual({ repo_id: REPO_ID, policy: 'off' })

    // 切回 inherit：请求体仍然只有 PRD 身份 + 三态值，生效值回到全局值。
    await page.getByTestId('prd-ci-policy-inherit').click()
    await expect(page.getByTestId('prd-ci-effective-value')).toContainText('自动修复开启')
    const inheritWrite = captures.filter((entry) => entry.url.includes('/ci-policy')).at(-1)
    expect(JSON.parse(inheritWrite?.body ?? '{}')).toEqual({ repo_id: REPO_ID, policy: 'inherit' })
  })

  test('手动修复只提交 PRD 身份：请求里没有 head 也没有轮次', async ({ page }) => {
    const captures = await mockBacklogSurface(page)
    await openCiTab(page)

    await page.getByTestId('prd-ci-repair').click()
    await expect(page.getByTestId('prd-ci-repair-success')).toContainText('第 2 轮')

    const repairWrite = captures.find((entry) => entry.url.includes('/ci-repair'))
    expect(repairWrite?.method).toBe('POST')
    expect(repairWrite?.url).toContain(`repo_id=${REPO_ID}`)
    expect(repairWrite?.body ?? null).toBeNull()
    expect(repairWrite?.url).not.toContain(HEAD_A)
  })

  test('服务端拒绝时（409）把原因原样展示，而不是本地猜测', async ({ page }) => {
    await mockBacklogSurface(page, {
      delivery: ciDelivery({ repair_rounds: 2, repair_exhausted: true }),
      repairStatus: 409,
    })
    await openCiTab(page)

    await expect(page.getByTestId('prd-ci-exhausted')).toBeVisible()
    await page.getByTestId('prd-ci-repair').click()
    await expect(page.getByTestId('prd-ci-action-error')).toContainText('max_repair_attempts=2')
  })

  test('仓库级开关单独 PATCH，回写后按服务端快照渲染', async ({ page }) => {
    const captures = await mockBacklogSurface(page)
    await page.goto('/app/backlog')

    await expect(page.getByTestId('backlog-ci-control')).toBeVisible()
    await expect(page.getByTestId('backlog-ci-unpersisted')).toBeVisible()
    await page.getByTestId('backlog-ci-auto-repair-toggle').click()

    const ciWrite = captures.find((entry) => entry.url.includes('/backlog/ci?'))
    expect(ciWrite?.method).toBe('PATCH')
    expect(JSON.parse(ciWrite?.body ?? '{}')).toEqual({ repo_id: REPO_ID, auto_repair_ci: true })
    await expect(page.getByTestId('backlog-ci-auto-repair-toggle')).toBeChecked()
  })

  test('没有关联 Issue 的 PRD 不出现 CI/CD 标签', async ({ page }) => {
    await mockBacklogSurface(page)
    await page.route(`${BACKLOG_BASE}/prds?**`, (route) =>
      route.fulfill({
        json: {
          prds: [
            {
              ...backlogPrd(ciDelivery()),
              issue_number: null,
              issue_url: null,
              state: 'not_started',
              ci_delivery: null,
            },
          ],
          repo_id: REPO_ID,
          include_archived: false,
          scanned_at: '2026-10-05T10:00:00+00:00',
        },
      }),
    )
    await page.goto('/app/backlog')
    await page.getByText('CI 监控 PRD').first().click()
    await expect(page.getByTestId('prd-detail')).toBeVisible()
    await expect(page.getByTestId('prd-detail-tab-ci')).toHaveCount(0)
  })
})
