/**
 * Realistic validation for the single-Issue verifier human-takeover card.
 *
 * 验证层级：**真实入口**。走 `/app/dashboard` 的真实 React 组件树（快照首屏 →
 * 点 Issue → IssueDetail → VerifierTakeoverCard → Radix 确认对话框 → 轮询交班回读），
 * 只在浏览器边界 stub 后端只读快照与接管动作端点，绝不注入组件内部状态、绝不
 * 用 component preview 冒充真实流程。接管面板是 Issue #278「运行中单 Issue 人工
 * 接管」的操作面；本 spec 覆盖确认、等待、结果呈递、失败态与桌面/窄视口布局。
 *
 * 前置依赖（本环境不执行、仅交付代码）：
 *   - `just e2e`（或 `pnpm -C tests/playwright-e2e test`）会先经 `setup` 项目登录并
 *     写入 `.auth/session.json`，`chromium` 项目带 storageState 打开真实 Dashboard；
 *   - 前端由 `just run` / dev server 在 `getBaseUrl()` 指向的端口提供。
 * 若没有可登录的完整栈，本用例无法运行——这是浏览器层的诚实边界，交班语义本身
 * 由 `tasks/evidence/.../scripts/rv*.py` 的真实跨进程 oracle 与后端契约测试覆盖。
 *
 * 注意：接管动作端点返回的 HTTP 语义即后端 `TAKEOVER_HTTP_STATUS` 映射
 * （202 处理中 / 200 已交班 / 409 目标已变化），此处按真实契约 stub。
 */

import { expect, test } from '../../fixtures/session.fixture'
import {
  AgentRunnerMonitorPage,
  type MonitoringRepositoryOverview,
  type MonitorSnapshotsResponse,
} from '../../page-objects/AgentRunnerMonitorPage'

const RUN_ID = 'run-2f15-0001'
const HEAD_SHA = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'
const TAKEOVER_ACTION_ENDPOINT =
  /\/api\/v1\/agent-runner\/console\/repositories\/[^/]+\/issues\/\d+\/actions$/
const ISSUE_DETAIL_ENDPOINT = /\/api\/v1\/agent-runner\/issues\/\d+$/

/** 活动 verifier 投影，字段与后端 `active_verifier_projection` 同构。 */
type ActiveVerifierProjection = {
  run_id: string
  head_sha: string
  state:
    | 'running'
    | 'takeover_requested'
    | 'taken_over'
    | 'takeover_failed'
    | 'verifier_completed'
    | 'verifier_stale'
    | 'pr_merged'
    | 'expired'
  can_take_over: boolean
  pr_number: number | null
  autopilot_hold: boolean
}

/** 只带仓库行与 Issue 按钮的快照，用于渲染可点击的列表项。 */
const SNAPSHOT_WITH_ISSUE: MonitorSnapshotsResponse = {
  repositories: [
    {
      repo_id: 'zata/keda-test',
      scanned_at: '2026-05-24T12:00:00+00:00',
      overview: {
        repo_id: 'zata/keda-test',
        display_name: 'Keda Test',
        enabled: true,
        base_branch: 'main',
        remote: 'origin',
        health: {
          gh_available: true,
          repo_path_exists: true,
          publish_remote_exists: true,
        },
        queue_counts: {
          ready: 0,
          running: 1,
          supervising: 0,
          review: 0,
          failed: 0,
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
            number: 100,
            title: 'Takeover target issue',
            url: 'https://github.com/zata/keda-test/issues/100',
            labels: ['agent/running', 'source/prd'],
            state: 'running',
            primary_label: 'agent/running',
            pr: {
              number: 101,
              url: 'https://github.com/zata/keda-test/pull/101',
              branch: 'issue-100',
              head_sha: HEAD_SHA,
              base_sha: 'b2c3d4e5f60718293a4b5c6d7e8f9012345678ab',
              mergeable: true,
              checks_state: 'PENDING',
              checks_summary: [],
            },
            worktree: {
              exists: true,
              path: '/tmp/wt-issue-100',
              branch: 'issue-100',
              head_sha: HEAD_SHA,
              is_clean: true,
              dirty_files: [],
            },
            timeline: [],
            latest_event: null,
            anomalies: [],
            suggested_cli_commands: [],
            has_anomaly: false,
            anomaly_types: [],
          } satisfies MonitoringRepositoryOverview['issues'][number],
        ],
        anomaly_count: 0,
        anomaly_summary: { warning: 0, error: 0 },
        scanned_at: '2026-05-24T12:00:00+00:00',
      },
    },
  ],
  missing_repo_ids: [],
  sync_status: 'ready',
  scanned_at: '2026-05-24T12:00:00+00:00',
  unreachable_repositories: [],
}

/** 组一份完整 Issue 详情：接管卡靠 fresh read 拿到 active_verifier。 */
function buildIssueDetail(activeVerifier: ActiveVerifierProjection): unknown {
  return {
    number: 100,
    title: 'Takeover target issue',
    url: 'https://github.com/zata/keda-test/issues/100',
    labels: ['agent/running', 'source/prd'],
    state: 'running',
    primary_label: 'agent/running',
    pr: SNAPSHOT_WITH_ISSUE.repositories[0].overview.issues[0].pr,
    worktree: SNAPSHOT_WITH_ISSUE.repositories[0].overview.issues[0].worktree,
    timeline: [],
    latest_event: null,
    anomalies: [],
    suggested_cli_commands: [],
    has_anomaly: false,
    anomaly_types: [],
    active_verifier: activeVerifier,
  }
}

/** 一次接管测试的编排状态：GET 详情随接管推进，POST 记录被发送的载荷。 */
type TakeoverHarness = {
  posts: Array<Record<string, unknown>>
  pollCount: number
}

/**
 * stub 快照、Issue 详情（随接管轮询推进）与接管动作端点。
 *
 * @param page - Playwright 页面对象。
 * @param monitor - 复用的监控 Dashboard 页面对象。
 * @param outcome - 接管动作返回语义：requested（202 处理中）、handed_over（200
 *   立即交班）、conflict（409 HEAD 变化）。
 */
async function stubTakeoverFlow(
  page: import('@playwright/test').Page,
  monitor: AgentRunnerMonitorPage,
  outcome: 'requested' | 'handed_over' | 'conflict',
): Promise<TakeoverHarness> {
  await monitor.mockSnapshots(SNAPSHOT_WITH_ISSUE)
  const harness: TakeoverHarness = { posts: [], pollCount: 0 }

  // 接管登记后，控制行随 runner 确认而前进；详情读到的状态必须离开
  // running / takeover_requested，交班才落定为 taken_over。
  await page.route(ISSUE_DETAIL_ENDPOINT, async (route) => {
    let verifier: ActiveVerifierProjection
    if (outcome === 'conflict') {
      // HEAD 漂移：后端 409，控制行始终停在 running，绝不误呈交班。
      verifier = {
        run_id: RUN_ID,
        head_sha: HEAD_SHA,
        state: 'running',
        can_take_over: true,
        pr_number: 101,
        autopilot_hold: false,
      }
    } else if (outcome === 'handed_over') {
      // 首读 running（按钮可用）；POST 返回 200 后 reload 直接落定 taken_over。
      verifier = {
        run_id: RUN_ID,
        head_sha: HEAD_SHA,
        state: harness.pollCount === 0 ? 'running' : 'taken_over',
        can_take_over: harness.pollCount === 0,
        pr_number: 101,
        autopilot_hold: harness.pollCount > 0,
      }
    } else {
      // requested：首读 running → 确认后 reload 呈 takeover_requested（"正在停止"）
      // → 轮询回读落定 taken_over。状态序列必须离开 running 交班才算完成。
      verifier = {
        run_id: RUN_ID,
        head_sha: HEAD_SHA,
        state:
          harness.pollCount === 0
            ? 'running'
            : harness.pollCount === 1
              ? 'takeover_requested'
              : 'taken_over',
        can_take_over: harness.pollCount === 0,
        pr_number: 101,
        autopilot_hold: harness.pollCount >= 2,
      }
    }
    harness.pollCount += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildIssueDetail(verifier)),
    })
  })

  await page.route(TAKEOVER_ACTION_ENDPOINT, async (route) => {
    harness.posts.push(route.request().postDataJSON() as Record<string, unknown>)
    if (outcome === 'conflict') {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'takeover outcome=binding_mismatch; observed_state=running',
        }),
      })
      return
    }
    if (outcome === 'handed_over') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          action: 'take_over_verifier',
          result: 'accepted',
          detail: 'takeover outcome=handed_over; observed_state=taken_over',
          process: null,
          takeover_http_status: 200,
          takeover_outcome: 'handed_over',
        }),
      })
      return
    }
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        action: 'take_over_verifier',
        result: 'accepted',
        detail: 'takeover outcome=accepted; observed_state=takeover_requested',
        process: null,
        takeover_http_status: 202,
        takeover_outcome: 'accepted',
      }),
    })
  })

  return harness
}

test.describe('workflows: verifier human takeover', () => {
  test.describe.configure({ mode: 'serial' })

  test('desktop 1440×900: confirm → requested → handover settled, Draft PR link offered', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const monitor = new AgentRunnerMonitorPage(page)
    const harness = await stubTakeoverFlow(page, monitor, 'requested')

    await monitor.goto()
    await monitor.expectHeading()
    await monitor.openIssue(100)

    await expect(page.getByTestId('verifier-takeover-card')).toBeVisible()
    const requestButton = page.getByTestId('verifier-takeover-request')
    await expect(requestButton).toBeEnabled()

    await requestButton.click()
    await expect(page.getByTestId('verifier-takeover-confirm')).toBeVisible()
    await expect(
      page.getByText(
        '只停止此 Issue 当前 verifier。PR 会保持 Draft；取消不会被记为验证通过，也不会自动合并。',
        { exact: false },
      ),
    ).toBeVisible()

    await page.getByTestId('verifier-takeover-confirm').click()

    // 请求必须原样携带详情读到的 run_id / head_sha，界面绝不自行构造。
    await expect.poll(() => harness.posts.length).toBe(1)
    expect(harness.posts[0]).toMatchObject({
      action: 'take_over_verifier',
      run_id: RUN_ID,
      head_sha: HEAD_SHA,
    })

    // 处理中呈递"正在停止"，交班落定后展示"已交给你审查"与打开 Draft PR。
    await expect(page.getByTestId('verifier-takeover-status')).toBeVisible()
    await expect(page.getByTestId('verifier-takeover-open-pr')).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page.getByText('已交给你审查 · verifier 未完成', { exact: false }),
    ).toBeVisible()
  })

  test('handed over immediately (200): no lingering "stopping" state', async ({ page }) => {
    const monitor = new AgentRunnerMonitorPage(page)
    await stubTakeoverFlow(page, monitor, 'handed_over')

    await monitor.goto()
    await monitor.openIssue(100)
    await expect(page.getByTestId('verifier-takeover-card')).toBeVisible()

    await page.getByTestId('verifier-takeover-request').click()
    await page.getByTestId('verifier-takeover-confirm').click()

    await expect(page.getByTestId('verifier-takeover-open-pr')).toBeVisible()
    await expect(
      page.getByText('已交给你审查 · verifier 未完成', { exact: false }),
    ).toBeVisible()
  })

  test('failure state (409 HEAD drift): surfaces conflict, never claims handover', async ({
    page,
  }) => {
    const monitor = new AgentRunnerMonitorPage(page)
    await stubTakeoverFlow(page, monitor, 'conflict')

    await monitor.goto()
    await monitor.openIssue(100)
    await expect(page.getByTestId('verifier-takeover-card')).toBeVisible()

    await page.getByTestId('verifier-takeover-request').click()
    await page.getByTestId('verifier-takeover-confirm').click()

    await expect(page.getByTestId('verifier-takeover-error')).toBeVisible()
    await expect(page.getByTestId('verifier-takeover-error')).toContainText(
      'HEAD 已变化',
    )
    // 失败态绝不出现交班确认与 Draft PR 链接，避免误导操作员。
    await expect(page.getByTestId('verifier-takeover-open-pr')).toHaveCount(0)
  })

  test('narrow 390×844: card and request remain usable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const monitor = new AgentRunnerMonitorPage(page)
    await stubTakeoverFlow(page, monitor, 'requested')

    await monitor.goto()
    await monitor.expectHeading()
    await monitor.openIssue(100)

    const card = page.getByTestId('verifier-takeover-card')
    await expect(card).toBeVisible()
    await expect(card).toBeInViewport()
    await expect(page.getByTestId('verifier-takeover-request')).toBeVisible()
  })
})
