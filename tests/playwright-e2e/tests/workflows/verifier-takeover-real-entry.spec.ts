import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { expect, test } from '@playwright/test'
import { AgentRunnerMonitorPage } from '../../page-objects/AgentRunnerMonitorPage'

const EVIDENCE_DIRECTORY = process.env.RV1_EVIDENCE_DIR
  ? resolve(process.env.RV1_EVIDENCE_DIR)
  : null
const ISSUE_DETAIL_PATTERN = /\/api\/v1\/agent-runner\/issues\/278\/?(?:\?.*)?$/
const ISSUE_ACTION_PATTERN =
  /\/api\/v1\/agent-runner\/console\/repositories\/[^/]+\/issues\/278\/actions\/?$/

test.use({ trace: 'on' })

test('真实 Dashboard 接管运行中的 verifier 并回读交班状态', async ({ page }) => {
  test.skip(
    process.env.RV1_LIVE_E2E !== '1',
    '需要 rv1_browser_runner_e2e.py 启动的隔离 API、runner、GitHub 替身与真实页面。',
  )
  test.setTimeout(120_000)

  if (!EVIDENCE_DIRECTORY) {
    throw new Error('RV1_EVIDENCE_DIR is required for live acceptance evidence.')
  }

  const detailStates: string[] = []
  const verifierBindings: Array<{ runId: string; headSha: string }> = []
  page.on('response', async (response) => {
    if (!ISSUE_DETAIL_PATTERN.test(response.url()) || response.status() !== 200) {
      return
    }
    try {
      const body = (await response.json()) as {
        active_verifier?: {
          state?: string
          run_id?: string
          head_sha?: string
        } | null
      }
      if (body.active_verifier?.state) {
        detailStates.push(body.active_verifier.state)
      }
      if (body.active_verifier?.run_id && body.active_verifier.head_sha) {
        verifierBindings.push({
          runId: body.active_verifier.run_id,
          headSha: body.active_verifier.head_sha,
        })
      }
    } catch {
      // 页面导航或关闭时的取消响应不属于状态断言。
    }
  })

  const monitor = new AgentRunnerMonitorPage(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  mkdirSync(EVIDENCE_DIRECTORY, { recursive: true })
  await monitor.goto()
  await monitor.expectHeading()
  await monitor.openIssue(278)

  const takeoverCard = page.getByTestId('verifier-takeover-card')
  await expect(takeoverCard).toBeVisible()
  await expect(page.getByTestId('verifier-takeover-request')).toBeEnabled()
  await expect.poll(() => detailStates.includes('running')).toBe(true)

  await page.getByTestId('verifier-takeover-request').click()
  await expect(page.getByTestId('verifier-takeover-confirm')).toBeVisible()
  const actionResponsePromise = page.waitForResponse((response) =>
    ISSUE_ACTION_PATTERN.test(response.url()) && response.status() !== 307 && response.status() !== 308,
  )
  await page.getByTestId('verifier-takeover-confirm').click()

  const actionResponse = await actionResponsePromise
  expect(actionResponse.status()).toBe(202)
  const actionBody = actionResponse.request().postDataJSON() as {
    action: string
    run_id: string
    head_sha: string
  }
  const submittedBinding = verifierBindings.at(-1)
  expect(submittedBinding).toBeDefined()
  expect(actionBody).toEqual({
    action: 'take_over_verifier',
    run_id: submittedBinding?.runId,
    head_sha: submittedBinding?.headSha,
  })
  await expect(page.getByTestId('verifier-takeover-open-pr')).toBeVisible({
    timeout: 30_000,
  })
  await expect(
    page.getByText('已交给你审查 · verifier 未完成', { exact: false }),
  ).toBeVisible()
  await expect.poll(() => detailStates.includes('taken_over')).toBe(true)
  await expect(page.getByTestId('verifier-takeover-status')).toHaveCount(0)

  const issueRow = page
    .getByRole('listitem')
    .filter({ hasText: 'Verifier takeover live acceptance' })
  await expect(issueRow.getByText('review', { exact: true })).toBeVisible({
    timeout: 30_000,
  })
  await expect(issueRow.getByText('running', { exact: true })).toHaveCount(0)
  const labelEditor = page.getByTestId('issue-label-editor')
  await expect(labelEditor.getByText('review', { exact: true })).toBeVisible({
    timeout: 30_000,
  })
  await expect(labelEditor.getByText('running', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/rv1-issue-278 已刷新/)).toBeHidden({ timeout: 10_000 })

  await page.screenshot({
    path: join(EVIDENCE_DIRECTORY, 'rv-1-verifier-takeover.png'),
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await takeoverCard.scrollIntoViewIfNeeded()
  await page.screenshot({
    path: join(EVIDENCE_DIRECTORY, 'rv-1-verifier-takeover-narrow.png'),
    fullPage: true,
  })
})
