/**
 * 桌面小窗系统通知的判定回归（mocked API + Tauri invoke 桩）。
 *
 * PRD：tasks/pending/P2-FEAT-20261120-145338-companion-human-action-notifications.md
 * 验证层级：production composition；从 /app/companion 真实页面入口进入，DesktopCompanion 轮询与
 * handleSummary 判定都是生产代码。浏览器里没有原生宿主，所以只顶替两处边界：
 * runtime/tasks 读响应（按轮次切换任务状态）与 Tauri invoke 桥（记录实际请求的通知）。
 * 系统通知中心的真实弹窗不在本 spec 范围内（PRD rv-3 由真机手动补证）。
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const TASKS_ENDPOINT = '**/api/v1/agent-runner/console/runtime/tasks'
const COMPANION_STATUS_ENDPOINT = '**/api/v1/agent-runner/console/desktop-companion/status'
const COMPANION_POLL_MS = 15_000
const BLOCKING_REASON_MARKER = 'SUPERVISOR-SUMMARY-MUST-NOT-LEAK'

const EVIDENCE_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../tasks/evidence/P2-FEAT-20261120-145338-companion-human-action-notifications',
)

type TaskState = {
  status: string
  waiting_reason?: string | null
  labels: string[]
}

/** 每轮任务状态：键名即证据文件里的轮次说明。 */
const RUNNING: TaskState = { status: 'running', labels: ['agent/running'] }
const HUMAN_REVIEW: TaskState = {
  status: 'waiting',
  waiting_reason: 'human_review',
  labels: ['agent/automated-review-passed', 'human/awaiting-review'],
}
const RECHECK: TaskState = {
  status: 'waiting',
  waiting_reason: 'recheck',
  labels: ['agent/automated-review-passed', 'human/awaiting-review'],
}
const CLOSED: TaskState = { status: 'closed', labels: [] }
const BLOCKED: TaskState = { status: 'blocked', labels: ['agent/blocked'] }

/** 组装单任务的看板摘要响应体。 */
function tasksPayload(state: TaskState) {
  return {
    generated_at: '2026-11-20T10:00:00+00:00',
    tasks: [
      {
        repo_id: 'keda-main',
        issue_number: 21,
        task_key: 'keda-main#21',
        repo_display_name: 'Keda Main',
        issue_title: 'Add review notifications',
        issue_url: 'https://example.test/issues/21',
        pr_url: 'https://example.test/pull/22',
        pr_number: 22,
        pr_is_draft: true,
        status: state.status,
        waiting_reason: state.waiting_reason ?? null,
        status_reason: `当前监控标签：${state.labels.join('、')}`,
        blocking_reason: state.status === 'blocked' ? BLOCKING_REASON_MARKER : null,
        anomaly_types: [],
        pipeline_stage: null,
        phase: null,
        agent: 'codex',
        preset: null,
        model: null,
        attempt_number: 1,
        prd_path: null,
        started_at: '2026-11-20T09:00:00+00:00',
        finished_at: null,
        duration_seconds: 60,
        last_outcome: null,
        labels: state.labels,
      },
    ],
    status_counts: { running: 0, waiting: 0, blocked: 0, failed: 0, completed: 0, closed: 0, unknown: 0 },
    unavailable_sources: [],
    missing_repo_ids: [],
    sync_status: 'ready',
    scanned_at: '2026-11-20T10:00:00+00:00',
    truncated: false,
  }
}

/** 记录到的一次 Tauri invoke 调用。 */
type InvokeCall = { cmd: string; args: { title?: string; body?: string } }

/**
 * 注入 Tauri 宿主桩：让 `isTauri()` 为真，并把所有 invoke 记录到 `window.__invokeCalls`。
 *
 * @param page - Playwright 页面对象。
 */
async function installTauriStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const recordedCalls: unknown[] = []
    Object.assign(window, {
      isTauri: true,
      __invokeCalls: recordedCalls,
      __TAURI_INTERNALS__: {
        invoke: async (cmd: string, args: unknown) => {
          recordedCalls.push({ cmd, args })
          return null
        },
        transformCallback: () => 0,
      },
    })
  })
}

/** 读出页面里记录到的 notify_task 调用。 */
async function notifyCalls(page: Page): Promise<InvokeCall[]> {
  const calls = await page.evaluate(
    () => (window as unknown as { __invokeCalls?: InvokeCall[] }).__invokeCalls ?? [],
  )
  return calls.filter((call) => call.cmd === 'notify_task')
}

/**
 * 按给定序列逐轮驱动小窗轮询，返回每轮之后累计的通知调用。
 *
 * @param page - Playwright 页面对象。
 * @param sequence - 每轮的任务状态与说明；第一项是页面首次加载的读数。
 * @returns 每轮的说明与该轮新增的通知。
 */
async function driveRounds(
  page: Page,
  sequence: Array<{ label: string; state: TaskState }>,
): Promise<Array<{ label: string; added: InvokeCall[] }>> {
  let roundIndex = 0
  const servedRounds: number[] = []
  await page.route(TASKS_ENDPOINT, (route) => {
    servedRounds.push(roundIndex)
    return route.fulfill({
      json: tasksPayload(sequence[Math.min(roundIndex, sequence.length - 1)].state),
    })
  })
  await page.route(COMPANION_STATUS_ENDPOINT, (route) =>
    route.fulfill({
      json: {
        status: 'idle',
        console_origin: 'http://127.0.0.1:8765',
        console_session: 'fixture-session',
        focus_ticket: 0,
        close_ticket: 0,
        detail: null,
        hint: '桌面小窗未开启。',
        window_exists: false,
        companion_url: null,
      },
    }),
  )
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { user_id: 'local-operator', display_name: 'operator', email: 'operator@localhost' },
    }),
  )
  await page.route('**/api/v1/agent-runner/console/repositories/*/issues/*/logs*', (route) =>
    route.fulfill({
      json: { status: 'no_attempt', content: '', next_offset: 0, attempt_id: null, truncated: false },
    }),
  )

  await page.goto('/app/companion')
  await expect(page.getByTestId('desktop-companion-row-keda-main-21')).toBeVisible()

  const rounds: Array<{ label: string; added: InvokeCall[] }> = []
  let seenCount = (await notifyCalls(page)).length
  rounds.push({ label: sequence[0].label, added: (await notifyCalls(page)).slice(0, seenCount) })
  for (roundIndex = 1; roundIndex < sequence.length; roundIndex += 1) {
    const currentRound = roundIndex
    await page.clock.runFor(COMPANION_POLL_MS)
    // 先确认这一轮的读数确实被请求过，再给回调留出处理时间后读调用记录。
    await expect.poll(() => servedRounds.includes(currentRound)).toBe(true)
    await page.waitForTimeout(300)
    const allCalls = await notifyCalls(page)
    rounds.push({ label: sequence[currentRound].label, added: allCalls.slice(seenCount) })
    seenCount = allCalls.length
  }
  return rounds
}

/** 把逐轮结果写成人读证据文件。 */
function writeEvidence(fileName: string, rounds: Array<{ label: string; added: InvokeCall[] }>): void {
  mkdirSync(EVIDENCE_DIR, { recursive: true })
  const lines = rounds.map((round, index) => {
    const added = round.added.length
      ? round.added.map((call) => `    + 通知 title="${call.args.title}" body="${call.args.body}"`).join('\n')
      : '    （无新增通知）'
    return `第 ${index + 1} 轮：${round.label}\n${added}`
  })
  writeFileSync(resolve(EVIDENCE_DIR, fileName), `${lines.join('\n')}\n`, 'utf-8')
}

test.describe('desktop companion notifications (mocked API + Tauri stub)', () => {
  test('human review notifies once per run and Issue close notifies as 已关闭', async ({ page }) => {
    await page.clock.install()
    await installTauriStub(page)

    const rounds = await driveRounds(page, [
      { label: '首次加载：运行中', state: RUNNING },
      { label: '进入等待人工审阅', state: HUMAN_REVIEW },
      { label: '仍在等待人工审阅', state: HUMAN_REVIEW },
      { label: '切到等待 CI 复查', state: RECHECK },
      { label: '回到等待人工审阅（期间未重新运行）', state: HUMAN_REVIEW },
      { label: '重新运行', state: RUNNING },
      { label: '再次进入等待人工审阅', state: HUMAN_REVIEW },
      { label: 'Issue 已关闭', state: CLOSED },
    ])
    writeEvidence('rv-1-notification-calls.txt', rounds)

    const titlesByRound = rounds.map((round) => round.added.map((call) => call.args.title))
    expect(titlesByRound).toEqual([
      [],
      ['Keda Main#21 待你审阅'],
      [],
      [],
      [],
      [],
      ['Keda Main#21 待你审阅'],
      ['Keda Main#21 已关闭'],
    ])
    for (const round of rounds) {
      for (const call of round.added) {
        expect(call.args.body).toBe('Add review notifications')
        expect(call.args.title).not.toContain('合并')
      }
    }
  })

  test('tasks already awaiting review on first load are not notified', async ({ page }) => {
    await page.clock.install()
    await installTauriStub(page)

    const rounds = await driveRounds(page, [
      { label: '首次加载：已在等待人工审阅', state: HUMAN_REVIEW },
      { label: '仍在等待人工审阅', state: HUMAN_REVIEW },
    ])

    expect(rounds.flatMap((round) => round.added)).toEqual([])
  })

  test('blocked keeps its title and never leaks the supervisor summary', async ({ page }) => {
    await page.clock.install()
    await installTauriStub(page)

    const rounds = await driveRounds(page, [
      { label: '首次加载：运行中', state: RUNNING },
      { label: '进入阻塞', state: BLOCKED },
    ])
    writeEvidence('rv-4-blocked-notification.txt', rounds)

    const added = rounds.flatMap((round) => round.added)
    expect(added.map((call) => call.args.title)).toEqual(['Keda Main#21 已阻塞'])
    expect(added[0].args.body).toBe('Add review notifications')
    expect(JSON.stringify(added)).not.toContain(BLOCKING_REASON_MARKER)
  })

  test('a plain browser never requests notifications', async ({ page }) => {
    await page.clock.install()
    const pageErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error.message))

    const rounds = await driveRounds(page, [
      { label: '首次加载：运行中', state: RUNNING },
      { label: '进入等待人工审阅', state: HUMAN_REVIEW },
    ])

    expect(rounds.flatMap((round) => round.added)).toEqual([])
    expect(pageErrors).toEqual([])
  })
})
