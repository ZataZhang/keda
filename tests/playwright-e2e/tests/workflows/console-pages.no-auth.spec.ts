/**
 * 管理终端四页 smoke（mock console API，不依赖真实 GitHub / runner）。
 *
 * 所有 /api 请求都被 page.route 拦截并返回固定 fixture，
 * 验证页面渲染、操作确认框与审计列表展示。
 *
 * Run with:
 *   playwright test --project=no-auth console-pages
 */
import { expect, test, type Page } from '@playwright/test'

const LOCAL_SESSION = {
  user_id: 'local-operator',
  display_name: 'tester',
  email: 'tester@localhost',
}

const MONITORING_OVERVIEW = {
  scanned_at: '2026-06-11T10:00:00+00:00',
  unreachable_repositories: [
    {
      repo_id: 'ghost',
      display_name: 'Ghost Repo',
      configured_path: '/missing/path',
      error: "Path '/missing/path' does not exist.",
    },
  ],
  repositories: [
    {
      repo_id: 'keda-main',
      display_name: 'Keda Main',
      enabled: true,
      base_branch: 'main',
      remote: 'zata',
      health: {
        gh_available: true,
        repo_path_exists: true,
        publish_remote_exists: true,
      },
      queue_counts: {
        ready: 0,
        running: 0,
        supervising: 0,
        review: 0,
        failed: 1,
        blocked: 0,
      },
      labels: {
        ready: 'agent/ready',
        running: 'agent/running',
        supervising: 'agent/supervising',
        review: 'agent/review',
        failed: 'agent/failed',
        blocked: 'agent/blocked',
      },
      issues: [
        {
          number: 19,
          title: 'Broken Issue',
          url: 'https://example.test/19',
          labels: ['agent/failed'],
          state: 'OPEN',
          primary_label: 'agent/failed',
          pr: null,
          worktree: {
            exists: false,
            path: '',
            branch: '',
            head_sha: '',
            is_clean: true,
            dirty_files: [],
          },
          timeline: [],
          latest_event: null,
          anomalies: [],
          suggested_cli_commands: [],
          has_anomaly: false,
          anomaly_types: [],
        },
      ],
      anomaly_count: 0,
      anomaly_summary: { warning: 0, error: 0 },
      scanned_at: '2026-06-11T10:00:00+00:00',
    },
  ],
}

/**
 * Dashboard 首屏现在读本地快照接口；这里把同一份 fixture 包装成快照响应，
 * 让"队列 + 完成度摘要 + 无法访问警示"三条断言仍走真实页面渲染路径。
 */
const MONITORING_SNAPSHOTS = {
  sync_status: 'ready',
  scanned_at: MONITORING_OVERVIEW.scanned_at,
  missing_repo_ids: [],
  unreachable_repositories: MONITORING_OVERVIEW.unreachable_repositories,
  repositories: MONITORING_OVERVIEW.repositories.map((repository) => ({
    repo_id: repository.repo_id,
    scanned_at: repository.scanned_at,
    overview: repository,
  })),
}

const COMPLETION_STATS = {  repositories: [
    {
      repo_id: 'keda-main',
      display_name: 'Keda Main',
      total_tracked: 10,
      completed: 7,
      failed: 2,
      blocked: 1,
      open_in_pipeline: 0,
      completion_rate: 0.7,
      truncated: false,
      error: null,
    },
  ],
}

const PROCESSES = {
  processes: [
    {
      process_id: 'abc123',
      repo_id: 'keda-main',
      kind: 'daemon',
      pid: 4242,
      status: 'running',
      exit_code: null,
      log_path: '/tmp/log',
      command: ['uv', 'run', 'iar', 'daemon', '--repo-id', 'keda-main'],
      started_at: '2026-06-11T10:00:00+00:00',
      stopped_at: null,
    },
  ],
}

const REGISTRY = {
  repositories: [
    {
      repo_id: 'keda-main',
      path: '/Users/me/code/keda',
      enabled: true,
      display_name: 'Keda Main',
      path_exists: true,
    },
  ],
}

const AUDITS = {
  audits: [
    {
      occurred_at: '2026-06-11T10:30:00+00:00',
      actor: 'console',
      action: 'retry_failed',
      repo_id: 'keda-main',
      issue_number: 19,
      params_json: '{}',
      result: 'accepted',
      detail: "Issue #19: 'agent/failed' -> 'agent/ready'.",
    },
  ],
}

/** 目录选择器 mock 用的假目录树：根 → code → foo。 */
const BROWSE_TREES: Record<string, string[]> = {
  '/Users/me': ['code'],
  '/Users/me/code': ['foo'],
  '/Users/me/code/foo': [],
}

/** 按请求路径构造目录选择器的 mock 响应（不传 path 时从主目录开始）。 */
function browseTreeFor(requestedPath: string | null) {
  const resolvedPath = requestedPath ?? '/Users/me'
  const parentIndex = resolvedPath.lastIndexOf('/')
  const directoryName = resolvedPath.split('/').pop() ?? 'repository'
  return {
    path: resolvedPath,
    parent: parentIndex > 0 ? resolvedPath.slice(0, parentIndex) : null,
    home: '/Users/me',
    suggested_repo_id: directoryName,
    suggested_display_name: directoryName,
    directories: (BROWSE_TREES[resolvedPath] ?? []).map((name) => ({
      name,
      path: `${resolvedPath}/${name}`,
      is_git_repo: name === 'foo',
      has_iar_config: name === 'foo',
      already_registered: false,
      suggested_repo_id: name,
    })),
  }
}

async function mockConsoleApi(page: Page): Promise<void> {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({ json: LOCAL_SESSION }),
  )
  await page.route('**/api/v1/agent-runner/overview', (route) =>
    route.fulfill({ json: MONITORING_OVERVIEW }),
  )
  await page.route('**/api/v1/agent-runner/overview/snapshots', (route) =>
    route.fulfill({ json: MONITORING_SNAPSHOTS }),
  )
  await page.route('**/api/v1/agent-runner/console/stats/overview', (route) =>
    route.fulfill({ json: COMPLETION_STATS }),
  )
  await page.route('**/api/v1/agent-runner/console/stats/history**', (route) =>
    route.fulfill({
      json: { repo_id: null, days: 30, trend: [] },
    }),
  )
  await page.route('**/api/v1/agent-runner/console/runs**', (route) =>
    route.fulfill({ json: { runs: [] } }),
  )
  await page.route('**/api/v1/agent-runner/console/processes', (route) =>
    route.fulfill({ json: PROCESSES }),
  )
  await page.route('**/api/v1/agent-runner/repositories', (route) =>
    route.fulfill({ json: REGISTRY }),
  )
  await page.route('**/api/v1/agent-runner/repositories/browse**', (route) => {
    const requestedPath = new URL(route.request().url()).searchParams.get('path')
    return route.fulfill({ json: browseTreeFor(requestedPath) })
  })
  await page.route('**/api/v1/agent-runner/console/audit**', (route) =>
    route.fulfill({ json: AUDITS }),
  )
}

test.describe('console pages smoke (mocked API)', () => {
  test('dashboard shows queue, completion summary and unreachable warning', async ({
    page,
  }) => {
    await mockConsoleApi(page)
    await page.goto('/app/dashboard')
    await expect(
      page.getByRole('heading', { name: 'Agent Runner 管理终端' }),
    ).toBeVisible()
    await expect(page.getByText('完成率 70%')).toBeVisible()
    await expect(page.getByText('个已注册仓库无法访问')).toBeVisible()
    // failed Issue 选中后出现「重试」操作条。
    await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
  })

  test('retry action asks for confirmation and posts whitelisted action', async ({
    page,
  }) => {
    await mockConsoleApi(page)
    let actionRequestBody: unknown = null
    await page.route(
      '**/api/v1/agent-runner/console/repositories/keda-main/issues/19/actions',
      (route) => {
        actionRequestBody = route.request().postDataJSON()
        return route.fulfill({
          json: {
            action: 'retry_failed',
            result: 'accepted',
            detail: "Issue #19: 'agent/failed' -> 'agent/ready'.",
            process: null,
          },
        })
      },
    )

    // 第一次 dismiss 确认框 → 不应发请求。
    page.once('dialog', (dialog) => void dialog.dismiss())
    await page.goto('/app/dashboard')
    await page.getByRole('button', { name: '重试' }).click()
    expect(actionRequestBody).toBeNull()

    // 第二次 accept 确认框 → 发送白名单动作。
    page.once('dialog', (dialog) => void dialog.accept())
    await page.getByRole('button', { name: '重试' }).click()
    await expect
      .poll(() => actionRequestBody, { timeout: 5_000 })
      .toEqual({ action: 'retry_failed' })
  })

  test('processes page lists managed processes', async ({ page }) => {
    await mockConsoleApi(page)
    await page.goto('/app/processes')
    await expect(page.getByRole('heading', { name: '托管进程' })).toBeVisible()
    await expect(page.getByText('4242')).toBeVisible()
    await expect(page.getByRole('button', { name: '停止' })).toBeVisible()
  })

  test('stats page shows completion table', async ({ page }) => {
    await mockConsoleApi(page)
    await page.goto('/app/stats')
    await expect(page.getByRole('heading', { name: '完成度统计' })).toBeVisible()
    await expect(page.getByText('70%')).toBeVisible()
  })

  test('repositories page shows registry and audit log', async ({ page }) => {
    await mockConsoleApi(page)
    await page.goto('/app/repositories')
    await expect(page.getByRole('heading', { name: '项目接入' })).toBeVisible()
    await expect(page.getByText('/Users/me/code/keda')).toBeVisible()
    await expect(page.getByText('retry_failed')).toBeVisible()
  })

  test('add repository picks a directory instead of typing a path', async ({
    page,
  }) => {
    await mockConsoleApi(page)
    await page.goto('/app/repositories')

    await page.getByTestId('repositories-add-path-picker').click()
    await expect(page.getByTestId('directory-picker-dialog')).toBeVisible()

    // 逐级下钻：主目录 → code → foo。
    await page.getByTestId('directory-picker-entry-code').click()
    await page.getByTestId('directory-picker-entry-foo').click()
    await expect(page.getByTestId('directory-picker-current-path')).toContainText(
      '/Users/me/code/foo',
    )

    await page.getByTestId('directory-picker-confirm').click()

    await expect(page.getByTestId('directory-picker-dialog')).toBeHidden()
    await expect(
      page.getByPlaceholder('本地路径，如 /Users/me/code/foo'),
    ).toHaveValue('/Users/me/code/foo')
    // repo_id 与显示名按目录名自动补全。
    await expect(page.getByPlaceholder('repo_id（小写-连字符）')).toHaveValue(
      'foo',
    )
    await expect(page.getByPlaceholder('显示名（可选）')).toHaveValue('foo')
  })
})
